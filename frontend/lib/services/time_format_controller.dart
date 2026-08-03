import 'package:flutter/foundation.dart';
import 'package:shared_preferences/shared_preferences.dart';

/// Whether times are shown in 24-hour ("15:34") or 12-hour ("3:34 PM") form,
/// persisted across launches.
class TimeFormatController extends ChangeNotifier {
  static const _prefsKey = 'use_24_hour_time';
  static final _prefs = SharedPreferencesAsync();

  bool _use24Hour = false;
  bool get use24Hour => _use24Hour;

  // See ThemeController for why this guard exists — an in-flight _load()
  // must not clobber a choice the user already made this session.
  bool _userSet = false;

  TimeFormatController() {
    _load();
  }

  Future<void> _load() async {
    final saved = await _prefs.getBool(_prefsKey);
    if (_userSet) return;
    if (saved != null && saved != _use24Hour) {
      _use24Hour = saved;
      notifyListeners();
    }
  }

  Future<void> setUse24Hour(bool value) async {
    _userSet = true;
    if (_use24Hour == value) return;
    _use24Hour = value;
    notifyListeners();
    try {
      await _prefs.setBool(_prefsKey, value);
    } catch (_) {
      // Best-effort persistence — e.g. storage quota/private-mode restrictions.
    }
  }
}
