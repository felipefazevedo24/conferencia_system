from datetime import datetime
import sys
from types import ModuleType
from unittest.mock import patch

import pytest

from test_producao_contexto import api  # noqa: F401

from conferencia_app.services import production_cpm_service as service


@pytest.fixture(autouse=True)
def isolate_unrelated_rpa_services(monkeypatch):
    """O fixture legado de rotas carrega modulos RPA fora do escopo do CPM."""
    for name in ("rpa_agrupamento_service", "rpa_grv_service", "rpa_queue_service"):
        module = ModuleType(f"production_test_app.services.{name}")
        monkeypatch.setitem(sys.modules, module.__name__, module)


def structure(operations=None):
    return {
        "ordem": {
            "numero": "7807/001", "codigo": 801, "titulo": "Equipamento",
            "data_prevista": "2026-09-21T17:00:00",
        },
        "avisos_estrutura": [],
        "nos": [{
            "aux_code": 1, "codigo": "7807/001/001", "descricao": "Base",
            "parent_id": None, "child_ids": [], "predecessor_ids": [],
            "operacoes_total": len(operations or []), "operacoes": operations or [],
        }],
    }


def operation(**changes):
    payload = {
        "codigo": "100", "nome": "Corte Laser", "sequencia": 1,
        "finalizada": False, "travada": False, "inicio": None,
        "inicio_previsto": "2026-09-21T08:00:00",
        "fim_previsto": "2026-09-21T10:00:00", "fim": None,
        "maquina": "Laser 01", "horas_realizadas": 0.5,
        "duracao_prevista_horas": None,
    }
    payload.update(changes)
    return payload


def purchase(**changes):
    payload = {
        "cod_os_aux": 1, "pedido": 78451, "solicitacao": None,
        "cod_produto": 55, "material_codigo": "A36",
        "material_descricao": "CHAPA A36", "quantidade": 2,
        "unidade": "UN", "fornecedor": "Acos SA",
        "dt_recebimento": None, "data_prometida": datetime(2026, 9, 21, 9),
    }
    payload.update(changes)
    return payload


def test_adaptador_usa_duracao_restante_e_compra_por_componente():
    with patch.object(service.producao_service, "obter_estrutura", return_value=structure([operation()])), \
         patch.object(service.producao_service, "_metadata_read", side_effect=[[purchase()], [{"recurso": "Laser 01", "fila": 4}]]):
        result = service.calculate_order("7807/001", now=datetime(2026, 9, 21, 8), serialized=False)
    process = next(item for item in result["activities"] if item["kind"] == "process")
    assert process["duration_minutes"] == 90
    assert process["metadata"]["duration_source"] == "intervalo_planejado_grv"
    assert process["metadata"]["queue_count"] == 4
    assert result["purchases"][0]["component_aux_code"] == 1
    assert result["purchases"][0]["needed_date"] == datetime(2026, 9, 21, 8)
    assert result["purchases"][0]["float_minutes"] == -60
    assert result["purchases"][0]["status"] == "ATRASO_PROJETADO"


def test_adaptador_nao_inventa_duracao_e_sinaliza_compra_sem_previsao():
    missing = operation(inicio_previsto=None, fim_previsto=None, duracao_prevista_horas=None)
    with patch.object(service.producao_service, "obter_estrutura", return_value=structure([missing])), \
         patch.object(service.producao_service, "_metadata_read", side_effect=[[purchase(data_prometida=None)], []]):
        result = service.calculate_order("7807/001", now=datetime(2026, 9, 21, 8), serialized=False)
    assert result["summary"]["status"] == "RISCO_NAO_CALCULAVEL"
    assert result["purchases"][0]["status"] == "SEM_PREVISAO"
    assert any("Duracao nao definida" in warning for warning in result["warnings"])


def test_endpoints_cpm_usam_os_e_validam_simulacao(api):
    client, routes = api
    payload = {"order": {"number": "7807/001"}, "summary": {}, "activities": [], "purchases": []}
    with patch.object(routes.production_cpm_service, "calculate_order", return_value=payload):
        response = client.get("/api/v1/orders/7807%2F001/cpm")
    assert response.status_code == 200
    assert response.get_json()["order"]["number"] == "7807/001"
    assert client.post("/api/v1/orders/7807%2F001/cpm/simulate", json={}).status_code == 400


def test_endpoint_ciclo_retorna_erro_controlado(api):
    client, routes = api
    with patch.object(routes.production_cpm_service, "calculate_order", side_effect=routes.CircularDependencyError("dependencia circular")):
        response = client.get("/api/v1/orders/7807/cpm")
    assert response.status_code == 422
    assert response.get_json()["code"] == "circular_dependency"
