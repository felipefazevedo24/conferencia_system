import smtplib
import threading
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from flask import current_app


def _send_async(app, msg, smtp_server, smtp_port, sender, password):
    """Send email in background thread to avoid blocking the request."""
    with app.app_context():
        try:
            with smtplib.SMTP(smtp_server, smtp_port, timeout=15) as server:
                server.starttls()
                server.login(sender, password)
                server.send_message(msg)
                app.logger.info("E-mail enviado para %s", msg["To"])
        except Exception as e:
            app.logger.error("Erro ao enviar e-mail: %s", e)


def enviar_email_registro(destinatario_email: str, username: str, role: str, url_login: str):
    """Send welcome email when a new user is registered."""
    app = current_app._get_current_object()
    smtp_server = app.config.get("MAIL_SMTP_SERVER", "smtp.gmail.com")
    smtp_port = app.config.get("MAIL_SMTP_PORT", 587)
    sender = app.config.get("MAIL_SENDER", "")
    password = app.config.get("MAIL_PASSWORD", "")
    sender_name = app.config.get("MAIL_SENDER_NAME", "Columbia Sync")

    if not sender or not password:
        app.logger.warning("E-mail não configurado. Pulando envio.")
        return

    msg = MIMEMultipart("alternative")
    msg["Subject"] = "Bem-vindo ao Columbia Sync"
    msg["From"] = f"{sender_name} <{sender}>"
    msg["To"] = destinatario_email

    html = f"""\
    <html>
    <body style="font-family: Arial, sans-serif; background: #f4f4f4; padding: 20px;">
      <div style="max-width: 520px; margin: auto; background: #fff; border-radius: 8px; padding: 30px; box-shadow: 0 2px 8px rgba(0,0,0,0.08);">
        <h2 style="color: #333; margin-bottom: 4px;">Bem-vindo ao Columbia Sync!</h2>
        <p style="color: #666; font-size: 14px;">Seu acesso foi criado com sucesso.</p>
        <hr style="border: none; border-top: 1px solid #eee; margin: 20px 0;">
        <table style="font-size: 15px; color: #333;">
          <tr><td style="padding: 6px 12px 6px 0; font-weight: bold;">Usuário:</td><td>{username}</td></tr>
          <tr><td style="padding: 6px 12px 6px 0; font-weight: bold;">Perfil:</td><td>{role}</td></tr>
          <tr><td style="padding: 6px 12px 6px 0; font-weight: bold;">Senha temporária:</td><td>HyC!4DVaFV</td></tr>
        </table>
        <hr style="border: none; border-top: 1px solid #eee; margin: 20px 0;">
        <p style="font-size: 14px; color: #555;">
          No seu primeiro acesso, você deverá cadastrar uma senha usando o e-mail registrado.
        </p>
        <a href="{url_login}" style="display: inline-block; margin-top: 12px; padding: 10px 24px; background: #0d6efd; color: #fff; text-decoration: none; border-radius: 5px; font-size: 14px;">
          Acessar o Sistema
        </a>
        <p style="margin-top: 24px; font-size: 12px; color: #999;">
          Este é um e-mail automático. Não responda.
        </p>
      </div>
    </body>
    </html>
    """

    msg.attach(MIMEText(html, "html"))

    thread = threading.Thread(
        target=_send_async,
        args=(app, msg, smtp_server, smtp_port, sender, password),
    )
    thread.daemon = True
    thread.start()


def enviar_email_solicitacao_epi(
    destinatarios_emails: list,
    solicitante_nome: str,
    item_nome: str,
    quantidade: int,
    tamanho: str,
    motivo: str,
    url_admin: str,
):
    """Notifica gestores quando ha uma nova solicitacao de EPI/Uniforme."""
    app = current_app._get_current_object()
    smtp_server = app.config.get("MAIL_SMTP_SERVER", "smtp.gmail.com")
    smtp_port = app.config.get("MAIL_SMTP_PORT", 587)
    sender = app.config.get("MAIL_SENDER", "")
    password = app.config.get("MAIL_PASSWORD", "")
    sender_name = app.config.get("MAIL_SENDER_NAME", "Columbia Sync")

    destinatarios = [e for e in (destinatarios_emails or []) if e]
    if not destinatarios:
        return
    if not sender or not password:
        app.logger.warning("E-mail nao configurado. Pulando envio Facilities.")
        return

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"[Facilities] Nova solicitacao de EPI - {solicitante_nome}"
    msg["From"] = f"{sender_name} <{sender}>"
    msg["To"] = ", ".join(destinatarios)

    html = f"""\
    <html><body style="font-family: Arial, sans-serif; background:#f4f4f4; padding:20px;">
      <div style="max-width:560px; margin:auto; background:#fff; border-radius:8px; padding:24px;">
        <h2 style="color:#059669; margin:0 0 8px;">Nova solicitacao de EPI/Uniforme</h2>
        <p style="color:#666; font-size:14px; margin:0 0 16px;">Aguardando sua aprovacao no painel Facilities.</p>
        <table style="width:100%; font-size:14px; color:#333;">
          <tr><td style="padding:6px 0; font-weight:bold; width:140px;">Solicitante:</td><td>{solicitante_nome}</td></tr>
          <tr><td style="padding:6px 0; font-weight:bold;">Item:</td><td>{item_nome}</td></tr>
          <tr><td style="padding:6px 0; font-weight:bold;">Tamanho:</td><td>{tamanho or '-'}</td></tr>
          <tr><td style="padding:6px 0; font-weight:bold;">Quantidade:</td><td>{quantidade}</td></tr>
          <tr><td style="padding:6px 0; font-weight:bold; vertical-align:top;">Motivo:</td><td>{motivo or '-'}</td></tr>
        </table>
        <a href="{url_admin}" style="display:inline-block; margin-top:18px; padding:10px 22px; background:#059669; color:#fff; text-decoration:none; border-radius:6px; font-size:14px;">
          Abrir Painel Facilities
        </a>
      </div>
    </body></html>
    """

    msg.attach(MIMEText(html, "html"))

    thread = threading.Thread(
        target=_send_async,
        args=(app, msg, smtp_server, smtp_port, sender, password),
    )
    thread.daemon = True
    thread.start()
