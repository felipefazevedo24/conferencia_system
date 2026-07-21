import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:intl/intl.dart';

import '../../core/theme.dart';
import '../../domain/entities/models.dart';
import '../providers/providers.dart';
import 'viagem_detail_screen.dart';

final _fmtData = DateFormat('dd/MM HH:mm');

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
    if (state == AppLifecycleState.resumed) _atualizar();
  }

  void _atualizar() {
    if (mounted) ref.invalidate(viagensProvider);
  }

  @override
  Widget build(BuildContext context) {
    final viagensAsync = ref.watch(viagensProvider);
    return Scaffold(
      appBar: AppBar(
        title: const Text('Minhas viagens'),
        actions: [
          IconButton(
            tooltip: 'Trocar motorista',
            icon: const Icon(Icons.logout),
            onPressed: () => _confirmarTrocarMotorista(context, ref),
          ),
        ],
      ),
      body: SafeArea(
        child: RefreshIndicator(
          onRefresh: () async => ref.invalidate(viagensProvider),
          child: viagensAsync.when(
            loading: () => const Center(child: CircularProgressIndicator()),
            error: (e, _) => _ErroCarregar(mensagem: e.toString(), onTentarNovamente: () => ref.invalidate(viagensProvider)),
            data: (viagens) => _ListaViagens(ativas: viagens.ativas, historico: viagens.historico),
          ),
        ),
      ),
    );
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

class _ErroCarregar extends StatelessWidget {
  final String mensagem;
  final VoidCallback onTentarNovamente;
  const _ErroCarregar({required this.mensagem, required this.onTentarNovamente});

  @override
  Widget build(BuildContext context) {
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(24),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            const Icon(Icons.wifi_off, size: 48, color: Colors.black38),
            const SizedBox(height: 12),
            Text(mensagem, textAlign: TextAlign.center),
            const SizedBox(height: 12),
            FilledButton(onPressed: onTentarNovamente, child: const Text('Tentar novamente')),
          ],
        ),
      ),
    );
  }
}

class _ListaViagens extends StatelessWidget {
  final List<Viagem> ativas;
  final List<Viagem> historico;
  const _ListaViagens({required this.ativas, required this.historico});

  @override
  Widget build(BuildContext context) {
    if (ativas.isEmpty && historico.isEmpty) {
      return ListView(
        physics: const AlwaysScrollableScrollPhysics(),
        children: const [
          SizedBox(height: 120),
          Icon(Icons.route, size: 48, color: AppColors.textSoft),
          SizedBox(height: 12),
          Center(child: Text('Nenhuma viagem por enquanto.', style: TextStyle(color: AppColors.textMuted))),
        ],
      );
    }
    return ListView(
      physics: const AlwaysScrollableScrollPhysics(),
      padding: const EdgeInsets.all(14),
      children: [
        if (ativas.isNotEmpty) ...[
          const _SecaoTitulo('Ativas'),
          ...ativas.map((v) => _ViagemCard(viagem: v)),
        ],
        if (historico.isNotEmpty) ...[
          const _SecaoTitulo('Histórico'),
          ...historico.map((v) => _ViagemCard(viagem: v)),
        ],
      ],
    );
  }
}

class _SecaoTitulo extends StatelessWidget {
  final String texto;
  const _SecaoTitulo(this.texto);

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.fromLTRB(4, 18, 4, 10),
      child: Text(
        texto.toUpperCase(),
        style: const TextStyle(fontWeight: FontWeight.w800, fontSize: 12, letterSpacing: 1, color: AppColors.textSoft),
      ),
    );
  }
}

class _ViagemCard extends StatelessWidget {
  final Viagem viagem;
  const _ViagemCard({required this.viagem});

  @override
  Widget build(BuildContext context) {
    final cor = AppTheme.corStatusViagem(viagem.status);
    final rota = [viagem.origemLabel, viagem.destinoLabel].where((e) => e != null && e.isNotEmpty).join(' → ');
    final quando = viagem.saidaReal ?? viagem.saidaPrevista;
    return Card(
      margin: const EdgeInsets.only(bottom: 10),
      child: InkWell(
        borderRadius: BorderRadius.circular(AppRadius.md),
        onTap: () => Navigator.of(context).push(MaterialPageRoute(builder: (_) => ViagemDetailScreen(viagem: viagem))),
        child: Padding(
          padding: const EdgeInsets.all(14),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                children: [
                  Expanded(
                    child: Text(viagem.codigo,
                        style: const TextStyle(fontWeight: FontWeight.w800, fontSize: 15, color: AppColors.text)),
                  ),
                  Container(
                    padding: const EdgeInsets.symmetric(horizontal: 9, vertical: 4),
                    decoration: BoxDecoration(color: cor.withValues(alpha: 0.14), borderRadius: BorderRadius.circular(999)),
                    child: Text(viagem.status,
                        style: TextStyle(color: cor, fontSize: 10.5, fontWeight: FontWeight.w800)),
                  ),
                ],
              ),
              if (viagem.titulo != null && viagem.titulo!.isNotEmpty) ...[
                const SizedBox(height: 4),
                Text(viagem.titulo!, style: const TextStyle(color: AppColors.text, fontWeight: FontWeight.w600)),
              ],
              if (rota.isNotEmpty) ...[
                const SizedBox(height: 6),
                Row(children: [
                  const Icon(Icons.alt_route, size: 15, color: AppColors.textSoft),
                  const SizedBox(width: 6),
                  Expanded(child: Text(rota, style: const TextStyle(color: AppColors.textMuted, fontSize: 12.5))),
                ]),
              ],
              const SizedBox(height: 10),
              Wrap(
                spacing: 14,
                runSpacing: 6,
                children: [
                  if (viagem.veiculoLabel != null)
                    _InfoChip(icon: Icons.local_shipping_outlined, texto: viagem.veiculoLabel!),
                  if (quando != null)
                    _InfoChip(icon: Icons.schedule, texto: _fmtData.format(quando)),
                  if (viagem.qtdParadas != null && viagem.qtdParadas! > 0)
                    _InfoChip(icon: Icons.flag_outlined, texto: '${viagem.qtdParadasOk ?? 0}/${viagem.qtdParadas} paradas'),
                  if (viagem.kmPercorrido != null)
                    _InfoChip(icon: Icons.speed, texto: '${viagem.kmPercorrido!.toStringAsFixed(0)} km'),
                ],
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class _InfoChip extends StatelessWidget {
  final IconData icon;
  final String texto;
  const _InfoChip({required this.icon, required this.texto});

  @override
  Widget build(BuildContext context) {
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        Icon(icon, size: 14, color: AppColors.textSoft),
        const SizedBox(width: 4),
        Text(texto, style: const TextStyle(fontSize: 12, color: AppColors.textMuted, fontWeight: FontWeight.w600)),
      ],
    );
  }
}
