import 'dart:convert';

import 'package:fl_chart/fl_chart.dart';
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

/// Market hours used for "Today" x-axis tick placement.
const int _marketOpenHour = 9;
const int _marketCloseHour = 17;

/// Below this many real PortfolioSnapshot points in the selected range, a
/// line/candlestick/waterfall chart would be more misleading than
/// informative (e.g. a single segment implying a trend from 2 dots) — show
/// a simpler placeholder instead.
const int _minPointsForChart = 3;

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
    super.dispose();
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

    if (_loading && _dashboard == null) {
      return Scaffold(
        backgroundColor: Colors.transparent,
        appBar: AppBar(title: titleWidget),
        body: const Center(child: CircularProgressIndicator(color: AppTheme.accent)),
      );
    }

    if (_error != null && _dashboard == null) {
      return Scaffold(
        backgroundColor: Colors.transparent,
        appBar: AppBar(title: titleWidget),
        body: Center(
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
      appBar: AppBar(title: titleWidget),
      body: RefreshIndicator(
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
                _ChartKindToggle(
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
      ),
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

const List<String> _monthAbbr = [
  'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
  'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec',
];
const List<String> _monthFull = [
  'January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December',
];

String _shortDate(DateTime d) => '${_monthAbbr[d.month - 1]} ${d.day}';

String _fullDate(DateTime d) =>
    '${_monthFull[d.month - 1]} ${d.day}, ${d.year}';

String _formatTime(DateTime d, bool use24Hour) {
  final minute = d.minute.toString().padLeft(2, '0');
  if (use24Hour) return '${d.hour}:$minute';
  final hour12 = d.hour % 12 == 0 ? 12 : d.hour % 12;
  final suffix = d.hour < 12 ? 'AM' : 'PM';
  return '$hour12:$minute $suffix';
}

/// Short label for axis ticks and the on-chart scrub tooltip.
String _chartLabel(DateTime d, {required bool isIntraday, required bool use24Hour}) =>
    isIntraday ? _formatTime(d, use24Hour) : _shortDate(d);

/// Fuller label for the "Total value" subtitle while scrubbing.
String _detailLabel(DateTime d, {required bool isIntraday, required bool use24Hour}) =>
    isIntraday ? _formatTime(d, use24Hour) : _fullDate(d);

/// Fraction (0..1) of the way [t] sits between [start] and [end]. All three
/// chart kinds and the scrubber share this so a point's pixel position is
/// proportional to real elapsed time rather than its index in the list —
/// otherwise a flat multi-hour pre/post-market segment (few points) would
/// render as a barely-visible sliver next to a busy market-hours segment
/// (many points) despite covering most of the day.
double _fracFor(DateTime t, DateTime start, DateTime end) {
  final total = end.difference(start).inMilliseconds;
  if (total <= 0) return 0.5;
  return (t.difference(start).inMilliseconds / total).clamp(0.0, 1.0);
}

int _nearestIndexForFrac(double frac, List<DateTime> times) {
  final start = times.first;
  final end = times.last;
  var best = 0;
  var bestDist = double.infinity;
  for (var i = 0; i < times.length; i++) {
    final d = (_fracFor(times[i], start, end) - frac).abs();
    if (d < bestDist) {
      bestDist = d;
      best = i;
    }
  }
  return best;
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
  int? _scrubIndex;

  bool get _hasChart => widget.values.length >= _minPointsForChart;

  void _updateScrub(double localX, double width) {
    if (!_hasChart || width <= 0) return;
    final frac = (localX / width).clamp(0.0, 1.0);
    final idx = _nearestIndexForFrac(frac, widget.times);
    if (idx != _scrubIndex) setState(() => _scrubIndex = idx);
  }

  void _endScrub() {
    if (_scrubIndex != null) setState(() => _scrubIndex = null);
  }

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
        ? _detailLabel(
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
            if (hasChart) ...[
              LayoutBuilder(
                builder: (context, constraints) {
                  final width = constraints.maxWidth;
                  final xForIndex = widget.times.length > 1
                      ? _fracFor(
                            widget.times[_scrubIndex ?? 0],
                            widget.times.first,
                            widget.times.last,
                          ) *
                          width
                      : width / 2;
                  return GestureDetector(
                    behavior: HitTestBehavior.opaque,
                    // Horizontal-only recognizers (not onPan*) so this plays
                    // nicely with the enclosing vertical ListView's scroll
                    // gesture instead of fighting it for the arena.
                    onHorizontalDragStart: (d) =>
                        _updateScrub(d.localPosition.dx, width),
                    onHorizontalDragUpdate: (d) =>
                        _updateScrub(d.localPosition.dx, width),
                    onHorizontalDragEnd: (_) => _endScrub(),
                    onHorizontalDragCancel: _endScrub,
                    onTapDown: (d) => _updateScrub(d.localPosition.dx, width),
                    onTapUp: (_) => _endScrub(),
                    child: SizedBox(
                      height: 180,
                      child: Stack(
                        children: [
                          Positioned.fill(
                            // fl_chart still occupies hit-test space even with
                            // its own touch data disabled; ignore it so only
                            // the GestureDetector above ever sees pointer
                            // events, regardless of chart kind.
                            child: IgnorePointer(
                              child: switch (widget.chartKind) {
                                ChartKind.line => _LineChartView(
                                    values: widget.values,
                                    times: widget.times,
                                  ),
                                ChartKind.candlestick => _CandlestickChartView(
                                    values: widget.values,
                                    times: widget.times,
                                  ),
                                ChartKind.waterfall => _WaterfallChartView(
                                    values: widget.values,
                                    times: widget.times,
                                  ),
                              },
                            ),
                          ),
                          if (scrubbing) ...[
                            Positioned.fill(
                              child: IgnorePointer(
                                child: CustomPaint(
                                  painter: _ScrubLinePainter(
                                    x: xForIndex,
                                    color: AppTheme.textSecondaryOf(context),
                                  ),
                                ),
                              ),
                            ),
                            Positioned(
                              top: 0,
                              left: (xForIndex - 54).clamp(0.0, width - 108),
                              child: IgnorePointer(
                                child: _ScrubTooltip(
                                  value: widget.values[_scrubIndex!],
                                  label: _chartLabel(
                                    widget.times[_scrubIndex!],
                                    isIntraday: widget.isIntraday,
                                    use24Hour: widget.use24Hour,
                                  ),
                                ),
                              ),
                            ),
                          ],
                        ],
                      ),
                    ),
                  );
                },
              ),
              const SizedBox(height: 8),
              _ChartXAxisLabels(
                times: widget.times,
                isIntraday: widget.isIntraday,
                use24Hour: widget.use24Hour,
              ),
            ] else
              _SparseHistoryPlaceholder(
                message: !widget.hasAnyHistory
                    ? 'Performance history will appear here once the agent starts trading.'
                    : widget.values.isEmpty
                        ? 'No snapshots yet for this range — try a wider range.'
                        : 'Not enough history yet to chart — showing current value only.',
              ),
          ],
        ),
      ),
    );
  }
}

