"""Planejamento > "Minhas tarefas": cada usuario tem um Kanban privado, com
os proprios status (colunas). O board da equipe continua como era, e
nenhum id de um board vale em outro."""
from tests.test_app import build_test_app, login_admin, set_logged_user

PERM = "PAGE_MINHAS_TAREFAS"
API = "/api/minhas-tarefas"


def _liberar(app, *usernames):
    from conferencia_app.extensions import db
    from conferencia_app.models import PermissaoAcesso

    with app.app_context():
        for username in usernames:
            db.session.add(PermissaoAcesso(scope_type="USER", scope_id=username, permission_key=PERM, allow=True))
        db.session.commit()


def _cliente(app, username, role="Logística"):
    client = app.test_client()
    set_logged_user(client, username, role)
    return client


def _board(client):
    resp = client.get(f"{API}/board")
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()["board"]


def test_minhas_tarefas_exige_a_permissao_nova(tmp_path):
    app = build_test_app(tmp_path)
    sem_permissao = _cliente(app, "joao")
    assert sem_permissao.get(f"{API}/board").status_code == 403
    assert sem_permissao.get("/minhas-tarefas").status_code == 403

    _liberar(app, "joao")
    assert sem_permissao.get("/minhas-tarefas").status_code == 200
    assert "Minhas tarefas" in sem_permissao.get("/minhas-tarefas").get_data(as_text=True)
    # A permissao pessoal nao abre o board da equipe (continua so' Admin).
    assert sem_permissao.get("/api/planejamento/board").status_code == 403
    assert sem_permissao.get("/planejamento").status_code == 403


def test_cada_usuario_tem_board_proprio_com_status_proprios(tmp_path):
    app = build_test_app(tmp_path)
    _liberar(app, "joao", "maria")
    joao, maria = _cliente(app, "joao"), _cliente(app, "maria")

    b_joao = _board(joao)
    assert [c["titulo"] for c in b_joao["columns"]] == ["A fazer", "Em andamento", "Concluido"]
    assert b_joao["responsaveis_admin"] == ["joao"]
    # Segundo acesso reaproveita o mesmo board.
    assert _board(joao)["id"] == b_joao["id"]

    # Joao cria um status proprio e uma tarefa.
    novo = joao.post(f"{API}/columns", json={"titulo": "Aguardando terceiros", "color": "#64748b"})
    assert novo.status_code == 200
    col_id = b_joao["columns"][0]["id"]
    card = joao.post(f"{API}/cards", json={"titulo": "Revisar contrato", "column_id": col_id, "prazo": "2026-10-01"})
    assert card.status_code == 200
    card_id = card.get_json()["card"]["id"]

    b_maria = _board(maria)
    assert b_maria["id"] != b_joao["id"]
    assert "Aguardando terceiros" not in [c["titulo"] for c in b_maria["columns"]]
    assert sum(len(c["cards"]) for c in b_maria["columns"]) == 0

    # Mover para "Concluido" marca como concluida.
    concluido = next(c for c in b_joao["columns"] if c["is_done"])
    assert joao.post(f"{API}/cards/{card_id}/move", json={"to_column_id": concluido["id"], "position": 0}).status_code == 200
    kpis = _board(joao)["kpis"]
    assert kpis["done_cards"] == 1 and kpis["open_cards"] == 0


def test_ids_de_um_board_nao_valem_em_outro(tmp_path):
    app = build_test_app(tmp_path)
    _liberar(app, "joao", "maria")
    joao, maria = _cliente(app, "joao"), _cliente(app, "maria")
    admin = app.test_client()
    login_admin(admin)

    b_joao = _board(joao)
    col_joao = b_joao["columns"][0]["id"]
    label_joao = b_joao["labels"][0]["id"]
    card_id = joao.post(f"{API}/cards", json={"titulo": "Privada", "column_id": col_joao}).get_json()["card"]["id"]
    item_id = joao.post(f"{API}/cards/{card_id}/checklist", json={"texto": "passo 1"}).get_json()["item"]["id"]

    col_maria = _board(maria)["columns"][0]["id"]
    tentativas = [
        maria.patch(f"{API}/cards/{card_id}", json={"titulo": "invadido"}),
        maria.delete(f"{API}/cards/{card_id}"),
        maria.post(f"{API}/cards/{card_id}/comments", json={"texto": "oi"}),
        maria.post(f"{API}/cards/{card_id}/checklist", json={"texto": "x"}),
        maria.patch(f"{API}/checklist/{item_id}", json={"is_done": True}),
        maria.delete(f"{API}/checklist/{item_id}"),
        maria.patch(f"{API}/columns/{col_joao}", json={"titulo": "x"}),
        maria.delete(f"{API}/columns/{col_joao}"),
        maria.delete(f"{API}/labels/{label_joao}"),
        maria.post(f"{API}/cards", json={"titulo": "x", "column_id": col_joao}),
        # Puxar o card do Joao pra coluna dela.
        maria.post(f"{API}/cards/{card_id}/move", json={"to_column_id": col_maria}),
        # Nem o Admin, pelo board da equipe, mexe no card privado.
        admin.patch(f"/api/planejamento/cards/{card_id}", json={"titulo": "invadido"}),
        admin.delete(f"/api/planejamento/cards/{card_id}"),
    ]
    assert [r.status_code for r in tentativas] == [404] * len(tentativas)

    # Joao tambem nao manda o proprio card pra coluna de outro board nem
    # vincula etiqueta de outro board.
    assert joao.post(f"{API}/cards/{card_id}/move", json={"to_column_id": col_maria}).status_code == 404
    label_maria = _board(maria)["labels"][0]["id"]
    resp = joao.patch(f"{API}/cards/{card_id}", json={"label_ids": [label_maria, label_joao]})
    assert resp.get_json()["card"]["label_ids"] == [label_joao]

    card = next(c for col in _board(joao)["columns"] for c in col["cards"] if c["id"] == card_id)
    assert card["titulo"] == "Privada"
    assert card["checklist"][0]["is_done"] is False


def test_board_da_equipe_continua_igual_e_contador_da_home_ignora_os_pessoais(tmp_path):
    from conferencia_app.models import PlannerBoard

    app = build_test_app(tmp_path)
    _liberar(app, "joao")
    joao = _cliente(app, "joao")
    admin = app.test_client()
    login_admin(admin)

    equipe = admin.get("/api/planejamento/board").get_json()["board"]
    assert equipe["nome"] == "Planejamento Sync"
    assert [c["titulo"] for c in equipe["columns"]] == ["Backlog", "Em andamento", "Revisao", "Concluido"]
    assert admin.post("/api/planejamento/cards", json={"titulo": "Da equipe", "column_id": equipe["columns"][0]["id"]}).status_code == 200

    b_joao = _board(joao)
    for i in range(3):
        joao.post(f"{API}/cards", json={"titulo": f"minha {i}", "column_id": b_joao["columns"][0]["id"]})

    # Board da equipe nao enxerga os cards pessoais.
    equipe = admin.get("/api/planejamento/board").get_json()["board"]
    assert equipe["kpis"]["total_cards"] == 1
    with app.app_context():
        assert PlannerBoard.query.count() == 2

    # Contador "tarefas abertas" da home: so' o board da equipe.
    from conferencia_app.routes import page_routes

    with app.test_request_context():
        assert page_routes._build_home_metrics()["planner_abertas"] == 1
