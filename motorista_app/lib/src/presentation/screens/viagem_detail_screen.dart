import 'dart:async';
import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter_foreground_task/flutter_foreground_task.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:geolocator/geolocator.dart';
import 'package:image_picker/image_picker.dart';
import 'package:intl/intl.dart';

import '../../core/services/location_tracking_service.dart';
import '../../core/theme.dart';
import '../../data/remote/api_client.dart';
import '../../domain/entities/models.dart';
import '../providers/providers.dart';
import '../widgets/ui.dart';

final _fmtDataHora = DateFormat('dd/MM HH:mm');

/// Mesmo polling que a versão web já fazia (a cada 30s) para as paradas
/// refletirem alterações feitas por outra pessoa (despachante reordenando,
/// etc.) sem precisar sair e voltar da tela.
const _intervaloAtualizacaoParadas = Duration(seconds: 30);

const _labelTipo = {
  'COLETA': 'Coleta',
  'ENTREGA': 'Entrega',
  'PARADA': 'Parada',
  'ABASTECIMENTO': 'Abastecimento',
  'REFEICAO': 'Refeição',
};

Color _corTipo(String tipo) {
  switch (tipo) {
    case 'COLETA':
      return const Color(0xFFB7791F);
    case 'ENTREGA':
      return AppColors.primary600;
    default:
      return AppColors.textMuted;
  }
}

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
  int? _kmInicial;
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
    _kmInicial = widget.viagem.kmInicial;
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
    final km = await pedirKm(
      context,
      titulo: 'KM inicial',
      legenda: 'Leitura do odômetro agora',
      referencia: widget.viagem.kmReferencia,
    );
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
        _kmInicial = km;
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
    final km = await pedirKm(
      context,
      titulo: 'KM final',
      legenda: 'Leitura do odômetro ao chegar',
      kmInicial: _kmInicial,
    );
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

  Future<void> _retomarRastreamento() async {
    await LocationTrackingService.solicitarPermissoes();
    await LocationTrackingService.iniciar(vid: _vid!, token: _token!, codigoViagem: widget.viagem.codigo);
    if (mounted) setState(() => _rastreando = true);
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
          : Column(children: [
              Expanded(
                child: RefreshIndicator(
                  onRefresh: () async => ref.invalidate(paradasProvider((vid: vid, token: token))),
                  child: ListView(
                    padding: const EdgeInsets.fromLTRB(16, 16, 16, 24),
                    children: [
                      _Cabecalho(viagem: widget.viagem, status: _status, vid: vid, token: token, kmInicial: _kmInicial),
                      if (_status == 'EmAndamento') ...[
                        const SizedBox(height: 12),
                        _CardRastreamento(
                          status: _status,
                          rastreando: _rastreando,
                          pontosEnviados: _pontosEnviados,
                          pontosPendentes: _pontosPendentes,
                          ultimaPosicaoTexto: _ultimaPosicaoTexto,
                          onRetomar: _retomarRastreamento,
                        ),
                      ],
                      _ParadasSecao(vid: vid, token: token, somenteLeitura: finalizada),
                    ],
                  ),
                ),
              ),
              if (_status == 'Planejada' || _status == 'EmAndamento')
                _BarraAcao(
                  status: _status,
                  ocupado: _acaoEmAndamento,
                  onIniciar: _iniciarViagem,
                  onConcluir: _concluirViagem,
                ),
            ]),
    );
  }
}

