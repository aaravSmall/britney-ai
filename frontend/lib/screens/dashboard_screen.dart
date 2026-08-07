import 'dart:async';
import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';
import 'package:provider/provider.dart';

import '../data/chart_kind.dart';
import '../data/chart_range.dart';
import '../services/api_service.dart';
import '../services/auth_controller.dart';
import '../services/portfolio_bus.dart';
import '../services/time_format_controller.dart';
import '../theme/app_theme.dart';
import '../utils/format.dart';
import '../widgets/price_chart.dart';

class DashboardScreen extends StatefulWidget {
  const DashboardScreen({super.key});

  @override
  State<DashboardScreen> createState() => _DashboardScreenState();
}

class _DashboardScreenState extends State<DashboardScreen> {
  ChartRange _range = ChartRange.month30;
  ChartKind _chartKind = ChartKind.line;
  bool _auto = false;
  bool _loading = false;
  String? _error;
  Map<String, dynamic>? _dashboard;
  List<Map<String, dynamic>> _performance = [];
  // The caller's own portfolio, plus the agent's three risk-tier model
  // portfolios (see GET /portfolios) — populates the switcher.
  List<Map<String, dynamic>> _portfolios = [];
  int? _selectedPortfolioId;
  bool _started = false;
  PortfolioBus? _portfolioBus;

  // Inline stock search, opened from the AppBar's search icon in place of
  // a navigation to a separate screen — see _buildAppBarTitle/_buildSearchDropdown.
  final TextEditingController _searchController = TextEditingController();
  Timer? _searchDebounce;
  bool _searchOpen = false;
  bool _searchLoading = false;
  String? _searchError;
  List<Map<String, dynamic>> _searchResults = [];

  int? get _myPortfolioId {
    for (final p in _portfolios) {
      if (p['owner_type'] == 'user') return p['id'] as int;
    }
    return null;
  }

  bool get _isMyPortfolioSelected =>
      _selectedPortfolioId != null && _selectedPortfolioId == _myPortfolioId;

