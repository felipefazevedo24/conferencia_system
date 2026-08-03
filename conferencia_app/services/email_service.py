import threading
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from flask import current_app

from .smtp_service import enviar_mensagem_smtp


def _send_async(app, msg, smtp_server, smtp_port, sender, password):
    """Send email in background thread to avoid blocking the request."""
    with app.app_context():
        try:
            enviar_mensagem_smtp(
                app,
                msg,
                smtp_server=smtp_server,
                smtp_port=smtp_port,
                sender=sender,
                password=password,
            )
            app.logger.info("E-mail enviado para %s", msg["To"])
        except Exception as e:
            app.logger.error("Erro ao enviar e-mail: %s", e)


def enviar_email_registro(destinatario_email: str, username: str, role: str, url_login: str):
    """Compatibilidade: envio antigo de boas-vindas sem token dedicado."""
    return enviar_email_convite_acesso(
        destinatario_email=destinatario_email,
        username=username,
        role=role,
        invite_link=url_login,
        expires_at_text="acesso imediato",
    )


def enviar_email_convite_acesso(
    destinatario_email: str,
    username: str,
    role: str,
    invite_link: str,
    expires_at_text: str,
) -> bool:
    """Envia convite de ativação de conta com link único e validade."""
    app = current_app._get_current_object()
    smtp_server = app.config.get("MAIL_SMTP_SERVER", "smtp.gmail.com")
    smtp_port = app.config.get("MAIL_SMTP_PORT", 587)
    sender = app.config.get("MAIL_SENDER", "")
    password = app.config.get("MAIL_PASSWORD", "")
    sender_name = app.config.get("MAIL_SENDER_NAME", "Columbia Sync")

    if not sender or not password:
        app.logger.warning("E-mail não configurado. Pulando envio.")
        return False

    msg = MIMEMultipart("alternative")
    msg["Subject"] = "Convite de acesso - Columbia Sync"
    msg["From"] = f"{sender_name} <{sender}>"
    msg["To"] = destinatario_email

    html = f"""\
    <html>
    <body style="font-family: Arial, sans-serif; background: #f4f4f4; padding: 20px;">
      <div style="max-width: 520px; margin: auto; background: #fff; border-radius: 8px; padding: 30px; box-shadow: 0 2px 8px rgba(0,0,0,0.08);">
        <h2 style="color: #1f2937; margin-bottom: 4px;">Seu acesso ao Columbia Sync foi criado</h2>
        <p style="color: #666; font-size: 14px;">Para concluir o cadastro, ative sua conta no botão abaixo.</p>
        <hr style="border: none; border-top: 1px solid #eee; margin: 20px 0;">
        <table style="font-size: 15px; color: #333;">
          <tr><td style="padding: 6px 12px 6px 0; font-weight: bold;">Usuário:</td><td>{username}</td></tr>
          <tr><td style="padding: 6px 12px 6px 0; font-weight: bold;">Perfil:</td><td>{role}</td></tr>
          <tr><td style="padding: 6px 12px 6px 0; font-weight: bold;">Validade do convite:</td><td>{expires_at_text}</td></tr>
        </table>
        <hr style="border: none; border-top: 1px solid #eee; margin: 20px 0;">
        <p style="font-size: 14px; color: #555;">
          Este link e único. Ao abrir, você definirá sua senha de acesso.
        </p>
        <a href="{invite_link}" style="display: inline-block; margin-top: 12px; padding: 10px 24px; background: #0f62c9; color: #fff; text-decoration: none; border-radius: 6px; font-size: 14px;">
          Ativar conta e definir senha
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
    return True


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


def enviar_email_agendamento_update(
    destinatario_email: str,
    assunto: str,
    titulo: str,
    linhas: list[tuple[str, str]],
    url: str,
):
    """Notifica solicitante sobre atualizacoes da solicitacao de transporte."""
    app = current_app._get_current_object()
    smtp_server = app.config.get("MAIL_SMTP_SERVER", "smtp.gmail.com")
    smtp_port = app.config.get("MAIL_SMTP_PORT", 587)
    sender = app.config.get("MAIL_SENDER", "")
    password = app.config.get("MAIL_PASSWORD", "")
    sender_name = app.config.get("MAIL_SENDER_NAME", "Columbia Sync")

    if not destinatario_email:
        return
    if not sender or not password:
        app.logger.warning("E-mail nao configurado. Pulando notificacao de agendamento.")
        return

    rows = "".join(
        f'<tr><td style="padding:6px 12px 6px 0;font-weight:bold;color:#334155;width:150px;">{label}</td><td style="padding:6px 0;color:#0f172a;">{value or "-"}</td></tr>'
        for label, value in (linhas or [])
    )

    msg = MIMEMultipart("alternative")
    msg["Subject"] = assunto
    msg["From"] = f"{sender_name} <{sender}>"
    msg["To"] = destinatario_email
    html = f"""\
    <html><body style="font-family:Arial,sans-serif;background:#f4f6f8;padding:20px;">
      <div style="max-width:600px;margin:auto;background:#fff;border-radius:10px;padding:24px;box-shadow:0 2px 10px rgba(15,23,42,.08);">
        <h2 style="margin:0 0 8px;color:#0f62c9;">{titulo}</h2>
        <p style="margin:0 0 18px;color:#64748b;font-size:14px;">Atualizacao automatica da sua solicitacao de transporte.</p>
        <table style="width:100%;font-size:14px;border-collapse:collapse;">{rows}</table>
        <a href="{url}" style="display:inline-block;margin-top:20px;padding:10px 18px;background:#0f62c9;color:#fff;text-decoration:none;border-radius:7px;font-size:14px;">Abrir minhas solicitacoes</a>
        <p style="margin-top:22px;font-size:12px;color:#94a3b8;">Este e um e-mail automatico. Nao responda.</p>
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


