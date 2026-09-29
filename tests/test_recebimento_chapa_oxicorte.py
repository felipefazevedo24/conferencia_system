"""Chapa oxicorte da Aços Radial na conferência de recebimento.

Fornecedor, família e códigos reais do GRV (consulta de 29/09/2026): Aços
Radial é o fornecedor 322, CNPJ 00.446.473/0001-17, e a família 42 tem os
produtos "MP AÇOS ... OXICORTE" em KG. GRV sempre com patch.
"""
from unittest.mock import patch

import pytest

from test_app import build_test_app, login_admin
from conferencia_app.extensions import db
from conferencia_app.models import ChapaCalculo, ChapaControleExclusao, ItemNota
from conferencia_app.routes import api_routes
from conferencia_app.services import chapa_oxicorte_service as svc

CNPJ_RADIAL = "00446473000117"
FAMILIA_42 = "N - 42 - MATÉRIA-PRIMA - MATERIAL ESPECÍFICO"
ESTOQUE_GRV = {"190100012": FAMILIA_42, "190100563": "N - 01 - MATÉRIA-PRIMA"}


def criar_item(app, *, nota="7001", cnpj=CNPJ_RADIAL, codigo_grv="19-01-00012", linha_po=0, codigo="X1", unidade="KG"):
    with app.app_context():
        item = ItemNota(numero_nota=nota, fornecedor="ACOS RADIAL IND E COM DE FERRO E ACO LTDA", cnpj_emitente=cnpj,
                        codigo=codigo, codigo_grv=codigo_grv, descricao="PECA OXICORTE", qtd_real=128.74,
                        unidade_comercial=unidade, status="Pendente", pedido_compra="12800",
                        linha_po_vinculada=linha_po)
        db.session.add(item)
        db.session.commit()
        return item.id


