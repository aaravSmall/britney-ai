import 'package:fl_chart/fl_chart.dart';
import 'package:flutter/material.dart';

import '../data/chart_kind.dart';
import '../theme/app_theme.dart';

/// Shared by the dashboard's portfolio-value chart and the stock detail
/// page's price chart — genuinely generic (a plain [values]/[times]
/// series, not PortfolioSnapshot-shaped) despite having started out
/// private to dashboard_screen.dart. See that file's git history for the
/// original single-file version this was extracted from.
///
/// One real limitation worth calling out explicitly rather than hiding:
/// the candlestick view only ever had one value per point to work with
/// (a portfolio snapshot is a single total, not real OHLC), so it
/// synthesizes a small fixed wick around whichever of open/close is
/// higher — see [_Candle]. A caller with genuine per-candle highs/lows
/// (e.g. real stock OHLCV) will have that data discarded down to just
/// closing prices if it feeds this widget, same as the dashboard's own
/// (already-synthetic) usage. Extending the candlestick painter to plot
/// real highs/lows would be a reasonable follow-up, but is a bigger,
/// separate change from "reuse this widget" — not done here.

/// Market hours used for "Today" x-axis tick placement.
const int _marketOpenHour = 9;
const int _marketCloseHour = 17;

/// Below this many points in the selected range, a line/candlestick/
/// waterfall chart would be more misleading than informative (e.g. a
/// single segment implying a trend from 2 dots) — show a placeholder
/// instead. Public: a caller's own header (e.g. whether to show a
/// period-change chip) may need the same threshold PriceChartView uses
/// internally to decide chart-vs-placeholder.
const int minPointsForChart = 3;

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

/// Fuller label for a scrub-driven "as of" subtitle.
String _detailLabel(DateTime d, {required bool isIntraday, required bool use24Hour}) =>
    isIntraday ? _formatTime(d, use24Hour) : _fullDate(d);

/// Fraction (0..1) of the way [t] sits between [start] and [end]. All
/// three chart kinds and the scrubber share this so a point's pixel
/// position is proportional to real elapsed time rather than its index
/// in the list — otherwise a flat multi-hour pre/post-market segment
/// (few points) would render as a barely-visible sliver next to a busy
/// market-hours segment (many points) despite covering most of the day.
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

/// Chart body: line/candlestick/waterfall rendering, drag-to-scrub
/// tooltip, and x-axis labels — everything below a caller's own
/// header/value display. Self-contained scrub state; [onScrubIndexChanged]
/// is an optional escape hatch for a caller whose own header needs to
/// react to the scrubbed point too (the dashboard's "Total value" card
/// does this; a caller that doesn't need it just omits the callback).
class PriceChartView extends StatefulWidget {
  const PriceChartView({
    super.key,
    required this.values,
    required this.times,
    required this.chartKind,
    required this.isIntraday,
    required this.use24Hour,
    required this.hasAnyHistory,
    required this.noHistoryMessage,
    required this.noDataInRangeMessage,
    required this.notEnoughPointsMessage,
    this.onScrubIndexChanged,
  });

  final List<double> values;
  final List<DateTime> times;
  final ChartKind chartKind;
  final bool isIntraday;
  final bool use24Hour;
  // Whether there's any real history at all, regardless of the selected
  // range — distinguishes "nothing captured yet" from "nothing in this
  // particular range" for the placeholder copy below.
  final bool hasAnyHistory;

  // Caller-provided copy for the three empty/sparse states — kept as
  // plain strings (not hardcoded here) since "no history yet" reads very
  // differently for a portfolio ("...once the agent starts trading")
  // than for a stock ("...for this ticker"), and this widget has no
  // business knowing which context it's in.
  final String noHistoryMessage;
  final String noDataInRangeMessage;
  final String notEnoughPointsMessage;

  final ValueChanged<int?>? onScrubIndexChanged;

  @override
  State<PriceChartView> createState() => _PriceChartViewState();
}

class _PriceChartViewState extends State<PriceChartView> {
  int? _scrubIndex;

  bool get _hasChart => widget.values.length >= minPointsForChart;

  void _setScrubIndex(int? i) {
    if (i == _scrubIndex) return;
    setState(() => _scrubIndex = i);
    widget.onScrubIndexChanged?.call(i);
  }

  void _updateScrub(double localX, double width) {
    if (!_hasChart || width <= 0) return;
    final frac = (localX / width).clamp(0.0, 1.0);
    _setScrubIndex(_nearestIndexForFrac(frac, widget.times));
  }

  void _endScrub() {
    if (_scrubIndex != null) _setScrubIndex(null);
  }

  @override
  void didUpdateWidget(covariant PriceChartView oldWidget) {
    super.didUpdateWidget(oldWidget);
    // A range/chart-kind change swaps the underlying data — a stale
    // scrub index into the old series would show the wrong value.
    if (oldWidget.values.length != widget.values.length && _scrubIndex != null) {
      _scrubIndex = null;
      widget.onScrubIndexChanged?.call(null);
    }
  }

  @override
  Widget build(BuildContext context) {
    if (!_hasChart) {
      return _SparseHistoryPlaceholder(
        message: !widget.hasAnyHistory
            ? widget.noHistoryMessage
            : widget.values.isEmpty
                ? widget.noDataInRangeMessage
                : widget.notEnoughPointsMessage,
      );
    }

    final scrubbing = _scrubIndex != null;

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
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
              // nicely with an enclosing vertical ListView's scroll
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
      ],
    );
  }
}

/// A fuller "as of `<date>`" label for a scrub-driven header, e.g. the
/// dashboard's "Total value" card subtitle while dragging. Exposed so a
/// caller using [PriceChartView.onScrubIndexChanged] can build the same
/// kind of subtitle without duplicating the intraday/date formatting
/// logic above.
String scrubDetailLabel(DateTime d, {required bool isIntraday, required bool use24Hour}) =>
    _detailLabel(d, isIntraday: isIntraday, use24Hour: use24Hour);

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
    // (which would mostly land in the flat pre/post-market segments) —
    // and since the market session is proportionally narrow (8 of 24h),
    // more than 3 labels start to visually overlap in that space.
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

    // Positioned (not a plain Row) so each label lines up with where
    // that moment actually falls on the chart above — the chart itself
    // is spaced by real elapsed time, not by index, so evenly-spaced
    // labels would drift out of alignment with it.
    return SizedBox(
      height: 14,
      width: double.infinity,
      child: LayoutBuilder(
        builder: (context, constraints) {
          final width = constraints.maxWidth;
          // Explicit tight size: a Stack with only Positioned children
          // and loose constraints (which CrossAxisAlignment.start gives
          // its Column children) collapses toward zero width, and its
          // default Clip.hardEdge then clips every label near the origin.
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

/// Line/candlestick/waterfall toggle — a small icon-button group, fully
/// self-contained and content-agnostic (just reports which [ChartKind]
/// was tapped).
class ChartKindToggle extends StatelessWidget {
  const ChartKindToggle({super.key, required this.value, required this.onChanged});

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
/// elapsed time (not index) so e.g. a flat multi-hour closed-market
/// segment takes up the width it actually spans.
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

/// Day-over-day candles synthesized from the value series (open =
/// previous value, close = current value — see this file's top-level
/// doc comment for why there's no real per-candle high/low here). A
/// small fixed wick is drawn around the body for a legible candlestick
/// silhouette. Positioned by real elapsed time, same as [_LineChartView].
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

/// Floating bars connecting each point's value to the next — green when
/// it rose, red when it fell. Positioned by real elapsed time, same as
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
