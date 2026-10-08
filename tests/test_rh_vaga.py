"""RH - Requisicao de Vaga: abertura pelo gestor, aprovacoes (Diretoria ->
Financeiro -> Gerencia de RH), publicacao com link/QR code e candidatura
pela pagina publica. Cobre tambem os cargos criados na Gestao de Acessos."""
from io import BytesIO
from unittest.mock import patch

import pytest

from conferencia_app.extensions import db
from conferencia_app.models import (
    ActiveSession,
    PermissaoAcesso,
    RhCandidato,
    RhCandidatoAcesso,
    RhRequisicaoVaga,
    Usuario,
)
from tests.test_app import build_test_app, login_admin, set_logged_user

PDF = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n%%EOF"


@pytest.fixture(autouse=True)
def _sem_smtp():
    with patch("conferencia_app.routes.rh_vaga_routes.enviar_mensagem_smtp") as smtp:
        yield smtp


def _permitir(app, cargo, *chaves):
    with app.app_context():
        for chave in chaves:
            db.session.add(PermissaoAcesso(scope_type="ROLE", scope_id=cargo, permission_key=chave, allow=True))
        db.session.commit()


def _clientes(app):
    """Um cliente por papel, cada um num cargo que so' tem a permissao da sua etapa."""
    _permitir(app, "Gerente de Manufatura", "PAGE_RH_REQUISICAO")
    _permitir(app, "Diretor", "APROVAR_RH_DIRETORIA")
    _permitir(app, "Gerente Financeiro", "APROVAR_RH_FINANCEIRO")
    papeis = {
        "gestor": ("GESTOR1", "Gerente de Manufatura"),
        "outro_gestor": ("GESTOR2", "Gerente de Manufatura"),
        "diretor": ("DIRETOR", "Diretor"),
        "financeiro": ("FINANCEIRO", "Gerente Financeiro"),
        "rh": ("RH1", "RH"),
    }
    clientes = {}
    for papel, (usuario, cargo) in papeis.items():
        clientes[papel] = app.test_client()
        set_logged_user(clientes[papel], usuario, cargo)
    return clientes


def _cadastrar_cargo(rh, nome="Soldador", faixa=("3500,00", "4.800,00")):
    resp = rh.post("/api/rh/cargos", json={"nome": nome, "perfil": "Solda MIG/MAG, leitura de desenho.",
                                           "faixa_min": faixa[0], "faixa_max": faixa[1]})
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()["cargo"]["id"]


def _abrir(cliente, cargo_id, **extra):
    dados = {"tipo": "substituicao", "cargo_id": cargo_id, "substituido_nome": "João da Silva",
             "departamento": "Manufatura", "quantidade": 1, "justificativa": "Desligamento."}
    dados.update(extra)
    resp = cliente.post("/api/rh/vagas", json=dados)
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()["requisicao"]


def _aprovar_tudo(c, rid):
    for papel, esperado in (("diretor", "Aguardando Financeiro"), ("financeiro", "Aguardando RH"), ("rh", "Aprovada")):
        resp = c[papel].post(f"/api/rh/vagas/{rid}/aprovar", json={})
        assert resp.status_code == 200, resp.get_json()
        assert resp.get_json()["requisicao"]["status"] == esperado


def _publicar(c, rid):
    resp = c["rh"].post(f"/api/rh/vagas/{rid}/publicar", json={"titulo": "Soldador(a)", "descricao": "Venha soldar com a gente."})
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()["requisicao"]["link_publico"].rsplit("/", 1)[-1]


def _candidatar(cliente, token, email="maria@exemplo.com", arquivo=("cv.pdf", PDF), ip="10.0.0.1", **extra):
    dados = {"nome": "Maria Souza", "email": email, "telefone": "(15) 99999-0000", "consentimento": "1",
             "curriculo": (BytesIO(arquivo[1]), arquivo[0])}
    dados.update(extra)
    return cliente.post(f"/api/vagas/{token}/candidatura", data=dados, content_type="multipart/form-data",
                        headers={"X-Forwarded-For": ip})


