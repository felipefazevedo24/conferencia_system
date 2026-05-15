from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from openpyxl import Workbook
from werkzeug.security import generate_password_hash

from conferencia_app import create_app
from conferencia_app.extensions import db
from conferencia_app.models import AgendamentoMotorista, AgendamentoSolicitacao, AgendamentoVeiculo, Usuario, Viagem
from conferencia_app.services.pedidos_service import _linha_postgres_to_pedido, buscar_linhas_pedido


def build_test_app(tmp_path: Path, fornecedores_path: Path, clientes_path: Path):
    db_path = tmp_path / "test_agendamento.db"
    return create_app(
        {
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{db_path}",
            "AGENDAMENTO_FORNECEDORES_XLSX": str(fornecedores_path),
            "AGENDAMENTO_CLIENTES_XLSX": str(clientes_path),
        }
    )


def login_admin(client):
    return client.post("/login", json={"username": "admin", "password": "admin1234"})


def criar_excel_fornecedores(path: Path):
    wb = Workbook()
    ws = wb.active
    ws.append(
        [
            "*Nome",
            "*Razão Social",
            "Código",
            "Tipo de Pessoa",
            "*C.N.P.J/CPF",
            "I.E./RG",
            "*Tipo de Fornecedor",
            "Geral",
            "Telefone (1)",
            "Telefone (2)",
            "Inativo",
            "Endereço",
            "Bairro",
            "Cidade",
            "*Estado",
            "CEP",
            "E-mail",
            "Código Transportadora",
            "Transportadora",
            "Contato",
            "Cod. Integração",
        ]
    )
    ws.append(
        [
            "Fornecedor Teste",
            "Fornecedor Teste Ltda",
            "900",
            "Jurídica",
            "11.222.333/0001-44",
            "",
            "Fornecedor",
            "Janela 08h-12h",
            "11999999999",
            "",
            "Não",
            "Rua Alfa",
            "Centro",
            "Campinas",
            "SP",
            "13000000",
            "fornecedor@teste.com",
            "",
            "",
            "Paula",
            "FOR-900",
        ]
    )
    wb.save(path)


def criar_excel_clientes(path: Path):
    wb = Workbook()
    ws = wb.active
    ws.append(
        [
            "*Nome",
            "Inativo",
            "*Código",
            "R. Social",
            "Pessoa",
            "CNPJ / CPF",
            "I.E. / RG",
            "I.M",
            "Tipo Cliente",
            "Telefone (1)",
            "Telefone (2)",
            "Representante",
            "Contato Principal",
            "CEP",
            "Endereço",
            "Bairro",
            "Cidade",
            "U.F.",
            "Pais",
            "Cidade de Cobrança",
            "Município de Entrega",
            "Código de Integração",
        ]
    )
    ws.append(
        [
            "Cliente Teste",
            "Não",
            "800",
            "Cliente Teste SA",
            "Jurídica",
            "55.666.777/0001-88",
            "",
            "",
            "",
            "11988887777",
            "",
            "",
            "Marina",
            "13001000",
            "Avenida Beta",
            "Jardim",
            "Campinas",
            "SP",
            "BRASIL",
            "",
            "Campinas",
            "CLI-800",
        ]
    )
    wb.save(path)


def test_agendamento_dashboard_importa_cadastros_e_renderiza_pagina(tmp_path):
    fornecedores = tmp_path / "fornecedores.xlsx"
    clientes = tmp_path / "clientes.xlsx"
    criar_excel_fornecedores(fornecedores)
    criar_excel_clientes(clientes)

    app = build_test_app(tmp_path, fornecedores, clientes)
    client = app.test_client()
    login_admin(client)

    page = client.get("/logistica/agendamento-veiculos")
    assert page.status_code == 302
    assert "/logistica/viagens" in page.location

    response = client.get("/api/logistica/agendamento-veiculos/dashboard")
    assert response.status_code == 200
    data = response.get_json()
    assert data["cadastros"]["fornecedores"] >= 1
    assert data["cadastros"]["clientes"] >= 1