  Map<String, dynamic>? get _selectedPortfolio {
    for (final p in _portfolios) {
      if (p['id'] == _selectedPortfolioId) return p;
    }
    return null;
  }

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    final bus = context.read<PortfolioBus>();
    if (!identical(_portfolioBus, bus)) {
      _portfolioBus?.removeListener(_onPortfolioTraded);
      _portfolioBus = bus..addListener(_onPortfolioTraded);
    }
    if (_started) return;
    _started = true;
    _load();
  }

  // A trade filled elsewhere (recommendations screen) always affects the
  // signed-in user's own portfolio — refetch whatever's currently selected
  // so cash/holdings stay in sync if that's what's showing; harmless (if
  // slightly redundant) when an agent portfolio is selected instead.
  void _onPortfolioTraded() {
    if (mounted) _loadPortfolioData();
  }

  @override
  void dispose() {
    _portfolioBus?.removeListener(_onPortfolioTraded);
    _searchDebounce?.cancel();
    _searchController.dispose();
    super.dispose();
  }

  void _openSearch() {
    setState(() => _searchOpen = true);
  }

  void _closeSearch() {
    _searchDebounce?.cancel();
    setState(() {
      _searchOpen = false;
      _searchController.clear();
      _searchResults = [];
      _searchLoading = false;
      _searchError = null;
    });
  }

  void _onSearchChanged(String value) {
    _searchDebounce?.cancel();
    final query = value.trim();
    if (query.isEmpty) {
      setState(() {
        _searchResults = [];
        _searchLoading = false;
        _searchError = null;
      });
      return;
    }
    // Flip the spinner on immediately (typing is the signal something's
    // about to happen) even though the request itself is debounced.
    setState(() {
      _searchLoading = true;
      _searchError = null;
    });
    _searchDebounce = Timer(const Duration(milliseconds: 300), () => _runSearch(query));
  }

  Future<void> _runSearch(String query) async {
    final api = context.read<ApiService>();
    try {
      final res = await api.get('/stocks/search?q=${Uri.encodeQueryComponent(query)}');
      if (!mounted) return;
      // A response for an older, already-superseded query could in
      // principle land after a newer one — only trust it if it still
      // matches what's in the field right now.
      if (_searchController.text.trim() != query) return;
      if (res.statusCode == 200) {
        final list = jsonDecode(res.body) as List<dynamic>;
        setState(() {
          _searchResults = list.map((e) => e as Map<String, dynamic>).toList();
          _searchLoading = false;
        });
      } else {
        setState(() {
          _searchError = 'Search failed (${res.statusCode}).';
          _searchLoading = false;
        });
      }
    } catch (e) {
      if (!mounted || _searchController.text.trim() != query) return;
      setState(() {
        _searchError = 'Network error: $e';
        _searchLoading = false;
      });
    }
  }

  void _selectSearchResult(String ticker) {
    _closeSearch();
    context.push('/stock/$ticker');
  }

  /// Fetches the switcher's portfolio list, picks a default selection (the
  /// user's own) on first load, then loads that portfolio's data.
  Future<void> _load() async {
    final api = context.read<ApiService>();
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final res = await api.get('/portfolios');
      if (!mounted) return;
      if (res.statusCode != 200) {
        setState(() {
          _error = 'Failed to load portfolios: ${res.body}';
          _loading = false;
        });
        return;
      }
      final list = (jsonDecode(res.body) as List<dynamic>)
          .map((e) => e as Map<String, dynamic>)
          .toList();
      setState(() => _portfolios = list);
      _selectedPortfolioId ??=
          _myPortfolioId ?? (list.isNotEmpty ? list.first['id'] as int : null);
      await _loadPortfolioData();
    } catch (e) {
      if (mounted) {
        setState(() {
          _error = 'Network error: $e';
          _loading = false;
        });
      }
    }
  }

  /// Fetches dashboard + performance for [_selectedPortfolioId]. "My
  /// Portfolio" uses the existing authenticated /dashboard endpoints;
  /// any other (agent) portfolio uses the portfolio-id-scoped ones from
  /// Sprint 6 Part 2, which intentionally have no auth of their own yet.
  Future<void> _loadPortfolioData() async {
    final id = _selectedPortfolioId;
    if (id == null) return;
    final mine = id == _myPortfolioId;
    final api = context.read<ApiService>();
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final results = await Future.wait([
        api.get(mine ? '/dashboard' : '/portfolios/$id/summary'),
        api.get('/users/me'),
        api.get(mine ? '/dashboard/performance' : '/portfolios/$id/performance'),
      ]);
      if (!mounted) return;
      final dashRes = results[0];
      final meRes = results[1];
      final perfRes = results[2];
      if (dashRes.statusCode == 200) {
        setState(() => _dashboard = jsonDecode(dashRes.body) as Map<String, dynamic>);
      } else {
        setState(() => _error = 'Failed to load dashboard: ${dashRes.body}');
      }
      if (meRes.statusCode == 200) {
        final u = jsonDecode(meRes.body) as Map<String, dynamic>;
        setState(() => _auto = u['auto_invest_enabled'] == true);
      }
      // Sparse/empty performance history is a valid, expected state (not an
      // error) — a failed fetch just degrades to the same empty-state UI.
      if (perfRes.statusCode == 200) {
        final list = jsonDecode(perfRes.body) as List<dynamic>;
        setState(
          () => _performance = list.map((e) => e as Map<String, dynamic>).toList(),
        );
      } else {
        setState(() => _performance = []);
      }
    } catch (e) {
      if (mounted) setState(() => _error = 'Network error: $e');
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  void _switchPortfolio(int id) {
    if (id == _selectedPortfolioId) return;
    setState(() {
      _selectedPortfolioId = id;
      _dashboard = null;
      _performance = [];
    });
    _loadPortfolioData();
  }

  Future<void> _toggleAuto(bool v) async {
    final api = context.read<ApiService>();
    try {
      final res = await api.patch('/settings/auto-invest', {'enabled': v});
      if (res.statusCode >= 200 && res.statusCode < 300 && mounted) {
        setState(() => _auto = v);
      }
    } catch (_) {
      if (mounted) setState(() => _auto = v);
    }
  }

  String _greeting() {
    final h = DateTime.now().hour;
    if (h < 12) return 'Good morning';
    if (h < 17) return 'Good afternoon';
    return 'Good evening';
  }

  /// Real snapshots within the selected range's time window, oldest first
  /// (the backend already returns ascending order). Snapshot cadence is
  /// uneven — every 15-60min depending on market hours — so this filters by
  /// elapsed wall-clock time rather than by a fixed point count.
  List<Map<String, dynamic>> _filterForRange(
    List<Map<String, dynamic>> snapshots,
    ChartRange range,
  ) {
    if (snapshots.isEmpty) return snapshots;
    final now = DateTime.now();
    final DateTime cutoff;
    switch (range) {
      case ChartRange.today:
        cutoff = DateTime(now.year, now.month, now.day);
        break;
      case ChartRange.week:
        cutoff = now.subtract(const Duration(days: 7));
        break;
      case ChartRange.month30:
        cutoff = now.subtract(const Duration(days: 30));
        break;
      case ChartRange.ytd:
        cutoff = DateTime(now.year, 1, 1);
        break;
      case ChartRange.fiveYear:
        cutoff = now.subtract(const Duration(days: 365 * 5));
        break;
    }
    return [
      for (final s in snapshots)
        if (!DateTime.parse(s['timestamp'] as String).toLocal().isBefore(cutoff))
          s,
    ];
  }

  @override
  Widget build(BuildContext context) {
    final auth = context.watch<AuthController>();
    final use24Hour = context.watch<TimeFormatController>().use24Hour;
    final t = Theme.of(context).textTheme;

    // The switcher (and its "loading"/error retry) stays visible through
    // every state below — losing it mid-load would strand the user unable
    // to pick a different portfolio while one is failing to fetch.
    final titleWidget = Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        _PortfolioSwitcher(
          portfolios: _portfolios,
          selectedId: _selectedPortfolioId,
          onSelected: _switchPortfolio,
        ),
        Text(
          _isMyPortfolioSelected ? _greeting() : 'Managed by the trading agent',
          style: t.bodySmall?.copyWith(color: AppTheme.textSecondaryOf(context)),
        ),
      ],
    );

    // The one entry point into stock search — an inline bar that slides
    // out in place of the search icon (not a navigation to a separate
    // screen), since no shared AppBar exists across the bottom-nav tabs
    // (_MainShell's own Scaffold has no appBar at all; every tab,
    // including this one, builds its own). Placed on this tab
    // specifically because it's where the user lands after login
    // (app_router.dart's redirect defaults to /home).
    final appBar = AppBar(
      title: AnimatedSwitcher(
        duration: const Duration(milliseconds: 220),
        switchInCurve: Curves.easeOut,
        switchOutCurve: Curves.easeIn,
        transitionBuilder: (child, animation) => FadeTransition(
          opacity: animation,
          child: SlideTransition(
            position: Tween<Offset>(
              begin: const Offset(0.25, 0),
              end: Offset.zero,
            ).animate(animation),
            child: child,
          ),
        ),
        child: _searchOpen
            ? _buildSearchField(key: const ValueKey('search'))
            : KeyedSubtree(key: const ValueKey('title'), child: titleWidget),
      ),
      actions: [
        IconButton(
          tooltip: _searchOpen ? 'Close search' : 'Search stocks',
          onPressed: _searchOpen ? _closeSearch : _openSearch,
          icon: Icon(_searchOpen ? Icons.close_rounded : Icons.search_rounded),
        ),
      ],
    );

    if (_loading && _dashboard == null) {
      return Scaffold(
        backgroundColor: Colors.transparent,
        appBar: appBar,
        body: _withSearchOverlay(
          const Center(child: CircularProgressIndicator(color: AppTheme.accent)),
        ),
      );
    }

    if (_error != null && _dashboard == null) {
      return Scaffold(
        backgroundColor: Colors.transparent,
        appBar: appBar,
        body: _withSearchOverlay(
          Center(
            child: Padding(
              padding: const EdgeInsets.all(32),
              child: Column(
                mainAxisAlignment: MainAxisAlignment.center,
                children: [
                  const Icon(
                    Icons.error_outline_rounded,
                    size: 40,
                    color: AppTheme.danger,
                  ),
                  const SizedBox(height: 16),
                  Text(
                    _error!,
                    textAlign: TextAlign.center,
                    style: t.bodyMedium?.copyWith(color: AppTheme.textSecondaryOf(context)),
                  ),
                  const SizedBox(height: 16),
                  FilledButton(
                    onPressed: _selectedPortfolioId == null ? _load : _loadPortfolioData,
                    child: const Text('Try again'),
                  ),
                ],
              ),
            ),
          ),
        ),
      );
    }

    final dashboard = _dashboard!;
    final isIntraday = _range == ChartRange.today;
    final filtered = _filterForRange(_performance, _range);
    final values = [
      for (final p in filtered) (p['total_value'] as num).toDouble(),
    ];
    final times = [
      for (final p in filtered) DateTime.parse(p['timestamp'] as String).toLocal(),
    ];
    final periodChangePct = values.length >= 2 && values.first != 0
        ? (values.last - values.first) / values.first * 100
        : 0.0;
    final total = (dashboard['total_portfolio_value'] as num).toDouble();
    final cash = (dashboard['cash_balance'] as num).toDouble();
    final holdings = (dashboard['holdings'] as List<dynamic>)
        .map((e) => e as Map<String, dynamic>)
        .toList();

    final isMine = _isMyPortfolioSelected;

    return Scaffold(
      backgroundColor: Colors.transparent,
      appBar: appBar,
      body: _withSearchOverlay(RefreshIndicator(
        color: AppTheme.accent,
        onRefresh: _loadPortfolioData,
        child: ListView(
          physics: const AlwaysScrollableScrollPhysics(),
          padding: const EdgeInsets.fromLTRB(20, 8, 20, 100),
          children: [
            if (isMine)
              Row(
                children: [
                  CircleAvatar(
                    radius: 26,
                    backgroundColor: AppTheme.accent.withValues(alpha: 0.15),
                    child: Text(
                      auth.displayName.isNotEmpty
                          ? auth.displayName[0].toUpperCase()
                          : '?',
                      style: const TextStyle(
                        color: AppTheme.accent,
                        fontSize: 22,
                        fontWeight: FontWeight.w600,
                      ),
                    ),
                  ),
                  const SizedBox(width: 14),
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(
                          auth.displayName,
                          style: t.titleMedium?.copyWith(
                            fontWeight: FontWeight.w600,
                          ),
                        ),
                        if (auth.displayEmail != null &&
                            auth.displayEmail!.isNotEmpty)
                          Text(
                            auth.displayEmail!,
                            style: t.bodySmall?.copyWith(
                              color: AppTheme.textSecondaryOf(context),
                            ),
                          ),
                      ],
                    ),
                  ),
                ],
              )
            else
              Row(
                children: [
                  CircleAvatar(
                    radius: 26,
                    backgroundColor: AppTheme.accent.withValues(alpha: 0.15),
                    child: const Icon(
                      Icons.auto_awesome_rounded,
                      color: AppTheme.accent,
                      size: 24,
                    ),
                  ),
                  const SizedBox(width: 14),
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(
                          _selectedPortfolio?['label'] as String? ?? 'Agent Portfolio',
                          style: t.titleMedium?.copyWith(fontWeight: FontWeight.w600),
                        ),
                        Text(
                          'Autonomous trading agent — \$10,000 starting cash',
                          style: t.bodySmall?.copyWith(
                            color: AppTheme.textSecondaryOf(context),
                          ),
                        ),
                      ],
                    ),
                  ),
                ],
              ),
            const SizedBox(height: 8),
            Text(
              'Paper portfolio — simulated execution, illustrative only.',
              style: t.bodySmall?.copyWith(color: AppTheme.textSecondaryOf(context)),
            ),
            const SizedBox(height: 16),
            Row(
              children: [
                Text(
                  'Performance',
                  style: t.titleSmall?.copyWith(
                    fontWeight: FontWeight.w600,
                    letterSpacing: 0.2,
                  ),
                ),
                const Spacer(),
                ChartKindToggle(
                  value: _chartKind,
                  onChanged: (k) => setState(() => _chartKind = k),
                ),
              ],
            ),
            const SizedBox(height: 10),
            SingleChildScrollView(
              scrollDirection: Axis.horizontal,
              child: Row(
                children: [
                  for (final r in ChartRange.values)
                    Padding(
                      padding: const EdgeInsets.only(right: 8),
                      child: ChoiceChip(
                        showCheckmark: false,
                        selectedColor:
                            AppTheme.accent.withValues(alpha: 0.22),
                        label: Text(r.shortLabel),
                        selected: _range == r,
                        onSelected: (_) => setState(() => _range = r),
                      ),
                    ),
                ],
              ),
            ),
            const SizedBox(height: 12),
            _SummaryCard(
              range: _range,
              chartKind: _chartKind,
              totalValue: total,
              cash: cash,
              periodChangePct: periodChangePct,
              values: values,
              times: times,
              isIntraday: isIntraday,
              use24Hour: use24Hour,
              hasAnyHistory: _performance.isNotEmpty,
            ),
            // Auto-invest is a User-level setting, not a portfolio one — it
            // has no meaning for the agent's own model portfolios.
            if (isMine) ...[
              const SizedBox(height: 16),
              Card(
                child: SwitchListTile(
                  contentPadding: const EdgeInsets.symmetric(
                    horizontal: 16,
                    vertical: 4,
                  ),
                  title: const Text('Auto-invest (paper)'),
                  subtitle: Text(
                    'When on, trade requests can use simulated execution.',
                    style: t.bodySmall?.copyWith(
                      color: AppTheme.textSecondaryOf(context),
                    ),
                  ),
                  value: _auto,
                  activeThumbColor: AppTheme.accent,
                  onChanged: _toggleAuto,
                ),
              ),
            ],
            const SizedBox(height: 20),
            Row(
              children: [
                Text(
                  'Holdings',
                  style: t.titleSmall?.copyWith(
                    fontWeight: FontWeight.w600,
                    letterSpacing: 0.2,
                  ),
                ),
                const Spacer(),
                Text(
                  '${holdings.length} positions',
                  style: t.bodySmall?.copyWith(
                    color: AppTheme.textSecondaryOf(context),
                  ),
                ),
              ],
            ),
            const SizedBox(height: 10),
            ...holdings.map((h) => _HoldingTile(h)),
            const SizedBox(height: 20),
            Card(
              child: ListTile(
                contentPadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 4),
                leading: const Icon(Icons.receipt_long_rounded),
                title: const Text('Trade History'),
                subtitle: Text(
                  isMine ? 'Your trades and agent activity' : 'This agent portfolio\'s trades',
                  style: t.bodySmall?.copyWith(color: AppTheme.textSecondaryOf(context)),
                ),
                trailing: const Icon(Icons.chevron_right_rounded),
                onTap: () => context.push('/trade-history/${dashboard['portfolio_id']}'),
              ),
            ),
          ],
        ),
      )),
    );
  }

  /// The search field shown in place of the AppBar title while
  /// [_searchOpen] — rounded-rectangle corners (not a pill), matching
  /// the app's default input styling.
  Widget _buildSearchField({required Key key}) {
    return TextField(
      key: key,
      controller: _searchController,
      autofocus: true,
      onChanged: _onSearchChanged,
      textInputAction: TextInputAction.search,
      style: const TextStyle(fontSize: 15),
      decoration: InputDecoration(
        isDense: true,
        contentPadding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
        prefixIcon: const Icon(Icons.search_rounded, size: 20),
        hintText: 'Search stocks',
        border: OutlineInputBorder(
          borderRadius: BorderRadius.circular(12),
          borderSide: BorderSide.none,
        ),
        focusedBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(12),
          borderSide: const BorderSide(color: AppTheme.accent, width: 1.5),
        ),
      ),
    );
  }

  /// Wraps [body] with the search results dropdown — a scrim + floating
  /// panel positioned right below the AppBar (i.e. below the search bar)
  /// when [_searchOpen], rather than a separate screen. Applied to every
  /// Scaffold body (loading/error/loaded) so search still works while the
  /// dashboard itself is loading or has failed to load.
  Widget _withSearchOverlay(Widget body) {
    if (!_searchOpen) return body;
    return Stack(
      children: [
        body,
        Positioned.fill(
          child: GestureDetector(
            behavior: HitTestBehavior.opaque,
            onTap: _closeSearch,
            child: Container(color: Colors.black.withValues(alpha: 0.4)),
          ),
        ),
        Positioned(
          top: 0,
          left: 0,
          right: 0,
          child: Material(
            color: Colors.transparent,
            child: _buildSearchDropdown(),
          ),
        ),
      ],
    );
  }

  Widget _buildSearchDropdown() {
    final t = Theme.of(context).textTheme;
    final query = _searchController.text.trim();
    if (query.isEmpty) return const SizedBox.shrink();

    Widget content;
    if (_searchLoading) {
      content = const Padding(
        padding: EdgeInsets.all(20),
        child: Center(
          heightFactor: 1,
          child: SizedBox(
            width: 20,
            height: 20,
            child: CircularProgressIndicator(strokeWidth: 2, color: AppTheme.accent),
          ),
        ),
      );
    } else if (_searchError != null) {
      content = Padding(
        padding: const EdgeInsets.all(20),
        child: Text(_searchError!, style: t.bodySmall?.copyWith(color: AppTheme.danger)),
      );
    } else if (_searchResults.isEmpty) {
      content = Padding(
        padding: const EdgeInsets.all(20),
        child: Text(
          'No results',
          style: t.bodySmall?.copyWith(color: AppTheme.textSecondaryOf(context)),
        ),
      );
    } else {
      content = ListView.separated(
        shrinkWrap: true,
        padding: const EdgeInsets.symmetric(vertical: 4),
        itemCount: _searchResults.length,
        separatorBuilder: (_, __) => Divider(
          height: 1,
          color: AppTheme.borderSubtleOf(context),
        ),
        itemBuilder: (context, i) {
          final r = _searchResults[i];
          final ticker = r['ticker'] as String;
          final name = r['name'] as String? ?? '';
          final exchange = r['exchange'] as String? ?? '';
          return ListTile(
            dense: true,
            title: Text(ticker, style: const TextStyle(fontWeight: FontWeight.w600)),
            subtitle: Text(
              exchange.isNotEmpty ? '$name · $exchange' : name,
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: t.bodySmall?.copyWith(color: AppTheme.textSecondaryOf(context)),
            ),
            onTap: () => _selectSearchResult(ticker),
          );
        },
      );
    }

    return Container(
      margin: const EdgeInsets.fromLTRB(12, 4, 12, 0),
      constraints: const BoxConstraints(maxHeight: 360),
      decoration: BoxDecoration(
        color: AppTheme.surfaceOf(context),
        borderRadius: BorderRadius.circular(14),
        border: Border.all(color: AppTheme.borderSubtleOf(context)),
        boxShadow: [
          BoxShadow(
            color: Colors.black.withValues(alpha: 0.3),
            blurRadius: 20,
            offset: const Offset(0, 8),
          ),
        ],
      ),
      clipBehavior: Clip.antiAlias,
      child: content,
    );
  }
}

