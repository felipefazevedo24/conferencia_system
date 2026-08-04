"""Cobrança/follow-up de pendências de expedição (a "cobradora" da Bia).

A cada abertura da Conferência de Expedição (ou quando consultado), este serviço:

  * Sincroniza as pendências atuais do assistente (todos os cards do insights)
    numa tabela de acompanhamento (ExpedicaoCobranca);
  * Devolve a próxima pendência "vencida" para a Bia perguntar o motivo do
    atraso — só para quem é da Logística ou Admin;
  * Registra a resposta do operador (motivo) e o histórico de interações;
  * Refaz o follow-up a cada 24h enquanto a pendência continuar em aberto;
  * Marca automaticamente como resolvida a pendência que saiu da lista.

O motivo/histórico também fica visível na tela da conferência da ordem.
"""
from __future__ import annotations

import os
import unicodedata
from datetime import datetime, timedelta

from flask import current_app

from ..extensions import db
from ..models import ExpedicaoCobranca, ExpedicaoCobrancaLog, ExpedicaoRomaneio
from . import expedicao_assistente_service as svc


# Intervalo entre cobranças da mesma pendência.
INTERVALO_COBRANCA = timedelta(hours=24)

_SEVERIDADE_ORDEM = {"alta": 0, "media": 1, "baixa": 2}


# --------------------------------------------------------------------------- #
# Permissão: só Logística ou Admin conversam com a cobradora.
# --------------------------------------------------------------------------- #
def _norm(texto: str) -> str:
    txt = unicodedata.normalize("NFKD", str(texto or ""))
    txt = "".join(c for c in txt if not unicodedata.combining(c))
    return txt.strip().casefold()


def pode_cobrar(role: str | None) -> bool:
    """True se o papel pode responder às cobranças (Logística ou Admin)."""
    r = _norm(role)
    return "admin" in r or r == "logistica"


# --------------------------------------------------------------------------- #
# Sincronização das pendências atuais -> tabela de acompanhamento.
# --------------------------------------------------------------------------- #
def _mapa_pendencias_atuais() -> dict:
    """(ref_tipo, ref_id) -> snapshot da pendência mais severa daquele item."""
    insights = svc._coletar_insights()
    mapa: dict[tuple[str, str], dict] = {}
    for card in insights:
        sev = card.get("severidade", "media")
        for it in card.get("itens", []):
            tipo = str(it.get("tipo") or "").strip()
            codigo = str(it.get("codigo") or "").strip()
            if not tipo or not codigo:
                continue
            chave = (tipo, codigo)
            atual = mapa.get(chave)
            if atual is None or _SEVERIDADE_ORDEM.get(sev, 1) < _SEVERIDADE_ORDEM.get(
                atual["severidade"], 1
            ):
                mapa[chave] = {
                    "categoria": card.get("chave", ""),
                    "titulo": card.get("titulo", ""),
                    "referencia": it.get("referencia", "") or "",
                    "numero_nf": it.get("numero_nf", "") or "",
                    "severidade": sev,
                }
    return mapa


# --------------------------------------------------------------------------- #
# Corte de ativação: a Bia só cobra pendências surgidas de hoje em diante.
# --------------------------------------------------------------------------- #
def _cutoff_file() -> str:
    try:
        base = current_app.instance_path
    except Exception:
        base = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "instance",
        )
    return os.path.join(base, "bia_cobranca_cutoff.txt")


def _cutoff_registrado() -> bool:
    try:
        return os.path.exists(_cutoff_file())
    except Exception:
        return False


def _registrar_cutoff(agora: datetime) -> None:
    try:
        caminho = _cutoff_file()
        os.makedirs(os.path.dirname(caminho), exist_ok=True)
        with open(caminho, "w", encoding="utf-8") as fh:
            fh.write(agora.isoformat())
    except Exception:
        pass


