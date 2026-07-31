import io
import json

from conferencia_app import create_app
from conferencia_app.extensions import db
from conferencia_app.models import CadastroWorkflowSolicitacao
from conferencia_app.services.cadastro_workflow_service import categoria_visivel, executar_acao
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
            data={"tipo": "fornecedor", "documento": "11.222.333/0001-81", "contribuinte_icms": "1"},
            follow_redirects=False,
        )

    assert response.status_code == 302
    with app.app_context():
        sol = CadastroWorkflowSolicitacao.query.one()
        assert sol.status == "Em Validacao Fiscal"
        assert sol.departamento_atual == "Fiscal"
        assert "11.222.333/0001-81" in sol.dados_json
        assert '"contribuinte_icms": "1"' in sol.dados_json
        assert "Fornecedor Teste LTDA" not in sol.dados_json
        assert {chk.departamento for chk in sol.checklists} == {"Fiscal"}
        assert "Contribuinte ICMS conferido" in {chk.item for chk in sol.checklists}
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
            dados_json='{"documento":"11.222.333/0001-81","contribuinte_icms":"1"}',
        )
        db.session.add(sol)
        db.session.commit()

        with patch(
            "conferencia_app.services.cadastro_workflow_service.consultar_cartao_cnpj",
            return_value={
                "documento": "11.222.333/0001-81",
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
        data={"tipo": "transportadora", "documento": "30.482.274/0001-25", "contribuinte_icms": "9"},
        follow_redirects=False,
    )

    assert response.status_code == 302
    with app.app_context():
        sol = CadastroWorkflowSolicitacao.query.one()
        assert sol.status == "Em Validacao Fiscal"
        assert sol.departamento_atual == "Fiscal"


def test_cliente_exige_cnpj_valido_para_abrir_solicitacao(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()
    set_logged_user(client, "felipe", "Solicitante")

    response = client.post(
        "/cadastros/novo",
        data={"tipo": "cliente", "documento": "12345", "contribuinte_icms": "1"},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert "Informe um CNPJ válido com 14 dígitos.".encode("utf-8") in response.data

    with app.app_context():
        assert CadastroWorkflowSolicitacao.query.count() == 0


def test_cadastro_direto_fiscal_nao_pode_retornar_para_compras(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()
    set_logged_user(client, "joao", "Fiscal")
    with app.app_context():
        sol = CadastroWorkflowSolicitacao(
            numero="000001",
            tipo="cliente",
            status="Em Validacao Fiscal",
            etapa_atual="Fiscal",
            solicitante="felipe",
            departamento_atual="Fiscal",
            dados_json='{"documento":"11.222.333/0001-81","contribuinte_icms":"1"}',
        )
        db.session.add(sol)
        db.session.commit()
        sol_id = sol.id

        try:
            executar_acao(sol, "devolver_compras", "joao", "Fiscal", comentario="voltar")
            assert False, "cliente nao deveria voltar para compras"
        except ValueError as exc:
            assert "somente pelo Fiscal" in str(exc)

    detail = client.get(f"/cadastros/{sol_id}")
    assert detail.status_code == 200
    assert b'value="devolver_compras"' not in detail.data


def test_workflow_registra_alerta_de_duplicidade(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()
    set_logged_user(client, "felipe", "Solicitante")

    payload = {
        "tipo": "cliente",
        "razao_social": "Cliente Duplicado",
        "documento": "11.222.333/0001-81",
        "contribuinte_icms": "1",
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


def test_solicitante_ve_apenas_as_proprias_solicitacoes(tmp_path):
    app = build_test_app(tmp_path)
    with app.app_context():
        db.session.add(CadastroWorkflowSolicitacao(
            numero="000001", tipo="material", status="Em Validacao Compras",
            etapa_atual="Compras", solicitante="felipe", departamento_atual="Compras",
            dados_json='{"descricao":"Item felipe"}',
        ))
        db.session.add(CadastroWorkflowSolicitacao(
            numero="000002", tipo="material", status="Em Validacao Compras",
            etapa_atual="Compras", solicitante="maria", departamento_atual="Compras",
            dados_json='{"descricao":"Item maria"}',
        ))
        db.session.commit()

    client = app.test_client()
    set_logged_user(client, "felipe", "Solicitante")
    resp = client.get("/cadastros/")
    assert resp.status_code == 200
    assert b"000001" in resp.data
    assert b"000002" not in resp.data


def test_compras_fiscal_e_admin_veem_todas_as_solicitacoes(tmp_path):
    app = build_test_app(tmp_path)
    with app.app_context():
        db.session.add(CadastroWorkflowSolicitacao(
            numero="000001", tipo="material", status="Em Validacao Compras",
            etapa_atual="Compras", solicitante="felipe", departamento_atual="Compras",
            dados_json='{"descricao":"Item felipe"}',
        ))
        db.session.add(CadastroWorkflowSolicitacao(
            numero="000002", tipo="material", status="Em Validacao Fiscal",
            etapa_atual="Fiscal", solicitante="maria", departamento_atual="Fiscal",
            dados_json='{"descricao":"Item maria"}',
        ))
        db.session.commit()

    client = app.test_client()
    for username, role in [("comprador1", "Compras"), ("fiscal1", "Fiscal"), ("admin1", "Admin")]:
        set_logged_user(client, username, role)
        resp = client.get("/cadastros/")
        assert resp.status_code == 200
        assert b"000001" in resp.data
        assert b"000002" in resp.data


def test_acao_de_atendimento_atribui_responsavel_automaticamente(tmp_path):
    app = build_test_app(tmp_path)
    with app.app_context():
        sol = CadastroWorkflowSolicitacao(
            numero="000001", tipo="material", status="Em Validacao Compras",
            etapa_atual="Compras", solicitante="felipe", departamento_atual="Compras",
            dados_json='{"descricao":"Item","unidade_medida":"UN","utilizacao":"07"}',
        )
        db.session.add(sol)
        db.session.commit()

        assert sol.responsavel_atual is None
        executar_acao(sol, "salvar_dados", "maria", "Compras", form={"campo_descricao": "Item revisado"})
        assert sol.responsavel_atual == "maria"

        # nao sobrescreve quem ja assumiu
        executar_acao(sol, "salvar_dados", "joao", "Compras", form={"campo_descricao": "Item revisado 2"})
        assert sol.responsavel_atual == "maria"


def test_categoria_visivel_cobre_as_4_categorias_e_excecoes(tmp_path):
    app = build_test_app(tmp_path)
    with app.app_context():
        aberta_compras = CadastroWorkflowSolicitacao(
            numero="000001", tipo="material", status="Em Validacao Compras",
            etapa_atual="Compras", solicitante="felipe", departamento_atual="Compras",
            dados_json="{}",
        )
        assumida_compras = CadastroWorkflowSolicitacao(
            numero="000002", tipo="material", status="Em Validacao Compras",
            etapa_atual="Compras", solicitante="felipe", departamento_atual="Compras",
            dados_json="{}", responsavel_atual="maria",
        )
        aberta_fiscal = CadastroWorkflowSolicitacao(
            numero="000003", tipo="cliente", status="Em Validacao Fiscal",
            etapa_atual="Fiscal", solicitante="felipe", departamento_atual="Fiscal",
            dados_json="{}",
        )
        assumida_fiscal = CadastroWorkflowSolicitacao(
            numero="000004", tipo="cliente", status="Em Validacao Fiscal",
            etapa_atual="Fiscal", solicitante="felipe", departamento_atual="Fiscal",
            dados_json="{}", responsavel_atual="joao",
        )
        finalizada = CadastroWorkflowSolicitacao(
            numero="000005", tipo="material", status="Cadastrado",
            etapa_atual="Concluido", solicitante="felipe", departamento_atual="Concluido",
            dados_json="{}",
        )
        cancelada = CadastroWorkflowSolicitacao(
            numero="000006", tipo="material", status="Cancelado",
            etapa_atual="Compras", solicitante="felipe", departamento_atual="Compras",
            dados_json="{}",
        )

        assert categoria_visivel(aberta_compras) == "em_analise"
        assert categoria_visivel(assumida_compras) == "atendimento_compras"
        assert categoria_visivel(aberta_fiscal) == "em_analise"
        assert categoria_visivel(assumida_fiscal) == "atendimento_fiscal"
        assert categoria_visivel(finalizada) == "finalizado"
        assert categoria_visivel(cancelada) is None


def test_atualizar_anexos_salva_arquivo_generico_e_preserva_imagem_existente(tmp_path):
    app = build_test_app(tmp_path)
    with app.app_context():
        sol = CadastroWorkflowSolicitacao(
            numero="000001", tipo="material", status="Em Validacao Compras",
            etapa_atual="Compras", solicitante="felipe", departamento_atual="Compras",
            dados_json='{"descricao":"Item"}',
            anexos='{"observacoes": "obs original", "imagem": {"nome": "foto.jpg", "arquivo": "foto_x.jpg", "url": "/cadastros/anexos/foto_x.jpg"}, "arquivo": null}',
        )
        db.session.add(sol)
        db.session.commit()
        sol_id = sol.id

    client = app.test_client()
    set_logged_user(client, "comprador1", "Compras")
    resp = client.post(
        f"/cadastros/{sol_id}/acao",
        data={
            "acao": "atualizar_anexos",
            "anexos": "nova observacao",
            "arquivo_anexo": (io.BytesIO(b"conteudo fake"), "documento.pdf"),
        },
        content_type="multipart/form-data",
        follow_redirects=False,
    )
    assert resp.status_code == 302

    with app.app_context():
        sol = db.session.get(CadastroWorkflowSolicitacao, sol_id)
        anexos = json.loads(sol.anexos)
        assert anexos["observacoes"] == "nova observacao"
        assert anexos["imagem"]["arquivo"] == "foto_x.jpg"
        assert anexos["arquivo"]["nome"] == "documento.pdf"
        assert sol.responsavel_atual == "comprador1"
