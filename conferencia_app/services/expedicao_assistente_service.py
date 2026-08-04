"""Assistente de Expedição (inteligência operacional offline).

Analisa o estado atual da Conferência de Expedição (ordens de faturamento FAT,
serviço de terceiro ST, registros de expedição e romaneios) e produz:

  * Uma lista PRIORIZADA de pendências ("o que lembrar / cobrar"), com uma
    orientação de próximo passo para cada situação ("o que fazer");
  * Respostas a perguntas em linguagem natural feitas pelo operador, tipo
    "o que falta expedir hoje?" — SEM depender de LLM externo: é um
    interpretador de intenções determinístico que consulta os mesmos dados.

Nenhum dado sai do servidor e não há custo por uso.
"""
from __future__ import annotations

import unicodedata
from datetime import datetime

from ..extensions import db
from ..models import (
    ExpedicaoConferenciaSimples,
    ExpedicaoOrdemFat,
    ExpedicaoOrdemST,
    ExpedicaoRomaneio,
)
from . import expedicao_fat_service as fat_svc
from . import expedicao_st_service as st_svc


# "Faturado sem conferência": esconde o backlog antigo (mesma regra da fila da
# tela — ver expedicao_fat_routes.FAT_SEM_CONF_COD_MINIMO).
FAT_SEM_CONF_COD_MINIMO = 1594

# Uma ordem/romaneio "parado" por mais dias que isto vira alerta de baixa
# prioridade (aguardando faturamento / rascunho esquecido).
DIAS_PARADO = 2

# Severidades (menor = mais urgente, usado para ordenar).
_ORDEM_SEVERIDADE = {"alta": 0, "media": 1, "baixa": 2}


# --------------------------------------------------------------------------- #
# Orientações contextuais por status (o "orienta").
# --------------------------------------------------------------------------- #
ORIENTACAO_STATUS = {
    fat_svc.STATUS_PENDENTE: (
        "Faça a conferência cega: conte os itens sem ver a quantidade esperada "
        "e informe pesos e volumes para concluir."
    ),
    fat_svc.STATUS_CONFERIDO: (
        "Conferência concluída. Aguarda o Faturamento emitir a NF."
    ),
    fat_svc.STATUS_FATURADO_SEM_CONF: (
        "⚠️ NF emitida SEM conferência. A conferência cega é obrigatória para "
        "liberar a expedição — confira o material agora."
    ),
    fat_svc.STATUS_FATURADO: (
        "NF emitida. Monte o romaneio (ou registre a expedição) para dar saída."
    ),
    fat_svc.STATUS_EM_ROMANEIO: (
        "A NF já está em um romaneio. Finalize/expedir o romaneio para concluir "
        "a saída."
    ),
    fat_svc.STATUS_EXPEDIDO: (
        "Expedição concluída. Se ainda faltar o canhoto/comprovante de entrega, "
        "anexe-o para finalizar."
    ),
    fat_svc.STATUS_FINALIZADO_SEM_CONF: (
        "Encerrada por um administrador sem conferência física."
    ),
}


def orientacao_por_status(status: str) -> str:
    return ORIENTACAO_STATUS.get(
        status, "Acompanhe a ordem na fila de Conferência de Expedição."
    )


# --------------------------------------------------------------------------- #
# Helpers.
# --------------------------------------------------------------------------- #
def _normalizar(texto: str) -> str:
    """Minúsculas, sem acento, sem espaços nas pontas."""
    txt = (texto or "").strip().lower()
    txt = unicodedata.normalize("NFKD", txt)
    return "".join(c for c in txt if not unicodedata.combining(c))


def _item_fat(ordem: ExpedicaoOrdemFat) -> dict:
    return {
        "tipo": "fat",
        "codigo": str(ordem.cod_ordem_fat or ""),
        "referencia": ordem.cliente or "—",
        "orcamento": ordem.orcamento or "",
        "numero_nf": ordem.numero_nf or "",
        "status": ordem.status,
    }


def _item_st(ordem: ExpedicaoOrdemST) -> dict:
    return {
        "tipo": "st",
        "codigo": str(ordem.cod_ordem_compra or ""),
        "referencia": ordem.fornecedor or "—",
        "orcamento": "",
        "numero_nf": ordem.numero_nf or "",
        "status": ordem.status,
    }


def _fat_visiveis():
    """Ordens FAT ativas (não excluídas)."""
    return ExpedicaoOrdemFat.query.filter_by(excluido=False).all()


