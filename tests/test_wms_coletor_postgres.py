from unittest.mock import patch

from conferencia_app import create_app
from conferencia_app.extensions import db
from conferencia_app.models import DepositoWMS, EstoqueWMS, ItemNota, ItemWMS, LocalizacaoArmazem


def build_test_app(tmp_path):
    db_path = tmp_path / "test_wms_coletor.db"
    return create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": f"sqlite:///{db_path}"})


def login_admin(client):
    return client.post("/login", json={"username": "admin", "password": "admin1234"})


def test_wms_coletor_bipa_sku_lancado_no_postgres_e_endereca_quantidade_parcial(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()
    login_admin(client)

    with app.app_context():
        deposito = DepositoWMS.query.filter_by(codigo="AL").first()
        if not deposito:
            deposito = DepositoWMS(codigo="AL", nome="Almoxarifado", ativo=True)
            db.session.add(deposito)
            db.session.flush()
        localizacao = LocalizacaoArmazem(
            codigo="AL-PB-01-01",
            deposito_id=deposito.id,
            rua="PB",
            predio="01",
            nivel="01",
            apartamento="",
            corredor="PB",
            prateleira="01",
            posicao="01",
            capacidade_maxima=100,
            capacidade_atual=0,
            ativo=True,
        )
        db.session.add_all(
            [
                localizacao,
                ItemNota(
                    numero_nota="WMSPG1",
                    fornecedor="Fornecedor PG",
                    codigo="SKU-PG",
                    descricao="Descricao local",
                    qtd_real=999,
                    status="Lançado",
                    numero_lancamento="GRV-PG-1",
                    chave_acesso="2" * 44,
                ),
            ]
        )
        db.session.commit()
        localizacao_id = localizacao.id

    entrada_pg = {
        "encontrada": True,
        "entrada": {
            "numero_nota": "WMSPG1",
            "codigo_lancamento": "GRV-PG-1",
            "chave_acesso": "2" * 44,
            "parceiro_nome": "Fornecedor Postgres",
        },
        "linhas": [
            {"codigo_item": "SKU-PG", "descricao": "Descricao Postgres", "qtd": 10, "unidade": "UN"},
        ],
        "fonte": "ERPPostgres",
    }
    with patch(
        "conferencia_app.services.wms_service.WMSService.consultar_entrada_erp_para_wms",
        return_value=entrada_pg,
    ):
        bip_sku = client.post("/api/wms/coletor/bipar", json={"codigo": "SKU-PG"})

    assert bip_sku.status_code == 200
    data_sku = bip_sku.get_json()
    assert data_sku["tipo"] == "SKU"
    assert len(data_sku["pendentes"]) == 1
    item_id = data_sku["pendentes"][0]["id"]
    assert data_sku["pendentes"][0]["descricao"] == "Descricao Postgres"
    assert data_sku["pendentes"][0]["qtd_atual"] == 10

    confirma = client.post(
        "/api/wms/coletor/enderecar",
        json={"item_wms_id": item_id, "localizacao_id": localizacao_id, "qtd": 4},
    )
    assert confirma.status_code == 200
    data = confirma.get_json()
    assert data["sucesso"] is True
    assert data["item"]["qtd_atual"] == 4
    assert data["item"]["localizacao_codigo"] == "AL-PB-01-01"

    with app.app_context():
        pendencia = ItemWMS.query.filter_by(codigo_item="SKU-PG", localizacao_id=None, ativo=True).one()
        armazenado = (
            ItemWMS.query.filter(
                ItemWMS.codigo_item == "SKU-PG",
                ItemWMS.localizacao_id.isnot(None),
                ItemWMS.ativo == True,
            ).one()
        )
        estoque = EstoqueWMS.query.filter_by(codigo_item="SKU-PG", localizacao_id=localizacao_id).one()
        assert float(pendencia.qtd_atual or 0) == 6
        assert float(armazenado.qtd_atual or 0) == 4
        assert float(estoque.qtd_total or 0) == 4


def test_wms_coletor_nao_retorna_pendencia_local_por_sku_sem_postgres(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()
    login_admin(client)

    with app.app_context():
        deposito = DepositoWMS.query.filter_by(codigo="AL").first()
        if not deposito:
            deposito = DepositoWMS(codigo="AL", nome="Almoxarifado", ativo=True)
            db.session.add(deposito)
            db.session.flush()
        db.session.add_all(
            [
                ItemNota(
                    numero_nota="LOCALSKU1",
                    fornecedor="Fornecedor local",
                    codigo="SKU-LOCAL",
                    descricao="Item local",
                    qtd_real=5,
                    status="Lancado",
                    numero_lancamento="GRV-LOCAL-1",
                    chave_acesso="3" * 44,
                ),
                ItemWMS(
                    numero_nota="LOCALSKU1",
                    codigo_item="SKU-LOCAL",
                    descricao="Pendencia local",
                    qtd_recebida=5,
                    qtd_atual=5,
                    unidade="UN",
                    status="Pendente Enderecamento",
                    deposito_id=deposito.id,
                    ativo=True,
                ),
            ]
        )
        db.session.commit()

    consulta_local = {
        "encontrada": True,
        "entrada": {"numero_nota": "LOCALSKU1"},
        "linhas": [{"codigo_item": "SKU-LOCAL", "descricao": "Item local", "qtd": 5, "unidade": "UN"}],
        "fonte": "ItemNotaLocal",
    }
    with patch(
        "conferencia_app.services.wms_service.WMSService.consultar_entrada_erp_para_wms",
        return_value=consulta_local,
    ):
        resposta = client.post("/api/wms/coletor/bipar", json={"codigo": "SKU-LOCAL"})

    assert resposta.status_code == 404
    assert resposta.get_json()["tipo"] == "DESCONHECIDO"


def test_wms_coletor_nao_retorna_nota_local_sem_postgres(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()
    login_admin(client)

    with app.app_context():
        deposito = DepositoWMS.query.filter_by(codigo="AL").first()
        if not deposito:
            deposito = DepositoWMS(codigo="AL", nome="Almoxarifado", ativo=True)
            db.session.add(deposito)
            db.session.flush()
        db.session.add(
            ItemWMS(
                numero_nota="LOCALNF1",
                codigo_item="SKU-NF-LOCAL",
                descricao="Pendencia local por nota",
                qtd_recebida=7,
                qtd_atual=7,
                unidade="UN",
                status="Pendente Enderecamento",
                deposito_id=deposito.id,
                ativo=True,
            )
        )
        db.session.commit()

    with patch(
        "conferencia_app.services.wms_service.WMSService.consultar_entrada_erp_para_wms",
        return_value={"encontrada": False, "entrada": None, "linhas": [], "fonte": None},
    ):
        resposta = client.post("/api/wms/coletor/bipar", json={"codigo": "NF:LOCALNF1"})

    assert resposta.status_code == 404
    assert resposta.get_json()["tipo"] == "DESCONHECIDO"


def test_wms_coletor_endereca_status_acentuado_e_endereco_sem_capacidade_configurada(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()
    login_admin(client)

    with app.app_context():
        dep_origem = DepositoWMS.query.filter_by(codigo="OR").first()
        if not dep_origem:
            dep_origem = DepositoWMS(codigo="OR", nome="Origem", ativo=True)
            db.session.add(dep_origem)
        dep_destino = DepositoWMS.query.filter_by(codigo="AL").first()
        if not dep_destino:
            dep_destino = DepositoWMS(codigo="AL", nome="Almoxarifado", ativo=True)
            db.session.add(dep_destino)
        db.session.flush()
        localizacao = LocalizacaoArmazem(
            codigo="AL-ZZ-01-01",
            deposito_id=dep_destino.id,
            rua="ZZ",
            predio="01",
            nivel="01",
            apartamento="",
            corredor="ZZ",
            prateleira="01",
            posicao="01",
            capacidade_maxima=0,
            capacidade_atual=0,
            ativo=True,
        )
        item = ItemWMS(
            numero_nota="WMSACC1",
            codigo_item="SKU-ACC",
            descricao="Item com status acentuado",
            qtd_recebida=3,
            qtd_atual=3,
            unidade="UN",
            status="Pendente Endereçamento",
            deposito_id=dep_origem.id,
            ativo=True,
        )
        db.session.add_all([localizacao, item])
        db.session.commit()
        item_id = item.id

    confirma = client.post(
        "/api/wms/coletor/enderecar",
        json={"item_wms_id": item_id, "localizacao_codigo": "AL-ZZ-01-01", "qtd": 3},
    )

    assert confirma.status_code == 200
    data = confirma.get_json()
    assert data["sucesso"] is True
    assert data["item"]["localizacao_codigo"] == "AL-ZZ-01-01"
