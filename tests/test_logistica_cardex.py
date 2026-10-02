"""Cardex (Logística > Inventário > Cardex).

A fixture é a planilha "Registro de Inventário Modelo 7 / Cardex" que a
Contabilidade montou à mão para a CHAPA A36 3/8" em 09/2026: saldo inicial
4.518,62 kg / R$ 18.707,09, entrada da NF 78851 (2.380 kg a 4,07 líquido de
ICMS 0,61 / PIS 0,07 / COFINS 0,34 por kg), saída avulsa 53918 de 20,76 kg
pelo médio (4,12 -> R$ 85,47, saldo R$ 28.315,03). O motor tem que chegar
nos mesmos números. As colunas de valor da tcom_aux ainda não foram vistas
no GRV real; aqui vão os nomes candidatos. Bridge sempre com patch."""
import json
from datetime import date, datetime
from io import BytesIO
from unittest.mock import patch

import pytest
from openpyxl import load_workbook

from conferencia_app.extensions import db
from conferencia_app.models import LogisticaCardexFechamento, LogisticaCardexFechamentoItem, LogisticaInventarioAjuste
from conferencia_app.services import logistica_cardex_service as svc
from tests.test_app import build_test_app, login_admin, set_logged_user

COD = 5001
CODIGO = "190100564"
SETEMBRO = (date(2026, 9, 1), date(2026, 9, 30))

ICMS, PIS, COFINS = 1451.80, 166.60, 809.20
LIQUIDO_NF_78851 = 9693.41


def _mov(id_, dia, tipo, qtde, tabela, chave, obs="", dep=1, cod=COD):
    return {"id": str(id_), "cod_produto": cod, "cod_deposito": dep, "data": f"{dia}T08:00:00",
            "tipo": tipo, "qtde": qtde, "tabela": tabela, "chave": chave, "obs": obs}


MOV_ENTRADA_78851 = _mov(1, "2026-09-01", 0, 2380.0, "TCOMPRAS", "1;16814", "ENTRADA DE MATERIAL. ENTRADA: 16814. NF: 78851.")
MOV_SAIDA_53918 = _mov(2, "2026-09-01", 1, -20.76, "TSAIDA_E", "1;53918", "SAÍDA DE ESTOQUE AVULSA. SAÍDA CÓD. 53918.")
MOV_ENTRADA_173975 = _mov(3, "2026-09-03", 0, 17205.0, "TCOMPRAS", "1;16870", "ENTRADA DE MATERIAL. ENTRADA: 16870. NF: 173975.")

NOTAS = {
    "16814": {"cod_compra": 16814, "n_nf": "78851", "dt_nf": "2026-08-28", "fornecedor": "USIMINAS", "chave_nfe": "3526...", "cfop": "1101",
              "itens": [{"cod_produto": COD, "qtde": 2380.0, "valor_total": LIQUIDO_NF_78851 + ICMS + PIS + COFINS,
                         "valor_icms": ICMS, "valor_pis": PIS, "valor_cofins": COFINS, "valor_ipi": 0}]},
    "16870": {"cod_compra": 16870, "n_nf": "173975", "dt_nf": "2026-08-31", "fornecedor": "GERDAU", "chave_nfe": "", "cfop": "1101",
              "itens": [{"cod_interno": "19-01-00564", "qtde": 17205.0, "valor_total": 92126.12}]},
}

# Estado de hoje no GRV depois dos 3 movimentos: é dele que o saldo inicial
# de setembro sai, de trás pra frente.
VALOR_HOJE = 28315.03 + 92126.12
QTDE_HOJE = 4518.62 + 2380.0 - 20.76 + 17205.0


def _produto(saldos=None, preco=None):
    return {"cod_produto": COD, "codigo_interno": "19-01-00564", "codigo": CODIGO,
            "descricao": 'CHAPA A36 - 3/8" (9,50MM)', "unidade": "KG", "familia": "N - 01 - MATÉRIA-PRIMA",
            "grupo": "1", "preco_custo": VALOR_HOJE / QTDE_HOJE if preco is None else preco,
            "fiscal": {"classificacao_fiscal": "72085200"}, "saldos": saldos or {"1": QTDE_HOJE}}


def _grv(produtos=None, movimentos=None, notas=None, historico=None):
    return {"disponivel": True, "produtos": produtos if produtos is not None else [_produto()],
            "movimentos": movimentos if movimentos is not None else [MOV_ENTRADA_78851, MOV_SAIDA_53918, MOV_ENTRADA_173975],
            "notas": notas if notas is not None else NOTAS, "historico": historico or [],
            "depositos": [{"codigo": 1, "nome": "PRINCIPAL"}, {"codigo": 2, "nome": "EM PRODUÇÃO (PRODUTO)"}]}


def _item(grv=None, inicio=SETEMBRO[0], fim=SETEMBRO[1], ancora=None):
    grv = grv or _grv()
    return svc.calcular_item(grv["produtos"][0], grv["movimentos"], grv["notas"], inicio=inicio, fim=fim, abertura_ancora=ancora)


