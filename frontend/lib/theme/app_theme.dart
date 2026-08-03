import 'package:flutter/material.dart';
import 'package:google_fonts/google_fonts.dart';

/// Robinhood-inspired theme: calm greens, clear hierarchy, dark and light.
class AppTheme {
  // Dark palette
  static const Color bg = Color(0xFF0A0D10);
  static const Color bgElevated = Color(0xFF0E1217);
  static const Color surface = Color(0xFF12161C);
  static const Color surface2 = Color(0xFF1A1F27);
  static const Color borderSubtle = Color(0xFF2A3140);
  static const Color textPrimary = Color(0xFFF5F7FA);
  static const Color textSecondary = Color(0xFF8B95A5);

  // Light palette
  static const Color lightBg = Color(0xFFF6F7F9);
  static const Color lightBgElevated = Color(0xFFFFFFFF);
  static const Color lightSurface = Color(0xFFFFFFFF);
  static const Color lightSurface2 = Color(0xFFEFF1F4);
  static const Color lightBorderSubtle = Color(0xFFE1E4E9);
  static const Color lightTextPrimary = Color(0xFF11151A);
  static const Color lightTextSecondary = Color(0xFF5B6472);

  // Brand colors — consistent across both themes.
  static const Color accent = Color(0xFF00D926);
  static const Color accentMuted = Color(0xFF00C805);
  static const Color accentDim = Color(0xFF00A004);
  static const Color danger = Color(0xFFFF6B6B);

  static bool _isDark(BuildContext context) =>
      Theme.of(context).brightness == Brightness.dark;

  static Color textPrimaryOf(BuildContext context) =>
      _isDark(context) ? textPrimary : lightTextPrimary;
  static Color textSecondaryOf(BuildContext context) =>
      _isDark(context) ? textSecondary : lightTextSecondary;
  static Color surfaceOf(BuildContext context) =>
      _isDark(context) ? surface : lightSurface;
  static Color surface2Of(BuildContext context) =>
      _isDark(context) ? surface2 : lightSurface2;
  static Color borderSubtleOf(BuildContext context) =>
      _isDark(context) ? borderSubtle : lightBorderSubtle;

  static LinearGradient scaffoldGradientOf(BuildContext context) {
    final dark = _isDark(context);
    return LinearGradient(
      begin: Alignment.topCenter,
      end: Alignment.bottomCenter,
      colors: dark ? [bgElevated, bg] : [lightBgElevated, lightBg],
      stops: const [0.0, 0.45],
    );
  }

  static ThemeData dark() => _themeFor(
        brightness: Brightness.dark,
        scaffoldBg: bg,
        surfaceColor: surface,
        surface2Color: surface2,
        borderColor: borderSubtle,
        primaryText: textPrimary,
        secondaryText: textSecondary,
      );

  static ThemeData light() => _themeFor(
        brightness: Brightness.light,
        scaffoldBg: lightBg,
        surfaceColor: lightSurface,
        surface2Color: lightSurface2,
        borderColor: lightBorderSubtle,
        primaryText: lightTextPrimary,
        secondaryText: lightTextSecondary,
      );

