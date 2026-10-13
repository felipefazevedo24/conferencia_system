"""Avisos de estoque por e-mail (pedido da logística em 13/10/2026).

Dois avisos, com ritmos diferentes:

- Reserva sem saldo: item com mais reservado do que saldo físico no depósito 1,
  em qualquer família da tela de Estoque. A reserva nasce no GRV e o Sync não
  recebe evento de lá, então "na hora" é uma checagem a cada 5 min
  (scripts/alerta_estoque_reservas.py). Avisa quando o item entra nessa
  situação e de novo se o reservado aumentar; não repete enquanto nada muda.
- Curva A em alerta: resumo às 7h e às 15h (scripts/alerta_estoque_resumo.py)
  com os itens de curva A sem estoque disponível, críticos e abaixo do mínimo
  sugerido. Lista completa a cada envio, com o que entrou ou piorou desde o
  último marcado como novo. Lista vazia não gera e-mail.

Item que já tem reposição a caminho não entra em nenhum dos dois: o aviso é
pra quem precisa comprar, e ali não há o que fazer. No resumo basta existir OC
aberta; na reserva a OC precisa cobrir o que falta (uma OC de 10 não resolve
uma reserva que estourou em 50).

Cada item sai com a sugestão de reposição: quanto manter em estoque pra não
comprar correndo, quanto comprar agora, o custo e em quanto tempo o consumo
dilui essa compra. Níveis, mínimo e lote são os mesmos da tela
(estoque_planejamento_service): nada é recalculado de outro jeito aqui.

Em EstoqueAlertaEstado fica só o que já foi avisado. Se o GRV ou o SMTP
falharem, nada é gravado e a próxima rodada tenta de novo - gravar antes de
enviar faria o aviso se perder.
"""
from __future__ import annotations

import re
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from html import escape
from typing import Any

from flask import current_app

from ..extensions import db
from ..models import EstoqueAlertaEstado
from ..tempo import agora_br
from . import estoque_planejamento_service as planejamento_svc
from .erp_estoque_service import (
    buscar_estoque_grv,
    buscar_ordens_compra_abertas_grv,
    buscar_planejamento_grv,
    buscar_reservas_produto_acabado_grv,
)
from .smtp_service import enviar_mensagem_smtp

# Famílias e visões são as da tela: o aviso tem que falar dos mesmos itens que
# ela mostra. Ficam definidas na rota, por isso o import no sentido inverso.
from ..routes.logistica_inventario_routes import ESTOQUE_VISOES, VISOES_PLANEJAMENTO, _normalizar_busca

TIPO_RESERVA = "reserva"
TIPO_CURVA_A = "curva_a"
# Saldo do GRV é soma de float por localização: diferença menor que isso é ruído.
TOLERANCIA = 0.0005
# Do mais grave pro menos grave; a ordem também é a dos blocos do e-mail.
# (título, explicação, cor)
SITUACOES = {
    "zerado": ("Sem estoque disponível", "Não há saldo livre no depósito.", "#b91c1c"),
    "critico": ("Crítico", "O saldo acaba antes de uma compra feita hoje chegar.", "#c2410c"),
    "abaixo_minimo": ("Abaixo do mínimo sugerido", "Já passou do ponto de pedido.", "#b45309"),
}
_GRAVIDADE = {"abaixo_minimo": 1, "critico": 2, "zerado": 3}


def _chave(codigo: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(codigo or "").upper())


def _fmt(valor: Any, casas: int = 2) -> str:
    if valor is None:
        return "—"
    texto = f"{float(valor):,.{casas}f}".replace(",", "#").replace(".", ",").replace("#", ".")
    return texto.rstrip("0").rstrip(",") if casas else texto


def _reais(valor: float | None) -> str:
    if valor is None:
        return "sem custo no GRV"
    return "R$ " + f"{valor:,.2f}".replace(",", "#").replace(".", ",").replace("#", ".")


def _duracao(meses: float | None) -> str:
    if meses is None:
        return "—"
    if meses < 1:
        return f"{max(1, round(meses * planejamento_svc.DIAS_POR_MES))} dias"
    return f"{_fmt(meses, 1)} {'mês' if round(meses, 1) == 1 else 'meses'}"