/// AppBar title that doubles as the portfolio switcher: the current
/// selection's label with a chevron, opening a menu of every portfolio
/// GET /portfolios returned (the user's own + the agent's three model
/// portfolios). Falls back to a plain, non-interactive label while the
/// list hasn't loaded yet (or failed to) rather than showing a dead-end
/// dropdown with nothing to switch to.
class _PortfolioSwitcher extends StatelessWidget {
  const _PortfolioSwitcher({
    required this.portfolios,
    required this.selectedId,
    required this.onSelected,
  });

  final List<Map<String, dynamic>> portfolios;
  final int? selectedId;
  final ValueChanged<int> onSelected;

  @override
  Widget build(BuildContext context) {
    final t = Theme.of(context).textTheme;
    final titleStyle = t.titleLarge?.copyWith(fontWeight: FontWeight.w600);

    String? label;
    for (final p in portfolios) {
      if (p['id'] == selectedId) {
        label = p['label'] as String;
        break;
      }
    }
    label ??= 'Portfolio';

    if (portfolios.length <= 1) {
      return Text(label, style: titleStyle);
    }

    return PopupMenuButton<int>(
      tooltip: 'Switch portfolio',
      offset: const Offset(0, 44),
      onSelected: onSelected,
      itemBuilder: (context) => [
        for (final p in portfolios)
          PopupMenuItem<int>(
            value: p['id'] as int,
            child: Row(
              children: [
                SizedBox(
                  width: 20,
                  child: p['id'] == selectedId
                      ? const Icon(Icons.check_rounded, size: 18, color: AppTheme.accent)
                      : null,
                ),
                const SizedBox(width: 4),
                Text(p['label'] as String),
              ],
            ),
          ),
      ],
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Flexible(child: Text(label, style: titleStyle, overflow: TextOverflow.ellipsis)),
          const SizedBox(width: 2),
          Icon(Icons.expand_more_rounded, color: AppTheme.textSecondaryOf(context)),
        ],
      ),
    );
  }
}

