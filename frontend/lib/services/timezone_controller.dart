import 'package:flutter/foundation.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:timezone/timezone.dart' as tz;

/// Sentinel meaning "render in the device's own local time" rather than a
/// specific named zone — the default, and the only choice that needs no
/// [tz] database lookup at all (plain [DateTime.toLocal]). Mirrors the
/// backend's User.timezone default (app/models/user.py).
const String kDeviceTimezone = 'device';

/// The IANA zone britney.ai actually trades on (NYSE) — always available
/// for an "ET" annotation next to a trade timestamp regardless of the
/// user's chosen display zone. Matches agent/market_hours.py's MARKET_TZ.
final tz.Location marketLocation = tz.getLocation('America/New_York');

/// User's chosen display timezone: either [kDeviceTimezone] or an IANA
/// name resolvable via package:timezone. Persisted locally (instant,
/// works offline) and mirrored to the account via `PATCH /settings/timezone`
/// so it's available server-side too and follows the user across devices —
/// see account_screen.dart's _TimezoneSection, which does the actual PATCH
/// call and then reports the result back here via [setZone], same split
/// dashboard_screen.dart's `_toggleAuto` uses for auto-invest.
class TimezoneController extends ChangeNotifier {
  static const _prefsKey = 'display_timezone';
  static final _prefs = SharedPreferencesAsync();

  String _zoneId = kDeviceTimezone;
  String get zoneId => _zoneId;

  /// Null when [zoneId] is [kDeviceTimezone] — callers should use
  /// DateTime.toLocal() in that case rather than a [tz.Location].
  tz.Location? get location =>
      _zoneId == kDeviceTimezone ? null : tz.getLocation(_zoneId);

  // See ThemeController for why this guard exists — an in-flight _load()
  // or applyServerValue() must not clobber a choice the user already made
  // this session.
  bool _userSet = false;

  TimezoneController() {
    _load();
  }

  Future<void> _load() async {
    final saved = await _prefs.getString(_prefsKey);
    if (_userSet) return;
    if (saved != null && saved != _zoneId) {
      _zoneId = saved;
      notifyListeners();
    }
  }

  /// Seeds from the account's server-side value (GET /users/me) the first
  /// time it's fetched after login, so a zone chosen on another device
  /// shows up here too.
  void applyServerValue(String value) {
    if (_userSet || value == _zoneId) return;
    _zoneId = value;
    notifyListeners();
  }

  Future<void> setZone(String zoneId) async {
    _userSet = true;
    if (_zoneId == zoneId) return;
    _zoneId = zoneId;
    notifyListeners();
    try {
      await _prefs.setString(_prefsKey, zoneId);
    } catch (_) {
      // Best-effort persistence — e.g. storage quota/private-mode restrictions.
    }
  }
}
