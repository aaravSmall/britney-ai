import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../data/chart_kind.dart';
import '../data/chart_range.dart';
import '../services/api_service.dart';
import '../services/time_format_controller.dart';
import '../services/timezone_controller.dart';
import '../theme/app_theme.dart';
import '../utils/format.dart';
import '../widgets/buy_sell_bottom_sheet.dart';
import '../widgets/price_chart.dart';
import '../widgets/trade_rationale_sheet.dart';

/// Standalone stock/crypto detail page — takes a [ticker], so it works
/// identically regardless of how the caller got here (search results,
/// favorites list, a deep link, ...). Pushed as a top-level route
/// (/stock/:ticker in app_router.dart), same as trade_history_screen.dart,
/// not nested inside the bottom-nav shell.
///
/// [assetType] and [portfolioId] are optional query params, set only when
/// navigating from a known holding (dashboard_screen.dart's _HoldingTile):
/// [assetType] picks the price/quote data source (Yahoo for 'stock' —
/// this app has no public crypto quote/history endpoint, so 'crypto'
/// skips that section entirely and relies on the position chart below
/// instead), and [portfolioId] (plus a resolved-if-absent "my portfolio"
/// fallback) is who GET /portfolios/{id}/summary is asked whether this
/// ticker is a current holding — if so, a "Your position" section shows
/// cost basis, unrealized P&L, a per-holding price chart (reusing the
/// exact PortfolioSnapshot data source behind the portfolio-level chart,
/// scoped to this symbol — see portfolio_snapshot_service.py), and recent
/// trades for this ticker.
class StockDetailScreen extends StatefulWidget {
  const StockDetailScreen({
    super.key,
    required this.ticker,
    this.assetType = 'stock',
    this.portfolioId,
  });

  final String ticker;
  final String assetType;
  final int? portfolioId;

  @override
  State<StockDetailScreen> createState() => _StockDetailScreenState();
}

class _StockDetailScreenState extends State<StockDetailScreen> {
  bool _started = false;
  bool _loading = true;
  bool _notFound = false;
  String? _error;
  Map<String, dynamic>? _quote;

  ChartRange _range = ChartRange.month30;
  ChartKind _chartKind = ChartKind.line;
  bool _loadingHistory = false;
  List<Map<String, dynamic>> _candles = [];

  // null = not yet known (favorites list still loading) — the heart
  // renders in a neutral/disabled state until this resolves, rather than
  // guessing and possibly flashing the wrong icon.
  bool? _isFavorite;
  bool _favoriteBusy = false;

  // The current holding matching this ticker (any asset type), resolved
  // from widget.portfolioId or, if absent, the user's own portfolio —
  // null once resolved means "not currently held", not "still loading"
  // (see _positionLoading for that). Powers the "Your position" section
  // for both stocks and crypto.
  bool _positionLoading = true;
  Map<String, dynamic>? _position;
  int? _positionPortfolioId;
  bool _positionChartLoading = false;
  List<Map<String, dynamic>> _positionCandles = [];
  bool _recentTradesLoading = false;
  List<Map<String, dynamic>> _recentTrades = [];

