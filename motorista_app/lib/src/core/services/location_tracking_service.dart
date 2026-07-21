import 'package:flutter_foreground_task/flutter_foreground_task.dart';
import 'package:geolocator/geolocator.dart';

import '../../data/local/app_database.dart';
import '../../data/remote/api_client.dart';
import '../../data/repositories/repositories.dart';
import '../../domain/entities/models.dart';
import '../config.dart';

/// Rastreamento em segundo plano de verdade: roda dentro de um Foreground
/// Service Android (flutter_foreground_task), com notificação persistente
/// que o sistema não remove nem suspende — ao contrário da aba de navegador
/// do app web antigo, que o Android mata sob pressão de memória ou após
/// minimizar.
///
/// IMPORTANTE (verificar ao compilar): a API do flutter_foreground_task
/// mudou entre versões majors. O que está aqui reflete o desenho estável
/// (TaskHandler + onRepeatEvent) das últimas versões — se `flutter analyze`
/// apontar divergência de assinatura nesta classe, ajuste conforme o exemplo
/// da versão resolvida pelo `flutter pub get` (pub.dev/packages/flutter_foreground_task).
class LocationTrackingService {
  static const String notificationChannelId = 'motorista_tracking_channel';

  static void initialize() {
    FlutterForegroundTask.init(
      androidNotificationOptions: AndroidNotificationOptions(
        channelId: notificationChannelId,
        channelName: 'Rastreamento de viagem',
        channelDescription: 'Notificação enquanto uma viagem está em andamento.',
        channelImportance: NotificationChannelImportance.LOW,
        priority: NotificationPriority.LOW,
      ),
      iosNotificationOptions: const IOSNotificationOptions(),
      foregroundTaskOptions: ForegroundTaskOptions(
        eventAction: ForegroundTaskEventAction.repeat(AppConfig.pingIntervaloMinimo.inMilliseconds),
        autoRunOnBoot: false,
        allowWakeLock: true,
        allowWifiLock: true,
      ),
    );
  }

  static Future<bool> solicitarPermissoes() async {
    // Localização em primeiro plano.
    var permissao = await Geolocator.checkPermission();
    if (permissao == LocationPermission.denied) {
      permissao = await Geolocator.requestPermission();
    }
    if (permissao == LocationPermission.denied ||
        permissao == LocationPermission.deniedForever) {
      return false;
    }
    if (!await Geolocator.isLocationServiceEnabled()) {
      return false;
    }

    // Localização em segundo plano ("permitir sempre") — obrigatória a
    // partir do Android 10 para o foreground service continuar recebendo
    // posições com a tela apagada ou o app minimizado.
    if (permissao != LocationPermission.always) {
      permissao = await Geolocator.requestPermission();
    }

    // Notificação (Android 13+) e permissões específicas do foreground task.
    final notifPermission = await FlutterForegroundTask.checkNotificationPermission();
    if (notifPermission != NotificationPermission.granted) {
      await FlutterForegroundTask.requestNotificationPermission();
    }

    return true;
  }

  static Future<void> iniciar({required int vid, required String token, required String codigoViagem}) async {
    if (await FlutterForegroundTask.isRunningService) {
      await FlutterForegroundTask.stopService();
    }
    await FlutterForegroundTask.startService(
      serviceId: 256,
      notificationTitle: 'Rastreamento ativo',
      notificationText: 'Viagem $codigoViagem em andamento',
      callback: _startCallback,
    );
    // Envia vid/token para dentro do isolate da task assim que ela estiver
    // pronta para receber (o handler também aceita reconectar se perder a
    // primeira mensagem, por isso reenviamos com um pequeno atraso).
    FlutterForegroundTask.sendDataToTask({'vid': vid, 'token': token});
    await Future.delayed(const Duration(milliseconds: 500));
    FlutterForegroundTask.sendDataToTask({'vid': vid, 'token': token});
  }

  static Future<void> parar() async {
    await FlutterForegroundTask.stopService();
  }
}

@pragma('vm:entry-point')
void _startCallback() {
  FlutterForegroundTask.setTaskHandler(_LocationTaskHandler());
}

