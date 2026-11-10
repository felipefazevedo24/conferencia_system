import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/services/link_parser.dart';
import '../../core/theme.dart';
import '../../data/remote/api_client.dart';
import '../providers/providers.dart';

/// Tela de pareamento (1ª vez que o motorista abre o app): cola o mesmo link
/// do painel que o despachante já manda hoje por WhatsApp
/// (gerado em `/motorista/<mid>/painel-link` no backend, sem mudanças ali).
class SetupScreen extends ConsumerStatefulWidget {
  const SetupScreen({super.key});

  @override
  ConsumerState<SetupScreen> createState() => _SetupScreenState();
}

class _SetupScreenState extends ConsumerState<SetupScreen> {
  final _controller = TextEditingController();
  bool _carregando = false;
  String? _erro;

  Future<void> _colarDaAreaDeTransferencia() async {
    final dados = await Clipboard.getData(Clipboard.kTextPlain);
    if (dados?.text != null) {
      setState(() => _controller.text = dados!.text!);
    }
  }

  Future<void> _confirmar() async {
    final parsed = LinkParser.parsePainelLink(_controller.text);
    if (parsed == null) {
      setState(() => _erro = 'Link inválido. Cole o link completo enviado pelo despachante.');
      return;
    }
    setState(() {
      _carregando = true;
      _erro = null;
    });
    try {
      final repo = ref.read(motoristaRepositoryProvider);
      final painel = await repo.painelViagens(mid: parsed.motoristaId, token: parsed.token);
      // Se chegou aqui sem exceção, o token é válido — o nome vem junto na
      // primeira viagem retornada, se houver; senão fica sem nome mesmo.
      String? nome;
      if (painel.ativas.isNotEmpty) {
        nome = painel.ativas.first.motoristaNome;
      } else if (painel.historico.isNotEmpty) {
        nome = painel.historico.first.motoristaNome;
      }
      await ConfigStore.salvar(motoristaId: parsed.motoristaId, token: parsed.token, nome: nome);
      ref.invalidate(configProvider);
    } on ApiException catch (e) {
      setState(() => _erro = e.message);
    } catch (_) {
      setState(() => _erro = 'Não foi possível validar o link. Tente novamente.');
    } finally {
      if (mounted) setState(() => _carregando = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: ListView(
        padding: EdgeInsets.zero,
        children: [
          Container(
            decoration: const BoxDecoration(
              gradient: AppTheme.gradienteMarca,
              borderRadius: BorderRadius.vertical(bottom: Radius.circular(AppRadius.xl)),
            ),
            padding: EdgeInsets.fromLTRB(24, MediaQuery.of(context).padding.top + 48, 24, 36),
            child: Column(children: [
              Container(
                width: 72,
                height: 72,
                decoration: BoxDecoration(color: Colors.white.withValues(alpha: 0.12), shape: BoxShape.circle),
                child: const Icon(Icons.local_shipping_rounded, size: 36, color: Colors.white),
              ),
              const SizedBox(height: 16),
              const Text('Motorista Sync', style: TextStyle(color: Colors.white, fontSize: 24, fontWeight: FontWeight.w800)),
              const SizedBox(height: 6),
              const Text('Columbia Machine', style: TextStyle(color: Colors.white70, fontWeight: FontWeight.w600)),
            ]),
          ),
          Padding(
            padding: const EdgeInsets.fromLTRB(24, 28, 24, 24),
            child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
              const Text('Conectar este celular',
                  style: TextStyle(fontSize: 18, fontWeight: FontWeight.w800, color: AppColors.text)),
              const SizedBox(height: 6),
              const Text(
                'Cole o link que a logística te mandou pelo WhatsApp.',
                style: TextStyle(color: AppColors.textMuted),
              ),
              const SizedBox(height: 18),
              TextField(
                controller: _controller,
                minLines: 2,
                maxLines: 4,
                decoration: const InputDecoration(
                  hintText: 'https://sync.columbiamachine.com.br/motorista/painel/...',
                ),
              ),
              const SizedBox(height: 10),
              OutlinedButton.icon(
                onPressed: _colarDaAreaDeTransferencia,
                icon: const Icon(Icons.content_paste_rounded),
                label: const Text('Colar link'),
              ),
              if (_erro != null) ...[
                const SizedBox(height: 12),
                Text(_erro!, style: const TextStyle(color: AppColors.danger500, fontWeight: FontWeight.w600)),
              ],
              const SizedBox(height: 16),
              FilledButton(
                onPressed: _carregando ? null : _confirmar,
                child: _carregando
                    ? const SizedBox(
                        height: 20,
                        width: 20,
                        child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white),
                      )
                    : const Text('Conectar'),
              ),
            ]),
          ),
        ],
      ),
    );
  }
}