def _st_visiveis():
    return ExpedicaoOrdemST.query.filter_by(excluido=False).all()


def _vencida(dt) -> bool:
    if not dt:
        return False
    try:
        return dt.date() < datetime.now().date()
    except Exception:
        return False


def _dias_desde(dt) -> int | None:
    if not dt:
        return None
    try:
        return (datetime.now() - dt).days
    except Exception:
        return None


# --------------------------------------------------------------------------- #
# Núcleo: análise de pendências.
# --------------------------------------------------------------------------- #
def _coletar_insights() -> list[dict]:
    """Percorre o estado do módulo e devolve uma lista de pendências (cards)."""
    fat = _fat_visiveis()
    st = _st_visiveis()

    insights: list[dict] = []

    # 1) Faturado sem conferência (bloqueia a expedição) — ALTA.
    fat_sem_conf = [
        o for o in fat
        if o.status == fat_svc.STATUS_FATURADO_SEM_CONF
        and (o.cod_ordem_fat or 0) >= FAT_SEM_CONF_COD_MINIMO
    ]
    st_sem_conf = [o for o in st if o.status == st_svc.STATUS_FATURADO_SEM_CONF]
    if fat_sem_conf or st_sem_conf:
        itens = [_item_fat(o) for o in fat_sem_conf] + [_item_st(o) for o in st_sem_conf]
        insights.append({
            "chave": "faturado_sem_conf",
            "severidade": "alta",
            "icone": "fa-triangle-exclamation",
            "titulo": "Faturado sem conferência",
            "quantidade": len(itens),
            "orientacao": (
                "NF já emitida sem conferência cega. Confira o material antes de "
                "expedir — a expedição fica travada até a conferência."
            ),
            "acao_filtro": "faturado_sem_conf",
            "itens": itens,
        })

    # 2) Pendente de conferência com previsão de entrega vencida — ALTA.
    fat_pend = [o for o in fat if o.status == fat_svc.STATUS_PENDENTE]
    st_pend = [o for o in st if o.status == st_svc.STATUS_PENDENTE]
    fat_atrasada = [o for o in fat_pend if _vencida(o.dt_previsao_entrega)]
    st_atrasada = [o for o in st_pend if _vencida(o.dt_prevista_entrega)]
    if fat_atrasada or st_atrasada:
        itens = [_item_fat(o) for o in fat_atrasada] + [_item_st(o) for o in st_atrasada]
        insights.append({
            "chave": "pendente_atrasada",
            "severidade": "alta",
            "icone": "fa-clock",
            "titulo": "Conferência atrasada (previsão vencida)",
            "quantidade": len(itens),
            "orientacao": (
                "A previsão de entrega já passou e a ordem ainda não foi "
                "conferida. Priorize a conferência cega destas ordens."
            ),
            "acao_filtro": "pendente",
            "itens": itens,
        })

    # 3) Romaneios com CC-e (carta de correção) pendente — ALTA.
    try:
        romaneios_cce = (
            ExpedicaoRomaneio.query.filter_by(cce_modalidade_pendente=True).all()
        )
    except Exception:
        romaneios_cce = []
    if romaneios_cce:
        insights.append({
            "chave": "cce_pendente",
            "severidade": "alta",
            "icone": "fa-file-pen",
            "titulo": "Carta de correção (CC-e) pendente",
            "quantidade": len(romaneios_cce),
            "orientacao": (
                "Romaneio finalizado com modalidade de frete divergente da NF. "
                "O Faturamento precisa emitir a CC-e."
            ),
            "acao_filtro": "romaneio",
            "itens": [
                {
                    "tipo": "romaneio",
                    "codigo": r.numero_romaneio or str(r.id),
                    "referencia": r.cliente or "—",
                    "orcamento": r.orcamento or "",
                    "numero_nf": "",
                    "status": r.status,
                }
                for r in romaneios_cce
            ],
        })

    # 4) Registros de expedição já expedidos SEM canhoto/comprovante — ALTA.
    try:
        sem_canhoto = (
            ExpedicaoConferenciaSimples.query
            .filter(ExpedicaoConferenciaSimples.status == "Expedido")
            .filter(
                db.or_(
                    ExpedicaoConferenciaSimples.canhoto_file_name.is_(None),
                    ExpedicaoConferenciaSimples.canhoto_file_name == "",
                )
            )
            .all()
        )
    except Exception:
        sem_canhoto = []
    if sem_canhoto:
        insights.append({
            "chave": "sem_canhoto",
            "severidade": "alta",
            "icone": "fa-file-circle-xmark",
            "titulo": "Expedido sem canhoto/comprovante",
            "quantidade": len(sem_canhoto),
            "orientacao": (
                "Material expedido mas sem o canhoto de entrega anexado. "
                "Anexe o comprovante para finalizar o registro."
            ),
            "acao_filtro": "expedido",
            "itens": [
                {
                    "tipo": "registro",
                    "codigo": str(r.id),
                    "referencia": r.nome_cliente or "—",
                    "orcamento": r.orcamento or "",
                    "numero_nf": r.numero_nf or "",
                    "status": r.status,
                }
                for r in sem_canhoto
            ],
        })

    # 5) Pendente de conferência (não atrasada) — MÉDIA.
    fat_pend_ok = [o for o in fat_pend if not _vencida(o.dt_previsao_entrega)]
    st_pend_ok = [o for o in st_pend if not _vencida(o.dt_prevista_entrega)]
    if fat_pend_ok or st_pend_ok:
        itens = [_item_fat(o) for o in fat_pend_ok] + [_item_st(o) for o in st_pend_ok]
        insights.append({
            "chave": "pendente",
            "severidade": "media",
            "icone": "fa-inbox",
            "titulo": "Aguardando conferência",
            "quantidade": len(itens),
            "orientacao": (
                "Ordens na fila para conferência cega. Conte os itens sem ver a "
                "quantidade esperada."
            ),
            "acao_filtro": "pendente",
            "itens": itens,
        })

    # 6) Divergências registradas ainda não expedidas — MÉDIA.
    fat_div = [
        o for o in fat
        if o.divergente and o.status not in (
            fat_svc.STATUS_EXPEDIDO, fat_svc.STATUS_FINALIZADO_SEM_CONF,
        )
    ]
    st_div = [
        o for o in st
        if o.divergente and o.status not in (
            st_svc.STATUS_EXPEDIDO, st_svc.STATUS_FINALIZADO_SEM_CONF,
        )
    ]
    if fat_div or st_div:
        itens = [_item_fat(o) for o in fat_div] + [_item_st(o) for o in st_div]
        insights.append({
            "chave": "divergente",
            "severidade": "media",
            "icone": "fa-not-equal",
            "titulo": "Divergência na conferência",
            "quantidade": len(itens),
            "orientacao": (
                "A contagem física divergiu do esperado. Trate a divergência "
                "antes de liberar a expedição."
            ),
            "acao_filtro": "",
            "itens": itens,
        })

    # 7) Conferido aguardando faturamento há mais de DIAS_PARADO dias — MÉDIA.
    fat_parado = [
        o for o in fat
        if o.status == fat_svc.STATUS_CONFERIDO
        and (_dias_desde(o.conferido_at) or 0) >= DIAS_PARADO
    ]
    st_parado = [
        o for o in st
        if o.status == st_svc.STATUS_CONFERIDO
        and (_dias_desde(o.conferido_at) or 0) >= DIAS_PARADO
    ]
    if fat_parado or st_parado:
        itens = [_item_fat(o) for o in fat_parado] + [_item_st(o) for o in st_parado]
        insights.append({
            "chave": "conferido_parado",
            "severidade": "media",
            "icone": "fa-hourglass-half",
            "titulo": "Conferido, aguardando faturamento",
            "quantidade": len(itens),
            "orientacao": (
                f"Conferência concluída há {DIAS_PARADO}+ dias e a NF ainda não "
                "foi emitida. Cobre o Faturamento."
            ),
            "acao_filtro": "conferido",
            "itens": itens,
        })

    # 8) Faturado aguardando romaneio/expedição — BAIXA.
    fat_fat = [o for o in fat if o.status == fat_svc.STATUS_FATURADO]
    st_fat = [o for o in st if o.status == st_svc.STATUS_FATURADO]
    if fat_fat or st_fat:
        itens = [_item_fat(o) for o in fat_fat] + [_item_st(o) for o in st_fat]
        insights.append({
            "chave": "faturado",
            "severidade": "baixa",
            "icone": "fa-file-invoice-dollar",
            "titulo": "Faturado, aguardando expedição",
            "quantidade": len(itens),
            "orientacao": (
                "NF emitida. Monte o romaneio ou registre a expedição para dar "
                "saída ao material."
            ),
            "acao_filtro": "faturado",
            "itens": itens,
        })

    # 9) Romaneios em rascunho parados — BAIXA.
    try:
        rascunhos = [
            r for r in ExpedicaoRomaneio.query.filter_by(status="Rascunho").all()
            if (_dias_desde(r.criado_em) or 0) >= DIAS_PARADO
        ]
    except Exception:
        rascunhos = []
    if rascunhos:
        insights.append({
            "chave": "romaneio_rascunho",
            "severidade": "baixa",
            "icone": "fa-pen-ruler",
            "titulo": "Romaneio em rascunho parado",
            "quantidade": len(rascunhos),
            "orientacao": (
                "Romaneio começado mas não finalizado. Conclua para poder "
                "expedir."
            ),
            "acao_filtro": "romaneio",
            "itens": [
                {
                    "tipo": "romaneio",
                    "codigo": r.numero_romaneio or str(r.id),
                    "referencia": r.cliente or "—",
                    "orcamento": r.orcamento or "",
                    "numero_nf": "",
                    "status": r.status,
                }
                for r in rascunhos
            ],
        })

    insights.sort(key=lambda c: _ORDEM_SEVERIDADE.get(c["severidade"], 9))
    return insights


