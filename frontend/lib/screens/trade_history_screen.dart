import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../services/api_service.dart';
import '../services/time_format_controller.dart';
import '../theme/app_theme.dart';
import '../utils/format.dart';
import '../widgets/trade_rationale_sheet.dart';

/// One page of trade history: limit/offset pagination via
/// GET /portfolios/{id}/trades, matching the endpoint built in Sprint 1.
/// Not wrapped in a shared model class — the rest of this app parses API
/// responses as raw maps inline (see dashboard_screen.dart), so this
/// follows the same convention rather than introducing a new one.
typedef _PageResult = ({
  List<Map<String, dynamic>>? trades,
  bool notFound,
  String? error,
});

class TradeHistoryScreen extends StatefulWidget {
  const TradeHistoryScreen({super.key, required this.portfolioId});

  // Nullable: a malformed/missing id in the URL (e.g. a hand-edited link)
  // should show the same friendly not-found state as a real 404, not crash.
  final int? portfolioId;

  @override
  State<TradeHistoryScreen> createState() => _TradeHistoryScreenState();
}

class _TradeHistoryScreenState extends State<TradeHistoryScreen> {
  static const _pageSize = 50;

  final _scroll = ScrollController();
  final List<Map<String, dynamic>> _trades = [];
  String _sourceFilter = 'all';
  bool _loading = false;
  bool _loadingMore = false;
  bool _hasMore = true;
  bool _notFound = false;
  String? _error;

  @override
  void initState() {
    super.initState();
    _scroll.addListener(_onScroll);
    _loadForCurrentPortfolio();
  }

  @override
  void didUpdateWidget(covariant TradeHistoryScreen oldWidget) {
    super.didUpdateWidget(oldWidget);
    // GoRouter reuses this State across navigations that match the same
    // route pattern (e.g. /trade-history/2 -> /trade-history/3), so a
    // changed id needs to explicitly reset and refetch rather than
    // relying on initState, which only runs once per State instance.
    if (oldWidget.portfolioId != widget.portfolioId) {
      _trades.clear();
      _sourceFilter = 'all';
      _hasMore = true;
      _loadForCurrentPortfolio();
    }
  }

  void _loadForCurrentPortfolio() {
    if (widget.portfolioId == null) {
      setState(() => _notFound = true);
    } else {
      _loadInitial();
    }
  }

  @override
  void dispose() {
    _scroll.removeListener(_onScroll);
    _scroll.dispose();
    super.dispose();
  }

  void _onScroll() {
    if (!_scroll.hasClients || _loadingMore || !_hasMore || _loading) return;
    if (_scroll.position.pixels >= _scroll.position.maxScrollExtent - 300) {
      _loadMore();
    }
  }

  Future<_PageResult> _fetchPage(int offset) async {
    final portfolioId = widget.portfolioId;
    if (portfolioId == null) {
      return (trades: null, notFound: true, error: null);
    }
    final api = context.read<ApiService>();
    final params = StringBuffer('limit=$_pageSize&offset=$offset');
    if (_sourceFilter != 'all') params.write('&source=$_sourceFilter');
    try {
      final res = await api.get('/portfolios/$portfolioId/trades?$params');
      if (res.statusCode == 404) {
        return (trades: null, notFound: true, error: null);
      }
      if (res.statusCode != 200) {
        return (
          trades: null,
          notFound: false,
          error: 'Failed to load trade history (${res.statusCode}).',
        );
      }
      final list = jsonDecode(res.body) as List<dynamic>;
      return (
        trades: list.map((e) => e as Map<String, dynamic>).toList(),
        notFound: false,
        error: null,
      );
    } catch (e) {
      return (trades: null, notFound: false, error: 'Network error: $e');
    }
  }

  Future<void> _loadInitial() async {
    setState(() {
      _loading = true;
      _error = null;
      _notFound = false;
    });
    final result = await _fetchPage(0);
    if (!mounted) return;
    if (result.notFound) {
      setState(() {
        _notFound = true;
        _loading = false;
      });
      return;
    }
    if (result.error != null) {
      setState(() {
        _error = result.error;
        _loading = false;
      });
      return;
    }
    setState(() {
      _trades
        ..clear()
        ..addAll(result.trades!);
      _hasMore = result.trades!.length == _pageSize;
      _loading = false;
    });
  }

