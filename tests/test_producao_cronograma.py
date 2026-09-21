"""Regressoes do Cronograma de Entregas (leitura de orcamentos do mes)."""
from unittest.mock import patch

from test_app import build_test_app, login_admin

from conferencia_app.services import producao_service
from conferencia_app.compras import queries


def _linha(cod_orcamento=1, n_orcamento=7175, versao="01", n_os="9958", **extra):
    base = dict(
        cod_orcamento=cod_orcamento,
        n_orcamento=n_orcamento,
        versao=versao,
        dt_previsao_entrega=None,
        n_os=n_os,
        titulo="Conjunto",
        status_servico="APROVADO",
        cliente="ACME",
        dt_prevista=None,
        qtde_itens=4,
    )
    base.update(extra)
    return base


def test_pagina_cronograma_renderiza(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()
    login_admin(client)
    response = client.get("/producao/cronograma")
    assert response.status_code == 200
    assert "Cronograma de Entregas" in response.get_data(as_text=True)


def test_api_agrupa_multiplas_os_do_mesmo_orcamento(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()
    login_admin(client)
    producao_service._QUERY_CACHE.clear()
    rows = [_linha(n_os="9958"), _linha(n_os="9959")]
    with patch.object(producao_service, "fetch_all", return_value=rows):
        response = client.get("/api/producao/cronograma/orcamentos?ano=2026&mes=9")
    assert response.status_code == 200
    data = response.get_json()["orcamentos"]
    assert len(data) == 1
    assert [os["numero_os"] for os in data[0]["ordens"]] == ["9958", "9959"]
    assert data[0]["cliente"] == "ACME"


def test_api_periodo_invalido_retorna_400(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()
    login_admin(client)
    response = client.get("/api/producao/cronograma/orcamentos?ano=2026&mes=13")
    assert response.status_code == 400


def test_api_lista_vazia_quando_nao_ha_entregas(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()
    login_admin(client)
    producao_service._QUERY_CACHE.clear()
    with patch.object(producao_service, "fetch_all", return_value=[]):
        response = client.get("/api/producao/cronograma/orcamentos?ano=2026&mes=9")
    assert response.status_code == 200
    assert response.get_json() == {"orcamentos": []}


def test_api_falha_de_consulta_retorna_503(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()
    login_admin(client)
    producao_service._QUERY_CACHE.clear()
    with patch.object(producao_service, "fetch_all", side_effect=RuntimeError("bridge indisponivel")):
        response = client.get("/api/producao/cronograma/orcamentos?ano=2026&mes=9")
    assert response.status_code == 503


def test_servico_calcula_limites_do_mes_e_repassa_busca(tmp_path):
    app = build_test_app(tmp_path)
    producao_service._QUERY_CACHE.clear()
    with app.app_context(), patch.object(producao_service, "fetch_all", return_value=[]) as fetch:
        producao_service.listar_orcamentos_mes(2026, 12, "acme")
    params = fetch.call_args.args[1]
    assert params["data_de"] == "2026-12-01"
    assert params["data_ate"] == "2027-01-01"
    assert params["busca"] == "%acme%"
    assert params["classificacao"] is None


def test_api_repassa_filtro_de_segmento(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()
    login_admin(client)
    producao_service._QUERY_CACHE.clear()
    with patch.object(producao_service, "fetch_all", return_value=[]) as fetch:
        response = client.get("/api/producao/cronograma/orcamentos?ano=2026&mes=9&classificacao=CMS")
    assert response.status_code == 200
    assert fetch.call_args.args[1]["classificacao"] == "CMS"


def test_api_segmentos_lista_classificacoes(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()
    login_admin(client)
    producao_service._QUERY_CACHE.clear()
    rows = [{"classificacao": "CMS", "qtd": 12}, {"classificacao": "MOLDE", "qtd": 5}]
    with patch.object(producao_service, "fetch_all", return_value=rows):
        response = client.get("/api/producao/cronograma/segmentos")
    assert response.status_code == 200
    assert response.get_json()["segmentos"] == rows


def test_orcamento_unico_e_tres_os_recebem_mesma_entrega_sem_escrever(tmp_path):
    app = build_test_app(tmp_path)
    rows = [
        _linha(n_orcamento=7222, n_os=numero, dt_previsao_entrega="2026-09-30",
               dt_prevista=data, qtde_itens=quantidade)
        for numero, data, quantidade in (
            ("01", "2026-09-30", 2),
            ("02", "2026-10-03", 3),
            ("03", "2026-09-30", 4),
        )
    ]
    producao_service._QUERY_CACHE.clear()
    with app.app_context(), patch.object(producao_service, "fetch_all", return_value=rows) as fetch:
        result = producao_service.listar_orcamentos_mes(2026, 9)
    assert len(result) == 1
    orc = result[0]
    assert orc["numero_orcamento"] == 7222
    assert orc["quantidade_os"] == 3
    assert orc["quantidade_itens"] == 9
    assert [os["numero_os"] for os in orc["ordens"]] == ["01", "02", "03"]
    assert {os["data_prevista_entrega"] for os in orc["ordens"]} == {"2026-09-30"}
    assert orc["datas_divergentes"] is True
    assert orc["datas_previstas_os"] == ["2026-09-30", "2026-10-03"]
    assert fetch.call_count == 1


def test_orcamento_com_uma_os_sem_divergencia(tmp_path):
    app = build_test_app(tmp_path)
    producao_service._QUERY_CACHE.clear()
    with app.app_context(), patch.object(producao_service, "fetch_all", return_value=[
        _linha(dt_previsao_entrega="2026-09-30", dt_prevista="2026-09-30")
    ]):
        result = producao_service.listar_orcamentos_mes(2026, 9)
    assert result[0]["quantidade_os"] == 1
    assert result[0]["datas_divergentes"] is False


def test_filtro_mensal_usa_data_do_orcamento_e_mantem_todas_as_os():
    sql = queries.SQL_PRODUCAO_ORCAMENTOS_MES
    assert "orc.dt_previsao_entrega AS dt_previsao_entrega" in sql
    assert "orc.dt_previsao_entrega >= %(data_de)s::date" in sql
    assert "orc.dt_previsao_entrega < %(data_ate)s::date" in sql
    assert "os.dt_prevista >= %(data_de)s::date" not in sql
    assert "COALESCE(os.cod_orcamento, ol.cod_orcamento)" in sql
    assert sql.lstrip().startswith("WITH ")
    assert not any(word in sql.upper() for word in ("INSERT ", "UPDATE ", "DELETE "))


def test_estrutura_e_sequencia_sao_da_os_selecionada(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()
    login_admin(client)

    def estrutura(numero):
        return {
            "ordem": {"numero": numero, "titulo": "Conjunto", "data_prevista": "2026-09-30"},
            "raizes": [f"{numero}-1"],
            "nos": [{
                "id": f"{numero}-1", "aux_code": 1, "codigo": f"P-{numero}",
                "descricao": "Peça", "quantidade": 2, "child_ids": [],
                "operacoes": [{"codigo": "CORTE", "nome": "Corte", "sequencia": 1,
                               "finalizada": True, "travada": False, "maquina": "M1"},
                              {"codigo": "MONT", "nome": "Montagem", "sequencia": 2,
                               "finalizada": False, "travada": False, "maquina": "M2"}],
            }],
        }

    with patch.object(producao_service, "obter_estrutura", side_effect=estrutura), \
         patch.object(producao_service, "obter_documentos", return_value=[]):
        for numero in ("01", "02", "03"):
            mapa = client.get(f"/api/v1/orders/{numero}/structure")
            item = client.get(f"/api/v1/orders/{numero}/items/1")
            assert mapa.status_code == item.status_code == 200
            assert mapa.get_json()["nodes"][0]["code"] == f"P-{numero}"
            assert item.get_json()["node"]["code"] == f"P-{numero}"
            assert [op["sequence"] for op in item.get_json()["operations"]] == [1, 2]
            assert next(op for op in item.get_json()["operations"] if not op["finalized"])["sequence"] == 2


def test_miniatura_usa_rota_existente_e_retorna_png(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()
    login_admin(client)
    png = b"\x89PNG\r\n\x1a\npreview"
    with patch.object(producao_service, "obter_preview", return_value=(png, "image/png", "abc")):
        response = client.get("/api/v1/orders/01/items/1/thumbnail")
    assert response.status_code == 200
    assert response.mimetype == "image/png"
    assert response.data == png