@pytest.fixture(autouse=True)
def _sem_cache():
    svc.limpar_cache()
    yield
    svc.limpar_cache()


# --------------------------------------------------------------------------
# Motor
# --------------------------------------------------------------------------

def test_custo_da_nf_sai_liquido_de_icms_pis_cofins():
    custo = svc.custo_da_nf(NOTAS["16814"]["itens"])
    assert custo["valor_liquido"] == pytest.approx(LIQUIDO_NF_78851)
    assert custo["impostos"]["icms"] == pytest.approx(ICMS)
    assert "valor_total" in custo["fontes"] and "valor_icms" in custo["fontes"]
    # Sem valor total, usa preço unitário x quantidade.
    custo = svc.custo_da_nf([{"preco_unitario": 2.5, "qtde": 10}])
    assert custo["valor_liquido"] == pytest.approx(25.0)
    assert svc.custo_da_nf([{"cod_produto": 1}]) is None


@pytest.mark.parametrize("campos", [
    {"icms_vl": ICMS, "pis_vl": PIS, "cofins_vl": COFINS},
    {"vicms": ICMS, "vpis": PIS, "vcofins": COFINS},
    {"total_icms": ICMS, "vlr_pis_item": PIS, "valor_cofins_item": COFINS},
    # Só base e alíquota: calcula.
    {"base_icms": 23800.0, "aliq_icms": ICMS / 238, "bc_pis": 23800.0, "perc_pis": PIS / 238, "bc_cofins": 23800.0, "aliq_cofins": COFINS / 238},
])
def test_custo_da_nf_reconhece_imposto_por_outros_nomes_de_coluna(campos):
    linha = {"cod_produto": COD, "qtde": 2380.0, "valor_total": LIQUIDO_NF_78851 + ICMS + PIS + COFINS,
             # ST e base não podem ser confundidos com o imposto próprio.
             "vl_icms_st": 999.0, **campos}
    custo = svc.custo_da_nf([linha])
    assert custo["impostos"]["icms"] == pytest.approx(ICMS)
    assert custo["impostos"]["pis"] == pytest.approx(PIS)
    assert custo["impostos"]["cofins"] == pytest.approx(COFINS)
    assert custo["valor_liquido"] == pytest.approx(LIQUIDO_NF_78851)


def test_cardex_reproduz_a_planilha_da_contabilidade():
    item = _item()
    det = svc.detalhe(item)
    assert det["ncm"] == "72085200"
    assert det["inicial"]["qtde"] == pytest.approx(4518.62)
    assert det["inicial"]["valor"] == pytest.approx(18707.09, abs=0.01)
    assert round(det["inicial"]["custo_medio"], 2) == 4.14

    entrada, saida, entrada2 = det["linhas"]
    assert entrada["documento"] == "NF 78851 · USIMINAS"
    assert round(entrada["custo_unit"], 2) == 4.07
    assert entrada["origem_custo"] == "nf"
    assert {k: round(v, 2) for k, v in entrada["impostos_unit"].items() if v} == {"icms": 0.61, "pis": 0.07, "cofins": 0.34}
    assert entrada["saldo_qtde"] == pytest.approx(6898.62)
    assert entrada["saldo_valor"] == pytest.approx(28400.50, abs=0.01)
    assert round(entrada["custo_medio"], 2) == 4.12

    # "operações de saída utilizam o médio"
    assert saida["documento"] == "Saída 53918"
    assert saida["origem_custo"] == "medio"
    assert round(saida["custo_unit"], 2) == 4.12
    assert saida["valor"] == pytest.approx(-85.47, abs=0.01)
    assert saida["saldo_qtde"] == pytest.approx(6877.86)
    assert saida["saldo_valor"] == pytest.approx(28315.03, abs=0.01)

    # A segunda NF casa pelo cod_interno (sem cod_produto na linha).
    assert entrada2["custo_unit"] == pytest.approx(92126.12 / 17205)
    assert det["final"]["qtde"] == pytest.approx(QTDE_HOJE)
    assert det["final"]["valor"] == pytest.approx(VALOR_HOJE, abs=0.01)
    # A NF 173975 da fixture vem sem imposto: o custo fica bruto e a tela avisa.
    assert entrada2["sem_imposto"] is True
    assert item["alertas"] == ["nf_sem_imposto"]


def test_resumo_do_item_soma_por_classe():
    linha = svc.resumo(_item())
    assert linha["qtde_entrada"] == pytest.approx(2380 + 17205)
    assert linha["valor_entrada"] == pytest.approx(LIQUIDO_NF_78851 + 92126.12)
    assert linha["valor_saida"] == pytest.approx(-85.47, abs=0.01)
    assert linha["valor_reavaliacao"] == 0.0
    assert linha["valor_final"] - linha["valor_inicial"] == pytest.approx(linha["valor_entrada"] + linha["valor_saida"], abs=0.01)


