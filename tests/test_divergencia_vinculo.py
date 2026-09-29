"""Ajuste de vinculo NF x pedido feito por Compras na tela de aprovacao da
divergencia. ERP sempre mockado (buscar_linhas_pedido)."""
from unittest.mock import patch

from test_app import build_test_app

from conferencia_app.extensions import db
from conferencia_app.models import DivergenciaPedidoAprovacao, DivergenciaVinculoAjuste, ItemNota

# Linhas por OC, como o ERP devolve (buscar_linhas_pedido ordena os pedidos).
_ERP = {
    "12717": [
        {"pedido_compra": "12717", "codigo_material": "ITM-A", "descricao_material": "CHAPA A", "qtd": 100.0, "valor_unit": 10.0},
        {"pedido_compra": "12717", "codigo_material": "ITM-B", "descricao_material": "CHAPA B", "qtd": 50.0, "valor_unit": 20.0},
    ],
    "10500": [
        {"pedido_compra": "10500", "codigo_material": "ITM-C", "descricao_material": "CHAPA C", "qtd": 30.0, "valor_unit": 5.0},
    ],
}


def _fake_buscar(pedidos):
    linhas = []
    for pd in sorted({p.strip() for p in str(pedidos).split(",") if p.strip()}):
        linhas.extend(dict(l) for l in _ERP.get(pd, []))
    return linhas


def _patch_erp():
    return (
        patch("conferencia_app.services.divergencia_vinculo_service.buscar_linhas_pedido", side_effect=_fake_buscar),
        patch("conferencia_app.services.pedidos_service.buscar_linhas_pedido", side_effect=_fake_buscar),
    )


def _setup(tmp_path, status="Pendente"):
    app = build_test_app(tmp_path)
    with app.app_context():
        for cod, qtd, valor in (("XML-1", 100.0, 1000.0), ("XML-2", 50.0, 1000.0)):
            db.session.add(ItemNota(
                numero_nota="555", codigo=cod, descricao=cod, qtd_real=qtd, valor_produto=valor,
                cnpj_emitente="11111111000111", fornecedor="FORN A", pedido_compra="12717",
            ))
        # NF homonima de outro fornecedor: nunca pode aparecer nem ser alterada.
        db.session.add(ItemNota(
            numero_nota="555", codigo="OUTRO", descricao="OUTRO", qtd_real=1.0, valor_produto=1.0,
            cnpj_emitente="22222222000122", fornecedor="FORN B", pedido_compra="99999",
        ))
        db.session.flush()
        itens = ItemNota.query.filter_by(cnpj_emitente="11111111000111").order_by(ItemNota.id).all()
        itens[0].linha_po_vinculada = 0  # OC 12717 linha 1
        itens[1].linha_po_vinculada = 1  # OC 12717 linha 2
        db.session.add(DivergenciaPedidoAprovacao(
            numero_nota="555", pedido_compra="12717", fornecedor="FORN A", status=status,
            token="tok", cnpj_emitente="11111111000111",
        ))
        db.session.commit()
    return app


def _login(client):
    r = client.post("/aprovar-divergencia/tok/login", json={"username": "admin", "password": "admin1234"})
    assert r.status_code == 200, r.get_json()


def _itens(app):
    with app.app_context():
        return [
            (i.codigo, i.pedido_compra, i.linha_po_vinculada)
            for i in ItemNota.query.filter_by(cnpj_emitente="11111111000111").order_by(ItemNota.id)
        ]


def _salvar(app, pedidos, vinculos):
    from conferencia_app.services.divergencia_vinculo_service import salvar_vinculos

    p1, p2 = _patch_erp()
    with app.app_context(), p1, p2:
        reg = DivergenciaPedidoAprovacao.query.filter_by(token="tok").first()
        ids = [i.id for i in ItemNota.query.filter_by(cnpj_emitente="11111111000111").order_by(ItemNota.id)]
        return salvar_vinculos(reg, pedidos, {ids[k]: v for k, v in vinculos.items()}, "COMPRADOR")


def test_divergencia_vinculo_troca_linha_grava_indice_e_historico(tmp_path):
    app = _setup(tmp_path)
    res = _salvar(app, "12717", {0: "12717#2"})
    assert res["alteracoes"] == 1
    assert _itens(app) == [("XML-1", "12717", 1), ("XML-2", "12717", 1)]
    with app.app_context():
        aj = DivergenciaVinculoAjuste.query.one()
        assert (aj.de, aj.para, aj.usuario) == ("OC 12717 · linha 1 · ITM-A", "OC 12717 · linha 2 · ITM-B", "COMPRADOR")
        assert ItemNota.query.filter_by(codigo="XML-1").one().codigo_grv == "ITM-B"


