import 'dart:convert';

import 'package:http/http.dart' as http;

import '../../core/config.dart';

/// Erro de chamada à API (rede indisponível, servidor retornou erro, etc.).
class ApiException implements Exception {
  final String message;
  ApiException(this.message);

  @override
  String toString() => message;
}

/// Wrapper fino sobre `http` para os endpoints públicos (protegidos por
/// token HMAC na própria URL, sem sessão/login) do motorista.
/// Ver conferencia_app/routes/viagem_routes.py (blueprint motorista_bp).
class ApiClient {
  final http.Client _client;
  static const _timeout = Duration(seconds: 20);

  ApiClient({http.Client? client}) : _client = client ?? http.Client();

  Uri _uri(String path) => Uri.parse('${AppConfig.baseUrl}$path');

  Future<Map<String, dynamic>> _get(String path) async {
    final http.Response res;
    try {
      res = await _client.get(_uri(path)).timeout(_timeout);
    } catch (_) {
      throw ApiException('Sem conexão com o servidor.');
    }
    return _decode(res);
  }

  Future<Map<String, dynamic>> _post(String path, Map<String, dynamic> body) async {
    final http.Response res;
    try {
      res = await _client
          .post(
            _uri(path),
            headers: {'Content-Type': 'application/json'},
            body: jsonEncode(body),
          )
          .timeout(_timeout);
    } catch (_) {
      throw ApiException('Sem conexão com o servidor.');
    }
    return _decode(res);
  }

  Map<String, dynamic> _decode(http.Response res) {
    Map<String, dynamic> data;
    try {
      data = jsonDecode(res.body) as Map<String, dynamic>;
    } catch (_) {
      throw ApiException('Resposta inválida do servidor (${res.statusCode}).');
    }
    if (res.statusCode >= 500) {
      throw ApiException(data['msg'] as String? ?? 'Erro no servidor (${res.statusCode}).');
    }
    return data;
  }

  // ---- Painel do motorista ----

  Future<Map<String, dynamic>> painelViagens({required int mid, required String token}) {
    return _get('/motorista/painel/$mid/$token/viagens');
  }

  // ---- Viagem (por vid + token específico da viagem) ----

  Future<Map<String, dynamic>> iniciarViagem({
    required int vid,
    required String token,
    int? kmInicial,
    double? latitude,
    double? longitude,
    double? precisaoM,
  }) {
    return _post('/motorista/viagem/$vid/$token/iniciar', {
      'km_inicial': kmInicial,
      'latitude': latitude,
      'longitude': longitude,
      'precisao_m': precisaoM,
    });
  }

  Future<Map<String, dynamic>> ping({
    required int vid,
    required String token,
    required double latitude,
    required double longitude,
    double? velocidadeKmh,
    double? rumo,
    double? precisaoM,
  }) {
    return _post('/motorista/viagem/$vid/$token/ping', {
      'latitude': latitude,
      'longitude': longitude,
      'velocidade_kmh': velocidadeKmh,
      'rumo': rumo,
      'precisao_m': precisaoM,
    });
  }

  Future<Map<String, dynamic>> concluirViagem({
    required int vid,
    required String token,
    required int kmFinal,
    double? latitude,
    double? longitude,
  }) {
    return _post('/motorista/viagem/$vid/$token/concluir', {
      'km_final': kmFinal,
      'latitude': latitude,
      'longitude': longitude,
    });
  }

  Future<Map<String, dynamic>> status({required int vid, required String token}) {
    return _get('/motorista/viagem/$vid/$token/status');
  }

  Future<Map<String, dynamic>> paradas({required int vid, required String token}) {
    return _get('/motorista/viagem/$vid/$token/paradas');
  }

  Future<Map<String, dynamic>> reordenarParadas({
    required int vid,
    required String token,
    required List<int> ordem,
  }) {
    return _post('/motorista/viagem/$vid/$token/paradas/reordenar', {'ordem': ordem});
  }

  Future<Map<String, dynamic>> chegarParada({
    required int vid,
    required String token,
    required int pid,
    double? latitude,
    double? longitude,
  }) {
    return _post('/motorista/viagem/$vid/$token/parada/$pid/chegar', {
      'latitude': latitude,
      'longitude': longitude,
    });
  }

  Future<Map<String, dynamic>> concluirParada({
    required int vid,
    required String token,
    required int pid,
    required String resultado,
    String? observacao,
    double? latitude,
    double? longitude,
  }) {
    return _post('/motorista/viagem/$vid/$token/parada/$pid/concluir', {
      'resultado': resultado,
      'observacao': observacao,
      'latitude': latitude,
      'longitude': longitude,
    });
  }
}
