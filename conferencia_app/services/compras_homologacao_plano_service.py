"""Plano de acao da homologacao de fornecedor (F 066 rev. 04) - fase 2.

Corre em paralelo, depois de homologar: os itens que nao pontuaram cheio
(Atende Parcial, Nao Atende e o "Atende" que valeu Parcial por falta de
evidencia) viram itens do plano. O comprador gera o relatorio (IA, ou o
modelo padrao quando a IA nao esta configurada/falha), revisa e manda o
link pro fornecedor, que anexa as evidencias; a Columbia aceita ou recusa
item a item. A nota da homologacao nao muda (decisao de Compras: a
avaliacao e' o retrato de quando foi feita).
"""
from __future__ import annotations

import hashlib
import json
import re
import secrets
from datetime import date, datetime, timedelta

import requests
from flask import current_app

from ..extensions import db
from ..models import (
    ComprasHomologacaoFornecedor as Homologacao,
    ComprasHomologacaoPlanoAcao as Plano,
    ComprasHomologacaoPlanoEvidencia as PlanoEvidencia,
    ComprasHomologacaoPlanoItem as PlanoItem,
)
from ..tempo import agora_br
from . import compras_homologacao_form_rev04 as form_r04
from . import compras_homologacao_service as svc

PRAZO_PADRAO_DIAS = 30
MOTIVO_PARCIAL = "Atende Parcial"
MOTIVO_NAO_ATENDE = "Não Atende"
MOTIVO_SEM_EVIDENCIA = "Sem evidência"


def _txt(valor) -> str:
    return str(valor if valor is not None else "").strip()


# ── Pendencias que viram plano ──────────────────────────────────────────
def pendencias(homologacao: Homologacao) -> list[dict]:
    """Itens da avaliacao rev. 04 que nao pontuaram cheio, na ordem do
    formulario. Formulario antigo nao gera plano."""
    if not svc.eh_rev04(homologacao):
        return []
    respostas = {(r.secao, r.item): r.resposta for r in homologacao.respostas}
    com_evidencia = {(e.secao, e.item) for e in homologacao.evidencias}
    iso = svc.iso_valido(homologacao)
    lista = []
    for secao in form_r04.SECOES:
        if iso and secao["pula_com_iso"]:
            continue
        for i, texto in enumerate(secao["itens"], start=1):
            resposta = respostas.get((secao["chave"], i))
            efetiva = form_r04.resposta_efetiva(secao, resposta, (secao["chave"], i) in com_evidencia)
            if resposta == form_r04.RESPOSTA_NAO_ATENDE:
                motivo = MOTIVO_NAO_ATENDE
            elif resposta == form_r04.RESPOSTA_PARCIAL:
                motivo = MOTIVO_PARCIAL
            elif resposta == form_r04.RESPOSTA_ATENDE and efetiva == form_r04.RESPOSTA_PARCIAL:
                motivo = MOTIVO_SEM_EVIDENCIA
            else:
                continue
            lista.append({
                "secao": secao["chave"], "secao_titulo": secao["titulo"], "item": i,
                "texto": texto, "resposta": resposta, "motivo": motivo,
            })
    return lista


def plano_ativo(homologacao: Homologacao) -> Plano | None:
    for plano in reversed(homologacao.planos_acao):
        if plano.status != Plano.STATUS_CANCELADO:
            return plano
    return None


def criar_se_pendente(homologacao: Homologacao, usuario: str) -> Plano | None:
    """Chamado ao homologar. Nao duplica: se ja' existe plano ativo, devolve ele."""
    existente = plano_ativo(homologacao)
    if existente:
        return existente
    itens = pendencias(homologacao)
    if not itens:
        return None
    plano = Plano(status=Plano.STATUS_RASCUNHO, criado_por=usuario)
    for p in itens:
        plano.itens.append(PlanoItem(
            secao=p["secao"], item=p["item"], texto=p["texto"], resposta=p["resposta"], motivo=p["motivo"],
        ))
    homologacao.planos_acao.append(plano)
    return plano


def cancelar_aberto(homologacao: Homologacao) -> None:
    """Reabrir a homologacao invalida o plano em aberto (a avaliacao vai mudar)."""
    plano = plano_ativo(homologacao)
    if plano and plano.status != Plano.STATUS_CONCLUIDO:
        plano.status = Plano.STATUS_CANCELADO