/// Shown in place of the chart when there are fewer than
/// [_minPointsForChart] real snapshots in the selected range — a line drawn
/// through 1-2 points would imply a trend that isn't actually there.
class _SparseHistoryPlaceholder extends StatelessWidget {
  const _SparseHistoryPlaceholder({required this.message});

  final String message;

  @override
  Widget build(BuildContext context) {
    final t = Theme.of(context).textTheme;
    return SizedBox(
      height: 180,
      child: Center(
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 24),
          child: Column(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              Icon(
                Icons.show_chart_rounded,
                size: 28,
                color: AppTheme.textSecondaryOf(context),
              ),
              const SizedBox(height: 10),
              Text(
                message,
                textAlign: TextAlign.center,
                style: t.bodySmall?.copyWith(color: AppTheme.textSecondaryOf(context)),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class _ScrubLinePainter extends CustomPainter {
  _ScrubLinePainter({required this.x, required this.color});

  final double x;
  final Color color;

  @override
  void paint(Canvas canvas, Size size) {
    canvas.drawLine(
      Offset(x, 0),
      Offset(x, size.height),
      Paint()
        ..color = color.withValues(alpha: 0.5)
        ..strokeWidth = 1.5,
    );
  }

  @override
  bool shouldRepaint(covariant _ScrubLinePainter oldDelegate) =>
      oldDelegate.x != x || oldDelegate.color != color;
}

class _ScrubTooltip extends StatelessWidget {
  const _ScrubTooltip({required this.value, required this.label});

  final double value;
  final String label;

  @override
  Widget build(BuildContext context) {
    return Container(
      width: 108,
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 6),
      decoration: BoxDecoration(
        color: AppTheme.surface2Of(context),
        borderRadius: BorderRadius.circular(8),
        border: Border.all(color: AppTheme.borderSubtleOf(context)),
      ),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          Text(
            '\$${value.toStringAsFixed(2)}',
            textAlign: TextAlign.center,
            style: TextStyle(
              fontWeight: FontWeight.w700,
              fontSize: 12,
              color: AppTheme.textPrimaryOf(context),
            ),
          ),
          Text(
            label,
            textAlign: TextAlign.center,
            style: TextStyle(
              fontSize: 10,
              color: AppTheme.textSecondaryOf(context),
            ),
          ),
        ],
      ),
    );
  }
}

class _ChartXAxisLabels extends StatelessWidget {
  const _ChartXAxisLabels({
    required this.times,
    required this.isIntraday,
    required this.use24Hour,
  });

  final List<DateTime> times;
  final bool isIntraday;
  final bool use24Hour;

  @override
  Widget build(BuildContext context) {
    if (times.isEmpty) return const SizedBox.shrink();
    final style = TextStyle(
      fontSize: 10,
      color: AppTheme.textSecondaryOf(context),
    );

    // Open/midday/close ticks read far better than evenly spaced indices
    // (which would mostly land in the flat pre/post-market segments) — and
    // since the market session is proportionally narrow (8 of 24h), more
    // than 3 labels start to visually overlap in that space.
    final List<DateTime> ticks;
    if (isIntraday) {
      final day = DateTime(times.first.year, times.first.month, times.first.day);
      final open = day.add(const Duration(hours: _marketOpenHour));
      final close = day.add(const Duration(hours: _marketCloseHour));
      final mid = open.add(
        Duration(minutes: close.difference(open).inMinutes ~/ 2),
      );
      ticks = [open, mid, close];
    } else {
      final n = times.length;
      final tickCount = n < 4 ? n : 4;
      ticks = [
        for (var i = 0; i < tickCount; i++)
          times[tickCount == 1 ? 0 : (i * (n - 1) / (tickCount - 1)).round()],
      ];
    }

    final start = times.first;
    final end = times.last;

    // Positioned (not a plain Row) so each label lines up with where that
    // moment actually falls on the chart above — the chart itself is now
    // spaced by real elapsed time, not by index, so evenly-spaced labels
    // would drift out of alignment with it (e.g. "9:00 AM" sitting well
    // left of where 9am actually falls once a multi-hour flat pre-market
    // segment is given its real proportional width).
    return SizedBox(
      height: 14,
      width: double.infinity,
      child: LayoutBuilder(
        builder: (context, constraints) {
          final width = constraints.maxWidth;
          // Explicit tight size: a Stack with only Positioned children and
          // loose constraints (which CrossAxisAlignment.start gives its
          // Column children) collapses toward zero width, and its default
          // Clip.hardEdge then clips every label down near the origin.
          return SizedBox(
            width: width,
            height: 14,
            child: Stack(
              children: [
                for (final tick in ticks)
                  Positioned(
                    left: _fracFor(tick, start, end) * width,
                    child: FractionalTranslation(
                      translation: Offset(-_fracFor(tick, start, end), 0),
                      child: Text(
                        _chartLabel(tick, isIntraday: isIntraday, use24Hour: use24Hour),
                        style: style,
                      ),
                    ),
                  ),
              ],
            ),
          );
        },
      ),
    );
  }
}

class _ChartKindToggle extends StatelessWidget {
  const _ChartKindToggle({required this.value, required this.onChanged});

  final ChartKind value;
  final ValueChanged<ChartKind> onChanged;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.all(3),
      decoration: BoxDecoration(
        color: AppTheme.surface2Of(context),
        borderRadius: BorderRadius.circular(10),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          for (final k in ChartKind.values) _button(context, k),
        ],
      ),
    );
  }

  Widget _button(BuildContext context, ChartKind k) {
    final selected = k == value;
    return Tooltip(
      message: k.label,
      child: InkWell(
        borderRadius: BorderRadius.circular(8),
        onTap: () => onChanged(k),
        child: Container(
          padding: const EdgeInsets.all(6),
          decoration: BoxDecoration(
            color: selected
                ? AppTheme.accent.withValues(alpha: 0.22)
                : Colors.transparent,
            borderRadius: BorderRadius.circular(8),
          ),
          child: Icon(
            k.icon,
            size: 18,
            color: selected ? AppTheme.accent : AppTheme.textSecondaryOf(context),
          ),
        ),
      ),
    );
  }
}

