"""Trilha da ordem de faturamento no GRV, mostrada na Conferência de Expedição.

O GRV não grava quem criou a OF; a trilha é quem mexeu em volta dela. A
fixture segue o formato real do bridge, com nomes e números fictícios. Bridge
sempre com patch: o teste não depende de rede."""
from datetime import datetime
from unittest.mock import MagicMock, patch

import requests

from conferencia_app.extensions import db
from conferencia_app.models import ExpedicaoOrdemFat
from conferencia_app.services import expedicao_fat_service
from conferencia_app.services import expedicao_grv_trilha_service as svc
from tests.test_app import build_test_app, set_logged_user

TRILHA = {
    "cod_ordem_fat": 500,
    "cliente": "CLIENTE EXEMPLO",
    "numero_nf": "9001",
    "dt_solicitacao_fat": "2026-09-10T00:00:00",
    "estagios": [
        {"orcamento": "700", "estagio": "EM ELABORAÇÃO", "data": "2026-06-25T16:29:42.403000", "usuario": "Vendedor Um"},
        {"orcamento": "700", "estagio": "GANHOU", "data": "2026-07-01T11:11:43.663000", "usuario": "VENDEDOR DOIS"},
    ],
    "movimentos": [
        {"data": "2026-07-01T11:11:42.976000", "tabela": "TSOL_MAT", "chave": "1;300", "usuario": "VEND2", "itens": 1,
         "obs": "INSERINDO ESTOQUE SOLICITADO RESERVADO. ORÇAMENTO: 700"},
        {"data": "2026-09-10T16:37:11.722000", "tabela": "TOS", "chave": "1;4000", "usuario": "PROD1", "itens": 1,
         "obs": "ENTRADA DE PRODUÇÃO. SERVIÇO: 80. DESTINO PARA O ORÇAMENTO 700"},
        {"data": "2026-09-11T12:01:50.081000", "tabela": "TNOTA_FISCAL", "chave": "1;55;1;1;9001", "usuario": "FISCAL1", "itens": 3,
         "obs": "MODELO: 55. SÉRIE: 1. NÚMERO DA NOTA: 9001."},
    ],
}


def _resposta(status=200, corpo=None, texto=""):
    resp = MagicMock(status_code=status, text=texto)
    resp.json.return_value = corpo or {}
    if status >= 400:
        resp.raise_for_status.side_effect = requests.HTTPError(response=resp)
    return resp


def _app(tmp_path, origem_status="em_aberto", cod=500):
    app = build_test_app(tmp_path)
    app.config["ERP_LANCAMENTO_API_URL"] = "https://bridge.teste"
    with app.app_context():
        db.session.add(ExpedicaoOrdemFat(cod_ordem_fat=cod, origem_status=origem_status))
        db.session.commit()
    client = app.test_client()
    set_logged_user(client, "conferente_teste", "Admin")
    return client


def test_eventos_em_ordem_e_solicitacao_de_faturamento_sem_usuario():
    eventos = svc.montar_eventos(TRILHA)
    assert [(e["titulo"], e["usuario"]) for e in eventos] == [
        ("Orçamento 700: Em elaboração", "Vendedor Um"),
        ("Solicitação de material", "VEND2"),
        ("Orçamento 700: Ganhou", "VENDEDOR DOIS"),
        ("Entrada de produção (OS 4000)", "PROD1"),
        # O GRV só tem a data da OF: fica depois do que aconteceu naquele dia.
        ("Solicitação de faturamento", ""),
        ("Nota fiscal 9001 emitida", "FISCAL1"),
    ]
    solicitacao = eventos[4]
    assert solicitacao["sem_hora"] is True and "sem usuário" in solicitacao["detalhe"]
    assert eventos[3]["detalhe"] == "Entrada de produção. serviço: 80. destino para o orçamento 700"
    assert eventos[5]["detalhe"] == "3 itens da ordem"


def test_rota_devolve_a_trilha_do_grv(tmp_path):
    client = _app(tmp_path)
    corpo = {"sucesso": True, "encontrada": True, "trilha": TRILHA}
    with patch.object(svc.requests, "post", return_value=_resposta(corpo=corpo)) as post:
        data = client.get("/api/expedicao/conf-cega/ordens/500/trilha-grv").get_json()
    assert post.call_args.args[0] == "https://bridge.teste/api/erp/expedicao/trilha-ordem-fat"
    assert post.call_args.kwargs["json"] == {"empresa": 1, "cod_ordem_fat": 500}
    assert data["disponivel"] is True and len(data["eventos"]) == 6
    assert client.get("/api/expedicao/conf-cega/ordens/999/trilha-grv").status_code == 404


