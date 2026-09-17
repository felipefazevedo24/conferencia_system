"""Regressoes do Cronograma de Entregas (leitura de orcamentos do mes)."""
from unittest.mock import patch

from test_app import build_test_app, login_admin

from conferencia_app.services import producao_service


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
