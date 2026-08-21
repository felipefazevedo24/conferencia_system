from types import SimpleNamespace

from conferencia_app import create_app
from conferencia_app.extensions import db
from conferencia_app.models import ItemNota

from conferencia_app.services.entrada_chapa_email_service import _anexar_qtd_chapas_und, _eh_entrada_chapa
from scripts.erp_lancamento_api_bridge import _montar_entrada_chapa


def test_entrada_chapa_email_exige_controle_lote_mesmo_com_cfop_alvo():
    app = SimpleNamespace(
        config={
            "ENTRADA_CHAPA_CFOPS": "1901,1915,1924",
            "ENTRADA_CHAPA_CONTROLE_LOTE_VALORES": "1,3",
        }
    )
    entrada = {
        "cfop_cabecalho": "1901",
        "itens": [
            {
                "cfop": "1901",
                "cod_interno": "ABC",
                "descricao": "Material sem controle",
                "controle_lote_serie": 0,
                "tipo_controle": 0,
            }
        ],
    }

    eh_chapa, cfops, itens = _eh_entrada_chapa(entrada, app)

    assert eh_chapa is False
    assert cfops == ["1901"]
    assert itens == []


def test_entrada_chapa_email_mantem_apenas_itens_com_controle_lote():
    app = SimpleNamespace(
        config={
            "ENTRADA_CHAPA_CFOPS": "1901,1915,1924",
            "ENTRADA_CHAPA_CONTROLE_LOTE_VALORES": "1,3",
        }
    )
    entrada = {
        "itens": [
            {"cfop": "1901", "descricao": "Sem lote", "controle_lote_serie": 0, "tipo_controle": 0},
            {"cfop": "5102", "descricao": "Com lote", "controle_lote_serie": 1, "tipo_controle": 0},
            {"cfop": "1924", "descricao": "Com tipo controle", "controle_lote_serie": 0, "tipo_controle": 3},
        ],
    }

    eh_chapa, cfops, itens = _eh_entrada_chapa(entrada, app)

    assert eh_chapa is True
    assert cfops == ["1901", "1924", "5102"]
    assert [item["descricao"] for item in itens] == ["Com lote", "Com tipo controle"]


def test_montar_entrada_chapa_usa_cliente_para_cfop_1924():
    entrada = _montar_entrada_chapa(
        [
            {
                "codigo_lancamento": "123",
                "numero_ar": "AR-123",
                "numero_nota": "456",
                "cfop_cabecalho": "1924",
                "cfop_item": "1924",
                "parceiro_nome": "Fornecedor Industrial",
                "parceiro_documento": "00.000.000/0001-00",
                "cliente_nome": "Cliente Triangulacao SA",
                "cod_interno": "CH-1",
                "descricao": "Chapa triangular",
                "quantidade": 2,
                "controle_lote_serie": 1,
            }
        ]
    )

    assert entrada["parceiro_nome"] == "Cliente Triangulacao SA"
    assert entrada["itens"][0]["cfop"] == "1924"


def test_anexar_qtd_chapas_und_casa_por_codigo_grv_ou_descricao():
    app = create_app()
    app.config.update(
        SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
        TESTING=True,
    )

    with app.app_context():
        db.drop_all()
        db.create_all()
        db.session.add(
            ItemNota(
                numero_nota="999",
                codigo="COD-XML",
                codigo_grv="CHAPA-001",
                descricao="CHAPA ASTM A36 6,35MM",
                status="Concluído",
                qtd_chapas_und=12,
            )
        )
        db.session.commit()

        itens = [
            {"cod_interno": "CHAPA-001", "descricao": "Descricao divergente"},
            {"cod_interno": "", "descricao": "CHAPA ASTM A36 6,35MM"},
        ]

        _anexar_qtd_chapas_und("999", itens)

        assert itens[0]["qtd_chapas_und"] == 12
        assert itens[1]["qtd_chapas_und"] == 12
