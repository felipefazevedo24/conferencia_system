/// Configuração fixa do app (deployment single-tenant, sem troca de ambiente
/// em runtime). Se precisar apontar para um ambiente de testes, mude aqui e
/// rebuilde o APK.
class AppConfig {
  static const String baseUrl = 'https://sync.columbiamachine.com.br';

  /// Intervalo mínimo entre pings (mesma regra do app web atual).
  static const Duration pingIntervaloMinimo = Duration(seconds: 15);

  /// Distância mínima (metros) para considerar um novo ponto relevante,
  /// mesmo dentro do intervalo mínimo (mesma regra do app web atual).
  static const double pingDistanciaMinimaM = 20;
}
