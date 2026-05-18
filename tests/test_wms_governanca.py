from datetime import datetime, timedelta
from unittest.mock import patch

from conferencia_app import create_app
from conferencia_app.extensions import db
from conferencia_app.models import DepositoWMS, EstoqueWMS, ItemNota, ItemWMS, LocalizacaoArmazem, MovimentacaoWMS, WMSIntegracaoEvento
from conferencia_app.services.erp_sync_service import ERPSyncService


def build_test_app(tmp_path):
    db_path = tmp_path / "test_wms.db"
    return create_app(
        {
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{db_path}",
        }
    )


def login_admin(client):
    return client.post("/login", json={"username": "admin", "password": "admin1234"})


def test_erp_sync_normaliza_linha_postgres_estoque():
    item = ERPSyncService._row_postgres_to_estoque(
        {
            "codigo_interno": " 19-02-00030 ",
            "item": " Mola ",
            "unidade": "PÇ",
            "qtde_total": "52",
            "qtde_reservada": "16",
            "qtde_disponivel": "36",
            "localizacao_estoque": " AL-PB-02-02 ",
            "familia": "Insumos",
            "grupo": 2,
        }
    )

    assert item["codigo_interno"] == "19-02-00030"
    assert item["item"] == "Mola"
    assert item["qtde_total"] == 52.0
    assert item["qtde_reservada"] == 16.0
    assert item["qtde_disponivel"] == 36.0
    assert item["localizacao_estoque"] == "AL-PB-02-02"


def test_erp_sync_consulta_estoque_pela_api_bridge():
    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "sucesso": True,
                "itens": [
                    {
                        "codigo_interno": "19-02-00030",
                        "item": "Mola",
                        "unidade": "PÇ",
                        "qtde_total": 52,
                        "qtde_reservada": 16,
                        "qtde_disponivel": 36,
                        "localizacao_estoque": "AL-PB-02-02",
                    }
                ],
                "codigos_ativos": ["19-02-00030"],
            }

    app = create_app({"TESTING": True, "ERP_ESTOQUE_PG_COMPANY": 1})
    with app.app_context(), patch("conferencia_app.services.erp_sync_service.requests.post", return_value=FakeResponse()) as post:
        itens = ERPSyncService._buscar_estoque_erp_api(
            {"api_url": "https://bridge.local", "api_token": "token", "api_timeout": 30}
        )

    assert itens[0]["codigo_interno"] == "19-02-00030"
    assert itens[0]["qtde_disponivel"] == 36.0
    post.assert_called_once()
    assert post.call_args.args[0] == "https://bridge.local/api/erp/estoque"
    assert post.call_args.kwargs["json"] == {"empresa": 1}
    assert post.call_args.kwargs["headers"]["Authorization"] == "Bearer token"


def test_erp_sync_popula_item_com_endereco_mesmo_sem_saldo():
    app = create_app(
        {
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
        }
    )

    with app.app_context():
        deposito = DepositoWMS.query.filter_by(codigo="AL").first()
        if not deposito:
            db.session.add(DepositoWMS(codigo="AL", nome="Almoxarifado", ativo=True))
            db.session.commit()

        criados = ERPSyncService.popular_estoque_wms(
            [
                {
                    "codigo_interno": "28-15-00002",
                    "localizacao_estoque": "AL-PA-01-01",
                    "qtde_total": 0,
                    "qtde_reservada": 0,
                }
            ]
        )

        estoque = EstoqueWMS.query.filter_by(codigo_item="28-15-00002").first()
        assert criados == 1
        assert estoque is not None
        assert float(estoque.qtd_total or 0) == 0.0
        assert db.session.get(LocalizacaoArmazem, estoque.localizacao_id).codigo == "AL-PA-01-01"


def test_erp_sync_remove_endereco_local_quando_erp_nao_traz_mais_endereco():
    app = create_app(
        {
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
        }
    )

    with app.app_context():
        deposito = DepositoWMS.query.filter_by(codigo="AL").first()
        if not deposito:
            deposito = DepositoWMS(codigo="AL", nome="Almoxarifado", ativo=True)
            db.session.add(deposito)
            db.session.commit()
        loc = LocalizacaoArmazem(
            codigo="J7",
            deposito_id=deposito.id,
            rua="J7",
            predio="01",
            nivel="01",
            corredor="J7",
            prateleira="01",
            posicao="01",
            ativo=True,
        )
        db.session.add(loc)
        db.session.flush()
        db.session.add(EstoqueWMS(codigo_item="19-03-00018", localizacao_id=loc.id, qtd_total=74, qtd_separada=0))
        db.session.commit()

        ERPSyncService._ultimos_codigos_erp_ativos = {"19-03-00018"}
        ERPSyncService.popular_estoque_wms([])

        assert EstoqueWMS.query.filter_by(codigo_item="19-03-00018").first() is None


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


def test_wms_cockpit_operacional_e_pendencia_priorizada(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()
    login_admin(client)

    with app.app_context():
        deposito_al = DepositoWMS.query.filter_by(codigo='AL').first()
        assert deposito_al is not None

        localizacao = LocalizacaoArmazem(
            codigo='AL-01-01-01',
            deposito_id=deposito_al.id,
            rua='01',
            predio='01',
            nivel='01',
            apartamento='',
            corredor='C1',
            prateleira='P1',
            posicao='1',
            capacidade_maxima=100.0,
            capacidade_atual=92.0,
            ativo=True,
        )
        db.session.add(localizacao)
        db.session.add(
            ItemNota(
                numero_nota='WMS500',
                fornecedor='Fornecedor Cockpit',
                codigo='SKU-500',
                descricao='Item critico',
                qtd_real=60.0,
                status='Lançado',
            )
        )
        item = ItemWMS(
            numero_nota='WMS500',
            codigo_item='SKU-500',
            descricao='Item critico',
            qtd_recebida=60.0,
            qtd_atual=60.0,
            status='Pendente Enderecamento',
            deposito_id=deposito_al.id,
            origem_estoque_inicial=True,
            ativo=True,
            data_criacao=datetime.now() - timedelta(hours=30),
        )
        db.session.add(item)
        db.session.flush()
        db.session.add(
            MovimentacaoWMS(
                item_wms_id=item.id,
                numero_nota='WMS500',
                tipo_movimentacao='Recebimento',
                qtd_movimentada=12.0,
                usuario='admin',
                data_movimentacao=datetime.now() - timedelta(hours=1),
            )
        )
        db.session.commit()

    pendentes = client.get('/api/wms/pendentes-enderecamento?nota=WMS500')
    assert pendentes.status_code == 200
    payload = pendentes.get_json()
    assert len(payload) == 1
    assert payload[0]['prioridade_operacional'] == 'Critica'
    assert payload[0]['idade_horas'] >= 29
    assert payload[0]['deposito_codigo'] == 'AL'

    cockpit = client.get('/api/wms/cockpit')
    assert cockpit.status_code == 200
    dados = cockpit.get_json()
    assert dados['cards']['pendentes_enderecamento'] >= 1
    assert dados['cards']['movimentacoes_24h'] >= 1
    assert dados['prioridades']['Critica'] >= 1
    assert any(n['numero_nota'] == 'WMS500' for n in dados['notas_pendentes'])