def test_fluxo_completo_da_abertura_ate_a_candidatura(tmp_path):
    app = build_test_app(tmp_path)
    c = _clientes(app)
    cargo_id = _cadastrar_cargo(c["rh"])

    req = _abrir(c["gestor"], cargo_id)
    assert req["status"] == "Aguardando Diretoria" and req["numero"].startswith("RV-")
    rid = req["id"]

    # Fora da vez: o Financeiro nao aprova a etapa da Diretoria.
    assert c["financeiro"].post(f"/api/rh/vagas/{rid}/aprovar", json={}).status_code == 400
    _aprovar_tudo(c, rid)

    # So' o RH publica.
    assert c["gestor"].post(f"/api/rh/vagas/{rid}/publicar", json={"titulo": "x", "descricao": "y"}).status_code == 403
    token = _publicar(c, rid)

    publico = app.test_client()
    pagina = publico.get(f"/vagas/{token}")
    assert pagina.status_code == 200
    html = pagina.get_data(as_text=True)
    assert "Soldador(a)" in html and "Venha soldar" in html
    assert "3500" not in html and "4800" not in html and "João da Silva" not in html

    assert _candidatar(publico, token).status_code == 200
    with app.app_context():
        candidato = RhCandidato.query.one()
        assert candidato.email == "maria@exemplo.com" and candidato.curriculo_dados == PDF
        cid = candidato.id

    lista = c["rh"].get(f"/api/rh/vagas/{rid}/candidatos").get_json()["candidatos"]
    assert [x["nome"] for x in lista] == ["Maria Souza"]
    baixado = c["rh"].get(f"/api/rh/candidatos/{cid}/curriculo")
    assert baixado.status_code == 200 and baixado.data == PDF
    assert "attachment" in baixado.headers["Content-Disposition"]
    with app.app_context():
        acesso = RhCandidatoAcesso.query.one()
        assert (acesso.usuario, acesso.acao, acesso.candidato_id) == ("RH1", "baixou", cid)
    registro = c["rh"].get(f"/api/rh/vagas/{rid}/acessos").get_json()["acessos"]
    assert [(a["usuario"], a["acao"], a["candidato"]) for a in registro] == [("RH1", "baixou", "Maria Souza")]
    assert c["diretor"].get(f"/api/rh/vagas/{rid}/acessos").status_code == 403

    qr = c["rh"].get(f"/api/rh/vagas/{rid}/qrcode.svg")
    assert qr.status_code == 200 and b"<svg" in qr.data

    # Encerrar mata o link publico, mas os candidatos ficam.
    assert c["rh"].post(f"/api/rh/vagas/{rid}/encerrar", json={}).status_code == 200
    assert publico.get(f"/vagas/{token}").status_code == 404
    assert _candidatar(publico, token, email="outro@exemplo.com").status_code == 404
    assert c["rh"].get(f"/api/rh/vagas/{rid}/qrcode.svg").status_code == 404
    assert len(c["rh"].get(f"/api/rh/vagas/{rid}/candidatos").get_json()["candidatos"]) == 1


def test_cada_perfil_ve_so_o_que_lhe_cabe(tmp_path):
    app = build_test_app(tmp_path)
    c = _clientes(app)
    cargo_id = _cadastrar_cargo(c["rh"])
    rid = _abrir(c["gestor"], cargo_id)["id"]

    # Outro gestor nao enxerga a requisicao alheia - nem na lista, nem pelo id.
    assert c["outro_gestor"].get("/api/rh/vagas").get_json()["requisicoes"] == []
    assert c["outro_gestor"].get(f"/api/rh/vagas/{rid}").status_code == 404
    assert c["outro_gestor"].post(f"/api/rh/vagas/{rid}/cancelar", json={"motivo": "x"}).status_code == 404

    # O solicitante acompanha, mas nao ve a faixa salarial do cadastro.
    proprio = c["gestor"].get(f"/api/rh/vagas/{rid}").get_json()["requisicao"]
    assert proprio["ver_salario"] is False and proprio["faixa_min"] is None and proprio["faixa_max"] is None
    assert all("faixa_min" not in cargo for cargo in c["gestor"].get("/api/rh/vagas").get_json()["cargos"])

    # Quem aprova ve a faixa.
    visto = c["diretor"].get(f"/api/rh/vagas/{rid}").get_json()["requisicao"]
    assert (visto["faixa_min"], visto["faixa_max"]) == (3500.0, 4800.0)

    # Candidatos, curriculo e cadastro de cargos: so' a Gestao do RH.
    for cliente in (c["gestor"], c["diretor"], c["financeiro"]):
        assert cliente.get(f"/api/rh/vagas/{rid}/candidatos").status_code == 403
        assert cliente.get("/api/rh/candidatos/1/curriculo").status_code == 403
        assert cliente.get("/api/rh/cargos").status_code == 403
        assert cliente.post("/api/rh/cargos", json={"nome": "X"}).status_code == 403
        assert "candidatos" not in cliente.get(f"/api/rh/vagas/{rid}").get_json()["requisicao"]

    # Sem nenhuma permissao de RH: nem a tela abre. Sem login: vai pro login.
    estranho = app.test_client()
    set_logged_user(estranho, "COMPRADOR", "Compras")
    assert estranho.get("/rh/vagas").status_code == 403
    assert estranho.get("/api/rh/vagas").status_code == 403
    assert app.test_client().get("/api/rh/vagas").status_code == 401