def test_motorista_cadastrado_em_usuarios_aparece_na_logistica_sem_reload(tmp_path):
    fornecedores = tmp_path / "fornecedores.xlsx"
    clientes = tmp_path / "clientes.xlsx"
    criar_excel_fornecedores(fornecedores)
    criar_excel_clientes(clientes)

    app = build_test_app(tmp_path, fornecedores, clientes)
    client = app.test_client()
    login_admin(client)

    with app.app_context():
        db.session.add(
            Usuario(
                username="motorista.teste",
                password=generate_password_hash("123456"),
                role="Motorista",
            )
        )
        db.session.commit()
        assert AgendamentoMotorista.query.filter_by(usuario_username="motorista.teste").first() is None

    response = client.get("/api/logistica/agendamento-veiculos/motoristas")
    assert response.status_code == 200
    rows = response.get_json()["rows"]
    assert any(row["nome"] == "motorista.teste" for row in rows)

    dashboard = client.get("/api/logistica/agendamento-veiculos/dashboard")
    assert dashboard.status_code == 200
    assert dashboard.get_json()["resumo"]["motoristas_ativos"] >= 1

    with app.app_context():
        motorista = AgendamentoMotorista.query.filter_by(usuario_username="motorista.teste").first()
        assert motorista is not None
        assert motorista.ativo is True


def test_motorista_cadastrado_em_usuarios_aparece_auxiliares_viagem_sem_reload(tmp_path):
    fornecedores = tmp_path / "fornecedores.xlsx"
    clientes = tmp_path / "clientes.xlsx"
    criar_excel_fornecedores(fornecedores)
    criar_excel_clientes(clientes)

    app = build_test_app(tmp_path, fornecedores, clientes)
    client = app.test_client()
    login_admin(client)

    with app.app_context():
        db.session.add(
            Usuario(
                username="motorista.viagem",
                password=generate_password_hash("123456"),
                role="Motorista",
            )
        )
        db.session.commit()
        assert AgendamentoMotorista.query.filter_by(usuario_username="motorista.viagem").first() is None

    response = client.get("/api/viagem/auxiliares")
    assert response.status_code == 200
    motoristas = response.get_json()["motoristas"]
    assert any(row["label"] == "motorista.viagem" for row in motoristas)

    with app.app_context():
        motorista = AgendamentoMotorista.query.filter_by(usuario_username="motorista.viagem").first()
        assert motorista is not None
        assert motorista.ativo is True


def test_viagem_bloqueia_conflito_de_motorista(tmp_path):
    fornecedores = tmp_path / "fornecedores.xlsx"
    clientes = tmp_path / "clientes.xlsx"
    criar_excel_fornecedores(fornecedores)
    criar_excel_clientes(clientes)

    app = build_test_app(tmp_path, fornecedores, clientes)
    client = app.test_client()
    login_admin(client)

    with app.app_context():
        veiculo_a = AgendamentoVeiculo.query.filter_by(codigo="VAN-A").first()
        if not veiculo_a:
            veiculo_a = AgendamentoVeiculo(codigo="VAN-A", nome_exibicao="Van A", ativo=True)
            db.session.add(veiculo_a)
        veiculo_b = AgendamentoVeiculo.query.filter_by(codigo="VAN-B").first()
        if not veiculo_b:
            veiculo_b = AgendamentoVeiculo(codigo="VAN-B", nome_exibicao="Van B", ativo=True)
            db.session.add(veiculo_b)
        motorista = AgendamentoMotorista(nome="Motorista Conflito", ativo=True)
        db.session.add(motorista)
        db.session.flush()
        saida = datetime.now().replace(second=0, microsecond=0) + timedelta(hours=2)
        db.session.add(
            Viagem(
                codigo="VG-CONFLITO-1",
                veiculo_id=veiculo_a.id,
                motorista_id=motorista.id,
                motorista_nome=motorista.nome,
                status="Planejada",
                tipo="MISTA",
                saida_prevista=saida,
                retorno_previsto=saida + timedelta(hours=2),
            )
        )
        db.session.commit()
        veiculo_b_id = veiculo_b.id
        motorista_id = motorista.id

    response = client.post(
        "/api/viagem",
        json={
            "veiculo_id": veiculo_b_id,
            "motorista_id": motorista_id,
            "tipo": "ENTREGA",
            "saida_prevista": saida.isoformat(timespec="minutes"),
            "retorno_previsto": (saida + timedelta(hours=1)).isoformat(timespec="minutes"),
            "paradas": [{"tipo": "ENTREGA", "parceiro_nome": "Cliente", "cidade": "Campinas", "uf": "SP"}],
        },
    )

    assert response.status_code == 409
    assert "outra viagem" in response.get_json()["msg"]


