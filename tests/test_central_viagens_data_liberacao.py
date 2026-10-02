from datetime import datetime

from test_app import build_test_app, set_logged_user
from conferencia_app.extensions import db
from conferencia_app.models import AgendamentoSolicitacao
from conferencia_app.services.solicitacao_coleta_service import (
    criar_ou_atualizar_detalhes_coleta,
    obter_detalhes_coleta,
)

BASE = "/api/logistica/central-viagens/solicitacoes"


def _solicitacao(tipo, codigo, status="Pendente"):
    return AgendamentoSolicitacao(
        tipo=tipo, status=status, codigo=codigo, solicitante="admin",
        documento_tipo="OC" if tipo == "COLETA" else "NF", documento_numero=codigo,
        numero_oc=codigo if tipo == "COLETA" else None,
        parceiro_tipo="Fornecedor", parceiro_nome="Fornecedor Teste",
        logradouro="Rua Teste", cidade="Campinas", uf="SP",
        origem_documento="ORDEM_DE_COMPRA" if tipo == "COLETA" else "ROMANEIO",
    )


def _ambiente(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()
    set_logged_user(client, "admin", "Admin")
    with app.app_context():
        linhas = {
            "coleta": _solicitacao("COLETA", "OC-LIB-1"),
            "entrega": _solicitacao("ENTREGA", "NF-LIB-1"),
            "concluida": _solicitacao("COLETA", "OC-LIB-2", status="Concluida"),
        }
        db.session.add_all(linhas.values())
        db.session.commit()
        ids = {nome: row.id for nome, row in linhas.items()}
    return app, client, ids


def test_alterar_data_liberacao_da_coleta(tmp_path):
    app, client, ids = _ambiente(tmp_path)
    url = f"{BASE}/{ids['coleta']}/data-liberacao"

    # Coleta ainda sem detalhes: a alteração cria o registro.
    resposta = client.post(url, json={"data_liberacao": "2030-05-10T14:30"})
    assert resposta.status_code == 200, resposta.get_json()
    assert resposta.get_json()["data_liberacao_label"] == "10/05/2030 14:30"
    with app.app_context():
        assert obter_detalhes_coleta(ids["coleta"])["data_liberacao"] == datetime(2030, 5, 10, 14, 30)

    # A Central passa a mostrar a nova data.
    painel = client.get("/api/logistica/central-viagens/dashboard").get_json()
    coleta = next(c for c in painel["coletas"] if c["id"] == ids["coleta"])
    assert coleta["data_liberacao_label"] == "10/05/2030 14:30"
    assert coleta["aguardando_liberacao"] is True

    # Remover a data libera a coleta de imediato.
    assert client.post(url, json={"data_liberacao": ""}).status_code == 200
    with app.app_context():
        assert obter_detalhes_coleta(ids["coleta"])["data_liberacao"] is None


def test_alterar_data_liberacao_preserva_observacao(tmp_path):
    app, client, ids = _ambiente(tmp_path)
    with app.app_context():
        assert criar_ou_atualizar_detalhes_coleta(
            ids["coleta"], data_liberacao=datetime(2030, 1, 1, 8, 0),
            observacao="Retirar na doca 3", usuario="admin",
        )
    resposta = client.post(f"{BASE}/{ids['coleta']}/data-liberacao", json={"data_liberacao": "2030-02-02T09:15"})
    assert resposta.status_code == 200, resposta.get_json()
    with app.app_context():
        detalhes = obter_detalhes_coleta(ids["coleta"])
        assert detalhes["data_liberacao"] == datetime(2030, 2, 2, 9, 15)
        assert detalhes["observacao"] == "Retirar na doca 3"


def test_alterar_data_liberacao_recusa_casos_invalidos(tmp_path):
    app, client, ids = _ambiente(tmp_path)
    corpo = {"data_liberacao": "2030-05-10T14:30"}
    assert client.post(f"{BASE}/{ids['entrega']}/data-liberacao", json=corpo).status_code == 404
    assert client.post(f"{BASE}/999999/data-liberacao", json=corpo).status_code == 404
    assert client.post(f"{BASE}/{ids['concluida']}/data-liberacao", json=corpo).status_code == 409
    assert client.post(f"{BASE}/{ids['coleta']}/data-liberacao", json={"data_liberacao": "amanhã"}).status_code == 400
    with app.app_context():
        assert obter_detalhes_coleta(ids["coleta"]) is None
