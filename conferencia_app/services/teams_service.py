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
import re
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


# Formato gerado em api_routes._notificar_divergencia_pedido_se_necessario:
# "Linha 1 do XML (ITM04724) -> Linha 1 do pedido: valor diverge"
_RE_LINHA_DIVERGENCIA = re.compile(r"^Linha (?P<nf>\S+) do XML \((?P<cod>[^)]*)\) -> (?P<destino>.+?): (?P<motivo>.+)$")


def _linha_divergencia_card(linha: str) -> dict:
    """Uma divergencia em 2 colunas (item | motivo). Se o texto nao seguir o
    formato esperado, mostra cru - melhor feio do que sumir do card."""
    m = _RE_LINHA_DIVERGENCIA.match(linha.strip())
    if not m:
        return {"type": "TextBlock", "text": f"• {linha}", "wrap": True, "spacing": "Small"}
    destino = m["destino"]
    destino = destino[0].lower() + destino[1:] if destino else destino
    return {
        "type": "ColumnSet",
        "spacing": "Small",
        "columns": [
            {
                "type": "Column",
                "width": "stretch",
                "items": [
                    {"type": "TextBlock", "text": f"**Linha {m['nf']}** · {m['cod']}", "wrap": True},
                    {"type": "TextBlock", "text": f"→ {destino}", "isSubtle": True, "size": "Small",
                     "spacing": "None", "wrap": True},
                ],
            },
            {
                "type": "Column",
                "width": "auto",
                "verticalContentAlignment": "Center",
                "items": [
                    {"type": "TextBlock", "text": m["motivo"].capitalize(), "color": "Attention",
                     "weight": "Bolder", "size": "Small", "wrap": True},
                ],
            },
        ],
    }


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

    # Card proprio (nao usa _card_payload): faixa de alerta, fatos em FactSet e
    # uma linha por divergencia - o texto corrido antigo era dificil de ler.
    payload = _card_payload("", "", None, mencionar_canal=True)
    card_content = payload["attachments"][0]["content"]
    corpo = [card_content["body"][0]]  # so a mencao do canal

    corpo.append(
        {
            "type": "Container",
            "style": "attention",
            "bleed": True,
            "items": [
                {"type": "TextBlock", "text": "⚠️ DIVERGÊNCIA NF × PEDIDO", "weight": "Bolder",
                 "size": "Small", "color": "Attention", "spacing": "None"},
                {"type": "TextBlock", "text": f"NF {numero_nota}", "weight": "Bolder",
                 "size": "ExtraLarge", "spacing": "Small", "wrap": True},
                {"type": "TextBlock", "text": fornecedor or "Fornecedor não identificado",
                 "isSubtle": True, "spacing": "None", "wrap": True},
            ],
        }
    )
    fatos = []
    if pedido_compra:
        fatos.append({"title": "Pedido de compra", "value": str(pedido_compra)})
    if linhas_divergentes:
        qtd = len(linhas_divergentes)
        fatos.append({"title": "Itens divergentes", "value": f"{qtd} {'linha' if qtd == 1 else 'linhas'}"})
    fatos.append({"title": "Situação", "value": "⏳ Aguardando decisão de Compras"})
    corpo.append({"type": "FactSet", "facts": fatos, "spacing": "Medium"})

    if linhas_divergentes:
        corpo.append({"type": "TextBlock", "text": "O QUE DIVERGIU", "weight": "Bolder", "size": "Small",
                      "isSubtle": True, "spacing": "Medium", "separator": True})
        for linha in linhas_divergentes:
            corpo.append(_linha_divergencia_card(str(linha)))

    card_content["body"] = corpo
    if link_conferencia:
        card_content["actions"] = [
            {"type": "Action.OpenUrl", "title": "Revisar e decidir", "url": link_conferencia, "style": "positive"}
        ]
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


_OCORRENCIA_CABECALHO = {
    # evento: (faixa, estilo do container, cor do texto, texto do botão)
    "aberta": ("📦 DIVERGÊNCIA NA CONFERÊNCIA FÍSICA", "attention", "Attention", "Abrir tratativa"),
    "novos_itens": ("📦 NOVA DIVERGÊNCIA NA MESMA NF", "attention", "Attention", "Abrir tratativa"),
    "aguardando_fiscal": ("↩️ DEVOLUÇÃO TOTAL — AGUARDANDO FISCAL", "warning", "Warning", "Informar recusa ou NF de devolução"),
    "encerrada": ("✅ DIVERGÊNCIA DE RECEBIMENTO ENCERRADA", "good", "Good", "Ver tratativa"),
    "lembrete": ("⏰ DIVERGÊNCIA SEM ATUALIZAÇÃO", "warning", "Warning", "Atualizar tratativa"),
}


