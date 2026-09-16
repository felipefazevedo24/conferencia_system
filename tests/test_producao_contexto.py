"""Regressoes do contrato consumido pelo painel original de Producao."""
import importlib
import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

import pytest
from flask import Flask

from test_producao_imagens import producao_service as service


@pytest.fixture(autouse=True)
def clear_cache():
    service._QUERY_CACHE.clear()
    yield
    service._QUERY_CACHE.clear()


@pytest.fixture
def api(monkeypatch):
    # Isola a API de Producao do bootstrap e do banco de usuarios.
    namespace = "production_test_app"
    for name, attrs in {
        "auth": {"permission_required": lambda _: lambda fn: fn},
        "extensions": {"db": None},
        "models": {"ProducaoObservacao": None, "ProducaoSequencia": None},
    }.items():
        module = ModuleType(f"{namespace}.{name}")
        vars(module).update(attrs)
        monkeypatch.setitem(sys.modules, module.__name__, module)
    routes_package = ModuleType(f"{namespace}.routes")
    routes_package.__path__ = [str(Path(__file__).resolve().parents[1] / "conferencia_app/routes")]
    monkeypatch.setitem(sys.modules, routes_package.__name__, routes_package)
    routes = importlib.import_module(f"{namespace}.routes.producao_routes")
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(routes.producao_bp)
    return app.test_client(), routes


def order(number, code=None, **extra):
    return dict(cod_empresa=1, codigo=code or int(number), n_os=number,
                titulo=f"Conjunto {number}", status_servico="APROVADO", **extra)


def test_busca_orcamento_preserva_os_e_identifica_vinculos(api):
    rows = [order("7175", matched_budget_number=7175),
            order("9959", matched_budget_number=7175),
            order("9958", matched_budget_number=7175)]
    with patch.object(service, "fetch_all", return_value=rows) as fetch:
        response = api[0].get("/api/v1/orders/search?q=7175")
    assert response.status_code == 200
    data = response.get_json()
    assert [r["number"] for r in data] == ["7175", "9959", "9958"]
    assert [r["matched_budget_number"] for r in data] == [None, 7175, 7175]
    assert fetch.call_args.args[1] == {"cod_empresa": 1, "busca": "%7175%", "termo": "7175", "limite": 20}


def test_busca_peca_preserva_codigo_e_descricao(api):
    with patch.object(service, "fetch_all", return_value=[order("9958", matched_item_code="9958/003", matched_item_description="CANECO")]):
        data = api[0].get("/api/v1/orders/search?q=9958/003").get_json()
    assert data[0]["matched_item_code"] == "9958/003"
    assert data[0]["matched_item_description"] == "CANECO"


def test_abrir_os_usa_numero_exato_sem_confundir_orcamento():
    with patch.object(service, "fetch_one", return_value=None) as fetch:
        with pytest.raises(LookupError):
            service._obter_ordem("7175")
    assert fetch.call_args.args == (service.queries.SQL_PRODUCAO_OBTER_OS, {"cod_empresa": 1, "numero_os": "7175"})


def test_orcamento_duas_os_sem_inventar_dependencia(api):
    selected = order("9958")
    selected["status_servico"] = "CONCLUÍDO"
    rows = [dict(selected, is_budget_order=True), order("9959", is_budget_order=True)]
    with (patch.object(service, "fetch_one", side_effect=[selected, {"budget_number": 7175}]),
          patch.object(service, "fetch_all", return_value=rows)):
        response = api[0].get("/api/v1/orders/9958/dependencies")
    assert response.status_code == 200
    data = response.get_json()
    assert data["budget_number"] == 7175
    assert len(data["nodes"]) == 2
    assert all(n["is_budget_order"] for n in data["nodes"])
    assert data["nodes"][0]["source_status"] == "CONCLUÍDO"
    assert data["edges"] == []


def test_dependencia_converte_codigos_internos_e_remove_duplicatas():
    rows = [order("9958", 110, is_budget_order=True),
            order("9959", 111, dependent_cod_os=110, is_budget_order=False),
            order("9959", 111, dependent_cod_os=110, is_budget_order=True),
            order("9960", 112, dependent_cod_os=999999)]
    with (patch.object(service, "fetch_one", side_effect=[order("9958", 110), {"budget_number": 7175}]),
          patch.object(service, "fetch_all", return_value=rows)):
        data = service.obter_dependencias("9958")
    assert len(data["nodes"]) == 3
    assert data["nodes"][1]["is_budget_order"] is True
    assert data["edges"] == [{"dependent_order_number": "9958", "prerequisite_order_number": "9959"}]


def test_ausencia_orcamento_e_falha_sao_respostas_distintas(api):
    with (patch.object(service, "fetch_one", side_effect=[order("9958"), None]),
          patch.object(service, "fetch_all") as fetch):
        response = api[0].get("/api/v1/orders/9958/dependencies")
    assert response.status_code == 200
    assert response.get_json()["budget_number"] is None
    fetch.assert_not_called()
    service._QUERY_CACHE.clear()
    with patch.object(service, "fetch_one", side_effect=RuntimeError("unavailable")):
        response = api[0].get("/api/v1/orders/9958/dependencies")
    assert response.status_code == 503
    assert "budget_number" not in response.get_json()


def test_dependencias_os_inexistente_retorna_404(api):
    with patch.object(service, "fetch_one", return_value=None):
        assert api[0].get("/api/v1/orders/999999/dependencies").status_code == 404


