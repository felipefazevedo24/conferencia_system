import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_foreground_task/flutter_foreground_task.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:geolocator/geolocator.dart';
import 'package:intl/intl.dart';

import '../../core/services/location_tracking_service.dart';
import '../../core/theme.dart';
import '../../data/remote/api_client.dart';
import '../../domain/entities/models.dart';
import '../providers/providers.dart';

final _fmtDataHora = DateFormat('dd/MM/yyyy HH:mm');

/// Mesmo polling que a versão web já fazia (a cada 30s) para as paradas
/// refletirem alterações feitas por outra pessoa (despachante reordenando,
/// etc.) sem precisar sair e voltar da tela.
const _intervaloAtualizacaoParadas = Duration(seconds: 30);

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
  Timer? _timerParadas;

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
    _timerParadas = Timer.periodic(_intervaloAtualizacaoParadas, (_) {
      if (mounted && _vid != null && _token != null) {
        ref.invalidate(paradasProvider((vid: _vid!, token: _token!)));
      }
    });
  }

  @override
  void dispose() {
    _timerParadas?.cancel();
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
            Text(legenda, style: const TextStyle(color: AppColors.textMuted, fontSize: 12)),
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
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(mensagem), backgroundColor: AppColors.danger500));
  }

  @override
  Widget build(BuildContext context) {
    final vid = _vid;
    final token = _token;
    final finalizada = _status == 'Concluida' || _status == 'Cancelada';
    return Scaffold(
      appBar: AppBar(title: Text(widget.viagem.codigo)),
      body: vid == null || token == null
          ? const Center(child: Padding(padding: EdgeInsets.all(24), child: Text('Link da viagem inválido.')))
          : SafeArea(
              child: Column(
                children: [
                  _ResumoViagemCard(viagem: widget.viagem, status: _status),
                  if (!finalizada)
                    _CardRastreamento(
                      status: _status,
                      rastreando: _rastreando,
                      pontosEnviados: _pontosEnviados,
                      pontosPendentes: _pontosPendentes,
                      ultimaPosicaoTexto: _ultimaPosicaoTexto,
                    ),
                  if (_status == 'Planejada')
                    Padding(
                      padding: const EdgeInsets.fromLTRB(14, 0, 14, 12),
                      child: FilledButton.icon(
                        onPressed: _acaoEmAndamento ? null : _iniciarViagem,
                        icon: const Icon(Icons.play_arrow),
                        label: const Text('Iniciar viagem'),
                      ),
                    ),
                  if (_status == 'EmAndamento' && !_rastreando)
                    Padding(
                      padding: const EdgeInsets.symmetric(horizontal: 14),
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
                  Expanded(child: _ParadasList(vid: vid, token: token, somenteLeitura: finalizada)),
                  if (_status == 'EmAndamento')
                    Padding(
                      padding: const EdgeInsets.all(14),
                      child: FilledButton.icon(
                        style: FilledButton.styleFrom(backgroundColor: AppColors.accent500),
                        onPressed: _acaoEmAndamento ? null : _concluirViagem,
                        icon: const Icon(Icons.flag),
                        label: const Text('Concluir viagem'),
                      ),
                    ),
                ],
              ),
            ),
    );
  }
}

class _ResumoViagemCard extends StatelessWidget {
  final Viagem viagem;
  final String status;
  const _ResumoViagemCard({required this.viagem, required this.status});

  @override
  Widget build(BuildContext context) {
    final cor = AppTheme.corStatusViagem(status);
    final rota = [viagem.origemLabel, viagem.destinoLabel].where((e) => e != null && e.isNotEmpty).join(' → ');
    return Card(
      margin: const EdgeInsets.fromLTRB(14, 14, 14, 8),
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                if (viagem.titulo != null && viagem.titulo!.isNotEmpty)
                  Expanded(
                    child: Text(viagem.titulo!,
                        style: const TextStyle(fontWeight: FontWeight.w800, fontSize: 15, color: AppColors.text)),
                  ),
                Container(
                  padding: const EdgeInsets.symmetric(horizontal: 9, vertical: 4),
                  decoration: BoxDecoration(color: cor.withValues(alpha: 0.14), borderRadius: BorderRadius.circular(999)),
                  child: Text(status, style: TextStyle(color: cor, fontSize: 10.5, fontWeight: FontWeight.w800)),
                ),
              ],
            ),
            if (rota.isNotEmpty) ...[
              const SizedBox(height: 8),
              Row(children: [
                const Icon(Icons.alt_route, size: 16, color: AppColors.textSoft),
                const SizedBox(width: 6),
                Expanded(child: Text(rota, style: const TextStyle(color: AppColors.textMuted, fontSize: 13))),
              ]),
            ],
            const SizedBox(height: 12),
            Wrap(
              spacing: 18,
              runSpacing: 8,
              children: [
                if (viagem.veiculoLabel != null) _ResumoItem(icone: Icons.local_shipping_outlined, texto: viagem.veiculoLabel!),
                if (viagem.motoristaNome != null) _ResumoItem(icone: Icons.badge_outlined, texto: viagem.motoristaNome!),
                if (viagem.saidaReal != null)
                  _ResumoItem(icone: Icons.north_east, texto: 'Saída ${_fmtDataHora.format(viagem.saidaReal!)}')
                else if (viagem.saidaPrevista != null)
                  _ResumoItem(icone: Icons.north_east, texto: 'Prev. saída ${_fmtDataHora.format(viagem.saidaPrevista!)}'),
                if (viagem.retornoReal != null)
                  _ResumoItem(icone: Icons.south_west, texto: 'Retorno ${_fmtDataHora.format(viagem.retornoReal!)}'),
                if (viagem.kmInicial != null) _ResumoItem(icone: Icons.speed, texto: 'KM inicial ${viagem.kmInicial}'),
                if (viagem.kmFinal != null) _ResumoItem(icone: Icons.speed, texto: 'KM final ${viagem.kmFinal}'),
                if (viagem.kmPercorrido != null)
                  _ResumoItem(icone: Icons.route, texto: '${viagem.kmPercorrido!.toStringAsFixed(0)} km percorridos'),
                if (viagem.tempoTotalMin != null)
                  _ResumoItem(icone: Icons.timer_outlined, texto: '${(viagem.tempoTotalMin! / 60).floor()}h${(viagem.tempoTotalMin! % 60).toString().padLeft(2, '0')}'),
                if (viagem.qtdParadas != null && viagem.qtdParadas! > 0)
                  _ResumoItem(icone: Icons.flag_outlined, texto: '${viagem.qtdParadasOk ?? 0}/${viagem.qtdParadas} paradas'),
              ],
            ),
            if (viagem.observacao != null && viagem.observacao!.isNotEmpty) ...[
              const SizedBox(height: 10),
              Text(viagem.observacao!, style: const TextStyle(color: AppColors.textMuted, fontSize: 12.5, fontStyle: FontStyle.italic)),
            ],
          ],
        ),
      ),
    );
  }
}