@pytest.fixture
def cliente(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()
    login_admin(client)
    # A sincronização com o pedido consulta planilha/ERP: fora do teste.
    with patch.object(api_routes, "_sincronizar_codigo_interno_por_pedido", return_value={"atualizou": False}):
        yield app, client


def test_abre_a_conferencia_com_o_item_oxicorte_marcado(cliente):
    app, client = cliente
    oxi = criar_item(app)
    comum = criar_item(app, codigo="X2", codigo_grv="19-01-00563", linha_po=1)
    with patch.object(svc, "buscar_familias_grv", return_value=ESTOQUE_GRV):
        resp = client.get("/api/itens/7001")
    assert resp.status_code == 200
    por_id = {i["id"]: i for i in resp.get_json()}
    assert por_id[oxi]["oxicorte"] is True
    assert por_id[comum]["oxicorte"] is False


def test_bloqueia_se_alguma_linha_nao_esta_vinculada_ao_pedido(cliente):
    app, client = cliente
    criar_item(app)
    criar_item(app, codigo="X2", codigo_grv=None, linha_po=None)
    with patch.object(svc, "buscar_familias_grv", return_value=ESTOQUE_GRV):
        resp = client.get("/api/itens/7001")
    assert resp.status_code == 409
    assert "sem vínculo com o pedido" in resp.get_json()["error"]


def test_bloqueia_se_o_grv_nao_responde(cliente):
    app, client = cliente
    criar_item(app)
    with patch.object(svc, "buscar_familias_grv", side_effect=RuntimeError("bridge fora")):
        resp = client.get("/api/itens/7001")
    assert resp.status_code == 409
    assert "não consegui consultar o GRV" in resp.get_json()["error"]


def test_bloqueia_se_o_codigo_do_pedido_nao_existe_no_grv(cliente):
    app, client = cliente
    criar_item(app, codigo_grv="19-01-99999")
    with patch.object(svc, "buscar_familias_grv", return_value=ESTOQUE_GRV):
        resp = client.get("/api/itens/7001")
    assert resp.status_code == 409
    assert "19-01-99999" in resp.get_json()["error"]


def test_outro_fornecedor_nao_passa_pela_regra(cliente):
    """Nem consulta o GRV: fornecedor diferente segue o fluxo de sempre, com ou sem vínculo."""
    app, client = cliente
    criar_item(app, nota="7002", cnpj="33699114000120", codigo_grv=None, linha_po=None)
    with patch.object(svc, "buscar_familias_grv") as mock_grv:
        resp = client.get("/api/itens/7002")
    assert resp.status_code == 200
    assert resp.get_json()[0]["oxicorte"] is False
    mock_grv.assert_not_called()


def test_validar_oxicorte_so_com_und_e_fora_do_controle_de_chapas(cliente):
    app, client = cliente
    item_id = criar_item(app)
    with patch.object(svc, "buscar_familias_grv", return_value=ESTOQUE_GRV):
        resp = client.post("/validar", json={"nota": "7001", "contagens": {}, "chapas_itens": {str(item_id): {"quantidade": "3"}}})
    corpo = resp.get_json()
    assert resp.status_code == 200 and corpo["sucesso"] is True
    with app.app_context():
        item = db.session.get(ItemNota, item_id)
        assert item.qtd_chapas_und == 3
        # Sem medidas: nada de cálculo de peso; e fora do Controle de Chapas.
        assert ChapaCalculo.query.filter_by(item_nota_id=item_id).count() == 0
        exclusao = ChapaControleExclusao.query.filter_by(item_nota_id=item_id).one()
        assert exclusao.usuario == svc.USUARIO_EXCLUSAO


def test_validar_oxicorte_sem_und_e_recusado_mesmo_se_a_tela_nao_mandar_chapa(cliente):
    """O servidor decide que é chapa: a tela não consegue "desmarcar" deixando de mandar."""
    app, client = cliente
    item_id = criar_item(app)
    with patch.object(svc, "buscar_familias_grv", return_value=ESTOQUE_GRV):
        resp = client.post("/validar", json={"nota": "7001", "contagens": {str(item_id): "128.74"}})
    assert resp.status_code == 400
    assert "quantidade de peças" in resp.get_json()["msg"]
    with app.app_context():
        assert db.session.get(ItemNota, item_id).qtd_chapas_und is None


def test_validar_bloqueia_se_o_grv_cair_depois_de_abrir(cliente):
    app, client = cliente
    item_id = criar_item(app)
    with patch.object(svc, "buscar_familias_grv", side_effect=RuntimeError("bridge fora")):
        resp = client.post("/validar", json={"nota": "7001", "contagens": {}, "chapas_itens": {str(item_id): {"quantidade": "3"}}})
    assert resp.status_code == 409
    assert resp.get_json()["sucesso"] is False


def test_tela_do_conferente_trata_oxicorte_e_erro_ao_abrir(cliente):
    app, client = cliente
    resp = client.get("/conferencia")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "chapa-modal-oxicorte" in html
    assert "dataset.oxicorte" in html
    assert "if (!res.ok)" in html


def test_familias_consulta_so_os_codigos_guarda_em_cache_e_cai_no_estoque_se_a_bridge_for_antiga(tmp_path):
    import requests
    from unittest.mock import MagicMock
    from conferencia_app.services import erp_estoque_service as est

    app = build_test_app(tmp_path)
    resposta = MagicMock(status_code=200, text="{}")
    resposta.json.return_value = {"sucesso": True, "familias": {"190100012": FAMILIA_42}}
    with app.app_context(), \
         patch.dict(est._FAMILIA_CACHE, clear=True), \
         patch.object(est, "estoque_grv_em_cache", return_value=None), \
         patch.object(est, "_bridge_config", return_value={"api_url": "http://bridge", "api_token": "", "timeout": 10}), \
         patch.object(est.requests, "post", return_value=resposta) as post:
        assert est.buscar_familias_grv(["19-01-00012"]) == {"190100012": FAMILIA_42}
        assert post.call_args.kwargs["json"]["codigos"] == ["190100012"]
        # Segunda abertura: vem do cache, sem ir à bridge.
        est.buscar_familias_grv(["19-01-00012"])
        assert post.call_count == 1

    # VM ainda sem o endpoint: 404 do próprio Flask cai na consulta de estoque completa.
    antiga = MagicMock(status_code=404, text='{"erro": "not found"}')
    with app.app_context(), \
         patch.dict(est._FAMILIA_CACHE, clear=True), \
         patch.object(est, "estoque_grv_em_cache", return_value=None), \
         patch.object(est, "_bridge_config", return_value={"api_url": "http://bridge", "api_token": "", "timeout": 10}), \
         patch.object(est.requests, "post", return_value=antiga), \
         patch.object(est, "buscar_estoque_grv", return_value={"por_codigo": {"19-01-00012": {"familia": FAMILIA_42}}}) as completo:
        assert est.buscar_familias_grv(["19-01-00012", "19-01-99999"]) == {"190100012": FAMILIA_42}
        completo.assert_called_once()


def test_bridge_produtos_familia(monkeypatch):
    from datetime import date  # noqa: F401
    from unittest.mock import MagicMock
    from scripts import erp_lancamento_api_bridge as bridge

    monkeypatch.setattr(bridge, "_config", lambda: {"host": "h", "database": "d", "user": "u"})
    monkeypatch.setattr(bridge, "_authorized", lambda cfg: True)
    conn = MagicMock()
    cursor = conn.__enter__.return_value.cursor.return_value.__enter__.return_value
    cursor.fetchall.return_value = [("190100012", FAMILIA_42)]
    monkeypatch.setattr(bridge, "_conectar", lambda cfg, readonly=False: conn)
    client = bridge.create_app().test_client()
    data = client.post("/api/erp/produtos-familia", json={"codigos": ["19-01-00012", "19-01-00012"]}).get_json()
    assert data == {"sucesso": True, "familias": {"190100012": FAMILIA_42}}
    assert cursor.execute.call_args.args[1] == (1, ["190100012"])