# ── Relatorio (IA ou modelo padrao) ─────────────────────────────────────
def _acao_modelo(item: PlanoItem) -> str:
    if item.motivo == MOTIVO_SEM_EVIDENCIA:
        return "Enviar a evidência que comprova o atendimento deste requisito (procedimento, registro ou foto)."
    if item.motivo == MOTIVO_NAO_ATENDE:
        return ("Implementar o controle exigido por este requisito e enviar a evidência da implementação "
                "(procedimento aprovado e registro de uso).")
    return ("Completar a implementação deste requisito - descrever o que falta, a ação tomada e enviar a "
            "evidência de que passou a ser atendido integralmente.")


def _relatorio_modelo(homologacao: Homologacao) -> str:
    nota = f"{(homologacao.nota or 0) * 100:.1f}".replace(".", ",")
    return (
        f"Prezados,\n\n"
        f"Agradecemos a participação no processo de avaliação de fornecedores da Columbia Machine Brasil. "
        f"A empresa {homologacao.razao_social} foi homologada com nota {nota}% "
        f"({homologacao.classificacao}).\n\n"
        f"Na avaliação foram identificados itens que ainda não atendem integralmente aos nossos requisitos. "
        f"Para cada um deles, listamos abaixo a ação esperada e o prazo para envio da evidência pelo link "
        f"de acompanhamento. A Columbia analisará cada evidência recebida.\n\n"
        f"Contamos com a colaboração de vocês."
    )


def _gerar_com_ia(homologacao: Homologacao, plano: Plano) -> dict | None:
    """Pede a IA (mesma configuracao da Bia - API estilo OpenAI) o texto de
    abertura e uma acao/prazo por item. Qualquer falha devolve None e o
    chamador cai no modelo padrao - a IA nunca trava o processo."""
    from .expedicao_assistente_service import _llm_cfg

    url, chave, modelo = _llm_cfg()
    if not url or not chave:
        return None
    itens = [
        {"id": it.id, "secao": it.secao.replace(" R04", ""), "requisito": it.texto, "situacao": it.motivo}
        for it in plano.itens
    ]
    sistema = (
        "Você é analista de Qualidade de Fornecedores da Columbia Machine Brasil (indústria de máquinas). "
        "Escreve em português do Brasil, tom profissional e cordial, objetivo. Responda SOMENTE com JSON."
    )
    pedido = (
        f"Fornecedor: {homologacao.razao_social}. Nota da avaliação: {(homologacao.nota or 0) * 100:.1f}% "
        f"({homologacao.classificacao}).\n"
        "Itens que não pontuaram cheio (situacao 'Sem evidência' = respondeu que atende, mas não anexou "
        "evidência; a ação é apenas enviar a evidência):\n"
        f"{json.dumps(itens, ensure_ascii=False)}\n\n"
        "Gere um JSON no formato {\"introducao\": \"texto de abertura do relatório ao fornecedor, 2 a 4 "
        "parágrafos, sem listar os itens\", \"itens\": [{\"id\": <id>, \"acao\": \"ação corretiva concreta e "
        "verificável, 1 a 2 frases, dizendo qual evidência enviar\", \"prazo_dias\": <15 a 90>}]} com um "
        "objeto por item."
    )
    try:
        resp = requests.post(
            url,
            headers={"Authorization": f"Bearer {chave}", "Content-Type": "application/json"},
            json={
                "model": modelo,
                "messages": [{"role": "system", "content": sistema}, {"role": "user", "content": pedido}],
                "temperature": 0.3,
                "max_tokens": 2000,
            },
            timeout=45,
        )
        resp.raise_for_status()
        conteudo = (resp.json().get("choices") or [{}])[0].get("message", {}).get("content", "")
        bloco = re.search(r"\{.*\}", conteudo or "", re.S)
        dados = json.loads(bloco.group(0)) if bloco else None
    except Exception:
        current_app.logger.warning("Plano de acao: falha ao gerar relatorio com IA (homologacao %s)", homologacao.id, exc_info=True)
        return None
    if not isinstance(dados, dict) or not _txt(dados.get("introducao")):
        return None
    return dados


