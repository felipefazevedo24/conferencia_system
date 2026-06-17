from conferencia_app import create_app
from conferencia_app.extensions import db
from conferencia_app.models import CadastroWorkflowSolicitacao
from conferencia_app.services.cadastro_workflow_service import executar_acao
from unittest.mock import patch


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
            "tipo": "material",
            "descricao": "Parafuso inox",
            "unidade_medida": "UN",
            "utilizacao": "07",
            "fornecedor_sugerido": "Fornecedor ABC",
        },
        follow_redirects=False,
    )

    assert response.status_code == 302
    detail = client.get(response.headers["Location"])
    assert detail.status_code == 200
    assert "Validação de Compras".encode("utf-8") in detail.data
    assert "NCM sugerido".encode("utf-8") in detail.data
    assert b"Fornecedor ABC" in detail.data

    with app.app_context():
        sol = CadastroWorkflowSolicitacao.query.one()
        assert sol.numero == "000001"
        assert sol.status == "Em Validacao Compras"
        assert sol.departamento_atual == "Compras"
        assert '"codigo"' not in sol.dados_json
        assert '"utilizacao": "07"' in sol.dados_json
        assert '"fornecedor_sugerido": "Fornecedor ABC"' in sol.dados_json
        assert '"unidade_compra": "UN"' in sol.dados_json
        assert '"ncm_sugerido": "73181500"' in sol.dados_json

        executar_acao(sol, "assumir", "maria", "Compras")
        executar_acao(sol, "aprovar_compras", "maria", "Compras")
        executar_acao(sol, "assumir", "joao", "Fiscal")
        executar_acao(sol, "finalizar", "joao", "Fiscal")

        assert sol.status == "Cadastrado"
        assert sol.departamento_atual == "Concluido"
        assert len(sol.historicos) >= 6
        assert sol.notificacoes


def test_fornecedor_abre_somente_com_cnpj_e_vai_direto_para_fiscal_sem_consultar_na_abertura(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()
    set_logged_user(client, "felipe", "Solicitante")

    with patch(
        "conferencia_app.services.cadastro_workflow_service.consultar_cartao_cnpj",
        side_effect=AssertionError("nao deve consultar na abertura"),
    ) as consulta:
        response = client.post(
            "/cadastros/novo",
            data={"tipo": "fornecedor", "documento": "12.345.678/0001-90"},
            follow_redirects=False,
        )

    assert response.status_code == 302
    with app.app_context():
        sol = CadastroWorkflowSolicitacao.query.one()
        assert sol.status == "Em Validacao Fiscal"
        assert sol.departamento_atual == "Fiscal"
        assert "12.345.678/0001-90" in sol.dados_json
        assert "Fornecedor Teste LTDA" not in sol.dados_json
        consulta.assert_not_called()


def test_fiscal_consulta_cnpj_e_preenche_dados_do_fornecedor(tmp_path):
    app = build_test_app(tmp_path)
    with app.app_context():
        sol = CadastroWorkflowSolicitacao(
            numero="000001",
            tipo="fornecedor",
            status="Em Validacao Fiscal",
            etapa_atual="Fiscal",
            solicitante="felipe",
            departamento_atual="Fiscal",
            dados_json='{"documento":"12.345.678/0001-90"}',
        )
        db.session.add(sol)
        db.session.commit()

        with patch(
            "conferencia_app.services.cadastro_workflow_service.consultar_cartao_cnpj",
            return_value={
                "documento": "12.345.678/0001-90",
                "razao_social": "Fornecedor Teste LTDA",
                "endereco": "Rua Teste, 100",
                "inscricao_estadual": "110042490114",
            },
        ):
            executar_acao(sol, "consultar_cnpj", "joao", "Fiscal")

        assert "Fornecedor Teste LTDA" in sol.dados_json
        assert "110042490114" in sol.dados_json


def test_transportadora_tambem_abre_direto_para_fiscal(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()
    set_logged_user(client, "felipe", "Solicitante")

    response = client.post(
        "/cadastros/novo",
        data={"tipo": "transportadora", "documento": "98.765.432/0001-10"},
        follow_redirects=False,
    )

    assert response.status_code == 302
    with app.app_context():
        sol = CadastroWorkflowSolicitacao.query.one()
        assert sol.status == "Em Validacao Fiscal"
        assert sol.departamento_atual == "Fiscal"


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
    with patch("conferencia_app.services.cadastro_workflow_service.consultar_cartao_cnpj", side_effect=ValueError("offline")):
        assert client.post("/cadastros/novo", data=payload).status_code == 302
        assert client.post("/cadastros/novo", data=payload).status_code == 302

    with app.app_context():
        rows = CadastroWorkflowSolicitacao.query.order_by(CadastroWorkflowSolicitacao.id).all()
        assert rows[1].alerta_duplicidade
        assert "Documento já usado" in rows[1].alerta_duplicidade


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
            dados_json='{"descricao":"Item","unidade_medida":"UN","utilizacao":"07"}',
        )
        db.session.add(sol)
        db.session.commit()

        try:
            executar_acao(sol, "devolver_solicitante", "maria", "Compras")
            assert False, "acao deveria exigir comentario"
        except ValueError as exc:
            assert "Comentário obrigatório" in str(exc)


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
            dados_json='{"descricao":"Item","unidade_medida":"UN","utilizacao":"07"}',
        )
        db.session.add(sol)
        db.session.commit()

        try:
            executar_acao(sol, "aprovar_compras", "felipe", "Solicitante")
            assert False, "solicitante nao deveria aprovar compras"
        except ValueError as exc:
            assert "Somente Compras" in str(exc)