  bool get _isCrypto => widget.assetType == 'crypto';

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    if (_started) return;
    _started = true;
    _loadPosition();
    if (!_isCrypto) {
      _loadQuoteAndFavoriteState();
      _loadHistory(_range);
    }
  }

  String get _ticker => widget.ticker.toUpperCase();

  /// Resolves which portfolio to check for a current holding (the given
  /// [widget.portfolioId], or the user's own if absent), then fetches
  /// that portfolio's holdings and looks for one matching this ticker.
  /// GET /portfolios/{id}/summary works identically for the caller's own
  /// portfolio as for any other (same auth rules as /dashboard), so this
  /// never needs to special-case "mine" vs. "an agent's" beyond picking
  /// the id.
  Future<void> _loadPosition() async {
    final api = context.read<ApiService>();
    setState(() => _positionLoading = true);
    try {
      var pid = widget.portfolioId;
      if (pid == null) {
        final portfoliosRes = await api.get('/portfolios');
        if (!mounted) return;
        if (portfoliosRes.statusCode == 200) {
          final list = (jsonDecode(portfoliosRes.body) as List<dynamic>)
              .map((e) => e as Map<String, dynamic>)
              .toList();
          for (final p in list) {
            if (p['owner_type'] == 'user') {
              pid = p['id'] as int;
              break;
            }
          }
        }
      }
      if (pid == null) {
        if (mounted) setState(() { _position = null; _positionLoading = false; });
        return;
      }

      final res = await api.get('/portfolios/$pid/summary');
      if (!mounted) return;
      if (res.statusCode != 200) {
        setState(() { _position = null; _positionLoading = false; });
        return;
      }
      final body = jsonDecode(res.body) as Map<String, dynamic>;
      final holdings = (body['holdings'] as List<dynamic>)
          .map((e) => e as Map<String, dynamic>)
          .toList();
      Map<String, dynamic>? match;
      for (final h in holdings) {
        if ((h['symbol'] as String).toUpperCase() == _ticker) {
          match = h;
          break;
        }
      }

      setState(() {
        _position = match;
        _positionPortfolioId = pid;
        _positionLoading = false;
      });
      if (match != null) {
        _loadRecentTrades(pid);
        if (_isCrypto) _loadPositionChart(pid);
      }
    } catch (_) {
      if (mounted) setState(() { _position = null; _positionLoading = false; });
    }
  }

  Future<void> _loadPositionChart(int portfolioId) async {
    final api = context.read<ApiService>();
    setState(() => _positionChartLoading = true);
    try {
      // .name (not .shortLabel) — the performance endpoints' `range`
      // param matches ChartRange's own enum names (today/week/month30/
      // ytd/fiveYear), unlike /stocks/{ticker}/history's Yahoo-flavored
      // shortLabel vocabulary (1D/1W/30D/YTD/5Y).
      final res = await api.get(
        '/portfolios/$portfolioId/performance/$_ticker?range=${_range.name}',
      );
      if (!mounted) return;
      if (res.statusCode == 200) {
        final list = jsonDecode(res.body) as List<dynamic>;
        setState(() {
          _positionCandles = list.map((e) => e as Map<String, dynamic>).toList();
          _positionChartLoading = false;
        });
      } else {
        setState(() { _positionCandles = []; _positionChartLoading = false; });
      }
    } catch (_) {
      if (mounted) setState(() { _positionCandles = []; _positionChartLoading = false; });
    }
  }

  Future<void> _loadRecentTrades(int portfolioId) async {
    final api = context.read<ApiService>();
    setState(() => _recentTradesLoading = true);
    try {
      final res = await api.get('/portfolios/$portfolioId/trades?symbol=$_ticker&limit=5');
      if (!mounted) return;
      if (res.statusCode == 200) {
        final list = jsonDecode(res.body) as List<dynamic>;
        setState(() {
          _recentTrades = list.map((e) => e as Map<String, dynamic>).toList();
          _recentTradesLoading = false;
        });
      } else {
        setState(() { _recentTrades = []; _recentTradesLoading = false; });
      }
    } catch (_) {
      if (mounted) setState(() { _recentTrades = []; _recentTradesLoading = false; });
    }
  }

  /// Fetches the quote and the user's favorites list in parallel. The
  /// favorites list is also how the initial heart state is determined
  /// (matching ticker client-side) — reusing GET /favorites rather than
  /// adding a new backend endpoint just for a single boolean. That list
  /// call already fetches a live quote for every favorited ticker
  /// server-side, so it's not free, but favorite lists are small (a
  /// handful of tickers for this app) and it's already cached
  /// server-side per ticker, so in practice this is a reasonable reuse,
  /// not a real hot path — a dedicated `GET /favorites/{ticker}` would be
  /// over-engineering for what this page needs.
  Future<void> _loadQuoteAndFavoriteState() async {
    final api = context.read<ApiService>();
    setState(() {
      _loading = true;
      _notFound = false;
      _error = null;
    });
    try {
      final results = await Future.wait([
        api.get('/stocks/$_ticker/quote'),
        api.get('/favorites'),
      ]);
      if (!mounted) return;
      final quoteRes = results[0];
      final favRes = results[1];

      if (quoteRes.statusCode == 404) {
        setState(() {
          _notFound = true;
          _loading = false;
        });
        return;
      }
      if (quoteRes.statusCode != 200) {
        setState(() {
          _error = 'Failed to load quote: ${quoteRes.body}';
          _loading = false;
        });
        return;
      }
      final quote = jsonDecode(quoteRes.body) as Map<String, dynamic>;

      bool? isFavorite;
      if (favRes.statusCode == 200) {
        final list = jsonDecode(favRes.body) as List<dynamic>;
        isFavorite = list.any((f) => (f as Map<String, dynamic>)['ticker'] == _ticker);
      }
      // A failed favorites fetch shouldn't block the rest of the page —
      // the heart just stays in its neutral/unknown state (disabled)
      // rather than the whole detail page erroring out over it.

      setState(() {
        _quote = quote;
        _isFavorite = isFavorite;
        _loading = false;
      });
    } catch (e) {
      if (mounted) {
        setState(() {
          _error = 'Network error: $e';
          _loading = false;
        });
      }
    }
  }

  Future<void> _loadHistory(ChartRange range) async {
    final api = context.read<ApiService>();
    setState(() => _loadingHistory = true);
    try {
      final res = await api.get('/stocks/$_ticker/history?range=${range.shortLabel}');
      if (!mounted) return;
      if (res.statusCode == 200) {
        final body = jsonDecode(res.body) as Map<String, dynamic>;
        final candles = (body['candles'] as List<dynamic>)
            .map((e) => e as Map<String, dynamic>)
            .toList();
        setState(() {
          _candles = candles;
          _loadingHistory = false;
        });
      } else {
        // Matches PriceChartView's own sparse-history handling — an
        // empty series renders its placeholder rather than an error;
        // the quote/header above still work fine on their own.
        setState(() {
          _candles = [];
          _loadingHistory = false;
        });
      }
    } catch (_) {
      if (mounted) {
        setState(() {
          _candles = [];
          _loadingHistory = false;
        });
      }
    }
  }

  void _onRangeSelected(ChartRange range) {
    if (range == _range) return;
    setState(() => _range = range);
    _loadHistory(range);
  }

  /// Crypto's range toggle drives the position chart instead of
  /// _loadHistory (there's no Yahoo history for crypto to fetch).
  void _onPositionRangeSelected(ChartRange range) {
    if (range == _range) return;
    setState(() => _range = range);
    final pid = _positionPortfolioId;
    if (pid != null) _loadPositionChart(pid);
  }

  Future<void> _toggleFavorite() async {
    if (_favoriteBusy) return;
    final wasFavorite = _isFavorite ?? false;
    final messenger = ScaffoldMessenger.of(context);
    final api = context.read<ApiService>();

    setState(() {
      _isFavorite = !wasFavorite;
      _favoriteBusy = true;
    });

    try {
      final res = wasFavorite
          ? await api.delete('/favorites/$_ticker')
          : await api.post('/favorites/$_ticker', null);
      if (!mounted) return;

      final ok = res.statusCode >= 200 && res.statusCode < 300;
      // 409 (already favorited) on a POST, or 404 (not favorited) on a
      // DELETE, both mean the server already agrees with the state we
      // just optimistically set — not a real failure to revert.
      final alreadyInDesiredState =
          (!wasFavorite && res.statusCode == 409) || (wasFavorite && res.statusCode == 404);

      if (!ok && !alreadyInDesiredState) {
        setState(() => _isFavorite = wasFavorite);
        messenger.showSnackBar(
          SnackBar(
            content: Text(_errorDetail(res.body)),
            backgroundColor: AppTheme.danger,
          ),
        );
      }
    } catch (e) {
      if (!mounted) return;
      setState(() => _isFavorite = wasFavorite);
      messenger.showSnackBar(
        SnackBar(content: Text('Network error: $e'), backgroundColor: AppTheme.danger),
      );
    } finally {
      if (mounted) setState(() => _favoriteBusy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final t = Theme.of(context).textTheme;

    // Crypto has no Yahoo quote/history/favorite in this app (see the
    // class doc comment) — a separate, simpler scaffold built entirely
    // from portfolio-holding data rather than threading `_isCrypto`
    // through every branch of the stock scaffold below.
    if (_isCrypto) return _buildCryptoScaffold(t);

    // Same reasoning as trade_history_screen.dart: this is a top-level
    // route outside StatefulShellRoute's _MainShell (the only widget that
    // paints the app's dark/light gradient), so no backgroundColor
    // override here — the theme's own scaffoldBackgroundColor already
    // renders correctly in both themes.
    return Scaffold(
      appBar: AppBar(
        title: Text(_ticker),
        actions: [
          if (!_loading && !_notFound && _error == null)
            IconButton(
              tooltip: (_isFavorite ?? false) ? 'Remove from favorites' : 'Add to favorites',
              onPressed: _favoriteBusy ? null : _toggleFavorite,
              icon: Icon(
                (_isFavorite ?? false) ? Icons.favorite : Icons.favorite_border,
                color: (_isFavorite ?? false) ? AppTheme.danger : null,
              ),
            ),
        ],
      ),
      body: _buildBody(t),
    );
  }

  Widget _buildBody(TextTheme t) {
    if (_loading) {
      return const Center(child: CircularProgressIndicator(color: AppTheme.accent));
    }

    if (_notFound) {
      return _StatusState(
        icon: Icons.search_off_rounded,
        message: "Couldn't find $_ticker.",
        onRetry: _loadQuoteAndFavoriteState,
      );
    }

    if (_error != null) {
      return _StatusState(
        icon: Icons.error_outline_rounded,
        message: _error!,
        iconColor: AppTheme.danger,
        onRetry: _loadQuoteAndFavoriteState,
      );
    }

    final quote = _quote!;
    final price = (quote['price'] as num).toDouble();
    final change = (quote['change'] as num?)?.toDouble();
    final changePct = (quote['change_pct'] as num?)?.toDouble();
    final positive = (change ?? 0) >= 0;
    final isIntraday = _range == ChartRange.today;
    final use24Hour = context.watch<TimeFormatController>().use24Hour;

    final displayZone = context.watch<TimezoneController>().location;
    final values = [for (final c in _candles) (c['close'] as num).toDouble()];
    final times = [
      for (final c in _candles)
        toDisplayZone(DateTime.parse(c['timestamp'] as String), displayZone),
    ];

    return RefreshIndicator(
      color: AppTheme.accent,
      onRefresh: () => Future.wait([_loadQuoteAndFavoriteState(), _loadHistory(_range)]),
      child: ListView(
        physics: const AlwaysScrollableScrollPhysics(),
        padding: const EdgeInsets.fromLTRB(20, 8, 20, 100),
        children: [
          Text(
            quote['company_name'] as String? ?? _ticker,
            style: t.titleMedium?.copyWith(fontWeight: FontWeight.w600),
          ),
          const SizedBox(height: 10),
          Text(
            '\$${price.toStringAsFixed(2)}',
            style: t.headlineMedium?.copyWith(fontWeight: FontWeight.w700, letterSpacing: -0.5),
          ),
          if (change != null && changePct != null) ...[
            const SizedBox(height: 4),
            Text(
              '${positive ? '+' : ''}\$${change.toStringAsFixed(2)} '
              '(${positive ? '+' : ''}${changePct.toStringAsFixed(2)}%)',
              style: t.bodyMedium?.copyWith(
                color: positive ? AppTheme.accent : AppTheme.danger,
                fontWeight: FontWeight.w600,
              ),
            ),
          ],
          const SizedBox(height: 16),
          Row(
            children: [
              Expanded(
                child: OutlinedButton(
                  onPressed: () => showBuySellSheet(
                    context,
                    ticker: _ticker,
                    assetType: widget.assetType,
                    side: 'sell',
                  ),
                  child: const Text('Sell'),
                ),
              ),
              const SizedBox(width: 10),
              Expanded(
                child: FilledButton(
                  onPressed: () => showBuySellSheet(
                    context,
                    ticker: _ticker,
                    assetType: widget.assetType,
                    side: 'buy',
                  ),
                  child: const Text('Buy'),
                ),
              ),
            ],
          ),
          if (_position != null) ...[
            const SizedBox(height: 24),
            _PositionCard(
              position: _position!,
              assetType: widget.assetType,
              portfolioId: _positionPortfolioId!,
              recentTrades: _recentTrades,
              recentTradesLoading: _recentTradesLoading,
            ),
          ],
          const SizedBox(height: 24),
          Row(
            children: [
              Text(
                'Price',
                style: t.titleSmall?.copyWith(fontWeight: FontWeight.w600, letterSpacing: 0.2),
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
                      selectedColor: AppTheme.accent.withValues(alpha: 0.22),
                      label: Text(r.shortLabel),
                      selected: _range == r,
                      onSelected: (_) => _onRangeSelected(r),
                    ),
                  ),
              ],
            ),
          ),
          const SizedBox(height: 12),
          Card(
            child: Padding(
              padding: const EdgeInsets.all(20),
              child: _loadingHistory
                  ? const SizedBox(
                      height: 180,
                      child: Center(child: CircularProgressIndicator(color: AppTheme.accent)),
                    )
                  : PriceChartView(
                      values: values,
                      times: times,
                      chartKind: _chartKind,
                      isIntraday: isIntraday,
                      use24Hour: use24Hour,
                      hasAnyHistory: _candles.isNotEmpty,
                      noHistoryMessage: 'No price history available for $_ticker.',
                      noDataInRangeMessage: 'No data in this range — try a wider range.',
                      notEnoughPointsMessage: 'Not enough data yet to chart this range.',
                    ),
            ),
          ),
          const SizedBox(height: 24),
          Text(
            'Key stats',
            style: t.titleSmall?.copyWith(fontWeight: FontWeight.w600, letterSpacing: 0.2),
          ),
          const SizedBox(height: 10),
          Card(
            child: Padding(
              padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
              child: Column(
                children: [
                  _StatRow('Market cap', _formatCompactCurrency(quote['market_cap'])),
                  _StatRow('P/E ratio', _formatDecimal(quote['pe_ratio_trailing'])),
                  _StatRow(
                    'Day range',
                    _formatRange(quote['day_low'], quote['day_high']),
                  ),
                  _StatRow(
                    '52-week range',
                    _formatRange(quote['fifty_two_week_low'], quote['fifty_two_week_high']),
                  ),
                  _StatRow('Volume', _formatCompactNumber(quote['volume'])),
                  _StatRow('Avg. volume', _formatCompactNumber(quote['average_volume'])),
                  _StatRow('Dividend yield', _formatPercent(quote['dividend_yield']), isLast: true),
                ],
              ),
            ),
          ),
          if ((quote['sector'] != null && (quote['sector'] as String).isNotEmpty) ||
              (quote['industry'] != null && (quote['industry'] as String).isNotEmpty) ||
              (quote['description'] != null && (quote['description'] as String).isNotEmpty)) ...[
            const SizedBox(height: 24),
            Text(
              'About',
              style: t.titleSmall?.copyWith(fontWeight: FontWeight.w600, letterSpacing: 0.2),
            ),
            const SizedBox(height: 10),
            Card(
              child: Padding(
                padding: const EdgeInsets.all(16),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    if (quote['sector'] != null || quote['industry'] != null)
                      Padding(
                        padding: const EdgeInsets.only(bottom: 10),
                        child: Wrap(
                          spacing: 8,
                          runSpacing: 8,
                          children: [
                            if (quote['sector'] != null)
                              _InfoChip(quote['sector'] as String),
                            if (quote['industry'] != null)
                              _InfoChip(quote['industry'] as String),
                          ],
                        ),
                      ),
                    if (quote['description'] != null &&
                        (quote['description'] as String).trim().isNotEmpty)
                      _ExpandableAbout(quote['description'] as String),
                  ],
                ),
              ),
            ),
          ],
        ],
      ),
    );
  }

  /// Crypto has no Yahoo quote/history in this app (stock_data.py is
  /// stock-only), so this branch is built entirely from the resolved
  /// holding: current price is the holding's own last_price, and the
  /// price chart reads the same PortfolioSnapshot-backed per-symbol
  /// series as the "Your position" section on the stock branch above,
  /// just promoted to the page's primary chart since there's no Yahoo
  /// chart to show alongside it.
  Widget _buildCryptoScaffold(TextTheme t) {
    final use24Hour = context.watch<TimeFormatController>().use24Hour;
    final position = _position;
    final lastPrice = (position?['last_price'] as num?)?.toDouble();
    final isIntraday = _range == ChartRange.today;

    final displayZone = context.watch<TimezoneController>().location;
    final values = [for (final c in _positionCandles) (c['price'] as num).toDouble()];
    final times = [
      for (final c in _positionCandles)
        toDisplayZone(DateTime.parse(c['timestamp'] as String), displayZone),
    ];

    return Scaffold(
      appBar: AppBar(title: Text(_ticker)),
      body: _positionLoading
          ? const Center(child: CircularProgressIndicator(color: AppTheme.accent))
          : RefreshIndicator(
              color: AppTheme.accent,
              onRefresh: _loadPosition,
              child: ListView(
                physics: const AlwaysScrollableScrollPhysics(),
                padding: const EdgeInsets.fromLTRB(20, 8, 20, 100),
                children: [
                  Text(_ticker, style: t.titleMedium?.copyWith(fontWeight: FontWeight.w600)),
                  const SizedBox(height: 10),
                  Text(
                    lastPrice != null ? '\$${lastPrice.toStringAsFixed(2)}' : '—',
                    style:
                        t.headlineMedium?.copyWith(fontWeight: FontWeight.w700, letterSpacing: -0.5),
                  ),
                  const SizedBox(height: 16),
                  Row(
                    children: [
                      Expanded(
                        child: OutlinedButton(
                          onPressed: () => showBuySellSheet(
                            context,
                            ticker: _ticker,
                            assetType: 'crypto',
                            side: 'sell',
                          ),
                          child: const Text('Sell'),
                        ),
                      ),
                      const SizedBox(width: 10),
                      Expanded(
                        child: FilledButton(
                          onPressed: () => showBuySellSheet(
                            context,
                            ticker: _ticker,
                            assetType: 'crypto',
                            side: 'buy',
                          ),
                          child: const Text('Buy'),
                        ),
                      ),
                    ],
                  ),
                  if (position != null) ...[
                    const SizedBox(height: 24),
                    _PositionCard(
                      position: position,
                      assetType: 'crypto',
                      portfolioId: _positionPortfolioId!,
                      recentTrades: _recentTrades,
                      recentTradesLoading: _recentTradesLoading,
                    ),
                    const SizedBox(height: 24),
                    Row(
                      children: [
                        Text(
                          'Price history',
                          style:
                              t.titleSmall?.copyWith(fontWeight: FontWeight.w600, letterSpacing: 0.2),
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
                                selectedColor: AppTheme.accent.withValues(alpha: 0.22),
                                label: Text(r.shortLabel),
                                selected: _range == r,
                                onSelected: (_) => _onPositionRangeSelected(r),
                              ),
                            ),
                        ],
                      ),
                    ),
                    const SizedBox(height: 12),
                    Card(
                      child: Padding(
                        padding: const EdgeInsets.all(20),
                        child: _positionChartLoading
                            ? const SizedBox(
                                height: 180,
                                child: Center(
                                  child: CircularProgressIndicator(color: AppTheme.accent),
                                ),
                              )
                            : PriceChartView(
                                values: values,
                                times: times,
                                chartKind: _chartKind,
                                isIntraday: isIntraday,
                                use24Hour: use24Hour,
                                hasAnyHistory: _positionCandles.isNotEmpty,
                                noHistoryMessage: 'No price history captured yet for $_ticker.',
                                noDataInRangeMessage: 'No data in this range — try a wider range.',
                                notEnoughPointsMessage: 'Not enough data yet to chart this range.',
                              ),
                      ),
                    ),
                  ] else
                    Padding(
                      padding: const EdgeInsets.only(top: 40),
                      child: Center(
                        child: Text(
                          "You don't currently hold $_ticker.",
                          style: t.bodyMedium?.copyWith(color: AppTheme.textSecondaryOf(context)),
                        ),
                      ),
                    ),
                ],
              ),
            ),
    );
  }
}