def test_viagem_agenda_e_torre_controle_retorna_dados(tmp_path):
    fornecedores = tmp_path / "fornecedores.xlsx"
    clientes = tmp_path / "clientes.xlsx"
    criar_excel_fornecedores(fornecedores)
    criar_excel_clientes(clientes)

    app = build_test_app(tmp_path, fornecedores, clientes)
    client = app.test_client()
    login_admin(client)

    with app.app_context():
        veiculo = AgendamentoVeiculo.query.filter_by(codigo="VAN-T").first()
        if not veiculo:
            veiculo = AgendamentoVeiculo(codigo="VAN-T", nome_exibicao="Van Torre", ativo=True)
            db.session.add(veiculo)
        motorista = AgendamentoMotorista(nome="Motorista Torre", ativo=True)
        db.session.add(motorista)
        db.session.flush()
        saida = datetime.now().replace(second=0, microsecond=0) + timedelta(minutes=30)
        db.session.add(
            Viagem(
                codigo="VG-TORRE-1",
                veiculo_id=veiculo.id,
                motorista_id=motorista.id,
                motorista_nome=motorista.nome,
                status="Planejada",
                tipo="COLETA",
                saida_prevista=saida,
                retorno_previsto=saida + timedelta(hours=1),
            )
        )
        db.session.commit()
        data = saida.strftime("%Y-%m-%d")

    agenda = client.get(f"/api/viagem/agenda?modo=dia&data={data}")
    assert agenda.status_code == 200
    assert agenda.get_json()["linhas"]

    torre = client.get("/api/viagem/torre-controle")
    assert torre.status_code == 200
    payload = torre.get_json()
    assert "resumo" in payload
    assert any(item["codigo"] == "VG-TORRE-1" for item in payload["items"])


def test_registrar_usuario_motorista_cria_cadastro_operacional(tmp_path):
    fornecedores = tmp_path / "fornecedores.xlsx"
    clientes = tmp_path / "clientes.xlsx"
    criar_excel_fornecedores(fornecedores)
    criar_excel_clientes(clientes)

    app = build_test_app(tmp_path, fornecedores, clientes)
    client = app.test_client()

    with client.session_transaction() as sess:
        sess["username"] = "ADMIN"
        sess["role"] = "Admin"

    with patch("conferencia_app.routes.api_routes.enviar_email_registro") as enviar_email_mock:
        response = client.post(
            "/api/registrar",
            json={
                "username": "motorista.registro",
                "email": "motorista.registro@teste.com",
                "role": "Motorista",
            },
        )

    assert response.status_code == 200
    assert response.get_json()["sucesso"] is True
    enviar_email_mock.assert_called_once()

    _, kwargs = enviar_email_mock.call_args
    assert kwargs["destinatario_email"] == "motorista.registro@teste.com"
    assert kwargs["username"] == "MOTORISTA.REGISTRO"
    assert kwargs["role"] == "Motorista"
    assert kwargs["url_login"].endswith("/login")

    with app.app_context():
        motorista = AgendamentoMotorista.query.filter_by(usuario_username="MOTORISTA.REGISTRO").first()
        assert motorista is not None
        assert motorista.nome == "MOTORISTA.REGISTRO"
        assert motorista.ativo is True


def test_busca_linhas_pedido_nao_usa_planilha_como_fallback():
    with patch("conferencia_app.services.pedidos_service._buscar_linhas_pedido_postgres", return_value=[]), patch(
        "conferencia_app.services.pedidos_service._carregar_rows_google_sheets"
    ) as carregar_sheets, patch("conferencia_app.services.pedidos_service._carregar_rows_excel_local") as carregar_excel, patch(
        "conferencia_app.services.pedidos_service._load_pedidos_cache"
    ) as carregar_cache:
        linhas = buscar_linhas_pedido("OC-9001")

    assert linhas == []
    carregar_sheets.assert_not_called()
    carregar_excel.assert_not_called()
    carregar_cache.assert_not_called()


