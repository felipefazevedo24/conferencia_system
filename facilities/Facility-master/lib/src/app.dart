import 'package:flutter/material.dart';

import 'presentation/screens/home_screen.dart';
import 'presentation/screens/cleaning_schedule_screen.dart';
import 'presentation/screens/collaborators_screen.dart';
import 'presentation/screens/epi_requests_screen.dart';

class ObraTrackerApp extends StatelessWidget {
  const ObraTrackerApp({super.key});

  @override
  Widget build(BuildContext context) {
    final baseTheme = ThemeData.dark(useMaterial3: true);
    final colorScheme = const ColorScheme.dark(
      primary: Color(0xFF6C63FF),
      secondary: Color(0xFF00E5FF),
      surface: Color(0xFF12183B),
      onSurface: Colors.white,
      error: Color(0xFFFF5252),
    ).copyWith(
      primaryContainer: const Color(0xFF232B6A),
      onPrimaryContainer: Colors.white,
      secondaryContainer: const Color(0xFF103B4A),
      onSecondaryContainer: Colors.white,
      outline: const Color(0xFF47539B),
      outlineVariant: const Color(0xFF28306D),
      surfaceContainerHighest: const Color(0xFF161D4E),
    );

    final textTheme = baseTheme.textTheme
        .apply(displayColor: Colors.white, bodyColor: Colors.white)
        .copyWith(
          titleLarge: const TextStyle(fontWeight: FontWeight.w700, color: Colors.white),
          titleMedium: const TextStyle(fontWeight: FontWeight.w700, color: Colors.white),
          bodyMedium: const TextStyle(color: Color(0xFFB8C1FF)),
          bodySmall: const TextStyle(color: Color(0xFF7A82B3)),
        );

    return MaterialApp(
      title: 'Obra Tracker',
      debugShowCheckedModeBanner: false,
      theme: baseTheme.copyWith(
        colorScheme: colorScheme,
        textTheme: textTheme,
        scaffoldBackgroundColor: const Color(0xFF0B1026),
        canvasColor: const Color(0xFF0B1026),
        appBarTheme: const AppBarTheme(
          centerTitle: false,
          backgroundColor: Color(0xFF0B1026),
          foregroundColor: Colors.white,
          surfaceTintColor: Colors.transparent,
          elevation: 0,
        ),
        cardTheme: CardThemeData(
          color: const Color(0xFF161D4E),
          elevation: 2,
          shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(16)),
        ),
        inputDecorationTheme: InputDecorationTheme(
          filled: true,
          fillColor: const Color(0xFF161D4E),
          border: OutlineInputBorder(borderRadius: BorderRadius.circular(14)),
          enabledBorder: OutlineInputBorder(
            borderRadius: BorderRadius.circular(14),
            borderSide: const BorderSide(color: Color(0xFF28306D)),
          ),
        ),
        filledButtonTheme: FilledButtonThemeData(
          style: FilledButton.styleFrom(
            minimumSize: const Size(0, 48),
            shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(14)),
          ),
        ),
        floatingActionButtonTheme: const FloatingActionButtonThemeData(
          backgroundColor: Color(0xFF6C63FF),
          foregroundColor: Colors.white,
        ),
      ),
      home: const _MainShell(),
    );
  }
}

class _MainShell extends StatefulWidget {
  const _MainShell();

  @override
  State<_MainShell> createState() => _MainShellState();
}

class _MainShellState extends State<_MainShell> {
  int _currentIndex = 0;

  static const _screens = <Widget>[
    HomeScreen(),
    CleaningScheduleScreen(),
    CollaboratorsScreen(),
    EpiRequestsScreen(),
  ];

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: _screens[_currentIndex],
      bottomNavigationBar: NavigationBar(
        selectedIndex: _currentIndex,
        onDestinationSelected: (index) => setState(() => _currentIndex = index),
        destinations: const [
          NavigationDestination(
            icon: Icon(Icons.construction_outlined),
            selectedIcon: Icon(Icons.construction),
            label: 'Obras',
          ),
          NavigationDestination(
            icon: Icon(Icons.event_note_outlined),
            selectedIcon: Icon(Icons.event_note),
            label: 'Cronograma',
          ),
          NavigationDestination(
            icon: Icon(Icons.people_outlined),
            selectedIcon: Icon(Icons.people),
            label: 'Colaboradores',
          ),
          NavigationDestination(
            icon: Icon(Icons.health_and_safety_outlined),
            selectedIcon: Icon(Icons.health_and_safety),
            label: 'MMD EPI',
          ),
        ],
      ),
    );
  }
}
