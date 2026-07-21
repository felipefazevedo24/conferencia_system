import 'package:flutter/material.dart';
import 'package:flutter_foreground_task/flutter_foreground_task.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:geolocator/geolocator.dart';

import '../../core/services/location_tracking_service.dart';
import '../../data/remote/api_client.dart';
import '../../domain/entities/models.dart';
import '../providers/providers.dart';

class ViagemDetailScreen extends ConsumerStatefulWidget {
  final Viagem viagem;
  const ViagemDetailScreen({super.key, required this.viagem});

  @override
  ConsumerState<ViagemDetailScreen> createState() => _ViagemDetailScreenState();
}

class _ViagemDetailScreenState extends ConsumerState<ViagemDetailScreen> {
  late String _status;
  int? _vid;
  String? _token;
  bool _rastreando = false;
  int _pontosEnviados = 0;
  int _pontosPendentes = 0;
  String? _ultimaPosicaoTexto;
  bool _acaoEmAndamento = false;

  ({int vid, String token})? get _tokenViagem => widget.viagem.viagemToken;

  @override
  void initState() {
    super.initState();
    _status = widget.viagem.status;
    final t = _tokenViagem;
    _vid = t?.vid;
    _token = t?.token;
    FlutterForegroundTask.addTaskDataCallback(_onTaskData);
    _sincronizarEstadoRastreamento();
  }

  @override
  void dispose() {
    FlutterForegroundTask.removeTaskDataCallback(_onTaskData);
    super.dispose();
  }

  Future<void> _sincronizarEstadoRastreamento() async {
    final rodando = await FlutterForegroundTask.isRunningService;
    if (mounted) setState(() => _rastreando = rodando);
  }

  void _onTaskData(Object data) {
    if (data is! Map) return;
    switch (data['tipo']) {
      case 'ping_ok':
        setState(() {
          _pontosEnviados++;
          final vel = data['velocidade'];
          _ultimaPosicaoTexto = vel != null
              ? '${(vel as num).toStringAsFixed(0)} km/h · agora'
              : 'atualizado agora';
        });
        break;
      case 'ping_offline':
        setState(() => _pontosPendentes++);
        break;
      case 'viagem_finalizada':
        setState(() {
          _status = data['status'] as String? ?? _status;
          _rastreando = false;
        });
        ref.invalidate(viagensProvider);
        if (mounted) {
          ScaffoldMessenger.of(context).showSnackBar(
            const SnackBar(content: Text('Viagem finalizada pelo sistema.')),
          );
        }
        break;
    }
  }

  Future<Map<String, double?>> _obterPosicaoAtual() async {
    try {
      final pos = await Geolocator.getCurrentPosition(
        locationSettings: const LocationSettings(accuracy: LocationAccuracy.high, timeLimit: Duration(seconds: 8)),
      );
      return {'latitude': pos.latitude, 'longitude': pos.longitude, 'precisao_m': pos.accuracy};
    } catch (_) {
      return {'latitude': null, 'longitude': null, 'precisao_m': null};
    }
  }

  Future<void> _iniciarViagem() async {
    final km = await _pedirKm(titulo: 'KM inicial', legenda: 'Leitura do odômetro agora (km)');
    if (km == null) return;
    setState(() => _acaoEmAndamento = true);
    try {
      final pos = await _obterPosicaoAtual();
      final vid = _vid!, token = _token!;
      final repo = ref.read(motoristaRepositoryProvider);
      final novoStatus = await repo.iniciarViagem(
        vid: vid,
        token: token,
        kmInicial: km,
        latitude: pos['latitude'],
        longitude: pos['longitude'],
        precisaoM: pos['precisao_m'],
      );
      final permitido = await LocationTrackingService.solicitarPermissoes();
      if (!permitido && mounted) {
        _mostrarErro('Permissão de localização negada. Libere "permitir sempre" nas configurações do app para rastrear a viagem.');
      }
      await LocationTrackingService.iniciar(vid: vid, token: token, codigoViagem: widget.viagem.codigo);
      setState(() {
        _status = novoStatus;
        _rastreando = true;
      });
      ref.invalidate(viagensProvider);
    } on ApiException catch (e) {
      _mostrarErro(e.message);
    } finally {
      if (mounted) setState(() => _acaoEmAndamento = false);
    }
  }

