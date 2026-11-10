import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:intl/intl.dart';

import '../../core/theme.dart';
import '../../domain/entities/models.dart';
import '../providers/providers.dart';
import '../widgets/ui.dart';
import 'viagem_detail_screen.dart';

final _fmtData = DateFormat('dd/MM HH:mm');
final _fmtHora = DateFormat('HH:mm');
final _fmtDia = DateFormat("EEEE, d 'de' MMMM", 'pt_BR');

/// Atualiza a lista sozinha (a cada 20s e sempre que o app volta do segundo
/// plano), igual ao polling que a versão web já fazia — sem isso, uma
/// viagem nova só apareceria depois de fechar e reabrir o app.
const _intervaloAtualizacao = Duration(seconds: 20);

class ViagensScreen extends ConsumerStatefulWidget {
  const ViagensScreen({super.key});

  @override
  ConsumerState<ViagensScreen> createState() => _ViagensScreenState();
}

class _ViagensScreenState extends ConsumerState<ViagensScreen> with WidgetsBindingObserver {
  Timer? _timer;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    _timer = Timer.periodic(_intervaloAtualizacao, (_) => _atualizar());
  }

  @override
  void dispose() {
    _timer?.cancel();
    WidgetsBinding.instance.removeObserver(this);
    super.dispose();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.resumed) {
      _atualizar();
      // Versão nova é conferida só ao voltar para o app, não no timer.
      ref.invalidate(atualizacaoProvider);
    }
  }

  void _atualizar() {
    if (mounted) ref.invalidate(viagensProvider);
  }

  @override
  Widget build(BuildContext context) {
    final viagensAsync = ref.watch(viagensProvider);
    final nome = ref.watch(configProvider).value?.motoristaNome;
    return Scaffold(
      body: RefreshIndicator(
        onRefresh: () async => ref.invalidate(viagensProvider),
        child: CustomScrollView(
          physics: const AlwaysScrollableScrollPhysics(),
          slivers: [
            SliverToBoxAdapter(child: _Cabecalho(nome: nome, onSair: () => _confirmarTrocarMotorista(context, ref))),
            SliverPadding(
              padding: const EdgeInsets.fromLTRB(16, 0, 16, 24),
              sliver: SliverList.list(children: [
                const AtualizacaoBanner(),
                ...viagensAsync.when(
                  loading: () => [const Padding(padding: EdgeInsets.only(top: 80), child: Center(child: CircularProgressIndicator()))],
                  error: (e, _) => [_ErroCarregar(mensagem: e.toString(), onTentarNovamente: () => ref.invalidate(viagensProvider))],
                  data: (viagens) => _conteudo(viagens.ativas, viagens.historico),
                ),
              ]),
            ),
          ],
        ),
      ),
    );
  }

  List<Widget> _conteudo(List<Viagem> ativas, List<Viagem> historico) {
    if (ativas.isEmpty && historico.isEmpty) return const [_Vazio()];
    final emAndamento = ativas.where((v) => v.status == 'EmAndamento').toList();
    final proximas = ativas.where((v) => v.status != 'EmAndamento').toList();
    return [
      if (emAndamento.isNotEmpty) ...[
        const SecaoTitulo('Agora'),
        ...emAndamento.map((v) => _ViagemDestaque(viagem: v)),
      ],
      if (proximas.isNotEmpty) ...[
        const SecaoTitulo('Próximas viagens'),
        ...proximas.map((v) => _ViagemCard(viagem: v)),
      ],
      if (ativas.isEmpty) ...[
        const SizedBox(height: 14),
        const _SemViagemAtiva(),
      ],
      if (historico.isNotEmpty) ...[
        const SecaoTitulo('Histórico'),
        Card(
          child: Column(children: [
            for (var i = 0; i < historico.length; i++) ...[
              if (i > 0) const Divider(height: 1, indent: 16, endIndent: 16),
              _HistoricoTile(viagem: historico[i]),
            ],
          ]),
        ),
      ],
    ];
  }

  Future<void> _confirmarTrocarMotorista(BuildContext context, WidgetRef ref) async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('Trocar motorista'),
        content: const Text('Isso desconecta este aparelho do motorista atual. Continuar?'),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, false), child: const Text('Cancelar')),
          TextButton(onPressed: () => Navigator.pop(ctx, true), child: const Text('Trocar')),
        ],
      ),
    );
    if (ok == true) {
      await ConfigStore.limpar();
      ref.invalidate(configProvider);
    }
  }
}

void _abrirViagem(BuildContext context, Viagem v) {
  Navigator.of(context).push(MaterialPageRoute(builder: (_) => ViagemDetailScreen(viagem: v)));
}

String _rota(Viagem v) =>
    [v.origemLabel, v.destinoLabel].where((e) => e != null && e.isNotEmpty).join(' → ');

