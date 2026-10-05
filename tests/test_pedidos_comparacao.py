"""Regra de quantidade da comparação NF x pedido.

O `qtd` da linha do pedido vem do ERP como saldo PENDENTE: NF abaixo do saldo
é entrega parcial (o resto fica pra próxima NF) e não é divergência. Só NF
acima do saldo diverge.
"""
from unittest.mock import patch

from conferencia_app.services.pedidos_service import comparar_pedido_com_nf


def _linha_po(qtd, valor_unit, codigo="19-01-00563"):
    return {"pedido_compra": "12751", "qtd": qtd, "valor_unit": valor_unit,
            "codigo_material": codigo, "descricao_material": "CHAPA A36"}


def _item_nf(qtd, valor_unit, unidade="KG", item_id=1, linha_po=None):
    return {"item_id": item_id, "codigo": "21613145", "descricao": "CH FQ 16,00 A36",
            "qtd": qtd, "qtd_original": qtd, "unidade_comercial": unidade,
            "valor_unit": valor_unit, "valor_total_linha": round(qtd * valor_unit, 2),
            "linha_po_vinculada": linha_po}


def _comparar(linhas_po, itens_nf):
    with patch("conferencia_app.services.pedidos_service.buscar_linhas_pedido", return_value=linhas_po):
        return comparar_pedido_com_nf("12751", itens_nf)


def test_nf_abaixo_do_saldo_do_pedido_e_entrega_parcial_ok():
    # Caso real da NF 405199: 1.860 KG na NF, 2.260,8 KG pendentes no pedido.
    r = _comparar([_linha_po(2260.8, 6.25)], [_item_nf(1860.0, 6.25, linha_po=0)])
    par = r["pares"][0]
    assert par["qtd_ok"] is True
    assert par["ok"] is True
    assert r["total_ok"] is True


def test_nf_igual_ao_saldo_continua_ok():
    r = _comparar([_linha_po(2260.8, 6.25)], [_item_nf(2260.8, 6.25, linha_po=0)])
    assert r["pares"][0]["qtd_ok"] is True


def test_nf_acima_do_saldo_do_pedido_diverge():
    r = _comparar([_linha_po(1000.0, 6.25)], [_item_nf(1860.0, 6.25, linha_po=0)])
    par = r["pares"][0]
    assert par["qtd_ok"] is False
    assert r["total_ok"] is False


def test_rateio_soma_abaixo_do_saldo_ok_e_acima_diverge():
    po = [_linha_po(2000.0, 6.25)]
    parcial = _comparar(po, [_item_nf(800.0, 6.25, item_id=1, linha_po=0), _item_nf(700.0, 6.25, item_id=2, linha_po=0)])
    assert all(p["qtd_ok"] for p in parcial["pares"])
    estourado = _comparar(po, [_item_nf(1200.0, 6.25, item_id=1, linha_po=0), _item_nf(900.0, 6.25, item_id=2, linha_po=0)])
    assert not any(p["qtd_ok"] for p in estourado["pares"])


# --------------------------------------------------------------------------
# Industrialização por terceiros - caso real: NF 1 da INDALECIO, pedido 12156
# (OS 9769). O XML traz o retorno do material (5902, R$ 88,96) e o serviço
# (5124, 4 x R$ 150); o pedido tem a linha do material (preço 0) e a do serviço.
# --------------------------------------------------------------------------

def _po_ind(classe, codigo, qtd, valor):
    return {"pedido_compra": "12156", "qtd": qtd, "valor_unit": valor, "codigo_material": codigo,
            "descricao_material": "OS 9769 - FP RET H5 30V GRID P/B", "classificacao_item": classe}


def _nf_ind(item_id, cfop, codigo, qtd, valor_unit, linha_po=None):
    return {**_item_nf(qtd, valor_unit, unidade="PC", item_id=item_id, linha_po=linha_po), "codigo": codigo, "cfop": cfop}


PO_12156 = [_po_ind("ProducaoPropria", "9769/002-2", 4.0, 0.0), _po_ind("ProducaoTerceiros", "22-02-04369", 4.0, 150.0)]


