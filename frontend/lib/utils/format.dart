import 'package:timezone/timezone.dart' as tz;

import '../services/timezone_controller.dart';

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

const List<String> _monthAbbr = [
  'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
  'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec',
];

/// "2:30 PM" (or "14:30" in 24-hour mode) — just the clock face, shared by
/// [formatTradeTime] and [formatEasternSuffix] so the two can't drift.
String _timeOfDay(DateTime d, bool use24Hour) {
  final minute = d.minute.toString().padLeft(2, '0');
  if (use24Hour) return '${d.hour}:$minute';
  final hour12 = d.hour % 12 == 0 ? 12 : d.hour % 12;
  final suffix = d.hour < 12 ? 'AM' : 'PM';
  return '$hour12:$minute $suffix';
}

/// "Aug 7 · 2:30 PM" (or "14:30" in 24-hour mode) — the app's shared
/// trade-timestamp format. [d] should already be converted to whatever
/// zone the caller wants displayed (see [toDisplayZone]) — this just reads
/// its own hour/day/month fields as-is.
String formatTradeTime(DateTime d, bool use24Hour) =>
    '${_monthAbbr[d.month - 1]} ${d.day} · ${_timeOfDay(d, use24Hour)}';

/// Converts a UTC-instant [utc] DateTime to the given display [zone] — null
/// means "device local" (plain [DateTime.toLocal], no tz database lookup
/// needed). [utc] must actually carry a UTC/offset instant (i.e. not the
/// result of an earlier `.toLocal()`) for the [zone] != null branch to be
/// meaningful.
DateTime toDisplayZone(DateTime utc, tz.Location? zone) =>
    zone == null ? utc.toLocal() : tz.TZDateTime.from(utc, zone);

/// "2:30 PM ET" — the NYSE's own clock for [utc], regardless of the user's
/// chosen display zone. Callers should skip this (it'd be a pure
/// duplicate) when the user has explicitly picked America/New_York as
/// their display zone — see call sites' `zoneId != 'America/New_York'`
/// checks.
String formatEasternSuffix(DateTime utc, bool use24Hour) =>
    '${_timeOfDay(tz.TZDateTime.from(utc, marketLocation), use24Hour)} ET';