def test_grv_fora_ou_bridge_antigo_nao_derruba_a_tela(tmp_path):
    client = _app(tmp_path)
    casos = [
        (_resposta(404, texto='{"erro": "not found"}'), "bridge_desatualizado"),  # VM sem o endpoint novo
        (_resposta(404, texto="<html>ERR_NGROK_3200</html>"), "bridge_fora"),      # túnel caído também dá 404
        (_resposta(corpo={"sucesso": False, "erro": "sql"}), "bridge_fora"),
        (_resposta(corpo={"sucesso": True, "encontrada": False, "trilha": None}), "nao_encontrada"),
    ]
    for resposta, motivo in casos:
        with patch.object(svc.requests, "post", return_value=resposta):
            resp = client.get("/api/expedicao/conf-cega/ordens/500/trilha-grv")
        assert resp.status_code == 200
        assert resp.get_json() == {"disponivel": False, "motivo": motivo, "eventos": []}
    with patch.object(svc.requests, "post", side_effect=requests.ConnectionError("recusada")):
        assert client.get("/api/expedicao/conf-cega/ordens/500/trilha-grv").get_json()["motivo"] == "bridge_fora"


def test_nf_lancada_a_mao_nao_consulta_o_grv(tmp_path):
    cod = expedicao_fat_service.COD_ORDEM_NF_MANUAL_BASE + 5
    client = _app(tmp_path, origem_status=expedicao_fat_service.ORIGEM_NF_MANUAL, cod=cod)
    with patch.object(svc.requests, "post") as post:
        data = client.get(f"/api/expedicao/conf-cega/ordens/{cod}/trilha-grv").get_json()
    assert data["motivo"] == "nf_manual"
    post.assert_not_called()


def test_tela_tem_o_cartao_da_trilha(tmp_path):
    client = _app(tmp_path)
    html = client.get("/expedicao/conferencia-cega").get_data(as_text=True)
    assert "card-trilha-grv" in html
    assert "function renderTrilhaGrv" in html
    assert "O GRV não registra quem criou a ordem de faturamento." in html


def test_bridge_monta_a_trilha(monkeypatch):
    from scripts import erp_lancamento_api_bridge as bridge

    monkeypatch.setattr(bridge, "_config", lambda: {"host": "h", "database": "d", "user": "u"})
    monkeypatch.setattr(bridge, "_authorized", lambda cfg: True)
    conn = MagicMock()
    cursor = conn.__enter__.return_value.cursor.return_value.__enter__.return_value
    cursor.fetchone.return_value = (500, "CLIENTE EXEMPLO", 9001, datetime(2026, 9, 10))
    cursor.fetchall.side_effect = [
        [("700", "GANHOU", datetime(2026, 7, 1, 11, 11, 43), "VENDEDOR DOIS")],
        [(datetime(2026, 9, 11, 12, 1, 50), "TNOTA_FISCAL", "1;55;1;1;9001", "FISCAL1", 1, "MODELO: 55.", "RESERVA PARA DISPONIVEL.")],
    ]
    monkeypatch.setattr(bridge, "_conectar", lambda cfg, readonly=False: conn)
    client = bridge.create_app().test_client()
    data = client.post("/api/erp/expedicao/trilha-ordem-fat", json={"cod_ordem_fat": 500}).get_json()
    assert data["encontrada"] is True
    trilha = data["trilha"]
    assert trilha["numero_nf"] == "9001" and trilha["dt_solicitacao_fat"].startswith("2026-09-10")
    assert trilha["estagios"][0]["usuario"] == "VENDEDOR DOIS"
    # A observação da saída/entrada vale mais que a das linhas de reserva.
    assert trilha["movimentos"] == [{"data": "2026-09-11T12:01:50", "tabela": "TNOTA_FISCAL", "chave": "1;55;1;1;9001",
                                     "usuario": "FISCAL1", "itens": 1, "obs": "MODELO: 55."}]
    # A consulta do kardex filtra pela NF da própria ordem.
    assert cursor.execute.call_args.args[1]["nf"] == "9001"

    cursor.fetchone.return_value = None
    assert client.post("/api/erp/expedicao/trilha-ordem-fat", json={"cod_ordem_fat": 5}).get_json() == {
        "sucesso": True, "encontrada": False, "trilha": None,
    }
    assert client.post("/api/erp/expedicao/trilha-ordem-fat", json={}).status_code == 400