/// Sharp-angled line chart of the raw value series, positioned by real
/// elapsed time (not index) so e.g. a flat multi-hour closed-market segment
/// takes up the width it actually spans.
class _LineChartView extends StatelessWidget {
  const _LineChartView({required this.values, required this.times});

  final List<double> values;
  final List<DateTime> times;

  @override
  Widget build(BuildContext context) {
    final spots = [
      for (var i = 0; i < values.length; i++)
        FlSpot(times[i].millisecondsSinceEpoch.toDouble(), values[i]),
    ];
    final minY = values.reduce((a, b) => a < b ? a : b);
    final maxY = values.reduce((a, b) => a > b ? a : b);
    final pad = (maxY - minY) * 0.08 + 1.0;
    final minX = times.first.millisecondsSinceEpoch.toDouble();
    final maxXRaw = times.last.millisecondsSinceEpoch.toDouble();
    final maxX = maxXRaw > minX ? maxXRaw : minX + 1;

    return LineChart(
      LineChartData(
        minX: minX,
        maxX: maxX,
        minY: minY - pad,
        maxY: maxY + pad,
        gridData: const FlGridData(show: false),
        titlesData: const FlTitlesData(show: false),
        borderData: FlBorderData(show: false),
        lineTouchData: const LineTouchData(enabled: false),
        lineBarsData: [
          LineChartBarData(
            spots: spots,
            isCurved: false,
            color: AppTheme.accent,
            barWidth: 2.5,
            dotData: const FlDotData(show: false),
            belowBarData: BarAreaData(
              show: true,
              color: AppTheme.accent.withValues(alpha: 0.12),
            ),
          ),
        ],
      ),
    );
  }
}

