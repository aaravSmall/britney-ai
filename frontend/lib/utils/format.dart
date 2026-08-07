/// Quantity for display: whole numbers as-is, fractional values to up to
/// 8 decimal places (satoshi-level precision) with trailing zeros trimmed.
/// A fixed 4-place format would silently round small crypto fills toward
/// zero — e.g. a 0.00030612 BTC fill would round to "0.0003" or, for a
/// smaller fill still, all the way to "0.0000" despite being a real,
/// paid-for position. Works the same for fractional stock shares.
String formatQuantity(double qty) {
  if (qty == qty.roundToDouble()) return qty.toStringAsFixed(0);
  var s = qty.toStringAsFixed(8);
  s = s.replaceFirst(RegExp(r'0+$'), '');
  if (s.endsWith('.')) s += '0';
  return s;
}