String _errorDetail(String body) {
  try {
    final decoded = jsonDecode(body);
    if (decoded is Map && decoded['detail'] != null) {
      return decoded['detail'].toString();
    }
  } catch (_) {
    // Fall through to generic message below.
  }
  return 'Something went wrong';
}

String _formatDecimal(dynamic v) {
  if (v == null) return '—';
  return (v as num).toStringAsFixed(2);
}

String _formatPercent(dynamic v) {
  if (v == null) return '—';
  // Backend already returns this as a percentage value (e.g. 0.35 means
  // 0.35%), not a 0-1 fraction — see app/services/stock_data.py's quote().
  return '${(v as num).toStringAsFixed(2)}%';
}

String _formatRange(dynamic low, dynamic high) {
  if (low == null || high == null) return '—';
  return '\$${(low as num).toStringAsFixed(2)} – \$${(high as num).toStringAsFixed(2)}';
}

/// Compact "4.57T"/"57.2M"/"1.2K" style formatting for large plain
/// numbers (volume, average volume).
String _formatCompactNumber(dynamic v) {
  if (v == null) return '—';
  final n = (v as num).toDouble();
  final abs = n.abs();
  if (abs >= 1e12) return '${(n / 1e12).toStringAsFixed(2)}T';
  if (abs >= 1e9) return '${(n / 1e9).toStringAsFixed(2)}B';
  if (abs >= 1e6) return '${(n / 1e6).toStringAsFixed(2)}M';
  if (abs >= 1e3) return '${(n / 1e3).toStringAsFixed(2)}K';
  return n.toStringAsFixed(0);
}

