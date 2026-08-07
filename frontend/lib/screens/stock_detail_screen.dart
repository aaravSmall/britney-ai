import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../data/chart_kind.dart';
import '../data/chart_range.dart';
import '../services/api_service.dart';
import '../services/time_format_controller.dart';
import '../theme/app_theme.dart';
import '../widgets/buy_sell_bottom_sheet.dart';
import '../widgets/price_chart.dart';

/// Standalone stock detail page — takes only a [ticker], so it works
/// identically regardless of how the caller got here (search results,
/// favorites list, a deep link, ...). Pushed as a top-level route
/// (/stock/:ticker in app_router.dart), same as trade_history_screen.dart,
/// not nested inside the bottom-nav shell.
class StockDetailScreen extends StatefulWidget {
  const StockDetailScreen({super.key, required this.ticker});

  final String ticker;

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

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    if (_started) return;
    _started = true;
    _loadQuoteAndFavoriteState();
    _loadHistory(_range);
  }

  String get _ticker => widget.ticker.toUpperCase();

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

    final values = [for (final c in _candles) (c['close'] as num).toDouble()];
    final times = [
      for (final c in _candles) DateTime.parse(c['timestamp'] as String).toLocal(),
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
                    assetType: 'stock',
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
                    assetType: 'stock',
                    side: 'buy',
                  ),
                  child: const Text('Buy'),
                ),
              ),
            ],
          ),
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
                    if (quote['description'] != null)
                      Text(
                        quote['description'] as String,
                        style: t.bodyMedium?.copyWith(height: 1.45),
                      ),
                  ],
                ),
              ),
            ),
          ],
        ],
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
  const _StatRow(this.label, this.value, {this.isLast = false});

  final String label;
  final String value;
  final bool isLast;

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
          Text(value, style: t.bodyMedium?.copyWith(fontWeight: FontWeight.w600)),
        ],
      ),
    );
  }
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