class _Cabecalho extends ConsumerWidget {
  final Viagem viagem;
  final String status;
  final int vid;
  final String token;
  final int? kmInicial;
  const _Cabecalho({required this.viagem, required this.status, required this.vid, required this.token, this.kmInicial});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    // Progresso pelas paradas carregadas (atualiza ao concluir uma), com o
    // resumo da lista como reserva enquanto carrega.
    final paradas = ref.watch(paradasProvider((vid: vid, token: token))).value;
    final total = paradas?.length ?? viagem.qtdParadas ?? 0;
    final feitas = paradas?.where((p) => p.concluida).length ?? viagem.qtdParadasOk ?? 0;
    final rota = [viagem.origemLabel, viagem.destinoLabel].where((e) => e != null && e.isNotEmpty).join(' → ');
    final saida = viagem.saidaReal ?? viagem.saidaPrevista;
    return Container(
      padding: const EdgeInsets.all(18),
      decoration: BoxDecoration(gradient: AppTheme.gradienteMarca, borderRadius: BorderRadius.circular(AppRadius.lg)),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Expanded(
            child: Text(
              (viagem.titulo != null && viagem.titulo!.isNotEmpty) ? viagem.titulo! : viagem.codigo,
              style: const TextStyle(color: Colors.white, fontSize: 18, fontWeight: FontWeight.w800),
            ),
          ),
          const SizedBox(width: 8),
          StatusPill(status: status, sobreEscuro: true),
        ]),
        if (rota.isNotEmpty) ...[
          const SizedBox(height: 8),
          InfoChip(icone: Icons.alt_route_rounded, texto: rota, sobreEscuro: true),
        ],
        const SizedBox(height: 12),
        Wrap(spacing: 16, runSpacing: 8, children: [
          if (viagem.veiculoLabel != null) InfoChip(icone: Icons.local_shipping_outlined, texto: viagem.veiculoLabel!, sobreEscuro: true),
          if (saida != null)
            InfoChip(
              icone: Icons.schedule_rounded,
              texto: '${viagem.saidaReal != null ? 'Saiu' : 'Saída'} ${_fmtDataHora.format(saida)}',
              sobreEscuro: true,
            ),
          if (kmInicial != null) InfoChip(icone: Icons.speed_rounded, texto: 'KM $kmInicial', sobreEscuro: true),
          if (viagem.kmPercorrido != null)
            InfoChip(icone: Icons.route_rounded, texto: '${viagem.kmPercorrido!.toStringAsFixed(0)} km', sobreEscuro: true),
        ]),
        if (total > 0) ...[
          const SizedBox(height: 16),
          BarraProgresso(feitas: feitas, total: total, sobreEscuro: true),
        ],
        if (viagem.observacao != null && viagem.observacao!.isNotEmpty) ...[
          const SizedBox(height: 12),
          Text(viagem.observacao!, style: const TextStyle(color: Colors.white70, fontSize: 12.5, fontStyle: FontStyle.italic)),
        ],
      ]),
    );
  }
}

class _CardRastreamento extends StatelessWidget {
  final String status;
  final bool rastreando;
  final int pontosEnviados;
  final int pontosPendentes;
  final String? ultimaPosicaoTexto;
  final VoidCallback onRetomar;

  const _CardRastreamento({
    required this.status,
    required this.rastreando,
    required this.pontosEnviados,
    required this.pontosPendentes,
    required this.ultimaPosicaoTexto,
    required this.onRetomar,
  });

  @override
  Widget build(BuildContext context) {
    final cor = rastreando ? AppColors.accent500 : AppColors.textSoft;
    final detalhes = [
      '$pontosEnviados ponto${pontosEnviados == 1 ? '' : 's'} enviado${pontosEnviados == 1 ? '' : 's'}',
      if (pontosPendentes > 0) '$pontosPendentes aguardando sinal',
      ?ultimaPosicaoTexto,
    ].join(' · ');
    return Container(
      padding: const EdgeInsets.fromLTRB(14, 12, 10, 12),
      decoration: BoxDecoration(
        color: rastreando ? AppColors.accent500.withValues(alpha: 0.08) : AppColors.surface,
        borderRadius: BorderRadius.circular(AppRadius.md),
        border: Border.all(color: rastreando ? AppColors.accent400.withValues(alpha: 0.5) : AppColors.border),
      ),
      child: Row(children: [
        Container(
          width: 38,
          height: 38,
          decoration: BoxDecoration(color: cor.withValues(alpha: 0.14), shape: BoxShape.circle),
          child: Icon(rastreando ? Icons.gps_fixed_rounded : Icons.gps_off_rounded, color: cor, size: 20),
        ),
        const SizedBox(width: 12),
        Expanded(
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text(
              rastreando ? 'Enviando sua localização' : 'Localização desligada',
              style: const TextStyle(fontWeight: FontWeight.w800, color: AppColors.text),
            ),
            Text(
              rastreando ? detalhes : 'A logística não vê onde você está.',
              style: const TextStyle(fontSize: 12, color: AppColors.textMuted),
            ),
          ]),
        ),
        if (!rastreando && status == 'EmAndamento')
          TextButton(onPressed: onRetomar, child: const Text('Ligar')),
      ]),
    );
  }
}