  Future<void> _loadMore() async {
    setState(() => _loadingMore = true);
    final result = await _fetchPage(_trades.length);
    if (!mounted) return;
    if (result.trades == null) {
      // A page past the first failing shouldn't blow away what's already
      // on screen — just stop paginating and leave the loaded rows as-is.
      setState(() {
        _loadingMore = false;
        _hasMore = false;
      });
      return;
    }
    setState(() {
      _trades.addAll(result.trades!);
      _hasMore = result.trades!.length == _pageSize;
      _loadingMore = false;
    });
  }

  void _onFilterChanged(String v) {
    if (v == _sourceFilter) return;
    setState(() => _sourceFilter = v);
    _loadInitial();
  }

  @override
  Widget build(BuildContext context) {
    final t = Theme.of(context).textTheme;

    // No backgroundColor override (unlike dashboard_screen.dart's Scaffolds):
    // this route is pushed at the top level, outside StatefulShellRoute's
    // _MainShell — the only widget that paints the dark/light gradient
    // behind shell-tab screens. Colors.transparent here would show nothing
    // but Flutter's default white canvas, i.e. this always renders "light"
    // regardless of theme. Falling back to the theme's real
    // scaffoldBackgroundColor (a flat, correctly dark/light color) matches
    // how onboarding_screen.dart and login_screen.dart — the app's other
    // top-level routes — already handle this.
    return Scaffold(
      appBar: AppBar(
        title: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text('Trade History', style: t.titleLarge?.copyWith(fontWeight: FontWeight.w600)),
            Text(
              'Your trades and agent activity',
              style: t.bodySmall?.copyWith(color: AppTheme.textSecondaryOf(context)),
            ),
          ],
        ),
      ),
      body: Column(
        children: [
          Padding(
            padding: const EdgeInsets.fromLTRB(20, 12, 20, 4),
            child: SizedBox(
              width: double.infinity,
              child: SegmentedButton<String>(
                segments: const [
                  ButtonSegment(value: 'all', label: Text('All')),
                  ButtonSegment(value: 'user', label: Text('You')),
                  ButtonSegment(value: 'agent', label: Text('Agent')),
                ],
                selected: {_sourceFilter},
                showSelectedIcon: false,
                onSelectionChanged: (s) => _onFilterChanged(s.first),
              ),
            ),
          ),
          Expanded(child: _buildBody(context)),
        ],
      ),
    );
  }

  Widget _buildBody(BuildContext context) {
    if (_loading) {
      return const Center(child: CircularProgressIndicator(color: AppTheme.accent));
    }

    if (_notFound) {
      return _StatusState(
        icon: Icons.search_off_rounded,
        message: "This portfolio couldn't be found.",
        onRetry: _loadInitial,
      );
    }

    if (_error != null) {
      return _StatusState(
        icon: Icons.error_outline_rounded,
        message: _error!,
        iconColor: AppTheme.danger,
        onRetry: _loadInitial,
      );
    }

    if (_trades.isEmpty) {
      final filtered = _sourceFilter != 'all';
      return _StatusState(
        icon: Icons.receipt_long_rounded,
        message: filtered
            ? "No ${_sourceFilter == 'agent' ? 'agent' : 'your'} trades yet — try the 'All' filter."
            : 'No trades yet — they\'ll show up here once you (or the agent) start trading.',
      );
    }

    return RefreshIndicator(
      color: AppTheme.accent,
      onRefresh: _loadInitial,
      child: ListView.builder(
        controller: _scroll,
        physics: const AlwaysScrollableScrollPhysics(),
        padding: const EdgeInsets.fromLTRB(20, 12, 20, 100),
        itemCount: _trades.length + (_hasMore ? 1 : 0),
        itemBuilder: (context, i) {
          if (i >= _trades.length) {
            return const Padding(
              padding: EdgeInsets.symmetric(vertical: 20),
              child: Center(
                child: SizedBox(
                  width: 22,
                  height: 22,
                  child: CircularProgressIndicator(color: AppTheme.accent, strokeWidth: 2),
                ),
              ),
            );
          }
          return _TradeTile(_trades[i], portfolioId: widget.portfolioId!);
        },
      ),
    );
  }
}

class _TradeTile extends StatelessWidget {
  const _TradeTile(this.trade, {required this.portfolioId});

  final Map<String, dynamic> trade;
  final int portfolioId;