class _SummaryCard extends StatefulWidget {
  const _SummaryCard({
    required this.range,
    required this.chartKind,
    required this.totalValue,
    required this.cash,
    required this.periodChangePct,
    required this.values,
    required this.times,
    required this.isIntraday,
    required this.use24Hour,
    required this.hasAnyHistory,
  });

  final ChartRange range;
  final ChartKind chartKind;
  final double totalValue;
  final double cash;
  final double periodChangePct;
  final List<double> values;
  final List<DateTime> times;
  final bool isIntraday;
  final bool use24Hour;
  // Whether the portfolio has any PortfolioSnapshot rows at all, regardless
  // of the selected range — distinguishes "nothing captured yet" from
  // "nothing captured in this particular range" for the placeholder copy.
  final bool hasAnyHistory;

  @override
  State<_SummaryCard> createState() => _SummaryCardState();
}

class _SummaryCardState extends State<_SummaryCard> {
  // Mirrors PriceChartView's own internal scrub state (via
  // onScrubIndexChanged) purely so this header can react to it too —
  // showing the value/date at the scrubbed point instead of the
  // period-end totals while the user is dragging across the chart.
  int? _scrubIndex;

  bool get _hasChart => widget.values.length >= minPointsForChart;

  @override
  void didUpdateWidget(covariant _SummaryCard oldWidget) {
    super.didUpdateWidget(oldWidget);
    // Range/chart-kind changes swap the underlying data — a stale scrub
    // index into the old series would show the wrong value.
    if (oldWidget.range != widget.range || oldWidget.values.length != widget.values.length) {
      _scrubIndex = null;
    }
  }