def sincronizar() -> None:
    """Cria/atualiza cobranças a partir das pendências atuais e resolve as que
    saíram da lista. Best-effort: nunca lança.

    Regra do usuário: a Bia ignora TODO o backlog atual e só cobra pendências
    que surgirem de hoje em diante. Na primeira sincronização (ativação), todas
    as pendências existentes são marcadas como ``ignorada`` (acompanhadas, mas
    nunca cobradas). A partir daí, só pendências novas geram cobrança.
    """
    try:
        mapa = _mapa_pendencias_atuais()
        agora = datetime.now()
        seeded = _cutoff_registrado()

        existentes = {
            (c.ref_tipo, c.ref_id): c
            for c in ExpedicaoCobranca.query.all()
        }

        if not seeded:
            # Ativação: congela o backlog atual como "ignorada" (não cobra nada
            # do que já está pendente hoje).
            for (tipo, codigo), snap in mapa.items():
                cob = existentes.get((tipo, codigo))
                if cob is None:
                    cob = ExpedicaoCobranca(
                        ref_tipo=tipo, ref_id=codigo, criada_em=agora,
                    )
                    db.session.add(cob)
                cob.status = "ignorada"
                cob.proxima_cobranca_em = None
                cob.categoria = snap["categoria"]
                cob.titulo = snap["titulo"]
                cob.referencia = snap["referencia"]
                cob.numero_nf = snap["numero_nf"]
                cob.severidade = snap["severidade"]
                cob.atualizada_em = agora
            # Rows pré-existentes (deploy anterior) também viram backlog.
            for chave, cob in existentes.items():
                if chave not in mapa and cob.status != "resolvida":
                    cob.status = "ignorada"
                    cob.atualizada_em = agora
            db.session.commit()
            _registrar_cutoff(agora)
            return

        # Operação normal: cria/atualiza as pendências atuais.
        for (tipo, codigo), snap in mapa.items():
            cob = existentes.get((tipo, codigo))
            if cob is None:
                # Pendência NOVA (surgiu depois da ativação) -> cobra.
                cob = ExpedicaoCobranca(
                    ref_tipo=tipo,
                    ref_id=codigo,
                    status="aberta",
                    proxima_cobranca_em=None,  # perguntar já
                    criada_em=agora,
                )
                db.session.add(cob)
            elif cob.status == "ignorada":
                # Backlog: continua ignorada, só atualiza o snapshot.
                pass
            elif cob.status == "resolvida":
                # Reabriu: voltou a ficar pendente.
                cob.status = "aberta"
                cob.resolvida_em = None
                cob.proxima_cobranca_em = None
            # Atualiza o snapshot.
            cob.categoria = snap["categoria"]
            cob.titulo = snap["titulo"]
            cob.referencia = snap["referencia"]
            cob.numero_nf = snap["numero_nf"]
            cob.severidade = snap["severidade"]
            cob.atualizada_em = agora

        # Resolve as que saíram da lista (menos o backlog ignorado).
        for chave, cob in existentes.items():
            if chave not in mapa and cob.status not in ("resolvida", "ignorada"):
                cob.status = "resolvida"
                cob.resolvida_em = agora
                cob.atualizada_em = agora

        db.session.commit()
    except Exception:
        db.session.rollback()


# --------------------------------------------------------------------------- #
# Próxima cobrança a fazer.
# --------------------------------------------------------------------------- #
def _pergunta_para(cob: ExpedicaoCobranca) -> str:
    ref = f" ({cob.referencia})" if cob.referencia else ""
    alvo = f"{cob.titulo or 'pendência'} {cob.ref_id}{ref}".strip()
    if cob.motivo:
        return (
            f"Sobre {alvo}: da última vez você me disse \"{cob.motivo}\". "
            "Teve alguma novidade? Já resolveu ou continua pendente?"
        )
    return (
        f"Notei que {alvo} está pendente. Pode me dizer o motivo do atraso? "
        "Vou registrar aqui pra acompanharmos. 🙂"
    )


def _cobranca_dict(cob: ExpedicaoCobranca) -> dict:
    return {
        "ref_tipo": cob.ref_tipo,
        "ref_id": cob.ref_id,
        "titulo": cob.titulo,
        "referencia": cob.referencia,
        "numero_nf": cob.numero_nf,
        "categoria": cob.categoria,
        "severidade": cob.severidade,
        "motivo_anterior": cob.motivo or "",
        "ja_respondida": bool(cob.motivo),
        "pergunta": _pergunta_para(cob),
    }


def proxima_para_cobrar(role: str | None, marcar: bool = True) -> dict | None:
    """Sincroniza e devolve a próxima pendência vencida a cobrar (ou None).

    Se marcar=True, registra que a Bia perguntou agora (log + agenda a próxima
    para daqui a 24h), garantindo no máximo uma cobrança por dia por pendência.
    """
    if not pode_cobrar(role):
        return None

    sincronizar()
    agora = datetime.now()

    cob = (
        ExpedicaoCobranca.query
        .filter(ExpedicaoCobranca.status.in_(("aberta", "respondida")))
        .filter(
            db.or_(
                ExpedicaoCobranca.proxima_cobranca_em.is_(None),
                ExpedicaoCobranca.proxima_cobranca_em <= agora,
            )
        )
        .order_by(
            ExpedicaoCobranca.proxima_cobranca_em.is_(None).desc(),
            ExpedicaoCobranca.severidade.asc(),
            ExpedicaoCobranca.proxima_cobranca_em.asc(),
        )
        .first()
    )
    if cob is None:
        return None

    dados = _cobranca_dict(cob)

    if marcar:
        try:
            if cob.primeira_cobranca_em is None:
                cob.primeira_cobranca_em = agora
            cob.ultima_cobranca_em = agora
            cob.proxima_cobranca_em = agora + INTERVALO_COBRANCA
            cob.atualizada_em = agora
            db.session.add(
                ExpedicaoCobrancaLog(
                    cobranca_id=cob.id,
                    tipo="cobranca",
                    texto=dados["pergunta"],
                    autor="Bia",
                    criado_em=agora,
                )
            )
            db.session.commit()
        except Exception:
            db.session.rollback()

    return dados


