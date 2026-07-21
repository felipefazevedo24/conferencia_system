import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'src/app.dart';
import 'src/core/services/location_tracking_service.dart';

void main() {
  WidgetsFlutterBinding.ensureInitialized();
  LocationTrackingService.initialize();
  runApp(const ProviderScope(child: MotoristaApp()));
}
