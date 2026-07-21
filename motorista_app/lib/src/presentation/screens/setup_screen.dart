import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/services/link_parser.dart';
import '../../data/remote/api_client.dart';
import '../providers/providers.dart';

/// Tela de pareamento (1ª vez que o motorista abre o app): cola o mesmo link
/// do painel que o despachante já manda hoje por WhatsApp
/// (gerado em /motorista/<mid>/painel-link no backend, sem mudanças ali).
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
      body: SafeArea(
        child: Padding(
          padding: const EdgeInsets.all(24),
          child: Column(
            mainAxisAlignment: MainAxisAlignment.center,
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              const Icon(Icons.local_shipping, size: 64, color: Color(0xFF0F62C9)),
              const SizedBox(height: 16),
              Text(
                'Configurar seu app',
                style: Theme.of(context).textTheme.headlineSmall,
                textAlign: TextAlign.center,
              ),
              const SizedBox(height: 8),
              const Text(
                'Cole abaixo o link que o despachante te enviou pelo WhatsApp '
                '(o mesmo link do painel de viagens).',
                textAlign: TextAlign.center,
                style: TextStyle(color: Colors.black54),
              ),
              const SizedBox(height: 24),
              TextField(
                controller: _controller,
                minLines: 2,
                maxLines: 4,
                decoration: const InputDecoration(
                  border: OutlineInputBorder(),
                  hintText: 'https://sync.columbiamachine.com.br/motorista/painel/...',
                ),
              ),
              const SizedBox(height: 8),
              OutlinedButton.icon(
                onPressed: _colarDaAreaDeTransferencia,
                icon: const Icon(Icons.paste),
                label: const Text('Colar da área de transferência'),
              ),
              if (_erro != null) ...[
                const SizedBox(height: 12),
                Text(_erro!, style: const TextStyle(color: Colors.red), textAlign: TextAlign.center),
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
                    : const Text('Confirmar'),
              ),
            ],
          ),
        ),
      ),
    );
  }
}
