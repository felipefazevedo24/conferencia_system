"""Estoque mínimo / lote econômico sugeridos na tela /logistica/estoque.

Fixture do tubo metalon 19-01-00591 copiada do GRV em 28/09/2026: é o item
que aparecia como "cobertura crítica" com 1 saída em 90 dias. Bridge sempre
com patch: o teste não depende de rede."""
import math
from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from conferencia_app.routes import logistica_inventario_routes as routes
from conferencia_app.services import estoque_planejamento_service as svc
from tests.test_app import build_test_app, login_admin, set_logged_user


HOJE = date(2026, 9, 28)
FAMILIA_MP = "N - 01 - MATÉRIA-PRIMA"

METALON = {
    "serie_mensal": [
        {"mes": "2025-08", "saida": 12000.0, "saida_os": 0.0},
        {"mes": "2025-11", "saida": 2460.0, "saida_os": 0.0},
        {"mes": "2025-12", "saida": 3540.0, "saida_os": 0.0},
        {"mes": "2026-03", "saida": 4120.0, "saida_os": 0.0},
        {"mes": "2026-07", "saida": 25444.4, "saida_os": 25444.4},
    ],
    "retiradas_24m": {"quantidade": 4, "p50": 8060.0, "p80": 17377.76},
    "lead_times": [
        {"dt_entrada": "2025-08-14", "dt_oc": "2025-07-28", "dias": 17, "ordem_compra": "9171", "fornecedor": "METALON", "para_os": False},
        {"dt_entrada": "2026-03-09", "dt_oc": "2026-03-06", "dias": 3, "ordem_compra": "10982", "fornecedor": "CECOFER", "para_os": False},
        {"dt_entrada": "2026-07-01", "dt_oc": "2026-06-18", "dias": 13, "ordem_compra": "11867", "fornecedor": "CECOFER", "para_os": True},
    ],
    "preco_custo": 0.0195,
    "unidade": "MM",
    "primeira_movimentacao": "2025-08-12",
    "oc_estoque_12m": 1,
    "oc_para_os_12m": 1,
}


def _regular(mensal=100.0, lead_times=None, preco=10.0):
    """Item que sai todo mês, sempre perto de `mensal`."""
    meses = svc._meses_fechados(HOJE, 24)
    return {
        "serie_mensal": [{"mes": m, "saida": mensal + (5 if i % 2 else -5), "saida_os": 0.0} for i, m in enumerate(meses)],
        "retiradas_24m": {"quantidade": 48, "p50": mensal / 2, "p80": mensal * 0.6},
        "lead_times": lead_times if lead_times is not None else [
            {"dt_entrada": "2026-06-01", "dias": 20}, {"dt_entrada": "2026-03-01", "dias": 30}, {"dt_entrada": "2025-12-01", "dias": 25},
        ],
        "preco_custo": preco,
        "primeira_movimentacao": "2019-06-18",
        "oc_estoque_12m": 6,
        "oc_para_os_12m": 0,
    }


def _calcular(historico, saldo, classe="C", custo=None):
    return svc.calcular_item(
        historico, saldo_disponivel=saldo, custo_unitario=custo if custo is not None else historico.get("preco_custo"),
        classe_abc=classe, parametros=dict(svc.PADROES), hoje=HOJE,
    )


def test_metalon_nao_e_critico_e_minimo_e_uma_retirada_tipica():
    plano = _calcular(METALON, 436.8)
    assert plano["tipo_demanda"] == "esporadico"
    assert plano["nivel"] == "esporadico"
    assert plano["cobertura_dias"] is None
    # O que importa pro item esporádico: o saldo não cobre nem 3% da retirada típica.
    assert plano["estoque_minimo_sugerido"] == 17377.76
    assert plano["cobre_retirada_pct"] == 2.5
    # Só a saída de julho foi material comprado para a OS 8465.
    assert plano["consumo_os_pct_12m"] == 71.5
    assert plano["candidato_estoque"] is False
    # Lote nunca menor que uma retirada comum.
    assert plano["lote_economico_sugerido"] >= 8060.0
    # 3 entregas: 17, 3 e 13 dias.
    assert plano["lead_time_dias"] == 11.0 and plano["lead_time_amostras"] == 3
    # Item novo (desde 08/2025): só os meses em que ele existia entram.
    assert plano["meses_analisados_24m"] == 13
    assert plano["confianca"] == "baixa"