def test_divergencia_vinculo_pedido_novo_menor_nao_escorrega_indice(tmp_path):
    """10500 < 12717: entra NA FRENTE da lista combinada. O item nao mexido
    tem que continuar na MESMA linha da OC 12717 (indice recalculado)."""
    app = _setup(tmp_path)
    _salvar(app, "12717,10500", {1: "10500#1"})
    # Lista nova: [10500#1, 12717#1, 12717#2]
    assert _itens(app) == [("XML-1", "10500,12717", 1), ("XML-2", "10500,12717", 0)]
    with app.app_context():
        reg = DivergenciaPedidoAprovacao.query.filter_by(token="tok").first()
        assert reg.pedido_compra == "10500,12717"
        # 1 troca de conjunto de pedidos + 1 troca de linha; item 1 nao mudou.
        assert DivergenciaVinculoAjuste.query.count() == 2


def test_divergencia_vinculo_remover_pedido_volta_item_pro_automatico(tmp_path):
    app = _setup(tmp_path)
    _salvar(app, "12717,10500", {1: "10500#1"})
    _salvar(app, "12717", {})
    assert _itens(app) == [("XML-1", "12717", 0), ("XML-2", "12717", None)]


def test_divergencia_vinculo_rejeita_pedido_sem_linhas_e_divergencia_decidida(tmp_path):
    import pytest

    app = _setup(tmp_path)
    with pytest.raises(ValueError, match="sem linhas no ERP: 77777"):
        _salvar(app, "12717,77777", {})
    with pytest.raises(ValueError, match="Linha de pedido inválida"):
        _salvar(app, "12717", {0: "12717#9"})
    assert _itens(app) == [("XML-1", "12717", 0), ("XML-2", "12717", 1)]

    (tmp_path / "b").mkdir()
    app2 = _setup(tmp_path / "b", status="Aprovado")
    with pytest.raises(ValueError, match="já foi aprovado"):
        _salvar(app2, "12717", {0: "12717#2"})


def test_divergencia_vinculo_rota_exige_login_e_isola_nf_homonima(tmp_path):
    app = _setup(tmp_path)
    client = app.test_client()
    assert client.post("/aprovar-divergencia/tok/vinculos", json={"pedidos": "12717"}).status_code == 401
    _login(client)

    with app.app_context():
        outro_id = ItemNota.query.filter_by(codigo="OUTRO").one().id
        item_id = ItemNota.query.filter_by(codigo="XML-1").one().id
    p1, p2 = _patch_erp()
    with p1, p2:
        r = client.post("/aprovar-divergencia/tok/vinculos", json={"pedidos": "12717", "vinculos": {str(outro_id): "12717#1"}})
        assert r.status_code == 400 and "não pertence" in r.get_json()["msg"]

        r = client.post("/aprovar-divergencia/tok/vinculos", json={"pedidos": "12717", "vinculos": {str(item_id): "12717#2"}})
        assert r.status_code == 200, r.get_json()
        assert r.get_json()["alteracoes"] == 1

        dados = client.get("/aprovar-divergencia/tok/dados").get_json()
    assert dados["sucesso"]
    assert [i["codigo"] for i in dados["itens_xml"]] == ["XML-1", "XML-2"]
    assert dados["pedidos"] == "12717"
    assert [l["chave"] for l in dados["linhas_po"]] == ["12717#1", "12717#2"]
    assert dados["historico"][0]["para"] == "OC 12717 · linha 2 · ITM-B"
    with app.app_context():
        assert ItemNota.query.filter_by(codigo="OUTRO").one().pedido_compra == "99999"


def test_divergencia_vinculo_linhas_pedido_e_status_do_auditor(tmp_path):
    app = _setup(tmp_path)
    client = app.test_client()
    _login(client)
    p1, p2 = _patch_erp()
    with p1, p2:
        r = client.get("/aprovar-divergencia/tok/linhas-pedido?pedidos=12717,10500")
    assert [l["chave"] for l in r.get_json()["linhas"]] == ["10500#1", "12717#1", "12717#2"]

    _salvar(app, "12717", {0: "12717#2"})
    admin = app.test_client()
    admin.post("/login", json={"username": "admin", "password": "admin1234"})
    st = admin.get("/api/xml_auditor/divergencia/status?nota=555").get_json()
    assert st["ajustes_vinculo"][0]["usuario"] == "COMPRADOR"