def analisar(limite_itens: int = 8) -> dict:
    """Retorna o panorama do assistente: saudação, contadores e pendências.

    limite_itens: quantos itens de exemplo devolver por card (o restante fica
    apenas no contador)."""
    insights = _coletar_insights()

    total_pendencias = sum(c["quantidade"] for c in insights)
    urgentes = sum(c["quantidade"] for c in insights if c["severidade"] == "alta")

    # Trunca a amostra de itens de cada card (mantém o total no contador).
    for card in insights:
        card["itens_amostra"] = card["itens"][:limite_itens]
        card["itens_ocultos"] = max(0, card["quantidade"] - len(card["itens_amostra"]))
        card.pop("itens", None)

    if total_pendencias == 0:
        resumo = "Tudo em dia por aqui! Nenhuma pendência de expedição no momento."
    elif urgentes:
        resumo = (
            f"Você tem {total_pendencias} pendência(s) de expedição, sendo "
            f"{urgentes} urgente(s). Comece pelas de prioridade alta."
        )
    else:
        resumo = (
            f"Você tem {total_pendencias} pendência(s) de expedição em "
            "andamento. Nada urgente no momento."
        )

    return {
        "resumo": resumo,
        "total_pendencias": total_pendencias,
        "urgentes": urgentes,
        "pendencias": insights,
        "gerado_em": datetime.now().isoformat(),
    }


