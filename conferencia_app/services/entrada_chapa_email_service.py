"""Aviso de entrada fiscal de chapas/barras com controle de lote."""
from __future__ import annotations

import json
import os
import re
import threading
from datetime import date, datetime
from pathlib import Path
from typing import Any
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import requests
from flask import current_app

from ..extensions import db
from ..models import EmailEntradaChapa, ItemNota
from .smtp_service import enviar_mensagem_smtp

DATA_MINIMA_ENTRADA_CHAPA = date(2026, 5, 13)


def _split_lista(valor: str | None) -> list[str]:
    itens: list[str] = []
    for parte in re.split(r"[,;\s]+", str(valor or "")):
        parte = parte.strip()
        if parte and parte not in itens:
            itens.append(parte)
    return itens


def _split_emails(valor: str | None) -> list[str]:
    return [e for e in _split_lista(valor) if "@" in e and "." in e]


def _cfg_bridge(app) -> dict[str, Any]:
    path = Path(app.instance_path) / "erp_lancamento_config.json"
    arquivo: dict[str, Any] = {}
    if path.exists():
        try:
            arquivo = json.loads(path.read_text(encoding="utf-8")) or {}
        except Exception:
            arquivo = {}
    return {
        "api_url": str(
            os.environ.get("ERP_LANCAMENTO_API_URL")
            or arquivo.get("api_url")
            or app.config.get("ERP_LANCAMENTO_API_URL")
            or ""
        ).strip().rstrip("/"),
        "api_token": str(
            os.environ.get("ERP_LANCAMENTO_API_TOKEN")
            or arquivo.get("api_token")
            or app.config.get("ERP_LANCAMENTO_API_TOKEN")
            or ""
        ),
        "timeout": int(
            os.environ.get("ERP_LANCAMENTO_API_TIMEOUT")
            or arquivo.get("api_timeout")
            or app.config.get("ERP_LANCAMENTO_API_TIMEOUT")
            or 30
        ),
    }


def _post_bridge(path: str, payload: dict[str, Any]) -> dict[str, Any]:
    app = current_app._get_current_object()
    cfg = _cfg_bridge(app)
    if not cfg["api_url"]:
        raise ValueError("ERP_LANCAMENTO_API_URL/api_url nao configurada.")
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "ngrok-skip-browser-warning": "true",
        "User-Agent": "ColumbiaSync/Entrada-Chapa",
    }
    if cfg["api_token"]:
        headers["Authorization"] = f"Bearer {cfg['api_token']}"
    resp = requests.post(f"{cfg['api_url']}{path}", headers=headers, json=payload, timeout=cfg["timeout"])
    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, dict) or not data.get("sucesso"):
        raise RuntimeError(str((data or {}).get("erro") or "Resposta invalida da API ERP."))
    return data


def _entrada_local(numero_nota: str) -> dict[str, Any] | None:
    itens = (
        ItemNota.query
        .filter(ItemNota.numero_nota == str(numero_nota))
        .filter(ItemNota.status.in_(["Lançado", "LanÃ§ado", "Lancado"]))
        .all()
    )
    if not itens:
        return None
    numero_ar = next((str(i.numero_lancamento or "").strip() for i in itens if i.numero_lancamento), "")
    return {
        "numero_ar": numero_ar,
        "codigo_lancamento": numero_ar,
        "numero_nota": str(numero_nota),
        "chave_acesso": next((i.chave_acesso for i in itens if i.chave_acesso), ""),
        "parceiro_nome": next((i.fornecedor for i in itens if i.fornecedor), ""),
        "cfop_cabecalho": "",
        "itens": [
            {
                "cfop": i.cfop or "",
                "natureza_operacao": "",
                "cod_interno": i.codigo or "",
                "descricao": i.descricao or "",
                "quantidade": i.qtd_real or 0,
                "unidade": i.unidade_comercial or "",
                "controle_lote_serie": 0,
                "tipo_controle": 0,
                "lote": "",
            }
            for i in itens
        ],
    }

def _buscar_entrada(numero_nota: str, numero_ar: str | None = None, chave: str | None = None) -> dict[str, Any] | None:
    try:
        data = _post_bridge(
            "/api/erp/entrada-chapa",
            {"numero_nota": numero_nota, "numero_ar": numero_ar or "", "chave": chave or ""},
        )
        entrada = data.get("entrada")
        if isinstance(entrada, dict):
            return entrada
    except Exception as exc:
        current_app.logger.warning("Entrada chapa: falha ao consultar bridge, usando dados locais: %s", exc)
    return _entrada_local(numero_nota)