class _BarraAcao extends StatelessWidget {
  final String status;
  final bool ocupado;
  final VoidCallback onIniciar;
  final VoidCallback onConcluir;
  const _BarraAcao({required this.status, required this.ocupado, required this.onIniciar, required this.onConcluir});

  @override
  Widget build(BuildContext context) {
    final iniciar = status == 'Planejada';
    return Container(
      decoration: BoxDecoration(
        color: AppColors.surface,
        boxShadow: [BoxShadow(color: AppColors.brand900.withValues(alpha: 0.08), blurRadius: 16, offset: const Offset(0, -4))],
      ),
      padding: const EdgeInsets.fromLTRB(16, 12, 16, 12),
      child: SafeArea(
        top: false,
        child: FilledButton.icon(
          onPressed: ocupado ? null : (iniciar ? onIniciar : onConcluir),
          style: FilledButton.styleFrom(
            minimumSize: const Size.fromHeight(56),
            backgroundColor: iniciar ? AppColors.primary600 : AppColors.accent500,
            textStyle: const TextStyle(fontSize: 16, fontWeight: FontWeight.w800),
          ),
          icon: ocupado
              ? const SizedBox(width: 20, height: 20, child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white))
              : Icon(iniciar ? Icons.play_arrow_rounded : Icons.flag_rounded),
          label: Text(iniciar ? 'Iniciar viagem' : 'Concluir viagem'),
        ),
      ),
    );
  }
}

class _ParadasSecao extends ConsumerWidget {
  final int vid;
  final String token;
  final bool somenteLeitura;
  const _ParadasSecao({required this.vid, required this.token, this.somenteLeitura = false});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final args = (vid: vid, token: token);
    final paradasAsync = ref.watch(paradasProvider(args));
    return paradasAsync.when(
      loading: () => const Padding(padding: EdgeInsets.only(top: 40), child: Center(child: CircularProgressIndicator())),
      error: (e, _) => Padding(
        padding: const EdgeInsets.only(top: 30),
        child: Text('Erro ao carregar paradas: $e', textAlign: TextAlign.center, style: const TextStyle(color: AppColors.textMuted)),
      ),
      data: (paradas) {
        if (paradas.isEmpty) {
          return const Padding(
            padding: EdgeInsets.only(top: 40),
            child: Center(child: Text('Nenhuma parada planejada.', style: TextStyle(color: AppColors.textMuted))),
          );
        }
        final pendentes = paradas.where((p) => !p.concluida).toList();
        final concluidas = paradas.where((p) => p.concluida).toList();
        return Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
          if (pendentes.isNotEmpty) ...[
            SecaoTitulo(
              somenteLeitura ? 'Pendentes' : 'Paradas',
              trailing: somenteLeitura || pendentes.length < 2
                  ? null
                  : const Text('segure e arraste para reordenar',
                      style: TextStyle(fontSize: 11, color: AppColors.textSoft)),
            ),
            if (somenteLeitura)
              ...pendentes.map((p) => _ParadaCard(key: ValueKey(p.id), parada: p, vid: vid, token: token, somenteLeitura: true))
            else
              ReorderableListView.builder(
                shrinkWrap: true,
                physics: const NeverScrollableScrollPhysics(),
                buildDefaultDragHandles: false,
                itemCount: pendentes.length,
                itemBuilder: (ctx, i) => ReorderableDelayedDragStartListener(
                  key: ValueKey(pendentes[i].id),
                  index: i,
                  child: _ParadaCard(parada: pendentes[i], vid: vid, token: token, destaque: i == 0),
                ),
                onReorderItem: (oldIndex, newIndex) async {
                  final novaLista = List<Parada>.from(pendentes);
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
            SecaoTitulo('Concluídas (${concluidas.length})'),
            Card(
              child: Column(children: [
                for (var i = 0; i < concluidas.length; i++) ...[
                  if (i > 0) const Divider(height: 1, indent: 16, endIndent: 16),
                  _ParadaConcluidaTile(parada: concluidas[i]),
                ],
              ]),
            ),
          ],
        ]);
      },
    );
  }
}