def test_regular_usa_ponto_de_pedido_com_seguranca_e_lead_time():
    historico = _regular()
    plano = _calcular(historico, saldo=1000, classe="A")
    assert plano["tipo_demanda"] == "regular"
    lt_meses = 25 / svc.DIAS_POR_MES
    desvio_lt = math.sqrt(((20 - 25) ** 2 + (30 - 25) ** 2 + 0) / 3) / svc.DIAS_POR_MES
    z = svc.NormalDist().inv_cdf(0.98)
    seguranca = z * math.sqrt(lt_meses * 5.0 ** 2 + 100.0 ** 2 * desvio_lt ** 2)
    assert plano["estoque_seguranca"] == pytest.approx(seguranca, abs=0.01)
    assert plano["estoque_minimo_sugerido"] == pytest.approx(100 * lt_meses + seguranca, abs=0.01)
    assert plano["nivel"] == "normal"
    # EOQ = raiz(2 × 1200 × 150 / (10 × 25%))
    assert plano["lote_economico_sugerido"] == pytest.approx(math.sqrt(2 * 1200 * 150 / 2.5), abs=0.01)
    assert plano["confianca"] == "alta"


def test_regular_critico_quando_acaba_antes_do_lead_time_e_atencao_abaixo_do_minimo():
    historico = _regular()
    # 100/mês ≈ 3,29/dia; 25 dias de lead time.
    assert _calcular(historico, saldo=50)["nivel"] == "critico"
    plano = _calcular(historico, saldo=90)
    assert plano["cobertura_dias"] >= 25
    assert plano["nivel"] == "atencao"


def test_sem_saida_em_12_meses_nao_sugere_estocar():
    historico = {**METALON, "serie_mensal": [{"mes": "2024-01", "saida": 500.0, "saida_os": 0.0}], "primeira_movimentacao": "2023-01-10"}
    plano = _calcular(historico, saldo=10)
    assert plano["tipo_demanda"] == "sem_giro"
    assert plano["nivel"] == "sem_consumo"
    assert plano["estoque_minimo_sugerido"] == 0
    assert plano["lote_economico_sugerido"] is None


def test_sem_entrega_com_oc_usa_lead_time_padrao_e_confianca_baixa():
    plano = _calcular(_regular(lead_times=[]), saldo=1000)
    assert plano["lead_time_fonte"] == "padrao"
    assert plano["lead_time_dias"] == svc.PADROES["lead_time_padrao_dias"]
    assert plano["confianca"] == "baixa"


def test_candidato_a_estoque_quando_comprado_para_os_com_frequencia():
    historico = _regular()
    historico["serie_mensal"] = [{**m, "saida_os": m["saida"]} for m in historico["serie_mensal"]]
    historico["oc_para_os_12m"] = 6
    assert _calcular(historico, saldo=1000)["candidato_estoque"] is True


def test_curva_abc_pelo_valor_consumido():
    classes = svc.classificar_abc({"X": 7000, "Y": 1500, "Z": 1000, "W": 500, "V": 0})
    assert classes == {"X": "A", "Y": "A", "Z": "B", "W": "C", "V": "C"}


def test_sem_custo_nao_calcula_lote():
    plano = _calcular(_regular(preco=None), saldo=1000, custo=0)
    assert plano["lote_economico_sugerido"] is None
    assert any("sem custo" in m for m in plano["confianca_motivos"])


# ------------------------------------------------------------------ rota

def _estoque(itens):
    return {"por_codigo": {codigo: {"familia": FAMILIA_MP, **dados} for codigo, dados in itens.items()}}


def _get(client, itens, planejamento, query=""):
    with patch.object(routes, "buscar_estoque_grv", return_value=_estoque(itens)), \
         patch.object(routes, "buscar_consumo_kardex_grv", return_value={"por_codigo": {}, "janela_dias": 90}), \
         patch.object(routes, "buscar_reservas_produto_acabado_grv", return_value={}), \
         patch.object(routes, "buscar_ordens_compra_abertas_grv", return_value={}), \
         patch.object(routes, "buscar_planejamento_grv", **planejamento) as mock_plan, \
         patch.object(svc, "agora_br", return_value=MagicMock(date=lambda: HOJE)):
        resp = client.get("/api/logistica/estoque?visao=materia_prima" + query)
    assert resp.status_code == 200
    return resp.get_json(), mock_plan