def test_devolucao_para_correcao_recomeca_pela_diretoria(tmp_path):
    app = build_test_app(tmp_path)
    c = _clientes(app)
    cargo_id = _cadastrar_cargo(c["rh"])
    rid = _abrir(c["gestor"], cargo_id)["id"]
    assert c["diretor"].post(f"/api/rh/vagas/{rid}/aprovar", json={}).status_code == 200

    # Devolver exige dizer o que corrigir.
    assert c["financeiro"].post(f"/api/rh/vagas/{rid}/reprovar", json={"motivo": "  "}).status_code == 400
    devolvida = c["financeiro"].post(f"/api/rh/vagas/{rid}/reprovar", json={"motivo": "Sem verba neste trimestre."})
    assert devolvida.get_json()["requisicao"]["status"] == "Em correção"

    corrigida = c["gestor"].put(f"/api/rh/vagas/{rid}", json={
        "tipo": "substituicao", "cargo_id": cargo_id, "substituido_nome": "João da Silva",
        "departamento": "Manufatura", "quantidade": 1, "justificativa": "Início em janeiro.",
    })
    assert corrigida.status_code == 200, corrigida.get_json()
    dados = corrigida.get_json()["requisicao"]
    assert dados["status"] == "Aguardando Diretoria"
    assert "Sem verba neste trimestre." in [e["comentario"] for e in dados["eventos"]]
    # Fora de "Em correção" nao se edita.
    assert c["gestor"].put(f"/api/rh/vagas/{rid}", json={}).status_code == 400


def test_cancelar_exige_justificativa_e_reabrir_reinicia_o_processo(tmp_path):
    app = build_test_app(tmp_path)
    c = _clientes(app)
    cargo_id = _cadastrar_cargo(c["rh"])
    rid = _abrir(c["gestor"], cargo_id)["id"]
    _aprovar_tudo(c, rid)
    token = _publicar(c, rid)

    assert c["gestor"].post(f"/api/rh/vagas/{rid}/cancelar", json={"motivo": ""}).status_code == 400
    cancelada = c["gestor"].post(f"/api/rh/vagas/{rid}/cancelar", json={"motivo": "Reestruturação da área."})
    assert cancelada.get_json()["requisicao"]["status"] == "Cancelada"
    assert app.test_client().get(f"/vagas/{token}").status_code == 404

    reaberta = c["gestor"].post(f"/api/rh/vagas/{rid}/reabrir", json={}).get_json()["requisicao"]
    assert reaberta["status"] == "Aguardando Diretoria" and reaberta["ciclo"] == 2
    # O link antigo nao volta: a vaga precisa ser aprovada e publicada de novo.
    assert app.test_client().get(f"/vagas/{token}").status_code == 404
    with app.app_context():
        assert db.session.get(RhRequisicaoVaga, rid).token_publico is None


def test_vaga_nova_fora_do_cadastro_exige_perfil(tmp_path):
    app = build_test_app(tmp_path)
    c = _clientes(app)
    base = {"tipo": "nova", "cargo_nome": "Analista de Dados", "departamento": "TI", "justificativa": "Área nova."}

    assert c["gestor"].post("/api/rh/vagas", json=base).status_code == 400
    resp = c["gestor"].post("/api/rh/vagas", json={**base, "perfil": "SQL e Python.", "faixa_min": "5000", "faixa_max": "7000"})
    assert resp.status_code == 200, resp.get_json()
    req = resp.get_json()["requisicao"]
    # A faixa que o proprio solicitante propos ele ve.
    assert req["cargo_id"] is None and (req["faixa_min"], req["faixa_max"]) == (5000.0, 7000.0)
    # Substituicao precisa de cargo do cadastro e do nome do substituido.
    assert c["gestor"].post("/api/rh/vagas", json={**base, "tipo": "substituicao", "perfil": "x"}).status_code == 400


