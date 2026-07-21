import 'package:flutter_test/flutter_test.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'package:motorista_app/src/app.dart';

void main() {
  testWidgets('App inicia sem travar e mostra um estado inicial', (tester) async {
    await tester.pumpWidget(const ProviderScope(child: MotoristaApp()));
    // Sem pareamento salvo (SharedPreferences vazio no ambiente de teste),
    // o app deve renderizar a tela de splash/configuração sem lançar erro.
    await tester.pump();
    expect(tester.takeException(), isNull);
  });
}
