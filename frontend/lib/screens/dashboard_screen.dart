import 'dart:convert';

import 'package:fl_chart/fl_chart.dart';
import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../data/chart_kind.dart';
import '../data/chart_range.dart';
import '../services/api_service.dart';
import '../services/auth_controller.dart';
import '../theme/app_theme.dart';

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
  bool _started = false;

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    if (_started) return;
    _started = true;
    _load();
  }

  Future<void> _load() async {
    final api = context.read<ApiService>();
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final results = await Future.wait([
        api.get('/dashboard'),
        api.get('/users/me'),
      ]);
      if (!mounted) return;
      final dashRes = results[0];
      final meRes = results[1];
      if (dashRes.statusCode == 200) {
        setState(() => _dashboard = jsonDecode(dashRes.body) as Map<String, dynamic>);
      } else {
        setState(() => _error = 'Failed to load dashboard: ${dashRes.body}');
      }
      if (meRes.statusCode == 200) {
        final u = jsonDecode(meRes.body) as Map<String, dynamic>;
        setState(() => _auto = u['auto_invest_enabled'] == true);
      }
    } catch (e) {
      if (mounted) setState(() => _error = 'Network error: $e');
    } finally {
      if (mounted) setState(() => _loading = false);
    }
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

  /// Backend currently returns a fixed ~30-day daily series; slice its tail
  /// to approximate shorter ranges (longer ranges fall back to full history).
  List<Map<String, dynamic>> _sliceForRange(
    List<Map<String, dynamic>> performance,
    ChartRange range,
  ) {
    final n = performance.length;
    int take;
    switch (range) {
      case ChartRange.today:
        take = 2;
        break;
      case ChartRange.week:
        take = 8;
        break;
      case ChartRange.month30:
      case ChartRange.ytd:
      case ChartRange.fiveYear:
        take = n;
        break;
    }
    take = take.clamp(1, n);
    return performance.sublist(n - take);
  }

  @override
  Widget build(BuildContext context) {
    final auth = context.watch<AuthController>();
    final t = Theme.of(context).textTheme;

    if (_loading && _dashboard == null) {
      return const Scaffold(
        backgroundColor: Colors.transparent,
        body: Center(child: CircularProgressIndicator(color: AppTheme.accent)),
      );
    }

    if (_error != null && _dashboard == null) {
      return Scaffold(
        backgroundColor: Colors.transparent,
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
                FilledButton(onPressed: _load, child: const Text('Try again')),
              ],
            ),
          ),
        ),
      );
    }

    final dashboard = _dashboard!;
    final performance = (dashboard['performance'] as List<dynamic>)
        .map((e) => e as Map<String, dynamic>)
        .toList();
    final sliced = _sliceForRange(performance, _range);
    final values = [
      for (final p in sliced) (p['value'] as num).toDouble(),
    ];
    final firstValue = values.first;
    final lastValue = values.last;
    final periodChangePct =
        firstValue != 0 ? (lastValue - firstValue) / firstValue * 100 : 0.0;
    final total = (dashboard['total_portfolio_value'] as num).toDouble();
    final cash = (dashboard['cash_balance'] as num).toDouble();
    final holdings = (dashboard['holdings'] as List<dynamic>)
        .map((e) => e as Map<String, dynamic>)
        .toList();

    return Scaffold(
      backgroundColor: Colors.transparent,
      appBar: AppBar(
        title: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              'Portfolio',
              style: t.titleLarge?.copyWith(fontWeight: FontWeight.w600),
            ),
            Text(
              _greeting(),
              style: t.bodySmall?.copyWith(color: AppTheme.textSecondaryOf(context)),
            ),
          ],
        ),
      ),
      body: RefreshIndicator(
        color: AppTheme.accent,
        onRefresh: _load,
        child: ListView(
          physics: const AlwaysScrollableScrollPhysics(),
          padding: const EdgeInsets.fromLTRB(20, 8, 20, 100),
          children: [
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
            ),
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
          ],
        ),
      ),
    );
  }
}

class _SummaryCard extends StatelessWidget {
  const _SummaryCard({
    required this.range,
    required this.chartKind,
    required this.totalValue,
    required this.cash,
    required this.periodChangePct,
    required this.values,
  });

  final ChartRange range;
  final ChartKind chartKind;
  final double totalValue;
  final double cash;
  final double periodChangePct;
  final List<double> values;

  @override
  Widget build(BuildContext context) {
    final t = Theme.of(context).textTheme;
    final positive = periodChangePct >= 0;

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
              '\$${totalValue.toStringAsFixed(2)}',
              style: t.headlineMedium?.copyWith(
                fontWeight: FontWeight.w700,
                letterSpacing: -0.5,
              ),
            ),
            const SizedBox(height: 6),
            Text(
              range.description,
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
                  'Cash \$${cash.toStringAsFixed(2)}',
                  style: t.bodySmall?.copyWith(color: AppTheme.textSecondaryOf(context)),
                ),
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
                    '${positive ? '+' : ''}${periodChangePct.toStringAsFixed(2)}% '
                    '${range == ChartRange.today ? 'today' : 'in period'}',
                    style: TextStyle(
                      color: positive ? AppTheme.accent : AppTheme.danger,
                      fontWeight: FontWeight.w600,
                      fontSize: 13,
                    ),
                  ),
                ),
              ],
            ),
            const SizedBox(height: 20),
            SizedBox(
              height: 180,
              child: switch (chartKind) {
                ChartKind.line => _LineChartView(values: values),
                ChartKind.candlestick =>
                  _CandlestickChartView(values: values),
                ChartKind.waterfall => _WaterfallChartView(values: values),
              },
            ),
          ],
        ),
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

