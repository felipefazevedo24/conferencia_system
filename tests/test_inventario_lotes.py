"""Inventario: item controlado por lote contado em varios lotes na mesma
contagem. Cada lote fica registrado, a contagem vale a SOMA, e a validacao
contra o GRV (e o ajuste que ela abre) e' so' pelo total."""
from io import BytesIO
from unittest.mock import patch

from openpyxl import load_workbook

from tests.test_app import build_test_app, login_admin

ROTA_GRV = "conferencia_app.routes.logistica_inventario_routes.buscar_estoque_grv"
API = "/api/logistica/inventario-inicial"


def _estoque(qtde_grv):
    return {
        "por_local": {"CHAPA-01|A01": {"qtde_total": qtde_grv, "custo_medio": 10.0}},
        "por_codigo": {"CHAPA-01": {"qtde_total": qtde_grv, "custo_medio": 10.0, "item": "CHAPA 3/16",
                                    "unidade": "KG", "tipo_controle": "1"}},
    }


def _contagem(lotes, **extra):
    corpo = {"local_codigo": "A01", "codigo_produto": "CHAPA-01", "unidade_medida": "KG",
             "lote": lotes[0]["lote"], "quantidade": lotes[0]["quantidade"], "lotes": lotes}
    corpo.update(extra)
    return corpo


def test_varios_lotes_somam_e_batem_com_o_grv_pelo_total(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()
    login_admin(client)

    with patch(ROTA_GRV, return_value=_estoque(480.0)):
        resp = client.post(API, json=_contagem([
            {"lote": "L-01", "quantidade": 300},
            {"lote": "L-02", "quantidade": "120,5"},
            {"lote": "L-03", "quantidade": 59.5},
        ]))
    assert resp.status_code == 201, resp.get_json()
    corpo = resp.get_json()
    registro = corpo["registro"]
    assert registro["quantidade"] == 480.0
    assert registro["lote"] == "L-01, L-02, L-03"
    assert registro["lotes"] == [
        {"lote": "L-01", "quantidade": 300.0},
        {"lote": "L-02", "quantidade": 120.5},
        {"lote": "L-03", "quantidade": 59.5},
    ]
    # Soma dos lotes = saldo total do GRV: sem divergencia, sem ajuste.
    assert corpo["ajuste_aberto"] is None

    # Consulta (Inventario Realizado) traz o detalhe e compara o total.
    lista = client.get(f"{API}?comparar_grv=1").get_json()["registros"]
    assert lista[0]["lotes"][1] == {"lote": "L-02", "quantidade": 120.5}
    assert lista[0]["qtde_grv"] == 480.0 and lista[0]["divergente"] is False


def test_diferenca_geral_abre_um_ajuste_pelo_total(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()
    login_admin(client)

    with patch(ROTA_GRV, return_value=_estoque(500.0)):
        resp = client.post(API, json=_contagem([
            {"lote": "L-01", "quantidade": 300},
            {"lote": "L-02", "quantidade": 180},
        ]))
    assert resp.status_code == 201
    assert resp.get_json()["ajuste_aberto"]["diferenca"] == -20.0

    ajustes = client.get("/api/logistica/inventario-ajustes").get_json()["ajustes"]
    assert len(ajustes) == 1
    assert ajustes[0]["qtde_contada"] == 480.0
    assert ajustes[0]["diferenca"] == -20.0


def test_linhas_de_lote_invalidas_nao_salvam_nada(tmp_path):
    from conferencia_app.models import LogisticaInventarioInicial, LogisticaInventarioLote

    app = build_test_app(tmp_path)
    client = app.test_client()
    login_admin(client)

    casos = [
        ([{"lote": "L-01", "quantidade": 10}, {"lote": "", "quantidade": 5}], "linha 2"),
        ([{"lote": "L-01", "quantidade": 10}, {"lote": "L-02", "quantidade": 0}], "L-02"),
        ([{"lote": "L-01", "quantidade": 10}, {"lote": "l-01", "quantidade": 5}], "duas vezes"),
        ([{"lote": "L-01", "quantidade": 10}, {"lote": "L-02", "quantidade": "abc"}], "L-02"),
    ]
    with patch(ROTA_GRV, return_value=_estoque(15.0)):
        for lotes, trecho in casos:
            resp = client.post(API, json=_contagem(lotes))
            assert resp.status_code == 400, lotes
            assert trecho in resp.get_json()["error"]

    with app.app_context():
        assert LogisticaInventarioInicial.query.count() == 0
        assert LogisticaInventarioLote.query.count() == 0


def test_contagem_de_um_lote_so_continua_como_antes(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()
    login_admin(client)

    with patch(ROTA_GRV, return_value=_estoque(480.0)):
        resp = client.post(API, json={"local_codigo": "A01", "codigo_produto": "CHAPA-01",
                                      "unidade_medida": "KG", "quantidade": 480, "lote": "L-01"})
    assert resp.status_code == 201
    assert resp.get_json()["registro"]["lote"] == "L-01"
    assert resp.get_json()["registro"]["lotes"] == []


def test_exportacao_excel_mostra_quantidade_de_cada_lote(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()
    login_admin(client)

    with patch(ROTA_GRV, return_value=_estoque(480.0)):
        client.post(API, json=_contagem([{"lote": "L-01", "quantidade": 300}, {"lote": "L-02", "quantidade": 180}]))

    planilha = load_workbook(BytesIO(client.get(f"{API}/exportar").data)).active
    cabecalho = [c.value for c in planilha[1]]
    linha = [c.value for c in planilha[2]]
    assert linha[cabecalho.index("Quantidade")] == 480
    assert linha[cabecalho.index("Lote")] == "L-01: 300; L-02: 180"