class _Cabecalho extends StatelessWidget {
  final String? nome;
  final VoidCallback onSair;
  const _Cabecalho({required this.nome, required this.onSair});

  @override
  Widget build(BuildContext context) {
    final primeiroNome = (nome ?? '').trim().split(' ').first;
    String dia;
    try {
      dia = _fmtDia.format(DateTime.now());
    } catch (_) {
      dia = DateFormat('dd/MM/yyyy').format(DateTime.now());
    }
    return Container(
      decoration: const BoxDecoration(
        gradient: AppTheme.gradienteMarca,
        borderRadius: BorderRadius.vertical(bottom: Radius.circular(AppRadius.xl)),
      ),
      padding: EdgeInsets.fromLTRB(20, MediaQuery.of(context).padding.top + 14, 8, 26),
      child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Expanded(
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text(
              primeiroNome.isEmpty ? 'Olá!' : 'Olá, ${primeiroNome[0].toUpperCase()}${primeiroNome.substring(1).toLowerCase()}',
              style: const TextStyle(color: Colors.white, fontSize: 24, fontWeight: FontWeight.w800),
            ),
            const SizedBox(height: 4),
            Text(dia, style: const TextStyle(color: Colors.white70, fontSize: 13.5, fontWeight: FontWeight.w600)),
          ]),
        ),
        IconButton(
          tooltip: 'Trocar motorista',
          icon: const Icon(Icons.logout_rounded, color: Colors.white70),
          onPressed: onSair,
        ),
      ]),
    );
  }
}

/// Viagem em andamento: card grande e escuro, é o que o motorista mais abre.
class _ViagemDestaque extends StatelessWidget {
  final Viagem viagem;
  const _ViagemDestaque({required this.viagem});

  @override
  Widget build(BuildContext context) {
    final rota = _rota(viagem);
    return Container(
      margin: const EdgeInsets.only(bottom: 12),
      decoration: BoxDecoration(
        gradient: AppTheme.gradienteMarca,
        borderRadius: BorderRadius.circular(AppRadius.lg),
        boxShadow: [BoxShadow(color: AppColors.brand900.withValues(alpha: 0.25), blurRadius: 18, offset: const Offset(0, 8))],
      ),
      child: Material(
        color: Colors.transparent,
        child: InkWell(
          borderRadius: BorderRadius.circular(AppRadius.lg),
          onTap: () => _abrirViagem(context, viagem),
          child: Padding(
            padding: const EdgeInsets.all(18),
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Row(children: [
                Expanded(
                  child: Text(viagem.codigo,
                      style: const TextStyle(color: Colors.white, fontSize: 18, fontWeight: FontWeight.w800)),
                ),
                StatusPill(status: viagem.status, sobreEscuro: true),
              ]),
              if (viagem.titulo != null && viagem.titulo!.isNotEmpty) ...[
                const SizedBox(height: 4),
                Text(viagem.titulo!, style: const TextStyle(color: Colors.white70, fontWeight: FontWeight.w600)),
              ],
              if (rota.isNotEmpty) ...[
                const SizedBox(height: 10),
                InfoChip(icone: Icons.alt_route_rounded, texto: rota, sobreEscuro: true),
              ],
              if ((viagem.qtdParadas ?? 0) > 0) ...[
                const SizedBox(height: 16),
                BarraProgresso(feitas: viagem.qtdParadasOk ?? 0, total: viagem.qtdParadas!, sobreEscuro: true),
              ],
              const SizedBox(height: 16),
              Row(children: [
                if (viagem.veiculoLabel != null)
                  Expanded(child: InfoChip(icone: Icons.local_shipping_outlined, texto: viagem.veiculoLabel!, sobreEscuro: true)),
                const Text('Abrir', style: TextStyle(color: Colors.white, fontWeight: FontWeight.w800)),
                const Icon(Icons.chevron_right_rounded, color: Colors.white),
              ]),
            ]),
          ),
        ),
      ),
    );
  }
}

class _ViagemCard extends StatelessWidget {
  final Viagem viagem;
  const _ViagemCard({required this.viagem});

