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
