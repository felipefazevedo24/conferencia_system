"""Painel de TV (somente leitura, sem login) com indicadores de
recebimento e expedicao atualizados em tempo real via polling.

Rotas publicas:
    GET /painel                     -> pagina fullscreen para TV
    GET /api/painel/indicadores     -> JSON com metricas + eventos recentes
"""
from __future__ import annotations

from datetime import datetime, timedelta

from flask import Blueprint, jsonify, render_template
from sqlalchemy import desc, func

from ..extensions import db
from ..models import ExpedicaoOrdemFat, ExpedicaoOrdemST, ItemNota

painel_tv_bp = Blueprint("painel_tv", __name__)

STATUS_EXP_PENDENTE = "Pendente de conferência"
STATUS_EXP_CONFERIDO = "Conferido/Ag. Fat"


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def _previsao_status(previsao: datetime | None, agora: datetime) -> str | None:
    """vencido | perto (<=1 dia) | None."""
    if not previsao:
        return None
    if previsao < agora:
        return "vencido"
    if previsao <= agora + timedelta(days=1):
        return "perto"
    return None


def _atrasado(desde: datetime | None, agora: datetime) -> bool:
    """True se esta pendente ha mais de 1 dia."""
    return bool(desde and desde < agora - timedelta(days=1))


def _count_distinct_notas(status: str) -> int:
    return (
        db.session.query(func.count(func.distinct(ItemNota.numero_nota)))
        .filter(ItemNota.status == status)
        .scalar()
        or 0
    )


def _coletar_indicadores() -> dict:
    agora = datetime.now()
    hoje = agora.date()

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

    # Lista de notas pendentes de conferencia (quais sao)
    pend_rows = (
        db.session.query(
            ItemNota.numero_nota,
            func.max(ItemNota.fornecedor).label("fornecedor"),
            func.min(ItemNota.data_importacao).label("desde"),
        )
        .filter(ItemNota.status == "Pendente")
        .group_by(ItemNota.numero_nota)
        .order_by("desde")
        .limit(60)
        .all()
    )
    recebimento_lista = [
        {
            "codigo": nota,
            "parceiro": fornecedor or "",
            "desde": _iso(desde),
            "atrasado": _atrasado(desde, agora),
        }
        for nota, fornecedor, desde in pend_rows
    ]

    # ---- EXPEDICAO ----
    exp_fat_pendente = (
        ExpedicaoOrdemFat.query.filter(ExpedicaoOrdemFat.status == STATUS_EXP_PENDENTE).count()
    )
    exp_st_pendente = (
        ExpedicaoOrdemST.query.filter(ExpedicaoOrdemST.status == STATUS_EXP_PENDENTE).count()
    )
    exp_fat_conferido = (
        ExpedicaoOrdemFat.query.filter(ExpedicaoOrdemFat.status == STATUS_EXP_CONFERIDO).count()
    )
    exp_st_conferido = (
        ExpedicaoOrdemST.query.filter(ExpedicaoOrdemST.status == STATUS_EXP_CONFERIDO).count()
    )
    exp_fat_hoje = (
        ExpedicaoOrdemFat.query.filter(func.date(ExpedicaoOrdemFat.expedido_at) == hoje).count()
    )
    exp_st_hoje = (
        ExpedicaoOrdemST.query.filter(func.date(ExpedicaoOrdemST.expedido_at) == hoje).count()
    )

    fat_pendentes = (
        ExpedicaoOrdemFat.query.filter(ExpedicaoOrdemFat.status == STATUS_EXP_PENDENTE)
        .order_by(ExpedicaoOrdemFat.created_at)
        .limit(60)
        .all()
    )
    fat_lista = [
        {
            "codigo": str(o.cod_ordem_fat),
            "parceiro": o.cliente or "",
            "previsao": _iso(o.dt_previsao_entrega),
            "previsao_status": _previsao_status(o.dt_previsao_entrega, agora),
            "desde": _iso(o.created_at),
            "atrasado": _atrasado(o.created_at, agora),
        }
        for o in fat_pendentes
    ]

    st_pendentes = (
        ExpedicaoOrdemST.query.filter(ExpedicaoOrdemST.status == STATUS_EXP_PENDENTE)
        .order_by(ExpedicaoOrdemST.created_at)
        .limit(60)
        .all()
    )
    st_lista = [
        {
            "codigo": str(o.cod_ordem_compra),
            "parceiro": o.fornecedor or "",
            "previsao": _iso(o.dt_prevista_entrega),
            "previsao_status": _previsao_status(o.dt_prevista_entrega, agora),
            "desde": _iso(o.created_at),
            "atrasado": _atrasado(o.created_at, agora),
        }
        for o in st_pendentes
    ]

    eventos = _coletar_eventos()

    return {
        "gerado_em": agora.isoformat(),
        "recebimento": {
            "pendente_conferencia": int(recebimento_pendente),
            "pendente_lancamento": int(recebimento_lancar),
            "lancadas_total": int(recebimento_lancado),
            "importadas_hoje": int(importadas_hoje),
            "lancadas_hoje": int(lancadas_hoje),
            "lista": recebimento_lista,
        },
        "expedicao": {
            "pendente_total": int(exp_fat_pendente + exp_st_pendente),
            "fat": {
                "pendente": int(exp_fat_pendente),
                "conferido": int(exp_fat_conferido),
                "expedidos_hoje": int(exp_fat_hoje),
                "lista": fat_lista,
            },
            "st": {
                "pendente": int(exp_st_pendente),
                "conferido": int(exp_st_conferido),
                "expedidos_hoje": int(exp_st_hoje),
                "lista": st_lista,
            },
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

    # NF conferida (conferencia cega de recebimento concluida) + quem conferiu
    conferidas = (
        db.session.query(
            ItemNota.numero_nota,
            func.max(ItemNota.usuario_conferencia).label("usuario"),
            func.max(ItemNota.fim_conferencia).label("dt"),
        )
        .filter(ItemNota.fim_conferencia.isnot(None))
        .group_by(ItemNota.numero_nota)
        .order_by(desc("dt"))
        .limit(8)
        .all()
    )
    for nota, usuario, dt in conferidas:
        eventos.append(
            {
                "key": f"conferida:{nota}:{_iso(dt)}",
                "tipo": "conferencia",
                "titulo": "NF conferida",
                "detalhe": f"NF {nota}" + (f" — por {usuario}" if usuario else ""),
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
                "subtipo": "fat",
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
                "subtipo": "st",
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
