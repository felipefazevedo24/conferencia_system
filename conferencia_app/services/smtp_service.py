"""SMTP helpers compartilhados pelos envios de e-mail do sistema."""
from __future__ import annotations

import smtplib
from email.message import Message


def enviar_mensagem_smtp(
    app,
    msg: Message,
    *,
    smtp_server: str | None = None,
    smtp_port: int | str | None = None,
    sender: str | None = None,
    password: str | None = None,
    timeout: int | float | None = None,
) -> None:
    server_host = smtp_server or app.config.get("MAIL_SMTP_SERVER")
    server_port = int(smtp_port or app.config.get("MAIL_SMTP_PORT", 587))
    remetente = sender or app.config.get("MAIL_SENDER") or ""
    senha = password or app.config.get("MAIL_PASSWORD") or ""
    usuario = app.config.get("MAIL_SMTP_USER") or remetente
    use_ssl = bool(app.config.get("MAIL_SMTP_USE_SSL")) or server_port == 465
    use_starttls = bool(app.config.get("MAIL_SMTP_STARTTLS", True)) and not use_ssl
    timeout_s = timeout if timeout is not None else app.config.get("MAIL_SMTP_TIMEOUT", 90)
    try:
        timeout_s = max(1, int(timeout_s))
    except (TypeError, ValueError):
        timeout_s = 90

    if not server_host or not remetente or not senha:
        raise RuntimeError("SMTP nao configurado (MAIL_SMTP_SERVER/MAIL_SENDER/MAIL_PASSWORD).")

    smtp_cls = smtplib.SMTP_SSL if use_ssl else smtplib.SMTP
    etapa = "conectar"

    def _enviar_uma_vez() -> None:
        nonlocal etapa
        etapa = "conectar"
        with smtp_cls(server_host, server_port, timeout=timeout_s) as server:
            etapa = "identificar servidor"
            server.ehlo()
            if use_starttls:
                etapa = "iniciar STARTTLS"
                server.starttls()
                etapa = "identificar servidor apos STARTTLS"
                server.ehlo()
            etapa = "autenticar"
            server.login(usuario, senha)
            etapa = "enviar mensagem"
            server.send_message(msg)

    try:
        for tentativa in range(2):
            try:
                _enviar_uma_vez()
                return
            except smtplib.SMTPServerDisconnected:
                if tentativa == 1:
                    raise
    except TimeoutError as exc:
        raise TimeoutError(
            f"Timeout SMTP ao {etapa} apos {timeout_s}s ({server_host}:{server_port})."
        ) from exc
    except smtplib.SMTPServerDisconnected as exc:
        tls_config = (
            f"SSL={'1' if use_ssl else '0'}, STARTTLS={'1' if use_starttls else '0'}"
        )
        raise RuntimeError(
            f"Servidor SMTP desconectou ao {etapa} ({server_host}:{server_port}, "
            f"timeout {timeout_s}s, {tls_config}): {exc}. "
            "Confira a combinacao de porta e TLS: normalmente 587 usa STARTTLS e 465 usa SSL."
        ) from exc
