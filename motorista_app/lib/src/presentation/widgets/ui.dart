import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/services/atualizacao_service.dart';
import '../../core/theme.dart';
import '../../data/remote/api_client.dart';
import '../providers/providers.dart';

class StatusPill extends StatelessWidget {
  final String status;
  final bool sobreEscuro;
  const StatusPill({super.key, required this.status, this.sobreEscuro = false});

  @override
  Widget build(BuildContext context) {
    final cor = AppTheme.corStatusViagem(status);
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 5),
      decoration: BoxDecoration(
        color: sobreEscuro ? Colors.white.withValues(alpha: 0.14) : cor.withValues(alpha: 0.13),
        borderRadius: BorderRadius.circular(999),
      ),
      child: Row(mainAxisSize: MainAxisSize.min, children: [
        Container(width: 7, height: 7, decoration: BoxDecoration(color: cor, shape: BoxShape.circle)),
        const SizedBox(width: 6),
        Text(
          AppTheme.labelStatus(status),
          style: TextStyle(color: sobreEscuro ? Colors.white : cor, fontSize: 11.5, fontWeight: FontWeight.w800),
        ),
      ]),
    );
  }
}

class InfoChip extends StatelessWidget {
  final IconData icone;
  final String texto;
  final bool sobreEscuro;
  const InfoChip({super.key, required this.icone, required this.texto, this.sobreEscuro = false});

  @override
  Widget build(BuildContext context) {
    final cor = sobreEscuro ? Colors.white70 : AppColors.textMuted;
    return Row(mainAxisSize: MainAxisSize.min, children: [
      Icon(icone, size: 15, color: cor),
      const SizedBox(width: 5),
      Text(texto, style: TextStyle(fontSize: 12.5, color: cor, fontWeight: FontWeight.w600)),
    ]);
  }
}

class SecaoTitulo extends StatelessWidget {
  final String texto;
  final Widget? trailing;
  const SecaoTitulo(this.texto, {super.key, this.trailing});

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.fromLTRB(4, 20, 4, 10),
      child: Row(children: [
        Expanded(
          child: Text(
            texto.toUpperCase(),
            style: const TextStyle(fontWeight: FontWeight.w800, fontSize: 11.5, letterSpacing: 1.1, color: AppColors.textSoft),
          ),
        ),
        ?trailing,
      ]),
    );
  }
}

class BarraProgresso extends StatelessWidget {
  final int feitas;
  final int total;
  final bool sobreEscuro;
  const BarraProgresso({super.key, required this.feitas, required this.total, this.sobreEscuro = false});

  @override
  Widget build(BuildContext context) {
    final fracao = total > 0 ? (feitas / total).clamp(0.0, 1.0) : 0.0;
    return Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      Row(children: [
        Text(
          '$feitas de $total paradas',
          style: TextStyle(
            fontSize: 12.5,
            fontWeight: FontWeight.w700,
            color: sobreEscuro ? Colors.white : AppColors.text,
          ),
        ),
        const Spacer(),
        Text(
          '${(fracao * 100).round()}%',
          style: TextStyle(fontSize: 12, fontWeight: FontWeight.w700, color: sobreEscuro ? Colors.white70 : AppColors.textMuted),
        ),
      ]),
      const SizedBox(height: 6),
      ClipRRect(
        borderRadius: BorderRadius.circular(999),
        child: LinearProgressIndicator(
          value: fracao,
          minHeight: 7,
          backgroundColor: sobreEscuro ? Colors.white.withValues(alpha: 0.18) : AppColors.surfaceMuted,
          valueColor: const AlwaysStoppedAnimation(AppColors.accent400),
        ),
      ),
    ]);
  }
}

// ----------------------------------------------------------------------------- atualização do app
final atualizacaoProvider = FutureProvider<AtualizacaoDisponivel?>((ref) async {
  final config = await ref.watch(configProvider.future);
  if (config == null) return null;
  return AtualizacaoService.verificar(ref.watch(apiClientProvider), mid: config.motoristaId, token: config.token);
});

class AtualizacaoBanner extends ConsumerStatefulWidget {
  const AtualizacaoBanner({super.key});

  @override
  ConsumerState<AtualizacaoBanner> createState() => _AtualizacaoBannerState();
}

class _AtualizacaoBannerState extends ConsumerState<AtualizacaoBanner> {
  double? _progresso;

  Future<void> _atualizar(AtualizacaoDisponivel a) async {
    setState(() => _progresso = 0);
    try {
      await AtualizacaoService.baixarEInstalar(a, (p) {
        if (mounted) setState(() => _progresso = p);
      });
    } on ApiException catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(e.message), backgroundColor: AppColors.danger500));
      }
    } finally {
      if (mounted) setState(() => _progresso = null);
    }
  }

  @override
  Widget build(BuildContext context) {
    final a = ref.watch(atualizacaoProvider).value;
    if (a == null) return const SizedBox.shrink();
    final baixando = _progresso != null;
    return Container(
      margin: const EdgeInsets.only(top: 14),
      padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(
        color: AppColors.primary600.withValues(alpha: 0.08),
        borderRadius: BorderRadius.circular(AppRadius.md),
        border: Border.all(color: AppColors.primary600.withValues(alpha: 0.25)),
      ),
      child: Row(children: [
        const Icon(Icons.system_update_rounded, color: AppColors.primary600, size: 28),
        const SizedBox(width: 12),
        Expanded(
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text('Nova versão ${a.nome}', style: const TextStyle(fontWeight: FontWeight.w800, color: AppColors.text)),
            const SizedBox(height: 2),
            if (baixando) ...[
              const SizedBox(height: 6),
              LinearProgressIndicator(value: _progresso! > 0 ? _progresso : null, minHeight: 5),
            ] else
              const Text('Toque em atualizar e depois em "Instalar".',
                  style: TextStyle(fontSize: 12, color: AppColors.textMuted)),
          ]),
        ),
        const SizedBox(width: 10),
        FilledButton(
          onPressed: baixando ? null : () => _atualizar(a),
          style: FilledButton.styleFrom(minimumSize: const Size(0, 42), padding: const EdgeInsets.symmetric(horizontal: 16)),
          child: Text(baixando ? '${(_progresso! * 100).round()}%' : 'Atualizar'),
        ),
      ]),
    );
  }
}

