"""Painel de TV (somente leitura, sem login) com indicadores de
recebimento e expedicao atualizados em tempo real via polling.

Rotas publicas:
    GET /painel                     -> pagina fullscreen para TV
    GET /api/painel/indicadores     -> JSON com metricas + eventos recentes
"""
from __future__ import annotations

from datetime import datetime

from flask import Blueprint, jsonify, render_template
from sqlalchemy import desc, func

from ..extensions import db
from ..models import ExpedicaoOrdemFat, ExpedicaoOrdemST, ItemNota

painel_tv_bp = Blueprint("painel_tv", __name__)

STATUS_EXP_PENDENTE = "Pendente de conferência"
STATUS_EXP_CONFERIDO = "Conferido/Ag. Fat"


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def _count_distinct_notas(status: str) -> int:
    return (
        db.session.query(func.count(func.distinct(ItemNota.numero_nota)))
        .filter(ItemNota.status == status)
        .scalar()
        or 0
    )


def _coletar_indicadores() -> dict:
    hoje = datetime.now().date()

    # ---- RECEBIMENTO ----
    recebimento_pendente = _count_distinct_notas("Pendente")
    recebimento_lancar = _count_distinct_notas("Concluído")
    recebimento_lancado = _count_distinct_notas("Lançado")

    importadas_hoje = (
        db.session.query(func.count(func.distinct(ItemNota.numero_nota)))
        .filter(func.date(ItemNota.data_importacao) == hoje)
        .scalar()
        or 0
    )
    lancadas_hoje = (
        db.session.query(func.count(func.distinct(ItemNota.numero_nota)))
        .filter(ItemNota.status == "Lançado", func.date(ItemNota.data_lancamento) == hoje)
        .scalar()
        or 0
    )

    # ---- EXPEDICAO ----
    exp_fat_pendente = (
        ExpedicaoOrdemFat.query.filter(ExpedicaoOrdemFat.status == STATUS_EXP_PENDENTE).count()
    )
    exp_st_pendente = (
        ExpedicaoOrdemST.query.filter(ExpedicaoOrdemST.status == STATUS_EXP_PENDENTE).count()
    )
    exp_conferido = (
        ExpedicaoOrdemFat.query.filter(ExpedicaoOrdemFat.status == STATUS_EXP_CONFERIDO).count()
        + ExpedicaoOrdemST.query.filter(ExpedicaoOrdemST.status == STATUS_EXP_CONFERIDO).count()
    )
    exp_expedidos_hoje = (
        ExpedicaoOrdemFat.query.filter(func.date(ExpedicaoOrdemFat.expedido_at) == hoje).count()
        + ExpedicaoOrdemST.query.filter(func.date(ExpedicaoOrdemST.expedido_at) == hoje).count()
    )

    eventos = _coletar_eventos()

    return {
        "gerado_em": datetime.now().isoformat(),
        "recebimento": {
            "pendente_conferencia": int(recebimento_pendente),
            "pendente_lancamento": int(recebimento_lancar),
            "lancadas_total": int(recebimento_lancado),
            "importadas_hoje": int(importadas_hoje),
            "lancadas_hoje": int(lancadas_hoje),
        },
        "expedicao": {
            "fat_pendente": int(exp_fat_pendente),
            "st_pendente": int(exp_st_pendente),
            "pendente_total": int(exp_fat_pendente + exp_st_pendente),
            "conferido_ag_fat": int(exp_conferido),
            "expedidos_hoje": int(exp_expedidos_hoje),
        },
        "eventos": eventos,
    }


def _coletar_eventos() -> list[dict]:
    """Ultimos acontecimentos relevantes para gerar avisos na TV."""
    eventos: list[dict] = []

    # Mercadoria chegou (nota importada) - agrupa por numero_nota
    chegadas = (
        db.session.query(
            ItemNota.numero_nota,
            func.max(ItemNota.fornecedor).label("fornecedor"),
            func.max(ItemNota.data_importacao).label("dt"),
        )
        .filter(ItemNota.data_importacao.isnot(None))
        .group_by(ItemNota.numero_nota)
        .order_by(desc("dt"))
        .limit(8)
        .all()
    )
    for nota, fornecedor, dt in chegadas:
        eventos.append(
            {
                "key": f"chegada:{nota}:{_iso(dt)}",
                "tipo": "chegada",
                "titulo": "Mercadoria chegou",
                "detalhe": f"NF {nota}" + (f" — {fornecedor}" if fornecedor else ""),
                "ts": _iso(dt),
            }
        )

    # NF lancada / estoque alimentado
    lancadas = (
        db.session.query(
            ItemNota.numero_nota,
            func.max(ItemNota.numero_lancamento).label("num_lanc"),
            func.max(ItemNota.data_lancamento).label("dt"),
        )
        .filter(ItemNota.status == "Lançado", ItemNota.data_lancamento.isnot(None))
        .group_by(ItemNota.numero_nota)
        .order_by(desc("dt"))
        .limit(8)
        .all()
    )
    for nota, num_lanc, dt in lancadas:
        detalhe = f"NF {nota} lançada"
        if num_lanc:
            detalhe += f" (lçto {num_lanc})"
        eventos.append(
            {
                "key": f"lancamento:{nota}:{_iso(dt)}",
                "tipo": "lancamento",
                "titulo": "NF lançada — estoque alimentado",
                "detalhe": detalhe,
                "ts": _iso(dt),
            }
        )

    # Novo faturamento na expedicao (FAT)
    fats = (
        ExpedicaoOrdemFat.query.order_by(desc(ExpedicaoOrdemFat.created_at)).limit(6).all()
    )
    for o in fats:
        eventos.append(
            {
                "key": f"exp_fat:{o.cod_ordem_fat}:{_iso(o.created_at)}",
                "tipo": "expedicao",
                "titulo": "Novo faturamento (Expedição)",
                "detalhe": f"Ordem {o.cod_ordem_fat}" + (f" — {o.cliente}" if o.cliente else ""),
                "ts": _iso(o.created_at),
            }
        )

    # Novo faturamento na expedicao (ST)
    sts = (
        ExpedicaoOrdemST.query.order_by(desc(ExpedicaoOrdemST.created_at)).limit(6).all()
    )
    for o in sts:
        eventos.append(
            {
                "key": f"exp_st:{o.cod_ordem_compra}:{_iso(o.created_at)}",
                "tipo": "expedicao",
                "titulo": "Novo faturamento ST (Expedição)",
                "detalhe": f"OC {o.cod_ordem_compra}" + (f" — {o.fornecedor}" if o.fornecedor else ""),
                "ts": _iso(o.created_at),
            }
        )

    eventos = [e for e in eventos if e["ts"]]
    eventos.sort(key=lambda e: e["ts"], reverse=True)
    return eventos[:14]


@painel_tv_bp.route("/painel")
def painel_tv_page():
    return render_template("painel_tv.html")


@painel_tv_bp.route("/api/painel/indicadores")
def painel_tv_indicadores():
    try:
        dados = _coletar_indicadores()
    except Exception:
        db.session.rollback()
        raise
    return jsonify(dados)