/// Same compact scaling as [_formatCompactNumber], with a leading `$`
/// for dollar-denominated figures (market cap).
String _formatCompactCurrency(dynamic v) {
  if (v == null) return '—';
  return '\$${_formatCompactNumber(v)}';
}

class _StatRow extends StatelessWidget {
  const _StatRow(this.label, this.value, {this.isLast = false, this.valueColor});

  final String label;
  final String value;
  final bool isLast;
  final Color? valueColor;

  @override
  Widget build(BuildContext context) {
    final t = Theme.of(context).textTheme;
    return Container(
      padding: const EdgeInsets.symmetric(vertical: 12),
      decoration: isLast
          ? null
          : BoxDecoration(
              border: Border(bottom: BorderSide(color: AppTheme.borderSubtleOf(context))),
            ),
      child: Row(
        children: [
          Text(
            label,
            style: t.bodyMedium?.copyWith(color: AppTheme.textSecondaryOf(context)),
          ),
          const Spacer(),
          Text(
            value,
            style: t.bodyMedium?.copyWith(fontWeight: FontWeight.w600, color: valueColor),
          ),
        ],
      ),
    );
  }
}

/// Cost basis, market value, and unrealized P&L for a current holding —
/// avg_cost/quantity/last_price/market_value already come straight from
/// GET /portfolios/{id}/summary's HoldingOut (avg_cost is itself a
/// running weighted-average maintained per fill in
/// portfolio_service.record_trade_fill, i.e. already "computed from
/// trade history"; recomputing it independently here would just be the
/// same arithmetic against the same rows, so this reuses the stored
/// value instead of re-deriving it from raw Trade rows). Shown for both
/// stocks and crypto — see StockDetailScreen's class doc comment.
class _PositionCard extends StatelessWidget {
  const _PositionCard({
    required this.position,
    required this.assetType,
    required this.portfolioId,
    required this.recentTrades,
    required this.recentTradesLoading,
  });

