import requests

import rpa_agent


DIAGNOSTIC = {
    "hostname": "PC-RPA-01",
    "usuario": "operador",
    "versao": rpa_agent.AGENT_VERSION,
    "desktop_interativo": True,
    "grv_disponivel": True,
    "janela_titulo": "CPS - COLUMBIA",
    "janela_hwnd": "123",
    "erro": None,
}


def test_producao_le_configuracao_isolada(monkeypatch):
    monkeypatch.setenv("RPA_AGENT_TOKEN", "token-hml")
    monkeypatch.setenv("RPA_AGENT_SERVER_URL", "https://hml.example")
    monkeypatch.setenv("RPA_AGENT_ID", "hml-01")
    monkeypatch.setenv("RPA_AGENT_TOKEN_PRODUCAO", "token-prod")
    monkeypatch.setenv("RPA_AGENT_SERVER_URL_PRODUCAO", "https://prod.example")
    monkeypatch.setenv("RPA_AGENT_ID_PRODUCAO", "prod-01")
    monkeypatch.setattr(rpa_agent.Agent, "_diagnose", lambda self: dict(DIAGNOSTIC))

    agent = rpa_agent.Agent("producao")

    assert agent.token == "token-prod"
    assert agent.base_url == "https://prod.example"
    assert agent.agent_id == "prod-01"


def test_loop_recupera_de_falha_transitoria_de_rede(monkeypatch):
    monkeypatch.setenv("RPA_AGENT_TOKEN", "token-hml")
    monkeypatch.setenv("RPA_AGENT_POLL_SECONDS", "5")
    monkeypatch.setattr(rpa_agent.Agent, "_diagnose", lambda self: dict(DIAGNOSTIC))

    agent = rpa_agent.Agent("homologacao")
    delays = []

    class ControlledEvent:
        stopped = False

        def is_set(self):
            return self.stopped

        def wait(self, seconds):
            delays.append(seconds)
            if seconds == agent.poll_seconds:
                self.stopped = True
            return self.stopped

        def set(self):
            self.stopped = True

    calls = iter(
        [requests.ConnectionError("rede indisponivel"), {"ok": True, "job": None}]
    )

    def post(*args, **kwargs):
        result = next(calls)
        if isinstance(result, Exception):
            raise result
        return result

    agent.stop_event = ControlledEvent()
    monkeypatch.setattr(agent, "_post", post)
    monkeypatch.setattr(rpa_agent.threading, "Thread", lambda **kwargs: type("T", (), {"start": lambda self: None})())

    agent.run()

    assert delays == [2, 5.0]