def test_movimento_depois_do_periodo_so_serve_para_desfazer():
    """Agosto visto hoje: os movimentos de setembro são desfeitos, não listados."""
    item = _item(inicio=date(2026, 8, 1), fim=date(2026, 8, 31))
    det = svc.detalhe(item)
    assert det["linhas"] == []
    assert det["final"]["qtde"] == pytest.approx(4518.62)
    assert det["final"]["valor"] == pytest.approx(18707.09, abs=0.01)


def test_abertura_pela_ancora_aplica_o_que_veio_antes_do_inicio():
    """Âncora = fechamento de 31/08; período começa em 02/09: os movimentos
    de 01/09 entram no saldo inicial."""
    ancora = {"q": {"1": 4518.62}, "m": 18707.09 / 4518.62}
    item = _item(inicio=date(2026, 9, 2), ancora=ancora)
    det = svc.detalhe(item)
    assert det["inicial"]["qtde"] == pytest.approx(6877.86)
    assert det["inicial"]["valor"] == pytest.approx(28315.03, abs=0.01)
    assert [l["documento"] for l in det["linhas"]] == ["NF 173975 · GERDAU"]


def test_cardex_considera_so_o_deposito_1():
    """O Cardex é o livro do depósito 1: o saldo e o movimento do depósito 2
    não entram, e a transferência 1 -> 2 é saída do Cardex, pelo médio."""
    movs = [_mov(10, "2026-09-10", 2, -100.0, "TSAIDA_E", "1;60000", dep=1),
            _mov(11, "2026-09-10", 2, 100.0, "TSAIDA_E", "1;60000", dep=2),
            _mov(12, "2026-09-12", 1, -30.0, "TSAIDA_E", "1;60001", dep=2)]
    grv = _grv(produtos=[_produto(saldos={"1": 900.0, "2": 70.0}, preco=5.0)], movimentos=movs, notas={})
    item = _item(grv)
    assert set(item["abertura"]["q"]) == {"1"} and set(item["fechamento"]["q"]) == {"1"}
    assert [l["deposito"] for l in item["linhas"]] == ["1"]
    linha = svc.resumo(item)
    assert linha["qtde_inicial"] == pytest.approx(1000.0)
    assert linha["qtde_saida"] == pytest.approx(-100.0)
    assert linha["valor_saida"] == pytest.approx(-500.0)
    assert linha["qtde_transferencia"] == 0 and linha["valor_transferencia"] == 0
    assert item["linhas"][0]["classe"] == "saida" and item["linhas"][0]["tipo_rotulo"] == "Transferência"
    assert linha["qtde_final"] == pytest.approx(900.0)
    assert linha["valor_final"] == pytest.approx(4500.0)
    assert linha["valor_reavaliacao"] == 0
    assert "divergente_grv" not in item["alertas"]


def test_transferencia_de_volta_para_o_deposito_1_e_entrada_pelo_medio():
    movs = [_mov(10, "2026-09-10", 2, 40.0, "TSAIDA_E", "1;60002", dep=1),
            _mov(11, "2026-09-10", 2, -40.0, "TSAIDA_E", "1;60002", dep=2)]
    grv = _grv(produtos=[_produto(saldos={"1": 1040.0}, preco=5.0)], movimentos=movs, notas={})
    item = _item(grv)
    linha = svc.resumo(item)
    assert linha["qtde_entrada"] == pytest.approx(40.0) and linha["valor_entrada"] == pytest.approx(200.0)
    assert item["linhas"][0]["origem_custo"] == "medio" and item["alertas"] == []


# --------------------------------------------------------------------------
# Regras calibradas contra o GRV real em 02/10/2026
# --------------------------------------------------------------------------

def test_custo_da_nf_com_os_nomes_reais_das_colunas_do_grv():
    """NF 405199 (SETEFER), item 19-01-00563, como está na tcom_aux: o PIS e o
    COFINS vêm em vpis_q09 / vcofins_s11 e o `total` já vem sem o IPI. O GRV
    grava custo de 4,6509 para essa linha (vl_custo_uni_sem_captacao)."""
    linha = {"cod_produto": 27400, "qtde": 1860, "total": 11625, "vl_unitario": 6.25, "vl_icms": 2092.5,
             "vpis_q09": 157.2862, "vcofins_s11": 724.47, "vl_ipi": 377.8125, "vlr_ibs": 8.6507, "vlr_cbs": 77.8567,
             "valor_total_oc": 14130, "vl_base_calc_icms": 11625, "vl_custo_uni_sem_captacao": 4.65093752688172}
    custo = svc.custo_da_nf([linha])
    assert custo["valor_bruto"] == 11625
    assert custo["impostos"]["pis"] == pytest.approx(157.2862) and custo["impostos"]["cofins"] == pytest.approx(724.47)
    assert custo["valor_liquido"] / 1860 == pytest.approx(4.65093752688172)
    # Frete, seguro e desconto do item também têm nome próprio no GRV.
    com_extras = svc.custo_da_nf([{**linha, "vfrete_i15": 100.0, "vseg_i16": 10.0, "vdesc_i17": 25.0}])
    assert com_extras["valor_liquido"] == pytest.approx(custo["valor_liquido"] + 100 + 10 - 25)