  static ThemeData _themeFor({
    required Brightness brightness,
    required Color scaffoldBg,
    required Color surfaceColor,
    required Color surface2Color,
    required Color borderColor,
    required Color primaryText,
    required Color secondaryText,
  }) {
    final colorScheme = brightness == Brightness.dark
        ? ColorScheme.dark(
            primary: accent,
            onPrimary: Colors.black,
            secondary: accentMuted,
            surface: surfaceColor,
            onSurface: primaryText,
            surfaceContainerHighest: surface2Color,
            outline: borderColor,
            error: danger,
          )
        : ColorScheme.light(
            primary: accent,
            onPrimary: Colors.black,
            secondary: accentMuted,
            surface: surfaceColor,
            onSurface: primaryText,
            surfaceContainerHighest: surface2Color,
            outline: borderColor,
            error: danger,
          );

    final base = ThemeData(
      useMaterial3: true,
      brightness: brightness,
      scaffoldBackgroundColor: scaffoldBg,
      colorScheme: colorScheme,
    );
    return base.copyWith(
      textTheme: GoogleFonts.interTextTheme(base.textTheme).apply(
        bodyColor: primaryText,
        displayColor: primaryText,
      ),
      appBarTheme: AppBarTheme(
        backgroundColor: Colors.transparent,
        elevation: 0,
        scrolledUnderElevation: 0,
        centerTitle: false,
        titleTextStyle: GoogleFonts.inter(
          color: primaryText,
          fontSize: 20,
          fontWeight: FontWeight.w600,
          letterSpacing: -0.3,
        ),
        iconTheme: IconThemeData(color: primaryText, size: 22),
      ),
      navigationBarTheme: NavigationBarThemeData(
        backgroundColor: surfaceColor.withValues(alpha: 0.94),
        elevation: 0,
        height: 68,
        indicatorColor: accent.withValues(alpha: 0.18),
        labelTextStyle: WidgetStateProperty.resolveWith((states) {
          final selected = states.contains(WidgetState.selected);
          return TextStyle(
            fontSize: 12,
            fontWeight: selected ? FontWeight.w600 : FontWeight.w500,
            letterSpacing: 0.2,
            color: selected ? accent : secondaryText,
          );
        }),
        iconTheme: WidgetStateProperty.resolveWith((states) {
          final selected = states.contains(WidgetState.selected);
          return IconThemeData(
            color: selected ? accent : secondaryText,
            size: 24,
          );
        }),
      ),
      cardTheme: CardThemeData(
        color: surfaceColor,
        elevation: 0,
        margin: EdgeInsets.zero,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(16),
          side: BorderSide(color: borderColor, width: 1),
        ),
      ),
      dividerTheme: DividerThemeData(
        color: borderColor,
        thickness: 1,
      ),
      listTileTheme: ListTileThemeData(
        iconColor: secondaryText,
        titleTextStyle: GoogleFonts.inter(
          color: primaryText,
          fontSize: 16,
          fontWeight: FontWeight.w600,
        ),
        subtitleTextStyle: GoogleFonts.inter(
          color: secondaryText,
          fontSize: 13,
        ),
      ),
      snackBarTheme: SnackBarThemeData(
        backgroundColor: surface2Color,
        contentTextStyle: GoogleFonts.inter(color: primaryText, fontSize: 14),
        behavior: SnackBarBehavior.floating,
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
      ),
      inputDecorationTheme: InputDecorationTheme(
        filled: true,
        fillColor: surface2Color,
        hintStyle: TextStyle(color: secondaryText.withValues(alpha: 0.85)),
        contentPadding:
            const EdgeInsets.symmetric(horizontal: 16, vertical: 14),
        border: OutlineInputBorder(
          borderRadius: BorderRadius.circular(12),
          borderSide: BorderSide.none,
        ),
        focusedBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(12),
          borderSide: const BorderSide(color: accent, width: 1.5),
        ),
      ),
      segmentedButtonTheme: SegmentedButtonThemeData(
        style: ButtonStyle(
          visualDensity: VisualDensity.compact,
          padding: WidgetStateProperty.all(
            const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
          ),
        ),
      ),
      elevatedButtonTheme: ElevatedButtonThemeData(
        style: ElevatedButton.styleFrom(
          backgroundColor: accent,
          foregroundColor: Colors.black,
          elevation: 0,
          shadowColor: Colors.transparent,
          padding: const EdgeInsets.symmetric(vertical: 16, horizontal: 24),
          shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(12),
          ),
        ),
      ),
      filledButtonTheme: FilledButtonThemeData(
        style: FilledButton.styleFrom(
          backgroundColor: accent,
          foregroundColor: Colors.black,
          elevation: 0,
          padding: const EdgeInsets.symmetric(vertical: 16, horizontal: 24),
          shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(12),
          ),
        ),
      ),
      outlinedButtonTheme: OutlinedButtonThemeData(
        style: OutlinedButton.styleFrom(
          foregroundColor: primaryText,
          side: BorderSide(color: borderColor),
          padding: const EdgeInsets.symmetric(vertical: 16, horizontal: 24),
          shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(12),
          ),
        ),
      ),
    );
  }
}
