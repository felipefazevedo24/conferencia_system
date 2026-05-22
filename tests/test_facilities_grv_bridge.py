from unittest.mock import patch

from conferencia_app import create_app
from conferencia_app.services.facilities_grv_service import FacilitiesGRVService


def test_facilities_grv_service_consulta_funcionarios_pela_api_bridge(monkeypatch):
    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "sucesso": True,
                "funcionarios": [
                    {
                        "cod_empresa": 1,
                        "codigo": 123,
                        "nome": "Funcionario GRV",
                    }
                ],
            }

    monkeypatch.setenv("ERP_LANCAMENTO_API_URL", "https://bridge.local")
    monkeypatch.setenv("ERP_LANCAMENTO_API_TOKEN", "token")

    app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    with app.app_context(), patch("conferencia_app.services.facilities_grv_service.requests.post", return_value=FakeResponse()) as post:
        rows = FacilitiesGRVService.listar_funcionarios(ativos=True)

    assert rows == [{"cod_empresa": 1, "codigo": 123, "nome": "Funcionario GRV"}]
    post.assert_called_once()
    assert post.call_args.args[0] == "https://bridge.local/api/erp/facilities/funcionarios"
    assert post.call_args.kwargs["json"] == {"ativos": True}
    assert post.call_args.kwargs["headers"]["Authorization"] == "Bearer token"


def test_facilities_grv_service_consulta_materiais_pela_api_bridge(monkeypatch):
    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "sucesso": True,
                "materiais": [
                    {
                        "codigo_interno": "EPI-001",
                        "nome": "Luva",
                        "qtd_estoque": 8,
                    }
                ],
            }

    monkeypatch.setenv("FACILITIES_GRV_API_URL", "https://bridge.local")
    monkeypatch.setenv("FACILITIES_GRV_API_TOKEN", "token")

    app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    with app.app_context(), patch("conferencia_app.services.facilities_grv_service.requests.post", return_value=FakeResponse()) as post:
        rows = FacilitiesGRVService.listar_materiais_epi_uniforme(com_saldo=True)

    assert rows[0]["codigo_interno"] == "EPI-001"
    assert post.call_args.args[0] == "https://bridge.local/api/erp/facilities/materiais"
    assert post.call_args.kwargs["json"] == {"com_saldo": True}


def test_facilities_grv_service_consulta_saldo_pela_api_bridge(monkeypatch):
    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"sucesso": True, "saldo": 4.0}

    monkeypatch.setenv("FACILITIES_GRV_API_URL", "https://bridge.local")

    app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    with app.app_context(), patch("conferencia_app.services.facilities_grv_service.requests.post", return_value=FakeResponse()) as post:
        saldo = FacilitiesGRVService.saldo_material("EPI-001")

    assert saldo == 4.0
    assert post.call_args.args[0] == "https://bridge.local/api/erp/facilities/saldo"
    assert post.call_args.kwargs["json"] == {"codigo_interno": "EPI-001"}

