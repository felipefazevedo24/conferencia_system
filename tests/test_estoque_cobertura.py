"""Cobertura de estoque na tela /logistica/estoque.

ERP e bridge sempre com patch: o teste nao depende de rede."""
from unittest.mock import patch

from conferencia_app.routes import logistica_inventario_routes as routes
from tests.test_app import build_test_app, login_admin


FAMILIA_MP = "N - 01 - MATÉRIA-PRIMA"


def _estoque(itens):
    return {"por_codigo": {codigo: {"familia": FAMILIA_MP, **dados} for codigo, dados in itens.items()}}


def _consultar(tmp_path, itens, consumos, ordens=None, query=""):
    app = build_test_app(tmp_path)
    client = app.test_client()
    login_admin(client)
    with patch.object(routes, "buscar_estoque_grv", return_value=_estoque(itens)), \
         patch.object(routes, "buscar_consumo_kardex_grv", return_value={"por_codigo": consumos, "janela_dias": 30}), \
         patch.object(routes, "buscar_reservas_produto_acabado_grv", return_value={}), \
         patch.object(routes, "buscar_ordens_compra_abertas_grv", return_value=ordens or {}):
        resp = client.get("/api/logistica/estoque?visao=materia_prima" + query)
    assert resp.status_code == 200
    return resp.get_json()


def test_consumo_em_unidade_nao_e_arredondado_para_cima(tmp_path):
    # 3 pecas em 30 dias = 0,1/dia. Arredondar pra 1/dia dava 10 dias em vez de 100.
    data = _consultar(
        tmp_path,
        {"1903ARRUELA": {"item": "ARRUELA", "unidade": "UND", "qtde_disponivel": 10, "qtde_total": 10}},
        {"1903ARRUELA": {"consumo_medio_diario": 0.1, "saida_total_periodo": 3, "dias_com_saida": 2}},
    )
    item = data["items"][0]
    assert item["consumo_diario"] == 0.1
    assert item["cobertura_dias"] == 100
    assert item["nivel"] == "normal"
    assert item["dias_com_saida"] == 2
    assert item["saida_total_periodo"] == 3


def test_cobertura_arredonda_para_baixo(tmp_path):
    # 44,51 / 13,976 = 3,18 dias: o 4o dia nao esta coberto.
    data = _consultar(
        tmp_path,
        {"1901CHAPA": {"item": "CHAPA", "unidade": "KG", "qtde_disponivel": 44.51, "qtde_total": 44.51}},
        {"1901CHAPA": {"consumo_medio_diario": 13.976}},
    )
    assert data["items"][0]["cobertura_dias"] == 3


def test_resumo_conta_antes_do_limite_e_criticos_sem_oc(tmp_path):
    itens = {f"19010{i}": {"item": f"ITEM {i}", "unidade": "KG", "qtde_disponivel": 1, "qtde_total": 1} for i in range(5)}
    consumos = {codigo: {"consumo_medio_diario": 1} for codigo in itens}
    ordens = {"190100": [{"ordem_compra": "12499", "quantidade_pendente": 10}]}
    data = _consultar(tmp_path, itens, consumos, ordens=ordens, query="&limit=2")
    assert len(data["items"]) == 2
    assert data["resumo"]["itens"] == 2
    assert data["resumo"]["total_filtrado"] == 5
    assert data["resumo"]["criticos"] == 5
    assert data["resumo"]["criticos_sem_oc"] == 4
    assert data["janela_consumo_dias"] == 30


