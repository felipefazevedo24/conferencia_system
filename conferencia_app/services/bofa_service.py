"""Compatibilidade legada para imports antigos."""

from .bb_boleto_service import BBBoletoService


class BofaService(BBBoletoService):
    """Alias temporario ate o codigo legado ser removido."""

    pass
