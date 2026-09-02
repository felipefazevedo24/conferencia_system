"""Notificacoes para canal do Teams via webhook (Power Automate / Workflows).

O webhook aceita Adaptive Card no formato {"type":"message","attachments":[...]}.
A URL contem uma assinatura secreta, entao NAO fica no git: e lida de
  1) variavel de ambiente TEAMS_WEBHOOK_EXPEDICAO_URL, ou
  2) instance/teams_config.json  ({"webhook_expedicao": "https://..."}).

Os envios sao assincronos e nunca quebram o fluxo principal.
"""
from __future__ import annotations

import json
import os
import threading

import requests
from flask import current_app

# Texto da mencao de canal (o Teams resolve via msteams.entities abaixo).
_MENTION_TEXT = "<at>Canal</at>"


def _webhook_url(env_var: str = "TEAMS_WEBHOOK_EXPEDICAO_URL", config_key: str = "webhook_expedicao") -> str:
    url = str(os.environ.get(env_var, "") or "").strip()
    if url:
        return url
    try:
        path = os.path.join(current_app.instance_path, "teams_config.json")
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as fh:
                return str((json.load(fh) or {}).get(config_key) or "").strip()
    except Exception:
        return ""
    return ""


def _card_payload(titulo: str, linha_principal: str, subinfo: str | None = None, mencionar_canal: bool = False) -> dict:
    body = []
    content = {
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "type": "AdaptiveCard",
        "version": "1.4",
        "body": body,
    }
    if mencionar_canal:
        body.append({"type": "TextBlock", "size": "Small", "isSubtle": True, "wrap": True, "text": f"{_MENTION_TEXT}"})
        content["msteams"] = {
            "entities": [
                {"type": "mention", "text": _MENTION_TEXT, "mentioned": {"id": "0", "name": "Canal"}}
            ]
        }
    body.append({"type": "TextBlock", "size": "Small", "weight": "Bolder", "color": "Good", "text": titulo, "wrap": True})
    body.append({"type": "TextBlock", "size": "Large", "weight": "Bolder", "text": linha_principal, "wrap": True})
    if subinfo:
        body.append({"type": "TextBlock", "size": "Small", "isSubtle": True, "wrap": True, "text": subinfo})
    return {
        "type": "message",
        "attachments": [
            {
                "contentType": "application/vnd.microsoft.card.adaptive",
                "content": content,
            }
        ],
    }


def _enviar_async(app, url: str, payload: dict) -> None:
    with app.app_context():
        try:
            resp = requests.post(url, json=payload, timeout=10)
            resp.raise_for_status()
        except Exception as exc:  # pragma: no cover - best effort
            app.logger.warning("Falha ao enviar aviso ao Teams: %s", exc)


def enviar_card(
    titulo: str,
    linha_principal: str,
    subinfo: str | None = None,
    mencionar_canal: bool = False,
    *,
    env_var: str = "TEAMS_WEBHOOK_EXPEDICAO_URL",
    config_key: str = "webhook_expedicao",
) -> None:
    """Dispara um Adaptive Card no canal do Teams (assincrono, tolerante a falha)."""
    app = current_app._get_current_object()
    url = _webhook_url(env_var, config_key)
    if not url:
        app.logger.info("TEAMS: webhook nao configurado; aviso ignorado (%s).", linha_principal)
        return
    payload = _card_payload(titulo, linha_principal, subinfo, mencionar_canal)
    threading.Thread(target=_enviar_async, args=(app, url, payload), daemon=True).start()


def notificar_expedicao_conferida(
    nome: str,
    referencia: str,
    *,
    conferente: str | None = None,
    volumes: str | None = None,
    peso_liquido: str | None = None,
    peso_bruto: str | None = None,
    especie_volumes: str | None = None,
    observacao: str | None = None,
    titulo: str = "✅ Conferência de expedição finalizada",
    env_var: str = "TEAMS_WEBHOOK_EXPEDICAO_URL",
    config_key: str = "webhook_expedicao",
) -> None:
    """Aviso de conferencia de expedicao finalizada.

    Formato principal (ex.): "PAULO CESAR & CIA LTDA - Orcamento 6953".
    """
    linha = f"{(nome or 'Nao informado').strip()} - {referencia}".strip()
    partes = []
    if conferente:
        partes.append(f"Conferente: {conferente}")
    if volumes:
        partes.append(f"Volumes: {volumes}")
    if especie_volumes:
        partes.append(f"Espécie: {especie_volumes}")
    if peso_liquido:
        partes.append(f"Peso líquido: {peso_liquido}")
    if peso_bruto:
        partes.append(f"Peso bruto: {peso_bruto}")
    if observacao:
        partes.append(observacao)
    subinfo = " · ".join(partes) or None
    enviar_card(
        titulo,
        linha,
        subinfo,
        mencionar_canal=True,
        env_var=env_var,
        config_key=config_key,
    )


_SOLICITACAO_NF_TITULOS = {
    "criada": "🧾 Nova solicitação de NF",
    "separada": "📦 Solicitação de NF separada",
    "faturada": "✅ Solicitação de NF faturada",
    "retorno": "🔁 Retorno de material registrado",
    "estorno": "↩️ Solicitação de NF estornada",
    "excluida": "🗑️ Solicitação de NF excluída",
}


