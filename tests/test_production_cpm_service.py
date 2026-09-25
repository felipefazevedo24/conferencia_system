from datetime import datetime
from unittest.mock import patch

from test_producao_contexto import api  # noqa: F401

from conferencia_app.services import production_cpm_service as service


def operation_row(**changes):
    row = {
        "cod_orcamento": 72, "n_orcamento": 7222, "versao": "A",
        "dt_previsao_entrega": datetime(2026, 9, 21, 17),
        "cod_os": 801, "n_os": "7807/001", "cliente": "Cliente",
        "titulo": "Equipamento", "u_classificacao": "CMS",
        "cod_os_aux": 1, "cod_os_completo": "7807/001/001", "subtitulo": "Base",
        "os_pai": None, "predecessora1": None, "predecessora2": None,
        "cod_processo": 100, "tiposervico": "Corte Laser", "seq_processo": 1,
        "finalizado": 0, "concluido": 0, "processo_travado": 0,
        "data_inicio": None, "dt_incio_previsto": datetime(2026, 9, 21, 8),
        "dt_termino_previsto": datetime(2026, 9, 21, 10),
        "dt_finalizacao": None, "hs_realizadas": 0, "maquina": "Laser 01",
        "duracao_prevista_horas": None,
    }
    row.update(changes)
    return row


def purchase_row(**changes):
    row = {
        "cod_orcamento": 72, "n_orcamento": 7222, "n_os": "7807/001",
        "cod_os": 801, "cod_os_aux": 1, "pedido": 78451, "cod_produto": 55,
        "material_codigo": "A36", "material_descricao": "CHAPA A36",
        "quantidade": 2, "unidade": "UN", "fornecedor": "Acos SA",
        "dt_recebimento": None, "data_prometida": datetime(2026, 9, 21, 9),
    }
    row.update(changes)
    return row


def test_adaptador_usa_duracao_restante_e_compra_com_necessidade_do_processo():
    rows = [
        operation_row(data_inicio=datetime(2026, 9, 21, 8), hs_realizadas=0.5),
        operation_row(cod_processo=101, tiposervico="Dobra", seq_processo=2,
                      dt_incio_previsto=datetime(2026, 9, 21, 10),
                      dt_termino_previsto=datetime(2026, 9, 21, 11)),
    ]
    with patch.object(service.producao_service, "_metadata_read", side_effect=[rows, [purchase_row()]]):
        result = service.calculate_budget(7222, now=datetime(2026, 9, 21, 8), serialized=False)
    processes = [item for item in result["activities"] if item["kind"] == "process"]
    assert processes[0]["duration_minutes"] == 90
    assert processes[0]["metadata"]["duration_source"] == "intervalo_planejado_grv"
    assert result["purchases"][0]["needed_date"] == datetime(2026, 9, 21, 8)
    assert result["purchases"][0]["status"] == "ATRASO_PROJETADO"
    assert result["purchases"][0]["impact_minutes"] == 60


def test_adaptador_nao_inventa_duracao_e_compra_sem_previsao_eleva_risco():
    row = operation_row(dt_incio_previsto=None, dt_termino_previsto=None)
    with patch.object(service.producao_service, "_metadata_read",
                      side_effect=[[row], [purchase_row(data_prometida=None)]]):
        result = service.calculate_budget(7222, now=datetime(2026, 9, 21, 8), serialized=False)
    assert result["summary"]["status"] == "RISCO_NAO_CALCULAVEL"
    assert any("Duracao nao definida" in warning for warning in result["warnings"])
    assert result["purchases"][0]["status"] == "SEM_PREVISAO"


def test_endpoints_cpm_preservam_permissao_e_validam_simulacao(api):
    client, routes = api
    payload = {"budget": {"number": 7222}, "purchases": [], "summary": {}}
    with patch.object(routes.production_cpm_service, "calculate_budget", return_value=payload):
        response = client.get("/api/cpm/orcamento/7222")
    assert response.status_code == 200
    assert response.get_json()["budget"]["number"] == 7222
    assert client.post("/api/cpm/simulate", json={"orcamento": 7222}).status_code == 400


def test_endpoint_ciclo_retorna_erro_controlado(api):
    client, routes = api
    with patch.object(routes.production_cpm_service, "calculate_budget",
                      side_effect=routes.CircularDependencyError("dependencia circular")):
        response = client.get("/api/cpm/orcamento/7222")
    assert response.status_code == 422
    assert response.get_json()["code"] == "circular_dependency"