  final Map<String, dynamic> position;
  final String assetType;
  final int portfolioId;
  final List<Map<String, dynamic>> recentTrades;
  final bool recentTradesLoading;

  @override
  Widget build(BuildContext context) {
    final t = Theme.of(context).textTheme;
    final use24Hour = context.watch<TimeFormatController>().use24Hour;

    final qty = (position['quantity'] as num).toDouble();
    final avgCost = (position['avg_cost'] as num).toDouble();
    final lastPrice = (position['last_price'] as num?)?.toDouble();
    final marketValue = (position['market_value'] as num?)?.toDouble() ??
        (lastPrice != null ? qty * lastPrice : null);
    final costBasis = avgCost * qty;
    final pnlRaw = marketValue != null ? marketValue - costBasis : null;
    final pnlPctRaw = (pnlRaw != null && costBasis != 0) ? pnlRaw / costBasis * 100 : null;
    // Rounded to cents/hundredths, with -0.0 normalized to 0.0 — floating-
    // point noise in market_value (e.g. 1566.6499999999999 vs. an exact
    // 1566.65 cost basis) otherwise renders an at-cost position as
    // "$-0.00 (-0.00%)" in red instead of a flat $0.00.
    final pnl = _roundNonNegativeZero(pnlRaw);
    final pnlPct = _roundNonNegativeZero(pnlPctRaw);
    final pnlPositive = (pnl ?? 0) >= 0;

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          'Your position',
          style: t.titleSmall?.copyWith(fontWeight: FontWeight.w600, letterSpacing: 0.2),
        ),
        const SizedBox(height: 10),
        Card(
          child: Padding(
            padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
            child: Column(
              children: [
                _StatRow('Quantity', '${formatQuantity(qty)} $assetType'),
                _StatRow('Avg. cost', '\$${avgCost.toStringAsFixed(2)} / unit'),
                _StatRow('Cost basis', '\$${costBasis.toStringAsFixed(2)}'),
                _StatRow(
                  'Market value',
                  marketValue != null ? '\$${marketValue.toStringAsFixed(2)}' : '—',
                ),
                _StatRow(
                  'Unrealized P&L',
                  pnl != null && pnlPct != null
                      ? '${pnlPositive ? '+' : ''}\$${pnl.toStringAsFixed(2)} '
                          '(${pnlPositive ? '+' : ''}${pnlPct.toStringAsFixed(2)}%)'
                      : '—',
                  isLast: true,
                  valueColor: pnl != null ? (pnlPositive ? AppTheme.accent : AppTheme.danger) : null,
                ),
              ],
            ),
          ),
        ),
        const SizedBox(height: 16),
        Text(
          'Recent trades',
          style: t.titleSmall?.copyWith(fontWeight: FontWeight.w600, letterSpacing: 0.2),
        ),
        const SizedBox(height: 10),
        if (recentTradesLoading)
          const Padding(
            padding: EdgeInsets.symmetric(vertical: 20),
            child: Center(
              child: SizedBox(
                width: 22,
                height: 22,
                child: CircularProgressIndicator(color: AppTheme.accent, strokeWidth: 2),
              ),
            ),
          )
        else if (recentTrades.isEmpty)
          Card(
            child: Padding(
              padding: const EdgeInsets.all(16),
              child: Text(
                'No trades yet for this ticker.',
                style: t.bodySmall?.copyWith(color: AppTheme.textSecondaryOf(context)),
              ),
            ),
          )
        else
          Card(
            child: Column(
              children: [
                for (var i = 0; i < recentTrades.length; i++)
                  _RecentTradeRow(
                    recentTrades[i],
                    use24Hour: use24Hour,
                    isLast: i == recentTrades.length - 1,
                    portfolioId: portfolioId,
                  ),
              ],
            ),
          ),
      ],
    );
  }
}

