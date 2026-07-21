import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_riverpod/legacy.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../../data/local/app_database.dart';
import '../../data/remote/api_client.dart';
import '../../data/repositories/repositories.dart';
import '../../domain/entities/models.dart';

const _prefMotoristaId = 'motorista_id';
const _prefToken = 'motorista_token';
const _prefNome = 'motorista_nome';

/// Lê/grava o pareamento (motoristaId + token do painel) no shared_preferences.
/// Fica aqui (e não em domain/) por ser puramente infraestrutura de app,
/// mesmo estilo enxuto do obra_tracker (sem camada de usecases separada).
class ConfigStore {
  static Future<MotoristaConfig?> ler() async {
    final prefs = await SharedPreferences.getInstance();
    final id = prefs.getInt(_prefMotoristaId);
    final token = prefs.getString(_prefToken);
    if (id == null || token == null) return null;
    return MotoristaConfig(
      motoristaId: id,
      token: token,
      motoristaNome: prefs.getString(_prefNome),
    );
  }

  static Future<void> salvar({required int motoristaId, required String token, String? nome}) async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setInt(_prefMotoristaId, motoristaId);
    await prefs.setString(_prefToken, token);
    if (nome != null) await prefs.setString(_prefNome, nome);
  }

  static Future<void> limpar() async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.remove(_prefMotoristaId);
    await prefs.remove(_prefToken);
    await prefs.remove(_prefNome);
  }
}

final appDatabaseProvider = Provider<AppDatabase>((ref) => AppDatabase.instance);

final apiClientProvider = Provider<ApiClient>((ref) => ApiClient());

final motoristaRepositoryProvider = Provider<MotoristaRepository>((ref) {
  return MotoristaRepository(ref.watch(apiClientProvider));
});

final pingQueueRepositoryProvider = Provider<PingQueueRepository>((ref) {
  return PingQueueRepository(ref.watch(appDatabaseProvider));
});

/// Pareamento salvo localmente. `null` = precisa passar pela tela de setup.
final configProvider = FutureProvider<MotoristaConfig?>((ref) => ConfigStore.ler());

/// Viagens ativas + histórico do motorista logado.
final viagensProvider = FutureProvider<ViagensPainel>((ref) async {
  final config = await ref.watch(configProvider.future);
  if (config == null) return (ativas: <Viagem>[], historico: <Viagem>[]);
  final repo = ref.watch(motoristaRepositoryProvider);
  return repo.painelViagens(mid: config.motoristaId, token: config.token);
});

/// Paradas de uma viagem específica (vid + token da viagem, extraídos de
/// Viagem.viagemToken).
final paradasProvider = FutureProvider.family<List<Parada>, ({int vid, String token})>((ref, args) {
  final repo = ref.watch(motoristaRepositoryProvider);
  return repo.paradas(vid: args.vid, token: args.token);
});

/// Estado simples de UI do rastreamento (o próprio serviço em segundo plano
/// roda fora do Riverpod, dentro do isolate da task — este provider só
/// reflete o que a tela mostra: ativo/parado, pontos enviados, pendentes).
final trackingAtivoProvider = StateProvider<bool>((ref) => false);
final pontosEnviadosProvider = StateProvider<int>((ref) => 0);