def _destinatarios() -> list[str]:
    bruto = str(current_app.config.get("ESTOQUE_ALERTA_EMAIL_DESTINATARIOS") or "")
    emails = [e.strip() for e in re.split(r"[,;]", bruto) if "@" in e and "." in e]
    if not emails:
        raise ValueError("ESTOQUE_ALERTA_EMAIL_DESTINATARIOS não configurado: nenhum aviso de estoque foi enviado.")
    return emails


def _por_codigo(estoque: dict[str, Any]) -> dict[str, Any]:
    por_codigo = (estoque or {}).get("por_codigo") or {}
    # Estoque vazio é bridge respondendo errado, não depósito zerado: seguir
    # daqui apagaria todo o estado e o próximo ciclo avisaria tudo de novo.
    if not por_codigo:
        raise RuntimeError("O GRV devolveu o estoque vazio; verificação ignorada.")
    return por_codigo


def _planos_da_visao(por_codigo: dict[str, Any], visao: str, parametros: dict[str, Any]):
    """(saldos, cadastro, planos) da família inteira da visão - a curva ABC é
    relativa ao conjunto, igual na tela. Levanta exceção se o histórico do
    GRV não responder."""
    familia_alvo = _normalizar_busca(ESTOQUE_VISOES[visao]["familia"])
    familia_itens = {}
    cadastro = {}
    for codigo, item in por_codigo.items():
        if _normalizar_busca(item.get("familia")) != familia_alvo:
            continue
        chave = _chave(codigo)
        familia_itens[chave] = {
            "saldo_disponivel": float(item.get("qtde_disponivel") or 0),
            "custo_medio": item.get("custo_medio"),
        }
        cadastro[chave] = (codigo, item)
    if not familia_itens:
        return {}, {}, {}
    historicos = buscar_planejamento_grv(codigos=list(familia_itens))
    return familia_itens, cadastro, planejamento_svc.calcular_planejamento(familia_itens, historicos, parametros)


def sugestao_reposicao(plano: dict[str, Any] | None, saldo_livre: float) -> dict[str, Any] | None:
    """Quanto manter e quanto comprar pra não comprar correndo de novo.

    Manter = o mínimo sugerido (ponto de pedido, já com a margem de segurança
    da curva). Comprar = o que falta pra voltar ao mínimo ou o lote econômico,
    o que for maior: repor só até o mínimo deixaria o item pedindo compra de
    novo na semana seguinte. `saldo_livre` negativo (reserva maior que o
    físico) entra na conta, então a compra cobre a reserva também.
    Item sem giro não tem sugestão: não é pra estocar."""
    if not plano or plano["tipo_demanda"] == "sem_giro":
        return None
    manter = float(plano["estoque_minimo_sugerido"] or 0)
    comprar = max(float(plano["lote_economico_sugerido"] or 0), manter - saldo_livre)
    if comprar <= TOLERANCIA:
        return None
    custo_unitario = plano["custo_unitario"]
    consumo_mensal = float(plano["consumo_mensal"] or 0)
    return {
        "manter": manter,
        "comprar": comprar,
        "custo": comprar * custo_unitario if custo_unitario else None,
        "meses": comprar / consumo_mensal if consumo_mensal > 0 else None,
    }


def _pendente_oc(ocs: dict[str, list[dict[str, Any]]], chave: str) -> float:
    return sum(float(oc.get("quantidade_pendente") or 0) for oc in ocs.get(chave) or [])


# -------------------------------------------------------------------- e-mail
# HTML de e-mail: só tabela e estilo inline, que é o que Outlook e Gmail respeitam.

_FONTE = "font-family:'Segoe UI',Arial,Helvetica,sans-serif;"
_AZUL = "#0f62c9"
_ESCURO = "#0b1f3a"
_TEXTO = "#1f2937"
_SUAVE = "#6b7280"
_BORDA = "#e5e7eb"
_TD = f"{_FONTE}padding:10px 12px;border-bottom:1px solid {_BORDA};font-size:13px;color:{_TEXTO};vertical-align:top;"
_TH = (
    f"{_FONTE}padding:8px 12px;background:#f8fafc;border-bottom:1px solid {_BORDA};font-size:10px;font-weight:700;"
    f"letter-spacing:.06em;text-transform:uppercase;color:{_SUAVE};text-align:left;white-space:nowrap;"
)
_NOVO = (
    f' <span style="{_FONTE}background:#b91c1c;color:#ffffff;font-size:9px;font-weight:700;letter-spacing:.05em;'
    'padding:2px 6px;border-radius:9px;vertical-align:middle;">NOVO</span>'
)