/// Day-over-day candles synthesized from the value series (open = previous
/// value, close = current value; the backend only tracks a single value per
/// day, so there's no real intraday high/low — a small fixed wick is drawn
/// around the body for a legible candlestick silhouette). Positioned by real
/// elapsed time, same as [_LineChartView].
class _CandlestickChartView extends StatelessWidget {
  const _CandlestickChartView({required this.values, required this.times});

  final List<double> values;
  final List<DateTime> times;

  @override
  Widget build(BuildContext context) {
    final candles = <_Candle>[
      for (var i = 1; i < values.length; i++)
        _Candle(time: times[i], open: values[i - 1], close: values[i]),
    ];
    if (candles.isEmpty) {
      candles.add(
        _Candle(time: times.last, open: values.first, close: values.first),
      );
    }
    final minY = candles.map((c) => c.low).reduce((a, b) => a < b ? a : b);
    final maxY = candles.map((c) => c.high).reduce((a, b) => a > b ? a : b);
    final pad = (maxY - minY) * 0.08 + 1.0;

    return CustomPaint(
      size: Size.infinite,
      painter: _CandlestickPainter(
        candles: candles,
        startTime: times.first,
        endTime: times.last,
        minY: minY - pad,
        maxY: maxY + pad,
        upColor: AppTheme.accent,
        downColor: AppTheme.danger,
      ),
    );
  }
}

class _Candle {
  _Candle({required this.time, required this.open, required this.close})
      : high = (open > close ? open : close) * 1.0025,
        low = (open < close ? open : close) * 0.9975;

  final DateTime time;
  final double open;
  final double close;
  final double high;
  final double low;

  bool get isUp => close >= open;
}

class _CandlestickPainter extends CustomPainter {
  _CandlestickPainter({
    required this.candles,
    required this.startTime,
    required this.endTime,
    required this.minY,
    required this.maxY,
    required this.upColor,
    required this.downColor,
  });

  final List<_Candle> candles;
  final DateTime startTime;
  final DateTime endTime;
  final double minY;
  final double maxY;
  final Color upColor;
  final Color downColor;

  @override
  void paint(Canvas canvas, Size size) {
    if (candles.isEmpty) return;
    final range = (maxY - minY).abs() < 1e-9 ? 1.0 : maxY - minY;
    double yFor(double v) => size.height - ((v - minY) / range) * size.height;

    final bodyWidth = (size.width / candles.length * 0.55).clamp(2.0, 16.0);

    for (final c in candles) {
      final cx = _fracFor(c.time, startTime, endTime) * size.width;
      final color = c.isUp ? upColor : downColor;

      canvas.drawLine(
        Offset(cx, yFor(c.high)),
        Offset(cx, yFor(c.low)),
        Paint()
          ..color = color
          ..strokeWidth = 1.4,
      );

      final bodyTop = yFor(c.isUp ? c.close : c.open);
      final bodyBottomRaw = yFor(c.isUp ? c.open : c.close);
      final bodyBottom =
          (bodyBottomRaw - bodyTop).abs() < 1.5 ? bodyTop + 1.5 : bodyBottomRaw;

      canvas.drawRect(
        Rect.fromLTRB(cx - bodyWidth / 2, bodyTop, cx + bodyWidth / 2, bodyBottom),
        Paint()..color = color,
      );
    }
  }

