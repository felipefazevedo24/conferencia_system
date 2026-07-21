import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'presentation/providers/providers.dart';
import 'presentation/screens/setup_screen.dart';
import 'presentation/screens/viagens_screen.dart';

class MotoristaApp extends ConsumerWidget {
  const MotoristaApp({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    return MaterialApp(
      title: 'Motorista Sync',
      debugShowCheckedModeBanner: false,
      theme: ThemeData(
        useMaterial3: true,
        colorSchemeSeed: const Color(0xFF0F62C9),
        scaffoldBackgroundColor: const Color(0xFFF3F7FB),
      ),
      home: ref.watch(configProvider).when(
            data: (config) => config == null ? const SetupScreen() : const ViagensScreen(),
            loading: () => const _Splash(),
            error: (e, _) => const SetupScreen(),
          ),
    );
  }
}

class _Splash extends StatelessWidget {
  const _Splash();

  @override
  Widget build(BuildContext context) {
    return const Scaffold(
      body: Center(child: CircularProgressIndicator()),
    );
  }
}