def _pequeno(texto: str) -> str:
    return f'<div style="font-size:11.5px;color:{_SUAVE};margin-top:2px;">{texto}</div>'


def _celula_item(item: dict[str, Any], rotulo: str, novo: bool = False) -> str:
    # min-width: sem ele as colunas de número espremem a descrição em 3 linhas.
    return (
        f'<div style="min-width:210px;"><div style="font-weight:700;white-space:nowrap;">{escape(item["codigo"])}{_NOVO if novo else ""}</div>'
        f'<div style="margin-top:2px;">{escape(item["descricao"])}</div>' + _pequeno(escape(rotulo)) + "</div>"
    )


def _celula_sugestao(sugestao: dict[str, Any] | None, unidade: str, sem_plano: str) -> list[str]:
    """As 3 últimas colunas de toda tabela: manter, comprar e em quanto tempo dilui."""
    if not sugestao:
        return ["—" + _pequeno(escape(sem_plano)), "—", "—"]
    un = escape(unidade)
    return [
        f"{_fmt(sugestao['manter'])} {un}",
        f'<b style="color:{_AZUL};">{_fmt(sugestao["comprar"])} {un}</b>' + _pequeno(escape(_reais(sugestao["custo"]))),
        escape(_duracao(sugestao["meses"])),
    ]


def _tabela(colunas: list[tuple[str, bool]], linhas: list[list[str]]) -> str:
    """`colunas`: (título, alinhar à direita). As células já chegam escapadas."""
    cab = "".join(f'<th style="{_TH}{"text-align:right;" if num else ""}">{escape(titulo)}</th>' for titulo, num in colunas)
    corpo = "".join(
        "<tr>" + "".join(
            f'<td style="{_TD}{"text-align:right;white-space:nowrap;" if colunas[i][1] else ""}">{celula}</td>'
            for i, celula in enumerate(linha)
        ) + "</tr>"
        for linha in linhas
    )
    return (
        f'<table role="presentation" cellpadding="0" cellspacing="0" width="100%" style="border-collapse:collapse;'
        f'border:1px solid {_BORDA};margin:0 0 22px;"><tr>{cab}</tr>{corpo}</table>'
    )


def _secao(titulo: str, explicacao: str, cor: str, quantidade: int) -> str:
    return (
        f'<table role="presentation" cellpadding="0" cellspacing="0" width="100%" style="margin:0 0 10px;"><tr>'
        f'<td style="{_FONTE}border-left:4px solid {cor};padding:2px 0 2px 10px;">'
        f'<span style="font-size:15px;font-weight:700;color:{_TEXTO};">{escape(titulo)}</span>'
        f'<span style="font-size:12px;font-weight:700;color:{cor};"> &nbsp;{quantidade} {"item" if quantidade == 1 else "itens"}</span>'
        f'<div style="font-size:12px;color:{_SUAVE};margin-top:2px;">{escape(explicacao)}</div></td></tr></table>'
    )


def _cartoes(cartoes: list[tuple[str, str, str]]) -> str:
    """Faixa de números no topo: (valor, rótulo, cor)."""
    largura = 100 // len(cartoes)
    celulas = "".join(
        f'<td width="{largura}%" style="{_FONTE}padding:14px 8px;text-align:center;border:1px solid {_BORDA};background:#ffffff;">'
        f'<div style="font-size:24px;font-weight:700;color:{cor};line-height:1.1;">{escape(valor)}</div>'
        f'<div style="font-size:10.5px;font-weight:700;letter-spacing:.05em;text-transform:uppercase;color:{_SUAVE};margin-top:4px;">{escape(rotulo)}</div></td>'
        for valor, rotulo, cor in cartoes
    )
    return f'<table role="presentation" cellpadding="0" cellspacing="0" width="100%" style="border-collapse:collapse;margin:0 0 24px;"><tr>{celulas}</tr></table>'