def test_busca_linhas_pedido_usa_erp_postgres():
    linhas_erp = [
        {
            "ordem_compra": "11560",
            "cod_fornecedor": "635",
            "fornecedor": "ARTOLE PARAFUSOS LTDA",
            "cod_interno": "19-03-00016",
            "descricao": "PARAFUSO ALLEN",
            "pendente": 100.0,
            "preco_unitario": 0.23,
            "vl_pendente": 23.0,
            "total_item": 23.0,
            "pedido_compra": "11560",
            "qtd": 100.0,
            "valor_unit": 0.23,
            "codigo_material": "19-03-00016",
            "descricao_material": "PARAFUSO ALLEN",
            "fornecedor_codigo": "635",
            "fornecedor_nome": "ARTOLE PARAFUSOS LTDA",
            "fonte_dados": "ERPPostgres",
        }
    ]

    with patch("conferencia_app.services.pedidos_service._buscar_linhas_pedido_postgres", return_value=linhas_erp), patch(
        "conferencia_app.services.pedidos_service._carregar_rows_google_sheets"
    ) as carregar_sheets, patch("conferencia_app.services.pedidos_service._save_pedidos_cache") as salvar_cache:
        linhas = buscar_linhas_pedido("11560")

    assert linhas == linhas_erp
    assert linhas[0]["pendente"] == 100.0
    assert linhas[0]["vl_pendente"] == 23.0
    carregar_sheets.assert_not_called()
    salvar_cache.assert_not_called()


def test_linha_postgres_preserva_codigo_material_do_erp_sem_formatar():
    linha = _linha_postgres_to_pedido(
        {
            "ordem_compra": "11560",
            "cod_fornecedor": "635",
            "fornecedor": "ARTOLE PARAFUSOS LTDA",
            "cod_interno": "190300016",
            "descricao": "PARAFUSO ALLEN",
            "pendente": 100.0,
            "preco_unitario": 0.23,
            "vl_pendente": 23.0,
            "total_item": 23.0,
        }
    )

    assert linha["codigo_material"] == "190300016"


def test_agendamento_cria_coleta_e_bloqueia_conflito_de_veiculo(tmp_path):
    fornecedores = tmp_path / "fornecedores.xlsx"
    clientes = tmp_path / "clientes.xlsx"
    criar_excel_fornecedores(fornecedores)
    criar_excel_clientes(clientes)

    app = build_test_app(tmp_path, fornecedores, clientes)
    client = app.test_client()
    login_admin(client)

    consulta_oc = {
        "encontrada": True,
        "numero_oc": "OC-9001",
        "fornecedor": {
            "nome": "Fornecedor Teste",
            "razao_social": "Fornecedor Teste Ltda",
            "cnpj_cpf": "11222333000144",
            "contato": "Paula",
            "telefone": "11999999999",
            "logradouro": "Rua Alfa",
            "bairro": "Centro",
            "cidade": "Campinas",
            "uf": "SP",
            "cep": "13000000",
        },
        "itens": [
            {"descricao": "Item A", "quantidade": 3, "unidade": "UN", "volumes": 2},
        ],
    }

    with patch("conferencia_app.routes.agendamento_routes.consultar_oc_agendamento", return_value=consulta_oc):
        create_1 = client.post(
            "/api/logistica/agendamento-veiculos/solicitacoes",
            json={
                "tipo": "COLETA",
                "prioridade": "Alta",
                "numero_oc": "OC-9001",
            },
        )
        assert create_1.status_code == 201
        solicitacao_id_1 = create_1.get_json()["solicitacao"]["id"]

        create_2 = client.post(
            "/api/logistica/agendamento-veiculos/solicitacoes",
            json={
                "tipo": "COLETA",
                "prioridade": "Media",
                "numero_oc": "OC-9001",
            },
        )
        assert create_2.status_code == 201
        solicitacao_id_2 = create_2.get_json()["solicitacao"]["id"]

    alocar_1 = client.post(
        f"/api/logistica/agendamento-veiculos/solicitacoes/{solicitacao_id_1}/alocar",
        json={
            "veiculo_codigo": "IVECO",
            "data_hora_saida_prevista": "2026-04-10T08:30",
            "data_hora_retorno_prevista": "2026-04-10T10:30",
        },
    )
    assert alocar_1.status_code == 200

    alocar_2 = client.post(
        f"/api/logistica/agendamento-veiculos/solicitacoes/{solicitacao_id_2}/alocar",
        json={
            "veiculo_codigo": "IVECO",
            "data_hora_saida_prevista": "2026-04-10T09:00",
            "data_hora_retorno_prevista": "2026-04-10T11:00",
        },
    )
    assert alocar_2.status_code == 409
    assert "IVECO" in alocar_2.get_json()["error"]