  @override
  Widget build(BuildContext context) {
    final t = Theme.of(context).textTheme;
    final use24Hour = context.watch<TimeFormatController>().use24Hour;

    final symbol = trade['symbol'] as String;
    final assetType = trade['asset_type'] as String;
    final side = trade['side'] as String;
    final source = trade['source'] as String;
    final quantity = (trade['quantity'] as num).toDouble();
    final price = (trade['price'] as num).toDouble();
    final timestamp = DateTime.parse(trade['timestamp'] as String).toLocal();
    final isAgent = source == 'agent';

    final buy = side == 'buy';
    final sideColor = buy ? AppTheme.accent : AppTheme.danger;
    final qtyLabel = formatQuantity(quantity);

    return Card(
      margin: const EdgeInsets.only(bottom: 10),
      child: ListTile(
        contentPadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 6),
        leading: CircleAvatar(
          backgroundColor: sideColor.withValues(alpha: 0.15),
          child: Icon(
            buy ? Icons.arrow_upward_rounded : Icons.arrow_downward_rounded,
            color: sideColor,
            size: 20,
          ),
        ),
        title: Row(
          children: [
            Text(symbol, style: const TextStyle(fontWeight: FontWeight.w700)),
            const SizedBox(width: 8),
            _SideBadge(side: side, color: sideColor),
          ],
        ),
        subtitle: Text(
          '$qtyLabel $assetType @ \$${price.toStringAsFixed(2)} · ${formatTradeTime(timestamp, use24Hour)}',
          style: t.bodySmall?.copyWith(color: AppTheme.textSecondaryOf(context)),
        ),
        trailing: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.end,
          children: [
            Text(
              '\$${(quantity * price).toStringAsFixed(2)}',
              style: t.titleSmall?.copyWith(fontWeight: FontWeight.w600),
            ),
            const SizedBox(height: 6),
            Row(
              mainAxisSize: MainAxisSize.min,
              children: [
                _SourceBadge(source: source),
                if (isAgent) ...[
                  const SizedBox(width: 4),
                  InkWell(
                    borderRadius: BorderRadius.circular(12),
                    onTap: () => showTradeRationaleSheet(
                      context,
                      portfolioId: portfolioId,
                      tradeId: trade['id'] as int,
                    ),
                    child: const Padding(
                      padding: EdgeInsets.all(2),
                      child: Icon(Icons.info_outline_rounded, size: 16, color: AppTheme.accent),
                    ),
                  ),
                ],
              ],
            ),
          ],
        ),
      ),
    );
  }
}

class _SideBadge extends StatelessWidget {
  const _SideBadge({required this.side, required this.color});

  final String side;
  final Color color;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 7, vertical: 2),
      decoration: BoxDecoration(
        color: color.withValues(alpha: 0.12),
        borderRadius: BorderRadius.circular(6),
      ),
      child: Text(
        side.toUpperCase(),
        style: TextStyle(fontSize: 10, fontWeight: FontWeight.w700, color: color),
      ),
    );
  }
}

class _SourceBadge extends StatelessWidget {
  const _SourceBadge({required this.source});

  final String source;

  @override
  Widget build(BuildContext context) {
    final isAgent = source == 'agent';
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 7, vertical: 3),
      decoration: BoxDecoration(
        color: AppTheme.surface2Of(context),
        borderRadius: BorderRadius.circular(6),
        border: Border.all(color: AppTheme.borderSubtleOf(context)),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(
            isAgent ? Icons.auto_awesome_rounded : Icons.person_outline_rounded,
            size: 11,
            color: isAgent ? AppTheme.accent : AppTheme.textSecondaryOf(context),
          ),
          const SizedBox(width: 3),
          Text(
            isAgent ? 'Agent' : 'You',
            style: TextStyle(
              fontSize: 10,
              fontWeight: FontWeight.w600,
              color: AppTheme.textSecondaryOf(context),
            ),
          ),
        ],
      ),
    );
  }
}

/// Shared shell for the empty state (no retry) and error/not-found states
/// (with retry) — same icon/text/button structure the dashboard's error
/// state and Sprint 1's sparse-chart placeholder already use.
class _StatusState extends StatelessWidget {
  const _StatusState({
    required this.icon,
    required this.message,
    this.iconColor,
    this.onRetry,
  });

  final IconData icon;
  final String message;
  final Color? iconColor;
  final VoidCallback? onRetry;

  @override
  Widget build(BuildContext context) {
    final t = Theme.of(context).textTheme;
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(32),
        child: Column(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            Icon(icon, size: 40, color: iconColor ?? AppTheme.textSecondaryOf(context)),
            const SizedBox(height: 16),
            Text(
              message,
              textAlign: TextAlign.center,
              style: t.bodyMedium?.copyWith(color: AppTheme.textSecondaryOf(context)),
            ),
            if (onRetry != null) ...[
              const SizedBox(height: 16),
              FilledButton(onPressed: onRetry, child: const Text('Try again')),
            ],
          ],
        ),
      ),
    );
  }
}