def enviar_email_retorno_epi(
    destinatario_email: str,
    gestor_nome: str,
    colaborador_nome: str,
    item_nome: str,
    quantidade: int,
    acao: str,          # "liberado" | "negado"
    motivo_recusa: str,
    url_ficha: str,
):
    """Notifica o gestor de setor quando sua solicitacao de EPI foi aprovada ou negada."""
    app = current_app._get_current_object()
    smtp_server = app.config.get("MAIL_SMTP_SERVER", "smtp.gmail.com")
    smtp_port   = app.config.get("MAIL_SMTP_PORT", 587)
    sender      = app.config.get("MAIL_SENDER", "")
    password    = app.config.get("MAIL_PASSWORD", "")
    sender_name = app.config.get("MAIL_SENDER_NAME", "Columbia Sync")

    if not destinatario_email or not sender or not password:
        app.logger.warning("E-mail retorno EPI: nao configurado ou sem destinatario.")
        return

    aprovado = acao == "liberado"
    cor       = "#059669" if aprovado else "#dc2626"
    icone     = "✅" if aprovado else "❌"
    titulo    = f"{icone} EPI {('Aprovado' if aprovado else 'Negado')} — {item_nome}"
    subtitulo = (
        f"Sua solicitação para <b>{colaborador_nome}</b> foi <b>{'APROVADA' if aprovado else 'NEGADA'}</b>."
    )
    extras = ""
    if not aprovado and motivo_recusa:
        extras = f'<tr><td style="padding:6px 0;font-weight:bold;color:#b91c1c;vertical-align:top">Motivo da recusa:</td><td style="padding:6px 0;color:#b91c1c">{motivo_recusa}</td></tr>'
    botao = ""
    if aprovado and url_ficha:
        botao = f'<a href="{url_ficha}" style="display:inline-block;margin-top:18px;padding:10px 22px;background:{cor};color:#fff;text-decoration:none;border-radius:6px;font-size:14px;">Abrir Ficha para Imprimir</a>'

    msg = MIMEMultipart("alternative")
    msg["Subject"] = titulo
    msg["From"]    = f"{sender_name} <{sender}>"
    msg["To"]      = destinatario_email

    html = f"""\
    <html><body style="font-family:Arial,sans-serif;background:#f4f4f4;padding:20px;">
      <div style="max-width:560px;margin:auto;background:#fff;border-radius:8px;padding:24px;border-top:4px solid {cor}">
        <h2 style="color:{cor};margin:0 0 8px;">{titulo}</h2>
        <p style="color:#555;font-size:14px;margin:0 0 16px;">{subtitulo}</p>
        <table style="width:100%;font-size:14px;color:#333;border-collapse:collapse;">
          <tr><td style="padding:6px 0;font-weight:bold;width:160px">Gestor solicitante:</td><td style="padding:6px 0">{gestor_nome}</td></tr>
          <tr><td style="padding:6px 0;font-weight:bold">Colaborador:</td><td style="padding:6px 0">{colaborador_nome}</td></tr>
          <tr><td style="padding:6px 0;font-weight:bold">Item:</td><td style="padding:6px 0">{item_nome}</td></tr>
          <tr><td style="padding:6px 0;font-weight:bold">Quantidade:</td><td style="padding:6px 0">{quantidade}</td></tr>
          {extras}
        </table>
        {botao}
        <p style="margin-top:24px;font-size:12px;color:#999;">Este é um e-mail automático. Não responda.</p>
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


def enviar_email_vencimento_epi(
    destinatario_email: str,
    gestor_nome: str,
    itens: list,   # lista de dicts: {colaborador, item, proxima_troca, dias_restantes}
    url_admin: str,
):
    """Alerta o gestor sobre EPIs prestes a vencer ou já vencidos no seu setor."""
    app = current_app._get_current_object()
    smtp_server = app.config.get("MAIL_SMTP_SERVER", "smtp.gmail.com")
    smtp_port   = app.config.get("MAIL_SMTP_PORT", 587)
    sender      = app.config.get("MAIL_SENDER", "")
    password    = app.config.get("MAIL_PASSWORD", "")
    sender_name = app.config.get("MAIL_SENDER_NAME", "Columbia Sync")

    if not destinatario_email or not sender or not password or not itens:
        return

    linhas = ""
    for it in itens:
        dias = it.get("dias_restantes", 0)
        cor_linha = "#b91c1c" if dias < 0 else ("#b45309" if dias <= 15 else "#555")
        situacao  = "VENCIDO" if dias < 0 else f"vence em {dias}d"
        linhas += (
            f'<tr>'
            f'<td style="padding:6px 8px;border-bottom:1px solid #f1f5f9">{it.get("colaborador","")}</td>'
            f'<td style="padding:6px 8px;border-bottom:1px solid #f1f5f9">{it.get("item","")}</td>'
            f'<td style="padding:6px 8px;border-bottom:1px solid #f1f5f9">{it.get("proxima_troca","")}</td>'
            f'<td style="padding:6px 8px;border-bottom:1px solid #f1f5f9;color:{cor_linha};font-weight:700">{situacao}</td>'
            f'</tr>'
        )

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"[Facilities] Alertas NR-6 — {len(itens)} EPI(s) com vencimento próximo"
    msg["From"]    = f"{sender_name} <{sender}>"
    msg["To"]      = destinatario_email

    html = f"""\
    <html><body style="font-family:Arial,sans-serif;background:#f4f4f4;padding:20px;">
      <div style="max-width:680px;margin:auto;background:#fff;border-radius:8px;padding:24px;border-top:4px solid #f59e0b">
        <h2 style="color:#b45309;margin:0 0 8px;">⚠️ Alertas NR-6 — Vencimento de EPIs</h2>
        <p style="color:#555;font-size:14px;margin:0 0 16px;">Olá {gestor_nome}, os EPIs abaixo precisam de atenção.</p>
        <table style="width:100%;font-size:13px;border-collapse:collapse;">
          <thead><tr style="background:#1e293b;color:#fff">
            <th style="padding:8px">Colaborador</th>
            <th style="padding:8px">Item</th>
            <th style="padding:8px">Próx. Troca</th>
            <th style="padding:8px">Situação</th>
          </tr></thead>
          <tbody>{linhas}</tbody>
        </table>
        <a href="{url_admin}" style="display:inline-block;margin-top:18px;padding:10px 22px;background:#f59e0b;color:#fff;text-decoration:none;border-radius:6px;font-size:14px;">
          Abrir Painel Facilities
        </a>
        <p style="margin-top:24px;font-size:12px;color:#999;">Este é um e-mail automático. Não responda.</p>
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