def test_tela_estoque_renderiza_novos_kpis(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()
    login_admin(client)
    resp = client.get("/logistica/estoque")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert 'id="mp-k-sem-oc"' in html
    assert "Consumo/dia" in html
    assert 'id="mp-k-disponivel"' not in html


def test_janela_de_consumo_e_90_dias_e_cobertura_projetada_soma_oc(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()
    login_admin(client)
    itens = {"1901CHAPA": {"item": "CHAPA", "unidade": "KG", "qtde_disponivel": 20, "qtde_total": 20}}
    consumo = {"por_codigo": {"1901CHAPA": {"consumo_medio_diario": 10}}, "janela_dias": 90}
    ordens = {"1901CHAPA": [{"ordem_compra": "12499", "quantidade_pendente": 50}, {"ordem_compra": "12500", "quantidade_pendente": 30}]}
    with patch.object(routes, "buscar_estoque_grv", return_value=_estoque(itens)), \
         patch.object(routes, "buscar_consumo_kardex_grv", return_value=consumo) as mock_consumo, \
         patch.object(routes, "buscar_reservas_produto_acabado_grv", return_value={}), \
         patch.object(routes, "buscar_ordens_compra_abertas_grv", return_value=ordens):
        data = client.get("/api/logistica/estoque?visao=materia_prima").get_json()
    assert mock_consumo.call_args.kwargs["janela_dias"] == 90
    item = data["items"][0]
    # Nivel segue o fisico (2 dias = critico); a projetada so' informa.
    assert item["cobertura_dias"] == 2
    assert item["nivel"] == "critico"
    assert item["quantidade_pendente_oc"] == 80
    assert item["cobertura_projetada_dias"] == 10


def test_item_normal_tambem_busca_oc_e_sem_oc_nao_tem_projetada(tmp_path):
    itens = {"1901A": {"item": "A", "unidade": "KG", "qtde_disponivel": 1000, "qtde_total": 1000}}
    with patch.object(routes, "buscar_ordens_compra_abertas_grv", return_value={}) as mock_oc:
        app = build_test_app(tmp_path)
        client = app.test_client()
        login_admin(client)
        with patch.object(routes, "buscar_estoque_grv", return_value=_estoque(itens)), \
             patch.object(routes, "buscar_consumo_kardex_grv", return_value={"por_codigo": {"1901A": {"consumo_medio_diario": 1}}}), \
             patch.object(routes, "buscar_reservas_produto_acabado_grv", return_value={}):
            data = client.get("/api/logistica/estoque?visao=materia_prima").get_json()
    assert mock_oc.call_args.kwargs["codigos"] == ["1901A"]
    assert data["items"][0]["nivel"] == "normal"
    assert data["items"][0]["cobertura_projetada_dias"] is None


def test_bridge_consumo_ignora_digitacao_e_conta_transferencia(monkeypatch):
    from datetime import date
    from unittest.mock import MagicMock
    from scripts import erp_lancamento_api_bridge as bridge

    monkeypatch.setattr(bridge, "_config", lambda: {"host": "h", "database": "d", "user": "u"})
    monkeypatch.setattr(bridge, "_authorized", lambda cfg: True)
    conn = MagicMock()
    cursor = conn.__enter__.return_value.cursor.return_value.__enter__.return_value
    # Valores reais de tsaida_e.tipo_movimento_estoque (consulta de 25/09/2026).
    cursor.fetchall.return_value = [
        ("1901A", date(2026, 9, 1), 60.0, "CONCLUSÃO SAÍDA", None),
        ("1901A", date(2026, 9, 2), 30.0, "CONCLUSÃO SAÍDA", None),
        ("1901A", date(2026, 9, 3), 500.0, "DIGITAÇÃO DA SAÍDA", None),
        ("1901A", date(2026, 9, 4), 700.0, "TRANSFERENCIA SAIDA", None),
        ("1901A", date(2026, 9, 5), 9.0, "AJUSTE SAIDA", None),
    ]
    monkeypatch.setattr(bridge, "_conectar", lambda cfg: conn)
    # Caminho de reserva: banco sem a tabela de kardex.
    monkeypatch.setattr(bridge, "_consumo_cardex", lambda cur, **kw: None)
    monkeypatch.setattr(bridge, "_detectar_fonte_kardex", lambda cur, **kw: {"tabela": "t", "sql": "select 1", "tem_empresa": True})
    client = bridge.create_app().test_client()
    resp = client.post("/api/erp/estoque/kardex-consumo", json={"codigos": ["1901A"], "janela_dias": 90})
    consumo = resp.get_json()["consumos"]["1901A"]
    # Transferencia 1 -> "em producao" e' consumo da fabrica; digitacao nao.
    assert consumo["saida_total_periodo"] == 799
    assert consumo["dias_com_saida"] == 4
    assert consumo["consumo_medio_diario"] == round(799 / 90, 6)


def test_bridge_consumo_so_considera_saida_do_deposito_1():
    from datetime import date
    from unittest.mock import MagicMock
    from scripts import erp_lancamento_api_bridge as bridge

    fonte = bridge._detectar_fonte_kardex(
        MagicMock(), data_inicio=date(2026, 6, 27), data_fim=date(2026, 9, 25), empresa=1, codigos=["1901A"],
    )
    assert fonte["tabela"] == "public.tsaida_e + public.tsaidaax"
    assert "i.cod_deposito = 1" in fonte["sql"]


def test_bridge_consumo_usa_kardex_e_ajuste_so_conta_liquido_negativo(monkeypatch):
    from datetime import date
    from unittest.mock import MagicMock
    from scripts import erp_lancamento_api_bridge as bridge

    monkeypatch.setattr(bridge, "_config", lambda: {"host": "h", "database": "d", "user": "u"})
    monkeypatch.setattr(bridge, "_authorized", lambda cfg: True)
    conn = MagicMock()
    cursor = conn.__enter__.return_value.cursor.return_value.__enter__.return_value
    cursor.fetchone.return_value = (True,)
    # (codigo, saida_liquida, ajuste_liquido, dias_saida, maior_saida, dia) como o SQL devolve.
    cursor.fetchall.return_value = [
        # Saiu 900, ajuste zerou 1000 e lancou 950 contado: consumo 900 + 50.
        ("1901A", -900.0, -50.0, 12, 600.0, date(2026, 9, 23)),
        # Ajuste positivo nao pode abater a saida real.
        ("1901B", -90.0, 400.0, 3, 40.0, date(2026, 8, 1)),
        # Estorno maior que a saida no periodo nao vira consumo negativo.
        ("1901C", 5.0, 0.0, 1, None, None),
    ]
    monkeypatch.setattr(bridge, "_conectar", lambda cfg: conn)
    detectar = MagicMock()
    monkeypatch.setattr(bridge, "_detectar_fonte_kardex", detectar)
    client = bridge.create_app().test_client()
    resp = client.post("/api/erp/estoque/kardex-consumo", json={"codigos": ["1901A", "1901B", "1901C", "1901D"], "janela_dias": 90})
    data = resp.get_json()
    assert data["fonte"] == "public.tproduto_cardex"
    detectar.assert_not_called()
    consumos = data["consumos"]
    assert consumos["1901A"]["saida_total_periodo"] == 950
    assert consumos["1901A"]["consumo_medio_diario"] == round(950 / 90, 6)
    assert consumos["1901A"]["dias_com_saida"] == 12
    assert consumos["1901A"]["maior_saida"] == 600
    assert consumos["1901A"]["maior_saida_data"] == "2026-09-23"
    assert consumos["1901C"]["maior_saida_data"] is None
    assert consumos["1901B"]["saida_total_periodo"] == 90
    assert consumos["1901C"]["saida_total_periodo"] == 0
    assert consumos["1901D"]["consumo_medio_diario"] == 0


def test_sql_do_kardex_filtra_deposito_1_e_separa_entrada_de_nf():
    from scripts import erp_lancamento_api_bridge as bridge

    sql = bridge.SQL_CONSUMO_CARDEX
    assert "c.cod_deposito = 1" in sql
    assert "'TINVENT_DEP'" in sql
    assert "NF DE ENTRADA" in sql
    # Reserva e solicitacao (3 a 6) nao movem estoque.
    assert "c.tipo_movimento in (0, 1, 2)" in sql
    # Maior saida agrupada pelo documento, pra estorno anular o lancamento.
    assert "SA.DA C.D[.] ([0-9]+)" in sql


def test_maior_saida_chega_na_api_e_tela_tem_aviso(tmp_path):
    itens = {"190100564": {"item": "CHAPA", "unidade": "KG", "qtde_disponivel": 4871, "qtde_total": 5256}}
    consumos = {"190100564": {
        "consumo_medio_diario": 358.35, "saida_total_periodo": 32252.11, "dias_com_saida": 34,
        "maior_saida": 18415.91, "maior_saida_data": "2026-09-23",
    }}
    data = _consultar(tmp_path, itens, consumos)
    item = data["items"][0]
    assert item["maior_saida"] == 18415.91
    assert item["maior_saida_data"] == "2026-09-23"
    (tmp_path / "tela").mkdir()
    app = build_test_app(tmp_path / "tela")
    client = app.test_client()
    login_admin(client)
    html = client.get("/logistica/estoque").get_data(as_text=True)
    assert "function picoSaida" in html
    assert "Maior saída" in html