def notificar_ocorrencia_recebimento(
    evento: str,
    *,
    numero_nota: str,
    fornecedor: str,
    pedido_compra: str = "",
    origem: str = "",
    situacao: str = "",
    acao: str = "",
    responsavel: str = "",
    ultimo_comentario: str = "",
    dias_sem_atualizacao: int = 0,
    linhas: list | None = None,
    link: str = "",
    sync: bool = False,
    env_var: str = "TEAMS_WEBHOOK_DIVERGENCIA_URL",
    config_key: str = "webhook_divergencia_pedido",
) -> bool | None:
    """Divergência da conferência física tratada por Compras - no MESMO grupo
    (webhook) da divergência XML x pedido, com faixa própria para não
    confundir as duas. Eventos: aberta, novos_itens, aguardando_fiscal,
    encerrada, lembrete."""
    app = current_app._get_current_object()
    url = _webhook_url(env_var, config_key)
    if not url:
        app.logger.info("TEAMS: webhook de divergencia nao configurado; ocorrencia de recebimento ignorada (NF %s).", numero_nota)
        return False if sync else None

    faixa, estilo, cor, botao = _OCORRENCIA_CABECALHO.get(evento, _OCORRENCIA_CABECALHO["aberta"])
    payload = _card_payload("", "", None, mencionar_canal=True)
    card_content = payload["attachments"][0]["content"]
    corpo = [card_content["body"][0]]
    corpo.append({
        "type": "Container", "style": estilo, "bleed": True,
        "items": [
            {"type": "TextBlock", "text": faixa, "weight": "Bolder", "size": "Small", "color": cor, "spacing": "None"},
            {"type": "TextBlock", "text": f"NF {numero_nota}", "weight": "Bolder", "size": "ExtraLarge", "spacing": "Small", "wrap": True},
            {"type": "TextBlock", "text": fornecedor or "Fornecedor não identificado", "isSubtle": True, "spacing": "None", "wrap": True},
        ],
    })
    fatos = []
    if pedido_compra:
        fatos.append({"title": "Pedido de compra", "value": str(pedido_compra)})
    if origem and evento in ("aberta", "novos_itens"):
        fatos.append({"title": "Origem", "value": origem})
    if situacao:
        fatos.append({"title": "Situação", "value": situacao})
    if acao:
        fatos.append({"title": "O que será feito", "value": acao})
    if responsavel:
        fatos.append({"title": "Responsável", "value": responsavel})
    if evento == "lembrete":
        fatos.append({"title": "Sem atualização há", "value": f"{dias_sem_atualizacao} dia(s)"})
    if fatos:
        corpo.append({"type": "FactSet", "facts": fatos, "spacing": "Medium"})
    if evento == "aguardando_fiscal":
        corpo.append({"type": "TextBlock", "wrap": True, "spacing": "Medium",
                      "text": "Compras decidiu devolver a NF inteira. Fiscal: informe a recusa da NF ou a NF de devolução emitida."})
    if linhas and evento in ("aberta", "novos_itens"):
        corpo.append({"type": "TextBlock", "text": "O QUE O RECEBIMENTO ENCONTROU", "weight": "Bolder", "size": "Small",
                      "isSubtle": True, "spacing": "Medium", "separator": True})
        for linha in linhas[:15]:
            corpo.append({"type": "TextBlock", "text": f"• {linha}", "wrap": True, "spacing": "Small"})
        if len(linhas) > 15:
            corpo.append({"type": "TextBlock", "text": f"… e mais {len(linhas) - 15}", "isSubtle": True, "spacing": "Small"})
    if ultimo_comentario and evento in ("encerrada", "lembrete", "aguardando_fiscal"):
        corpo.append({"type": "TextBlock", "text": f"💬 {ultimo_comentario}", "wrap": True, "spacing": "Medium", "isSubtle": True})
    card_content["body"] = corpo
    if link:
        card_content["actions"] = [{"type": "Action.OpenUrl", "title": botao, "url": link, "style": "positive"}]
    payload["evento"] = evento
    payload["numero_nota"] = str(numero_nota or "")
    payload["link"] = str(link or "")

    if sync:
        try:
            resp = requests.post(url, json=payload, timeout=10)
            resp.raise_for_status()
            return True
        except Exception as exc:
            app.logger.warning("Falha ao enviar ocorrência de recebimento ao Teams (NF %s): %s", numero_nota, exc)
            return False
    threading.Thread(target=_enviar_async, args=(app, url, payload), daemon=True).start()
    return None


