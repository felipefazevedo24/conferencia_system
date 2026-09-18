"""Eventos de chapas gravados na mesma transação da mudança auditada."""
import json

from ..extensions import db
from ..models import ChapaAuditoria


def registrar(item, acao, usuario, antes, depois):
    if antes == depois:
        return
    db.session.add(ChapaAuditoria(item_nota_id=item.id, numero_nota=item.numero_nota,
        codigo=item.codigo_grv or item.codigo, descricao=item.descricao,
        ar=item.numero_lancamento, acao=acao, usuario=usuario or 'desconhecido',
        antes=antes, depois=depois))


def alterar_unidades(item, unidades, usuario):
    anterior = item.qtd_chapas_und
    if anterior == unidades:
        return
    item.qtd_chapas_und = unidades
    registrar(item, 'Unidades informadas' if anterior is None else 'Unidades alteradas',
              usuario, {'und': anterior}, {'und': unidades})


def estado_calculo(calc):
    if not calc:
        return None
    try:
        dimensoes = json.loads(calc.dimensoes) if calc.dimensoes else {}
    except (ValueError, TypeError):
        dimensoes = {}
    return dict(material=calc.material, formato=calc.formato,
                dimensoes=dimensoes, peso_por_peca=calc.peso_por_peca)