def _buscar_entradas_recebidas_desde(app, data_minima: date) -> list[dict[str, Any]]:
    data = _post_bridge(
        "/api/erp/entrada-chapa-desde",
        {
            "data_recebimento_minima": data_minima.isoformat(),
            "cfops": _split_lista(app.config.get("ENTRADA_CHAPA_CFOPS", "1901,1915")),
            "controles_lote": _split_lista(app.config.get("ENTRADA_CHAPA_CONTROLE_LOTE_VALORES", "1,3")),
            "limite": 500,
        },
    )
    entradas = data.get("entradas") or []
    return [entrada for entrada in entradas if isinstance(entrada, dict)]


def _eh_entrada_chapa(entrada: dict[str, Any], app) -> tuple[bool, list[str], list[dict[str, Any]]]:
    controles_alvo = set(_split_lista(app.config.get("ENTRADA_CHAPA_CONTROLE_LOTE_VALORES", "1,3")))
    itens = [i for i in (entrada.get("itens") or []) if isinstance(i, dict)]
    itens_relevantes = []
    cfops_encontrados = set()

    for item in itens:
        cfop = str(item.get("cfop") or entrada.get("cfop_cabecalho") or "").strip()[:4]
        controle = str(item.get("controle_lote_serie") or "").strip()
        tipo_controle = str(item.get("tipo_controle") or "").strip()
        if cfop:
            cfops_encontrados.add(cfop)
        if (controle and controle in controles_alvo) or (tipo_controle and tipo_controle in controles_alvo):
            itens_relevantes.append(item)

    return bool(itens_relevantes), sorted(cfops_encontrados), itens_relevantes


def _fmt_qtd(valor: Any) -> str:
    try:
        num = float(valor or 0)
        return f"{num:,.3f}".replace(",", "X").replace(".", ",").replace("X", ".").rstrip("0").rstrip(",")
    except Exception:
        return str(valor or "0")


def _parse_date(valor: Any) -> date | None:
    if not valor:
        return None
    if isinstance(valor, datetime):
        return valor.date()
    if isinstance(valor, date):
        return valor
    try:
        return datetime.fromisoformat(str(valor)[:10]).date()
    except Exception:
        return None


def _data_lancamento_valida(entrada: dict[str, Any]) -> bool:
    data_ref = _parse_date(entrada.get("dt_recebimento"))
    return bool(data_ref and data_ref >= DATA_MINIMA_ENTRADA_CHAPA)


def _fmt_data(valor: Any) -> str:
    data = _parse_date(valor)
    return data.strftime("%d/%m/%Y") if data else "-"


