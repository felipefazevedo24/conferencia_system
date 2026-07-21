import 'package:flutter/material.dart';

/// Paleta e estilo espelhando o site Columbia Sync
/// (static/css/ui_erp.css :root — mesmos tokens de cor/raio/sombra),
/// pra o app não parecer um produto à parte.
class AppColors {
  static const brand950 = Color(0xFF081523);
  static const brand900 = Color(0xFF0F2235);
  static const brand800 = Color(0xFF16324D);
  static const brand700 = Color(0xFF23486D);
  static const primary600 = Color(0xFF0F62C9);
  static const primary500 = Color(0xFF1C86F2);
  static const primary400 = Color(0xFF7DC4FF);
  static const accent500 = Color(0xFF11A36C);
  static const accent400 = Color(0xFF44C18F);
  static const warning500 = Color(0xFFF4B340);
  static const danger500 = Color(0xFFE05A54);
  static const text = Color(0xFF16324D);
  static const textMuted = Color(0xFF60758D);
  static const textSoft = Color(0xFF7F90A3);
  static const surface = Color(0xFFFFFFFF);
  static const surfaceAlt = Color(0xFFF6FBFF);
  static const surfaceMuted = Color(0xFFEDF3F8);
  static const border = Color(0xFFD7E2EC);
  static const borderStrong = Color(0xFFC3D1DE);
}

class AppRadius {
  static const xs = 8.0;
  static const sm = 12.0;
  static const md = 16.0;
  static const lg = 22.0;
  static const xl = 28.0;
}

class AppTheme {
  static ThemeData light() {
    final scheme = ColorScheme.fromSeed(
      seedColor: AppColors.primary600,
      brightness: Brightness.light,
      primary: AppColors.primary600,
      secondary: AppColors.accent500,
      error: AppColors.danger500,
      surface: AppColors.surface,
    );
    return ThemeData(
      useMaterial3: true,
      colorScheme: scheme,
      scaffoldBackgroundColor: AppColors.surfaceAlt,
      fontFamily: 'Roboto',
      appBarTheme: const AppBarTheme(
        backgroundColor: AppColors.brand900,
        foregroundColor: Colors.white,
        elevation: 0,
        centerTitle: false,
        titleTextStyle: TextStyle(color: Colors.white, fontSize: 18, fontWeight: FontWeight.w800),
      ),
      cardTheme: CardThemeData(
        color: AppColors.surface,
        elevation: 0,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(AppRadius.md),
          side: const BorderSide(color: AppColors.border),
        ),
        margin: EdgeInsets.zero,
      ),
      dividerTheme: const DividerThemeData(color: AppColors.border, thickness: 1),
      textTheme: const TextTheme(
        headlineSmall: TextStyle(color: AppColors.text, fontWeight: FontWeight.w800),
        titleMedium: TextStyle(color: AppColors.text, fontWeight: FontWeight.w800),
        titleSmall: TextStyle(color: AppColors.text, fontWeight: FontWeight.w700),
        bodyMedium: TextStyle(color: AppColors.text),
        bodySmall: TextStyle(color: AppColors.textMuted),
        labelSmall: TextStyle(color: AppColors.textSoft, fontWeight: FontWeight.w700),
      ),
      filledButtonTheme: FilledButtonThemeData(
        style: FilledButton.styleFrom(
          backgroundColor: AppColors.primary600,
          foregroundColor: Colors.white,
          minimumSize: const Size.fromHeight(52),
          shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(AppRadius.sm)),
          textStyle: const TextStyle(fontWeight: FontWeight.w800, fontSize: 15),
        ),
      ),
      outlinedButtonTheme: OutlinedButtonThemeData(
        style: OutlinedButton.styleFrom(
          foregroundColor: AppColors.primary600,
          side: const BorderSide(color: AppColors.borderStrong),
          minimumSize: const Size.fromHeight(48),
          shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(AppRadius.sm)),
          textStyle: const TextStyle(fontWeight: FontWeight.w700),
        ),
      ),
      inputDecorationTheme: InputDecorationTheme(
        filled: true,
        fillColor: AppColors.surfaceMuted,
        border: OutlineInputBorder(
          borderRadius: BorderRadius.circular(AppRadius.sm),
          borderSide: BorderSide.none,
        ),
        contentPadding: const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
      ),
      chipTheme: ChipThemeData(
        backgroundColor: AppColors.surfaceMuted,
        labelStyle: const TextStyle(fontSize: 11, fontWeight: FontWeight.w800),
        padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(999)),
        side: BorderSide.none,
      ),
      listTileTheme: const ListTileThemeData(
        iconColor: AppColors.primary600,
      ),
    );
  }

  /// Cor de destaque por status de viagem/parada — mesma linguagem visual
  /// usada no restante do site (verde = ok/ativo, âmbar = pendente, etc.).
  static Color corStatusViagem(String status) {
    switch (status) {
      case 'EmAndamento':
        return AppColors.accent500;
      case 'Concluida':
        return AppColors.textMuted;
      case 'Cancelada':
        return AppColors.danger500;
      default: // Planejada
        return AppColors.warning500;
    }
  }
}