def test_retorno_de_material_nao_diverge_e_servico_continua_conferido():
    r = _comparar(PO_12156, [_nf_ind(1, "5902", "9769/002-2", 4.0, 22.24), _nf_ind(2, "5124", "22-02-043/69", 4.0, 150.0)])
    retorno, servico = r["pares"]
    assert retorno["po_classificacao"] == "ProducaoPropria" and retorno["ok"] is True
    assert "Retorno de industrialização" in retorno["regra_industrializacao"]
    assert servico["po_classificacao"] == "ProducaoTerceiros" and servico["ok"] is True
    assert servico["regra_industrializacao"] is None
    assert r["total_ok"] is True


def test_servico_com_preco_diferente_do_pedido_continua_divergindo():
    r = _comparar(PO_12156, [_nf_ind(1, "5902", "9769/002-2", 4.0, 22.24), _nf_ind(2, "5124", "22-02-043/69", 4.0, 180.0)])
    assert r["pares"][0]["ok"] is True
    assert r["pares"][1]["valor_ok"] is False and r["total_ok"] is False


def test_pedido_de_industrializacao_com_quantidade_zerada_nao_vira_divergencia():
    po = [_po_ind("ProducaoPropria", "9769/002-2", 0.0, 0.0), _po_ind("ProducaoTerceiros", "22-02-04369", 0.0, 150.0)]
    r = _comparar(po, [_nf_ind(1, "5902", "9769/002-2", 4.0, 22.24), _nf_ind(2, "5124", "22-02-043/69", 4.0, 150.0)])
    assert all(p["qtd_ok"] for p in r["pares"]) and r["total_ok"] is True
    assert "não controla quantidade" in r["pares"][1]["regra_industrializacao"]


def test_retorno_nao_e_pareado_com_linha_de_servico_da_mesma_os():
    # Sem código em comum, o pareamento por posição/score ligaria o retorno
    # (1ª linha do XML) à linha de serviço (1ª do pedido).
    po = [_po_ind("ProducaoTerceiros", "22-02-04369", 4.0, 150.0), _po_ind("ProducaoPropria", "9769/002-2", 4.0, 0.0)]
    r = _comparar(po, [_nf_ind(1, "5902", "XX-1", 4.0, 22.24), _nf_ind(2, "5124", "XX-2", 4.0, 150.0)])
    assert r["pares"][0]["po_classificacao"] != "ProducaoTerceiros"
    assert r["pares"][1]["po_classificacao"] == "ProducaoTerceiros" and r["pares"][1]["ok"] is True


def test_retorno_sem_linha_no_pedido_nao_e_divergencia():
    po = [_po_ind("ProducaoTerceiros", "22-02-04369", 4.0, 150.0)]
    r = _comparar(po, [_nf_ind(1, "5902", "9769/002-2", 4.0, 22.24), _nf_ind(2, "5124", "22-02-043/69", 4.0, 150.0)])
    assert r["pares"][0]["po_index"] is None and r["pares"][0]["ok"] is True
    assert r["total_ok"] is True


def test_compra_comum_nao_e_afetada_pela_regra():
    r = _comparar([_linha_po(1000.0, 6.25)], [{**_item_nf(1860.0, 6.25, linha_po=0), "cfop": "5101"}])
    assert r["pares"][0]["qtd_ok"] is False and r["pares"][0]["regra_industrializacao"] is None


def test_entrega_parcial_em_unidade_nao_peso_nao_inventa_fator():
    # Preço diverge nos dois cenários; sem o desempate o "ratio direto"
    # (fator que faz a NF encostar no saldo) venceria só por chegar mais perto.
    r = _comparar([_linha_po(50.0, 10.0)], [_item_nf(20.0, 11.0, unidade="UN", linha_po=0)])
    par = r["pares"][0]
    assert par["conversao_fator"] == 1.0
    assert par["nf_qtd"] == 20.0
    assert par["qtd_ok"] is True
    assert par["valor_ok"] is False