def test_quantidade_que_vale_e_a_variacao_do_saldo_gravado_na_linha():
    """Inventário: o GRV tira o disponível (fica o reservado) e depois lança o
    saldo NOVO inteiro. Somar o lançado contaria o reservado em dobro."""
    movs = [
        {**_mov(30, "2026-09-10", 1, -1130.88, "TINVENT_DEP", "1;2947"), "saldo": 25.76, "saldo_ant": 1156.64},
        {**_mov(31, "2026-09-10", 0, 706.64, "TINVENT_DEP", "1;2947"), "saldo": 706.64, "saldo_ant": 25.76},
    ]
    grv = _grv(produtos=[_produto(saldos={"1": 706.64}, preco=2.0)], movimentos=movs, notas={})
    item = _item(grv)
    remocao, ajuste = item["linhas"]
    assert remocao["qtde"] == pytest.approx(-1130.88) and remocao["qtde_lancada"] is None
    assert ajuste["qtde"] == pytest.approx(680.88) and ajuste["qtde_lancada"] == pytest.approx(706.64)
    assert item["abertura"]["q"] == {"1": pytest.approx(1156.64)}
    assert item["fechamento"]["q"] == {"1": pytest.approx(706.64)}
    assert "divergente_grv" not in item["alertas"]


@pytest.mark.parametrize("com_saldo", [True, False])
def test_anotacao_de_reserva_gravada_como_entrada_nao_mexe_no_saldo(com_saldo):
    """Tipo 0 TCOMPRAS "INSERINDO ESTOQUE SOLICITADO RESERVADO" não altera o
    qtde_total no GRV. Com o saldo da linha, vale a variação (zero); sem ele
    (bridge antiga), o texto da observação."""
    nota = _mov(40, "2026-09-05", 0, 1152.0, "TCOMPRAS", "1;16814", obs="INSERINDO ESTOQUE SOLICITADO RESERVADO. ENTRADA: 16814")
    entrada = dict(MOV_ENTRADA_78851)
    if com_saldo:
        nota = {**nota, "saldo": 6898.62, "saldo_ant": 6898.62}
        entrada = {**entrada, "saldo": 6898.62, "saldo_ant": 4518.62}
    grv = _grv(produtos=[_produto(saldos={"1": 6898.62})], movimentos=[entrada, nota])
    item = _item(grv)
    assert [l["classe"] for l in item["linhas"]] == ["entrada", "informativo"]
    # Não entra na base do custo unitário da NF (2.380 kg, não 3.532).
    assert item["linhas"][0]["custo_unit"] == pytest.approx(LIQUIDO_NF_78851 / 2380)
    assert svc.resumo(item)["qtde_entrada"] == pytest.approx(2380.0)
    assert item["fechamento"]["q"] == {"1": pytest.approx(6898.62)}


def test_troca_de_material_tipo_9_mexe_no_saldo():
    movs = [_mov(50, "2026-09-08", 9, 5.98, "TPRODUTO", "", obs="MATERIAL DE ORIGEM:19-01-00999"),
            _mov(51, "2026-09-09", 9, -2.0, "TPRODUTO", "", obs="MATERIAL DE DESTINO:19-01-00998")]
    grv = _grv(produtos=[_produto(saldos={"1": 103.98}, preco=5.0)], movimentos=movs, notas={})
    item = _item(grv)
    assert [(l["classe"], l["tipo_rotulo"]) for l in item["linhas"]] == [("entrada", "Troca de material"), ("saida", "Troca de material")]
    linha = svc.resumo(item)
    assert linha["qtde_inicial"] == pytest.approx(100.0) and linha["qtde_final"] == pytest.approx(103.98)
    assert linha["valor_entrada"] == pytest.approx(29.9) and linha["valor_saida"] == pytest.approx(-10.0)


def test_sem_mes_fechado_abre_pelo_saldo_e_custo_do_grv_na_vespera():
    """Como a Contabilidade abre a ficha: saldo do depósito 1 em 31/08 pelo
    custo que o GRV tinha gravado na data (4.518,62 kg x 4,14 = 18.707,09)."""
    produto = {**_produto(preco=9.99), "abertura": {"qtde": 4518.62, "custo": 4.14, "data": "2026-08-31T15:54:08"}}
    det = svc.detalhe(_item(_grv(produtos=[produto])))
    assert det["inicial"] == {"qtde": 4518.62, "valor": 18707.09, "custo_medio": 4.14}
    assert det["linhas"][0]["saldo_valor"] == pytest.approx(28400.50, abs=0.01)
    assert det["linhas"][1]["valor"] == pytest.approx(-85.47, abs=0.01)
    assert det["linhas"][1]["saldo_valor"] == pytest.approx(28315.03, abs=0.01)
    # GRV sem custo na data: usa o custo atual do produto e avisa.
    sem_custo = {**produto, "abertura": {"qtde": 4518.62, "custo": None, "data": None}}
    item = _item(_grv(produtos=[sem_custo]))
    assert item["abertura"]["m"] == 9.99 and "custo_estimado" in item["alertas"]