class _RecentTradeRow extends StatelessWidget {
  const _RecentTradeRow(
    this.trade, {
    required this.use24Hour,
    required this.isLast,
    required this.portfolioId,
  });

  final Map<String, dynamic> trade;
  final bool use24Hour;
  final bool isLast;
  final int portfolioId;

  @override
  Widget build(BuildContext context) {
    final t = Theme.of(context).textTheme;
    final side = trade['side'] as String;
    final buy = side == 'buy';
    final sideColor = buy ? AppTheme.accent : AppTheme.danger;
    final qty = (trade['quantity'] as num).toDouble();
    final status = trade['status'] as String? ?? 'filled';
    final isPending = status == 'pending';
    // Null for a pending (queued off-hours) trade — see TradeOut's
    // docstring — same nullable handling as trade_history_screen.dart's
    // _TradeTile.
    final price = (trade['price'] as num?)?.toDouble();
    final source = trade['source'] as String;
    final isAgent = source == 'agent';
    final tzController = context.watch<TimezoneController>();
    final showEt = tzController.zoneId != 'America/New_York';
    final timestampUtc = DateTime.parse(trade['timestamp'] as String);
    final timestamp = toDisplayZone(timestampUtc, tzController.location);

    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 10),
      decoration: isLast
          ? null
          : BoxDecoration(
              border: Border(bottom: BorderSide(color: AppTheme.borderSubtleOf(context))),
            ),
      child: Row(
        children: [
          Container(
            padding: const EdgeInsets.symmetric(horizontal: 7, vertical: 2),
            decoration: BoxDecoration(
              color: sideColor.withValues(alpha: 0.12),
              borderRadius: BorderRadius.circular(6),
            ),
            child: Text(
              side.toUpperCase(),
              style: TextStyle(fontSize: 10, fontWeight: FontWeight.w700, color: sideColor),
            ),
          ),
          const SizedBox(width: 10),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  isPending
                      ? '${formatQuantity(qty)} · Queued for open'
                      : '${formatQuantity(qty)} @ \$${price!.toStringAsFixed(2)}',
                  style: t.bodyMedium?.copyWith(fontWeight: FontWeight.w600),
                ),
                Text(
                  isPending
                      ? 'Pending · ${source == 'agent' ? 'Agent' : 'You'}'
                      : '${formatTradeTime(timestamp, use24Hour)}'
                          '${showEt ? ' (${formatEasternSuffix(timestampUtc, use24Hour)})' : ''}'
                          ' · ${source == 'agent' ? 'Agent' : 'You'}',
                  style: t.bodySmall?.copyWith(color: AppTheme.textSecondaryOf(context)),
                ),
              ],
            ),
          ),
          if (!isPending)
            Text(
              '\$${(qty * price!).toStringAsFixed(2)}',
              style: t.bodyMedium?.copyWith(fontWeight: FontWeight.w600),
            ),
          if (isAgent) ...[
            const SizedBox(width: 6),
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
    );
  }
}