def gerar_relatorio(plano: Plano) -> Plano:
    """Preenche relatorio + acao/prazo de cada item. Sobrescreve o que o
    comprador ja' tinha editado - a tela confirma antes."""
    if plano.status != Plano.STATUS_RASCUNHO:
        raise ValueError("O relatório só pode ser gerado enquanto o plano está em rascunho.")
    homologacao = plano.homologacao
    hoje = agora_br().date()
    dados = _gerar_com_ia(homologacao, plano)
    sugestoes = {}
    if dados:
        for s in dados.get("itens") or []:
            if isinstance(s, dict):
                sugestoes[str(s.get("id"))] = s
        plano.relatorio = _txt(dados.get("introducao"))
        plano.relatorio_origem = "ia"
    else:
        plano.relatorio = _relatorio_modelo(homologacao)
        plano.relatorio_origem = "modelo"
    for item in plano.itens:
        sugestao = sugestoes.get(str(item.id)) or {}
        item.acao = _txt(sugestao.get("acao")) or _acao_modelo(item)
        try:
            dias = int(sugestao.get("prazo_dias") or PRAZO_PADRAO_DIAS)
        except (TypeError, ValueError):
            dias = PRAZO_PADRAO_DIAS
        item.prazo = hoje + timedelta(days=max(7, min(dias, 180)))
    db.session.commit()
    return plano


def _data(valor) -> date | None:
    texto = _txt(valor)[:10]
    if not texto:
        return None
    for formato in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(texto, formato).date()
        except ValueError:
            continue
    raise ValueError("Prazo inválido.")


def salvar(plano: Plano, dados: dict) -> Plano:
    if plano.status != Plano.STATUS_RASCUNHO:
        raise ValueError("Depois de enviado ao fornecedor o plano não pode mais ser editado.")
    if "relatorio" in dados:
        plano.relatorio = _txt(dados.get("relatorio")) or None
    por_id = {str(it.id): it for it in plano.itens}
    for entrada in dados.get("itens") or []:
        item = por_id.get(str((entrada or {}).get("id")))
        if not item:
            continue
        if "acao" in entrada:
            item.acao = _txt(entrada.get("acao")) or None
        if "prazo" in entrada:
            item.prazo = _data(entrada.get("prazo"))
    db.session.commit()
    return plano


# ── Envio ao fornecedor ─────────────────────────────────────────────────
def _hash(token: str) -> str:
    return hashlib.sha256((token or "").encode("utf-8")).hexdigest()


def enviar(plano: Plano, email: str, usuario: str) -> str:
    """Gera (ou regera, no reenvio) o link. Devolve o token bruto - so' o
    hash fica salvo."""
    if plano.status not in (Plano.STATUS_RASCUNHO, Plano.STATUS_COM_FORNECEDOR):
        raise ValueError(f"Plano '{plano.status}' não pode ser enviado.")
    if not _txt(plano.relatorio):
        raise ValueError("Gere ou escreva o relatório antes de enviar.")
    faltando = [it for it in plano.itens if not _txt(it.acao) or not it.prazo]
    if faltando:
        raise ValueError(f"Defina a ação e o prazo de todos os itens - faltam {len(faltando)}.")
    email = _txt(email).lower()
    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
        raise ValueError("Informe um e-mail válido para o fornecedor.")
    token = secrets.token_urlsafe(32)
    plano.token_hash = _hash(token)
    plano.email = email[:120]
    plano.enviado_em = agora_br()
    plano.enviado_por = usuario
    plano.status = Plano.STATUS_COM_FORNECEDOR
    db.session.commit()
    return token


def obter_por_token(token: str) -> Plano | None:
    if not token:
        return None
    return Plano.query.filter_by(token_hash=_hash(token)).first()


def aceita_fornecedor(plano: Plano) -> bool:
    return plano.status == Plano.STATUS_COM_FORNECEDOR


def _item_do_plano(plano: Plano, item_id) -> PlanoItem:
    item = next((it for it in plano.itens if str(it.id) == str(item_id)), None)
    if not item:
        raise ValueError("Item do plano não encontrado.")
    return item


