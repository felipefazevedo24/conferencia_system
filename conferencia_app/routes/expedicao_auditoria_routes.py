"""Auditoria/busca administrativa da Conferencia de Expedicao.

Ferramenta restrita ao role Admin (checagem direta via roles_required, sem
depender do catalogo de permissoes delegavel) para localizar qualquer ordem
de faturamento (FAT) ou ordem de compra de Servico de Terceiro (ST) e o
historico completo de conferencias/edicoes - por codigo interno
(OF-/OC-/CNF-), cliente/fornecedor, ordem de compra ou numero da ordem de
faturamento. Funciona mesmo que a ordem tenha sumido da API de origem, pois
consulta apenas o banco local (a sincronizacao so cria/atualiza, nunca
apaga ordens ja existentes)."""

from flask import Blueprint, jsonify, render_template, request, session

from ..auth import roles_required
from ..extensions import db
from ..models import ExpedicaoConferenciaLog, ExpedicaoOrdemFat, ExpedicaoOrdemST
from ..services import expedicao_log_service as log_svc


expedicao_auditoria_bp = Blueprint("expedicao_auditoria", __name__)

LIMITE_RESULTADOS = 50


def _iso(value):
    return value.isoformat() if value else None


def _prefixo_codigo(q: str) -> str | None:
    q_upper = q.upper()
    for prefixo in ("OF-", "OC-", "CNF-"):
        if q_upper.startswith(prefixo):
            return prefixo
    return None


def _resultado_fat(ordem: ExpedicaoOrdemFat) -> dict:
    return {
        "origem": "fat",
        "origem_label": "Ordem de faturamento",
        "id": ordem.id,
        "codigo_interno": ordem.codigo_interno,
        "cod_ordem": str(ordem.cod_ordem_fat),
        "cliente_fornecedor": ordem.cliente,
        "status": ordem.status,
        "numero_nf": ordem.numero_nf,
        "created_at": _iso(ordem.created_at),
        "excluido": bool(ordem.excluido),
        "excluido_at": _iso(ordem.excluido_at),
        "excluido_by": ordem.excluido_by,
        "excluido_motivo": ordem.excluido_motivo,
        "historico": log_svc.listar_logs("fat", ordem.id),
    }


def _resultado_st(ordem: ExpedicaoOrdemST) -> dict:
    return {
        "origem": "st",
        "origem_label": "Serviço de terceiro",
        "id": ordem.id,
        "codigo_interno": ordem.codigo_interno,
        "cod_ordem": ordem.cod_ordem_compra,
        "cliente_fornecedor": ordem.fornecedor,
        "status": ordem.status,
        "numero_nf": ordem.numero_nf,
        "created_at": _iso(ordem.created_at),
        "excluido": bool(ordem.excluido),
        "excluido_at": _iso(ordem.excluido_at),
        "excluido_by": ordem.excluido_by,
        "excluido_motivo": ordem.excluido_motivo,
        "historico": log_svc.listar_logs("st", ordem.id),
    }


def _resultado_log_orfao(log: ExpedicaoConferenciaLog) -> dict:
    """Fallback defensivo: log encontrado mas a ordem-pai nao existe mais.

    Nao deveria acontecer (ordens nunca sao apagadas pela sincronizacao),
    mas garante que o codigo CNF- continue localizavel mesmo assim."""
    return {
        "origem": log.origem,
        "origem_label": "Ordem de faturamento" if log.origem == "fat" else "Serviço de terceiro",
        "id": log.ordem_id,
        "codigo_interno": None,
        "cod_ordem": log.cod_ordem,
        "cliente_fornecedor": None,
        "status": log.status_novo,
        "numero_nf": None,
        "created_at": None,
        "historico": log_svc.listar_logs(log.origem, log.ordem_id),
    }


@expedicao_auditoria_bp.route("/expedicao/auditoria")
@roles_required("Admin")
def auditoria_page():
    return render_template("expedicao_auditoria.html", user=session["username"])


@expedicao_auditoria_bp.route("/api/expedicao/auditoria/buscar", methods=["GET"])
@roles_required("Admin")
def buscar_auditoria():
    q = (request.args.get("q") or "").strip()
    if not q:
        return jsonify({"resultados": []})

    prefixo = _prefixo_codigo(q)
    resultados = []

    if prefixo == "OF-":
        ordem = ExpedicaoOrdemFat.query.filter_by(codigo_interno=q.upper()).first()
        if ordem:
            resultados.append(_resultado_fat(ordem))
    elif prefixo == "OC-":
        ordem = ExpedicaoOrdemST.query.filter_by(codigo_interno=q.upper()).first()
        if ordem:
            resultados.append(_resultado_st(ordem))
    elif prefixo == "CNF-":
        log = ExpedicaoConferenciaLog.query.filter_by(codigo_interno=q.upper()).first()
        if log:
            ordem = None
            if log.origem == "fat":
                ordem = ExpedicaoOrdemFat.query.get(log.ordem_id)
            elif log.origem == "st":
                ordem = ExpedicaoOrdemST.query.get(log.ordem_id)
            if ordem:
                resultados.append(_resultado_fat(ordem) if log.origem == "fat" else _resultado_st(ordem))
            else:
                resultados.append(_resultado_log_orfao(log))
    else:
        termo = f"%{q}%"
        filtros_fat = [
            ExpedicaoOrdemFat.cliente.ilike(termo),
            ExpedicaoOrdemFat.pedido.ilike(termo),
            ExpedicaoOrdemFat.orcamento.ilike(termo),
        ]
        try:
            filtros_fat.append(ExpedicaoOrdemFat.cod_ordem_fat == int(q))
        except ValueError:
            pass
        fat_matches = (
            ExpedicaoOrdemFat.query.filter(db.or_(*filtros_fat))
            .order_by(ExpedicaoOrdemFat.id.desc())
            .limit(LIMITE_RESULTADOS)
            .all()
        )
        resultados.extend(_resultado_fat(o) for o in fat_matches)

        st_matches = (
            ExpedicaoOrdemST.query.filter(
                db.or_(
                    ExpedicaoOrdemST.fornecedor.ilike(termo),
                    ExpedicaoOrdemST.cod_ordem_compra.ilike(termo),
                )
            )
            .order_by(ExpedicaoOrdemST.id.desc())
            .limit(LIMITE_RESULTADOS)
            .all()
        )
        resultados.extend(_resultado_st(o) for o in st_matches)

    return jsonify({"resultados": resultados})