# --------------------------------------------------------------------------- #
# Registrar resposta / adiar.
# --------------------------------------------------------------------------- #
def _buscar(ref_tipo: str, ref_id: str) -> ExpedicaoCobranca | None:
    return ExpedicaoCobranca.query.filter_by(
        ref_tipo=str(ref_tipo or "").strip(),
        ref_id=str(ref_id or "").strip(),
    ).first()


def registrar_resposta(ref_tipo: str, ref_id: str, texto: str, autor: str = "") -> bool:
    """Salva o motivo informado pelo operador e agenda o próximo follow-up."""
    texto = str(texto or "").strip()
    if not texto:
        return False
    try:
        cob = _buscar(ref_tipo, ref_id)
        if cob is None:
            return False
        agora = datetime.now()
        cob.motivo = texto[:1000]
        cob.status = "respondida"
        cob.respondida_por = autor or ""
        cob.respondida_em = agora
        cob.proxima_cobranca_em = agora + INTERVALO_COBRANCA
        cob.atualizada_em = agora
        db.session.add(
            ExpedicaoCobrancaLog(
                cobranca_id=cob.id,
                tipo="resposta",
                texto=texto[:1000],
                autor=autor or "",
                criado_em=agora,
            )
        )
        db.session.commit()
        return True
    except Exception:
        db.session.rollback()
        return False


def adiar(ref_tipo: str, ref_id: str, autor: str = "") -> bool:
    """Adia (snooze) a cobrança para o próximo ciclo sem registrar motivo."""
    try:
        cob = _buscar(ref_tipo, ref_id)
        if cob is None:
            return False
        agora = datetime.now()
        cob.proxima_cobranca_em = agora + INTERVALO_COBRANCA
        cob.atualizada_em = agora
        db.session.commit()
        return True
    except Exception:
        db.session.rollback()
        return False


# --------------------------------------------------------------------------- #
# Histórico (para a tela da conferência e para a Bia).
# --------------------------------------------------------------------------- #
def historico(ref_tipo: str, ref_id: str) -> dict:
    """Devolve o motivo atual e o histórico de interações de uma pendência."""
    cob = _buscar(ref_tipo, ref_id)
    if cob is None:
        return {"encontrada": False, "motivo": "", "logs": []}

    def _fmt(dt):
        try:
            return dt.strftime("%d/%m/%Y %H:%M")
        except Exception:
            return ""

    return {
        "encontrada": True,
        "status": cob.status,
        "motivo": cob.motivo or "",
        "titulo": cob.titulo,
        "referencia": cob.referencia,
        "respondida_por": cob.respondida_por or "",
        "respondida_em": _fmt(cob.respondida_em) if cob.respondida_em else "",
        "logs": [
            {
                "tipo": log.tipo,
                "texto": log.texto,
                "autor": log.autor or "",
                "em": _fmt(log.criado_em),
            }
            for log in cob.logs
        ],
    }


# --------------------------------------------------------------------------- #
# CC-e: marcar como emitida (o webhook do Teams é de mão única).
# --------------------------------------------------------------------------- #
def marcar_cce_feita(numero_romaneio: str, autor: str = "") -> dict:
    """Marca a CC-e de um romaneio como emitida e resolve a cobrança."""
    numero = str(numero_romaneio or "").strip()
    if not numero:
        return {"ok": False, "erro": "Informe o número do romaneio."}
    try:
        rom = ExpedicaoRomaneio.query.filter_by(numero_romaneio=numero).first()
        if rom is None:
            return {"ok": False, "erro": f"Romaneio {numero} não encontrado."}
        if not rom.cce_modalidade_pendente:
            return {"ok": False, "erro": f"O romaneio {numero} não tem CC-e pendente."}
        agora = datetime.now()
        rom.cce_modalidade_pendente = False
        # Resolve a cobrança associada (se houver).
        cob = _buscar("romaneio", numero)
        if cob is not None:
            cob.status = "resolvida"
            cob.resolvida_em = agora
            cob.atualizada_em = agora
            db.session.add(
                ExpedicaoCobrancaLog(
                    cobranca_id=cob.id,
                    tipo="sistema",
                    texto=f"CC-e marcada como emitida por {autor or 'operador'}.",
                    autor=autor or "",
                    criado_em=agora,
                )
            )
        db.session.commit()
        return {"ok": True, "mensagem": f"CC-e do romaneio {numero} marcada como emitida. ✅"}
    except Exception:
        db.session.rollback()
        return {"ok": False, "erro": "Não foi possível marcar a CC-e agora."}