def test_ancora_antiga_com_outros_depositos_abre_so_pelo_deposito_1():
    ancora = {"q": {"1": 4518.62, "2": 300.0}, "m": 18707.09 / 4518.62}
    det = svc.detalhe(_item(ancora=ancora))
    assert det["inicial"]["qtde"] == pytest.approx(4518.62)
    assert det["inicial"]["valor"] == pytest.approx(18707.09, abs=0.01)


def test_inventario_vira_ajuste_e_tipo_8_nao_mexe_no_saldo():
    movs = [_mov(20, "2026-09-15", 1, -50.0, "TINVENT_DEP", "1;777", dep=1),
            _mov(21, "2026-09-15", 0, 45.0, "TINVENT_DEP", "1;777", dep=1),
            _mov(22, "2026-09-16", 8, 999.0, "TINVENT_DEP", "1;778", dep=1)]
    grv = _grv(produtos=[_produto(saldos={"1": 45.0}, preco=2.0)], movimentos=movs, notas={})
    det = svc.detalhe(_item(grv))
    assert [l["classe"] for l in det["linhas"]] == ["ajuste", "ajuste", "informativo"]
    assert det["linhas"][0]["doc_inventario"] == "777"
    assert det["inicial"]["qtde"] == pytest.approx(50.0)
    assert det["final"]["qtde"] == pytest.approx(45.0)
    assert svc.resumo(_item(grv))["qtde_ajuste"] == pytest.approx(-5.0)


def test_saldo_que_passa_por_zero_marca_custo_estimado():
    movs = [_mov(30, "2026-09-02", 1, -10.0, "TSAIDA_E", "1;1"),
            _mov(31, "2026-09-05", 0, 20.0, "TCOMPRAS", "1;500")]
    notas = {"500": {"cod_compra": 500, "n_nf": "1", "itens": [{"cod_produto": COD, "qtde": 20, "valor_total": 60.0}]}}
    grv = _grv(produtos=[_produto(saldos={"1": 20.0}, preco=3.0)], movimentos=movs, notas=notas)
    item = _item(grv)
    assert "custo_estimado" in item["alertas"]
    assert svc.detalhe(item)["inicial"]["qtde"] == pytest.approx(10.0)


def test_entrada_de_nf_sem_valor_vai_pelo_medio_e_alerta():
    movs = [_mov(40, "2026-09-05", 0, 10.0, "TCOMPRAS", "1;900")]
    notas = {"900": {"cod_compra": 900, "n_nf": "9", "itens": [{"cod_produto": 999, "valor_total": 1.0}]}}
    grv = _grv(produtos=[_produto(saldos={"1": 20.0}, preco=3.0)], movimentos=movs, notas=notas)
    item = _item(grv)
    assert "nf_sem_custo" in item["alertas"]
    assert item["linhas"][0]["origem_custo"] == "nf_nao_encontrada"
    assert item["linhas"][0]["custo_unit"] == pytest.approx(3.0)


# --------------------------------------------------------------------------
# Rotas, exportação, fechamento
# --------------------------------------------------------------------------

