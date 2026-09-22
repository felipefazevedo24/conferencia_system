"""Chapa no recebimento: o conferente conta as chapas, o peso vem da NF.

Antes o conferente digitava o peso em KG e ainda informava a quantidade em
UND. Agora ele informa só as chapas e as medidas — o peso em KG é lido da
própria nota, do lado do servidor, e segue pelo mesmo caminho de sempre.
"""
import pytest

from test_app import build_test_app, login_admin
from conferencia_app.extensions import db
from conferencia_app.models import (
    ItemNota,
    LogTentativaConferencia,
    RecebimentoEnderecamento as Tarefa,
)

CHAPA = {
    "quantidade": "2",
    "material": "aco_carbono",
    "formato": "chapa",
    "dimensoes": {"espessura": 10, "largura": 1000, "comprimento": 2000},
}


def criar_item(app, *, nota="900A", unidade="KG", qtd_real=314.0, codigo_grv="GRV-1"):
    with app.app_context():
        item = ItemNota(numero_nota=nota, fornecedor="Aços", codigo="CH-1",
                        codigo_grv=codigo_grv, descricao="Chapa aço",
                        qtd_real=qtd_real, unidade_comercial=unidade, status="Pendente")
        db.session.add(item)
        db.session.commit()
        return item.id


def test_peso_vem_da_nf_sem_o_conferente_digitar(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()
    login_admin(client)
    item_id = criar_item(app)

    # A tela nem manda a contagem do item de chapa.
    resposta = client.post("/validar", json={
        "nota": "900A", "contagens": {},
        "chapas_itens": {str(item_id): CHAPA},
    })

    assert resposta.status_code == 200
    corpo = resposta.get_json()
    assert corpo["sucesso"] is True
    assert corpo["itens"][0]["status"] == "OK"

    with app.app_context():
        log = LogTentativaConferencia.query.filter_by(item_id=item_id).one()
        assert log.qtd_digitada == pytest.approx(314.0)
        assert log.qtd_convertida == pytest.approx(314.0)
        assert log.fator_conversao == pytest.approx(1.0)
        assert db.session.get(ItemNota, item_id).qtd_chapas_und == 2


def test_peso_mandado_pela_tela_e_ignorado(tmp_path):
    """Quem decide é o servidor: a nota manda, não o que veio no corpo."""
    app = build_test_app(tmp_path)
    client = app.test_client()
    login_admin(client)
    item_id = criar_item(app)

    resposta = client.post("/validar", json={
        "nota": "900A", "contagens": {str(item_id): "99"},
        "conversoes_itens": {str(item_id): {"fator": "7", "unidade": "PC"}},
        "chapas_itens": {str(item_id): CHAPA},
    })

    assert resposta.status_code == 200
    assert resposta.get_json()["sucesso"] is True
    with app.app_context():
        log = LogTentativaConferencia.query.filter_by(item_id=item_id).one()
        assert log.qtd_digitada == pytest.approx(314.0)
        assert log.qtd_convertida == pytest.approx(314.0)
        assert log.unidade_informada == "KG"


def test_item_ja_marcado_como_chapa_continua_pegando_o_peso_da_nf(tmp_path):
    """O fluxo chama /validar mais de uma vez; a segunda não reenvia a chapa."""
    app = build_test_app(tmp_path)
    client = app.test_client()
    login_admin(client)
    item_id = criar_item(app)

    assert client.post("/validar", json={
        "nota": "900A", "contagens": {},
        "chapas_itens": {str(item_id): CHAPA},
    }).status_code == 200

    segunda = client.post("/validar", json={"nota": "900A", "contagens": {}})
    assert segunda.status_code == 200
    assert segunda.get_json()["itens"][0]["status"] == "OK"
    with app.app_context():
        ultimo = (LogTentativaConferencia.query
                  .filter_by(item_id=item_id)
                  .order_by(LogTentativaConferencia.id.desc()).first())
        assert ultimo.qtd_convertida == pytest.approx(314.0)


def test_enderecamento_recebe_a_quantidade_da_nf(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()
    login_admin(client)
    item_id = criar_item(app)

    resposta = client.post("/validar", json={
        "nota": "900A", "contagens": {},
        "chapas_itens": {str(item_id): CHAPA},
        "forcar_pendencia": True, "motivos_itens": {},
    })

    assert resposta.status_code == 200
    with app.app_context():
        tarefa = Tarefa.query.filter_by(item_nota_id=item_id).one()
        assert tarefa.quantidade == pytest.approx(314.0)
        assert tarefa.unidade == "KG"


def test_item_em_kg_sem_marcar_chapa_continua_exigindo_a_contagem(tmp_path):
    """A mudança vale só para quem o conferente marcou como chapa."""
    app = build_test_app(tmp_path)
    client = app.test_client()
    login_admin(client)
    item_id = criar_item(app, nota="900B")

    resposta = client.post("/validar", json={"nota": "900B", "contagens": {}})
    assert resposta.status_code == 200
    corpo = resposta.get_json()
    assert corpo["sucesso"] is False
    assert corpo["itens"][0]["msg"] == "Quantidade não informada."


def test_item_comum_nao_vira_chapa_por_engano(tmp_path):
    """Unidade que não é peso não entra nesse caminho, mesmo com payload."""
    app = build_test_app(tmp_path)
    client = app.test_client()
    login_admin(client)
    item_id = criar_item(app, nota="900C", unidade="PC", qtd_real=10)

    resposta = client.post("/validar", json={
        "nota": "900C", "contagens": {},
        "chapas_itens": {str(item_id): CHAPA},
    })
    assert resposta.status_code == 200
    assert resposta.get_json()["itens"][0]["msg"] == "Quantidade não informada."
    with app.app_context():
        assert db.session.get(ItemNota, item_id).qtd_chapas_und is None
