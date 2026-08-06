/// Time horizon for the portfolio performance chart.
enum ChartRange {
  today,
  week,
  month30,
  ytd,
  fiveYear,
}

extension ChartRangeLabel on ChartRange {
  String get shortLabel {
    switch (this) {
      case ChartRange.today:
        return '1D';
      case ChartRange.week:
        return '1W';
      case ChartRange.month30:
        return '30D';
      case ChartRange.ytd:
        return 'YTD';
      case ChartRange.fiveYear:
        return '5Y';
    }
  }

  String get description {
    switch (this) {
      case ChartRange.today:
        return 'Today';
      case ChartRange.week:
        return 'Last week';
      case ChartRange.month30:
        return 'Last 30 days';
      case ChartRange.ytd:
        return 'Year to date';
      case ChartRange.fiveYear:
        return 'Last 5 years';
    }
  }
}
