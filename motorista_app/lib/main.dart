import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:intl/date_symbol_data_local.dart';

import 'src/app.dart';
import 'src/core/services/location_tracking_service.dart';

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  // Datas por extenso em português ("terça, 10 de novembro").
  await initializeDateFormatting('pt_BR');
  LocationTrackingService.initialize();
  runApp(const ProviderScope(child: MotoristaApp()));
}