/// Company description, truncated to its first ~3 sentences with a "Read
/// more"/"Read less" toggle — Yahoo/yfinance descriptions are often a
/// full multi-paragraph bio, and the About card is meant to be a glance,
/// not a wall of text.
class _ExpandableAbout extends StatefulWidget {
  const _ExpandableAbout(this.text);

  final String text;

  @override
  State<_ExpandableAbout> createState() => _ExpandableAboutState();
}

class _ExpandableAboutState extends State<_ExpandableAbout> {
  bool _expanded = false;

  @override
  Widget build(BuildContext context) {
    final t = Theme.of(context).textTheme;
    final full = widget.text.trim();
    final truncated = _truncateToSentences(full, 3);
    final canExpand = truncated.length < full.length;

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          _expanded || !canExpand ? full : truncated,
          style: t.bodyMedium?.copyWith(height: 1.45),
        ),
        if (canExpand)
          Padding(
            padding: const EdgeInsets.only(top: 6),
            child: GestureDetector(
              onTap: () => setState(() => _expanded = !_expanded),
              child: Text(
                _expanded ? 'Read less' : 'Read more',
                style: t.bodyMedium?.copyWith(
                  color: AppTheme.accent,
                  fontWeight: FontWeight.w600,
                ),
              ),
            ),
          ),
      ],
    );
  }
}