  @override
  Widget build(BuildContext context) {
    final t = Theme.of(context).textTheme;
    final hasChart = _hasChart;
    final scrubbing = hasChart && _scrubIndex != null;
    final start = hasChart ? widget.values.first : 0.0;
    final displayValue = scrubbing ? widget.values[_scrubIndex!] : widget.totalValue;
    final displayPct = scrubbing
        ? (start != 0
            ? (widget.values[_scrubIndex!] - start) / start * 100
            : 0.0)
        : widget.periodChangePct;
    final positive = displayPct >= 0;
    final subtitle = scrubbing
        ? scrubDetailLabel(
            widget.times[_scrubIndex!],
            isIntraday: widget.isIntraday,
            use24Hour: widget.use24Hour,
          )
        : widget.range.description;
    final chipLabel = scrubbing
        ? 'since start'
        : (widget.range == ChartRange.today ? 'today' : 'in period');

    return Card(
      child: Padding(
        padding: const EdgeInsets.all(20),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              'Total value',
              style: t.labelLarge?.copyWith(
                color: AppTheme.textSecondaryOf(context),
                fontWeight: FontWeight.w500,
              ),
            ),
            const SizedBox(height: 4),
            Text(
              '\$${displayValue.toStringAsFixed(2)}',
              style: t.headlineMedium?.copyWith(
                fontWeight: FontWeight.w700,
                letterSpacing: -0.5,
              ),
            ),
            const SizedBox(height: 6),
            Text(
              subtitle,
              style: t.bodySmall?.copyWith(color: AppTheme.textSecondaryOf(context)),
            ),
            const SizedBox(height: 8),
            Row(
              children: [
                Icon(
                  Icons.account_balance_wallet_outlined,
                  size: 16,
                  color: AppTheme.textSecondaryOf(context),
                ),
                const SizedBox(width: 6),
                Text(
                  'Cash \$${widget.cash.toStringAsFixed(2)}',
                  style: t.bodySmall?.copyWith(color: AppTheme.textSecondaryOf(context)),
                ),
                if (hasChart) ...[
                  const SizedBox(width: 14),
                  Container(
                    padding: const EdgeInsets.symmetric(
                      horizontal: 8,
                      vertical: 4,
                    ),
                    decoration: BoxDecoration(
                      color: positive
                          ? AppTheme.accent.withValues(alpha: 0.12)
                          : AppTheme.danger.withValues(alpha: 0.12),
                      borderRadius: BorderRadius.circular(8),
                    ),
                    child: Text(
                      '${positive ? '+' : ''}${displayPct.toStringAsFixed(2)}% $chipLabel',
                      style: TextStyle(
                        color: positive ? AppTheme.accent : AppTheme.danger,
                        fontWeight: FontWeight.w600,
                        fontSize: 13,
                      ),
                    ),
                  ),
                ],
              ],
            ),
            const SizedBox(height: 20),
            PriceChartView(
              values: widget.values,
              times: widget.times,
              chartKind: widget.chartKind,
              isIntraday: widget.isIntraday,
              use24Hour: widget.use24Hour,
              hasAnyHistory: widget.hasAnyHistory,
              noHistoryMessage:
                  'Performance history will appear here once the agent starts trading.',
              noDataInRangeMessage: 'No snapshots yet for this range — try a wider range.',
              notEnoughPointsMessage:
                  'Not enough history yet to chart — showing current value only.',
              onScrubIndexChanged: (i) => setState(() => _scrubIndex = i),
            ),
          ],
        ),
      ),
    );
  }
}