def test_pagina_publica_recusa_envio_invalido_e_abuso(tmp_path):
    app = build_test_app(tmp_path)
    c = _clientes(app)
    rid = _abrir(c["gestor"], _cadastrar_cargo(c["rh"]))["id"]
    _aprovar_tudo(c, rid)
    token = _publicar(c, rid)
    publico = app.test_client()

    def total():
        with app.app_context():
            return RhCandidato.query.count()

    assert _candidatar(publico, token, arquivo=("cv.docx", b"PK\x03\x04")).status_code == 400
    # Extensao .pdf com conteudo que nao e' PDF.
    assert _candidatar(publico, token, arquivo=("cv.pdf", b"MZ\x90\x00 executavel")).status_code == 400
    assert _candidatar(publico, token, consentimento="").status_code == 400
    assert _candidatar(publico, token, email="nao-e-email").status_code == 400
    grande = publico.post(f"/api/vagas/{token}/candidatura", data={"nome": "x"}, headers={"Content-Length": str(50 * 1024 * 1024)})
    assert grande.status_code == 400
    assert publico.get("/vagas/token-que-nao-existe").status_code == 404
    assert total() == 0

    # Campo isca preenchido (robo): responde sucesso, mas nao grava.
    assert _candidatar(publico, token, site="http://spam.example").status_code == 200
    assert total() == 0

    assert _candidatar(publico, token).status_code == 200
    # Mesmo e-mail de novo: resposta igual, sem segunda candidatura.
    assert _candidatar(publico, token, ip="10.0.0.2").status_code == 200
    assert total() == 1

    # Teto por endereco: a 6a candidatura do mesmo IP na hora e' recusada.
    for i in range(4):
        assert _candidatar(publico, token, email=f"c{i}@exemplo.com").status_code == 200
    assert _candidatar(publico, token, email="c9@exemplo.com").status_code == 400
    assert total() == 5

    # O RH pode excluir candidato e curriculo; a exclusao fica registrada.
    with app.app_context():
        cid = RhCandidato.query.first().id
    assert c["rh"].delete(f"/api/rh/candidatos/{cid}").status_code == 200
    assert total() == 4
    with app.app_context():
        assert RhCandidatoAcesso.query.filter_by(candidato_id=cid, acao="excluiu").count() == 1


def test_aviso_por_email_vai_para_quem_aprova_a_etapa(tmp_path, _sem_smtp):
    app = build_test_app(tmp_path)
    app.config["MAIL_SENDER"] = "sync@exemplo.com"
    c = _clientes(app)
    with app.app_context():
        db.session.add(Usuario(username="DIRETOR", email="diretor@exemplo.com", role="Diretor"))
        db.session.add(Usuario(username="FINANCEIRO", email="financeiro@exemplo.com", role="Gerente Financeiro"))
        db.session.commit()

    rid = _abrir(c["gestor"], _cadastrar_cargo(c["rh"]))["id"]
    primeira = _sem_smtp.call_args.args[1]
    # Admin tem todas as permissoes e nao entra no aviso.
    assert primeira["To"] == "diretor@exemplo.com"
    corpo = primeira.get_payload()[0].get_payload(decode=True).decode("utf-8")
    assert "Soldador" not in corpo and "3500" not in corpo

    c["diretor"].post(f"/api/rh/vagas/{rid}/aprovar", json={})
    assert _sem_smtp.call_args.args[1]["To"] == "financeiro@exemplo.com"

    # Falha no e-mail nao desfaz a aprovacao.
    _sem_smtp.side_effect = RuntimeError("smtp fora")
    resp = c["financeiro"].post(f"/api/rh/vagas/{rid}/aprovar", json={})
    assert resp.status_code == 200 and resp.get_json()["requisicao"]["status"] == "Aguardando RH"


