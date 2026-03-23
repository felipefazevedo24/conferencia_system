from datetime import datetime
from unittest.mock import Mock, patch

from conferencia_app import create_app
from conferencia_app.extensions import db
from conferencia_app.models import BoletoContaReceber, ItemNota, LogDivergencia, LogEstornoLancamento, LogManifestacaoDestinatario, SolicitacaoDevolucaoRecebimento
from conferencia_app.services.xml_service import process_xml_and_store
from werkzeug.security import generate_password_hash


def build_test_app(tmp_path):
    db_path = tmp_path / "test.db"
    return create_app(
        {
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{db_path}",
        }
    )


def login_admin(client):
    return client.post("/login", json={"username": "admin", "password": "admin123"})


def login_portaria(client, app):
    with app.app_context():
        from conferencia_app.models import Usuario

        if not Usuario.query.filter_by(username="portaria_teste").first():
            db.session.add(
                Usuario(
                    username="portaria_teste",
                    password=generate_password_hash("portaria123"),
                    role="Portaria",
                )
            )
            db.session.commit()
    return client.post("/login", json={"username": "portaria_teste", "password": "portaria123"})


def set_logged_user(client, username, role):
    with client.session_transaction() as sess:
        sess["username"] = username
        sess["role"] = role


def build_test_nfe_xml(numero_nota, itens, fornecedor="Fornecedor XML"):
        itens_xml = "".join(
                f"""
                <det nItem=\"{idx}\">
                        <prod>
                                <cProd>{item['codigo']}</cProd>
                                <xProd>{item['descricao']}</xProd>
                                <CFOP>{item['cfop']}</CFOP>
                                <uCom>{item.get('unidade', 'UN')}</uCom>
                                <qCom>{item['quantidade']}</qCom>
                        </prod>
                </det>
                """
                for idx, item in enumerate(itens, start=1)
        )
        return f"""
        <nfeProc xmlns="http://www.portalfiscal.inf.br/nfe">
            <NFe>
                <infNFe Id="NFe12345678901234567890123456789012345678901234">
                    <ide><nNF>{numero_nota}</nNF></ide>
                    <emit><xNome>{fornecedor}</xNome></emit>
                    {itens_xml}
                    <total><ICMSTot><vNF>100.00</vNF><vICMS>18.00</vICMS></ICMSTot></total>
                </infNFe>
            </NFe>
        </nfeProc>
        """.encode("utf-8")