  @override
  Widget build(BuildContext context) {
    final rota = _rota(viagem);
    final quando = viagem.saidaReal ?? viagem.saidaPrevista;
    return Card(
      margin: const EdgeInsets.only(bottom: 10),
      child: InkWell(
        borderRadius: BorderRadius.circular(AppRadius.md),
        onTap: () => _abrirViagem(context, viagem),
        child: Padding(
          padding: const EdgeInsets.all(14),
          child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
            // Horário de saída em destaque: é o que o motorista procura.
            Container(
              width: 62,
              padding: const EdgeInsets.symmetric(vertical: 10),
              decoration: BoxDecoration(color: AppColors.surfaceMuted, borderRadius: BorderRadius.circular(AppRadius.sm)),
              child: Column(children: [
                Text(quando != null ? _fmtHora.format(quando) : '--:--',
                    style: const TextStyle(fontSize: 16, fontWeight: FontWeight.w800, color: AppColors.text)),
                Text(quando != null ? DateFormat('dd/MM').format(quando) : '',
                    style: const TextStyle(fontSize: 11, color: AppColors.textMuted, fontWeight: FontWeight.w600)),
              ]),
            ),
            const SizedBox(width: 12),
            Expanded(
              child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                Row(children: [
                  Expanded(
                    child: Text(viagem.codigo,
                        style: const TextStyle(fontWeight: FontWeight.w800, fontSize: 15, color: AppColors.text)),
                  ),
                  StatusPill(status: viagem.status),
                ]),
                if (viagem.titulo != null && viagem.titulo!.isNotEmpty) ...[
                  const SizedBox(height: 3),
                  Text(viagem.titulo!, style: const TextStyle(color: AppColors.text, fontWeight: FontWeight.w600)),
                ],
                if (rota.isNotEmpty) ...[
                  const SizedBox(height: 6),
                  InfoChip(icone: Icons.alt_route_rounded, texto: rota),
                ],
                const SizedBox(height: 8),
                Wrap(spacing: 14, runSpacing: 6, children: [
                  if (viagem.veiculoLabel != null) InfoChip(icone: Icons.local_shipping_outlined, texto: viagem.veiculoLabel!),
                  if ((viagem.qtdParadas ?? 0) > 0) InfoChip(icone: Icons.flag_outlined, texto: '${viagem.qtdParadas} paradas'),
                ]),
              ]),
            ),
          ]),
        ),
      ),
    );
  }
}

class _HistoricoTile extends StatelessWidget {
  final Viagem viagem;
  const _HistoricoTile({required this.viagem});

  @override
  Widget build(BuildContext context) {
    final quando = viagem.retornoReal ?? viagem.saidaReal ?? viagem.saidaPrevista;
    final detalhes = [
      if (quando != null) _fmtData.format(quando),
      if (viagem.kmPercorrido != null) '${viagem.kmPercorrido!.toStringAsFixed(0)} km',
      if ((viagem.qtdParadas ?? 0) > 0) '${viagem.qtdParadasOk ?? 0}/${viagem.qtdParadas} paradas',
    ].join(' · ');
    return ListTile(
      onTap: () => _abrirViagem(context, viagem),
      leading: Icon(
        viagem.status == 'Cancelada' ? Icons.cancel_outlined : Icons.check_circle_outline_rounded,
        color: AppTheme.corStatusViagem(viagem.status),
      ),
      title: Text(viagem.codigo, style: const TextStyle(fontWeight: FontWeight.w700, color: AppColors.text)),
      subtitle: Text(detalhes, style: const TextStyle(fontSize: 12, color: AppColors.textMuted)),
      trailing: const Icon(Icons.chevron_right_rounded, color: AppColors.textSoft),
    );
  }
}

class _SemViagemAtiva extends StatelessWidget {
  const _SemViagemAtiva();

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.all(18),
      decoration: BoxDecoration(
        color: AppColors.surface,
        borderRadius: BorderRadius.circular(AppRadius.md),
        border: Border.all(color: AppColors.border),
      ),
      child: const Row(children: [
        Icon(Icons.event_available_rounded, color: AppColors.accent500, size: 30),
        SizedBox(width: 12),
        Expanded(
          child: Text('Nenhuma viagem liberada para você agora.',
              style: TextStyle(color: AppColors.textMuted, fontWeight: FontWeight.w600)),
        ),
      ]),
    );
  }
}

class _Vazio extends StatelessWidget {
  const _Vazio();

  @override
  Widget build(BuildContext context) {
    return const Padding(
      padding: EdgeInsets.only(top: 90),
      child: Column(children: [
        Icon(Icons.route_rounded, size: 56, color: AppColors.textSoft),
        SizedBox(height: 12),
        Text('Nenhuma viagem por enquanto.',
            style: TextStyle(color: AppColors.textMuted, fontSize: 15, fontWeight: FontWeight.w600)),
        SizedBox(height: 4),
        Text('Puxe a tela para baixo para atualizar.', style: TextStyle(color: AppColors.textSoft, fontSize: 12.5)),
      ]),
    );
  }
}

class _ErroCarregar extends StatelessWidget {
  final String mensagem;
  final VoidCallback onTentarNovamente;
  const _ErroCarregar({required this.mensagem, required this.onTentarNovamente});

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(top: 70),
      child: Column(children: [
        const Icon(Icons.wifi_off_rounded, size: 52, color: AppColors.textSoft),
        const SizedBox(height: 12),
        Text(mensagem, textAlign: TextAlign.center, style: const TextStyle(color: AppColors.textMuted)),
        const SizedBox(height: 16),
        FilledButton(onPressed: onTentarNovamente, child: const Text('Tentar novamente')),
      ]),
    );
  }
}
