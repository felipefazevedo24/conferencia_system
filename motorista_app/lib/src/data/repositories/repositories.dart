import 'dart:io';

import '../../domain/entities/models.dart';
import '../local/app_database.dart';
import '../remote/api_client.dart';

typedef ViagensPainel = ({List<Viagem> ativas, List<Viagem> historico});

/// Repositório das chamadas ao backend do motorista. Interpreta o padrão
/// `{"sucesso": bool, "msg": ...}` / `{"erro": ...}` usado pelas rotas em
/// conferencia_app/routes/viagem_routes.py e traduz para exceções.
class MotoristaRepository {
  final ApiClient _api;
  MotoristaRepository(this._api);

  void _checkOk(Map<String, dynamic> data) {
    final erro = data['erro'];
    if (erro != null) throw ApiException(erro.toString());
    if (data.containsKey('sucesso') && data['sucesso'] != true) {
      throw ApiException(data['msg']?.toString() ?? 'Falha na operação.');
    }
  }

  Future<ViagensPainel> painelViagens({required int mid, required String token}) async {
    final data = await _api.painelViagens(mid: mid, token: token);
    _checkOk(data);
    final ativas = (data['viagens'] as List? ?? [])
        .map((e) => Viagem.fromJson(e as Map<String, dynamic>))
        .toList();
    final historico = (data['historico'] as List? ?? [])
        .map((e) => Viagem.fromJson(e as Map<String, dynamic>))
        .toList();
    return (ativas: ativas, historico: historico);
  }

  /// Retorna o status da viagem após iniciar (ex.: 'EmAndamento').
  Future<String> iniciarViagem({
    required int vid,
    required String token,
    int? kmInicial,
    double? latitude,
    double? longitude,
    double? precisaoM,
  }) async {
    final data = await _api.iniciarViagem(
      vid: vid,
      token: token,
      kmInicial: kmInicial,
      latitude: latitude,
      longitude: longitude,
      precisaoM: precisaoM,
    );
    _checkOk(data);
    return data['status'] as String? ?? 'EmAndamento';
  }

  /// Envia um ping de posição. Retorna o status atual da viagem retornado
  /// pelo servidor (útil para detectar se ela já foi concluída/cancelada por
  /// outra via, ex. pelo despachante).
  Future<String?> ping({
    required int vid,
    required String token,
    required double latitude,
    required double longitude,
    double? velocidadeKmh,
    double? rumo,
    double? precisaoM,
  }) async {
    final data = await _api.ping(
      vid: vid,
      token: token,
      latitude: latitude,
      longitude: longitude,
      velocidadeKmh: velocidadeKmh,
      rumo: rumo,
      precisaoM: precisaoM,
    );
    // Ping não usa _checkOk: quando a viagem não está mais EmAndamento o
    // servidor responde sucesso=false só como aviso (200 OK), não é erro.
    return data['status'] as String?;
  }

  Future<void> concluirViagem({
    required int vid,
    required String token,
    required int kmFinal,
    double? latitude,
    double? longitude,
  }) async {
    final data = await _api.concluirViagem(
      vid: vid,
      token: token,
      kmFinal: kmFinal,
      latitude: latitude,
      longitude: longitude,
    );
    _checkOk(data);
  }

  Future<String> statusViagem({required int vid, required String token}) async {
    final data = await _api.status(vid: vid, token: token);
    _checkOk(data);
    return data['status'] as String? ?? '';
  }

  Future<List<Parada>> paradas({required int vid, required String token}) async {
    final data = await _api.paradas(vid: vid, token: token);
    _checkOk(data);
    return (data['paradas'] as List? ?? [])
        .map((e) => Parada.fromJson(e as Map<String, dynamic>))
        .toList();
  }

  Future<void> reordenarParadas({
    required int vid,
    required String token,
    required List<int> ordem,
  }) async {
    final data = await _api.reordenarParadas(vid: vid, token: token, ordem: ordem);
    _checkOk(data);
  }

  Future<void> chegarParada({
    required int vid,
    required String token,
    required int pid,
    double? latitude,
    double? longitude,
  }) async {
    final data = await _api.chegarParada(
      vid: vid,
      token: token,
      pid: pid,
      latitude: latitude,
      longitude: longitude,
    );
    _checkOk(data);
  }

  Future<void> concluirParada({
    required int vid,
    required String token,
    required int pid,
    required String resultado,
    String? observacao,
    double? latitude,
    double? longitude,
    File? foto,
  }) async {
    final data = await _api.concluirParada(
      vid: vid,
      token: token,
      pid: pid,
      resultado: resultado,
      observacao: observacao,
      latitude: latitude,
      longitude: longitude,
      foto: foto,
    );
    _checkOk(data);
  }
}

/// Fila local (sqflite) de pontos de GPS que falharam ao enviar. Diferente
/// da fila em memória do app web, sobrevive a app fechado/processo morto.
class PingQueueRepository {
  final AppDatabase _db;
  PingQueueRepository(this._db);

  Future<void> enfileirar(PendingPing ping) async {
    final db = await _db.database;
    await db.insert('pending_pings', ping.toMap()..remove('id'));
  }

  /// Pontos pendentes de uma viagem, mais antigos primeiro (preserva a ordem
  /// de envio, igual ao comportamento do app web).
  Future<List<PendingPing>> pendentes({required int vid}) async {
    final db = await _db.database;
    final rows = await db.query(
      'pending_pings',
      where: 'vid = ?',
      whereArgs: [vid],
      orderBy: 'criado_em ASC',
    );
    return rows.map(PendingPing.fromMap).toList();
  }

  Future<int> contarPendentes({required int vid}) async {
    final db = await _db.database;
    final rows = await db.rawQuery(
      'SELECT COUNT(*) AS total FROM pending_pings WHERE vid = ?',
      [vid],
    );
    return (rows.first['total'] as int?) ?? 0;
  }

  Future<void> remover(int id) async {
    final db = await _db.database;
    await db.delete('pending_pings', where: 'id = ?', whereArgs: [id]);
  }
}
