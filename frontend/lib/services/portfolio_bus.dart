import 'package:flutter/foundation.dart';

/// Signals screens holding cached portfolio data (dashboard) to refetch
/// after a trade fills elsewhere (e.g. the recommendations screen).
class PortfolioBus extends ChangeNotifier {
  void notifyTraded() => notifyListeners();
}
