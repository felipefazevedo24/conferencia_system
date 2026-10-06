"""Central de Viagens > Veículos e motoristas (MANAGE_LOGISTICA_VIAGEM_CADASTROS).

Cadastro e gestão de veículos/motoristas e o link do app do motorista, com
permissão própria (Admin ganha automático)."""
import pytest

from test_app import build_test_app, login_admin, set_logged_user
from conferencia_app.extensions import db
from conferencia_app.models import AgendamentoMotorista, AgendamentoVeiculo, PermissaoAcesso

PERM = "MANAGE_LOGISTICA_VIAGEM_CADASTROS"
USUARIO = "gestor_frota"


def _cliente(tmp_path, permitido):
    app = build_test_app(tmp_path)
    client = app.test_client()
    set_logged_user(client, USUARIO, "Portaria")
    with app.app_context():
        for chave in ["PAGE_LOGISTICA_VIAGEM"] + ([PERM] if permitido else []):
            db.session.add(PermissaoAcesso(scope_type="USER", scope_id=USUARIO, permission_key=chave, allow=True))
        db.session.commit()
    return app, client


@pytest.mark.parametrize("permitido", [False, True])
def test_cadastros_exigem_permissao_propria(tmp_path, permitido):
    app, client = _cliente(tmp_path, permitido)
    esperado = 200 if permitido else 403
    assert client.get("/api/viagem/cadastros").status_code == esperado
    assert client.post("/api/viagem/cadastros/veiculos", json={"nome": "Fiorino"}).status_code == esperado
    assert client.post("/api/viagem/cadastros/motoristas", json={"nome": "João"}).status_code == esperado
    html = client.get("/logistica/viagens").get_data(as_text=True)
    assert ('id="btnCadastrosFrota"' in html) is permitido


def test_cria_edita_e_desativa_veiculo(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()
    login_admin(client)

    r = client.post("/api/viagem/cadastros/veiculos", json={"nome": "Fiorino Branca", "placa": "abc-1d23", "cor": "green", "duracao_padrao_min": 90})
    assert r.status_code == 200, r.get_json()
    v = r.get_json()["veiculo"]
    assert (v["codigo"], v["placa"], v["cor"], v["duracao_padrao_min"], v["ativo"]) == ("FIORINO_BRANCA", "ABC1D23", "green", 90, True)

    # Placa repetida e placa inválida são recusadas.
    assert client.post("/api/viagem/cadastros/veiculos", json={"nome": "Outro", "placa": "ABC1D23"}).status_code == 400
    assert client.post("/api/viagem/cadastros/veiculos", json={"nome": "Outro", "placa": "12345"}).status_code == 400
    assert client.post("/api/viagem/cadastros/veiculos", json={"nome": ""}).status_code == 400

    # Edição não muda o código (chave estável); desativar mantém o registro.
    r = client.post("/api/viagem/cadastros/veiculos", json={"id": v["id"], "nome": "Fiorino", "placa": "ABC1D23", "cor": "green", "ativo": False})
    assert r.get_json()["veiculo"]["codigo"] == "FIORINO_BRANCA" and r.get_json()["veiculo"]["ativo"] is False
    lista = client.get("/api/viagem/cadastros").get_json()["veiculos"]
    assert any(x["id"] == v["id"] and not x["ativo"] for x in lista)


def test_motorista_novo_tem_link_do_app(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()
    login_admin(client)
    r = client.post("/api/viagem/cadastros/motoristas", json={"nome": "Carlos Silva", "telefone": "(11) 91234-5678"})
    assert r.status_code == 200, r.get_json()
    mid = r.get_json()["motorista"]["id"]

    link = client.get(f"/api/viagem/motorista/{mid}/painel-link").get_json()
    assert link["sucesso"] and f"/motorista/painel/{mid}/" in link["url"]
    assert link["whatsapp"].startswith("https://wa.me/5511912345678")
    # O link abre o painel público do motorista.
    assert client.get(link["path"]).status_code == 200

    assert client.post("/api/viagem/cadastros/motoristas", json={"id": 99999, "nome": "X"}).status_code == 404


def test_bootstrap_nao_regrava_veiculo_padrao(tmp_path):
    app = build_test_app(tmp_path)
    with app.app_context():
        iveco = AgendamentoVeiculo.query.filter_by(codigo="IVECO").first()
        assert iveco is not None
        iveco.placa, iveco.ativo = "ABC1D23", False
        db.session.commit()
        from conferencia_app.bootstrap import initialize_database
        initialize_database(app)
        iveco = AgendamentoVeiculo.query.filter_by(codigo="IVECO").first()
        assert (iveco.placa, iveco.ativo) == ("ABC1D23", False)
