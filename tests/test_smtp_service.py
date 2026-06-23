from types import SimpleNamespace

import pytest

from conferencia_app.services import smtp_service


class FakeSMTP:
    timeout_usado = None

    def __init__(self, host, port, timeout):
        self.host = host
        self.port = port
        FakeSMTP.timeout_usado = timeout

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def ehlo(self):
        return None

    def starttls(self):
        return None

    def login(self, usuario, senha):
        return None

    def send_message(self, msg):
        return None


class SMTPDesconectaNoEnvio(FakeSMTP):
    def send_message(self, msg):
        raise smtp_service.smtplib.SMTPServerDisconnected(
            "Connection unexpectedly closed: The read operation timed out"
        )


class SMTPServerNotConnectedUmaVez(FakeSMTP):
    instancias = 0

    def __init__(self, host, port, timeout):
        super().__init__(host, port, timeout)
        type(self).instancias += 1
        self.instancia = type(self).instancias

    def send_message(self, msg):
        if self.instancia == 1:
            raise smtp_service.smtplib.SMTPServerDisconnected("Server not connected")
        return None


class SMTPDesconectaDuasVezes(FakeSMTP):
    instancias = 0

    def __init__(self, host, port, timeout):
        super().__init__(host, port, timeout)
        type(self).instancias += 1
        self.instancia = type(self).instancias

    def send_message(self, msg):
        if self.instancia <= 2:
            raise smtp_service.smtplib.SMTPServerDisconnected(
                "Connection unexpectedly closed: The read operation timed out"
            )
        return None


class FakeSMTPSSL(FakeSMTP):
    usado = False

    def __init__(self, host, port, timeout):
        type(self).usado = True
        super().__init__(host, port, timeout)


def _app(timeout=90):
    return SimpleNamespace(
        config={
            "MAIL_SMTP_SERVER": "email-ssl.com.br",
            "MAIL_SMTP_PORT": 587,
            "MAIL_SENDER": "nfe@example.com",
            "MAIL_PASSWORD": "secret",
            "MAIL_SMTP_STARTTLS": True,
            "MAIL_SMTP_TIMEOUT": timeout,
            "MAIL_SMTP_RETRY_DELAY_SECONDS": 0,
        }
    )


def test_enviar_mensagem_smtp_usa_timeout_configurado(monkeypatch):
    monkeypatch.setattr(smtp_service.smtplib, "SMTP", FakeSMTP)

    smtp_service.enviar_mensagem_smtp(_app(timeout=120), SimpleNamespace())

    assert FakeSMTP.timeout_usado == 120


def test_enviar_mensagem_smtp_descreve_etapa_quando_servidor_desconecta(monkeypatch):
    monkeypatch.setattr(smtp_service.smtplib, "SMTP", SMTPDesconectaNoEnvio)

    with pytest.raises(RuntimeError) as excinfo:
        smtp_service.enviar_mensagem_smtp(_app(timeout=120), SimpleNamespace())

    mensagem = str(excinfo.value)
    assert "Servidor SMTP desconectou ao enviar mensagem" in mensagem
    assert "timeout 120s" in mensagem
    assert "Connection unexpectedly closed" in mensagem


def test_enviar_mensagem_smtp_reconecta_quando_server_not_connected(monkeypatch):
    SMTPServerNotConnectedUmaVez.instancias = 0
    monkeypatch.setattr(smtp_service.smtplib, "SMTP", SMTPServerNotConnectedUmaVez)

    smtp_service.enviar_mensagem_smtp(_app(timeout=120), SimpleNamespace())

    assert SMTPServerNotConnectedUmaVez.instancias == 2


def test_enviar_mensagem_smtp_tenta_tres_vezes_em_timeout_de_leitura(monkeypatch):
    SMTPDesconectaDuasVezes.instancias = 0
    monkeypatch.setattr(smtp_service.smtplib, "SMTP", SMTPDesconectaDuasVezes)

    smtp_service.enviar_mensagem_smtp(_app(timeout=120), SimpleNamespace())

    assert SMTPDesconectaDuasVezes.instancias == 3


def test_enviar_mensagem_smtp_nao_trata_string_zero_como_ssl(monkeypatch):
    FakeSMTPSSL.usado = False
    monkeypatch.setattr(smtp_service.smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(smtp_service.smtplib, "SMTP_SSL", FakeSMTPSSL)
    app = _app()
    app.config["MAIL_SMTP_USE_SSL"] = "0"
    app.config["MAIL_SMTP_STARTTLS"] = "1"

    smtp_service.enviar_mensagem_smtp(app, SimpleNamespace())

    assert FakeSMTPSSL.usado is False
