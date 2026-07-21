"""Painel de TV (somente leitura, sem login) com indicadores de
recebimento e expedicao atualizados em tempo real via polling.

Rotas publicas:
    GET /painel                     -> pagina fullscreen para TV
    GET /api/painel/indicadores     -> JSON com metricas + eventos recentes
"""
from __future__ import annotations

import os
import threading
from datetime import date, datetime, timedelta

from flask import Blueprint, current_app, jsonify, render_template
from sqlalchemy import desc, func

from ..extensions import db
from ..models import ExpedicaoOrdemFat, ExpedicaoOrdemST, ItemNota

painel_tv_bp = Blueprint("painel_tv", __name__)

STATUS_EXP_PENDENTE = "Pendente de conferência"
STATUS_EXP_CONFERIDO = "Conferido/Ag. Fat"
STATUS_EXP_FATURADO = "Faturado"
STATUS_EXP_FATURADO_SEM_CONF = "Faturado sem conferência"
STATUS_EXP_EXPEDIDO = "Expedido"

# Mesmo corte de backlog aplicado na tela de Conferência de Expedição cega
# (FAT): oculta solicitações de "Faturado sem conferência" anteriores à
# ordem de faturamento 1594 (ver expedicao_fat_routes.FAT_SEM_CONF_COD_MINIMO).
FAT_SEM_CONF_COD_MINIMO = 1594


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


def _cons_item(codigo, parceiro, tipo, numero_nf, previsao, desde, ts, agora) -> dict:
    """Item consolidado (FAT ou ST) para o painel de expedicao."""
    return {
        "codigo": str(codigo),
        "parceiro": parceiro or "",
        "tipo": tipo,  # "FAT" (faturamento) ou "OC" (servico de terceiro)
        "numero_nf": numero_nf or "",
        "previsao": _iso(previsao),
        "previsao_status": _previsao_status(previsao, agora),
        "desde": _iso(desde),
        "atrasado": _atrasado(desde, agora),
        "ts": _iso(ts),
    }


