import 'dart:io';

import 'package:http/http.dart' as http;
import 'package:open_filex/open_filex.dart';
import 'package:package_info_plus/package_info_plus.dart';
import 'package:path_provider/path_provider.dart';

import '../../data/remote/api_client.dart';
import '../config.dart';

class AtualizacaoDisponivel {
  final int codigo;
  final String nome;
  final int tamanho;
  final String url;
  const AtualizacaoDisponivel({required this.codigo, required this.nome, required this.tamanho, required this.url});
}

/// Atualização pelo próprio app: o gestor publica o APK no Sync e o app,
/// ao abrir, compara com o build instalado. O Android sempre pede 1 toque
/// em "Instalar" para APK fora da Play Store; o resto é automático.
///
/// Só funciona se o APK novo for assinado com a mesma chave do instalado.
class AtualizacaoService {
  /// `null` quando não há versão mais nova ou a consulta falhou: atualização
  /// nunca pode atrapalhar o uso do app.
  static Future<AtualizacaoDisponivel?> verificar(ApiClient api, {required int mid, required String token}) async {
    try {
      final info = await PackageInfo.fromPlatform();
      final instalada = int.tryParse(info.buildNumber) ?? 0;
      final data = await api.appVersao(mid: mid, token: token);
      final codigo = (data['versao_codigo'] as num?)?.toInt() ?? 0;
      if (codigo <= instalada) return null;
      return AtualizacaoDisponivel(
        codigo: codigo,
        nome: data['versao_nome'] as String? ?? '$codigo',
        tamanho: (data['tamanho'] as num?)?.toInt() ?? 0,
        // Montada aqui com o https do app (não vem do servidor).
        url: '${AppConfig.baseUrl}/motorista/painel/$mid/$token/app.apk',
      );
    } catch (_) {
      return null;
    }
  }

  /// Baixa o APK para o cache do app (pasta liberada pelo FileProvider do
  /// open_filex) e abre o instalador do Android.
  static Future<void> baixarEInstalar(AtualizacaoDisponivel a, void Function(double progresso) onProgresso) async {
    final dir = await getTemporaryDirectory();
    final arquivo = File('${dir.path}/motorista_sync_${a.codigo}.apk');
    final cliente = http.Client();
    try {
      final res = await cliente.send(http.Request('GET', Uri.parse(a.url)));
      if (res.statusCode != 200) {
        throw ApiException('Falha ao baixar a atualização (${res.statusCode}).');
      }
      final total = res.contentLength ?? a.tamanho;
      final destino = arquivo.openWrite();
      var recebido = 0;
      try {
        await for (final parte in res.stream) {
          destino.add(parte);
          recebido += parte.length;
          if (total > 0) onProgresso(recebido / total);
        }
      } finally {
        await destino.close();
      }
    } on ApiException {
      rethrow;
    } catch (_) {
      throw ApiException('Sem conexão para baixar a atualização.');
    } finally {
      cliente.close();
    }
    final aberto = await OpenFilex.open(arquivo.path, type: 'application/vnd.android.package-archive');
    if (aberto.type != ResultType.done) {
      throw ApiException('Não foi possível abrir o instalador: ${aberto.message}');
    }
  }
}