def _nota(texto: str, cor: str = _SUAVE, fundo: str = "#f8fafc") -> str:
    return f'<p style="{_FONTE}font-size:12.5px;line-height:1.55;color:{cor};background:{fundo};padding:10px 12px;margin:0 0 16px;">{texto}</p>'


_COMO_LER = (
    "<b>Manter em estoque</b> é o mínimo sugerido pelo histórico do item: cobre o consumo durante o prazo do fornecedor "
    "mais a margem de segurança. <b>Comprar agora</b> é o que falta pra voltar a esse mínimo ou o lote econômico, o que for "
    "maior. <b>Dilui em</b> é o tempo que o consumo médio leva pra gastar essa compra."
)


def _pagina(titulo: str, subtitulo: str, miolo: str) -> str:
    base = str(current_app.config.get("PUBLIC_BASE_URL") or "").rstrip("/")
    botao = (
        f'<table role="presentation" cellpadding="0" cellspacing="0" style="margin:4px 0 0;"><tr><td style="background:{_AZUL};border-radius:6px;">'
        f'<a href="{escape(base)}/logistica/estoque" style="{_FONTE}display:inline-block;padding:10px 18px;font-size:13px;font-weight:700;'
        'color:#ffffff;text-decoration:none;">Abrir o Estoque no Sync</a></td></tr></table>'
    ) if base else ""
    return (
        f'<html><body style="margin:0;padding:0;background:#eef2f7;">'
        f'<table role="presentation" cellpadding="0" cellspacing="0" width="100%" style="background:#eef2f7;"><tr><td align="center" style="padding:24px 12px;">'
        f'<table role="presentation" cellpadding="0" cellspacing="0" width="860" style="width:860px;max-width:100%;background:#ffffff;border:1px solid {_BORDA};">'
        f'<tr><td style="{_FONTE}background:{_ESCURO};padding:22px 28px;border-bottom:4px solid {_AZUL};">'
        '<div style="font-size:11px;font-weight:700;letter-spacing:.12em;color:#93c5fd;">COLUMBIA SYNC &nbsp;·&nbsp; ESTOQUE</div>'
        f'<div style="font-size:22px;font-weight:700;color:#ffffff;margin-top:6px;">{escape(titulo)}</div>'
        f'<div style="font-size:13px;color:#cbd5e1;margin-top:4px;">{escape(subtitulo)} &nbsp;·&nbsp; {agora_br().strftime("%d/%m/%Y às %H:%M")}</div>'
        f'</td></tr><tr><td style="{_FONTE}padding:24px 28px 26px;color:{_TEXTO};">{miolo}{botao}</td></tr>'
        f'<tr><td style="{_FONTE}padding:14px 28px;background:#f8fafc;border-top:1px solid {_BORDA};font-size:11px;color:#9ca3af;">'
        "Aviso automático do Sync. Saldos do depósito 1 no GRV; mínimo e lote sugeridos pelo histórico do kardex desde 2019."
        "</td></tr></table></td></tr></table></body></html>"
    )


def _enviar(assunto: str, texto: str, corpo_html: str, destinatarios: list[str]) -> None:
    app = current_app._get_current_object()
    msg = MIMEMultipart("alternative")
    msg["Subject"] = assunto
    msg["From"] = f"Columbia Sync | Estoque <{app.config.get('MAIL_SENDER') or ''}>"
    msg["To"] = ", ".join(destinatarios)
    msg.attach(MIMEText(texto, "plain", "utf-8"))
    msg.attach(MIMEText(corpo_html, "html", "utf-8"))
    enviar_mensagem_smtp(app, msg)


# ------------------------------------------------------------------ reservas