def _texto(valor: Any) -> str:
    texto = str(valor or "")
    return (
        texto.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _und_chapas_por_codigo(numero_nota: str) -> dict[str, float]:
    """Quantidade de chapas (UND) informada pelo conferente, indexada por código.

    O valor é auditado na conferência de recebimento (ItemNota.qtd_chapas_und)
    e usado para complementar o aviso — a NF vem em KG, mas a UND é o que
    interessa para a etiquetagem/lote.
    """
    mapa: dict[str, float] = {}
    try:
        rows = (
            ItemNota.query
            .filter(ItemNota.numero_nota == str(numero_nota))
            .filter(ItemNota.qtd_chapas_und.isnot(None))
            .all()
        )
    except Exception:
        return mapa
    for r in rows:
        codigo = str(r.codigo or "").strip()
        if codigo and r.qtd_chapas_und:
            mapa[codigo] = float(r.qtd_chapas_und)
    return mapa


def _fmt_und(valor: Any) -> str:
    """Formata a quantidade de chapas em UND (inteiro quando possível)."""
    if valor is None:
        return "-"
    try:
        num = float(valor)
    except (TypeError, ValueError):
        return "-"
    if num <= 0:
        return "-"
    if num == int(num):
        return str(int(num))
    return f"{num:.3f}".rstrip("0").rstrip(".")


def _html(entrada: dict[str, Any], itens: list[dict[str, Any]], cfops: list[str]) -> str:
    linhas = []
    for item in itens:
        lote = item.get("lote") or entrada.get("numero_ar") or "Nao informado"
        natureza = item.get("natureza_operacao") or item.get("cfop") or "-"
        und_chapas = _fmt_und(item.get("qtd_chapas_und"))
        linhas.append(
            "<tr style=\"background:#ffffff;color:#1f2937\">"
            f"<td style=\"padding:10px;border:1px solid #dbe3ef;background:#ffffff;color:#1f2937\">{_texto(item.get('cod_interno') or '-')}</td>"
            f"<td style=\"padding:10px;border:1px solid #dbe3ef;background:#ffffff;color:#1f2937\">{_texto(item.get('descricao') or '-')}</td>"
            f"<td style=\"padding:10px;border:1px solid #dbe3ef;background:#ffffff;color:#1f2937\">{_fmt_qtd(item.get('quantidade'))} {_texto(item.get('unidade') or '')}</td>"
            f"<td style=\"padding:10px;border:1px solid #dbe3ef;background:#ffffff;color:#0f172a;font-weight:800;text-align:center\">{und_chapas}</td>"
            f"<td style=\"padding:10px;border:1px solid #dbe3ef;background:#ffffff;color:#1f2937\">{_texto(natureza)}</td>"
            f"<td style=\"padding:10px;border:1px solid #dbe3ef;background:#ffffff;color:#0f172a;font-weight:700\">{_texto(lote)}</td>"
            "</tr>"
        )
    numero_ar = entrada.get("numero_ar") or "Nao informado"
    return f"""\
<!doctype html>
<html lang="pt-BR">
<head><meta charset="UTF-8"><meta name="color-scheme" content="light"><meta name="supported-color-schemes" content="light"></head>
<body style="margin:0;background:#eef3f8;font-family:Arial,Helvetica,sans-serif;color:#0f172a;padding:28px 12px;color-scheme:light">
  <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:#eef3f8"><tr><td align="center" style="background:#eef3f8">
  <div style="max-width:760px;width:100%;margin:auto;background:#ffffff;border:1px solid #d7e0ea;border-radius:10px;overflow:hidden">
    <div style="background:#173a5e;color:#fff;padding:20px 26px">
      <div style="font-size:11px;letter-spacing:1.6px;text-transform:uppercase;color:#b8d7f2;font-weight:700">Controle de aviso de recebimento</div>
      <h2 style="margin:5px 0 0;font-size:21px;line-height:1.25;color:#ffffff">RECEBIMENTO DE MATERIAIS COM CONTROLE DE LOTE</h2>
    </div>
    <div style="padding:22px 26px;background:#ffffff;color:#0f172a">
      <p style="margin:0 0 16px;font-size:14px;line-height:1.6;color:#334155">Uma nota fiscal com controle de lote foi lan&ccedil;ada no GRV e precisa ser identificada com a etiqueta.</p>
      <table width="100%" cellspacing="0" cellpadding="0" style="border-collapse:collapse;margin:14px 0 18px;font-size:13px;background:#ffffff;color:#1f2937">
        <tr><td style="padding:10px;border:1px solid #dbe3ef;background:#f3f7fb;color:#0f172a;font-weight:700;width:170px">NF</td><td style="padding:10px;border:1px solid #dbe3ef;background:#ffffff;color:#1f2937">{_texto(entrada.get('numero_nota') or '-')}</td></tr>
        <tr><td style="padding:10px;border:1px solid #dbe3ef;background:#f3f7fb;color:#0f172a;font-weight:700">AR / lote</td><td style="padding:10px;border:1px solid #dbe3ef;background:#ffffff;color:#0f172a"><strong>{_texto(numero_ar)}</strong></td></tr>
        <tr><td style="padding:10px;border:1px solid #dbe3ef;background:#f3f7fb;color:#0f172a;font-weight:700">Cliente/fornecedor</td><td style="padding:10px;border:1px solid #dbe3ef;background:#ffffff;color:#1f2937">{_texto(entrada.get('parceiro_nome') or '-')}</td></tr>
        <tr><td style="padding:10px;border:1px solid #dbe3ef;background:#f3f7fb;color:#0f172a;font-weight:700">Data de recebimento</td><td style="padding:10px;border:1px solid #dbe3ef;background:#ffffff;color:#1f2937">{_fmt_data(entrada.get('dt_recebimento'))}</td></tr>
      </table>
      <table width="100%" cellspacing="0" cellpadding="0" style="border-collapse:collapse;font-size:12.5px;background:#ffffff;color:#1f2937">
        <thead>
          <tr style="background:#173a5e;color:#fff;text-align:left">
            <th style="padding:10px;border:1px solid #173a5e;background:#173a5e;color:#ffffff">C&oacute;digo</th>
            <th style="padding:10px;border:1px solid #173a5e;background:#173a5e;color:#ffffff">Descri&ccedil;&atilde;o</th>
            <th style="padding:10px;border:1px solid #173a5e;background:#173a5e;color:#ffffff">Quantidade</th>
            <th style="padding:10px;border:1px solid #173a5e;background:#173a5e;color:#ffffff;text-align:center">Qtd. chapas (UND)</th>
            <th style="padding:10px;border:1px solid #173a5e;background:#173a5e;color:#ffffff">Natureza da opera&ccedil;&atilde;o</th>
            <th style="padding:10px;border:1px solid #173a5e;background:#173a5e;color:#ffffff">AR/Lote</th>
          </tr>
        </thead>
        <tbody>{''.join(linhas)}</tbody>
      </table>
      <div style="margin-top:20px;padding-top:14px;border-top:1px solid #e2e8f0;font-size:11px;color:#64748b;text-align:right">
        Powered by <strong style="color:#173a5e">Columbia Sync</strong>
      </div>
    </div>
  </div>
  </td></tr></table>
</body>
</html>"""

def _enviar_email(app, entrada: dict[str, Any], itens: list[dict[str, Any]], cfops: list[str], usuario: str, origem: str) -> dict:
    destinatarios = _split_emails(app.config.get("ENTRADA_CHAPA_EMAIL_DESTINATARIOS"))
    cc = _split_emails(app.config.get("ENTRADA_CHAPA_EMAIL_CC"))
    if not destinatarios:
        return {"sucesso": False, "erro": "ENTRADA_CHAPA_EMAIL_DESTINATARIOS nao configurado."}

    numero_nota = str(entrada.get("numero_nota") or "").strip()
    numero_ar = str(entrada.get("numero_ar") or "").strip()
    numero_ar_log = (numero_ar or "Nao informado")[:80]
    existente = EmailEntradaChapa.query.filter_by(numero_nota=numero_nota, numero_ar=numero_ar_log).first()
    if existente and existente.status == "Enviado":
        return {"sucesso": True, "ignorado": True, "log_id": existente.id}

    # Complementa cada item com a quantidade de chapas (UND) informada e
    # auditada na conferência de recebimento (a NF vem em KG).
    und_por_codigo = _und_chapas_por_codigo(numero_nota)
    if und_por_codigo:
        for item in itens:
            codigo = str(item.get("cod_interno") or "").strip()
            if codigo and codigo in und_por_codigo:
                item["qtd_chapas_und"] = und_por_codigo[codigo]

    assunto = f"CONTROLE DE LOTE - NF {numero_nota} - AR {numero_ar or 'Nao informado'}"
    log = existente or EmailEntradaChapa(numero_nota=numero_nota, numero_ar=numero_ar_log, criado_em=datetime.now())
    log.chave_acesso = str(entrada.get("chave_acesso") or "")[:44]
    log.parceiro_nome = str(entrada.get("parceiro_nome") or "")[:220]
    log.cfops = ", ".join(cfops)[:120]
    log.destinatarios = ", ".join(destinatarios + cc)
    log.assunto = assunto
    log.status = "Pendente"
    log.erro_mensagem = None
    log.disparado_por = usuario
    log.origem = origem
    if not existente:
        db.session.add(log)
    db.session.commit()

    try:
        sender = app.config.get("MAIL_SENDER") or ""
        password = app.config.get("MAIL_PASSWORD") or ""
        smtp_server = app.config.get("MAIL_SMTP_SERVER")
        smtp_port = int(app.config.get("MAIL_SMTP_PORT", 587))
        sender_name = app.config.get("MAIL_SENDER_NAME", "Columbia Sync")
        if not sender or not password:
            raise RuntimeError("SMTP nao configurado (MAIL_SENDER/MAIL_PASSWORD).")

        msg = MIMEMultipart("alternative")
        msg["Subject"] = assunto
        msg["From"] = f"{sender_name} <{sender}>"
        msg["To"] = ", ".join(destinatarios)
        if cc:
            msg["Cc"] = ", ".join(cc)
        msg.attach(MIMEText(f"NF {numero_nota} lancada. AR/lote: {numero_ar or 'Nao informado'}.", "plain", "utf-8"))
        msg.attach(MIMEText(_html(entrada, itens, cfops), "html", "utf-8"))

        enviar_mensagem_smtp(
            app,
            msg,
            smtp_server=smtp_server,
            smtp_port=smtp_port,
            sender=sender,
            password=password,
        )

        log.status = "Enviado"
        log.tentativas = (log.tentativas or 0) + 1
        log.enviado_em = datetime.now()
        db.session.commit()
        return {"sucesso": True, "log_id": log.id}
    except Exception as exc:
        log.status = "Falha"
        log.tentativas = (log.tentativas or 0) + 1
        log.erro_mensagem = str(exc)[:800]
        db.session.commit()
        app.logger.exception("Falha ao enviar aviso de entrada de chapa NF %s", numero_nota)
        return {"sucesso": False, "erro": str(exc), "log_id": log.id}


def notificar_entrada_chapa_lancada(
    numero_nota: str,
    *,
    numero_ar: str | None = None,
    chave: str | None = None,
    usuario: str = "Sistema",
    origem: str = "Sistema",
    assincrono: bool = True,
) -> dict:
    app = current_app._get_current_object()
    if not app.config.get("ENTRADA_CHAPA_EMAIL_ENABLED", True):
        return {"sucesso": True, "ignorado": True, "motivo": "desabilitado"}

    def _run() -> dict:
        with app.app_context():
            entrada = _buscar_entrada(str(numero_nota), numero_ar, chave)
            if not entrada:
                return {"sucesso": True, "ignorado": True, "motivo": "entrada_nao_encontrada"}
            if not _data_lancamento_valida(entrada):
                return {"sucesso": True, "ignorado": True, "motivo": "antes_de_2026_05_13"}
            eh_chapa, cfops, itens = _eh_entrada_chapa(entrada, app)
            if not eh_chapa:
                return {"sucesso": True, "ignorado": True, "motivo": "nao_eh_chapa_lote"}
            return _enviar_email(app, entrada, itens, cfops, usuario, origem)

    if assincrono:
        threading.Thread(target=_run, daemon=True, name=f"entrada-chapa-email-{numero_nota}").start()
        return {"sucesso": True, "pendente": True}
    return _run()


def _executar_varredura_entradas_chapa_legado(usuario: str = "Sistema", origem: str = "Varredura") -> dict[str, Any]:
    """Tenta enviar avisos para NFs ja lancadas no Sync.

    Serve para quando a automacao e ativada depois que as NFs do dia ja foram
    lancadas. A validacao fina de data de recebimento e lote continua vindo do ERP.
    """
    registros = (
        db.session.query(ItemNota.numero_nota, ItemNota.numero_lancamento, ItemNota.chave_acesso)
        .filter(ItemNota.status.in_(["Lançado", "LanÃ§ado", "Lancado"]))
        .filter(ItemNota.numero_lancamento.isnot(None))
        .distinct()
        .all()
    )
    resumo = {"consultadas": 0, "enviadas": 0, "ignoradas": 0, "falhas": 0}
    for numero_nota, numero_ar, chave in registros:
        if not numero_nota:
            continue
        resumo["consultadas"] += 1
        resultado = notificar_entrada_chapa_lancada(
            str(numero_nota),
            numero_ar=str(numero_ar or ""),
            chave=str(chave or ""),
            usuario=usuario,
            origem=origem,
            assincrono=False,
        )
        if resultado.get("sucesso") and resultado.get("log_id") and not resultado.get("ignorado"):
            resumo["enviadas"] += 1
        elif resultado.get("sucesso"):
            resumo["ignoradas"] += 1
        else:
            resumo["falhas"] += 1
    return resumo


def executar_varredura_entradas_chapa(usuario: str = "Sistema", origem: str = "Varredura") -> dict[str, Any]:
    """Envia avisos somente para entradas recebidas a partir de 13/05/2026."""
    resumo = {"consultadas": 0, "enviadas": 0, "ignoradas": 0, "falhas": 0}
    app = current_app._get_current_object()
    if not app.config.get("ENTRADA_CHAPA_EMAIL_ENABLED", True):
        resumo["ignoradas"] = 1
        return resumo

    try:
        entradas = _buscar_entradas_recebidas_desde(app, DATA_MINIMA_ENTRADA_CHAPA)
    except Exception as exc:
        app.logger.exception("Falha ao varrer entradas de chapa recebidas no ERP")
        resumo["falhas"] = 1
        resumo["erro"] = str(exc)
        return resumo

    for entrada in entradas:
        resumo["consultadas"] += 1
        if not _data_lancamento_valida(entrada):
            resumo["ignoradas"] += 1
            continue
        eh_chapa, cfops, itens = _eh_entrada_chapa(entrada, app)
        if not eh_chapa:
            resumo["ignoradas"] += 1
            continue
        resultado = _enviar_email(app, entrada, itens, cfops, usuario, origem)
        if resultado.get("sucesso") and resultado.get("log_id") and not resultado.get("ignorado"):
            resumo["enviadas"] += 1
        elif resultado.get("sucesso"):
            resumo["ignoradas"] += 1
        else:
            resumo["falhas"] += 1
    return resumo
