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
from ..models import ExpedicaoOrdemFat, ExpedicaoOrdemST, ItemNota, PlannerBoard

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
        q = ExpedicaoOrdemFat.query.filter(
            ExpedicaoOrdemFat.status == status,
            ExpedicaoOrdemFat.excluido.is_(False),
        )
        if status == STATUS_EXP_FATURADO_SEM_CONF:
            q = q.filter(ExpedicaoOrdemFat.cod_ordem_fat >= FAT_SEM_CONF_COD_MINIMO)
        return q

    def _query_st(status):
        return ExpedicaoOrdemST.query.filter(
            ExpedicaoOrdemST.status == status,
            ExpedicaoOrdemST.excluido.is_(False),
        )

    def _cons_categoria(status, ts_attr, reverse, limite=120):
        fat_rows = _query_fat(status).limit(limite).all()
        st_rows = _query_st(status).limit(limite).all()
        itens = [_cons_fat(o, getattr(o, ts_attr, None)) for o in fat_rows]
        itens += [_cons_st(o, getattr(o, ts_attr, None)) for o in st_rows]
        itens.sort(key=lambda x: x["ts"] or "", reverse=reverse)
        return itens

    def _cons_total(status):
        return _query_fat(status).count() + _query_st(status).count()

    exp_fat_pendente = _query_fat(STATUS_EXP_PENDENTE).count()
    exp_st_pendente = _query_st(STATUS_EXP_PENDENTE).count()

    # Pendentes e conferidos: mais antigos primeiro (destaca o que esta parado).
    cat_pendente = _cons_categoria(STATUS_EXP_PENDENTE, "created_at", False)
    cat_conferido = _cons_categoria(STATUS_EXP_CONFERIDO, "created_at", False)
    # Faturados: mais recentes primeiro.
    cat_fat_sem_conf = _cons_categoria(STATUS_EXP_FATURADO_SEM_CONF, "faturado_at", True)
    cat_faturado = _cons_categoria(STATUS_EXP_FATURADO, "faturado_at", True)

    # Expedidos: consolidado apenas do dia (evita listas gigantes na TV).
    fat_exp_hoje = (
        ExpedicaoOrdemFat.query.filter(
            func.date(ExpedicaoOrdemFat.expedido_at) == hoje,
            ExpedicaoOrdemFat.excluido.is_(False),
        )
        .order_by(ExpedicaoOrdemFat.expedido_at.desc()).limit(120).all()
    )
    st_exp_hoje = (
        ExpedicaoOrdemST.query.filter(
            func.date(ExpedicaoOrdemST.expedido_at) == hoje,
            ExpedicaoOrdemST.excluido.is_(False),
        )
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
        ExpedicaoOrdemFat.query.filter(ExpedicaoOrdemFat.excluido.is_(False))
        .order_by(desc(ExpedicaoOrdemFat.created_at)).limit(6).all()
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
        ExpedicaoOrdemST.query.filter(ExpedicaoOrdemST.excluido.is_(False))
        .order_by(desc(ExpedicaoOrdemST.created_at)).limit(6).all()
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


def _coletar_planejamento() -> dict:
    """Resumo do quadro de Planejamento de Tarefas para o painel de TV."""
    hoje = date.today()
    board = PlannerBoard.query.order_by(PlannerBoard.id.asc()).first()
    if not board:
        return {
            "kpis": {"total": 0, "andamento": 0, "concluidas": 0, "atrasadas": 0},
            "colunas": [],
            "atrasadas": [],
        }

    colunas_data = []
    total = concluidas = andamento = atrasadas = 0
    lista_atrasadas: list[dict] = []
    colunas = sorted(board.colunas, key=lambda c: (c.order_index, c.id))
    for col in colunas:
        cards = sorted(col.cards, key=lambda c: (c.order_index, c.id))
        n = len(cards)
        total += n
        col_atrasadas = 0
        cards_data = []
        if col.is_done:
            concluidas += n
        else:
            andamento += n
        for c in cards:
            atrasada = bool(c.prazo and c.prazo < hoje and not col.is_done)
            if atrasada:
                col_atrasadas += 1
            card_info = {
                "titulo": c.titulo,
                "responsavel": c.responsavel or "",
                "prioridade": c.prioridade or "",
                "prazo": c.prazo.strftime("%d/%m") if c.prazo else "",
                "atrasada": atrasada,
                "dias_atraso": (hoje - c.prazo).days if atrasada else 0,
            }
            cards_data.append(card_info)
            if atrasada:
                lista_atrasadas.append(
                    {
                        "titulo": c.titulo,
                        "coluna": col.titulo,
                        "responsavel": c.responsavel or "",
                        "prioridade": c.prioridade or "",
                        "prazo": c.prazo.strftime("%d/%m") if c.prazo else "",
                        "dias": (hoje - c.prazo).days if c.prazo else 0,
                    }
                )
        atrasadas += col_atrasadas
        colunas_data.append(
            {
                "titulo": col.titulo,
                "color": col.color or "#0f62c9",
                "is_done": bool(col.is_done),
                "total": n,
                "atrasadas": col_atrasadas,
                "cards": cards_data[:30],
            }
        )

    lista_atrasadas.sort(key=lambda x: x["dias"], reverse=True)
    return {
        "kpis": {
            "total": total,
            "andamento": andamento,
            "concluidas": concluidas,
            "atrasadas": atrasadas,
        },
        "colunas": colunas_data,
        "atrasadas": lista_atrasadas[:40],
    }


# Resumo do modulo Comex pro painel de TV: os 10 status granulares do
# workflow (ver comex_service.MODULOS_SEQUENCIA) agrupados em 3 "baskets"
# resumidos - mesmo modelo em colunas tipo kanban do Planejamento de
# Tarefas acima, so que a fonte dos cards e o ComexProcesso em vez do
# PlannerBoard.
_COMEX_BASKETS = [
    {"titulo": "Preparação", "color": "#fbbf24", "status": ("OC", "PO", "Cotacao", "Instrucao")},
    {"titulo": "Trânsito", "color": "#3da5f4", "status": ("Coleta", "EmTransito", "Desembarque", "Desembaraco")},
    {"titulo": "Entregue", "color": "#34d399", "status": ("Transporte", "NFCambio")},
]
_COMEX_STATUS_LABEL = {
    "OC": "OC",
    "PO": "PO",
    "Cotacao": "Cotação",
    "Instrucao": "Instrução",
    "Coleta": "Coleta",
    "EmTransito": "Em Trânsito",
    "Desembarque": "Desembarque",
    "Desembaraco": "Desembaraço",
    "Transporte": "Transporte",
    "NFCambio": "NF/Câmbio",
}
_COMEX_STATUS_PARA_BASKET = {
    status: idx
    for idx, basket in enumerate(_COMEX_BASKETS)
    for status in basket["status"]
}


def _dias_uteis_desde(momento: datetime, agora: datetime) -> int:
    """Conta dias uteis (seg-sex, sem feriados - so pula sabado/domingo)
    inteiros decorridos entre `momento` e `agora`, por data corrida (nao
    por bloco de 24h) - se `momento` foi sexta e hoje e' segunda, conta 1
    dia util (o fim de semana nao conta), nao 3 dias corridos."""
    if agora <= momento:
        return 0
    dia = momento.date()
    fim = agora.date()
    dias_uteis = 0
    while dia < fim:
        dia += timedelta(days=1)
        if dia.weekday() < 5:  # 0=segunda ... 4=sexta
            dias_uteis += 1
    return dias_uteis


def _coletar_comex() -> dict:
    """Resumo do modulo Comex pro painel de TV, nos 3 baskets acima. Cada
    card mostra a OC (numero da Ordem de Compra - cai pro ID OP quando o
    processo e' manual, sem OC vinculada, ver comex_service.
    criar_processo_manual), fornecedor, pagador do frete, o status
    detalhado (dentro da basket) e a ultima interacao (comentario OU
    mudanca de processo - estornos nao contam, ver comex_service.
    estornar) - `ultima_interacao_atrasada` marca quando ja fazem mais de
    3 dias UTEIS (fim de semana nao conta, ver _dias_uteis_desde), pro
    front destacar em vermelho. `processo_id` vai junto pra buscar os
    itens da PO ao clicar no card (ver painel_tv_comex_itens)."""
    from ..models import ComexComentario, ComexProcesso

    # OCs combinadas na PO de outro processo somem da lista principal -
    # mesma logica de listar_processos() do modulo Comex (comex_service).
    processos = (
        ComexProcesso.query
        .filter(ComexProcesso.po_processo_principal_id.is_(None))
        .all()
    )

    # Data do comentario mais recente por processo - comex_service.estornar
    # ja garante que ComexProcesso.atualizado_em nao avanca num estorno
    # (so em mudancas de verdade), entao so falta somar os comentarios pra
    # ter a "ultima interacao" completa.
    ultimo_comentario_por_processo = dict(
        db.session.query(
            ComexComentario.processo_id,
            func.max(ComexComentario.criado_em),
        )
        .group_by(ComexComentario.processo_id)
        .all()
    )

    def _ultima_interacao(p) -> datetime | None:
        candidatos = [p.atualizado_em, ultimo_comentario_por_processo.get(p.id)]
        candidatos = [c for c in candidatos if c is not None]
        return max(candidatos) if candidatos else None

    processos_com_interacao = [(p, _ultima_interacao(p)) for p in processos]
    processos_com_interacao.sort(key=lambda item: item[1] or datetime.min, reverse=True)

    agora = datetime.now()
    colunas_cards: list[list[dict]] = [[] for _ in _COMEX_BASKETS]
    for p, ultima_interacao in processos_com_interacao:
        idx = _COMEX_STATUS_PARA_BASKET.get(p.status_modulo)
        if idx is None:
            continue  # status fora do fluxo modelado (ex.: legado/futuro) - nao exibido na torre
        colunas_cards[idx].append(
            {
                "processo_id": p.id,
                "id_op": p.id_op,
                "oc": str(p.cod_ordem_compra) if p.cod_ordem_compra else p.id_op,
                "fornecedor": p.fornecedor or "",
                "status_detalhado": _COMEX_STATUS_LABEL.get(p.status_modulo, p.status_modulo),
                "pagador_frete": p.pagador_frete or "",
                "ultima_interacao": ultima_interacao.strftime("%d/%m %H:%M") if ultima_interacao else "",
                # Mais de 3 dias UTEIS sem interacao (comentario/mudanca
                # real) - fim de semana nao conta, sinaliza processo parado
                # direto na tela, sem precisar calcular data no front.
                "ultima_interacao_atrasada": bool(ultima_interacao and _dias_uteis_desde(ultima_interacao, agora) > 3),
            }
        )

    total = sum(len(cards) for cards in colunas_cards)
    colunas_data = [
        {
            "titulo": basket["titulo"],
            "color": basket["color"],
            "total": len(cards),
            "cards": cards[:60],
        }
        for basket, cards in zip(_COMEX_BASKETS, colunas_cards)
    ]

    return {"kpis": {"total": total}, "colunas": colunas_data}


@painel_tv_bp.route("/painel")
def painel_tv_page():
    return render_template("painel_tv.html", show_compras=True, show_frota=True)
@painel_tv_bp.route("/painel/recebimento-expedicao")
def painel_tv_rec_exp_page():
    return render_template(
        "painel_tv.html", show_compras=False, show_frota=False, show_planejamento=False, show_comex=False
    )


@painel_tv_bp.route("/painel/comex")
def painel_tv_comex_page():
    """Mesma Torre de Controle, mas o carrossel fica travado so no slide do
    Comex - pra quem so precisa acompanhar esse painel (ex.: TV dedicada do
    setor), sem passar pelas outras secoes. As demais secoes (frota,
    planejamento, compras) ficam desligadas pra nao gastar polling/mapa a
    toa com uma tela que nunca vai aparecer."""
    return render_template(
        "painel_tv.html",
        show_compras=False, show_frota=False, show_planejamento=False,
        only_slide="comex",
    )


@painel_tv_bp.route("/api/painel/indicadores")
def painel_tv_indicadores():
    try:
        dados = _coletar_indicadores()
    except Exception:
        db.session.rollback()
        raise
    return jsonify(dados)


@painel_tv_bp.route("/api/painel/planejamento")
def painel_tv_planejamento():
    try:
        dados = _coletar_planejamento()
    except Exception:
        db.session.rollback()
        raise
    return jsonify(dados)


@painel_tv_bp.route("/api/painel/comex")
def painel_tv_comex():
    try:
        dados = _coletar_comex()
    except Exception:
        db.session.rollback()
        raise
    return jsonify(dados)


@painel_tv_bp.route("/api/painel/comex/<int:processo_id>/itens")
def painel_tv_comex_itens(processo_id):
    """Itens da PO de um processo Comex, pro popup que abre ao clicar num
    card na Torre de Controle - so codigo/descricao/quantidade (sem valor,
    de proposito: e' um painel publico sem login, nao expoe dado
    comercial)."""
    from ..models import ComexPoItem

    try:
        itens = (
            ComexPoItem.query
            .filter_by(processo_id=processo_id)
            .order_by(ComexPoItem.order_index)
            .all()
        )
        dados = [
            {"codigo": it.codigo or "", "descricao": it.descricao or "", "quantidade": it.quantidade}
            for it in itens
        ]
    except Exception:
        db.session.rollback()
        raise
    return jsonify({"itens": dados})


@painel_tv_bp.route("/api/painel/frota")
def painel_tv_frota():
    # Mesma fonte de dados do Mapa da Frota / Rastreamento de Veiculos
    # (autenticados) - aqui exposta sem login, no mesmo padrao publico dos
    # demais endpoints deste painel, para alimentar o mapa ao vivo na TV.
    from .viagem_routes import dados_mapa_frota

    try:
        return jsonify(dados_mapa_frota())
    except Exception:
        current_app.logger.exception("Falha ao buscar frota para o painel TV")
        return jsonify({"base": {}, "veiculos": [], "sem_posicao": 0})


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
