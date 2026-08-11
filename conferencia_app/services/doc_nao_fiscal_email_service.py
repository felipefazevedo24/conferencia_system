"""Notificações por e-mail para documentos não fiscais (faturas, contas, etc.)

Destinatários fixos: fiscal@colmac.com, compras@colmac.com, felaze@colmac.com.
Enviado de forma assíncrona para não bloquear a requisição.
"""
import threading
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from flask import current_app

from .smtp_service import enviar_mensagem_smtp

_DEST_DOC_NAO_FISCAL = ["fiscal@colmac.com", "compras@colmac.com", "felaze@colmac.com"]

_LABELS_TIPO = {
    "FATURA": "Fatura",
    "DEBITO": "Nota de Débito",
    "CONTA": "Conta",
    "OUTRO": "Documento",
}


def _send_async(app, msg, smtp_server, smtp_port, sender, password):
    with app.app_context():
        try:
            enviar_mensagem_smtp(app, msg, smtp_server=smtp_server, smtp_port=smtp_port,
                                 sender=sender, password=password)
            app.logger.info("[doc_nao_fiscal_email] Enviado para %s", msg["To"])
        except Exception as exc:
            app.logger.error("[doc_nao_fiscal_email] Falha ao enviar: %s", exc)


def notificar_doc_nao_fiscal(
    evento: str,          # "importado" | "lancado"
    numero: str,
    tipo: str,
    fornecedor: str,
    pedido_compra: str,
    usuario: str,
    codigo_erp: str = "",
) -> None:
    """Envia notificação quando um documento não fiscal é registrado ou lançado."""
    app = current_app._get_current_object()
    smtp_server = app.config.get("MAIL_SMTP_SERVER", "smtp.gmail.com")
    smtp_port   = app.config.get("MAIL_SMTP_PORT", 587)
    sender      = app.config.get("MAIL_SENDER", "")
    password    = app.config.get("MAIL_PASSWORD", "")
    sender_name = app.config.get("MAIL_SENDER_NAME", "Columbia Sync")

    if not sender or not password:
        app.logger.warning("[doc_nao_fiscal_email] SMTP não configurado. Pulando envio.")
        return

    tipo_label = _LABELS_TIPO.get(tipo, tipo)

    if evento == "importado":
        assunto = f"[Sync] Novo documento nao fiscal — {tipo_label} {numero}"
        cor = "#0f62c9"
        icone = "&#128196;"  # 📄
        titulo = f"Novo documento nao fiscal registrado"
        linha_erp = ""
    else:
        assunto = f"[Sync] Documento nao fiscal lancado — {tipo_label} {numero}"
        cor = "#059669"
        icone = "&#9989;"   # ✅
        titulo = "Documento nao fiscal lancado no ERP"
        linha_erp = (
            f'<tr><td style="padding:7px 0;font-weight:bold;width:140px;">Codigo ERP:</td>'
            f'<td style="padding:7px 0;">{codigo_erp or "&mdash;"}</td></tr>'
        )

    linhas_tabela = (
        f'<tr><td style="padding:7px 0;font-weight:bold;width:140px;">Tipo:</td><td style="padding:7px 0;">{tipo_label}</td></tr>'
        f'<tr><td style="padding:7px 0;font-weight:bold;">Numero:</td><td style="padding:7px 0;">{numero}</td></tr>'
        f'<tr><td style="padding:7px 0;font-weight:bold;">Fornecedor:</td><td style="padding:7px 0;">{fornecedor or "&mdash;"}</td></tr>'
        f'<tr><td style="padding:7px 0;font-weight:bold;">OC vinculada:</td><td style="padding:7px 0;">{pedido_compra or "&mdash;"}</td></tr>'
        f'{linha_erp}'
        f'<tr><td style="padding:7px 0;font-weight:bold;">Usuario:</td><td style="padding:7px 0;">{usuario}</td></tr>'
    )

    html = (
        f'<html><body style="font-family:Arial,sans-serif;background:#f4f6f8;padding:20px;">'
        f'<div style="max-width:580px;margin:auto;background:#fff;border-radius:10px;padding:24px;'
        f'border-top:4px solid {cor};box-shadow:0 2px 10px rgba(15,23,42,.08);">'
        f'<h2 style="color:{cor};margin:0 0 6px;font-size:18px;">{icone} {titulo}</h2>'
        f'<p style="color:#64748b;font-size:13px;margin:0 0 18px;">Registro automatico do Columbia Sync &mdash; Documento de Entrada.</p>'
        f'<table style="width:100%;font-size:14px;color:#334155;border-collapse:collapse;">{linhas_tabela}</table>'
        f'<p style="margin-top:22px;font-size:11px;color:#94a3b8;">E-mail automatico. Nao responda.</p>'
        f'</div></body></html>'
    )

    destinatarios = ", ".join(_DEST_DOC_NAO_FISCAL)
    msg = MIMEMultipart("alternative")
    msg["Subject"] = assunto
    msg["From"] = f"{sender_name} <{sender}>"
    msg["To"] = destinatarios
    msg.attach(MIMEText(html, "html"))

    thread = threading.Thread(
        target=_send_async,
        args=(app, msg, smtp_server, smtp_port, sender, password),
    )
    thread.daemon = True
    thread.start()
