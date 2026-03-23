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


def test_cadastro_estoque_inicial_por_codigo_com_endereco_existente_funciona(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()
    login_admin(client)

    deps = client.get('/api/wms/depositos')
    assert deps.status_code == 200
    deposito_al = next((d for d in deps.get_json() if d.get('codigo') == 'AL'), None)
    assert deposito_al is not None

    cria_loc = client.post(
        '/api/wms/localizacoes',
        json={
            'deposito_id': deposito_al['id'],
            'rua': 'PA',
            'predio': '09',
            'nivel': '02',
            'apartamento': 'A1',
        },
    )
    assert cria_loc.status_code == 201

    response = client.post(
        "/api/wms/estoque-inicial",
        json={
            "codigo_item": "LEG-001",
            "descricao": "Material legado",
            "qtd": 12.5,
            "unidade": "UN",
            "numero_nota": "ESTOQUE_INICIAL",
            "deposito_id": deposito_al['id'],
            "rua": "PA",
            "predio": "09",
            "nivel": "02",
            "apartamento": "A1",
        },
    )

    assert response.status_code == 201
    payload = response.get_json()
    assert payload["sucesso"] is True

    with app.app_context():
        item = ItemWMS.query.filter_by(codigo_item="LEG-001", ativo=True).first()
        assert item is not None
        assert item.localizacao_id is not None
        assert item.status == "Armazenado"
        assert bool(item.origem_estoque_inicial) is True


def test_excluir_localizacao_sem_estoque_vinculado_funciona(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()
    login_admin(client)

    cria = client.post(
        "/api/wms/localizacoes",
        json={
            "rua": "RLEG",
            "predio": "P1",
            "nivel": "N1",
            "apartamento": "A1",
        },
    )
    assert cria.status_code == 201
    localizacao_id = cria.get_json()["id"]

    exclui = client.delete(f"/api/wms/localizacoes/{localizacao_id}")
    assert exclui.status_code == 200
    assert exclui.get_json()["sucesso"] is True


def test_recriar_localizacao_excluida_reativa_registro(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()
    login_admin(client)

    cria = client.post(
        "/api/wms/localizacoes",
        json={
            "rua": "PA",
            "predio": "01",
            "nivel": "00",
            "apartamento": "",
        },
    )
    assert cria.status_code == 201
    id_original = cria.get_json()["id"]

    exclui = client.delete(f"/api/wms/localizacoes/{id_original}")
    assert exclui.status_code == 200

    recria = client.post(
        "/api/wms/localizacoes",
        json={
            "rua": "PA",
            "predio": "01",
            "nivel": "00",
            "apartamento": "",
        },
    )
    assert recria.status_code == 201
    payload = recria.get_json()
    assert payload["id"] == id_original


def test_transferencia_com_deposito_e_endereco_destino(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()
    login_admin(client)

    deps = client.get('/api/wms/depositos')
    assert deps.status_code == 200
    lista_dep = deps.get_json()
    dep_al = next((d for d in lista_dep if d.get('codigo') == 'AL'), None)
    dep_ch = next((d for d in lista_dep if d.get('codigo') == 'CH'), None)
    assert dep_al is not None and dep_ch is not None

    loc_origem = client.post(
        '/api/wms/localizacoes',
        json={'deposito_id': dep_al['id'], 'rua': 'AL', 'predio': '01', 'nivel': '01', 'apartamento': 'A1'},
    )
    assert loc_origem.status_code == 201

    loc_destino = client.post(
        '/api/wms/localizacoes',
        json={'deposito_id': dep_ch['id'], 'rua': 'CH', 'predio': '02', 'nivel': '01', 'apartamento': 'A1'},
    )
    assert loc_destino.status_code == 201

    cad_item = client.post(
        '/api/wms/estoque-inicial',
        json={
            'codigo_item': 'LEG-TRF-1',
            'descricao': 'Item para transferencia',
            'qtd': 3,
            'unidade': 'UN',
            'numero_nota': 'ESTOQUE_INICIAL',
            'deposito_id': dep_al['id'],
            'rua': 'AL',
            'predio': '01',
            'nivel': '01',
            'apartamento': 'A1',
        },
    )
    assert cad_item.status_code == 201

    with app.app_context():
        item = ItemWMS.query.filter_by(codigo_item='LEG-TRF-1', ativo=True).first()
        assert item is not None
        item_id = item.id

    transf = client.post(
        '/api/wms/transferir-deposito',
        json={
            'item_wms_id': item_id,
            'deposito_destino_id': dep_ch['id'],
            'localizacao_destino_id': loc_destino.get_json()['id'],
            'motivo': 'Teste de transferencia completa',
        },
    )
    assert transf.status_code == 200
    assert transf.get_json()['sucesso'] is True

    with app.app_context():
        atualizado = ItemWMS.query.get(item_id)
        assert atualizado is not None
        assert atualizado.deposito_id == dep_ch['id']
        assert atualizado.localizacao_id == loc_destino.get_json()['id']
        assert atualizado.status == 'Armazenado'