class _LocationTaskHandler extends TaskHandler {
  int? _vid;
  String? _token;
  double? _ultLat;
  double? _ultLng;
  DateTime? _ultEnvio;

  late final MotoristaRepository _repo;
  late final PingQueueRepository _fila;

  @override
  Future<void> onStart(DateTime timestamp, TaskStarter starter) async {
    _repo = MotoristaRepository(ApiClient());
    _fila = PingQueueRepository(AppDatabase.instance);
  }

  @override
  void onReceiveData(Object data) {
    if (data is Map) {
      final vid = data['vid'];
      final token = data['token'];
      if (vid is int) _vid = vid;
      if (token is String) _token = token;
    }
  }

  @override
  void onRepeatEvent(DateTime timestamp) async {
    final vid = _vid;
    final token = _token;
    if (vid == null || token == null) return;

    // Antes de capturar um ponto novo, tenta drenar a fila offline — assim
    // os pontos perdidos durante uma queda de conexão saem na ordem certa.
    await _drenarFila(vid);

    Position pos;
    try {
      pos = await Geolocator.getCurrentPosition(
        locationSettings: const LocationSettings(accuracy: LocationAccuracy.high),
      );
    } catch (_) {
      return;
    }

    final agora = DateTime.now();
    final distancia = (_ultLat != null && _ultLng != null)
        ? Geolocator.distanceBetween(_ultLat!, _ultLng!, pos.latitude, pos.longitude)
        : double.infinity;
    final tempoDesdeUltimo = _ultEnvio == null ? null : agora.difference(_ultEnvio!);
    final dentroDoIntervalo =
        tempoDesdeUltimo != null && tempoDesdeUltimo < AppConfig.pingIntervaloMinimo;
    if (dentroDoIntervalo && distancia < AppConfig.pingDistanciaMinimaM) {
      return;
    }
    _ultLat = pos.latitude;
    _ultLng = pos.longitude;
    _ultEnvio = agora;

    final velocidadeKmh = pos.speed >= 0 ? pos.speed * 3.6 : null;

    try {
      final status = await _repo.ping(
        vid: vid,
        token: token,
        latitude: pos.latitude,
        longitude: pos.longitude,
        velocidadeKmh: velocidadeKmh,
        rumo: pos.heading,
        precisaoM: pos.accuracy,
      );
      FlutterForegroundTask.sendDataToMain({
        'tipo': 'ping_ok',
        'status': status,
        'lat': pos.latitude,
        'lng': pos.longitude,
        'velocidade': velocidadeKmh,
        'em': agora.toIso8601String(),
      });
      if (status == 'Concluida' || status == 'Cancelada') {
        FlutterForegroundTask.sendDataToMain({'tipo': 'viagem_finalizada', 'status': status});
      }
    } catch (_) {
      // Sem conexão ou erro no servidor: guarda em disco, não perde o ponto.
      await _fila.enfileirar(PendingPing(
        vid: vid,
        token: token,
        latitude: pos.latitude,
        longitude: pos.longitude,
        velocidadeKmh: velocidadeKmh,
        rumo: pos.heading,
        precisaoM: pos.accuracy,
        criadoEm: agora,
      ));
      FlutterForegroundTask.sendDataToMain({'tipo': 'ping_offline'});
    }
  }

  Future<void> _drenarFila(int vid) async {
    final pendentes = await _fila.pendentes(vid: vid);
    for (final p in pendentes) {
      try {
        await _repo.ping(
          vid: p.vid,
          token: p.token,
          latitude: p.latitude,
          longitude: p.longitude,
          velocidadeKmh: p.velocidadeKmh,
          rumo: p.rumo,
          precisaoM: p.precisaoM,
        );
        if (p.id != null) await _fila.remover(p.id!);
      } catch (_) {
        // Continua sem conexão: para e tenta de novo no próximo ciclo,
        // preservando a ordem (não pula pontos).
        break;
      }
    }
  }

  @override
  Future<void> onDestroy(DateTime timestamp) async {}

  @override
  void onNotificationButtonPressed(String id) {}

  @override
  void onNotificationPressed() {
    FlutterForegroundTask.launchApp('/');
  }

  @override
  void onNotificationDismissed() {}
}
