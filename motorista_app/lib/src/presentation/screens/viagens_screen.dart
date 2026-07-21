import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../domain/entities/models.dart';
import '../providers/providers.dart';
import 'viagem_detail_screen.dart';

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
      body: RefreshIndicator(
        onRefresh: () async => ref.invalidate(viagensProvider),
        child: viagensAsync.when(
          loading: () => const Center(child: CircularProgressIndicator()),
          error: (e, _) => _ErroCarregar(mensagem: e.toString(), onTentarNovamente: () => ref.invalidate(viagensProvider)),
          data: (viagens) => _ListaViagens(ativas: viagens.ativas, historico: viagens.historico),
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
          Icon(Icons.route, size: 48, color: Colors.black26),
          SizedBox(height: 12),
          Center(child: Text('Nenhuma viagem por enquanto.')),
        ],
      );
    }
    return ListView(
      physics: const AlwaysScrollableScrollPhysics(),
      padding: const EdgeInsets.all(12),
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
      padding: const EdgeInsets.fromLTRB(8, 16, 8, 8),
      child: Text(
        texto.toUpperCase(),
        style: const TextStyle(fontWeight: FontWeight.w800, fontSize: 12, letterSpacing: 1, color: Colors.black54),
      ),
    );
  }
}

class _ViagemCard extends StatelessWidget {
  final Viagem viagem;
  const _ViagemCard({required this.viagem});

  Color _corStatus(String status) {
    switch (status) {
      case 'EmAndamento':
        return const Color(0xFF0F8F5F);
      case 'Concluida':
        return Colors.black45;
      case 'Cancelada':
        return const Color(0xFFC0392B);
      default:
        return const Color(0xFFB7791F);
    }
  }

  @override
  Widget build(BuildContext context) {
    final podeAbrir = !viagem.finalizada;
    return Card(
      margin: const EdgeInsets.only(bottom: 10),
      child: ListTile(
        onTap: podeAbrir ? () => Navigator.of(context).push(MaterialPageRoute(builder: (_) => ViagemDetailScreen(viagem: viagem))) : null,
        title: Text(viagem.codigo, style: const TextStyle(fontWeight: FontWeight.bold)),
        subtitle: Text(viagem.titulo ?? viagem.veiculoLabel ?? ''),
        trailing: Chip(
          label: Text(viagem.status, style: const TextStyle(color: Colors.white, fontSize: 11)),
          backgroundColor: _corStatus(viagem.status),
          padding: EdgeInsets.zero,
        ),
      ),
    );
  }
}
