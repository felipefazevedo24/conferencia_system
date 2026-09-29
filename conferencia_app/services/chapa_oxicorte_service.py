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

A família sai da consulta de estoque que a bridge já tem (todos os produtos
ativos com a família, com ou sem saldo): não precisa de endpoint novo na VM.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Iterable

from ..extensions import db
from ..models import ChapaControleExclusao, ItemNota
from .erp_estoque_service import buscar_estoque_grv


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


def _rotulo(item: ItemNota) -> str:
    return f"{item.codigo or '-'} {str(item.descricao or '')[:40]}".strip()


def identificar_oxicorte(itens: list[ItemNota]) -> set[int]:
    """IDs dos itens da NF da Aços Radial que são chapa oxicorte.

    Levanta ValueError (mensagem para o conferente) quando não dá pra
    garantir: linha sem vínculo com o pedido, GRV fora do ar ou código do
    pedido que não existe no GRV."""
    sem_vinculo = [i for i in itens if i.linha_po_vinculada is None or not _codigo(i.codigo_grv)]
    if sem_vinculo:
        raise ValueError(
            "NF da Aços Radial: todas as linhas precisam estar vinculadas ao pedido de compra "
            "no Documento de Entrada para o Sync identificar as chapas oxicorte. Sem vínculo: "
            + "; ".join(_rotulo(i) for i in sem_vinculo[:5])
            + (f" e mais {len(sem_vinculo) - 5}" if len(sem_vinculo) > 5 else "")
            + "."
        )

    try:
        estoque = buscar_estoque_grv()
    except Exception:
        raise ValueError(
            "NF da Aços Radial: não foi possível consultar o GRV para identificar as chapas oxicorte. "
            "Tente novamente em instantes; a conferência fica bloqueada até o GRV responder."
        )
    familias = {_codigo(codigo): _normalizar(dados.get("familia")) for codigo, dados in (estoque.get("por_codigo") or {}).items()}

    nao_encontrados = [i for i in itens if _codigo(i.codigo_grv) not in familias]
    if nao_encontrados:
        raise ValueError(
            "NF da Aços Radial: código do pedido não encontrado no GRV (ou inativo): "
            + "; ".join(f"{i.codigo_grv} ({_rotulo(i)})" for i in nao_encontrados[:5])
            + ". Corrija o vínculo no Documento de Entrada."
        )
    return {i.id for i in itens if familias[_codigo(i.codigo_grv)] == FAMILIA_OXICORTE}


def excluir_do_controle_de_chapas(item: ItemNota) -> None:
    """Oxicorte não entra no Controle de Chapas. Usa a mesma exclusão do
    Admin: o estorno da conferência já a apaga e o /validar a recria."""
    if not ChapaControleExclusao.query.filter_by(item_nota_id=item.id).first():
        db.session.add(ChapaControleExclusao(item_nota_id=item.id, usuario=USUARIO_EXCLUSAO))