/// First [maxSentences] sentences of [text] (split on '.'/'!'/'?'
/// followed by whitespace or end-of-string) — descriptions from this data
/// source are one long paragraph, not multiple, so a sentence boundary is
/// what actually shortens them, not a paragraph break. Returns [text]
/// unchanged if it doesn't have more than [maxSentences] to begin with.
String _truncateToSentences(String text, int maxSentences) {
  final sentences = RegExp(r'[^.!?]+[.!?]+(?:\s+|$)').allMatches(text).toList();
  if (sentences.length <= maxSentences) return text;
  return text.substring(0, sentences[maxSentences - 1].end).trimRight();
}

/// Rounds to 2 decimal places and normalizes -0.0 to 0.0 — without this,
/// a value like -1.1e-13 (floating-point noise from a market_value vs.
/// cost_basis subtraction that should net to exactly zero) still prints
/// as "-0.00" via toStringAsFixed, since Dart's formatting preserves the
/// sign bit even on a value that rounds to zero.
double? _roundNonNegativeZero(double? v) {
  if (v == null) return null;
  final rounded = double.parse(v.toStringAsFixed(2));
  return rounded == 0 ? 0.0 : rounded;
}

class _InfoChip extends StatelessWidget {
  const _InfoChip(this.label);

  final String label;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 5),
      decoration: BoxDecoration(
        color: AppTheme.surface2Of(context),
        borderRadius: BorderRadius.circular(8),
        border: Border.all(color: AppTheme.borderSubtleOf(context)),
      ),
      child: Text(
        label,
        style: Theme.of(context)
            .textTheme
            .bodySmall
            ?.copyWith(color: AppTheme.textSecondaryOf(context)),
      ),
    );
  }
}

/// Shared shell for the not-found/error states — same icon/text/button
/// structure trade_history_screen.dart's own _StatusState already uses
/// (duplicated, not imported: that class is private to its file, and
/// this app's existing convention is per-screen private helpers rather
/// than a shared model/widget library for small things like this — see
/// trade_history_screen.dart's own header comment on the same tradeoff).
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