def test_rota_usa_planejamento_e_abc_considera_a_familia_inteira(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()
    login_admin(client)
    itens = {
        "19-01-00591": {"item": "TUBO METALON 50X50X2MM", "unidade": "MM", "qtde_disponivel": 436.8, "qtde_total": 436.8},
        "19-01-99999": {"item": "CHAPA CARA", "unidade": "KG", "qtde_disponivel": 5000, "qtde_total": 5000},
    }
    historicos = {"190100591": METALON, "190199999": _regular(mensal=1000, preco=50)}
    data, mock_plan = _get(client, itens, {"return_value": historicos}, query="&q=METALON")
    # A busca filtra a lista, mas o planejamento é pedido pra família inteira.
    assert sorted(mock_plan.call_args.kwargs["codigos"]) == ["190100591", "190199999"]
    assert data["planejamento_ativo"] is True
    assert data["planejamento_indisponivel"] is False
    assert data["pode_editar_parametros"] is True
    [item] = data["items"]
    assert item["nivel"] == "esporadico"
    assert item["cobertura_dias"] is None
    assert item["cobertura_projetada_dias"] is None
    assert item["planejamento"]["classe_abc"] == "C"
    assert data["resumo"]["criticos"] == 0
    assert data["resumo"]["esporadicos"] == 1


def test_rota_volta_ao_calculo_de_90_dias_se_a_bridge_nao_tem_planejamento(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()
    login_admin(client)
    itens = {"1901CHAPA": {"item": "CHAPA", "unidade": "KG", "qtde_disponivel": 20, "qtde_total": 20}}
    with patch.object(routes, "buscar_estoque_grv", return_value=_estoque(itens)), \
         patch.object(routes, "buscar_consumo_kardex_grv", return_value={"por_codigo": {"1901CHAPA": {"consumo_medio_diario": 10}}}), \
         patch.object(routes, "buscar_reservas_produto_acabado_grv", return_value={}), \
         patch.object(routes, "buscar_ordens_compra_abertas_grv", return_value={}), \
         patch.object(routes, "buscar_planejamento_grv", side_effect=RuntimeError("404 bridge antiga")):
        data = client.get("/api/logistica/estoque?visao=materia_prima").get_json()
    assert data["planejamento_ativo"] is False
    assert data["planejamento_indisponivel"] is True
    item = data["items"][0]
    assert item["cobertura_dias"] == 2
    assert item["nivel"] == "critico"
    assert "planejamento" not in item


def test_parametros_so_admin_altera(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()
    login_admin(client)
    resp = client.put("/api/logistica/estoque/planejamento/parametros", json={
        "custo_pedido": "210,50", "taxa_manutencao_pct": 20, "nivel_servico_a_pct": 97,
        "nivel_servico_b_pct": 95, "nivel_servico_c_pct": 90, "lead_time_padrao_dias": 12,
    })
    assert resp.status_code == 200
    assert resp.get_json()["parametros"]["custo_pedido"] == 210.5
    assert client.get("/api/logistica/estoque/planejamento/parametros").get_json()["parametros"]["lead_time_padrao_dias"] == 12

    invalido = client.put("/api/logistica/estoque/planejamento/parametros", json={
        "custo_pedido": 100, "taxa_manutencao_pct": 20, "nivel_servico_a_pct": 90,
        "nivel_servico_b_pct": 95, "nivel_servico_c_pct": 90, "lead_time_padrao_dias": 12,
    })
    assert invalido.status_code == 400
    assert "A ≥ B ≥ C" in invalido.get_json()["error"]

    outro = app.test_client()
    set_logged_user(outro, "LOGISTICA_TESTE", "Logística")
    assert outro.get("/api/logistica/estoque/planejamento/parametros").get_json()["pode_editar"] is False
    negado = outro.put("/api/logistica/estoque/planejamento/parametros", json={"custo_pedido": 1})
    assert negado.status_code == 403


def test_tela_tem_coluna_de_sugestao_e_dialogo_de_parametros(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()
    login_admin(client)
    html = client.get("/logistica/estoque").get_data(as_text=True)
    assert 'id="mp-th-sugestao"' in html
    assert 'id="mp-param-dialog"' in html
    assert "function calculoHtml" in html
    assert '<option value="esporadico">' in html


# ---------------------------------------------------------------- bridge

def test_bridge_planejamento_monta_historico_por_codigo(monkeypatch):
    from scripts import erp_lancamento_api_bridge as bridge

    monkeypatch.setattr(bridge, "_config", lambda: {"host": "h", "database": "d", "user": "u"})
    monkeypatch.setattr(bridge, "_authorized", lambda cfg: True)
    conn = MagicMock()
    cursor = conn.__enter__.return_value.cursor.return_value.__enter__.return_value
    # Uma resposta por consulta, na ordem: mensal, retiradas, lead time, cadastro.
    cursor.fetchall.side_effect = [
        [("190100591", "2026-07", 25444.4, 25444.4)],
        [("190100591", 4, 8060.0, 17377.76)],
        [("190100591", date(2026, 7, 1), date(2026, 6, 18), 13, 11867, "CECOFER", True)],
        [("190100591", 0.0195, "MM", date(2025, 8, 12), 1, 1)],
    ]
    monkeypatch.setattr(bridge, "_conectar", lambda cfg, readonly=False: conn)
    client = bridge.create_app().test_client()
    resp = client.post("/api/erp/estoque/planejamento", json={"codigos": ["19-01-00591", "1901OUTRO"]})
    data = resp.get_json()
    assert data["sucesso"] is True
    item = data["itens"]["190100591"]
    assert item["serie_mensal"] == [{"mes": "2026-07", "saida": 25444.4, "saida_os": 25444.4}]
    assert item["retiradas_24m"] == {"quantidade": 4, "p50": 8060.0, "p80": 17377.76}
    assert item["lead_times"][0]["dias"] == 13 and item["lead_times"][0]["para_os"] is True
    assert item["primeira_movimentacao"] == "2025-08-12"
    # Código sem movimento volta vazio, não some.
    assert data["itens"]["1901OUTRO"]["serie_mensal"] == []


def test_sql_do_planejamento_segue_a_regra_do_kardex():
    from scripts import erp_lancamento_api_bridge as bridge

    sql = bridge.SQL_PLANEJAMENTO_MENSAL
    assert "c.cod_deposito = 1" in sql
    assert "'TINVENT_DEP'" in sql
    assert "NF DE ENTRADA" in sql
    assert "tcom_aux_os" in sql
    assert "tcom_ordem_compra" in bridge.SQL_PLANEJAMENTO_LEAD_TIME


def test_visao_insumos_filtra_a_familia_06_e_tem_planejamento(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()
    login_admin(client)
    estoque = {"por_codigo": {
        "19-06-00001": {"familia": "N - 06 - INSUMOS DA PRODUÇÃO", "item": "DISCO DE CORTE", "unidade": "UND", "qtde_disponivel": 40, "qtde_total": 40},
        "19-01-00591": {"familia": FAMILIA_MP, "item": "TUBO METALON", "unidade": "MM", "qtde_disponivel": 436.8, "qtde_total": 436.8},
    }}
    with patch.object(routes, "buscar_estoque_grv", return_value=estoque), \
         patch.object(routes, "buscar_consumo_kardex_grv", return_value={"por_codigo": {}, "janela_dias": 90}), \
         patch.object(routes, "buscar_reservas_produto_acabado_grv", return_value={}), \
         patch.object(routes, "buscar_ordens_compra_abertas_grv", return_value={}) as mock_oc, \
         patch.object(routes, "buscar_planejamento_grv", return_value={"190600001": _regular(mensal=30)}) as mock_plan:
        data = client.get("/api/logistica/estoque?visao=insumos").get_json()
    assert data["visao_label"] == "Insumos da produção"
    assert [i["codigo"] for i in data["items"]] == ["19-06-00001"]
    assert mock_plan.call_args.kwargs["codigos"] == ["190600001"]
    assert data["planejamento_ativo"] is True
    assert mock_oc.call_args.kwargs["codigos"] == ["19-06-00001"]
    html = client.get("/logistica/estoque").get_data(as_text=True)
    assert 'data-visao="insumos"' in html
