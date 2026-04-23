import 'dart:ui';

import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:intl/date_symbol_data_local.dart';

import 'src/app.dart';
import 'src/data/local/app_database.dart';

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();

  // Catch all uncaught async errors so they show up in DevTools console.
  PlatformDispatcher.instance.onError = (error, stack) {
    debugPrint('UNCAUGHT ERROR: $error\n$stack');
    return true;
  };

  FlutterError.onError = (details) {
    debugPrint('FLUTTER ERROR: ${details.exceptionAsString()}\n${details.stack}');
  };

  String? initError;

  try {
    await initializeDateFormatting('pt_BR');
  } catch (e) {
    initError = 'Date init: $e';
  }

  try {
    await AppDatabase.initialize();
    await AppDatabase.instance.database;
  } catch (e) {
    initError = 'DB init: $e';
  }

  if (initError != null) {
    runApp(MaterialApp(
      home: Scaffold(
        body: Center(child: Text('ERRO INICIALIZAÇÃO: $initError',
          style: const TextStyle(color: Colors.red, fontSize: 18))),
      ),
    ));
    return;
  }

  runApp(const ProviderScope(child: ObraTrackerApp()));
}