class _ParadaConcluidaTile extends StatelessWidget {
  final Parada parada;
  const _ParadaConcluidaTile({required this.parada});

  @override
  Widget build(BuildContext context) {
    final falhou = parada.status == 'Nao_realizada';
    return ListTile(
      dense: true,
      leading: Icon(
        falhou ? Icons.report_problem_rounded : Icons.check_circle_rounded,
        color: falhou ? AppColors.warning500 : AppColors.accent500,
      ),
      title: Text(
        '${parada.sequencia}. ${parada.parceiroNome ?? _labelTipo[parada.tipo] ?? parada.tipo}',
        style: const TextStyle(fontWeight: FontWeight.w700, color: AppColors.text),
      ),
      subtitle: Text(
        '${_labelTipo[parada.tipo] ?? parada.tipo} · ${AppTheme.labelStatus(parada.status)}',
        style: const TextStyle(fontSize: 12, color: AppColors.textMuted),
      ),
    );
  }
}

class _ParadaCard extends ConsumerStatefulWidget {
  final Parada parada;
  final int vid;
  final String token;
  final bool somenteLeitura;
  final bool destaque;
  const _ParadaCard({
    super.key,
    required this.parada,
    required this.vid,
    required this.token,
    this.somenteLeitura = false,
    this.destaque = false,
  });

  @override
  ConsumerState<_ParadaCard> createState() => _ParadaCardState();
}

class _ParadaCardState extends ConsumerState<_ParadaCard> {
  bool _carregando = false;

