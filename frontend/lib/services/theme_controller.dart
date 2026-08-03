import 'package:flutter/material.dart';
import 'package:shared_preferences/shared_preferences.dart';

/// User's light/dark/system theme preference, persisted across launches.
///
/// Uses [SharedPreferencesAsync] (not the deprecated [SharedPreferences]
/// singleton) — the legacy `getInstance()` API never resolved its returned
/// future under Flutter web release builds in testing here, silently
/// breaking persistence.
class ThemeController extends ChangeNotifier {
  static const _prefsKey = 'theme_mode';
  static final _prefs = SharedPreferencesAsync();

  ThemeMode _mode = ThemeMode.dark;
  ThemeMode get mode => _mode;

  ThemeController() {
    _load();
  }

  Future<void> _load() async {
    final saved = await _prefs.getString(_prefsKey);
    final match = ThemeMode.values.where((m) => m.name == saved);
    if (match.isNotEmpty && match.first != _mode) {
      _mode = match.first;
      notifyListeners();
    }
  }

  Future<void> setMode(ThemeMode mode) async {
    if (_mode == mode) return;
    _mode = mode;
    notifyListeners();
    try {
      await _prefs.setString(_prefsKey, mode.name);
    } catch (_) {
      // Best-effort persistence — e.g. storage quota/private-mode restrictions.
    }
  }
}