def test_tela_renderiza_e_menu_aparece_para_quem_so_aprova(tmp_path):
    app = build_test_app(tmp_path)
    c = _clientes(app)

    admin = app.test_client()
    login_admin(admin)
    pagina = admin.get("/rh/vagas")
    assert pagina.status_code == 200
    html = pagina.get_data(as_text=True)
    assert 'id="rh-aba-cargos"' in html and 'id="rh-btn-nova"' in html and 'id="rh-modal-cargo"' in html

    # So' a permissao de aprovar ja' abre a secao RH do menu e a tela, sem o cadastro do RH.
    html = c["financeiro"].get("/rh/vagas").get_data(as_text=True)
    assert 'href="/rh/vagas"' in html
    assert 'id="rh-aba-cargos"' not in html and 'id="rh-btn-nova"' not in html and 'id="rh-modal-cargo"' not in html


def test_cargos_criados_na_gestao_de_acessos(tmp_path):
    app = build_test_app(tmp_path)
    admin = app.test_client()
    login_admin(admin)

    # "Administrativo" contem "admin": o sistema daria acesso total ao cargo.
    assert admin.post("/api/permissoes/cargos", json={"nome": "Gerente Administrativo"}).status_code == 400
    assert admin.post("/api/permissoes/cargos", json={"nome": "compras"}).status_code == 400
    criado = admin.post("/api/permissoes/cargos", json={"nome": "Gerente de Manufatura"})
    assert criado.status_code == 200, criado.get_json()
    assert admin.post("/api/permissoes/cargos", json={"nome": "gerente de manufatura"}).status_code == 400

    catalogo = admin.get("/api/permissoes/catalogo").get_json()
    assert "Gerente de Manufatura" in catalogo["roles"] and "RH" in catalogo["roles"]
    assert catalogo["cargos_personalizados"] == ["Gerente de Manufatura"]

    # Nasce sem permissao nenhuma; o Admin libera o que quiser.
    assert not any(admin.get("/api/permissoes/role/Gerente de Manufatura").get_json()["efetivo"].values())
    salvo = admin.post("/api/permissoes/role/Gerente de Manufatura", json={"permissoes": {"PAGE_RH_REQUISICAO": True}})
    assert salvo.status_code == 200

    with patch("conferencia_app.routes.api_routes.enviar_email_convite_acesso", return_value=True):
        novo = admin.post("/api/registrar", json={"username": "GERENTE", "email": "gerente@exemplo.com", "role": "Gerente de Manufatura"})
        invalido = admin.post("/api/registrar", json={"username": "OUTRO", "email": "outro@exemplo.com", "role": "Cargo Inexistente"})
    assert novo.status_code == 200, novo.get_json()
    assert invalido.status_code == 400

    gerente = app.test_client()
    set_logged_user(gerente, "GERENTE", "Gerente de Manufatura")
    assert gerente.get("/rh/vagas").status_code == 200
    assert gerente.get("/api/rh/cargos").status_code == 403

    # Cargo em uso nao pode ser excluido.
    assert admin.delete("/api/permissoes/cargos/Gerente de Manufatura").status_code == 400

    # Mudar o cargo derruba as sessoes do usuario, pra valer na hora.
    with app.app_context():
        db.session.add(ActiveSession(username="GERENTE", session_id="sessao-gerente", is_active=True))
        db.session.commit()
    mudou = admin.post("/api/admin/usuario/GERENTE/cargo", json={"role": "RH"})
    assert mudou.status_code == 200, mudou.get_json()
    assert admin.post("/api/admin/usuario/GERENTE/cargo", json={"role": "Nao Existe"}).status_code == 400
    with app.app_context():
        assert Usuario.query.filter_by(username="GERENTE").one().role == "RH"
        assert ActiveSession.query.filter_by(session_id="sessao-gerente").one().is_active is False

    assert admin.delete("/api/permissoes/cargos/Gerente de Manufatura").status_code == 200
    assert admin.delete("/api/permissoes/cargos/Compras").status_code == 404
    with app.app_context():
        assert PermissaoAcesso.query.filter_by(scope_type="ROLE", scope_id="Gerente de Manufatura").count() == 0

    # So' Admin mexe em cargo.
    assert gerente.post("/api/permissoes/cargos", json={"nome": "Diretor"}).status_code == 403