class _HoldingTile extends StatelessWidget {
  const _HoldingTile(this.h);

  final Map<String, dynamic> h;

  @override
  Widget build(BuildContext context) {
    final sym = h['symbol'] as String;
    final qty = (h['quantity'] as num).toDouble();
    final mv = h['market_value'];
    final t = Theme.of(context).textTheme;

    return Card(
      margin: const EdgeInsets.only(bottom: 10),
      child: ListTile(
        contentPadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 4),
        leading: CircleAvatar(
          backgroundColor: AppTheme.surface2Of(context),
          child: Text(
            sym.length >= 2 ? sym.substring(0, 2) : sym,
            style: TextStyle(
              fontWeight: FontWeight.w700,
              fontSize: 13,
              color: AppTheme.textPrimaryOf(context),
            ),
          ),
        ),
        title: Text(sym, style: const TextStyle(fontWeight: FontWeight.w600)),
        subtitle: Text(
          '${formatQuantity(qty)} ${h['asset_type']}',
          style: t.bodySmall?.copyWith(color: AppTheme.textSecondaryOf(context)),
        ),
        trailing: Text(
          mv != null ? '\$${(mv as num).toStringAsFixed(2)}' : '—',
          style: t.titleSmall?.copyWith(fontWeight: FontWeight.w600),
        ),
      ),
    );
  }
}