def test_agendamento_coleta_grava_origem_google_sheets(tmp_path):
    fornecedores = tmp_path / "fornecedores.xlsx"
    clientes = tmp_path / "clientes.xlsx"
    criar_excel_fornecedores(fornecedores)
    criar_excel_clientes(clientes)

    app = build_test_app(tmp_path, fornecedores, clientes)
    client = app.test_client()
    login_admin(client)

    consulta_oc = {
        "encontrada": True,
        "numero_oc": "OC-9050",
        "fonte": {
            "tipo": "GoogleSheets",
            "label": "Google Sheets",
            "url": "https://docs.google.com/spreadsheets/d/1mo0Vb8mvVl_XyPdRENVqVF_UiPY4DNarhM5xB_wCtx8/edit",
        },
        "fornecedor": {
            "nome": "Fornecedor Teste",
            "razao_social": "Fornecedor Teste Ltda",
            "cnpj_cpf": "11222333000144",
            "contato": "Paula",
            "telefone": "11999999999",
            "logradouro": "Rua Alfa",
            "bairro": "Centro",
            "cidade": "Campinas",
            "uf": "SP",
            "cep": "13000000",
        },
        "itens": [
            {"descricao": "Item A", "quantidade": 3, "unidade": "UN", "volumes": 2},
        ],
    }

    with patch("conferencia_app.routes.agendamento_routes.consultar_oc_agendamento", return_value=consulta_oc):
        create = client.post(
            "/api/logistica/agendamento-veiculos/solicitacoes",
            json={
                "tipo": "COLETA",
                "prioridade": "Alta",
                "numero_oc": "OC-9050",
            },
        )

    assert create.status_code == 201
    payload = create.get_json()["solicitacao"]
    assert payload["origem_documento"] == "GoogleSheets"
    assert payload["origem_documento_label"] == "Google Sheets"

    with app.app_context():
        row = AgendamentoSolicitacao.query.get(payload["id"])
        assert row.origem_documento == "GoogleSheets"


def test_agendamento_entrega_conclui_fluxo(tmp_path):
    fornecedores = tmp_path / "fornecedores.xlsx"
    clientes = tmp_path / "clientes.xlsx"
    criar_excel_fornecedores(fornecedores)
    criar_excel_clientes(clientes)

    app = build_test_app(tmp_path, fornecedores, clientes)
    client = app.test_client()
    login_admin(client)

    consulta_nf = {
        "encontrada": True,
        "numero_nf": "99881",
        "cliente": {
            "nome": "Cliente Teste",
            "razao_social": "Cliente Teste SA",
            "cnpj_cpf": "55666777000188",
            "contato": "Marina",
            "telefone": "11988887777",
            "logradouro": "Avenida Beta",
            "bairro": "Jardim",
            "cidade": "Campinas",
            "uf": "SP",
            "cep": "13001000",
        },
        "itens": [
            {"descricao": "Produto 1", "quantidade": 1, "unidade": "UN", "volumes": 1},
        ],
    }

    with patch("conferencia_app.routes.agendamento_routes.consultar_nf_agendamento", return_value=consulta_nf):
        create = client.post(
            "/api/logistica/agendamento-veiculos/solicitacoes",
            json={
                "tipo": "ENTREGA",
                "prioridade": "Critica",
                "numero_nf": "99881",
            },
        )
    assert create.status_code == 201
    solicitacao_id = create.get_json()["solicitacao"]["id"]

    alocar = client.post(
        f"/api/logistica/agendamento-veiculos/solicitacoes/{solicitacao_id}/alocar",
        json={
            "veiculo_codigo": "SAVEIRO",
            "data_hora_saida_prevista": "2026-04-11T13:30",
        },
    )
    assert alocar.status_code == 200

    em_rota = client.post(
        f"/api/logistica/agendamento-veiculos/solicitacoes/{solicitacao_id}/status",
        json={"status": "EmRota"},
    )
    assert em_rota.status_code == 200

    concluido = client.post(
        f"/api/logistica/agendamento-veiculos/solicitacoes/{solicitacao_id}/status",
        json={"status": "Concluida", "data_hora_saida_real": "2026-04-11T13:35"},
    )
    assert concluido.status_code == 200
    assert concluido.get_json()["solicitacao"]["status"] == "Concluida"

    with app.app_context():
        row = AgendamentoSolicitacao.query.get(solicitacao_id)
        assert row.status == "Concluida"
        assert row.concluido_em is not None
