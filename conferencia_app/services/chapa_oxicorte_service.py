"""Chapa oxicorte da Aços Radial na conferência de recebimento.

A Aços Radial (CNPJ 00.446.473) é quem fornece a matéria-prima da família
"N - 42 - MATÉRIA-PRIMA - MATERIAL ESPECÍFICO" no GRV: peça cortada em
oxicorte (MP AÇOS ASTM A36 / SAE 1020 / 1045 / 4140 / AMT 350 OXICORTE...),
vendida em KG. Regras combinadas com a logística em 29/09/2026:

- o item dessa família chega sempre marcado como chapa, e o conferente não
  pode desmarcar; ele informa só a quantidade de peças (UND), sem medidas -
  peça de oxicorte não tem medida de chapa padrão;
- entra no aviso de entrada de chapa, mas NÃO no Controle de Chapas (lote):
  cada peça é específica, não vira estoque de lote;
- a família vem do item do PEDIDO de compra (codigo_grv), não do XML. Se
  alguma linha da NF não estiver vinculada ao pedido, ou se o GRV não
  responder, a conferência não segue - melhor parar do que marcar errado.

A família vem da bridge só para os códigos da NF (buscar_familias_grv), com
cache de 12 h; bridge antiga cai na consulta de estoque completa.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Iterable

from ..extensions import db
from ..models import ChapaControleExclusao, ItemNota
from .erp_estoque_service import buscar_familias_grv


CNPJ_RAIZ_ACOS_RADIAL = "00446473"
FAMILIA_OXICORTE = "N - 42 - MATERIA-PRIMA - MATERIAL ESPECIFICO"
# Quem aparece como autor da exclusão do Controle de Chapas.
USUARIO_EXCLUSAO = "SYNC (oxicorte Aços Radial)"


def _normalizar(texto: str | None) -> str:
    valor = unicodedata.normalize("NFKD", str(texto or "").strip().upper())
    valor = "".join(ch for ch in valor if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", valor)


def _codigo(codigo: str | None) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(codigo or "").upper())


def eh_nf_acos_radial(itens: Iterable[ItemNota]) -> bool:
    """NF da Aços Radial com pedido de compra (remessa e material do cliente
    não têm pedido, então a regra não se aplica)."""
    for item in itens:
        cnpj = re.sub(r"\D", "", str(item.cnpj_emitente or ""))
        if not cnpj.startswith(CNPJ_RAIZ_ACOS_RADIAL):
            return False
        if item.material_cliente or item.remessa:
            return False
    return True


def _lista(itens: list[ItemNota], com_codigo: bool = False) -> str:
    linhas = [
        "• " + (f"{i.codigo_grv} — " if com_codigo else "") + (str(i.descricao or i.codigo or "-").strip())
        for i in itens[:8]
    ]
    if len(itens) > 8:
        linhas.append(f"• e mais {len(itens) - 8}")
    return "\n".join(linhas)


def identificar_oxicorte(itens: list[ItemNota]) -> set[int]:
    """IDs dos itens da NF da Aços Radial que são chapa oxicorte.

    Levanta ValueError (mensagem para o conferente, uma linha por item) quando
    não dá pra garantir: linha sem vínculo com o pedido, GRV fora do ar ou
    código do pedido que não existe no GRV."""
    sem_vinculo = [i for i in itens if i.linha_po_vinculada is None or not _codigo(i.codigo_grv)]
    if sem_vinculo:
        raise ValueError(
            f"Conferência bloqueada: {len(sem_vinculo)} linha(s) desta NF da Aços Radial sem vínculo com o pedido de compra.\n"
            "Faça o vínculo no Documento de Entrada e abra a conferência de novo.\n"
            + _lista(sem_vinculo)
        )

    try:
        familias = {c: _normalizar(f) for c, f in buscar_familias_grv([i.codigo_grv for i in itens]).items()}
    except Exception:
        raise ValueError(
            "Conferência bloqueada: não consegui consultar o GRV para identificar as chapas oxicorte da Aços Radial.\n"
            "Tente de novo em alguns instantes."
        )

    nao_encontrados = [i for i in itens if _codigo(i.codigo_grv) not in familias]
    if nao_encontrados:
        raise ValueError(
            f"Conferência bloqueada: {len(nao_encontrados)} código(s) do pedido não existem (ou estão inativos) no GRV.\n"
            "Corrija o vínculo no Documento de Entrada.\n"
            + _lista(nao_encontrados, com_codigo=True)
        )
    return {i.id for i in itens if familias[_codigo(i.codigo_grv)] == FAMILIA_OXICORTE}


def excluir_do_controle_de_chapas(item: ItemNota) -> None:
    """Oxicorte não entra no Controle de Chapas. Usa a mesma exclusão do
    Admin: o estorno da conferência já a apaga e o /validar a recria."""
    if not ChapaControleExclusao.query.filter_by(item_nota_id=item.id).first():
        db.session.add(ChapaControleExclusao(item_nota_id=item.id, usuario=USUARIO_EXCLUSAO))