def notificar_relatorio_ajuste_inventario(
    numero_documento: str,
    qtd_itens: int,
    gerado_por: str,
    *,
    tipo_ajuste: str | None = None,
    motivo_ajuste: str | None = None,
    deposito: str | None = None,
    link: str | None = None,
    env_var: str = "TEAMS_WEBHOOK_INVENTARIO_FINANCE_URL",
    config_key: str = "webhook_inventario_finance",
) -> None:
    """Avisa o Finance que um relatorio de ajuste de inventario (FORM-08.52)
    foi gerado e os itens estao disponiveis pra ajuste no ERP."""
    app = current_app._get_current_object()
    url = _webhook_url(env_var, config_key)
    if not url:
        app.logger.info("TEAMS: webhook do inventario/finance nao configurado; aviso ignorado (%s).", numero_documento)
        return

    linha = f"Relatório {numero_documento} · {qtd_itens} item(ns)"
    partes = [f"Gerado por: {gerado_por}"]
    if tipo_ajuste:
        partes.append(f"Tipo: {tipo_ajuste}")
    if motivo_ajuste:
        partes.append(f"Motivo: {motivo_ajuste}")
    if deposito:
        partes.append(f"Depósito: {deposito}")
    subinfo = " · ".join(partes) + "\n\nDisponível para ajuste no ERP."
    payload = _card_payload("📋 Ajuste de inventário para o Finance", linha, subinfo, mencionar_canal=True)
    if link:
        payload["attachments"][0]["content"]["actions"] = [
            {"type": "Action.OpenUrl", "title": "Abrir ajustes de inventário", "url": link}
        ]
    threading.Thread(target=_enviar_async, args=(app, url, payload), daemon=True).start()


def notificar_divergencia_inventario_gestor(
    codigo_produto: str,
    local_codigo: str,
    diferenca: float,
    *,
    descricao: str | None = None,
    unidade: str | None = None,
    qtde_contada: float | None = None,
    qtde_sistema: float | None = None,
    custo_medio: float | None = None,
    contado_por: str | None = None,
    link: str | None = None,
    env_var: str = "TEAMS_WEBHOOK_INVENTARIO_GESTOR_URL",
    config_key: str = "webhook_inventario_gestor",
) -> None:
    """Avisa o gestor que uma contagem de inventario divergiu do GRV e o
    ajuste esta em "Aguardando gestor" pra ser validado."""
    app = current_app._get_current_object()
    url = _webhook_url(env_var, config_key)
    if not url:
        app.logger.info("TEAMS: webhook do inventario/gestor nao configurado; aviso ignorado (%s).", codigo_produto)
        return

    def _q(v):
        return f"{v:g}" if isinstance(v, (int, float)) else "—"

    un = f" {unidade}" if unidade else ""
    linha = f"{codigo_produto} · {local_codigo} · diferença {diferenca:+g}{un}"
    partes = []
    if descricao:
        partes.append(descricao)
    partes.append(f"Contado: {_q(qtde_contada)}{un} · Sistema (GRV): {_q(qtde_sistema)}{un}")
    if custo_medio is not None:
        impacto = f"{diferenca * custo_medio:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        partes.append(f"Impacto estimado: R$ {impacto}")
    if contado_por:
        partes.append(f"Contado por: {contado_por}")
    subinfo = "\n".join(partes) + "\n\nAguardando validação do gestor."
    payload = _card_payload("⚠️ Divergência de inventário para validar", linha, subinfo, mencionar_canal=True)
    if link:
        payload["attachments"][0]["content"]["actions"] = [
            {"type": "Action.OpenUrl", "title": "Abrir ajustes de inventário", "url": link}
        ]
    threading.Thread(target=_enviar_async, args=(app, url, payload), daemon=True).start()