def _receb_item(nota, parceiro, quem, quem_label, ts, desde, agora, extra="") -> dict:
    """Item consolidado do painel de recebimento (por nota fiscal)."""
    return {
        "codigo": str(nota),
        "parceiro": parceiro or "",
        "quem": quem or "",
        "quem_label": quem_label or "",  # "Importou" | "Conferiu"
        "extra": extra or "",             # ex.: numero do lancamento
        "ts": _iso(ts),
        "desde": _iso(desde),
        "atrasado": _atrasado(desde, agora),
    }


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

    # Categorias consolidadas do recebimento (por nota fiscal).
    #  1) Pendentes de conferencia  -> quem importou
    #  2) Conferidas (hoje)         -> quem conferiu
    #  3) Pendentes de lancamento   -> quem conferiu
    #  4) Lancadas (hoje)           -> numero do lancamento
    receb_pend_conf_rows = (
        db.session.query(
            ItemNota.numero_nota,
            func.max(ItemNota.fornecedor).label("fornecedor"),
            func.max(ItemNota.usuario_importacao).label("quem"),
            func.min(ItemNota.data_importacao).label("desde"),
        )
        .filter(ItemNota.status == "Pendente")
        .group_by(ItemNota.numero_nota)
        .order_by("desde")
        .limit(80)
        .all()
    )
    receb_pend_conf = [
        _receb_item(r.numero_nota, r.fornecedor, r.quem, "Importou", r.desde, r.desde, agora)
        for r in receb_pend_conf_rows
    ]

    receb_conferidas_rows = (
        db.session.query(
            ItemNota.numero_nota,
            func.max(ItemNota.fornecedor).label("fornecedor"),
            func.max(ItemNota.usuario_conferencia).label("quem"),
            func.max(ItemNota.fim_conferencia).label("ts"),
        )
        .filter(ItemNota.fim_conferencia.isnot(None), func.date(ItemNota.fim_conferencia) == hoje)
        .group_by(ItemNota.numero_nota)
        .order_by(desc("ts"))
        .limit(80)
        .all()
    )
    receb_conferidas = [
        _receb_item(r.numero_nota, r.fornecedor, r.quem, "Conferiu", r.ts, r.ts, agora)
        for r in receb_conferidas_rows
    ]

    receb_pend_lanc_rows = (
        db.session.query(
            ItemNota.numero_nota,
            func.max(ItemNota.fornecedor).label("fornecedor"),
            func.max(ItemNota.usuario_conferencia).label("quem"),
            func.max(ItemNota.fim_conferencia).label("ts"),
            func.min(ItemNota.fim_conferencia).label("desde"),
        )
        .filter(ItemNota.status == "Concluído")
        .group_by(ItemNota.numero_nota)
        .order_by("desde")
        .limit(80)
        .all()
    )
    receb_pend_lanc = [
        _receb_item(r.numero_nota, r.fornecedor, r.quem, "Conferiu", r.ts, r.desde or r.ts, agora)
        for r in receb_pend_lanc_rows
    ]

    receb_lancadas_rows = (
        db.session.query(
            ItemNota.numero_nota,
            func.max(ItemNota.fornecedor).label("fornecedor"),
            func.max(ItemNota.numero_lancamento).label("num_lanc"),
            func.max(ItemNota.data_lancamento).label("ts"),
        )
        .filter(ItemNota.status == "Lançado", func.date(ItemNota.data_lancamento) == hoje)
        .group_by(ItemNota.numero_nota)
        .order_by(desc("ts"))
        .limit(80)
        .all()
    )
    receb_lancadas = [
        _receb_item(
            r.numero_nota, r.fornecedor, None, "", r.ts, r.ts, agora,
            extra=("Lçto " + str(r.num_lanc)) if r.num_lanc else "",
        )
        for r in receb_lancadas_rows
    ]

    # ---- EXPEDICAO (consolidado FAT + ST por status) ----
    def _cons_fat(o, ts):
        return _cons_item(
            o.cod_ordem_fat, o.cliente, "FAT", o.numero_nf,
            o.dt_previsao_entrega, o.created_at, ts, agora,
        )

    def _cons_st(o, ts):
        return _cons_item(
            o.cod_ordem_compra, o.fornecedor, "OC", o.numero_nf,
            o.dt_prevista_entrega, o.created_at, ts, agora,
        )

    def _query_fat(status):
        q = ExpedicaoOrdemFat.query.filter(ExpedicaoOrdemFat.status == status)
        if status == STATUS_EXP_FATURADO_SEM_CONF:
            q = q.filter(ExpedicaoOrdemFat.cod_ordem_fat >= FAT_SEM_CONF_COD_MINIMO)
        return q

    def _cons_categoria(status, ts_attr, reverse, limite=120):
        fat_rows = _query_fat(status).limit(limite).all()
        st_rows = (
            ExpedicaoOrdemST.query.filter(ExpedicaoOrdemST.status == status).limit(limite).all()
        )
        itens = [_cons_fat(o, getattr(o, ts_attr, None)) for o in fat_rows]
        itens += [_cons_st(o, getattr(o, ts_attr, None)) for o in st_rows]
        itens.sort(key=lambda x: x["ts"] or "", reverse=reverse)
        return itens

    def _cons_total(status):
        return (
            _query_fat(status).count()
            + ExpedicaoOrdemST.query.filter(ExpedicaoOrdemST.status == status).count()
        )

    exp_fat_pendente = ExpedicaoOrdemFat.query.filter(ExpedicaoOrdemFat.status == STATUS_EXP_PENDENTE).count()
    exp_st_pendente = ExpedicaoOrdemST.query.filter(ExpedicaoOrdemST.status == STATUS_EXP_PENDENTE).count()

    # Pendentes e conferidos: mais antigos primeiro (destaca o que esta parado).
    cat_pendente = _cons_categoria(STATUS_EXP_PENDENTE, "created_at", False)
    cat_conferido = _cons_categoria(STATUS_EXP_CONFERIDO, "created_at", False)
    # Faturados: mais recentes primeiro.
    cat_fat_sem_conf = _cons_categoria(STATUS_EXP_FATURADO_SEM_CONF, "faturado_at", True)
    cat_faturado = _cons_categoria(STATUS_EXP_FATURADO, "faturado_at", True)

    # Expedidos: consolidado apenas do dia (evita listas gigantes na TV).
    fat_exp_hoje = (
        ExpedicaoOrdemFat.query.filter(func.date(ExpedicaoOrdemFat.expedido_at) == hoje)
        .order_by(ExpedicaoOrdemFat.expedido_at.desc()).limit(120).all()
    )
    st_exp_hoje = (
        ExpedicaoOrdemST.query.filter(func.date(ExpedicaoOrdemST.expedido_at) == hoje)
        .order_by(ExpedicaoOrdemST.expedido_at.desc()).limit(120).all()
    )
    cat_expedido = (
        [_cons_fat(o, o.expedido_at) for o in fat_exp_hoje]
        + [_cons_st(o, o.expedido_at) for o in st_exp_hoje]
    )
    cat_expedido.sort(key=lambda x: x["ts"] or "", reverse=True)


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
            "categorias": {
                "pendente_conf": {"total": int(recebimento_pendente), "lista": receb_pend_conf},
                "conferidas": {"total": len(receb_conferidas), "lista": receb_conferidas},
                "pendente_lanc": {"total": int(recebimento_lancar), "lista": receb_pend_lanc},
                "lancadas": {"total": int(lancadas_hoje), "lista": receb_lancadas},
            },
        },
        "expedicao": {
            "pendente_total": int(exp_fat_pendente + exp_st_pendente),
            "resumo": {
                "fat_pendente": int(exp_fat_pendente),
                "st_pendente": int(exp_st_pendente),
            },
            "categorias": {
                "pendente": {"total": int(_cons_total(STATUS_EXP_PENDENTE)), "lista": cat_pendente},
                "conferido": {"total": int(_cons_total(STATUS_EXP_CONFERIDO)), "lista": cat_conferido},
                "faturado_sem_conf": {"total": int(_cons_total(STATUS_EXP_FATURADO_SEM_CONF)), "lista": cat_fat_sem_conf},
                "faturado": {"total": int(_cons_total(STATUS_EXP_FATURADO)), "lista": cat_faturado},
                "expedido": {"total": len(cat_expedido), "lista": cat_expedido},
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
    return render_template("painel_tv.html", show_compras=True)


@painel_tv_bp.route("/painel/recebimento-expedicao")
def painel_tv_rec_exp_page():
    return render_template("painel_tv.html", show_compras=False)


@painel_tv_bp.route("/api/painel/indicadores")
def painel_tv_indicadores():
    try:
        dados = _coletar_indicadores()
    except Exception:
        db.session.rollback()
        raise
    return jsonify(dados)


# ---------------------------------------------------------------------------
# OCs atrasadas (ERP Postgres via modulo de compras) com cache em memoria.
# A consulta ao ERP e pesada; o painel publico faz polling, entao guardamos
# o resultado por alguns segundos para nao sobrecarregar o banco.
# ---------------------------------------------------------------------------
_oc_cache = {"data": None, "ts": None}
_oc_lock = threading.Lock()


def _oc_cache_ttl() -> int:
    try:
        return int(os.environ.get("PAINEL_OC_CACHE_TTL", "90"))
    except (TypeError, ValueError):
        return 90


def _oc_atraso_dias() -> int:
    """Dias em aberto a partir dos quais uma OC e considerada atrasada."""
    try:
        return int(os.environ.get("PAINEL_OC_ATRASO_DIAS", "30"))
    except (TypeError, ValueError):
        return 30


def _to_date(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return datetime.fromisoformat(str(value)[:10]).date()
    except (TypeError, ValueError):
        return None


def _oc_janela_dias() -> int:
    """Janela de recencia (dias) para 'atrasadas' — evita OCs legadas."""
    try:
        return int(os.environ.get("PAINEL_OC_JANELA_DIAS", "90"))
    except (TypeError, ValueError):
        return 90


def _coletar_ocs_atrasadas() -> dict:
    from ..compras.services import compras_service

    hoje = datetime.now().date()
    limite_semana = hoje + timedelta(days=1)
    rows = compras_service.ordens_compra_entregas(limite=3000)

    atrasadas = []
    semana = []
    for h in rows:
        prev = _to_date(h.get("previsao_entrega"))
        if not prev:
            continue
        item = {
            "oc": h.get("cod_ordem_compra"),
            "fornecedor": (h.get("fornecedor") or "").strip(),
            "status": (h.get("status_oc_nome") or "").strip(),
            "previsao": prev.isoformat(),
        }
        if prev < hoje:
            item["dias"] = (hoje - prev).days
            atrasadas.append(item)
        elif prev <= limite_semana:
            item["dias"] = (prev - hoje).days
            semana.append(item)

    atrasadas.sort(key=lambda x: x["previsao"])          # mais antiga (mais atrasada) primeiro
    semana.sort(key=lambda x: x["previsao"])             # mais proxima primeiro
    return {
        "atrasadas": {"total": len(atrasadas), "lista": atrasadas[:150]},
        "semana": {"total": len(semana), "lista": semana[:150]},
        "gerado_em": datetime.now().isoformat(),
    }


@painel_tv_bp.route("/api/painel/compras")
def painel_tv_compras():
    agora = datetime.now()
    ttl = _oc_cache_ttl()
    with _oc_lock:
        cache_ok = (
            _oc_cache["data"] is not None
            and _oc_cache["ts"] is not None
            and (agora - _oc_cache["ts"]).total_seconds() < ttl
        )
        if cache_ok:
            return jsonify(_oc_cache["data"])
    try:
        dados = _coletar_ocs_atrasadas()
        with _oc_lock:
            _oc_cache["data"] = dados
            _oc_cache["ts"] = agora
        return jsonify(dados)
    except Exception:
        current_app.logger.exception("Falha ao buscar OCs (entregas) para o painel")
        # Se ha cache antigo, devolve ele; senao, retorna vazio com aviso.
        if _oc_cache["data"] is not None:
            return jsonify({**_oc_cache["data"], "stale": True})
        return jsonify({
            "atrasadas": {"total": 0, "lista": []},
            "semana": {"total": 0, "lista": []},
            "erro": "indisponivel",
        })
