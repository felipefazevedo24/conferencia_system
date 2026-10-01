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


def test_entrega_parcial_em_unidade_nao_peso_nao_inventa_fator():
    # Preço diverge nos dois cenários; sem o desempate o "ratio direto"
    # (fator que faz a NF encostar no saldo) venceria só por chegar mais perto.
    r = _comparar([_linha_po(50.0, 10.0)], [_item_nf(20.0, 11.0, unidade="UN", linha_po=0)])
    par = r["pares"][0]
    assert par["conversao_fator"] == 1.0
    assert par["nf_qtd"] == 20.0
    assert par["qtd_ok"] is True
    assert par["valor_ok"] is False