def _app_logado(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()
    login_admin(client)
    return app, client


def test_pagina_do_cardex_renderiza_e_aparece_no_menu(tmp_path):
    _, client = _app_logado(tmp_path)
    html = client.get("/logistica/cardex").get_data(as_text=True)
    assert 'id="cx-resumo-body"' in html
    assert 'href="/logistica/cardex"' in html
    assert 'id="cx-btn-fechar"' in html


def test_api_resumo_e_item(tmp_path):
    _, client = _app_logado(tmp_path)
    with patch.object(svc, "buscar_cardex_grv", return_value=_grv()) as grv:
        dados = client.get("/api/logistica/cardex/resumo?inicio=2026-09-01&fim=2026-09-30").get_json()
        assert grv.call_args.args[0] == date(2026, 9, 1) and grv.call_args.args[1] is None
        assert dados["fonte"] == "grv"
        assert dados["depositos"] == [{"codigo": 1, "nome": "PRINCIPAL"}]
        assert dados["indicadores"]["valor_saidas"] == pytest.approx(-85.47, abs=0.01)
        assert list(dados["subtotais_familia"]) == ["N - 01 - MATÉRIA-PRIMA"]

        item = client.get("/api/logistica/cardex/item?inicio=2026-09-01&fim=2026-09-30&codigo=19-01-00564").get_json()
        # Veio do cache do resumo: não foi ao GRV de novo.
        assert grv.call_count == 1
        assert len(item["item"]["linhas"]) == 3

        assert client.get("/api/logistica/cardex/item?inicio=2026-09-01&fim=2026-09-30&codigo=OUTRO").status_code == 404
        assert client.get("/api/logistica/cardex/resumo?inicio=2026-09-30&fim=2026-09-01").status_code == 400
        assert client.get("/api/logistica/cardex/resumo?depositos=abc").status_code == 400


def test_grv_fora_nao_derruba_a_tela(tmp_path):
    _, client = _app_logado(tmp_path)
    with patch.object(svc, "buscar_cardex_grv", return_value={"disponivel": False, "erro": "Bridge fora"}):
        dados = client.get("/api/logistica/cardex/resumo?inicio=2026-09-01&fim=2026-09-30").get_json()
    assert dados["disponivel"] is False and dados["erro"] == "Bridge fora"
    assert dados["linhas"] == []


def test_exportacoes(tmp_path):
    _, client = _app_logado(tmp_path)
    base = "inicio=2026-09-01&fim=2026-09-30"
    with patch.object(svc, "buscar_cardex_grv", return_value=_grv()):
        resp = client.get(f"/api/logistica/cardex/exportar.xlsx?{base}&tipo=completo")
        assert resp.status_code == 200
        wb = load_workbook(BytesIO(resp.data))
        assert wb.sheetnames == ["Resumo", "Cardex"]
        cardex = [[c for c in linha] for linha in wb["Cardex"].iter_rows(values_only=True)]
        assert any(l[1] == "Saldo inicial" and l[19] == pytest.approx(4518.62) for l in cardex)
        assert any(l[3] == "Saída 53918" and l[13] == pytest.approx(-85.47, abs=0.01) for l in cardex)
        resumo = [l for l in wb["Resumo"].iter_rows(values_only=True)]
        assert any(l[0] == "TOTAL GERAL" for l in resumo)

        resp = client.get(f"/api/logistica/cardex/exportar.xlsx?{base}&tipo=item&codigo=19-01-00564")
        assert load_workbook(BytesIO(resp.data)).sheetnames == ["Cardex"]
        for tipo in ("resumo", "item"):
            resp = client.get(f"/api/logistica/cardex/exportar.pdf?{base}&tipo={tipo}&codigo=19-01-00564")
            assert resp.status_code == 200 and resp.data.startswith(b"%PDF")
        assert client.get(f"/api/logistica/cardex/exportar.xlsx?{base}&tipo=xpto").status_code == 400


def test_fechar_mes_congela_e_ancora_o_mes_seguinte(tmp_path):
    app, client = _app_logado(tmp_path)
    agosto = _grv(movimentos=[MOV_ENTRADA_78851, MOV_SAIDA_53918, MOV_ENTRADA_173975])
    with patch.object(svc, "buscar_cardex_grv", return_value=agosto):
        resp = client.post("/api/logistica/cardex/fechamentos", json={"ano": 2026, "mes": 8})
    assert resp.status_code == 201, resp.get_json()
    fech = resp.get_json()["fechamento"]
    assert fech["total_itens"] == 1 and fech["origem_abertura"] == "grv"
    assert fech["valor_final"] == pytest.approx(18707.09, abs=0.01)

    # Agosto agora vem da foto: o GRV nem é consultado.
    with patch.object(svc, "buscar_cardex_grv", side_effect=AssertionError("não devia ir ao GRV")):
        dados = client.get("/api/logistica/cardex/resumo?inicio=2026-08-01&fim=2026-08-31").get_json()
    assert dados["fonte"] == "congelado"
    assert dados["totais"]["valor_final"] == pytest.approx(18707.09, abs=0.01)

    # Setembro abre pelo fechamento de agosto e busca só a partir de 01/09.
    with patch.object(svc, "buscar_cardex_grv", return_value=_grv()) as grv:
        dados = client.get("/api/logistica/cardex/resumo?inicio=2026-09-01&fim=2026-09-30&atualizar=1").get_json()
    assert grv.call_args.args[:2] == (date(2026, 9, 1), date(2026, 9, 30))
    assert dados["fonte"] == "ancora" and dados["ancora"]["rotulo"] == "08/2026"
    assert dados["totais"]["valor_inicial"] == pytest.approx(18707.09, abs=0.01)
    assert dados["totais"]["valor_final"] == pytest.approx(VALOR_HOJE, abs=0.01)

    assert client.post("/api/logistica/cardex/fechamentos", json={"ano": 2026, "mes": 8}).status_code == 400
    assert client.post("/api/logistica/cardex/fechamentos", json={"ano": 2099, "mes": 1}).status_code == 400
    assert client.post(f"/api/logistica/cardex/fechamentos/{fech['id']}/reabrir", json={}).status_code == 400
    resp = client.post(f"/api/logistica/cardex/fechamentos/{fech['id']}/reabrir", json={"motivo": "NF lançada com data errada"})
    assert resp.get_json()["fechamento"]["status"] == "reaberto"
    with app.app_context():
        assert LogisticaCardexFechamentoItem.query.count() == 0
        assert LogisticaCardexFechamento.query.one().motivo_reabertura == "NF lançada com data errada"


def test_nao_reabre_mes_com_mes_seguinte_fechado(tmp_path):
    app, _ = _app_logado(tmp_path)
    with app.app_context():
        with patch.object(svc, "buscar_cardex_grv", return_value=_grv()):
            julho = svc.fechar_mes(2026, 7, "ADMIN", hoje=date(2026, 9, 30))
            svc.fechar_mes(2026, 8, "ADMIN", hoje=date(2026, 9, 30))
        with pytest.raises(ValueError, match="08/2026 está fechado"):
            svc.reabrir_fechamento(julho["id"], "ADMIN", "motivo qualquer")
        with pytest.raises(ValueError, match="depois que o mês terminar"):
            svc.fechar_mes(2026, 9, "ADMIN", hoje=date(2026, 9, 30))


def test_conferir_fechamento_mostra_o_que_mudou_no_grv(tmp_path):
    app, client = _app_logado(tmp_path)
    with patch.object(svc, "buscar_cardex_grv", return_value=_grv()):
        fech = client.post("/api/logistica/cardex/fechamentos", json={"ano": 2026, "mes": 8}).get_json()["fechamento"]
    # Depois do fechamento entrou no GRV uma saída retroativa em agosto (que
    # também baixou o saldo de hoje).
    retroativa = _mov(50, "2026-08-20", 1, -18.62, "TSAIDA_E", "1;59999")
    grv_hoje = _grv(produtos=[_produto(saldos={"1": QTDE_HOJE - 18.62})],
                    movimentos=[retroativa, MOV_ENTRADA_78851, MOV_SAIDA_53918, MOV_ENTRADA_173975])
    with patch.object(svc, "buscar_cardex_grv", return_value=grv_hoje):
        dados = client.get(f"/api/logistica/cardex/fechamentos/{fech['id']}/conferir").get_json()
    assert dados["itens_conferidos"] == 1
    (dif,) = dados["diferencas"]
    assert dif["qtde_congelada"] == pytest.approx(4518.62)
    assert dif["qtde_grv_hoje"] == pytest.approx(4500.0)
    assert dif["movimentos_grv_hoje"] == 1


def test_conciliacao(tmp_path):
    app, client = _app_logado(tmp_path)
    movs = [_mov(60, "2026-09-15", 1, -5.0, "TINVENT_DEP", "1;555")]
    grv = _grv(produtos=[_produto(saldos={"1": 95.0}, preco=2.0)], movimentos=movs, notas={})
    historico = {"disponivel": True, "produtos": [
        _produto(saldos={"1": 95.0}),
        {**_produto(saldos={"1": 10.0}), "cod_produto": 6000, "codigo": "X1", "codigo_interno": "X-1"},
    ], "historico": [
        {"cod_produto": COD, "deposito": 1, "tipo": 0, "qtde": 100.0},
        {"cod_produto": COD, "deposito": 1, "tipo": 1, "qtde": -5.0},
        {"cod_produto": 6000, "deposito": 1, "tipo": 0, "qtde": 4.0},
        {"cod_produto": 6000, "deposito": 1, "tipo": 8, "qtde": 6.0},
    ]}
    with app.app_context():
        db.session.add(LogisticaInventarioAjuste(
            codigo_produto="19-01-00564", local_codigo="A1", unidade_medida="KG", qtde_contada=95, qtde_estoque_no_momento=100,
            diferenca=-5, finance_documento_grv="555", finance_concluido_em=datetime(2026, 9, 15, 10, 0),
        ))
        db.session.add(LogisticaInventarioAjuste(
            codigo_produto="19-01-00564", local_codigo="A1", unidade_medida="KG", qtde_contada=1, qtde_estoque_no_momento=2,
            diferenca=-1, finance_documento_grv="556", finance_concluido_em=datetime(2026, 9, 20, 10, 0),
        ))
        db.session.commit()

    def falso(desde, ate=None, codigo=None, conciliar=False, empresa=1, apenas_historico=False):
        return historico if apenas_historico else grv

    with patch.object(svc, "buscar_cardex_grv", side_effect=falso):
        dados = client.get("/api/logistica/cardex/conciliacao?inicio=2026-09-01&fim=2026-09-30").get_json()
        item = client.get("/api/logistica/cardex/item?inicio=2026-09-01&fim=2026-09-30&codigo=19-01-00564").get_json()
    saldo = dados["saldo"]
    assert saldo["fecham"] == 1 and saldo["total_divergentes"] == 1
    assert saldo["fecham_so_com_tipos_8_9"] == 1
    assert saldo["divergentes"][0]["codigo_interno"] == "X-1"
    (ajuste_grv,) = dados["ajustes"]["no_grv"]
    assert ajuste_grv["documento"] == "555" and ajuste_grv["ajuste_sync_id"] is not None
    assert [a["documento"] for a in dados["ajustes"]["sem_movimento_no_grv"]] == ["556"]
    assert item["item"]["linhas"][0]["ajuste_sync"]["id"] == ajuste_grv["ajuste_sync_id"]


def test_permissoes_do_cardex(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()
    set_logged_user(client, "LOGISTICA_TESTE", "Logística")
    assert client.get("/logistica/cardex").status_code in (302, 403)
    assert client.get("/api/logistica/cardex/resumo").status_code == 403

    set_logged_user(client, "CONTROLADORIA_TESTE", "Controladoria")
    html = client.get("/logistica/cardex").get_data(as_text=True)
    assert 'href="/logistica/cardex"' in html and 'id="cx-btn-fechar"' in html


# --------------------------------------------------------------------------
# Bridge
# --------------------------------------------------------------------------

class _CursorFalso:
    """Uma resposta (colunas, linhas) por execute, na ordem das consultas."""

    def __init__(self, respostas):
        self.respostas = list(respostas)
        self.description = None
        self.linhas = []
        self.executados = []

    def execute(self, sql, params=None):
        self.executados.append((sql, params))
        if sql.strip().lower().startswith("set local"):
            return
        colunas, self.linhas = self.respostas.pop(0)
        self.description = [(c,) for c in colunas]

    def fetchall(self):
        return self.linhas

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_bridge_cardex(monkeypatch):
    from unittest.mock import MagicMock

    from scripts import erp_lancamento_api_bridge as bridge

    cursor = _CursorFalso([
        (["cod_produto", "codigo_interno", "codigo", "descricao", "unidade", "familia", "grupo", "preco_custo", "fiscal", "saldos"],
         [(COD, "19-01-00564", CODIGO, "CHAPA", "KG", "MP", "1", 5.03, {"classificacao_fiscal": "72085200"}, {"1": 23946.48})]),
        (["cod_produto", "qtde", "custo", "data"], [(COD, 4518.62, 4.14, datetime(2026, 8, 31, 15, 54))]),
        (["id", "cod_produto", "cod_deposito", "data", "tipo", "qtde", "tabela", "chave", "obs", "saldo", "saldo_ant"],
         [("1", COD, 1, datetime(2026, 9, 1, 8), 0, 2380.0, "TCOMPRAS", "1;16814", "ENTRADA", 6898.62, 4518.62)]),
        (["cod_compra", "n_nf", "dt_nf", "fornecedor", "chave_nfe", "cfop"], [(16814, "78851", date(2026, 8, 28), "USIMINAS", "", "1101")]),
        (["cod_compra", "campos"], [(16814, {"cod_produto": COD, "valor_total": 12121.01})]),
        (["to_jsonb"], [({"cod_empresa": 1, "codigo": 1, "descricao": "PRINCIPAL"},), ({"cod_empresa": 2, "codigo": 9, "nome": "OUTRA"},)]),
    ])
    conn = MagicMock()
    conn.__enter__.return_value.cursor.return_value = cursor
    monkeypatch.setattr(bridge, "_config", lambda: {"host": "h", "database": "d", "user": "u"})
    monkeypatch.setattr(bridge, "_authorized", lambda cfg: True)
    monkeypatch.setattr(bridge, "_conectar", lambda cfg, readonly=False: conn)
    client = bridge.create_app().test_client()
    data = client.post("/api/erp/estoque/cardex", json={"desde": "2026-09-01", "codigo": "19-01-00564"}).get_json()
    assert data["sucesso"] is True
    assert data["movimentos"][0]["data"] == "2026-09-01T08:00:00"
    # Saldo da linha e da anterior (o Sync usa a variação) e abertura na véspera.
    assert data["movimentos"][0]["saldo"] == 6898.62 and data["movimentos"][0]["saldo_ant"] == 4518.62
    assert data["produtos"][0]["abertura"] == {"qtde": 4518.62, "custo": 4.14, "data": "2026-08-31T15:54:00"}
    assert data["notas"]["16814"]["n_nf"] == "78851"
    assert data["notas"]["16814"]["itens"] == [{"cod_produto": COD, "valor_total": 12121.01}]
    assert data["depositos"] == [{"codigo": 1, "nome": "PRINCIPAL"}]
    sqls = [s for s, _ in cursor.executados]
    assert "= %(codigo)s" in sqls[1]
    # Abertura (véspera, depósito 1) e movimentos, os dois filtrados pelo produto.
    assert "k.cod_deposito = 1" in sqls[2] and "k.cod_produto = any(%(produtos)s)" in sqls[2]
    assert "lag(k.qtde_total)" in sqls[3] and "k.cod_produto = any(%(produtos)s)" in sqls[3]
    assert cursor.executados[3][1]["produtos"] == [COD]
    assert client.post("/api/erp/estoque/cardex", json={}).status_code == 400
    assert client.post("/api/erp/estoque/cardex", json={"desde": "xx"}).status_code == 400