def pieces():
    return [dict(aux_code=code, cod_os_completo=f"9958/{code:03}", subtitulo=title,
                 qtde_pecas=qty, os_pai=parent, status="CONCLUÍDO", predecessora1=pred)
            for code, title, qty, parent, pred in [(5, "PB2", 1, None, None),
                (2, "PB", 1, 5, None), (3, "CANECO", 10, 5, 2), (6, "USINAGEM FACÃO", 5, 5, None)]]


def test_estrutura_caminho_quantidades_e_predecessoras(api):
    data = service._estrutura_payload(order("9958"), pieces(), [])
    with (patch.object(service, "obter_estrutura", return_value=data),
          patch.object(service, "obter_documentos", return_value=[])):
        structure = api[0].get("/api/v1/orders/9958/structure").get_json()
        detail = api[0].get("/api/v1/orders/9958/items/3").get_json()
    assert structure["roots"] == ["5"]
    assert structure["nodes"][0]["child_ids"] == ["2", "3", "6"]
    assert [n["quantity"] for n in structure["nodes"]] == [1, 1, 10, 5]
    assert detail["node"]["path_ids"] == ["5", "3"]
    assert detail["parent"]["code"] == "9958/005"
    assert detail["predecessors"][0]["code"] == "9958/002"


def test_status_sem_operacoes_usa_o_item_correto_na_recursao():
    items = pieces()
    items[0]["status"] = "APROVADO"
    data = service._estrutura_payload(order("9958"), items, [])
    assert [n["estado"] for n in data["nos"]] == ["nao_iniciado", "disponivel", "disponivel", "disponivel"]


def test_fabricacao_pendente_impede_liberacao_so_pelos_filhos():
    operations = [dict(cod_os_aux=5, tiposervico="CORTE", codigo=1),
                  dict(cod_os_aux=5, tiposervico="MONTAGEM", codigo=2)]
    data = service._estrutura_payload(order("9958"), pieces(), operations)
    assert data["nos"][0]["estado"] == "nao_iniciado"
    operations[0]["finalizado"] = True
    operations[0]["data_inicio"] = "2026-09-15T10:00:00"
    data = service._estrutura_payload(order("9958"), pieces(), operations)
    assert data["nos"][0]["estado"] == "disponivel"


def test_corte_iniciado_nao_significa_montagem_iniciada():
    operations = [dict(cod_os_aux=5, tiposervico="CORTE", codigo=1, data_inicio="2026-09-15T10:00:00"),
                  dict(cod_os_aux=5, tiposervico="MONTAGEM", codigo=2)]
    data = service._estrutura_payload(order("9958"), pieces(), operations)
    assert data["nos"][0]["estado"] == "fabricacao"


@pytest.mark.parametrize("hours", [None, 0, "0", "0.0000", "0,00"])
def test_todas_etapas_pendentes_nao_iniciam_fabricacao(hours):
    items = pieces()
    items[0]["status"] = "EM PRODUCAO"
    operations = [dict(cod_os_aux=5, tiposervico=name, codigo=i, hs_realizadas=hours)
                  for i, name in enumerate(["SOLDA 2", "USINAGEM 4", "SOLDA 2", "CONFERENCIA PECAS E MOLDES"])]
    data = service._estrutura_payload(order("9958"), items, operations)
    assert data["nos"][0]["estado"] == "nao_iniciado"


@pytest.mark.parametrize("finished_field", ["finalizado", "concluido", "dt_finalizacao"])
@pytest.mark.parametrize("all_finished, expected", [(False, "manufacturing"), (True, "available")])
def test_etapas_finalizadas_preservam_progresso_no_mapa_e_detalhes(api, finished_field, all_finished, expected):
    operations = [dict(cod_os_aux=3, tiposervico=name, codigo=i,
                       **({finished_field: "2026-09-15" if finished_field == "dt_finalizacao" else True}
                          if i < 3 or all_finished else {}))
                  for i, name in enumerate(["CORTE", "DOBRA", "SOLDA", "CONFERENCIA"])]
    data = service._estrutura_payload(order("9958"), pieces(), operations)
    with (patch.object(service, "obter_estrutura", return_value=data),
          patch.object(service, "obter_documentos", return_value=[])):
        structure = api[0].get("/api/v1/orders/9958/structure").get_json()
        detail = api[0].get("/api/v1/orders/9958/items/3").get_json()
    assert next(n for n in structure["nodes"] if n["id"] == "3")["state"] == expected
    assert detail["node"]["state"] == expected


@pytest.mark.parametrize("evidence", [{"hs_realizadas": "0.5"}, {"pcp_dt_primeiro_apont": "2026-09-15"}])
def test_evidencia_real_de_execucao_inicia_fabricacao(evidence):
    operations = [dict(cod_os_aux=3, tiposervico="CORTE", codigo=1, **evidence)]
    data = service._estrutura_payload(order("9958"), pieces(), operations)
    assert next(n for n in data["nos"] if n["id"] == "3")["estado"] == "fabricacao"


def test_ciclo_na_hierarquia_nao_trava_a_serializacao(api):
    nodes = [dict(id="1", aux_code=1, parent_id="2"), dict(id="2", aux_code=2, parent_id="1")]
    assert api[1]._original_node(nodes[0], nodes, "9958")["path_ids"] == ["2", "1"]
