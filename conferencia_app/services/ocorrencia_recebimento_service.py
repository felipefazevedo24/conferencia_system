"""Divergências da conferência física tratadas por Compras.

Fluxo:
1. O conferente grava a conferência com divergência (ou registra divergência
   de produto, ou recusa a mercadoria) -> abrir_ocorrencia() cria (ou
   complementa) a ocorrência da NF e avisa no Teams, no mesmo grupo da
   divergência XML x pedido.
2. Compras, pelo painel, diz o que será feito e vai atualizando a situação
   (atualizar_ocorrencia). Cada mudança fica na linha do tempo.
3. Se a decisão for "Devolver totalmente", a ocorrência vai para
   AguardandoFiscal e o Fiscal informa a recusa da NF ou a NF de devolução
   (registrar_etapa_fiscal) - só aí ela fecha.
4. Ocorrência parada há N dias sem atualização gera lembrete no Teams
   (enviar_lembretes, rodado pelo scripts/lembrete_ocorrencias_recebimento.py).

Enquanto Compras não decide o que será feito, a NF não pode ser lançada
(lancamento_bloqueado). O aviso ao Teams nunca derruba a conferência:
falhou, fica no log.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta

from flask import current_app, has_request_context, request

from ..extensions import db
from ..models import (
    ItemNota,
    LogDivergencia,
    OcorrenciaRecebimento,
    OcorrenciaRecebimentoEvento,
    OcorrenciaRecebimentoItem,
)
from ..tempo import agora_br

STATUS = {
    "Aberta": "Aberta",
    "EmTratativa": "Em tratativa",
    "AguardandoFornecedor": "Aguardando fornecedor",
    "AguardandoFiscal": "Aguardando fiscal (devolução)",
    "Resolvida": "Resolvida",
    "Cancelada": "Cancelada",
}
STATUS_FINAIS = {"Resolvida", "Cancelada"}
# O que Compras pode escolher no painel. AguardandoFiscal é consequência da
# "Devolução total", não uma escolha.
STATUS_ESCOLHA_COMPRAS = ("Aberta", "EmTratativa", "AguardandoFornecedor", "Resolvida", "Cancelada")

ACOES = {
    "Reposicao": "Cobrar reposição do fornecedor",
    "NotaDebito": "Nota de débito / abatimento",
    "DevolucaoTotal": "Devolver totalmente",
    "AceitarDiferenca": "Aceitar a diferença",
    "Outro": "Outro",
}
TIPOS_FISCAL = {"Recusa": "Recusa da NF", "NFDevolucao": "NF de devolução emitida"}
ORIGENS = {"Conferencia": "Conferência física", "DivergenciaProduto": "Divergência de produto", "Recusa": "Recusa da mercadoria"}

DIAS_LEMBRETE_PADRAO = 3


# --------------------------------------------------------------------------
# Leitura
# --------------------------------------------------------------------------

def _fmt_qtd(valor) -> str:
    if valor is None:
        return "—"
    try:
        numero = float(valor)
    except (TypeError, ValueError):
        return str(valor)
    texto = f"{numero:,.3f}".rstrip("0").rstrip(".")
    return texto.replace(",", "X").replace(".", ",").replace("X", ".")


def _iso(valor) -> str | None:
    return valor.isoformat(timespec="minutes") if isinstance(valor, datetime) else (valor.isoformat() if isinstance(valor, date) else None)


def item_para_dict(item: OcorrenciaRecebimentoItem) -> dict:
    diferenca = None
    if item.qtd_esperada is not None and item.qtd_contada is not None and (item.qtd_esperada or item.qtd_contada):
        diferenca = (item.qtd_contada or 0) - (item.qtd_esperada or 0)
    return {
        "id": item.id,
        "descricao": item.descricao or "",
        "qtd_esperada": item.qtd_esperada,
        "qtd_contada": item.qtd_contada,
        "diferenca": diferenca,
        "motivo_tipo": item.motivo_tipo or "",
        "destino_fisico": item.destino_fisico or "",
        "evidencia_path": item.evidencia_path or "",
        "registrado_por": item.registrado_por or "",
        "registrado_em": _iso(item.registrado_em),
    }


def evento_para_dict(ev: OcorrenciaRecebimentoEvento) -> dict:
    return {
        "id": ev.id,
        "em": _iso(ev.em),
        "usuario": ev.usuario or "",
        "tipo": ev.tipo,
        "status_anterior": ev.status_anterior,
        "status_anterior_label": STATUS.get(ev.status_anterior or "", ev.status_anterior or ""),
        "status_novo": ev.status_novo,
        "status_novo_label": STATUS.get(ev.status_novo or "", ev.status_novo or ""),
        "acao": ev.acao,
        "acao_label": ACOES.get(ev.acao or "", ev.acao or ""),
        "comentario": ev.comentario or "",
    }


def ocorrencia_para_dict(oc: OcorrenciaRecebimento, completo: bool = False) -> dict:
    agora = agora_br()
    ultimo_comentario = next((e.comentario for e in reversed(oc.eventos or []) if e.comentario), "")
    dados = {
        "id": oc.id,
        "numero_nota": oc.numero_nota,
        "cnpj_emitente": oc.cnpj_emitente or "",
        "fornecedor": oc.fornecedor or "",
        "pedido_compra": oc.pedido_compra or "",
        "origem": oc.origem,
        "origem_label": ORIGENS.get(oc.origem, oc.origem),
        "status": oc.status,
        "status_label": STATUS.get(oc.status, oc.status),
        "finalizada": oc.status in STATUS_FINAIS,
        "acao": oc.acao or "",
        "acao_label": ACOES.get(oc.acao or "", ""),
        "responsavel": oc.responsavel or "",
        "previsao": oc.previsao.isoformat() if oc.previsao else None,
        "aberta_em": _iso(oc.aberta_em),
        "aberta_por": oc.aberta_por or "",
        "atualizado_em": _iso(oc.atualizado_em),
        "atualizado_por": oc.atualizado_por or "",
        "resolvida_em": _iso(oc.resolvida_em),
        "dias_aberta": (agora - oc.aberta_em).days if oc.aberta_em else 0,
        "dias_sem_atualizacao": (agora - oc.atualizado_em).days if oc.atualizado_em else 0,
        "qtd_itens": len(oc.itens or []),
        "ultimo_comentario": ultimo_comentario,
        "aguardando_fiscal": oc.status == "AguardandoFiscal",
        "aguardando_decisao": _aguardando_decisao(oc),
        "fiscal": {
            "tipo": oc.fiscal_tipo,
            "tipo_label": TIPOS_FISCAL.get(oc.fiscal_tipo or "", ""),
            "numero_nf": oc.fiscal_numero_nf or "",
            "data": oc.fiscal_data.isoformat() if oc.fiscal_data else None,
            "observacao": oc.fiscal_observacao or "",
            "por": oc.fiscal_por or "",
            "em": _iso(oc.fiscal_em),
        } if oc.fiscal_tipo else None,
    }
    if completo:
        dados["nf_lancada"] = _nf_lancada(oc)
        dados["itens"] = [item_para_dict(i) for i in oc.itens]
        dados["eventos"] = [evento_para_dict(e) for e in oc.eventos]
    return dados


def listar_ocorrencias(status: str = "", busca: str = "", somente_abertas: bool = False) -> list[dict]:
    query = OcorrenciaRecebimento.query
    if status:
        query = query.filter(OcorrenciaRecebimento.status == status)
    elif somente_abertas:
        query = query.filter(~OcorrenciaRecebimento.status.in_(STATUS_FINAIS))
    termo = str(busca or "").strip()
    if termo:
        like = f"%{termo}%"
        query = query.filter(db.or_(
            OcorrenciaRecebimento.numero_nota.ilike(like),
            OcorrenciaRecebimento.fornecedor.ilike(like),
            OcorrenciaRecebimento.pedido_compra.ilike(like),
        ))
    linhas = query.order_by(OcorrenciaRecebimento.aberta_em.desc()).limit(500).all()
    return [ocorrencia_para_dict(o) for o in linhas]


def contagem_por_status() -> dict:
    contagem = {chave: 0 for chave in STATUS}
    for status, total in db.session.query(OcorrenciaRecebimento.status, db.func.count()).group_by(OcorrenciaRecebimento.status):
        contagem[status] = total
    return contagem


def obter_ocorrencia(ocorrencia_id: int) -> dict | None:
    oc = db.session.get(OcorrenciaRecebimento, ocorrencia_id)
    return ocorrencia_para_dict(oc, completo=True) if oc else None


def ocorrencia_da_nota(numero_nota: str, cnpj_emitente: str = "") -> OcorrenciaRecebimento | None:
    """A ocorrência mais recente da NF (aberta primeiro)."""
    query = OcorrenciaRecebimento.query.filter_by(numero_nota=str(numero_nota or "").strip())
    cnpj = re.sub(r"\D", "", str(cnpj_emitente or ""))
    if cnpj:
        query = query.filter(db.or_(OcorrenciaRecebimento.cnpj_emitente == cnpj, OcorrenciaRecebimento.cnpj_emitente.is_(None)))
    abertas = [o for o in query.order_by(OcorrenciaRecebimento.id.desc()).all()]
    return next((o for o in abertas if o.status not in STATUS_FINAIS), abertas[0] if abertas else None)


def _nf_lancada(oc: OcorrenciaRecebimento) -> bool:
    query = ItemNota.query.filter_by(numero_nota=oc.numero_nota, status="Lançado")
    if oc.cnpj_emitente:
        query = query.filter(ItemNota.cnpj_emitente == oc.cnpj_emitente)
    return query.first() is not None


def _aguardando_decisao(oc: OcorrenciaRecebimento) -> bool:
    """Decidir = escolher "o que será feito" (ou encerrar/cancelar)."""
    return oc.status not in STATUS_FINAIS and not oc.acao


def lancamento_bloqueado(numero_nota: str, cnpj_emitente: str = "") -> str | None:
    """Mensagem de bloqueio se a NF tem divergência sem decisão de Compras."""
    oc = ocorrencia_da_nota(numero_nota, cnpj_emitente)
    if oc and _aguardando_decisao(oc):
        return (f"NF {numero_nota} com divergência no recebimento aguardando decisão de Compras "
                "(Compras > Divergências de recebimento). O lançamento libera quando Compras definir o que será feito.")
    return None


# --------------------------------------------------------------------------
# Abertura (chamada pela conferência)
# --------------------------------------------------------------------------

def itens_da_conferencia(numero_nota: str, tentativa_numero: int | None = None) -> list[dict]:
    """Itens divergentes gravados pela conferência (LogDivergencia) - os da
    última tentativa, que é a que o conferente gravou."""
    query = LogDivergencia.query.filter_by(numero_nota=str(numero_nota))
    if tentativa_numero is not None:
        query = query.filter_by(tentativa_numero=tentativa_numero)
    return [
        {
            "log_divergencia_id": log.id,
            "descricao": log.item_descricao,
            "qtd_esperada": log.qtd_esperada,
            "qtd_contada": log.qtd_contada,
            "motivo_tipo": log.motivo_tipo,
            "destino_fisico": log.destino_fisico,
            "evidencia_path": log.evidencia_path,
            "registrado_por": log.usuario_erro,
        }
        for log in query.order_by(LogDivergencia.id).all()
    ]


def _dados_da_nf(numero_nota: str, cnpj_emitente: str = "") -> dict:
    query = ItemNota.query.filter_by(numero_nota=str(numero_nota))
    cnpj = re.sub(r"\D", "", str(cnpj_emitente or ""))
    if cnpj:
        query = query.filter(ItemNota.cnpj_emitente == cnpj)
    itens = query.all()
    pedidos = sorted({str(i.pedido_compra).strip() for i in itens if str(i.pedido_compra or "").strip()})
    return {
        "cnpj_emitente": cnpj or (str(itens[0].cnpj_emitente or "") if itens else ""),
        "fornecedor": str(itens[0].fornecedor or "") if itens else "",
        "pedido_compra": ", ".join(pedidos),
    }


def abrir_ocorrencia(numero_nota: str, itens: list[dict], usuario: str, origem: str = "Conferencia",
                     cnpj_emitente: str = "", comentario: str = "") -> OcorrenciaRecebimento | None:
    """Cria a ocorrência da NF (ou junta os itens novos na que já está
    aberta) e avisa no Teams. Faz o próprio commit. Nunca levanta: quem chama
    é a conferência, que não pode falhar por causa disto."""
    try:
        if not itens:
            return None
        dados_nf = _dados_da_nf(numero_nota, cnpj_emitente)
        oc = ocorrencia_da_nota(numero_nota, dados_nf["cnpj_emitente"])
        nova = oc is None or oc.status in STATUS_FINAIS
        agora = agora_br()
        if nova:
            oc = OcorrenciaRecebimento(
                numero_nota=str(numero_nota), cnpj_emitente=dados_nf["cnpj_emitente"] or None,
                fornecedor=dados_nf["fornecedor"][:160], pedido_compra=dados_nf["pedido_compra"][:200],
                origem=origem if origem in ORIGENS else "Conferencia", status="Aberta",
                aberta_em=agora, aberta_por=usuario, atualizado_em=agora, atualizado_por=usuario,
            )
            db.session.add(oc)
            db.session.flush()
        ja_registrados = {i.log_divergencia_id for i in oc.itens if i.log_divergencia_id}
        novos = [i for i in itens if not i.get("log_divergencia_id") or i["log_divergencia_id"] not in ja_registrados]
        if not novos:
            # Mesma gravação chamada de novo: nada a acrescentar nem a avisar.
            db.session.rollback()
            return None if nova else oc
        for dados in novos:
            oc.itens.append(OcorrenciaRecebimentoItem(
                log_divergencia_id=dados.get("log_divergencia_id"),
                descricao=str(dados.get("descricao") or "")[:300],
                qtd_esperada=dados.get("qtd_esperada"), qtd_contada=dados.get("qtd_contada"),
                motivo_tipo=str(dados.get("motivo_tipo") or "")[:80], destino_fisico=str(dados.get("destino_fisico") or "")[:80],
                evidencia_path=str(dados.get("evidencia_path") or "")[:300],
                registrado_por=str(dados.get("registrado_por") or usuario)[:100], registrado_em=agora,
            ))
        oc.eventos.append(OcorrenciaRecebimentoEvento(
            em=agora, usuario=usuario, tipo="Abertura" if nova else "NovosItens", status_novo=oc.status,
            comentario=(comentario or f"{len(novos)} item(ns) com divergência registrado(s) pelo recebimento.")[:1000],
        ))
        db.session.commit()
        _avisar_teams("aberta" if nova else "novos_itens", oc, itens_novos=novos)
        return oc
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Ocorrência de recebimento: falha ao abrir para a NF %s", numero_nota)
        return None


# --------------------------------------------------------------------------
# Compras e Fiscal
# --------------------------------------------------------------------------

def _data(valor) -> date | None:
    texto = str(valor or "").strip()
    if not texto:
        return None
    try:
        return date.fromisoformat(texto[:10])
    except ValueError:
        raise ValueError("Data inválida: use o formato AAAA-MM-DD.")


def atualizar_ocorrencia(ocorrencia_id: int, usuario: str, *, status: str | None = None, acao: str | None = None,
                         responsavel: str | None = None, previsao: str | None = None, comentario: str = "") -> dict | None:
    oc = db.session.get(OcorrenciaRecebimento, ocorrencia_id)
    if not oc:
        return None
    if oc.status in STATUS_FINAIS:
        raise ValueError("Esta ocorrência já foi encerrada.")
    comentario = str(comentario or "").strip()
    novo_status = str(status or oc.status).strip()
    nova_acao = (str(acao).strip() if acao is not None else (oc.acao or "")) or None
    if nova_acao and nova_acao not in ACOES:
        raise ValueError("Escolha o que será feito.")
    if novo_status not in STATUS_ESCOLHA_COMPRAS and novo_status != oc.status:
        raise ValueError("Situação inválida.")
    if nova_acao == "Outro" and not comentario and (oc.acao != "Outro"):
        raise ValueError("Em \"Outro\", descreva no comentário o que será feito.")
    if novo_status == "Cancelada" and len(comentario) < 5:
        raise ValueError("Informe no comentário por que a ocorrência foi cancelada.")

    # Devolução total: quem fecha é o Fiscal, informando a recusa ou a NF de devolução.
    if nova_acao == "DevolucaoTotal" and novo_status != "Cancelada":
        if novo_status == "Resolvida":
            raise ValueError("Na devolução total quem encerra é o Fiscal, ao informar a recusa ou a NF de devolução.")
        novo_status = "AguardandoFiscal"
    elif oc.status == "AguardandoFiscal" and novo_status == "AguardandoFiscal":
        novo_status = "EmTratativa"  # mudou de ideia: saiu da devolução total

    previsao_data = _data(previsao) if previsao is not None else oc.previsao
    responsavel_txt = (str(responsavel).strip()[:100] if responsavel is not None else oc.responsavel) or None
    mudou = (novo_status != oc.status or nova_acao != oc.acao or responsavel_txt != oc.responsavel
             or previsao_data != oc.previsao or comentario)
    if not mudou:
        raise ValueError("Nada mudou: altere a situação, o que será feito ou escreva um comentário.")

    status_anterior = oc.status
    agora = agora_br()
    oc.status, oc.acao, oc.responsavel, oc.previsao = novo_status, nova_acao, responsavel_txt, previsao_data
    oc.atualizado_em, oc.atualizado_por = agora, usuario
    if novo_status == "Resolvida":
        oc.resolvida_em = agora
    oc.eventos.append(OcorrenciaRecebimentoEvento(
        em=agora, usuario=usuario, tipo="Atualizacao", status_anterior=status_anterior, status_novo=novo_status,
        acao=nova_acao, comentario=comentario[:1000] or None,
    ))
    db.session.commit()
    if novo_status == "AguardandoFiscal" and status_anterior != "AguardandoFiscal":
        _avisar_teams("aguardando_fiscal", oc)
    elif novo_status in STATUS_FINAIS:
        _avisar_teams("encerrada", oc)
    return ocorrencia_para_dict(oc, completo=True)


def registrar_etapa_fiscal(ocorrencia_id: int, usuario: str, *, tipo: str, numero_nf: str = "", data: str = "",
                           observacao: str = "") -> dict | None:
    oc = db.session.get(OcorrenciaRecebimento, ocorrencia_id)
    if not oc:
        return None
    if oc.status != "AguardandoFiscal":
        raise ValueError("Esta ocorrência não está aguardando o Fiscal (só a devolução total passa por esta etapa).")
    tipo = str(tipo or "").strip()
    if tipo not in TIPOS_FISCAL:
        raise ValueError("Informe se foi recusa da NF ou NF de devolução.")
    numero_nf = str(numero_nf or "").strip()[:30]
    if tipo == "NFDevolucao" and not numero_nf:
        raise ValueError("Informe o número da NF de devolução.")
    # A NF de devolução referencia a entrada: sem lançamento não há o que devolver.
    if tipo == "NFDevolucao" and not _nf_lancada(oc):
        raise ValueError("A NF precisa estar lançada antes da devolução. Lance a NF no Documento de entrada e depois informe a NF de devolução.")
    data_fiscal = _data(data) or agora_br().date()
    agora = agora_br()
    oc.fiscal_tipo, oc.fiscal_numero_nf, oc.fiscal_data = tipo, numero_nf or None, data_fiscal
    oc.fiscal_observacao = str(observacao or "").strip()[:500] or None
    oc.fiscal_por, oc.fiscal_em = usuario, agora
    status_anterior = oc.status
    oc.status, oc.resolvida_em = "Resolvida", agora
    oc.atualizado_em, oc.atualizado_por = agora, usuario
    detalhe = TIPOS_FISCAL[tipo] + (f" nº {numero_nf}" if numero_nf else "") + f" em {data_fiscal.strftime('%d/%m/%Y')}"
    if oc.fiscal_observacao:
        detalhe += f". {oc.fiscal_observacao}"
    oc.eventos.append(OcorrenciaRecebimentoEvento(
        em=agora, usuario=usuario, tipo="Fiscal", status_anterior=status_anterior, status_novo="Resolvida",
        acao=oc.acao, comentario=detalhe[:1000],
    ))
    db.session.commit()
    _avisar_teams("encerrada", oc)
    return ocorrencia_para_dict(oc, completo=True)


def estornar_resolucao(ocorrencia_id: int, usuario: str, motivo: str) -> dict | None:
    """Reabre uma ocorrência Resolvida. Se quem fechou foi a etapa do Fiscal,
    ela volta para AguardandoFiscal sem os dados fiscais (que ficam no
    histórico); senão volta para EmTratativa com a mesma ação."""
    oc = db.session.get(OcorrenciaRecebimento, ocorrencia_id)
    if not oc:
        return None
    if oc.status != "Resolvida":
        raise ValueError("Só uma ocorrência resolvida pode ser estornada.")
    motivo = str(motivo or "").strip()
    if len(motivo) < 5:
        raise ValueError("Informe o motivo do estorno.")
    comentario = f"Resolução estornada: {motivo}"
    if oc.fiscal_tipo:
        comentario += (f" (desfeito: {TIPOS_FISCAL.get(oc.fiscal_tipo, oc.fiscal_tipo)}"
                       + (f" nº {oc.fiscal_numero_nf}" if oc.fiscal_numero_nf else "") + f", registrado por {oc.fiscal_por})")
        novo_status = "AguardandoFiscal"
        oc.fiscal_tipo = oc.fiscal_numero_nf = oc.fiscal_data = oc.fiscal_observacao = oc.fiscal_por = oc.fiscal_em = None
    else:
        novo_status = "EmTratativa"
    agora = agora_br()
    oc.status, oc.resolvida_em = novo_status, None
    oc.atualizado_em, oc.atualizado_por = agora, usuario
    oc.eventos.append(OcorrenciaRecebimentoEvento(
        em=agora, usuario=usuario, tipo="Reabertura", status_anterior="Resolvida", status_novo=novo_status,
        acao=oc.acao, comentario=comentario[:1000],
    ))
    db.session.commit()
    _avisar_teams("reaberta", oc)
    return ocorrencia_para_dict(oc, completo=True)


# --------------------------------------------------------------------------
# Estornos da NF
# --------------------------------------------------------------------------

def ao_estornar_conferencia(numero_nota: str, cnpj_emitente: str, usuario: str, motivo: str) -> None:
    """A contagem que gerou a divergência deixou de valer.
    Sem decisão de Compras: cancela (a nova conferência abre outra, se houver
    diferença) - senão a ocorrência velha travaria o lançamento de uma NF
    que talvez esteja certa. Com decisão: mantém e pede para reavaliar.
    Faz o próprio commit e nunca levanta (quem chama é o estorno)."""
    try:
        oc = ocorrencia_da_nota(numero_nota, cnpj_emitente)
        if not oc or oc.status in STATUS_FINAIS:
            return
        agora = agora_br()
        status_anterior = oc.status
        comentario = f"Conferência estornada por {usuario}: {motivo}"
        if _aguardando_decisao(oc):
            oc.status, oc.resolvida_em = "Cancelada", agora
            comentario += ". Ocorrência cancelada automaticamente; se a nova conferência achar diferença, abre outra."
        else:
            comentario += ". Compras já tinha decidido: reavalie a tratativa depois da nova conferência."
        oc.atualizado_em, oc.atualizado_por = agora, usuario
        oc.eventos.append(OcorrenciaRecebimentoEvento(
            em=agora, usuario=usuario, tipo="Estorno", status_anterior=status_anterior, status_novo=oc.status,
            acao=oc.acao, comentario=comentario[:1000],
        ))
        db.session.commit()
        _avisar_teams("encerrada" if oc.status == "Cancelada" else "conferencia_estornada", oc)
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Ocorrência de recebimento: falha ao registrar estorno de conferência da NF %s", numero_nota)


def estorno_lancamento_bloqueado(numero_nota: str, cnpj_emitente: str = "") -> str | None:
    """A NF de devolução já saiu contra este lançamento: estornar a entrada
    deixaria a devolução sem origem."""
    query = OcorrenciaRecebimento.query.filter_by(numero_nota=str(numero_nota or "").strip(), fiscal_tipo="NFDevolucao")
    cnpj = re.sub(r"\D", "", str(cnpj_emitente or ""))
    if cnpj:
        query = query.filter(db.or_(OcorrenciaRecebimento.cnpj_emitente == cnpj, OcorrenciaRecebimento.cnpj_emitente.is_(None)))
    oc = query.order_by(OcorrenciaRecebimento.id.desc()).first()
    if oc:
        return (f"Não é possível estornar: a NF de devolução nº {oc.fiscal_numero_nf} já foi emitida contra este "
                "lançamento (Compras > Divergências de recebimento).")
    return None


def ao_estornar_lancamento(numero_nota: str, cnpj_emitente: str, usuario: str, motivo: str) -> None:
    """Só registra no histórico da ocorrência. Nunca levanta."""
    try:
        oc = ocorrencia_da_nota(numero_nota, cnpj_emitente)
        if not oc:
            return
        oc.eventos.append(OcorrenciaRecebimentoEvento(
            em=agora_br(), usuario=usuario, tipo="Estorno", status_anterior=oc.status, status_novo=oc.status,
            acao=oc.acao, comentario=f"Lançamento da NF estornado por {usuario}: {motivo}"[:1000],
        ))
        db.session.commit()
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Ocorrência de recebimento: falha ao registrar estorno de lançamento da NF %s", numero_nota)


# --------------------------------------------------------------------------
# Lembrete
# --------------------------------------------------------------------------

def enviar_lembretes(agora: datetime | None = None, dias: int | None = None) -> dict:
    """Avisa no Teams a ocorrência aberta há `dias` sem atualização de
    Compras/Fiscal, e repete a cada `dias` enquanto continuar parada."""
    agora = agora or agora_br()
    dias = int(dias or current_app.config.get("OCORRENCIA_RECEBIMENTO_LEMBRETE_DIAS") or DIAS_LEMBRETE_PADRAO)
    janela = timedelta(days=dias)
    enviados = []
    for oc in OcorrenciaRecebimento.query.filter(~OcorrenciaRecebimento.status.in_(STATUS_FINAIS)).all():
        referencia = max(d for d in (oc.atualizado_em, oc.ultimo_lembrete_em) if d)
        if agora - referencia < janela:
            continue
        oc.ultimo_lembrete_em = agora
        oc.eventos.append(OcorrenciaRecebimentoEvento(
            em=agora, usuario="sistema", tipo="Lembrete", status_novo=oc.status,
            comentario=f"Lembrete no Teams: {(agora - oc.atualizado_em).days} dia(s) sem atualização.",
        ))
        db.session.commit()
        _avisar_teams("lembrete", oc, sync=True)
        enviados.append(oc.id)
    return {"enviados": len(enviados), "ids": enviados, "dias": dias}


# --------------------------------------------------------------------------
# Teams
# --------------------------------------------------------------------------

def link_ocorrencia(oc: OcorrenciaRecebimento) -> str:
    base = str(current_app.config.get("PUBLIC_BASE_URL") or "").strip().rstrip("/")
    if not base and has_request_context():
        base = request.url_root.rstrip("/")
    return f"{base}/compras/divergencias-recebimento?id={oc.id}" if base else ""


def _linha_item(dados: dict) -> str:
    esperado, contado = dados.get("qtd_esperada"), dados.get("qtd_contada")
    partes = [str(dados.get("descricao") or "Item")]
    if esperado or contado:
        diferenca = (contado or 0) - (esperado or 0)
        rotulo = "faltam" if diferenca < 0 else "sobram"
        partes.append(f"esperado {_fmt_qtd(esperado)} · contado {_fmt_qtd(contado)} ({rotulo} {_fmt_qtd(abs(diferenca))})")
    if dados.get("motivo_tipo"):
        partes.append(str(dados["motivo_tipo"]))
    return " — ".join(partes)


def _avisar_teams(evento: str, oc: OcorrenciaRecebimento, itens_novos: list[dict] | None = None, sync: bool = False) -> None:
    from . import teams_service

    try:
        linhas = [_linha_item(i) for i in (itens_novos if itens_novos is not None else [item_para_dict(i) for i in oc.itens])]
        teams_service.notificar_ocorrencia_recebimento(
            evento,
            numero_nota=oc.numero_nota,
            fornecedor=oc.fornecedor or "",
            pedido_compra=oc.pedido_compra or "",
            origem=ORIGENS.get(oc.origem, oc.origem),
            situacao=STATUS.get(oc.status, oc.status),
            acao=ACOES.get(oc.acao or "", ""),
            responsavel=oc.responsavel or "",
            ultimo_comentario=next((e.comentario for e in reversed(oc.eventos) if e.comentario and e.tipo != "Lembrete"), ""),
            dias_sem_atualizacao=(agora_br() - oc.atualizado_em).days if oc.atualizado_em else 0,
            linhas=linhas,
            link=link_ocorrencia(oc),
            sync=sync,
        )
    except Exception:
        current_app.logger.exception("Ocorrência de recebimento: falha ao avisar no Teams (NF %s)", oc.numero_nota)