def notificar_solicitacao_nf(
    evento: str,
    protocolo: str,
    solicitante: str,
    cliente: str,
    tipo_operacao: str,
    *,
    subinfo: str | None = None,
    env_var: str = "TEAMS_WEBHOOK_SOLICITACAO_NF_URL",
    config_key: str = "webhook_solicitacao_nf",
) -> None:
    """Aviso de criacao/separacao/faturamento de solicitacao de NF."""
    titulo = _SOLICITACAO_NF_TITULOS.get(evento, "🧾 Solicitação de NF")
    linha = f"{protocolo} · {cliente} ({tipo_operacao})"
    partes = [f"Solicitante: {solicitante}"]
    if subinfo:
        partes.append(subinfo)
    enviar_card(
        titulo,
        linha,
        " · ".join(partes),
        mencionar_canal=(evento == "criada"),
        env_var=env_var,
        config_key=config_key,
    )


def notificar_divergencia_pedido(
    numero_nota: str,
    fornecedor: str,
    pedido_compra: str,
    linhas_divergentes: list,
    link_conferencia: str | None = None,
    *,
    sync: bool = False,
    env_var: str = "TEAMS_WEBHOOK_DIVERGENCIA_URL",
    config_key: str = "webhook_divergencia_pedido",
) -> bool | None:
    """
    Notifica Compras (via Power Automate) de uma divergencia XML x Pedido que
    precisa de aprovacao antes da NF poder ser liberada para conferencia.

    O webhook "Enviar alertas de webhook" do Power Automate (mesmo tipo usado
    em webhook_expedicao) EXIGE que a mensagem seja um Adaptive Card ou
    Message Card - por isso o payload usa o mesmo envelope de enviar_card()
    (senao o Power Automate ignora/rejeita a chamada). Os dados brutos da
    divergencia (numero_nota, pedido_compra, linhas_divergentes, etc.) vao
    JUNTO como campos extras no mesmo JSON, fora do card - servem pra quem
    quiser configurar no flow um passo seguinte de "Post adaptive card and
    wait for a response" (com Aprovar/Rejeitar) usando esses campos, que
    depois chama de volta POST /api/xml_auditor/divergencia/webhook-decisao.

    sync=True: envia SINCRONO (bloqueia ate a resposta) e retorna True/False
    conforme o envio deu certo - usado quando o chamador precisa SABER se
    realmente notificou (ex.: pra decidir se tenta de novo depois), em vez do
    fire-and-forget padrao (sync=False, retorna None, nunca informa falha).
    """
    app = current_app._get_current_object()
    url = _webhook_url(env_var, config_key)
    if not url:
        app.logger.info("TEAMS: webhook de divergencia nao configurado; aviso ignorado (NF %s).", numero_nota)
        return False if sync else None

    # === Texto do cartao do Teams (edite aqui para mudar a mensagem) ==========
    titulo_card = "🚨 Divergência de recebimento — Compras precisa decidir"
    linha_principal = f"NF {numero_nota} · {fornecedor or 'Fornecedor não identificado'}"
    partes_subinfo = []
    if pedido_compra:
        partes_subinfo.append(f"📄 Pedido de compra: {pedido_compra}")
    if linhas_divergentes:
        partes_subinfo.append("O que divergiu entre a nota e o pedido:")
        partes_subinfo.extend(f"• {linha}" for linha in linhas_divergentes)
    partes_subinfo.append("")
    partes_subinfo.append("👉 Abra o link abaixo, faça login com seu usuário do Sync e aprove ou recuse. Na tela você vê a ordem de compra, a nota (XML) e pode baixar o PDF da NF-e.")
    subinfo = "\n".join(partes_subinfo) or None
    # =========================================================================

    payload = _card_payload(
        titulo_card,
        linha_principal,
        subinfo,
        mencionar_canal=True,
    )
    # Botao clicavel no proprio card (abre a tela de aprovacao no navegador).
    if link_conferencia:
        try:
            card_content = payload["attachments"][0]["content"]
            card_content["actions"] = [
                {"type": "Action.OpenUrl", "title": "Abrir e decidir (Aprovar / Recusar)", "url": link_conferencia}
            ]
        except (KeyError, IndexError, TypeError):
            pass
    # Campos extras (fora do envelope do card), para automações adicionais no flow.
    payload["numero_nota"] = str(numero_nota or "")
    payload["fornecedor"] = str(fornecedor or "")
    payload["pedido_compra"] = str(pedido_compra or "")
    payload["linhas_divergentes"] = [str(linha) for linha in (linhas_divergentes or [])]
    payload["link_conferencia"] = str(link_conferencia or "")

    if sync:
        try:
            resp = requests.post(url, json=payload, timeout=10)
            resp.raise_for_status()
            return True
        except Exception as exc:
            app.logger.warning("Falha ao enviar aviso de divergência ao Teams (NF %s): %s", numero_nota, exc)
            return False

    threading.Thread(target=_enviar_async, args=(app, url, payload), daemon=True).start()
    return None

    threading.Thread(target=_enviar_async, args=(app, url, payload), daemon=True).start()