# --------------------------------------------------------------------------- #
# Chat offline por intenção (o "conversar").
# --------------------------------------------------------------------------- #
SUGESTOES = [
    "O que falta expedir hoje?",
    "O que está atrasado?",
    "Tem algo faturado sem conferência?",
    "Quais romaneios estão sem canhoto?",
    "Tem carta de correção pendente?",
]


def _resposta(texto: str, pendencias: list[dict] | None = None) -> dict:
    return {
        "resposta": texto,
        "pendencias": pendencias or [],
        "sugestoes": SUGESTOES,
    }


def _card_por_chave(chaves: tuple[str, ...]) -> list[dict]:
    dados = analisar()
    return [c for c in dados["pendencias"] if c["chave"] in chaves]


def _texto_de_cards(cards: list[dict], vazio: str) -> str:
    if not cards:
        return vazio
    partes = []
    for c in cards:
        partes.append(f"• {c['titulo']}: {c['quantidade']}. {c['orientacao']}")
    return "\n\n".join(partes)


def _buscar_ordem(numero: str) -> dict | None:
    """Localiza uma ordem/NF pelo número informado e devolve status + orientação."""
    num = numero.strip()
    ordem = None
    tipo = None
    try:
        cod = int(num)
        ordem = ExpedicaoOrdemFat.query.filter_by(cod_ordem_fat=cod, excluido=False).first()
        tipo = "Ordem de faturamento"
    except ValueError:
        pass
    if ordem is None:
        ordem = ExpedicaoOrdemFat.query.filter_by(numero_nf=num, excluido=False).first()
        tipo = "Ordem de faturamento (por NF)"
    if ordem is None:
        ordem_st = ExpedicaoOrdemST.query.filter_by(cod_ordem_compra=num, excluido=False).first()
        if ordem_st is None:
            ordem_st = ExpedicaoOrdemST.query.filter_by(numero_nf=num, excluido=False).first()
        if ordem_st is not None:
            return {
                "tipo": "Ordem de compra (ST)",
                "referencia": ordem_st.fornecedor or "—",
                "status": ordem_st.status,
                "orientacao": orientacao_por_status(ordem_st.status),
            }
        return None
    return {
        "tipo": tipo,
        "referencia": ordem.cliente or "—",
        "status": ordem.status,
        "orientacao": orientacao_por_status(ordem.status),
    }


