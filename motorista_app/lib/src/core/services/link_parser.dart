/// Extrai motoristaId + token do link de painel que o despachante já envia
/// hoje por WhatsApp (gerado em /motorista/<mid>/painel-link no backend).
/// Aceita colar a mensagem inteira (com texto ao redor), não só a URL pura.
class LinkParser {
  static final _painelRegex = RegExp(r'/motorista/painel/(\d+)/([a-f0-9]+)');

  static ({int motoristaId, String token})? parsePainelLink(String texto) {
    final match = _painelRegex.firstMatch(texto);
    if (match == null) return null;
    return (motoristaId: int.parse(match.group(1)!), token: match.group(2)!);
  }
}
