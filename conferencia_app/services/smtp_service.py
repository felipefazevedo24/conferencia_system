"""SMTP helpers compartilhados pelos envios de e-mail do sistema."""
from __future__ import annotations

import smtplib
from email.message import Message
from typing import Any


def enviar_mensagem_smtp(
    app,
    msg: Message,
    *,
    smtp_server: str | None = None,
    smtp_port: int | str | None = None,
    sender: str | None = None,
    password: str | None = None,
    timeout: int = 30,
) -> None:
    server_host = smtp_server or app.config.get("MAIL_SMTP_SERVER")
    server_port = int(smtp_port or app.config.get("MAIL_SMTP_PORT", 587))
    remetente = sender or app.config.get("MAIL_SENDER") or ""
    senha = password or app.config.get("MAIL_PASSWORD") or ""
    usuario = app.config.get("MAIL_SMTP_USER") or remetente
    use_ssl = bool(app.config.get("MAIL_SMTP_USE_SSL")) or server_port == 465
    use_starttls = bool(app.config.get("MAIL_SMTP_STARTTLS", True)) and not use_ssl

    if not server_host or not remetente or not senha:
        raise RuntimeError("SMTP nao configurado (MAIL_SMTP_SERVER/MAIL_SENDER/MAIL_PASSWORD).")

    smtp_cls = smtplib.SMTP_SSL if use_ssl else smtplib.SMTP
    with smtp_cls(server_host, server_port, timeout=timeout) as server:
        if use_starttls:
            server.starttls()
        server.login(usuario, senha)
        server.send_message(msg)