def responder(pergunta: str) -> dict:
    """Interpreta uma pergunta em linguagem natural e responde com dados reais.

    100% offline: mapeia a intenção da pergunta para uma consulta ao estado do
    módulo. Não usa nenhum modelo de linguagem externo."""
    q = _normalizar(pergunta)
    if not q:
        dados = analisar()
        return _resposta(dados["resumo"], dados["pendencias"])

    # Ajuda / capacidades.
    if any(t in q for t in ("ajuda", "o que voce faz", "como funciona", "help", "quem e voce")):
        return _resposta(
            "Sou o assistente da Conferência de Expedição. Eu acompanho o que "
            "está pendente e oriento o próximo passo. Pergunte, por exemplo: "
            "\"o que falta expedir hoje?\", \"o que está atrasado?\", \"tem "
            "algo faturado sem conferência?\", \"quais romaneios estão sem "
            "canhoto?\" ou informe o número de uma ordem/NF para ver a situação."
        )

    # Número de ordem / NF (sequência de dígitos com 3+ caracteres).
    import re
    m = re.search(r"\b(\d{3,})\b", q)
    if m:
        info = _buscar_ordem(m.group(1))
        if info:
            return _resposta(
                f"{info['tipo']} {m.group(1)} — {info['referencia']}\n\n"
                f"Status: {info['status']}.\n\n{info['orientacao']}"
            )
        return _resposta(
            f"Não encontrei nenhuma ordem ou NF com o número {m.group(1)} na "
            "Conferência de Expedição."
        )

    # Atrasados / prazo vencido.
    if any(t in q for t in ("atras", "vencid", "prazo", "atrasad")):
        cards = _card_por_chave(("pendente_atrasada",))
        return _resposta(
            _texto_de_cards(cards, "Nada atrasado: nenhuma conferência com previsão de entrega vencida."),
            cards,
        )

    # Faturado sem conferência.
    if "sem conferenc" in q or "faturado sem" in q:
        cards = _card_por_chave(("faturado_sem_conf",))
        return _resposta(
            _texto_de_cards(cards, "Nenhuma NF faturada sem conferência no momento."),
            cards,
        )

    # Canhoto / comprovante.
    if any(t in q for t in ("canhoto", "comprovante", "assinatur")):
        cards = _card_por_chave(("sem_canhoto",))
        return _resposta(
            _texto_de_cards(cards, "Todos os materiais expedidos já têm canhoto anexado."),
            cards,
        )

    # CC-e / carta de correção.
    if any(t in q for t in ("cc-e", "cce", "carta de correc", "correcao", "correcao")):
        cards = _card_por_chave(("cce_pendente",))
        return _resposta(
            _texto_de_cards(cards, "Nenhuma carta de correção (CC-e) pendente."),
            cards,
        )

    # Divergências.
    if "diverg" in q:
        cards = _card_por_chave(("divergente",))
        return _resposta(
            _texto_de_cards(cards, "Nenhuma divergência de conferência em aberto."),
            cards,
        )

    # Romaneios.
    if "romaneio" in q:
        cards = _card_por_chave(("cce_pendente", "romaneio_rascunho"))
        return _resposta(
            _texto_de_cards(cards, "Nenhuma pendência de romaneio no momento."),
            cards,
        )

    # Pendente de conferência.
    if any(t in q for t in ("conferir", "pendente de conferenc", "aguardando conferenc", "falta conferir")):
        cards = _card_por_chave(("pendente", "pendente_atrasada"))
        return _resposta(
            _texto_de_cards(cards, "Nada aguardando conferência no momento."),
            cards,
        )

    # Panorama geral ("o que falta expedir", "resumo", "pendencias", "hoje").
    if any(t in q for t in (
        "falta expedir", "expedir hoje", "resumo", "pendenc", "panorama",
        "o que", "situacao", "status", "hoje", "faltando",
    )):
        dados = analisar()
        return _resposta(dados["resumo"], dados["pendencias"])

    # Fallback: panorama geral com dica.
    dados = analisar()
    return _resposta(
        dados["resumo"] + "\n\n(Não entendi exatamente a pergunta — te mostrei "
        "o panorama geral. Toque em uma das sugestões abaixo.)",
        dados["pendencias"],
    )