  Future<void> _concluirViagem() async {
    final km = await _pedirKm(titulo: 'KM final', legenda: 'Leitura do odômetro ao chegar (km)');
    if (km == null) return;
    setState(() => _acaoEmAndamento = true);
    try {
      final pos = await _obterPosicaoAtual();
      final repo = ref.read(motoristaRepositoryProvider);
      await repo.concluirViagem(
        vid: _vid!,
        token: _token!,
        kmFinal: km,
        latitude: pos['latitude'],
        longitude: pos['longitude'],
      );
      await LocationTrackingService.parar();
      setState(() {
        _status = 'Concluida';
        _rastreando = false;
      });
      ref.invalidate(viagensProvider);
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('Viagem concluída!')));
        Navigator.of(context).pop();
      }
    } on ApiException catch (e) {
      _mostrarErro(e.message);
    } finally {
      if (mounted) setState(() => _acaoEmAndamento = false);
    }
  }

  Future<int?> _pedirKm({required String titulo, required String legenda}) async {
    final controller = TextEditingController();
    return showDialog<int>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text(titulo),
        content: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(legenda, style: const TextStyle(color: Colors.black54, fontSize: 12)),
            const SizedBox(height: 8),
            TextField(
              controller: controller,
              autofocus: true,
              keyboardType: TextInputType.number,
              decoration: const InputDecoration(border: OutlineInputBorder(), hintText: 'Ex: 123456'),
            ),
          ],
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx), child: const Text('Cancelar')),
          FilledButton(
            onPressed: () {
              final km = int.tryParse(controller.text.trim());
              Navigator.pop(ctx, km);
            },
            child: const Text('Confirmar'),
          ),
        ],
      ),
    );
  }

  void _mostrarErro(String mensagem) {
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(mensagem), backgroundColor: Colors.red));
  }

  @override
  Widget build(BuildContext context) {
    final vid = _vid;
    final token = _token;
    return Scaffold(
      appBar: AppBar(title: Text(widget.viagem.codigo)),
      body: vid == null || token == null
          ? const Center(child: Padding(padding: EdgeInsets.all(24), child: Text('Link da viagem inválido.')))
          : Column(
              children: [
                _CardRastreamento(
                  status: _status,
                  rastreando: _rastreando,
                  pontosEnviados: _pontosEnviados,
                  pontosPendentes: _pontosPendentes,
                  ultimaPosicaoTexto: _ultimaPosicaoTexto,
                ),
                if (_status == 'Planejada')
                  Padding(
                    padding: const EdgeInsets.all(12),
                    child: FilledButton.icon(
                      onPressed: _acaoEmAndamento ? null : _iniciarViagem,
                      icon: const Icon(Icons.play_arrow),
                      label: const Text('Iniciar viagem'),
                    ),
                  ),
                if (_status == 'EmAndamento' && !_rastreando)
                  Padding(
                    padding: const EdgeInsets.symmetric(horizontal: 12),
                    child: OutlinedButton.icon(
                      onPressed: () async {
                        await LocationTrackingService.solicitarPermissoes();
                        await LocationTrackingService.iniciar(vid: vid, token: token, codigoViagem: widget.viagem.codigo);
                        setState(() => _rastreando = true);
                      },
                      icon: const Icon(Icons.satellite_alt),
                      label: const Text('Retomar rastreamento'),
                    ),
                  ),
                if (_status == 'EmAndamento' || _status == 'Planejada')
                  Expanded(child: _ParadasList(vid: vid, token: token)),
                if (_status == 'EmAndamento')
                  Padding(
                    padding: const EdgeInsets.all(12),
                    child: FilledButton.icon(
                      style: FilledButton.styleFrom(backgroundColor: const Color(0xFF0F8F5F)),
                      onPressed: _acaoEmAndamento ? null : _concluirViagem,
                      icon: const Icon(Icons.flag),
                      label: const Text('Concluir viagem'),
                    ),
                  ),
                if (_status == 'Concluida' || _status == 'Cancelada')
                  Expanded(
                    child: Center(
                      child: Text(_status == 'Concluida' ? 'Viagem concluída.' : 'Viagem cancelada.'),
                    ),
                  ),
              ],
            ),
    );
  }
}

class _CardRastreamento extends StatelessWidget {
  final String status;
  final bool rastreando;
  final int pontosEnviados;
  final int pontosPendentes;
  final String? ultimaPosicaoTexto;

  const _CardRastreamento({
    required this.status,
    required this.rastreando,
    required this.pontosEnviados,
    required this.pontosPendentes,
    required this.ultimaPosicaoTexto,
  });