def anexar_evidencia(plano: Plano, item_id, arquivo, comentario: str = "") -> PlanoItem:
    if not aceita_fornecedor(plano):
        raise ValueError("Este plano de ação não está mais aberto para envio.")
    item = _item_do_plano(plano, item_id)
    if item.status == PlanoItem.STATUS_ACEITO:
        raise ValueError("Este item já foi aceito pela Columbia.")
    if not arquivo or not getattr(arquivo, "filename", ""):
        raise ValueError("Nenhum arquivo recebido.")
    nome = _txt(arquivo.filename)
    extensao = ("." + nome.rsplit(".", 1)[-1].lower()) if "." in nome else ""
    content_type = svc.TIPOS_EVIDENCIA.get(extensao)
    if not content_type:
        raise ValueError("Formato não suportado - envie PDF, JPG ou PNG.")
    conteudo = arquivo.read(svc.MAX_EVIDENCIA_BYTES + 1)
    if not conteudo:
        raise ValueError("Arquivo vazio.")
    if len(conteudo) > svc.MAX_EVIDENCIA_BYTES:
        raise ValueError("Arquivo muito grande (máximo 10 MB).")
    item.evidencias.append(PlanoEvidencia(
        nome_arquivo=nome[:260], content_type=content_type, tamanho_bytes=len(conteudo),
        dados=conteudo, enviado_por=f"Fornecedor ({plano.email})"[:100],
    ))
    if _txt(comentario):
        item.comentario_fornecedor = _txt(comentario)[:2000]
    item.status = PlanoItem.STATUS_EM_ANALISE
    item.enviado_em = agora_br()
    db.session.commit()
    return item


def comentar(plano: Plano, item_id, comentario: str) -> PlanoItem:
    if not aceita_fornecedor(plano):
        raise ValueError("Este plano de ação não está mais aberto para envio.")
    item = _item_do_plano(plano, item_id)
    if item.status == PlanoItem.STATUS_ACEITO:
        raise ValueError("Este item já foi aceito pela Columbia.")
    item.comentario_fornecedor = _txt(comentario)[:2000] or None
    db.session.commit()
    return item


def remover_evidencia(plano: Plano, evidencia_id) -> None:
    if not aceita_fornecedor(plano):
        raise ValueError("Este plano de ação não está mais aberto para envio.")
    for item in plano.itens:
        for ev in item.evidencias:
            if str(ev.id) == str(evidencia_id):
                if item.status == PlanoItem.STATUS_ACEITO:
                    raise ValueError("Este item já foi aceito pela Columbia.")
                item.evidencias.remove(ev)
                if not item.evidencias:
                    item.status = PlanoItem.STATUS_PENDENTE
                db.session.commit()
                return
    raise ValueError("Evidência não encontrada.")


def evidencia(plano: Plano, evidencia_id) -> PlanoEvidencia | None:
    for item in plano.itens:
        for ev in item.evidencias:
            if str(ev.id) == str(evidencia_id):
                return ev
    return None


# ── Decisao da Columbia ─────────────────────────────────────────────────
def aceitar_item(plano: Plano, item_id, usuario: str) -> Plano:
    item = _item_do_plano(plano, item_id)
    if plano.status != Plano.STATUS_COM_FORNECEDOR:
        raise ValueError("O plano não está com o fornecedor.")
    if item.status != PlanoItem.STATUS_EM_ANALISE:
        raise ValueError("Só dá pra aceitar um item com evidência enviada.")
    item.status = PlanoItem.STATUS_ACEITO
    item.motivo_recusa = None
    item.decidido_em = agora_br()
    item.decidido_por = usuario
    if all(it.status == PlanoItem.STATUS_ACEITO for it in plano.itens):
        plano.status = Plano.STATUS_CONCLUIDO
        plano.concluido_em = agora_br()
    db.session.commit()
    return plano


def recusar_item(plano: Plano, item_id, usuario: str, motivo: str) -> Plano:
    item = _item_do_plano(plano, item_id)
    if plano.status != Plano.STATUS_COM_FORNECEDOR:
        raise ValueError("O plano não está com o fornecedor.")
    if item.status != PlanoItem.STATUS_EM_ANALISE:
        raise ValueError("Só dá pra recusar um item com evidência enviada.")
    motivo = _txt(motivo)
    if not motivo:
        raise ValueError("Informe o motivo da recusa - ele aparece para o fornecedor.")
    item.status = PlanoItem.STATUS_PENDENTE
    item.motivo_recusa = motivo[:500]
    item.decidido_em = agora_br()
    item.decidido_por = usuario
    db.session.commit()
    return plano


def obter(plano_id: int) -> Plano | None:
    return db.session.get(Plano, plano_id)


def resumo(plano: Plano | None) -> dict | None:
    if not plano:
        return None
    total = len(plano.itens)
    aceitos = sum(1 for it in plano.itens if it.status == PlanoItem.STATUS_ACEITO)
    em_analise = sum(1 for it in plano.itens if it.status == PlanoItem.STATUS_EM_ANALISE)
    return {"id": plano.id, "status": plano.status, "total": total, "aceitos": aceitos, "em_analise": em_analise}
