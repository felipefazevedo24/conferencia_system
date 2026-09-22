from types import SimpleNamespace

import pytest
from flask import Flask

from conferencia_app.services import rpa_grv_service as service


ROWS = [
    {
        "codigo_processo": "101053",
        "produto_chave": "CHAPA A36|9.50MM|A36",
        "material": 'CHAPA A36 - 3/8" (9,50MM)',
        "espessura": "9.50MM",
        "norma": "A36",
        "os": "6132",
        "os_completa": "6132/010",
        "elegivel": True,
    },
    {
        "codigo_processo": "101088",
        "produto_chave": "CHAPA A36|9.50MM|A36",
        "material": 'CHAPA A36 - 3/8" (9,50MM)',
        "espessura": "9.50MM",
        "norma": "A36",
        "os": "6134",
        "os_completa": "6134/004",
        "elegivel": True,
    },
]


@pytest.fixture()
def app():
    instance = Flask(__name__)
    instance.config.update(
        GRV_WEB_RPA_ENABLED=True,
        GRV_RPA_WINDOW_TITLE=r".*CPS.*COLUMBIA.*",
        GRV_RPA_DELAY=0.1,
    )
    return instance


@pytest.fixture(autouse=True)
def reset_execution_state():
    service._last_execution = None
    yield
    service._last_execution = None


def _mock_payload(selected):
    return {
        "produto": selected[0]["material"],
        "produto_chave": selected[0]["produto_chave"],
        "espessura_extraida": selected[0]["espessura"],
        "norma_extraida": selected[0]["norma"],
        "quantidade_itens": len(selected),
        "os_selecionadas": sorted({row["os"] for row in selected}),
        "cod_os_completo": sorted({row["os_completa"] for row in selected}),
        "codigos_destacados_para_agrupamento": sorted(row["codigo_processo"] for row in selected),
    }


def test_simulacao_reconstroi_payload_e_mapeia_descricao(app, monkeypatch):
    monkeypatch.setattr(service.rpa_agrupamento_service, "consultar", lambda *_: ROWS)
    monkeypatch.setattr(service.rpa_agrupamento_service, "montar_payload", _mock_payload)
    with app.app_context():
        result = service.montar_agrupamento(
            {
                "codes": ["101053", "101088"],
                "materialKey": "CHAPA A36|9.50MM|A36",
                "description": "21889 - A36 - 6545",
            },
            "guilherme.bonfim",
        )
    assert result["payload"]["descricao_agrupamento"] == "21889 - A36 - 6545"
    assert result["payload"]["codigos_destacados_para_agrupamento"] == ["101053", "101088"]


def test_execucao_reutiliza_automator_e_bloqueia_duplicidade(app, monkeypatch):
    calls = []

    class FakeAutomator:
        def __init__(self, **kwargs):
            calls.append(("init", kwargs))

        def listar_janelas(self):
            return [{"visivel": True, "tela_agrupamento": True, "largura": 1920, "altura": 1080}]

        def executar_apontamento_agrupamento(self, payload, dry_run):
            calls.append(("execute", payload.copy(), dry_run))
            return {"gravado": True, "comando_gravar_enviado": True, "quantidade_codigos": 2}

    monkeypatch.setattr(service, "desktop_permite_rpa", lambda: True)
    monkeypatch.setattr(service, "_load_backend", lambda: SimpleNamespace(GRVRpaAutomator=FakeAutomator))
    monkeypatch.setattr(service.rpa_agrupamento_service, "consultar", lambda *_: ROWS)
    monkeypatch.setattr(service.rpa_agrupamento_service, "montar_payload", _mock_payload)
    request = {
        "codes": ["101053", "101088"],
        "materialKey": "CHAPA A36|9.50MM|A36",
        "description": "21889 - A36 - 6545",
        "confirmed": True,
    }
    with app.app_context():
        result = service.executar_agrupamento(request, "guilherme.bonfim")
        with pytest.raises(service.RpaApiError, match="solicitado recentemente") as duplicate:
            service.executar_agrupamento(request, "guilherme.bonfim")
    assert result["result"]["gravado"] is True
    assert calls[1][1]["descricao_agrupamento"] == "21889 - A36 - 6545"
    assert calls[1][1]["codigos_destacados_para_agrupamento"] == ["101053", "101088"]
    assert duplicate.value.status_code == 409


def test_execucao_exige_confirmacao_descricao_e_desktop(app, monkeypatch):
    monkeypatch.setattr(service, "desktop_permite_rpa", lambda: False)
    with app.app_context(), pytest.raises(service.RpaApiError) as desktop_error:
        service.executar_agrupamento({}, "operador")
    assert desktop_error.value.status_code == 409

    monkeypatch.setattr(service, "desktop_permite_rpa", lambda: True)
    with app.app_context(), pytest.raises(service.RpaApiError, match="confirmação explícita"):
        service.executar_agrupamento({}, "operador")
    with app.app_context(), pytest.raises(service.RpaApiError, match="descrição do agrupamento"):
        service.executar_agrupamento({"confirmed": True}, "operador")