def test_login_success(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()

    response = login_admin(client)

    assert response.status_code == 200
    assert response.get_json()["sucesso"] is True


def test_login_invalid_password(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()

    response = client.post("/login", json={"username": "admin", "password": "errada"})

    assert response.status_code == 401
    assert response.get_json()["sucesso"] is False


def test_stats_requires_authentication(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()

    response = client.get("/api/stats")

    assert response.status_code == 302
    assert "/login" in response.location


def test_validar_payload_missing_fields_returns_400(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()
    login_admin(client)

    response = client.post("/validar", json={"nota": "123"})

    assert response.status_code == 400
    data = response.get_json()
    assert data["error"] == "Payload inválido"
    assert "contagens" in data["details"]


def test_reverter_conferencia_requires_reason(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()
    login_admin(client)

    response = client.post("/api/admin/resetar_nota", json={"nota": "123"})

    assert response.status_code == 400
    data = response.get_json()
    assert data["error"] == "Payload inválido"
    assert "motivo" in data["details"]


def test_nao_permite_reverter_conferencia_de_nota_lancada(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()
    login_admin(client)

    with app.app_context():
        db.session.add(
            ItemNota(
                numero_nota="999",
                fornecedor="Fornecedor X",
                codigo="ABC",
                descricao="Item teste",
                qtd_real=1.0,
                status="Lançado",
                numero_lancamento="ERP-1",
                usuario_lancamento="admin",
            )
        )
        db.session.commit()

    response = client.post("/api/admin/resetar_nota", json={"nota": "999", "motivo": "teste"})
    assert response.status_code == 409


def test_fiscal_estorna_lancamento_e_volta_para_concluido(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()
    login_admin(client)

    with app.app_context():
        db.session.add(
            ItemNota(
                numero_nota="1000",
                fornecedor="Fornecedor Y",
                codigo="DEF",
                descricao="Item teste 2",
                qtd_real=2.0,
                status="Lançado",
                numero_lancamento="ERP-2",
                usuario_lancamento="admin",
            )
        )
        db.session.commit()

    response = client.post(
        "/api/fiscal/estornar_lancamento",
        json={"nota": "1000", "motivo": "Ajuste fiscal"},
    )
    assert response.status_code == 200
    assert response.get_json()["sucesso"] is True

    with app.app_context():
        item = ItemNota.query.filter_by(numero_nota="1000").first()
        assert item.status == "Concluído"
        assert item.numero_lancamento is None


def test_confirmar_lancamento_envia_manifestacao_do_destinatario(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()
    login_admin(client)

    with app.app_context():
        db.session.add(
            ItemNota(
                numero_nota="2000",
                fornecedor="Fornecedor Manifestacao",
                codigo="MAN1",
                descricao="Item manifestado",
                qtd_real=1.0,
                status="Concluído",
                chave_acesso="20002000200020002000200020002000200020002000",
            )
        )
        db.session.commit()

    with patch("conferencia_app.routes.api_routes.manifestar_destinatario_consyste", return_value=(True, 200, {})):
        response = client.post(
            "/api/confirmar_lancamento",
            json={"nota": "2000", "codigo": "ERP-2000", "manifestar_destinatario": True},
        )

    assert response.status_code == 200
    data = response.get_json()
    assert data["sucesso"] is True
    assert data["manifestacao"]["sucesso"] is True

    with app.app_context():
        item = ItemNota.query.filter_by(numero_nota="2000").first()
        log = LogManifestacaoDestinatario.query.filter_by(numero_nota="2000").first()
        assert item.status == "Lançado"
        assert log is not None
        assert log.status == "Sucesso"


def test_confirmar_lancamento_reverte_quando_manifestacao_falha(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()
    login_admin(client)

    with app.app_context():
        db.session.add(
            ItemNota(
                numero_nota="2005",
                fornecedor="Fornecedor Falha Manifestacao",
                codigo="MAN5",
                descricao="Item com falha de manifestacao",
                qtd_real=1.0,
                status="Concluído",
                chave_acesso="20052005200520052005200520052005200520052005",
            )
        )
        db.session.commit()

    with patch(
        "conferencia_app.routes.api_routes.manifestar_destinatario_consyste",
        return_value=(False, 502, {"error": "SEFAZ indisponível"}),
    ):
        response = client.post(
            "/api/confirmar_lancamento",
            json={"nota": "2005", "codigo": "ERP-2005", "manifestar_destinatario": True},
        )

    assert response.status_code == 502
    data = response.get_json()
    assert data["sucesso"] is False
    assert "SEFAZ indisponível" in data["msg"]

    with app.app_context():
        item = ItemNota.query.filter_by(numero_nota="2005").first()
        log = LogManifestacaoDestinatario.query.filter_by(numero_nota="2005").order_by(LogManifestacaoDestinatario.id.desc()).first()
        assert item.status == "Concluído"
        assert item.numero_lancamento is None
        assert item.usuario_lancamento is None
        assert log is not None
        assert log.status == "Falha"


def test_reenvio_manifestacao_exige_nf_lancada(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()
    login_admin(client)

    with app.app_context():
        db.session.add(
            ItemNota(
                numero_nota="2001",
                fornecedor="Fornecedor Sem Lancamento",
                codigo="MAN2",
                descricao="Item sem lancamento",
                qtd_real=1.0,
                status="Concluído",
                chave_acesso="20012001200120012001200120012001200120012001",
            )
        )
        db.session.commit()

    response = client.post("/api/fiscal/manifestar_destinatario", json={"nota": "2001"})
    assert response.status_code == 409


def test_download_documento_por_numero_da_nf(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()
    login_admin(client)

    with app.app_context():
        db.session.add(
            ItemNota(
                numero_nota="321",
                fornecedor="Fornecedor Download",
                codigo="XML1",
                descricao="Item XML",
                qtd_real=1.0,
                status="Concluído",
                chave_acesso="12345678901234567890123456789012345678901234",
            )
        )
        db.session.commit()

    resposta_consyste = Mock()
    resposta_consyste.ok = True
    resposta_consyste.content = b"%PDF-1.4 fake"

    with patch("conferencia_app.routes.api_routes.requests.get", return_value=resposta_consyste) as mocked_get:
        response = client.get("/api/consyste/documento?nota=321&tipo=pdf")

    assert response.status_code == 200
    assert response.data == b"%PDF-1.4 fake"
    assert "NF_321.pdf" in response.headers["Content-Disposition"]
    mocked_get.assert_called_once()


def test_portaria_pode_consultar_nfes_liberadas_mas_nao_baixar_documento(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()
    login_portaria(client, app)

    with app.app_context():
        db.session.add(
            ItemNota(
                numero_nota="322",
                fornecedor="Fornecedor Consulta Portaria",
                codigo="PORT1",
                descricao="Item portaria",
                qtd_real=1.0,
                status="Concluído",
                chave_acesso="12345123451234512345123451234512345123451234",
            )
        )
        db.session.commit()

    pagina = client.get("/fiscal/liberadas")
    assert pagina.status_code == 200

    consulta = client.get("/api/fiscal/notas_liberadas")
    assert consulta.status_code == 200
    data = consulta.get_json()
    assert any(item["numero"] == "322" for item in data)

    download = client.get("/api/consyste/documento?nota=322&tipo=xml")
    assert download.status_code == 403


def test_financeiro_contas_receber_page_disponivel_para_fiscal(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()
    set_logged_user(client, "fiscal_teste", "Fiscal")

    response = client.get("/financeiro/contas-receber")
    assert response.status_code == 200
    assert b"Contas a Receber" in response.data


def test_portaria_sem_acesso_a_financeiro_contas_receber(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()
    login_portaria(client, app)

    response = client.get("/financeiro/contas-receber")
    assert response.status_code == 403


def test_fiscal_sem_acesso_a_financeiro_faturamento(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()
    set_logged_user(client, "fiscal_teste", "Fiscal")

    response = client.get("/financeiro/faturamento")
    assert response.status_code == 403


def test_api_financeiro_contas_receber_lista_so_nota_com_pagamento_xml(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()
    set_logged_user(client, "fiscal_teste", "Fiscal")

    with app.app_context():
        db.session.add(
            ItemNota(
                numero_nota="CR100",
                fornecedor="Fornecedor CR",
                codigo="CR-1",
                descricao="Item com pagamento",
                qtd_real=1.0,
                status="Lançado",
                pagamento_xml=True,
                tipo_pagamento_xml="01",
                valor_pagamento_xml=150.75,
            )
        )
        db.session.add(
            ItemNota(
                numero_nota="CR101",
                fornecedor="Fornecedor sem pagamento",
                codigo="CR-2",
                descricao="Item sem pagamento",
                qtd_real=1.0,
                status="Lançado",
                pagamento_xml=False,
            )
        )
        db.session.commit()

    response = client.get("/api/financeiro/contas-receber/notas")
    assert response.status_code == 200
    data = response.get_json()
    assert data["total"] == 1
    assert data["itens"][0]["numero_nota"] == "CR100"
    assert data["itens"][0]["boleto_gerado"] is False


def test_api_financeiro_gerar_boleto_e_mostrar_na_lista(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()
    set_logged_user(client, "fiscal_teste", "Fiscal")

    with app.app_context():
        db.session.add(
            ItemNota(
                numero_nota="CR200",
                fornecedor="Fornecedor boleto",
                codigo="CR-3",
                descricao="Item boleto",
                qtd_real=1.0,
                status="Lançado",
                pagamento_xml=True,
                tipo_pagamento_xml="15",
                valor_pagamento_xml=500.00,
            )
        )
        db.session.commit()

    gera = client.post("/api/financeiro/contas-receber/gerar-boleto", json={"nota": "CR200"})
    assert gera.status_code == 200
    payload = gera.get_json()
    assert payload["sucesso"] is True
    assert payload["boleto"]["banco"] == "BOFA - Bank of America"

    with app.app_context():
        boleto = BoletoContaReceber.query.filter_by(numero_nota="CR200").first()
        assert boleto is not None

    lista = client.get("/api/financeiro/contas-receber/notas")
    assert lista.status_code == 200
    data = lista.get_json()
    item = next((x for x in data["itens"] if x["numero_nota"] == "CR200"), None)
    assert item is not None
    assert item["boleto_gerado"] is True


def test_api_consyste_emissao_solicitar_bloqueada_para_admin(tmp_path):
    app = build_test_app(tmp_path)
    app.config["CONSYSTE_TOKEN"] = "token_teste_valido"
    client = app.test_client()
    login_admin(client)

    response = client.post(
        "/api/consyste/emissao/solicitar",
        json={
            "ambiente": 2,
            "cnpj": "88309136000129",
            "txt_payload": "NOTAFISCAL|1\nA|3.10||\nB|35||VENDA",
        },
    )
    assert response.status_code == 403



def test_api_consyste_emissao_consultar_bloqueada_para_admin(tmp_path):
    app = build_test_app(tmp_path)
    app.config["CONSYSTE_TOKEN"] = "token_teste_valido"
    client = app.test_client()
    login_admin(client)

    response = client.post(
        "/api/consyste/emissao/consultar",
        json={
            "ambiente": 2,
            "emissao_id": "62b080477bbe81e57e06b5bc",
        },
    )
    assert response.status_code == 403


def test_importacao_xml_ignora_cfop_5902_quando_nf_tem_cfop_5124(tmp_path):
    app = build_test_app(tmp_path)

    xml_bytes = build_test_nfe_xml(
        "4001",
        [
            {"codigo": "RET1", "descricao": "Linha retorno", "cfop": "5902", "quantidade": "1.0000"},
            {"codigo": "VEN1", "descricao": "Linha conferivel", "cfop": "5124", "quantidade": "2.0000"},
        ],
    )

    with app.app_context():
        assert process_xml_and_store(xml_bytes, "admin", status_inicial="Pendente") == 1
        db.session.commit()
        itens = ItemNota.query.filter_by(numero_nota="4001").order_by(ItemNota.codigo.asc()).all()

    assert len(itens) == 1
    assert itens[0].codigo == "VEN1"
    assert itens[0].cfop == "5124"


def test_api_itens_ignora_linha_cfop_5902_quando_nf_tem_5124(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()
    login_admin(client)

    with app.app_context():
        db.session.add(
            ItemNota(
                numero_nota="4002",
                fornecedor="Fornecedor CFOP",
                codigo="RET2",
                descricao="Linha retorno sem conferencia",
                cfop="5902",
                qtd_real=1.0,
                status="Pendente",
            )
        )
        db.session.add(
            ItemNota(
                numero_nota="4002",
                fornecedor="Fornecedor CFOP",
                codigo="VEN2",
                descricao="Linha principal conferivel",
                cfop="5125",
                qtd_real=3.0,
                status="Pendente",
            )
        )
        db.session.commit()

    response = client.get("/api/itens/4002")

    assert response.status_code == 200
    data = response.get_json()
    assert len(data) == 1
    assert data[0]["codigo"] == "VEN2"


def test_validar_nf_considera_apenas_linha_cfop_5124_ou_5125(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()
    login_admin(client)

    with app.app_context():
        db.session.add(
            ItemNota(
                numero_nota="4003",
                fornecedor="Fornecedor Validacao CFOP",
                codigo="RET3",
                descricao="Linha 5902 ignorada",
                cfop="5902",
                qtd_real=1.0,
                status="Pendente",
            )
        )
        item_valido = ItemNota(
            numero_nota="4003",
            fornecedor="Fornecedor Validacao CFOP",
            codigo="VEN3",
            descricao="Linha 5124 conferivel",
            cfop="5124",
            qtd_real=5.0,
            status="Pendente",
        )
        db.session.add(item_valido)
        db.session.commit()
        item_id = item_valido.id

    response = client.post(
        "/validar",
        json={"nota": "4003", "contagens": {str(item_id): "5"}},
    )

    assert response.status_code == 200
    data = response.get_json()
    assert data["resumo"]["total_itens"] == 1
    assert data["resumo"]["ok"] == 1
    assert data["resumo"]["divergencias"] == 0


def test_pagina_e_api_de_notas_liberadas_estao_disponiveis_para_todos_os_perfis(tmp_path):
    app = build_test_app(tmp_path)

    with app.app_context():
        db.session.add(
            ItemNota(
                numero_nota="999",
                fornecedor="Fornecedor Liberado",
                codigo="LIB999",
                descricao="Item liberado para consulta geral",
                qtd_real=1.0,
                status="Concluído",
            )
        )
        db.session.commit()

    for role in ("Admin", "Fiscal", "Conferente", "Portaria"):
        client = app.test_client()
        set_logged_user(client, f"usuario_{role.lower()}", role)

        pagina = client.get("/fiscal/liberadas")
        assert pagina.status_code == 200, role

        consulta = client.get("/api/fiscal/notas_liberadas")
        assert consulta.status_code == 200, role
        assert any(item["numero"] == "999" for item in consulta.get_json()), role


def test_api_notas_liberadas_retorna_historico_de_estorno(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()
    login_admin(client)

    with app.app_context():
        db.session.add(
            ItemNota(
                numero_nota="654",
                fornecedor="Fornecedor Liberado",
                codigo="LIB1",
                descricao="Item liberado",
                qtd_real=7.0,
                status="Concluído",
                chave_acesso="98765432109876543210987654321098765432109876",
            )
        )
        db.session.add(
            LogEstornoLancamento(
                numero_nota="654",
                usuario_estorno="admin",
                motivo="Liberado para reprocessamento",
            )
        )
        db.session.commit()

    response = client.get("/api/fiscal/notas_liberadas")
    assert response.status_code == 200
    data = response.get_json()
    assert isinstance(data, list)
    nota = next((item for item in data if item["numero"] == "654"), None)
    assert nota is not None
    assert nota["status_atual"] == "Concluído"
    assert nota["documentos_disponiveis"] is True


def test_api_notas_liberadas_lista_nf_concluida_sem_estorno(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()
    login_admin(client)

    with app.app_context():
        db.session.add(
            ItemNota(
                numero_nota="7777",
                fornecedor="Fornecedor Liberado Fiscal",
                codigo="FISC1",
                descricao="Item concluido",
                qtd_real=9.0,
                status="Concluído",
                usuario_conferencia="conferente.teste",
                chave_acesso="11112222333344445555666677778888999900001111",
            )
        )
        db.session.commit()

    response = client.get("/api/fiscal/notas_liberadas")
    assert response.status_code == 200
    data = response.get_json()
    nota = next((item for item in data if item["numero"] == "7777"), None)
    assert nota is not None
    assert nota["liberado_por"] == "conferente.teste"
    assert nota["status_atual"] == "Concluído"


def test_api_notas_liberadas_ordena_pela_ultima_acao(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()
    login_admin(client)

    with app.app_context():
        db.session.add(
            ItemNota(
                numero_nota="9001",
                fornecedor="Fornecedor Antigo",
                codigo="OLD",
                descricao="Item antigo",
                qtd_real=1.0,
                status="Concluído",
                usuario_conferencia="conf.antigo",
                fim_conferencia=datetime(2026, 3, 10, 8, 0, 0),
                chave_acesso="90019001900190019001900190019001900190019001",
            )
        )
        db.session.add(
            ItemNota(
                numero_nota="9002",
                fornecedor="Fornecedor Recente",
                codigo="NEW",
                descricao="Item recente",
                qtd_real=1.0,
                status="Lançado",
                usuario_conferencia="conf.recente",
                usuario_lancamento="fiscal.recente",
                fim_conferencia=datetime(2026, 3, 10, 8, 0, 0),
                data_lancamento=datetime(2026, 3, 12, 9, 30, 0),
                numero_lancamento="ERP-RECENTE",
                chave_acesso="90029002900290029002900290029002900290029002",
            )
        )
        db.session.commit()

    response = client.get("/api/fiscal/notas_liberadas")
    assert response.status_code == 200
    data = response.get_json()
    notas_teste = [item["numero"] for item in data if item["numero"] in {"9001", "9002"}]
    assert notas_teste[:2] == ["9002", "9001"]


def test_excluir_nota_pendente_exige_confirmacao_e_motivo(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()
    login_admin(client)

    with app.app_context():
        db.session.add(
            ItemNota(
                numero_nota="555",
                fornecedor="Fornecedor Z",
                codigo="GHI",
                descricao="Item pendente",
                qtd_real=3.0,
                status="Pendente",
            )
        )
        db.session.commit()

    # Confirmacao incorreta
    response = client.post(
        "/api/excluir_nota_pendente",
        json={"nota": "555", "confirmacao_nota": "000", "motivo": "duplicada"},
    )
    assert response.status_code == 400

    # Sucesso com confirmacao e motivo
    response = client.post(
        "/api/excluir_nota_pendente",
        json={"nota": "555", "confirmacao_nota": "555", "motivo": "duplicada na carga"},
    )
    assert response.status_code == 200
    assert response.get_json()["sucesso"] is True


def test_api_admin_acessos_retorna_paginacao(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()
    login_admin(client)

    response = client.get("/api/admin/acessos?page=1&per_page=20")
    assert response.status_code == 200
    data = response.get_json()
    assert "items" in data
    assert "page" in data
    assert "per_page" in data
    assert "total" in data
    assert "pages" in data


def test_api_sla_dashboard_retorna_estrutura(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()
    login_admin(client)

    response = client.get("/api/sla_dashboard")
    assert response.status_code == 200
    data = response.get_json()
    assert "pendentes" in data
    assert "fiscal" in data
    assert "em_risco" in data
    assert "fornecedores_criticos" in data
    assert "resumo" in data


def test_api_processo_recebimento_painel_retorna_estrutura(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()
    login_admin(client)

    with app.app_context():
        db.session.add(
            ItemNota(
                numero_nota="PROC100",
                fornecedor="Fornecedor Processo",
                codigo="PROC-1",
                descricao="Item processo",
                qtd_real=2.0,
                status="AguardandoLiberacao",
                data_importacao=datetime.now(),
            )
        )
        db.session.commit()

    response = client.get("/api/processo/recebimento_painel?dias=30&limite_fila=10")
    assert response.status_code == 200
    data = response.get_json()
    assert "janela_dias" in data
    assert "etapas" in data
    assert "kpis" in data
    assert "fila_excecao" in data


def test_api_processo_recebimento_painel_bloqueia_portaria(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()
    login_portaria(client, app)

    response = client.get("/api/processo/recebimento_painel")
    assert response.status_code == 403


def test_api_pendentes_priorizadas_retorna_escala_0_a_5(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()
    login_admin(client)

    with app.app_context():
        db.session.add(
            ItemNota(
                numero_nota="888",
                fornecedor="Fornecedor Prioridade",
                codigo="PRIO",
                descricao="Item prioridade",
                qtd_real=1.0,
                status="Pendente",
            )
        )
        db.session.commit()

    response = client.get("/api/pendentes_priorizadas")
    assert response.status_code == 200
    data = response.get_json()
    assert isinstance(data, list)
    assert len(data) >= 1
    item = next((x for x in data if x.get("numero") == "888"), None)
    assert item is not None
    assert "prioridade_nivel" in item
    assert 0 <= int(item["prioridade_nivel"]) <= 5
    assert "idade_label" in item


def test_historico_mostra_nf_excluida(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()
    login_admin(client)

    with app.app_context():
        db.session.add(
            ItemNota(
                numero_nota="777",
                fornecedor="Fornecedor Excluido",
                codigo="JKL",
                descricao="Item pendente historico",
                qtd_real=4.0,
                status="Pendente",
            )
        )
        db.session.commit()

    response = client.post(
        "/api/excluir_nota_pendente",
        json={"nota": "777", "confirmacao_nota": "777", "motivo": "teste de exclusao"},
    )
    assert response.status_code == 200

    hist = client.get("/api/historico_completo?nota=777")
    assert hist.status_code == 200
    data = hist.get_json()
    assert isinstance(data, list)
    assert len(data) == 1
    assert data[0]["nota"] == "777"
    assert data[0]["status"] == "Excluída"


def test_validar_retorna_resumo_de_conferencia(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()
    login_admin(client)

    with app.app_context():
        item = ItemNota(
            numero_nota="321",
            fornecedor="Fornecedor Resumo",
            codigo="AAA",
            descricao="Item resumo",
            qtd_real=5.0,
            status="Pendente",
        )
        db.session.add(item)
        db.session.commit()
        item_id = item.id

    response = client.post(
        "/validar",
        json={"nota": "321", "contagens": {str(item_id): "5"}},
    )
    assert response.status_code == 200
    data = response.get_json()
    assert "resumo" in data
    assert data["resumo"]["total_itens"] == 1
    assert data["resumo"]["ok"] == 1
    assert data["resumo"]["divergencias"] == 0


def test_validar_aceita_tolerancia_de_2_porcento_para_kg(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()
    login_admin(client)

    with app.app_context():
        item = ItemNota(
            numero_nota="322",
            fornecedor="Fornecedor KG",
            codigo="KG1",
            descricao="Item pesado",
            qtd_real=10.0,
            unidade_comercial="KG",
            status="Pendente",
        )
        db.session.add(item)
        db.session.commit()
        item_id = item.id

    response = client.post(
        "/validar",
        json={"nota": "322", "contagens": {str(item_id): "10.2"}},
    )

    assert response.status_code == 200
    data = response.get_json()
    assert data["sucesso"] is True
    assert data["resumo"]["ok"] == 1
    assert data["resumo"]["divergencias"] == 0


def test_validar_aceita_tolerancia_de_2_porcento_para_mm(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()
    login_admin(client)

    with app.app_context():
        item = ItemNota(
            numero_nota="323",
            fornecedor="Fornecedor MM",
            codigo="MM1",
            descricao="Item milimetrado",
            qtd_real=100.0,
            unidade_comercial="MM",
            status="Pendente",
        )
        db.session.add(item)
        db.session.commit()
        item_id = item.id

    response = client.post(
        "/validar",
        json={"nota": "323", "contagens": {str(item_id): "98"}},
    )

    assert response.status_code == 200
    data = response.get_json()
    assert data["sucesso"] is True
    assert data["resumo"]["ok"] == 1
    assert data["resumo"]["divergencias"] == 0


def test_validar_mantem_divergencia_fora_da_tolerancia_de_kg(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()
    login_admin(client)

    with app.app_context():
        item = ItemNota(
            numero_nota="324",
            fornecedor="Fornecedor KG Fora",
            codigo="KG2",
            descricao="Item pesado fora da tolerancia",
            qtd_real=10.0,
            unidade_comercial="KG",
            status="Pendente",
        )
        db.session.add(item)
        db.session.commit()
        item_id = item.id

    response = client.post(
        "/validar",
        json={"nota": "324", "contagens": {str(item_id): "10.21"}},
    )

    assert response.status_code == 200
    data = response.get_json()
    assert data["sucesso"] is False
    assert data["resumo"]["ok"] == 0
    assert data["resumo"]["divergencias"] == 1


def test_validar_bloqueia_conclusao_sem_motivo_de_divergencia(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()
    login_admin(client)

    with app.app_context():
        item = ItemNota(
            numero_nota="654",
            fornecedor="Fornecedor Divergente",
            codigo="BBB",
            descricao="Item divergente",
            qtd_real=10.0,
            status="Pendente",
        )
        db.session.add(item)
        db.session.commit()
        item_id = item.id

    response = client.post(
        "/validar",
        json={
            "nota": "654",
            "contagens": {str(item_id): "7"},
            "forcar_pendencia": True,
            "motivos_itens": {},
            "checklist": {
                "lacre_ok": True,
                "volumes_ok": True,
                "avaria_visual": True,
                "etiqueta_ok": True,
            },
        },
    )
    assert response.status_code == 400


def test_api_expedicao_conferencia_lista_html_e_pasta_de_imagens(tmp_path):
    reports_dir = tmp_path / "eReports"
    reports_dir.mkdir(parents=True, exist_ok=True)

    html_file = reports_dir / "Relatorio de conferencia - Columbia 6mm 310ss.HTML"
    html_file.write_text("<html><body>teste</body></html>", encoding="utf-8")

    image_folder = reports_dir / "Relatorio de conferencia - Columbia 6mm 310ss.files"
    image_folder.mkdir(parents=True, exist_ok=True)
    (image_folder / "img0.png").write_bytes(b"PNG")
    (image_folder / "img1.jpg").write_bytes(b"JPG")

    app = create_app(
        {
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'test.db'}",
            "EXPEDICAO_REPORTS_DIR": str(reports_dir),
        }
    )
    client = app.test_client()
    login_admin(client)

    response = client.get("/api/expedicao/conferencia/relatorios")

    assert response.status_code == 200
    data = response.get_json()
    assert data["exists"] is True
    assert data["total"] == 1
    assert data["reports"][0]["file_name"] == "Relatorio de conferencia - Columbia 6mm 310ss.HTML"
    assert data["reports"][0]["image_folder_exists"] is True
    assert data["reports"][0]["images_count"] == 2


def test_api_expedicao_conferencia_abre_relatorio_e_valida_cego(tmp_path):
    reports_dir = tmp_path / "eReports"
    reports_dir.mkdir(parents=True, exist_ok=True)

    html_file = reports_dir / "Relatorio de conferencia - Columbia 6mm 310ss.HTML"
    html_file.write_text(
        """
        <html><body>
        <img src="Relatorio de conferencia - Columbia 6mm 310ss.files/img2.png"/>
        <td class="s13">25-FOT78-ALT-3227</td>
        <td class="s15">OS 7726 - 6409</td>
        <td class="s16">40,00</td>
        <td class="s17">X 100,00</td>
        <td class="s13">6</td>
        <td class="s15">ALUMITA</td>
        </body></html>
        """,
        encoding="utf-8",
    )

    image_folder = reports_dir / "Relatorio de conferencia - Columbia 6mm 310ss.files"
    image_folder.mkdir(parents=True, exist_ok=True)
    (image_folder / "img2.png").write_bytes(b"PNG")

    app = create_app(
        {
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'test.db'}",
            "EXPEDICAO_REPORTS_DIR": str(reports_dir),
        }
    )
    client = app.test_client()
    login_admin(client)

    detalhe = client.get("/api/expedicao/conferencia/relatorio", query_string={"file_name": html_file.name})
    assert detalhe.status_code == 200
    detalhe_data = detalhe.get_json()
    assert detalhe_data["total_items"] == 1
    assert detalhe_data["items"][0]["nome_peca"] == "25-FOT78-ALT-3227"
    assert detalhe_data["items"][0]["qtd_esperada"] == 6

    validacao_ok = client.post(
        "/api/expedicao/conferencia/validar",
        json={"file_name": html_file.name, "contagens": {"0": 6}},
    )
    assert validacao_ok.status_code == 200
    validacao_ok_data = validacao_ok.get_json()
    assert validacao_ok_data["total_divergencias"] == 0

    validacao_div = client.post(
        "/api/expedicao/conferencia/validar",
        json={"file_name": html_file.name, "contagens": {"0": 4}},
    )
    assert validacao_div.status_code == 409
    validacao_div_data = validacao_div.get_json()
    assert validacao_div_data.get("bloqueio_quantidade") is True


def test_expedicao_faturamento_parcial_total_e_estorno_admin(tmp_path):
    reports_dir = tmp_path / "eReports"
    reports_dir.mkdir(parents=True, exist_ok=True)

    html_file = reports_dir / "Relatorio de conferencia - Columbia 5mm 310ss.HTML"
    html_file.write_text(
        """
        <html><body>
        <img src="Relatorio de conferencia - Columbia 5mm 310ss.files/img2.png"/>
        <td class="s13">25-FOT78-ALT-3019</td>
        <td class="s15">OS 7558 - 6409</td>
        <td class="s16">46,00</td>
        <td class="s17">X 100,00</td>
        <td class="s13">5</td>
        <td class="s15">ALUMITA</td>

        <img src="Relatorio de conferencia - Columbia 5mm 310ss.files/img3.png"/>
        <td class="s13">25-FOT78-ALT-3022</td>
        <td class="s15">OS 7559 - 6409</td>
        <td class="s16">46,00</td>
        <td class="s17">X 120,00</td>
        <td class="s13">3</td>
        <td class="s15">ALUMITA</td>
        </body></html>
        """,
        encoding="utf-8",
    )

    image_folder = reports_dir / "Relatorio de conferencia - Columbia 5mm 310ss.files"
    image_folder.mkdir(parents=True, exist_ok=True)
    (image_folder / "img2.png").write_bytes(b"PNG")
    (image_folder / "img3.png").write_bytes(b"PNG")

    app = create_app(
        {
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'test.db'}",
            "EXPEDICAO_REPORTS_DIR": str(reports_dir),
        }
    )
    client = app.test_client()
    login_admin(client)

    # Inicializa conferencia persistida
    detalhe = client.get("/api/expedicao/conferencia/relatorio", query_string={"file_name": html_file.name})
    assert detalhe.status_code == 200
    detalhe_data = detalhe.get_json()
    assert detalhe_data["total_items"] == 2
    conferencia_id = detalhe_data["conferencia_id"]

    # Conferencia cega
    validacao = client.post(
        "/api/expedicao/conferencia/validar",
        json={"file_name": html_file.name, "contagens": {"0": 5, "1": 3}},
    )
    assert validacao.status_code == 200

    item_a = detalhe_data["items"][0]
    item_b = detalhe_data["items"][1]

    # Faturamento parcial: deve continuar aberta
    parcial = client.post(
        "/api/expedicao/faturamento",
        json={
            "file_name": html_file.name,
            "numero_nf": "123",
            "tipo": "Parcial",
            "itens": [
                {"item_id": item_a["id"], "qtd_enviada": 2, "foto_path": "foto_a.jpg"},
            ],
        },
    )
    assert parcial.status_code == 200
    assert parcial.get_json()["status"] == "Aberta"

    # Faturamento total do restante: deve fechar
    fechamento = client.post(
        "/api/expedicao/faturamento",
        json={
            "file_name": html_file.name,
            "numero_nf": "124",
            "tipo": "Total",
            "itens": [
                {"item_id": item_a["id"], "qtd_enviada": 3, "foto_path": "foto_a2.jpg"},
                {"item_id": item_b["id"], "qtd_enviada": 3, "foto_path": "foto_b.jpg"},
            ],
        },
    )
    assert fechamento.status_code == 200
    assert fechamento.get_json()["status"] == "Fechada"

    # Estorno total admin: deve reabrir
    estorno = client.post(
        "/api/admin/expedicao/estornar_total",
        json={"conferencia_id": conferencia_id, "motivo": "Teste estorno"},
    )
    assert estorno.status_code == 200
    assert estorno.get_json()["sucesso"] is True

    detalhe_pos_estorno = client.get(
        "/api/expedicao/conferencia/relatorio",
        query_string={"file_name": html_file.name},
    )
    assert detalhe_pos_estorno.status_code == 200
    assert detalhe_pos_estorno.get_json()["status"] == "Aberta"


def test_validar_com_pendencia_retorna_instrucoes_operacionais(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()
    login_admin(client)

    with app.app_context():
        item = ItemNota(
            numero_nota="901",
            fornecedor="Fornecedor Pendencia",
            codigo="PP1",
            descricao="Item com divergencia",
            qtd_real=10.0,
            status="Pendente",
        )
        db.session.add(item)
        db.session.commit()
        item_id = item.id

    response = client.post(
        "/validar",
        json={
            "nota": "901",
            "contagens": {str(item_id): "7"},
            "forcar_pendencia": True,
            "motivos_itens": {str(item_id): "Falta de item"},
            "motivos_tipos": {str(item_id): "Falta de item"},
            "destinos_itens": {str(item_id): "Quarentena"},
            "checklist": {
                "lacre_ok": True,
                "volumes_ok": True,
                "avaria_visual": True,
                "etiqueta_ok": True,
            },
        },
    )
    assert response.status_code == 200
    data = response.get_json()
    assert data["pendencia_confirmada"] is True
    assert "instrucoes_pendencia" in data
    assert len(data["instrucoes_pendencia"].get("passos", [])) > 0


def test_validar_com_pendencia_sem_destino_manual_ainda_conclui(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()
    login_admin(client)

    with app.app_context():
        item = ItemNota(
            numero_nota="902",
            fornecedor="Fornecedor Sem Destino",
            codigo="PP2",
            descricao="Item divergente sem destino",
            qtd_real=8.0,
            status="Pendente",
        )
        db.session.add(item)
        db.session.commit()
        item_id = item.id

    response = client.post(
        "/validar",
        json={
            "nota": "902",
            "contagens": {str(item_id): "6"},
            "forcar_pendencia": True,
            "motivos_itens": {str(item_id): "Falta de item: faltaram 2 volumes"},
            "motivos_tipos": {str(item_id): "Falta de item"},
            "checklist": {
                "lacre_ok": True,
                "volumes_ok": True,
                "avaria_visual": True,
                "etiqueta_ok": True,
            },
        },
    )

    assert response.status_code == 200
    data = response.get_json()
    assert data["pendencia_confirmada"] is True


def test_validar_com_pendencia_aceita_motivo_tipo_e_observacao_separados(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()
    login_admin(client)

    with app.app_context():
        item = ItemNota(
            numero_nota="904",
            fornecedor="Fornecedor Pendencia Separada",
            codigo="PP3",
            descricao="Item pendencia com campos separados",
            qtd_real=8.0,
            status="Pendente",
        )
        db.session.add(item)
        db.session.commit()
        item_id = item.id

    response = client.post(
        "/validar",
        json={
            "nota": "904",
            "contagens": {str(item_id): "6"},
            "forcar_pendencia": True,
            "motivos_tipos": {str(item_id): "Falta de item"},
            "motivos_observacoes": {str(item_id): "faltaram 2 volumes"},
            "checklist": {
                "lacre_ok": True,
                "volumes_ok": True,
                "avaria_visual": True,
                "etiqueta_ok": True,
            },
        },
    )

    assert response.status_code == 200
    data = response.get_json()
    assert data["pendencia_confirmada"] is True
    with app.app_context():
        log = LogDivergencia.query.filter_by(numero_nota="904").first()
        assert log is not None
        assert "Falta de item" in log.item_descricao


def test_solicitacao_devolucao_recebimento_exige_aprovacao_admin(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()
    login_admin(client)

    with app.app_context():
        db.session.add(
            ItemNota(
                numero_nota="903",
                fornecedor="Fornecedor Devolucao",
                codigo="DEV1",
                descricao="Item devolucao",
                qtd_real=3.0,
                status="Pendente",
                chave_acesso="90309030903090309030903090309030903090309030",
            )
        )
        db.session.commit()

    solicitar = client.post(
        "/api/recebimento/solicitar_devolucao",
        json={"nota": "903", "motivo": "Carga recusada por divergencia total"},
    )
    assert solicitar.status_code == 200
    assert solicitar.get_json()["sucesso"] is True

    with app.app_context():
        item = ItemNota.query.filter_by(numero_nota="903").first()
        solicitacao = SolicitacaoDevolucaoRecebimento.query.filter_by(numero_nota="903", ativa=True).first()
        assert item.status == "AguardandoDevolucao"
        assert solicitacao is not None
        solicitacao_id = solicitacao.id

    lista = client.get("/api/admin/recebimento/solicitacoes_devolucao")
    assert lista.status_code == 200
    registros = lista.get_json()
    assert any(row["numero"] == "903" for row in registros)

    with patch(
        "conferencia_app.routes.api_routes.manifestar_destinatario_consyste",
        return_value=(True, 200, {"protocolo": "903123"}),
    ), patch(
        "conferencia_app.routes.api_routes.enviar_decisao_consyste",
        return_value=(True, 200, {}),
    ):
        aprovar = client.post(
            "/api/admin/recebimento/aprovar_devolucao",
            json={"solicitacao_id": solicitacao_id, "observacao_admin": "Autorizado pela gerencia"},
        )
    assert aprovar.status_code == 200
    assert aprovar.get_json()["sucesso"] is True

    with app.app_context():
        item = ItemNota.query.filter_by(numero_nota="903").first()
        solicitacao = SolicitacaoDevolucaoRecebimento.query.filter_by(id=solicitacao_id).first()
        assert item.status == "Devolvido"
        assert solicitacao.status == "Aprovada"
        assert solicitacao.ativa is False


def test_aprovar_devolucao_recebimento_falha_se_consyste_recusar(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()
    login_admin(client)

    with app.app_context():
        db.session.add(
            ItemNota(
                numero_nota="905",
                fornecedor="Fornecedor Devolucao Falha",
                codigo="DEV5",
                descricao="Item devolucao falha",
                qtd_real=2.0,
                status="AguardandoDevolucao",
                chave_acesso="90509050905090509050905090509050905090509050",
            )
        )
        db.session.add(
            SolicitacaoDevolucaoRecebimento(
                numero_nota="905",
                fornecedor="Fornecedor Devolucao Falha",
                chave_acesso="90509050905090509050905090509050905090509050",
                usuario_solicitante="admin",
                motivo="Recusa total",
                status="Pendente",
                ativa=True,
            )
        )
        db.session.commit()
        solicitacao_id = SolicitacaoDevolucaoRecebimento.query.filter_by(numero_nota="905").first().id

    with patch(
        "conferencia_app.routes.api_routes.manifestar_destinatario_consyste",
        return_value=(True, 200, {"protocolo": "905123"}),
    ), patch(
        "conferencia_app.routes.api_routes.enviar_decisao_consyste",
        return_value=(False, 502, {"error": "Consyste fora"}),
    ):
        response = client.post(
            "/api/admin/recebimento/aprovar_devolucao",
            json={"solicitacao_id": solicitacao_id, "observacao_admin": "Aprovado"},
        )

    assert response.status_code == 502
    data = response.get_json()
    assert data["sucesso"] is False
    assert "Consyste fora" in data["msg"]

    with app.app_context():
        item = ItemNota.query.filter_by(numero_nota="905").first()
        solicitacao = SolicitacaoDevolucaoRecebimento.query.filter_by(id=solicitacao_id).first()
        assert item.status == "AguardandoDevolucao"
        assert solicitacao.status == "Pendente"
        assert solicitacao.ativa is True


def test_aprovar_devolucao_recebimento_manifesta_operacao_nao_realizada(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()
    login_admin(client)

    with app.app_context():
        db.session.add(
            ItemNota(
                numero_nota="906",
                fornecedor="Fornecedor Manifestacao Devolucao",
                codigo="DEV6",
                descricao="Item devolucao manifestado",
                qtd_real=2.0,
                status="AguardandoDevolucao",
                chave_acesso="90609060906090609060906090609060906090609060",
            )
        )
        db.session.add(
            SolicitacaoDevolucaoRecebimento(
                numero_nota="906",
                fornecedor="Fornecedor Manifestacao Devolucao",
                chave_acesso="90609060906090609060906090609060906090609060",
                usuario_solicitante="admin",
                motivo="Mercadoria recusada no recebimento",
                status="Pendente",
                ativa=True,
            )
        )
        db.session.commit()
        solicitacao_id = SolicitacaoDevolucaoRecebimento.query.filter_by(numero_nota="906").first().id

    with patch(
        "conferencia_app.routes.api_routes.manifestar_destinatario_consyste",
        return_value=(True, 200, {"protocolo": "1234567890"}),
    ) as manifest_mock, patch(
        "conferencia_app.routes.api_routes.enviar_decisao_consyste",
        return_value=(True, 200, {}),
    ):
        response = client.post(
            "/api/admin/recebimento/aprovar_devolucao",
            json={"solicitacao_id": solicitacao_id, "observacao_admin": "Aprovado"},
        )

    assert response.status_code == 200
    data = response.get_json()
    assert data["sucesso"] is True
    manifest_mock.assert_called_once_with(
        "90609060906090609060906090609060906090609060",
        manifestacao="operacao_nao_realizada",
        justificativa="Mercadoria recusada no recebimento",
    )

    with app.app_context():
        log = LogManifestacaoDestinatario.query.filter_by(numero_nota="906").order_by(LogManifestacaoDestinatario.id.desc()).first()
        assert log is not None
        assert log.manifestacao == "operacao_nao_realizada"
        assert log.status == "Sucesso"
