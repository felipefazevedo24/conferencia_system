from unittest.mock import patch

from conferencia_app import create_app
from conferencia_app.extensions import db
from conferencia_app.models import ItemNota, ItemWMS, WMSIntegracaoEvento


def build_test_app(tmp_path):
    db_path = tmp_path / "test_wms.db"
    return create_app(
        {
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{db_path}",
        }
    )


def login_admin(client):
    return client.post("/login", json={"username": "admin", "password": "admin123"})


def test_confirmar_lancamento_enfileira_integracao_wms_e_agrega_sku(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()
    login_admin(client)

    with app.app_context():
        db.session.add(
            ItemNota(
                numero_nota="WMS100",
                fornecedor="Fornecedor WMS",
                codigo="SKU-1",
                descricao="Item A",
                qtd_real=2.0,
                status="Concluído",
                chave_acesso="10001000100010001000100010001000100010001000",
            )
        )
        db.session.add(
            ItemNota(
                numero_nota="WMS100",
                fornecedor="Fornecedor WMS",
                codigo="SKU-1",
                descricao="Item A complemento",
                qtd_real=3.0,
                status="Concluído",
                chave_acesso="10001000100010001000100010001000100010001000",
            )
        )
        db.session.commit()

    with patch("conferencia_app.routes.api_routes.manifestar_destinatario_consyste", return_value=(True, 200, {})):
        response = client.post(
            "/api/confirmar_lancamento",
            json={"nota": "WMS100", "codigo": "ERP-WMS-100", "manifestar_destinatario": True},
        )

    assert response.status_code == 200
    data = response.get_json()
    assert data["sucesso"] is True
    assert data["fila_integracao"]["evento_id"] is not None

    with app.app_context():
        evento = WMSIntegracaoEvento.query.filter_by(referencia="WMS100").first()
        assert evento is not None
        assert evento.status == "Sucesso"

        pendencia = ItemWMS.query.filter_by(numero_nota="WMS100", codigo_item="SKU-1", localizacao_id=None, ativo=True).first()
        assert pendencia is not None
        assert float(pendencia.qtd_atual or 0) == 5.0


def test_wms_governanca_parametros_e_reconciliacao(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()
    login_admin(client)

    with app.app_context():
        db.session.add(
            ItemNota(
                numero_nota="WMS200",
                fornecedor="Fornecedor Recon",
                codigo="SKU-2",
                descricao="Item Recon",
                qtd_real=10.0,
                status="Lançado",
            )
        )
        db.session.add(
            ItemWMS(
                numero_nota="WMS200",
                codigo_item="SKU-2",
                descricao="Item Recon",
                qtd_recebida=8.0,
                qtd_atual=8.0,
                status="Armazenado",
                ativo=True,
            )
        )
        db.session.commit()

    upd = client.post(
        "/api/wms/parametros-operacionais",
        json={"parametros": {"WMS_PENDENCIA_ALERTA_HORAS": "6"}},
    )
    assert upd.status_code == 200
    assert upd.get_json()["sucesso"] is True

    recon = client.post("/api/wms/governanca/reconciliar", json={"numero_nota": "WMS200"})
    assert recon.status_code == 200
    resultado = recon.get_json()["resultado"]
    assert resultado["analisadas"] >= 1

    painel = client.get("/api/wms/governanca")
    assert painel.status_code == 200
    dados = painel.get_json()
    assert "kpis" in dados
    assert "divergencias" in dados
    assert any(d["numero_nota"] == "WMS200" for d in dados["divergencias"])


def test_wms_fila_processamento_endpoint(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()
    login_admin(client)

    with app.app_context():
        db.session.add(
            WMSIntegracaoEvento(
                idempotency_key="nota_lancada:WMS300",
                tipo_evento="NotaLancada",
                referencia="WMS300",
                origem="Fiscal",
                payload_json='{"numero_nota":"WMS300","usuario":"admin"}',
                status="Pendente",
            )
        )
        db.session.commit()

    response = client.post("/api/wms/integracao/processar", json={"limite": 10})
    assert response.status_code == 200
    data = response.get_json()
    assert data["sucesso"] is True
    assert data["resultado"]["processados"] >= 1