  @override
  bool shouldRepaint(covariant _CandlestickPainter oldDelegate) =>
      oldDelegate.candles != candles ||
      oldDelegate.minY != minY ||
      oldDelegate.maxY != maxY;
}

/// Floating bars connecting each day's value to the next — green when it
/// rose, red when it fell. Positioned by real elapsed time, same as
/// [_LineChartView]; built with a CustomPainter (rather than fl_chart's
/// BarChart, which only positions bars by discrete category index) so a
/// multi-hour closed-market bar can be as wide as it actually is.
class _WaterfallChartView extends StatelessWidget {
  const _WaterfallChartView({required this.values, required this.times});

  final List<double> values;
  final List<DateTime> times;

  @override
  Widget build(BuildContext context) {
    final bars = <_WaterfallBar>[
      for (var i = 1; i < values.length; i++)
        _WaterfallBar(time: times[i], from: values[i - 1], to: values[i]),
    ];
    if (bars.isEmpty) {
      bars.add(
        _WaterfallBar(time: times.last, from: values.first, to: values.first),
      );
    }
    final minY = values.reduce((a, b) => a < b ? a : b);
    final maxY = values.reduce((a, b) => a > b ? a : b);
    final pad = (maxY - minY) * 0.08 + 1.0;

    return CustomPaint(
      size: Size.infinite,
      painter: _WaterfallPainter(
        bars: bars,
        startTime: times.first,
        endTime: times.last,
        minY: minY - pad,
        maxY: maxY + pad,
        upColor: AppTheme.accent,
        downColor: AppTheme.danger,
      ),
    );
  }
}

class _WaterfallBar {
  _WaterfallBar({required this.time, required this.from, required this.to});

  final DateTime time;
  final double from;
  final double to;

  bool get isUp => to >= from;
}

class _WaterfallPainter extends CustomPainter {
  _WaterfallPainter({
    required this.bars,
    required this.startTime,
    required this.endTime,
    required this.minY,
    required this.maxY,
    required this.upColor,
    required this.downColor,
  });

  final List<_WaterfallBar> bars;
  final DateTime startTime;
  final DateTime endTime;
  final double minY;
  final double maxY;
  final Color upColor;
  final Color downColor;

  @override
  void paint(Canvas canvas, Size size) {
    if (bars.isEmpty) return;
    final range = (maxY - minY).abs() < 1e-9 ? 1.0 : maxY - minY;
    double yFor(double v) => size.height - ((v - minY) / range) * size.height;

    final barWidth = (size.width / bars.length * 0.6).clamp(3.0, 22.0);

    for (final b in bars) {
      final cx = _fracFor(b.time, startTime, endTime) * size.width;
      final color = b.isUp ? upColor : downColor;
      final top = yFor(b.isUp ? b.to : b.from);
      final bottomRaw = yFor(b.isUp ? b.from : b.to);
      final bottom = (bottomRaw - top).abs() < 1.5 ? top + 1.5 : bottomRaw;

      canvas.drawRRect(
        RRect.fromRectAndRadius(
          Rect.fromLTRB(cx - barWidth / 2, top, cx + barWidth / 2, bottom),
          const Radius.circular(2),
        ),
        Paint()..color = color,
      );
    }
  }

  @override
  bool shouldRepaint(covariant _WaterfallPainter oldDelegate) =>
      oldDelegate.bars != bars ||
      oldDelegate.minY != minY ||
      oldDelegate.maxY != maxY;
}

/// Quantity for display: whole numbers as-is, fractional values to up to
/// 8 decimal places (satoshi-level precision) with trailing zeros trimmed.
/// A fixed 4-place format would silently round small crypto fills toward
/// zero — e.g. a 0.00030612 BTC fill would round to "0.0003" or, for a
/// smaller fill still, all the way to "0.0000" despite being a real,
/// paid-for position.
String _formatQuantity(double qty) {
  if (qty == qty.roundToDouble()) return qty.toStringAsFixed(0);
  var s = qty.toStringAsFixed(8);
  s = s.replaceFirst(RegExp(r'0+$'), '');
  if (s.endsWith('.')) s += '0';
  return s;
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
          '${_formatQuantity(qty)} ${h['asset_type']}',
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
