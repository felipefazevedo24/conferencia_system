import requests

import rpa_agent
from conferencia_app.services import rpa_grv_uia_automator as uia_automator


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


def test_polling_padrao_reduz_latencia_sem_ficar_agressivo(monkeypatch):
    monkeypatch.setattr(
        rpa_agent,
        "_instance_env",
        lambda name, _instance, default="": "token-hml"
        if name == "RPA_AGENT_TOKEN" else default,
    )
    monkeypatch.setattr(rpa_agent.Agent, "_diagnose", lambda self: dict(DIAGNOSTIC))

    agent = rpa_agent.Agent("homologacao")

    assert agent.poll_seconds == 2.0
    assert rpa_agent.AGENT_VERSION == "1.3.0"


def test_wait_condicional_avanca_assim_que_a_condicao_confirma():
    class Base:
        def __init__(self, **_kwargs):
            self.delay = 0.55

    automator = uia_automator.criar_automator_verificado(Base)()
    attempts = 0

    def condition():
        nonlocal attempts
        attempts += 1
        return attempts == 3

    assert automator._wait_until(
        condition, timeout=1, poll_interval=0.001, message="timeout"
    ) is True
    assert attempts == 3


def test_sequencia_otimizada_preserva_validacoes_e_confirmacoes(monkeypatch):
    events = []

    class Event:
        def set(self):
            events.append("monitor_stop")

    class Thread:
        def join(self, timeout):
            events.append(("monitor_join", timeout))

    class Base:
        VK_F3 = 114

        def __init__(self, **kwargs):
            self.window_title = kwargs.get("window_title")
            self.executable = kwargs.get("executable")
            self.delay = kwargs.get("delay", 0.55)
            self.gravar_sem_confirmar = kwargs.get("gravar_sem_confirmar", True)

        def encontrar_janela(self, _title):
            events.append("localizar")
            return 123

        def ativar_janela(self, _hwnd):
            events.append("ativar")

        def iniciar_monitor_tela_cheia(self, _hwnd):
            events.append("monitor_start")
            return Event(), Thread()

        def garantir_janela_tela_cheia(self, _hwnd):
            events.append("tela_cheia")

    automator = uia_automator.criar_automator_verificado(Base)(
        window_title="CPS", executable=None, delay=0.55, gravar_sem_confirmar=True
    )
    monkeypatch.setattr(uia_automator.os, "name", "nt")
    monkeypatch.setattr(automator, "novo_apontamento", lambda _hwnd: events.append("novo"))
    monkeypatch.setattr(automator, "selecionar_status_liberado", lambda _hwnd: events.append("status"))

    def description(_hwnd, _value):
        events.append("descricao")
        automator._description_validated = True

    def codes(_hwnd, values):
        events.append("codigos")
        automator._inserted_codes = list(values)

    monkeypatch.setattr(automator, "preencher_descricao", description)
    monkeypatch.setattr(automator, "selecionar_empresa_columbia", lambda _hwnd: events.append("empresa"))
    monkeypatch.setattr(automator, "inserir_codigos_processos", codes)
    monkeypatch.setattr(automator, "_validar_antes_de_gravar", lambda *_: events.append("validar"))
    monkeypatch.setattr(automator, "_gravar_e_aguardar", lambda _hwnd: events.append("gravar"))

    result = automator.executar_apontamento_agrupamento(
        {
            "descricao_agrupamento": "TESTE",
            "codigos_destacados_para_agrupamento": ["179273", "180842"],
        },
        False,
    )

    assert result["gravado"] is True
    assert result["descricao_validada"] is True
    assert result["codigos_inseridos"] == ["179273", "180842"]
    assert result["gravacao_confirmada"] is True
    assert result["timings_ms"]["total"] >= 0
    assert events[:10] == [
        "localizar", "ativar", "monitor_start", "novo", "status",
        "empresa", "descricao", "codigos", "validar", "gravar",
    ]