/// Sharp-angled line chart of the raw value series.
class _LineChartView extends StatelessWidget {
  const _LineChartView({required this.values});

  final List<double> values;

  @override
  Widget build(BuildContext context) {
    final spots = [
      for (var i = 0; i < values.length; i++) FlSpot(i.toDouble(), values[i]),
    ];
    final minY = values.reduce((a, b) => a < b ? a : b);
    final maxY = values.reduce((a, b) => a > b ? a : b);
    final pad = (maxY - minY) * 0.08 + 1.0;

    return LineChart(
      LineChartData(
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
/// around the body for a legible candlestick silhouette).
class _CandlestickChartView extends StatelessWidget {
  const _CandlestickChartView({required this.values});

  final List<double> values;

  @override
  Widget build(BuildContext context) {
    final candles = <_Candle>[
      for (var i = 1; i < values.length; i++)
        _Candle(open: values[i - 1], close: values[i]),
    ];
    if (candles.isEmpty) {
      candles.add(_Candle(open: values.first, close: values.first));
    }
    final minY = candles.map((c) => c.low).reduce((a, b) => a < b ? a : b);
    final maxY = candles.map((c) => c.high).reduce((a, b) => a > b ? a : b);
    final pad = (maxY - minY) * 0.08 + 1.0;

    return CustomPaint(
      size: Size.infinite,
      painter: _CandlestickPainter(
        candles: candles,
        minY: minY - pad,
        maxY: maxY + pad,
        upColor: AppTheme.accent,
        downColor: AppTheme.danger,
      ),
    );
  }
}

class _Candle {
  _Candle({required this.open, required this.close})
      : high = (open > close ? open : close) * 1.0025,
        low = (open < close ? open : close) * 0.9975;

  final double open;
  final double close;
  final double high;
  final double low;

  bool get isUp => close >= open;
}

class _CandlestickPainter extends CustomPainter {
  _CandlestickPainter({
    required this.candles,
    required this.minY,
    required this.maxY,
    required this.upColor,
    required this.downColor,
  });

  final List<_Candle> candles;
  final double minY;
  final double maxY;
  final Color upColor;
  final Color downColor;

  @override
  void paint(Canvas canvas, Size size) {
    if (candles.isEmpty) return;
    final range = (maxY - minY).abs() < 1e-9 ? 1.0 : maxY - minY;
    double yFor(double v) => size.height - ((v - minY) / range) * size.height;

    final n = candles.length;
    final slot = size.width / n;
    final bodyWidth = (slot * 0.55).clamp(2.0, 16.0);

    for (var i = 0; i < n; i++) {
      final c = candles[i];
      final cx = slot * i + slot / 2;
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
/// rose, red when it fell.
class _WaterfallChartView extends StatelessWidget {
  const _WaterfallChartView({required this.values});

  final List<double> values;

  @override
  Widget build(BuildContext context) {
    final n = values.length;
    final groups = <BarChartGroupData>[
      for (var i = 1; i < n; i++)
        BarChartGroupData(
          x: i - 1,
          barRods: [
            BarChartRodData(
              fromY: values[i - 1] < values[i] ? values[i - 1] : values[i],
              toY: values[i - 1] < values[i] ? values[i] : values[i - 1],
              color: values[i] >= values[i - 1]
                  ? AppTheme.accent
                  : AppTheme.danger,
              width: (280 / n).clamp(3.0, 22.0),
              borderRadius: BorderRadius.circular(2),
            ),
          ],
        ),
    ];
    final minY = values.reduce((a, b) => a < b ? a : b);
    final maxY = values.reduce((a, b) => a > b ? a : b);
    final pad = (maxY - minY) * 0.08 + 1.0;

    return BarChart(
      BarChartData(
        minY: minY - pad,
        maxY: maxY + pad,
        gridData: const FlGridData(show: false),
        titlesData: const FlTitlesData(show: false),
        borderData: FlBorderData(show: false),
        barTouchData: BarTouchData(enabled: false),
        barGroups: groups.isEmpty
            ? [
                BarChartGroupData(
                  x: 0,
                  barRods: [
                    BarChartRodData(
                      fromY: minY,
                      toY: maxY,
                      color: AppTheme.accent,
                    ),
                  ],
                ),
              ]
            : groups,
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
          '${h['asset_type']} · ${qty == qty.roundToDouble() ? qty.toStringAsFixed(0) : qty.toStringAsFixed(4)} shares',
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