// ----------------------------------------------------------------------------- km
/// Mesmos limites do servidor (viagem_routes.py): aqui só pedem confirmação,
/// nunca bloqueiam.
const _kmLimiteViagem = 1500;
const _kmLimiteDiferencaVeiculo = 1000;

String? avisoKm({required int km, int? referencia, int? kmInicial}) {
  if (kmInicial != null) {
    if (km < kmInicial) return 'O KM final ($km) é menor que o KM inicial ($kmInicial).';
    if (km - kmInicial > _kmLimiteViagem) return 'Isso dá ${km - kmInicial} km nesta viagem (saiu com $kmInicial).';
    return null;
  }
  if (referencia != null && (km - referencia).abs() > _kmLimiteDiferencaVeiculo) {
    return 'O último KM registrado deste veículo é $referencia.';
  }
  return null;
}

/// Pede o odômetro num bottom sheet. Se o valor parecer errado, mostra o
/// aviso e deixa o motorista corrigir ou confirmar assim mesmo.
Future<int?> pedirKm(
  BuildContext context, {
  required String titulo,
  required String legenda,
  int? referencia,
  int? kmInicial,
}) {
  return showModalBottomSheet<int>(
    context: context,
    isScrollControlled: true,
    builder: (ctx) => _KmSheet(titulo: titulo, legenda: legenda, referencia: referencia, kmInicial: kmInicial),
  );
}

class _KmSheet extends StatefulWidget {
  final String titulo;
  final String legenda;
  final int? referencia;
  final int? kmInicial;
  const _KmSheet({required this.titulo, required this.legenda, this.referencia, this.kmInicial});

  @override
  State<_KmSheet> createState() => _KmSheetState();
}

class _KmSheetState extends State<_KmSheet> {
  final _controller = TextEditingController();
  String? _erro;
  String? _aviso;

  void _confirmar({bool forcar = false}) {
    final km = int.tryParse(_controller.text.replaceAll(RegExp(r'\D'), ''));
    if (km == null) {
      setState(() => _erro = 'Digite o KM do painel.');
      return;
    }
    final aviso = avisoKm(km: km, referencia: widget.referencia, kmInicial: widget.kmInicial);
    if (aviso != null && !forcar) {
      setState(() => _aviso = aviso);
      return;
    }
    Navigator.pop(context, km);
  }

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: EdgeInsets.fromLTRB(20, 0, 20, MediaQuery.of(context).viewInsets.bottom + 20),
      child: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        Row(children: [
          Container(
            width: 44,
            height: 44,
            decoration: BoxDecoration(color: AppColors.primary600.withValues(alpha: 0.1), borderRadius: BorderRadius.circular(12)),
            child: const Icon(Icons.speed_rounded, color: AppColors.primary600),
          ),
          const SizedBox(width: 12),
          Expanded(
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Text(widget.titulo, style: const TextStyle(fontSize: 18, fontWeight: FontWeight.w800, color: AppColors.text)),
              Text(widget.legenda, style: const TextStyle(fontSize: 12.5, color: AppColors.textMuted)),
            ]),
          ),
        ]),
        const SizedBox(height: 18),
        TextField(
          controller: _controller,
          autofocus: true,
          keyboardType: TextInputType.number,
          inputFormatters: [FilteringTextInputFormatter.digitsOnly],
          textAlign: TextAlign.center,
          style: const TextStyle(fontSize: 30, fontWeight: FontWeight.w800, letterSpacing: 2, color: AppColors.text),
          decoration: InputDecoration(hintText: '000000', errorText: _erro, suffixText: 'km'),
          onChanged: (_) {
            if (_erro != null || _aviso != null) setState(() => _erro = _aviso = null);
          },
          onSubmitted: (_) => _confirmar(),
        ),
        if (_aviso != null) ...[
          const SizedBox(height: 14),
          Container(
            padding: const EdgeInsets.all(12),
            decoration: BoxDecoration(
              color: AppColors.warning500.withValues(alpha: 0.14),
              borderRadius: BorderRadius.circular(AppRadius.sm),
            ),
            child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
              const Icon(Icons.warning_amber_rounded, color: Color(0xFFB7791F)),
              const SizedBox(width: 10),
              Expanded(
                child: Text('Confere o painel? $_aviso',
                    style: const TextStyle(color: AppColors.text, fontWeight: FontWeight.w600)),
              ),
            ]),
          ),
          const SizedBox(height: 12),
          Row(children: [
            Expanded(
              child: OutlinedButton(
                onPressed: () => setState(() {
                  _aviso = null;
                  _controller.clear();
                }),
                child: const Text('Corrigir'),
              ),
            ),
            const SizedBox(width: 10),
            Expanded(child: FilledButton(onPressed: () => _confirmar(forcar: true), child: const Text('Está certo'))),
          ]),
        ] else ...[
          const SizedBox(height: 16),
          FilledButton(onPressed: _confirmar, child: const Text('Confirmar')),
        ],
      ]),
    );
  }
}
