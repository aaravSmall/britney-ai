import 'package:flutter/material.dart';

/// Visual style for the portfolio performance chart.
enum ChartKind { line, candlestick, waterfall }

extension ChartKindMeta on ChartKind {
  String get label {
    switch (this) {
      case ChartKind.line:
        return 'Line';
      case ChartKind.candlestick:
        return 'Candles';
      case ChartKind.waterfall:
        return 'Waterfall';
    }
  }

  IconData get icon {
    switch (this) {
      case ChartKind.line:
        return Icons.show_chart_rounded;
      case ChartKind.candlestick:
        return Icons.candlestick_chart_rounded;
      case ChartKind.waterfall:
        return Icons.waterfall_chart_rounded;
    }
  }
}
