import hashlib
from datetime import datetime, timedelta

from conferencia_app import create_app
from conferencia_app.extensions import db
from conferencia_app.models import RpaExecutor
from conferencia_app.services import rpa_grv_service


TOKEN = "token-de-teste-do-agente"
AGENT_ID = "columbia-grv-hml-01"
PRODUCTION_TOKEN = "token-de-teste-producao"
PRODUCTION_AGENT_ID = "columbia-grv-prod-01"


def build_app(
    tmp_path,
    *,
    environment="homologacao",
    token=TOKEN,
    agent_id=AGENT_ID,
    database_name="agent.db",
):
    return create_app(
        {
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / database_name}",
            "RPA_AGENT_ENABLED": True,
            "RPA_AGENT_ENVIRONMENT": environment,
            "RPA_AGENT_ID": agent_id,
            "RPA_AGENT_TOKEN_HASH": hashlib.sha256(token.encode()).hexdigest(),
            "RPA_AGENT_HEARTBEAT_TIMEOUT_SECONDS": 45,
        }
    )


def agent_headers(token=TOKEN, agent_id=AGENT_ID):
    return {
        "Authorization": f"Bearer {token}",
        "X-RPA-Agent-ID": agent_id,
    }


def login_admin(client):
    response = client.post(
        "/login", json={"username": "admin", "password": "admin1234"}
    )
    assert response.status_code == 200


def heartbeat_payload(grv=True):
    return {
        "hostname": "PC-RPA-01",
        "usuario": "operador",
        "versao": "1.0",
        "desktop_interativo": True,
        "grv_disponivel": grv,
        "janela_titulo": "CPS - COLUMBIA",
        "janela_hwnd": "123",
    }


def test_fluxo_completo_fila_agente_e_retorno_ao_usuario(tmp_path, monkeypatch):
    app = build_app(tmp_path)
    client = app.test_client()
    login_admin(client)
    monkeypatch.setattr(
        rpa_grv_service,
        "montar_agrupamento",
        lambda data, usuario: {
            "ok": True,
            "valid": True,
            "payload": {
                "descricao_agrupamento": data["description"],
                "quantidade_itens": 2,
                "codigos_destacados_para_agrupamento": ["179273", "180842"],
                "cod_os_completo": ["10674/001", "10793/001"],
                "produto": "CHAPA SAE 1,90MM",
                "espessura_extraida": "1.90MM",
                "norma_extraida": "SAE 1006 / 1008",
            },
        },
    )

    heartbeat = client.post(
        "/api/rpa/agent/heartbeat",
        headers=agent_headers(),
        json=heartbeat_payload(),
    )
    assert heartbeat.status_code == 200

    status = client.get("/api/rpa/status")
    assert status.status_code == 200
    assert status.get_json()["executor_online"] is True
    assert status.get_json()["grv_disponivel"] is True
    assert status.get_json()["rpa_disponivel"] is True

    agent_status = client.get(
        "/api/rpa/agent/status",
        headers=agent_headers(),
    )
    assert agent_status.status_code == 200
    assert agent_status.get_json()["executor_online"] is True
    assert agent_status.get_json()["executor"]["id"] == AGENT_ID

    queued = client.post(
        "/api/agrupamentos/executar",
        json={
            "codes": ["179273", "180842"],
            "description": "teste - teste - teste",
            "confirmed": True,
        },
    )
    assert queued.status_code == 202
    execution_id = queued.get_json()["execution_id"]

    claim = client.post(
        "/api/rpa/agent/claim",
        headers=agent_headers(),
        json=heartbeat_payload(),
    )
    assert claim.status_code == 200
    assert claim.get_json()["job"]["execution_id"] == execution_id
    assert claim.get_json()["job"]["payload"]["codigos_destacados_para_agrupamento"] == [
        "179273",
        "180842",
    ]

    running = client.post(
        f"/api/rpa/agent/executions/{execution_id}/running",
        headers=agent_headers(),
        json={},
    )
    assert running.get_json()["status"] == "RUNNING"

    finished = client.post(
        f"/api/rpa/agent/executions/{execution_id}/result",
        headers=agent_headers(),
        json={
            "success": True,
            "result": {
                "gravado": True,
                "comando_gravar_enviado": True,
                "quantidade_codigos": 2,
            },
        },
    )
    assert finished.get_json()["status"] == "SUCCEEDED"

    user_result = client.get(f"/api/agrupamentos/executar/{execution_id}")
    assert user_result.status_code == 200
    assert user_result.get_json()["result"]["gravado"] is True


def test_agente_exige_token_e_estacao_autorizada(tmp_path):
    app = build_app(tmp_path)
    client = app.test_client()
    assert client.post(
        "/api/rpa/agent/heartbeat", headers=agent_headers("invalido"), json={}
    ).status_code == 401
    assert client.post(
        "/api/rpa/agent/heartbeat",
        headers=agent_headers(agent_id="outra-estacao"),
        json={},
    ).status_code == 403


def test_heartbeat_expirado_marca_executor_offline(tmp_path):
    app = build_app(tmp_path)
    client = app.test_client()
    login_admin(client)
    client.post(
        "/api/rpa/agent/heartbeat",
        headers=agent_headers(),
        json=heartbeat_payload(),
    )
    with app.app_context():
        executor = db.session.get(RpaExecutor, AGENT_ID)
        executor.ultima_comunicacao = datetime.now() - timedelta(minutes=2)
        db.session.commit()
    current = client.get("/api/rpa/status").get_json()
    assert current["configurado"] is True
    assert current["executor_online"] is False
    assert current["grv_disponivel"] is False
    assert current["rpa_disponivel"] is False


def test_heartbeat_do_agente_continua_disponivel_em_manutencao(tmp_path, monkeypatch):
    app = build_app(tmp_path)
    client = app.test_client()
    monkeypatch.setattr(
        "conferencia_app.services.maintenance_mode_service.get_maintenance_state",
        lambda: {"enabled": True, "message": "Manutenção programada"},
    )

    heartbeat = client.post(
        "/api/rpa/agent/heartbeat",
        headers=agent_headers(),
        json=heartbeat_payload(),
    )

    assert heartbeat.status_code == 200
    assert heartbeat.get_json()["agent_id"] == AGENT_ID


def test_credenciais_e_filas_sao_isoladas_por_ambiente(tmp_path):
    homologacao = build_app(tmp_path, database_name="homologacao.db")
    producao = build_app(
        tmp_path,
        environment="producao",
        token=PRODUCTION_TOKEN,
        agent_id=PRODUCTION_AGENT_ID,
        database_name="producao.db",
    )

    with homologacao.test_client() as client:
        assert client.post(
            "/api/rpa/agent/heartbeat",
            headers=agent_headers(PRODUCTION_TOKEN, PRODUCTION_AGENT_ID),
            json=heartbeat_payload(),
        ).status_code == 401

    with producao.test_client() as client:
        assert client.post(
            "/api/rpa/agent/heartbeat",
            headers=agent_headers(),
            json=heartbeat_payload(),
        ).status_code == 401
        accepted = client.post(
            "/api/rpa/agent/heartbeat",
            headers=agent_headers(PRODUCTION_TOKEN, PRODUCTION_AGENT_ID),
            json=heartbeat_payload(),
        )
        assert accepted.status_code == 200
        with producao.app_context():
            executor = db.session.get(RpaExecutor, PRODUCTION_AGENT_ID)
            assert executor.ambiente == "producao"