  @override
  Widget build(BuildContext context) {
    return Card(
      margin: const EdgeInsets.all(12),
      child: Padding(
        padding: const EdgeInsets.all(14),
        child: Row(
          children: [
            Icon(
              rastreando ? Icons.satellite_alt : Icons.satellite_alt_outlined,
              color: rastreando ? const Color(0xFF0F8F5F) : Colors.black38,
              size: 32,
            ),
            const SizedBox(width: 12),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    rastreando ? 'Rastreamento ativo' : 'Rastreamento parado',
                    style: const TextStyle(fontWeight: FontWeight.bold),
                  ),
                  Text(
                    '$pontosEnviados ponto${pontosEnviados == 1 ? '' : 's'} enviado${pontosEnviados == 1 ? '' : 's'}'
                    '${pontosPendentes > 0 ? ' · $pontosPendentes pendente${pontosPendentes == 1 ? '' : 's'} offline' : ''}',
                    style: const TextStyle(fontSize: 12, color: Colors.black54),
                  ),
                  if (ultimaPosicaoTexto != null)
                    Text(ultimaPosicaoTexto!, style: const TextStyle(fontSize: 12, color: Colors.black54)),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _ParadasList extends ConsumerWidget {
  final int vid;
  final String token;
  const _ParadasList({required this.vid, required this.token});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final args = (vid: vid, token: token);
    final paradasAsync = ref.watch(paradasProvider(args));
    return paradasAsync.when(
      loading: () => const Center(child: CircularProgressIndicator()),
      error: (e, _) => Center(child: Text('Erro ao carregar paradas: $e')),
      data: (paradas) {
        if (paradas.isEmpty) {
          return const Center(child: Text('Nenhuma parada planejada.'));
        }
        final pendentes = paradas.where((p) => !p.concluida).toList();
        final concluidas = paradas.where((p) => p.concluida).toList();
        return ListView(
          padding: const EdgeInsets.symmetric(horizontal: 12),
          children: [
            if (pendentes.isNotEmpty) ...[
              const Padding(
                padding: EdgeInsets.symmetric(vertical: 8),
                child: Text('PENDENTES — arraste para ajustar a ordem',
                    style: TextStyle(fontSize: 11, fontWeight: FontWeight.bold, color: Colors.black54)),
              ),
              ReorderableListView.builder(
                shrinkWrap: true,
                physics: const NeverScrollableScrollPhysics(),
                itemCount: pendentes.length,
                itemBuilder: (ctx, i) => _ParadaTile(
                  key: ValueKey(pendentes[i].id),
                  parada: pendentes[i],
                  vid: vid,
                  token: token,
                ),
                onReorder: (oldIndex, newIndex) async {
                  final novaLista = List<Parada>.from(pendentes);
                  if (newIndex > oldIndex) newIndex -= 1;
                  final item = novaLista.removeAt(oldIndex);
                  novaLista.insert(newIndex, item);
                  final repo = ref.read(motoristaRepositoryProvider);
                  try {
                    await repo.reordenarParadas(vid: vid, token: token, ordem: novaLista.map((p) => p.id).toList());
                  } on ApiException {
                    // Ignora silenciosamente: o refresh abaixo já vai trazer a
                    // ordem real do servidor de volta.
                  }
                  ref.invalidate(paradasProvider(args));
                },
              ),
            ],
            if (concluidas.isNotEmpty) ...[
              const Padding(
                padding: EdgeInsets.symmetric(vertical: 8),
                child: Text('CONCLUÍDAS', style: TextStyle(fontSize: 11, fontWeight: FontWeight.bold, color: Colors.black54)),
              ),
              ...concluidas.map((p) => _ParadaTile(key: ValueKey(p.id), parada: p, vid: vid, token: token)),
            ],
            const SizedBox(height: 24),
          ],
        );
      },
    );
  }
}

class _ParadaTile extends ConsumerStatefulWidget {
  final Parada parada;
  final int vid;
  final String token;
  const _ParadaTile({super.key, required this.parada, required this.vid, required this.token});

  @override
  ConsumerState<_ParadaTile> createState() => _ParadaTileState();
}

class _ParadaTileState extends ConsumerState<_ParadaTile> {
  bool _carregando = false;

  static const _labelTipo = {
    'COLETA': 'Coleta',
    'ENTREGA': 'Entrega',
    'PARADA': 'Parada',
    'ABASTECIMENTO': 'Abastecimento',
    'REFEICAO': 'Refeição',
  };

  Future<Map<String, double?>> _pos() async {
    try {
      final p = await Geolocator.getCurrentPosition(
        locationSettings: const LocationSettings(accuracy: LocationAccuracy.medium, timeLimit: Duration(seconds: 4)),
      );
      return {'latitude': p.latitude, 'longitude': p.longitude};
    } catch (_) {
      return {'latitude': null, 'longitude': null};
    }
  }

  Future<void> _marcarChegada() async {
    setState(() => _carregando = true);
    try {
      final pos = await _pos();
      final repo = ref.read(motoristaRepositoryProvider);
      await repo.chegarParada(
        vid: widget.vid,
        token: widget.token,
        pid: widget.parada.id,
        latitude: pos['latitude'],
        longitude: pos['longitude'],
      );
      ref.invalidate(paradasProvider((vid: widget.vid, token: widget.token)));
    } on ApiException catch (e) {
      if (mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(e.message)));
    } finally {
      if (mounted) setState(() => _carregando = false);
    }
  }

  Future<void> _abrirConcluir() async {
    final resultado = ValueNotifier<String>(widget.parada.tipo == 'COLETA' ? 'Coletado' : 'Entregue');
    final obsController = TextEditingController();
    final confirmou = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('Concluir parada'),
        content: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const Text('Resultado', style: TextStyle(fontSize: 12, fontWeight: FontWeight.bold)),
            ValueListenableBuilder<String>(
              valueListenable: resultado,
              builder: (ctx, valor, _) => DropdownButton<String>(
                value: valor,
                isExpanded: true,
                items: const [
                  DropdownMenuItem(value: 'Entregue', child: Text('Entregue')),
                  DropdownMenuItem(value: 'Coletado', child: Text('Coletado')),
                  DropdownMenuItem(value: 'NaoRealizada', child: Text('Não realizada')),
                  DropdownMenuItem(value: 'Recusado', child: Text('Recusado pelo destinatário')),
                  DropdownMenuItem(value: 'AusenciaRecebedor', child: Text('Ausência do recebedor')),
                  DropdownMenuItem(value: 'Outros', child: Text('Outros')),
                ],
                onChanged: (v) => resultado.value = v ?? resultado.value,
              ),
            ),
            const SizedBox(height: 12),
            const Text('Observação (opcional)', style: TextStyle(fontSize: 12, fontWeight: FontWeight.bold)),
            TextField(
              controller: obsController,
              maxLines: 3,
              decoration: const InputDecoration(border: OutlineInputBorder()),
            ),
          ],
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, false), child: const Text('Cancelar')),
          FilledButton(onPressed: () => Navigator.pop(ctx, true), child: const Text('Confirmar')),
        ],
      ),
    );
    if (confirmou != true) return;
    setState(() => _carregando = true);
    try {
      final pos = await _pos();
      final repo = ref.read(motoristaRepositoryProvider);
      await repo.concluirParada(
        vid: widget.vid,
        token: widget.token,
        pid: widget.parada.id,
        resultado: resultado.value,
        observacao: obsController.text.trim(),
        latitude: pos['latitude'],
        longitude: pos['longitude'],
      );
      ref.invalidate(paradasProvider((vid: widget.vid, token: widget.token)));
    } on ApiException catch (e) {
      if (mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(e.message)));
    } finally {
      if (mounted) setState(() => _carregando = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final p = widget.parada;
    final subtitulo = [
      if (p.endereco != null) p.endereco!,
      if (p.cidade != null) '${p.cidade}${p.uf != null ? '/${p.uf}' : ''}',
    ].join(' · ');
    return Card(
      margin: const EdgeInsets.only(bottom: 8),
      child: ListTile(
        leading: CircleAvatar(
          backgroundColor: p.concluida ? const Color(0xFFDFF7EB) : const Color(0xFFE6F0FB),
          foregroundColor: p.concluida ? const Color(0xFF06633E) : const Color(0xFF0F62C9),
          child: Text('${p.sequencia}'),
        ),
        title: Text('${p.parceiroNome ?? 'Parada ${p.sequencia}'} · ${_labelTipo[p.tipo] ?? p.tipo}'),
        subtitle: subtitulo.isEmpty ? null : Text(subtitulo),
        trailing: _carregando
            ? const SizedBox(width: 20, height: 20, child: CircularProgressIndicator(strokeWidth: 2))
            : p.concluida
                ? const Icon(Icons.check_circle, color: Color(0xFF0F8F5F))
                : p.noLocal
                    ? TextButton(onPressed: _abrirConcluir, child: const Text('Concluir'))
                    : TextButton(onPressed: _marcarChegada, child: const Text('Cheguei')),
      ),
    );
  }
}
