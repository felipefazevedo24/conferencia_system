"""Coleta por OC: marcação "Correios" e bloqueio de alocação antes da data de liberação."""
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest
from sqlalchemy import text

from test_app import build_test_app, set_logged_user
from conferencia_app.extensions import db
from conferencia_app.models import AgendamentoMotorista, AgendamentoSolicitacao, AgendamentoVeiculo, Viagem
from conferencia_app.tempo import agora_br

CONSULTA_OC = {
    "encontrada": True,
    "fornecedor": {"codigo": "F1", "nome": "Fornecedor Teste", "logradouro": "Rua A", "cidade": "Campinas", "uf": "SP"},
    "itens": [{"descricao": "Item", "quantidade": 1, "unidade": "UN"}],
    "fonte": {"label": "teste"},
}
ROTA_FAKE = {"origem_latitude": None, "origem_longitude": None, "destino_latitude": None,
             "destino_longitude": None, "km_estimado": None, "km_estimado_retorno": None}


@pytest.fixture
def ambiente(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()
    set_logged_user(client, "admin", "Admin")
    with app.app_context():
        veiculo = AgendamentoVeiculo(codigo="TEST-COL", nome_exibicao="Veiculo Teste", ativo=True)
        motorista = AgendamentoMotorista(nome="Motorista Teste")
        db.session.add_all([veiculo, motorista])
        db.session.commit()
        ids = veiculo.id, motorista.id
    return app, client, ids


def criar_coleta(client, numero_oc, liberacao, correios):
    dados = {"numero_oc": numero_oc, "data_liberacao": liberacao.strftime("%Y-%m-%dT%H:%M")}
    if correios:
        dados["correios"] = "1"
    with patch("conferencia_app.routes.agendamento_routes.consultar_oc_agendamento", return_value=CONSULTA_OC):
        resp = client.post("/api/logistica/central-viagens/oc/criar", data=dados)
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()["solicitacao"]["id"]


def alocar(client, sid, veiculo_id, motorista_id):
    saida = agora_br() + timedelta(days=1)
    with patch("conferencia_app.routes.agendamento_routes.estimar_rota_agendamento", return_value=ROTA_FAKE):
        return client.post(f"/api/logistica/agendamento-veiculos/solicitacoes/{sid}/alocar", json={
            "veiculo_id": veiculo_id, "motorista_id": motorista_id, "departamento_solicitante": "COMPRAS",
            "data_hora_saida_prevista": saida.strftime("%Y-%m-%dT%H:%M"),
            "data_hora_retorno_prevista": (saida + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M"),
        })


def test_coleta_correios_aparece_marcada_no_painel(ambiente):
    app, client, _ = ambiente
    sid_correios = criar_coleta(client, "900001", agora_br() - timedelta(hours=1), correios=True)
    sid_normal = criar_coleta(client, "900002", agora_br() - timedelta(hours=1), correios=False)
    coletas = {c["id"]: c for c in client.get("/api/logistica/central-viagens/dashboard").get_json()["coletas"]}
    assert coletas[sid_correios]["correios"] is True
    assert coletas[sid_normal]["correios"] is False
    assert coletas[sid_correios]["aguardando_liberacao"] is False


def test_coleta_nao_liberada_bloqueia_todos_os_caminhos_de_alocacao(ambiente):
    app, client, (veiculo_id, motorista_id) = ambiente
    liberacao = agora_br() + timedelta(days=2)
    sid = criar_coleta(client, "900003", liberacao, correios=False)

    card = client.get("/api/logistica/central-viagens/dashboard").get_json()["coletas"][0]
    assert card["aguardando_liberacao"] is True

    resp = alocar(client, sid, veiculo_id, motorista_id)
    assert resp.status_code == 409
    assert liberacao.strftime("%d/%m/%Y %H:%M") in resp.get_json()["error"]

    resp = client.post("/api/logistica/agendamento-veiculos/solicitacoes/alocar-lote", json={
        "ids": [sid], "veiculo_id": veiculo_id, "motorista_id": motorista_id, "departamento_solicitante": "COMPRAS",
        "data_hora_saida_prevista": (agora_br() + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M"),
    })
    assert resp.status_code == 409

    # Mesmo que já esteja com veículo/motorista (ex.: dado legado), não entra em viagem.
    with app.app_context():
        sol = db.session.get(AgendamentoSolicitacao, sid)
        sol.veiculo_id, sol.motorista_id, sol.status = veiculo_id, motorista_id, "Alocada"
        viagem = Viagem(codigo="TEST-V", veiculo_id=veiculo_id, motorista_id=motorista_id, status="Planejada",
                        saida_prevista=datetime.now() + timedelta(days=1))
        db.session.add(viagem)
        db.session.commit()
        vid = viagem.id
    for resp in (
        client.post(f"/api/viagem/nova-de-solicitacao/{sid}"),
        client.post(f"/api/viagem/{vid}/anexar-solicitacao/{sid}"),
        client.post("/api/viagem/montar-com-solicitacoes", json={"ids": [sid], "modo": "nova"}),
        client.post("/api/viagem/assistente/criar", json={"solicitacao_ids": [sid], "veiculo_id": veiculo_id,
                                                          "motorista_id": motorista_id}),
    ):
        assert resp.status_code == 409, resp.get_json()
        assert "só está liberada a partir de" in resp.get_json()["msg"]


def test_coleta_pode_ser_alocada_depois_da_data_de_liberacao(ambiente):
    app, client, (veiculo_id, motorista_id) = ambiente
    sid = criar_coleta(client, "900004", agora_br() + timedelta(days=2), correios=False)
    assert alocar(client, sid, veiculo_id, motorista_id).status_code == 409
    with app.app_context():
        db.session.execute(text("UPDATE solicitacao_coleta_detalhes SET data_liberacao = :d WHERE solicitacao_id = :s"),
                           {"d": agora_br() - timedelta(minutes=5), "s": sid})
        db.session.commit()
    resp = alocar(client, sid, veiculo_id, motorista_id)
    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()["solicitacao"]["aguardando_liberacao"] is False