class _ResumoItem extends StatelessWidget {
  final IconData icone;
  final String texto;
  const _ResumoItem({required this.icone, required this.texto});

  @override
  Widget build(BuildContext context) {
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        Icon(icone, size: 14, color: AppColors.textSoft),
        const SizedBox(width: 4),
        Text(texto, style: const TextStyle(fontSize: 12, color: AppColors.textMuted, fontWeight: FontWeight.w600)),
      ],
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
              color: rastreando ? AppColors.accent500 : AppColors.textSoft,
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
                    style: const TextStyle(fontSize: 12, color: AppColors.textMuted),
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
  final bool somenteLeitura;
  const _ParadasList({required this.vid, required this.token, this.somenteLeitura = false});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final args = (vid: vid, token: token);
    final paradasAsync = ref.watch(paradasProvider(args));
    return paradasAsync.when(
      loading: () => const Center(child: CircularProgressIndicator()),
      error: (e, _) => Center(child: Text('Erro ao carregar paradas: $e')),
      data: (paradas) {
        if (paradas.isEmpty) {
          return const Center(child: Text('Nenhuma parada planejada.', style: TextStyle(color: AppColors.textMuted)));
        }
        final pendentes = paradas.where((p) => !p.concluida).toList();
        final concluidas = paradas.where((p) => p.concluida).toList();
        return ListView(
          padding: const EdgeInsets.fromLTRB(14, 8, 14, 8),
          children: [
            if (pendentes.isNotEmpty) ...[
              Padding(
                padding: const EdgeInsets.symmetric(vertical: 8),
                child: Text(
                  somenteLeitura ? 'PENDENTES' : 'PENDENTES — arraste para ajustar a ordem',
                  style: const TextStyle(fontSize: 11, fontWeight: FontWeight.bold, color: AppColors.textSoft),
                ),
              ),
              somenteLeitura
                  ? Column(
                      children: pendentes
                          .map((p) => _ParadaTile(key: ValueKey(p.id), parada: p, vid: vid, token: token, somenteLeitura: true))
                          .toList(),
                    )
                  : ReorderableListView.builder(
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
                child: Text('CONCLUÍDAS', style: TextStyle(fontSize: 11, fontWeight: FontWeight.bold, color: AppColors.textSoft)),
              ),
              ...concluidas.map((p) => _ParadaTile(key: ValueKey(p.id), parada: p, vid: vid, token: token, somenteLeitura: somenteLeitura)),
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
  final bool somenteLeitura;
  const _ParadaTile({super.key, required this.parada, required this.vid, required this.token, this.somenteLeitura = false});

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
          backgroundColor: p.concluida ? AppColors.accent500.withValues(alpha: 0.14) : AppColors.primary600.withValues(alpha: 0.10),
          foregroundColor: p.concluida ? AppColors.accent500 : AppColors.primary600,
          child: Text('${p.sequencia}'),
        ),
        title: Text('${p.parceiroNome ?? 'Parada ${p.sequencia}'} · ${_labelTipo[p.tipo] ?? p.tipo}'),
        subtitle: subtitulo.isEmpty ? null : Text(subtitulo),
        trailing: _carregando
            ? const SizedBox(width: 20, height: 20, child: CircularProgressIndicator(strokeWidth: 2))
            : p.concluida
                ? const Icon(Icons.check_circle, color: AppColors.accent500)
                : widget.somenteLeitura
                    ? null
                    : p.noLocal
                        ? TextButton(onPressed: _abrirConcluir, child: const Text('Concluir'))
                        : TextButton(onPressed: _marcarChegada, child: const Text('Cheguei')),
      ),
    );
  }
}