  static const _resultadosNaoRealizada = {'Recusado', 'AusenciaRecebedor', 'NaoRealizada'};
  static const _resultados = {
    'Entregue': 'Entregue',
    'Coletado': 'Coletado',
    'Outros': 'Outros',
    'NaoRealizada': 'Não realizada',
    'Recusado': 'Recusado',
    'AusenciaRecebedor': 'Ausência do recebedor',
  };
  static const _motivosNaoFeita = {
    'NaoRealizada': 'Não realizada',
    'Recusado': 'Recusado pelo destinatário',
    'AusenciaRecebedor': 'Ausência do recebedor',
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

  void _recarregar() => ref.invalidate(paradasProvider((vid: widget.vid, token: widget.token)));

  void _erro(String msg) {
    if (mounted) {
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(msg), backgroundColor: AppColors.danger500));
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
      _recarregar();
    } on ApiException catch (e) {
      _erro(e.message);
    } finally {
      if (mounted) setState(() => _carregando = false);
    }
  }

  Future<void> _enviarConclusao(String resultado, String observacao, File? foto) async {
    setState(() => _carregando = true);
    try {
      final pos = await _pos();
      final repo = ref.read(motoristaRepositoryProvider);
      await repo.concluirParada(
        vid: widget.vid,
        token: widget.token,
        pid: widget.parada.id,
        resultado: resultado,
        observacao: observacao,
        latitude: pos['latitude'],
        longitude: pos['longitude'],
        foto: foto,
      );
      _recarregar();
    } on ApiException catch (e) {
      _erro(e.message);
    } finally {
      if (mounted) setState(() => _carregando = false);
    }
  }

  Future<void> _abrirConcluir() async {
    String resultado = widget.parada.tipo == 'COLETA' ? 'Coletado' : 'Entregue';
    final obsController = TextEditingController();
    File? foto;
    String? erroFoto;
    String? erroObs;

    final confirmou = await showModalBottomSheet<bool>(
      context: context,
      isScrollControlled: true,
      builder: (ctx) => StatefulBuilder(
        builder: (ctx, setSheet) {
          final exigeFoto = widget.parada.tipo == 'ENTREGA' && !_resultadosNaoRealizada.contains(resultado);
          final exigeJustificativa = _resultadosNaoRealizada.contains(resultado);

          Future<void> escolherFoto(ImageSource origem) async {
            final arquivo = await ImagePicker().pickImage(source: origem, imageQuality: 80, maxWidth: 1600);
            if (arquivo != null) {
              setSheet(() {
                foto = File(arquivo.path);
                erroFoto = null;
              });
            }
          }

          return Padding(
            padding: EdgeInsets.fromLTRB(20, 0, 20, MediaQuery.of(ctx).viewInsets.bottom + 20),
            child: SingleChildScrollView(
              child: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.stretch, children: [
                const Text('Concluir parada', style: TextStyle(fontSize: 18, fontWeight: FontWeight.w800, color: AppColors.text)),
                Text(widget.parada.parceiroNome ?? '', style: const TextStyle(color: AppColors.textMuted)),
                const SizedBox(height: 16),
                const Text('Resultado', style: TextStyle(fontSize: 12.5, fontWeight: FontWeight.w800, color: AppColors.text)),
                const SizedBox(height: 8),
                Wrap(spacing: 8, runSpacing: 8, children: [
                  for (final r in _resultados.entries)
                    ChoiceChip(
                      label: Text(r.value),
                      selected: resultado == r.key,
                      onSelected: (_) => setSheet(() {
                        resultado = r.key;
                        erroObs = null;
                      }),
                    ),
                ]),
                if (exigeFoto) ...[
                  const SizedBox(height: 18),
                  const Text('Foto do canhoto *', style: TextStyle(fontSize: 12.5, fontWeight: FontWeight.w800, color: AppColors.text)),
                  const SizedBox(height: 8),
                  if (foto != null) ...[
                    ClipRRect(
                      borderRadius: BorderRadius.circular(AppRadius.sm),
                      child: Image.file(foto!, height: 160, width: double.infinity, fit: BoxFit.cover),
                    ),
                    const SizedBox(height: 8),
                  ],
                  Row(children: [
                    Expanded(
                      child: OutlinedButton.icon(
                        onPressed: () => escolherFoto(ImageSource.camera),
                        icon: const Icon(Icons.photo_camera_outlined),
                        label: Text(foto == null ? 'Tirar foto' : 'Tirar outra'),
                      ),
                    ),
                    const SizedBox(width: 8),
                    Expanded(
                      child: OutlinedButton.icon(
                        onPressed: () => escolherFoto(ImageSource.gallery),
                        icon: const Icon(Icons.photo_library_outlined),
                        label: const Text('Galeria'),
                      ),
                    ),
                  ]),
                  if (erroFoto != null) ...[
                    const SizedBox(height: 6),
                    Text(erroFoto!, style: const TextStyle(color: AppColors.danger500, fontSize: 12.5)),
                  ],
                ],
                const SizedBox(height: 18),
                Text(
                  exigeJustificativa ? 'Justificativa *' : 'Observação (opcional)',
                  style: const TextStyle(fontSize: 12.5, fontWeight: FontWeight.w800, color: AppColors.text),
                ),
                const SizedBox(height: 8),
                TextField(
                  controller: obsController,
                  maxLines: 3,
                  onChanged: (_) {
                    if (erroObs != null) setSheet(() => erroObs = null);
                  },
                  decoration: InputDecoration(errorText: erroObs),
                ),
                const SizedBox(height: 18),
                FilledButton(
                  onPressed: () {
                    if (exigeFoto && foto == null) {
                      setSheet(() => erroFoto = 'Anexe a foto do canhoto para concluir a entrega.');
                      return;
                    }
                    if (exigeJustificativa && obsController.text.trim().isEmpty) {
                      setSheet(() => erroObs = 'Informe o motivo.');
                      return;
                    }
                    Navigator.pop(ctx, true);
                  },
                  child: const Text('Confirmar'),
                ),
              ]),
            ),
          );
        },
      ),
    );
    if (confirmou != true) return;
    await _enviarConclusao(resultado, obsController.text.trim(), foto);
  }

  Future<void> _abrirNaoRealizada() async {
    String resultado = 'NaoRealizada';
    final obsController = TextEditingController();
    String? erroObs;

    final confirmou = await showModalBottomSheet<bool>(
      context: context,
      isScrollControlled: true,
      builder: (ctx) => StatefulBuilder(
        builder: (ctx, setSheet) => Padding(
          padding: EdgeInsets.fromLTRB(20, 0, 20, MediaQuery.of(ctx).viewInsets.bottom + 20),
          child: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.stretch, children: [
            const Text('Não foi possível fazer', style: TextStyle(fontSize: 18, fontWeight: FontWeight.w800, color: AppColors.text)),
            Text(widget.parada.parceiroNome ?? '', style: const TextStyle(color: AppColors.textMuted)),
            const SizedBox(height: 16),
            const Text('Motivo', style: TextStyle(fontSize: 12.5, fontWeight: FontWeight.w800, color: AppColors.text)),
            const SizedBox(height: 8),
            Wrap(spacing: 8, runSpacing: 8, children: [
              for (final m in _motivosNaoFeita.entries)
                ChoiceChip(
                  label: Text(m.value),
                  selected: resultado == m.key,
                  onSelected: (_) => setSheet(() => resultado = m.key),
                ),
            ]),
            const SizedBox(height: 18),
            const Text('Justificativa *', style: TextStyle(fontSize: 12.5, fontWeight: FontWeight.w800, color: AppColors.text)),
            const SizedBox(height: 8),
            TextField(
              controller: obsController,
              maxLines: 4,
              textInputAction: TextInputAction.newline,
              onChanged: (_) {
                if (erroObs != null) setSheet(() => erroObs = null);
              },
              decoration: InputDecoration(
                hintText: 'Ex: cliente ausente, carga recusada, endereço fechado...',
                errorText: erroObs,
              ),
            ),
            const SizedBox(height: 18),
            FilledButton(
              style: FilledButton.styleFrom(backgroundColor: AppColors.danger500),
              onPressed: () {
                if (obsController.text.trim().isEmpty) {
                  setSheet(() => erroObs = 'Informe o motivo.');
                  return;
                }
                Navigator.pop(ctx, true);
              },
              child: const Text('Registrar'),
            ),
          ]),
        ),
      ),
    );
    if (confirmou != true) return;
    await _enviarConclusao(resultado, obsController.text.trim(), null);
  }

  @override
  Widget build(BuildContext context) {
    final p = widget.parada;
    final corTipo = _corTipo(p.tipo);
    final endereco = [
      if (p.endereco != null && p.endereco!.isNotEmpty) p.endereco!,
      if (p.cidade != null) '${p.cidade}${p.uf != null ? '/${p.uf}' : ''}',
    ].join(' · ');
    final podeAgir = !p.concluida && !widget.somenteLeitura;
    return Card(
      margin: const EdgeInsets.only(bottom: 10),
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(AppRadius.md),
        side: BorderSide(
          color: widget.destaque ? AppColors.primary600.withValues(alpha: 0.55) : AppColors.border,
          width: widget.destaque ? 1.5 : 1,
        ),
      ),
      child: Padding(
        padding: const EdgeInsets.all(14),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Container(
              width: 36,
              height: 36,
              alignment: Alignment.center,
              decoration: BoxDecoration(
                color: widget.destaque ? AppColors.primary600 : corTipo.withValues(alpha: 0.12),
                shape: BoxShape.circle,
              ),
              child: Text(
                '${p.sequencia}',
                style: TextStyle(
                  fontWeight: FontWeight.w800,
                  color: widget.destaque ? Colors.white : corTipo,
                ),
              ),
            ),
            const SizedBox(width: 12),
            Expanded(
              child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                Wrap(spacing: 6, runSpacing: 4, children: [
                  _Etiqueta(texto: _labelTipo[p.tipo] ?? p.tipo, cor: corTipo),
                  if (widget.destaque) const _Etiqueta(texto: 'Próxima', cor: AppColors.primary600),
                  if (p.noLocal) const _Etiqueta(texto: 'No local', cor: AppColors.accent500),
                ]),
                const SizedBox(height: 6),
                Text(
                  p.parceiroNome ?? 'Parada ${p.sequencia}',
                  style: TextStyle(fontWeight: FontWeight.w800, fontSize: widget.destaque ? 16.5 : 15, color: AppColors.text),
                ),
                if (endereco.isNotEmpty) ...[
                  const SizedBox(height: 3),
                  Text(endereco, style: const TextStyle(color: AppColors.textMuted, fontSize: 13)),
                ],
              ]),
            ),
            if (_carregando)
              const Padding(
                padding: EdgeInsets.only(left: 8),
                child: SizedBox(width: 20, height: 20, child: CircularProgressIndicator(strokeWidth: 2)),
              ),
          ]),
          if (podeAgir) ...[
            const SizedBox(height: 14),
            if (widget.destaque) ...[
              FilledButton.icon(
                onPressed: _carregando ? null : (p.noLocal ? _abrirConcluir : _marcarChegada),
                style: FilledButton.styleFrom(
                  backgroundColor: p.noLocal ? AppColors.accent500 : AppColors.primary600,
                  minimumSize: const Size.fromHeight(50),
                ),
                icon: Icon(p.noLocal ? Icons.task_alt_rounded : Icons.location_on_rounded),
                label: Text(p.noLocal ? 'Concluir parada' : 'Cheguei no local'),
              ),
              const SizedBox(height: 4),
              Center(
                child: TextButton(
                  onPressed: _carregando ? null : _abrirNaoRealizada,
                  style: TextButton.styleFrom(foregroundColor: AppColors.danger500),
                  child: const Text('Não foi possível fazer'),
                ),
              ),
            ] else
              Row(children: [
                Expanded(
                  child: OutlinedButton.icon(
                    onPressed: _carregando ? null : (p.noLocal ? _abrirConcluir : _marcarChegada),
                    icon: Icon(p.noLocal ? Icons.task_alt_rounded : Icons.location_on_outlined, size: 18),
                    label: Text(p.noLocal ? 'Concluir' : 'Cheguei'),
                    style: OutlinedButton.styleFrom(minimumSize: const Size.fromHeight(42)),
                  ),
                ),
                const SizedBox(width: 8),
                Expanded(
                  child: OutlinedButton(
                    onPressed: _carregando ? null : _abrirNaoRealizada,
                    style: OutlinedButton.styleFrom(
                      foregroundColor: AppColors.danger500,
                      side: BorderSide(color: AppColors.danger500.withValues(alpha: 0.5)),
                      minimumSize: const Size.fromHeight(42),
                    ),
                    child: const Text('Não feita'),
                  ),
                ),
              ]),
          ],
        ]),
      ),
    );
  }
}

class _Etiqueta extends StatelessWidget {
  final String texto;
  final Color cor;
  const _Etiqueta({required this.texto, required this.cor});

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
      decoration: BoxDecoration(color: cor.withValues(alpha: 0.12), borderRadius: BorderRadius.circular(6)),
      child: Text(texto.toUpperCase(),
          style: TextStyle(color: cor, fontSize: 10.5, fontWeight: FontWeight.w800, letterSpacing: 0.6)),
    );
  }
}
