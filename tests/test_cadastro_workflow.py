from conferencia_app import create_app
from conferencia_app.extensions import db
from conferencia_app.models import CadastroWorkflowSolicitacao
from conferencia_app.services.cadastro_workflow_service import executar_acao


def build_test_app(tmp_path):
    db_path = tmp_path / "workflow.db"
    return create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": f"sqlite:///{db_path}"})


def set_logged_user(client, username, role):
    with client.session_transaction() as sess:
        sess["username"] = username
        sess["role"] = role


def test_workflow_cria_solicitacao_e_encaminha_ate_cadastro(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()
    set_logged_user(client, "felipe", "Solicitante")

    response = client.post(
        "/cadastros/novo",
        data={
            "tipo": "fornecedor",
            "razao_social": "Fornecedor Teste LTDA",
            "documento": "12.345.678/0001-90",
            "endereco": "Rua Teste, 100",
            "email": "fornecedor@example.com",
        },
        follow_redirects=False,
    )

    assert response.status_code == 302
    with app.app_context():
        sol = CadastroWorkflowSolicitacao.query.one()
        assert sol.numero == "000001"
        assert sol.status == "Em Validacao Compras"
        assert sol.departamento_atual == "Compras"

        executar_acao(sol, "assumir", "maria", "Compras")
        executar_acao(sol, "aprovar_compras", "maria", "Compras")
        executar_acao(sol, "assumir", "joao", "Fiscal")
        executar_acao(sol, "finalizar", "joao", "Fiscal")

        assert sol.status == "Cadastrado"
        assert sol.departamento_atual == "Concluido"
        assert len(sol.historicos) >= 6
        assert sol.notificacoes


def test_workflow_registra_alerta_de_duplicidade(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()
    set_logged_user(client, "felipe", "Solicitante")

    payload = {
        "tipo": "cliente",
        "razao_social": "Cliente Duplicado",
        "documento": "11.222.333/0001-44",
        "endereco": "Rua A",
    }
    assert client.post("/cadastros/novo", data=payload).status_code == 302
    assert client.post("/cadastros/novo", data=payload).status_code == 302

    with app.app_context():
        rows = CadastroWorkflowSolicitacao.query.order_by(CadastroWorkflowSolicitacao.id).all()
        assert rows[1].alerta_duplicidade
        assert "Documento ja usado" in rows[1].alerta_duplicidade


def test_workflow_exige_comentario_para_devolucao(tmp_path):
    app = build_test_app(tmp_path)
    with app.app_context():
        sol = CadastroWorkflowSolicitacao(
            numero="000001",
            tipo="material",
            status="Em Validacao Compras",
            etapa_atual="Compras",
            solicitante="felipe",
            departamento_atual="Compras",
            dados_json='{"descricao":"Item","unidade_medida":"UN","grupo_produto":"Teste"}',
        )
        db.session.add(sol)
        db.session.commit()

        try:
            executar_acao(sol, "devolver_solicitante", "maria", "Compras")
            assert False, "acao deveria exigir comentario"
        except ValueError as exc:
            assert "Comentario obrigatorio" in str(exc)


def test_solicitante_nao_aprova_compras(tmp_path):
    app = build_test_app(tmp_path)
    with app.app_context():
        sol = CadastroWorkflowSolicitacao(
            numero="000001",
            tipo="material",
            status="Em Validacao Compras",
            etapa_atual="Compras",
            solicitante="felipe",
            departamento_atual="Compras",
            dados_json='{"descricao":"Item","unidade_medida":"UN","grupo_produto":"Teste"}',
        )
        db.session.add(sol)
        db.session.commit()

        try:
            executar_acao(sol, "aprovar_compras", "felipe", "Solicitante")
            assert False, "solicitante nao deveria aprovar compras"
        except ValueError as exc:
            assert "Somente Compras" in str(exc)