def itens_reserva_sem_saldo(estoque: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """codigo (sem pontuação) -> item com mais reservado do que saldo físico."""
    visoes = {_normalizar_busca(cfg["familia"]): visao for visao, cfg in ESTOQUE_VISOES.items()}
    itens = {}
    for codigo, item in _por_codigo(estoque).items():
        visao = visoes.get(_normalizar_busca(item.get("familia")))
        if not visao:
            continue
        fisico = float(item.get("qtde_total") or 0)
        reservado = float(item.get("qtde_reservada") or 0)
        if reservado <= 0 or reservado - fisico <= TOLERANCIA:
            continue
        itens[_chave(codigo)] = {
            "chave": _chave(codigo),
            "codigo": codigo,
            "descricao": str(item.get("item") or "").strip(),
            "unidade": str(item.get("unidade") or "UN").strip() or "UN",
            "visao": visao,
            "familia": ESTOQUE_VISOES[visao]["label"],
            "fisico": fisico,
            "reservado": reservado,
            "falta": reservado - fisico,
        }
    return itens


def _texto_reserva(reserva: dict[str, Any], unidade: str) -> str:
    orcamento = str(reserva.get("numero_orcamento") or reserva.get("orcamento_descricao") or reserva.get("cod_orcamento") or "").strip()
    versao = str(reserva.get("versao_orcamento") or "").strip()
    numero_os = str(reserva.get("os_descricao") or reserva.get("os_por_orcamento") or "").strip()
    cliente = str(reserva.get("cliente_orcamento") or reserva.get("cliente_os_orcamento") or reserva.get("cliente_os_descricao") or "").strip()
    partes = [f"{_fmt(reserva.get('qtde'))} {unidade}"]
    if numero_os:
        partes.append(f"OS {numero_os}")
    if orcamento:
        partes.append(f"Orç. {orcamento}{'/' + versao if versao else ''}")
    elif not numero_os and str(reserva.get("descricao") or "").strip():
        partes.append(str(reserva.get("descricao")).strip())
    if cliente:
        partes.append(cliente)
    return escape(" · ".join(partes))


def _sugestoes_das_reservas(estoque: dict[str, Any], itens: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """chave -> {plano, sugestao} dos itens comprados. Produto acabado é
    fabricado, não tem planejamento. Histórico fora do ar: o aviso sai sem a
    sugestão, que vale menos que o aviso."""
    sugestoes = {}
    parametros = None
    for visao in {item["visao"] for item in itens if item["visao"] in VISOES_PLANEJAMENTO}:
        try:
            parametros = parametros or planejamento_svc.obter_parametros()
            _, _, planos = _planos_da_visao(_por_codigo(estoque), visao, parametros)
        except Exception:
            current_app.logger.warning("Aviso de reserva: historico de %s nao respondeu.", visao, exc_info=True)
            continue
        for item in itens:
            plano = planos.get(item["chave"]) if item["visao"] == visao else None
            if plano:
                sugestoes[item["chave"]] = {"plano": plano, "sugestao": sugestao_reposicao(plano, -item["falta"])}
    return sugestoes


def verificar_reservas(teste_para: str | None = None) -> dict[str, Any]:
    """Um ciclo da checagem de reservas. Levanta exceção se o GRV ou o SMTP
    falharem - quem chama registra e tenta de novo no próximo ciclo.

    `teste_para`: manda tudo que está sem saldo agora só pra esse endereço e
    não grava nada - o aviso de verdade sai normalmente depois."""
    destinatarios = [teste_para] if teste_para else _destinatarios()
    estoque = buscar_estoque_grv(forcar_atualizacao=True)
    sem_saldo = itens_reserva_sem_saldo(estoque)
    try:
        ocs = buscar_ordens_compra_abertas_grv(codigos=[item["codigo"] for item in sem_saldo.values()], forcar_atualizacao=True)
    except Exception:
        # Sem saber das OCs não dá pra dizer que a reposição está a caminho: avisa.
        current_app.logger.warning("Aviso de reserva: OCs abertas nao responderam.", exc_info=True)
        ocs = {}
    # Fora do aviso E do estado: se a OC for cancelada ou entregue sem resolver, avisa.
    atuais = {chave: item for chave, item in sem_saldo.items() if _pendente_oc(ocs, chave) < item["falta"] - TOLERANCIA}

    estados = {} if teste_para else {e.codigo: e for e in EstoqueAlertaEstado.query.filter_by(tipo=TIPO_RESERVA)}
    novos = [
        item for chave, item in atuais.items()
        if chave not in estados or item["reservado"] > float(estados[chave].quantidade or 0) + TOLERANCIA
    ]

    if novos:
        novos.sort(key=lambda item: item["codigo"])
        try:
            reservas = buscar_reservas_produto_acabado_grv(codigos=[item["codigo"] for item in novos], forcar_atualizacao=True)
        except Exception:
            # O aviso vale mais que o detalhe: sai sem a lista de OS/orçamento.
            current_app.logger.warning("Nao foi possivel detalhar as reservas do aviso de estoque.", exc_info=True)
            reservas = {}
        sugestoes = _sugestoes_das_reservas(estoque, novos)
        linhas = []
        texto = ["Reserva sem saldo para atender:"]
        custo_total = 0.0
        for item in novos:
            un = escape(item["unidade"])
            dados = sugestoes.get(item["chave"]) or {}
            sugestao = dados.get("sugestao")
            custo_total += (sugestao or {}).get("custo") or 0
            curva = f" · curva {dados['plano']['classe_abc']}" if dados.get("plano") else ""
            pendente = _pendente_oc(ocs, item["chave"])
            falta = f'<b style="color:#b91c1c;">{_fmt(item["falta"])} {un}</b>'
            if pendente > 0:
                falta += _pequeno(f"OC aberta cobre só {_fmt(pendente)} {un}")
            detalhe = "<br>".join(_texto_reserva(r, item["unidade"]) for r in (reservas.get(item["chave"]) or [])[:20])
            sem_plano = "item fabricado" if item["visao"] not in VISOES_PLANEJAMENTO else "sem giro: não estocar"
            linhas.append([
                _celula_item(item, item["familia"] + curva),
                f"{_fmt(item['reservado'])} {un}" + _pequeno(f"físico {_fmt(item['fisico'])} {un}"),
                falta,
                f'<div style="min-width:180px;font-size:12px;line-height:1.5;">{detalhe or "—"}</div>',
                *_celula_sugestao(sugestao, item["unidade"], sem_plano),
            ])
            texto.append(
                f"- {item['codigo']} {item['descricao']}: reservado {_fmt(item['reservado'])} {item['unidade']}, "
                f"saldo físico {_fmt(item['fisico'])}, faltam {_fmt(item['falta'])}"
                + (f"; comprar {_fmt(sugestao['comprar'])} ({_reais(sugestao['custo'])}), dilui em {_duracao(sugestao['meses'])}" if sugestao else "")
            )
        primeiro = novos[0]["codigo"]
        assunto = "Estoque: reserva sem saldo - " + (primeiro if len(novos) == 1 else f"{primeiro} e mais {len(novos) - 1}")
        miolo = _cartoes([
            (str(len(novos)), "item sem saldo" if len(novos) == 1 else "itens sem saldo", "#b91c1c"),
            (_reais(custo_total) if custo_total else "—", "compra sugerida", _AZUL),
        ]) + _secao(
            "Reserva maior que o saldo físico", "Falta = reservado − saldo físico no depósito 1.", "#b91c1c", len(novos)
        ) + _tabela(
            [("Item", False), ("Reservado", True), ("Falta p/ reserva", True), ("Reservas", False),
             ("Manter em estoque", True), ("Comprar agora", True), ("Dilui em", True)],
            linhas,
        ) + _nota(_COMO_LER + " Aqui a compra já inclui o que falta pra atender a reserva.")
        if teste_para:
            assunto = "[TESTE] " + assunto
        _enviar(assunto, "\n".join(texto), _pagina("Reserva sem saldo para atender", "Reservado acima do saldo físico", miolo), destinatarios)

    resultado = {
        "sem_saldo": len(atuais),
        "com_oc": len(sem_saldo) - len(atuais),
        "avisados": [item["codigo"] for item in novos],
    }
    if teste_para:
        return resultado
    for chave, estado in estados.items():
        if chave not in atuais:
            db.session.delete(estado)
    for chave, item in atuais.items():
        estado = estados.get(chave)
        if not estado:
            estado = EstoqueAlertaEstado(tipo=TIPO_RESERVA, codigo=chave, desde=agora_br())
            db.session.add(estado)
        estado.quantidade = item["reservado"]
    db.session.commit()
    return resultado


# ------------------------------------------------------------------- curva A

def itens_curva_a_em_alerta(estoque: dict[str, Any]) -> tuple[dict[str, list[dict[str, Any]]], list[str]]:
    """Itens de curva A em alerta por visão, e o rótulo das visões cujo
    histórico não respondeu (ficam fora do dicionário, não como lista vazia)."""
    por_codigo = _por_codigo(estoque)
    parametros = planejamento_svc.obter_parametros()
    por_visao: dict[str, list[dict[str, Any]]] = {}
    falhas = []
    for visao, cfg in ESTOQUE_VISOES.items():
        if visao not in VISOES_PLANEJAMENTO:
            continue
        try:
            familia_itens, cadastro, planos = _planos_da_visao(por_codigo, visao, parametros)
        except Exception:
            current_app.logger.warning("Aviso de estoque: historico de %s nao respondeu.", visao, exc_info=True)
            falhas.append(cfg["label"])
            continue
        linhas = []
        for chave, plano in planos.items():
            if plano["classe_abc"] != "A":
                continue
            saldo = familia_itens[chave]["saldo_disponivel"]
            if saldo <= TOLERANCIA:
                situacao = "zerado"
            elif plano["nivel"] == "critico":
                situacao = "critico"
            elif plano["nivel"] == "atencao":
                situacao = "abaixo_minimo"
            else:
                continue
            codigo, item = cadastro[chave]
            linhas.append({
                "chave": chave,
                "codigo": codigo,
                "descricao": str(item.get("item") or "").strip(),
                "unidade": str(item.get("unidade") or "UN").strip() or "UN",
                "visao": visao,
                "visao_label": cfg["label"],
                "situacao": situacao,
                "saldo_disponivel": saldo,
                "consumo_mensal": plano["consumo_mensal"],
                "cobertura_dias": plano["cobertura_dias"],
                "lead_time_dias": plano["lead_time_dias"],
                "sugestao": sugestao_reposicao(plano, saldo),
            })
        por_visao[visao] = linhas
    return por_visao, falhas


def enviar_resumo_curva_a(teste_para: str | None = None) -> dict[str, Any]:
    """`teste_para`: manda o resumo só pra esse endereço e não grava nada."""
    destinatarios = [teste_para] if teste_para else _destinatarios()
    por_visao, falhas = itens_curva_a_em_alerta(buscar_estoque_grv(forcar_atualizacao=True))
    em_alerta = [item for linhas in por_visao.values() for item in linhas]
    oc_sem_resposta = False
    ocs = {}
    if em_alerta:
        try:
            ocs = buscar_ordens_compra_abertas_grv(codigos=[i["codigo"] for i in em_alerta], forcar_atualizacao=True)
        except Exception:
            # Sem saber das OCs, melhor avisar a mais do que esconder item sem reposição.
            current_app.logger.warning("Aviso de estoque: OCs abertas nao responderam.", exc_info=True)
            oc_sem_resposta = True
    # Com OC aberta a reposição já está a caminho: fora do e-mail e do estado
    # (se a OC chegar e o item continuar em alerta, ele volta como novo).
    itens = [i for i in em_alerta if not ocs.get(i["chave"])]
    com_oc = len(em_alerta) - len(itens)

    estados = {e.codigo: e for e in EstoqueAlertaEstado.query.filter_by(tipo=TIPO_CURVA_A)}
    for item in itens:
        estado = estados.get(item["chave"])
        item["novo"] = not estado or _GRAVIDADE[item["situacao"]] > _GRAVIDADE.get(estado.situacao or "", 0)
        item["desde"] = estado.desde if estado else None

    contagem = {situacao: sum(1 for i in itens if i["situacao"] == situacao) for situacao in SITUACOES}
    novos = sum(1 for i in itens if i["novo"])
    if itens:
        custo_total = sum((i["sugestao"] or {}).get("custo") or 0 for i in itens)
        miolo = _cartoes([
            (str(contagem["zerado"]), "sem estoque", SITUACOES["zerado"][2]),
            (str(contagem["critico"]), "críticos", SITUACOES["critico"][2]),
            (str(contagem["abaixo_minimo"]), "abaixo do mínimo", SITUACOES["abaixo_minimo"][2]),
            (_reais(custo_total) if custo_total else "—", "compra sugerida", _AZUL),
        ])
        texto = []
        for situacao, (titulo, explicacao, cor) in SITUACOES.items():
            bloco = sorted((i for i in itens if i["situacao"] == situacao), key=lambda i: (not i["novo"], i["codigo"]))
            if not bloco:
                continue
            linhas = []
            texto.append(f"{titulo} ({len(bloco)}):")
            for i in bloco:
                un = escape(i["unidade"])
                sugestao = i["sugestao"]
                desde = i["desde"].strftime("%d/%m") if i["desde"] else "hoje"
                if situacao == "zerado":
                    cobertura = ""
                else:
                    cobertura = f"cobre {i['cobertura_dias']} dias" if i["cobertura_dias"] is not None else "demanda esporádica"
                linhas.append([
                    _celula_item(i, f"{i['visao_label']} · em alerta desde {desde}", i["novo"]),
                    f"<b>{_fmt(i['saldo_disponivel'])} {un}</b>" + (_pequeno(escape(cobertura)) if cobertura else ""),
                    f"{_fmt(i['consumo_mensal'])} {un}" + _pequeno(f"prazo do fornecedor {_fmt(i['lead_time_dias'], 0)} d"),
                    *_celula_sugestao(sugestao, i["unidade"], "sem sugestão"),
                ])
                texto.append(
                    f"- {i['codigo']} {i['descricao']}: disponível {_fmt(i['saldo_disponivel'])} {i['unidade']}"
                    + (f"; comprar {_fmt(sugestao['comprar'])} ({_reais(sugestao['custo'])}), dilui em {_duracao(sugestao['meses'])}" if sugestao else "")
                    + (" (NOVO)" if i["novo"] else "")
                )
            miolo += _secao(titulo, explicacao, cor, len(bloco)) + _tabela(
                [("Item", False), ("Disponível", True), ("Consumo/mês", True),
                 ("Manter em estoque", True), ("Comprar agora", True), ("Dilui em", True)],
                linhas,
            )
        miolo += _nota(_COMO_LER + " <b>Curva A</b> são os itens que tiveram saída em todos os últimos 6 meses.")
        avisos = []
        if oc_sem_resposta:
            avisos.append("As ordens de compra abertas não responderam: a lista pode incluir item que já tem reposição a caminho.")
        if falhas:
            avisos.append("O histórico do GRV não respondeu para: " + ", ".join(falhas) + ". Esses itens ficaram fora deste aviso.")
        if com_oc:
            de_fora = (
                f"{com_oc} {'item' if com_oc == 1 else 'itens'} de curva A em alerta "
                f"{'ficou' if com_oc == 1 else 'ficaram'} de fora por já ter ordem de compra aberta."
            )
            miolo += _nota(escape(de_fora))
            texto.append(de_fora)
        for aviso in avisos:
            miolo += _nota(escape(aviso), "#92400e", "#fffbeb")
        texto.extend(avisos)
        assunto = (
            f"Estoque curva A: {contagem['zerado']} sem estoque, {contagem['critico']} críticos, "
            f"{contagem['abaixo_minimo']} abaixo do mínimo" + (f" ({novos} novos)" if novos else "")
        )
        subtitulo = "Itens de curva A sem reposição a caminho"
        if teste_para:
            assunto = "[TESTE] " + assunto
        _enviar(assunto, "\n".join(texto), _pagina("Curva A em alerta", subtitulo, miolo), destinatarios)

    resultado = {"enviado": bool(itens), "novos": novos, "com_oc": com_oc, "sem_resposta": falhas, **contagem}
    if teste_para:
        return resultado
    atuais = {i["chave"]: i for i in itens}
    for chave, estado in estados.items():
        # Só sai do estado quem é de uma visão que respondeu e não está mais em alerta.
        if chave not in atuais and estado.visao in por_visao:
            db.session.delete(estado)
    for chave, item in atuais.items():
        estado = estados.get(chave)
        if not estado:
            estado = EstoqueAlertaEstado(tipo=TIPO_CURVA_A, codigo=chave, desde=agora_br())
            db.session.add(estado)
        estado.visao = item["visao"]
        estado.situacao = item["situacao"]
    db.session.commit()
    return resultado
