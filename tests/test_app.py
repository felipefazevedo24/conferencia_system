import io
import hashlib
import hmac
from datetime import datetime, timedelta
import sqlite3
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch

from flask import session
from sqlalchemy import event
from sqlalchemy.exc import OperationalError

from conferencia_app import create_app
from conferencia_app.bootstrap import initialize_database
from conferencia_app.auth import check_active_session
from conferencia_app.extensions import db
from conferencia_app.models import (
    BoletoContaReceber,
    ClassificacaoContabilItem,
    ClassificacaoContabilCompetencia,
    ClassificacaoContabilPadrao,
    ExpedicaoConferenciaSimples,
    ExpedicaoConferenciaSimplesEstorno,
    ExpedicaoConferenciaSimplesFoto,
    EmailNFEnviado,
    ItemNota,
    PlanoContaDominio,
    LogDivergencia,
    LogEstornoLancamento,
    LogEventoFiscalNota,
    LogManifestacaoDestinatario,
    SolicitacaoDevolucaoRecebimento,
    AgendamentoVeiculo,
    Viagem,
    ViagemParada,
)
from conferencia_app.services.xml_service import process_xml_and_store
from werkzeug.security import check_password_hash, generate_password_hash


def build_test_app(tmp_path):
    db_path = tmp_path / "test.db"
    return create_app(
        {
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{db_path}",
        }
    )


def test_alembic_env_works_with_app_context(tmp_path):
    app = build_test_app(tmp_path)
    with app.app_context():
        import importlib

        env_module = importlib.import_module("migrations.env")
        assert env_module.get_engine_url()
        assert env_module.target_db is not None


def enable_sqlite_foreign_keys(app):
    with app.app_context():
        engine = db.engine
        if engine.dialect.name != "sqlite" or engine.info.get("foreign_keys_enabled_for_tests"):
            return

        @event.listens_for(engine, "connect")
        def _set_sqlite_pragma(dbapi_connection, _connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys = ON")
            cursor.close()

        engine.info["foreign_keys_enabled_for_tests"] = True
        db.session.remove()
        engine.dispose()


def login_admin(client):
    return client.post("/login", json={"username": "admin", "password": "admin1234"})


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


def build_test_nfe_xml(
    numero_nota,
    itens,
    fornecedor="Fornecedor XML",
    cnpj_emitente="",
    chave_acesso="12345678901234567890123456789012345678901234",
):
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
                <infNFe Id="NFe{chave_acesso}">
                    <ide><nNF>{numero_nota}</nNF></ide>
                    <emit>
                        <xNome>{fornecedor}</xNome>
                        {f"<CNPJ>{cnpj_emitente}</CNPJ>" if cnpj_emitente else ""}
                    </emit>
                    {itens_xml}
                    <total><ICMSTot><vNF>100.00</vNF><vICMS>18.00</vICMS></ICMSTot></total>
                </infNFe>
            </NFe>
        </nfeProc>
        """.encode("utf-8")


def build_test_nfse_xml(numero_nota="7001", prestador="Prestador Servicos", tomador="Tomador Teste"):
        return f"""
        <CompNfse xmlns="http://www.abrasf.org.br/nfse.xsd">
            <Nfse>
                <InfNfse Id="NFSE{numero_nota}">
                    <Numero>{numero_nota}</Numero>
                    <CodigoVerificacao>ABCD1234</CodigoVerificacao>
                    <ValoresNfse>
                        <ValorLiquidoNfse>250.00</ValorLiquidoNfse>
                    </ValoresNfse>
                    <PrestadorServico>
                        <RazaoSocial>{prestador}</RazaoSocial>
                        <IdentificacaoPrestador>
                            <Cnpj>11222333000181</Cnpj>
                        </IdentificacaoPrestador>
                    </PrestadorServico>
                    <TomadorServico>
                        <RazaoSocial>{tomador}</RazaoSocial>
                        <IdentificacaoTomador>
                            <CpfCnpj>
                                <Cnpj>55444333000199</Cnpj>
                            </CpfCnpj>
                        </IdentificacaoTomador>
                    </TomadorServico>
                    <Servico>
                        <Valores>
                            <ValorServicos>250.00</ValorServicos>
                        </Valores>
                        <Discriminacao>Servico de manutencao</Discriminacao>
                    </Servico>
                </InfNfse>
            </Nfse>
        </CompNfse>
        """.encode("utf-8")


def build_test_nfse_xml_sem_numero(id_externo="NFSESEMNUM123456", prestador="Prestador Sem Numero"):
        return f"""
        <CompNfse xmlns="http://www.abrasf.org.br/nfse.xsd">
            <Nfse>
                <InfNfse Id="{id_externo}">
                    <CodigoVerificacao>ZXCV7788</CodigoVerificacao>
                    <PrestadorServico>
                        <RazaoSocial>{prestador}</RazaoSocial>
                    </PrestadorServico>
                    <Servico>
                        <Valores>
                            <ValorServicos>180.50</ValorServicos>
                        </Valores>
                        <Discriminacao>Servico sem numero explicito</Discriminacao>
                    </Servico>
                </InfNfse>
            </Nfse>
        </CompNfse>
        """.encode("utf-8")


def test_login_success(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()

    response = login_admin(client)

    assert response.status_code == 200
    assert response.get_json()["sucesso"] is True


def test_aviso_atualizacao_aparece_uma_vez_por_login(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()

    login_admin(client)
    salvar = client.post(
        "/api/admin/atualizacoes",
        json={
            "titulo": "Novidades do sistema",
            "conteudo": "Pedido de compra agora vem direto do ERP.",
            "ativo": True,
        },
    )
    assert salvar.status_code == 200

    response = client.get("/api/atualizacoes/ativo")
    data = response.get_json()
    assert data["aviso"]["titulo"] == "Novidades do sistema"
    assert "ERP" in data["aviso"]["conteudo"]

    dispensar = client.post("/api/atualizacoes/dispensar")
    assert dispensar.status_code == 200
    response = client.get("/api/atualizacoes/ativo")
    assert response.get_json()["aviso"] is None

    client.get("/logout")
    login_admin(client)
    response = client.get("/api/atualizacoes/ativo")
    assert response.get_json()["aviso"]["titulo"] == "Novidades do sistema"


def test_login_invalid_password(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()

    response = client.post("/login", json={"username": "admin", "password": "errada"})

    assert response.status_code == 401
    assert response.get_json()["sucesso"] is False


def test_registrar_usuario_solicitante(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()
    set_logged_user(client, "ADMIN", "Admin")

    response = client.post(
        "/api/registrar",
        json={
            "username": "solicitante.teste",
            "email": "solicitante.teste@example.com",
            "role": "Solicitante",
        },
    )

    assert response.status_code == 200
    assert response.get_json()["sucesso"] is True

    with app.app_context():
        from conferencia_app.models import Usuario

        usuario = Usuario.query.filter_by(username="SOLICITANTE.TESTE").first()
        assert usuario is not None
        assert usuario.email == "solicitante.teste@example.com"
        assert usuario.role == "Solicitante"


def test_login_recovers_legacy_plaintext_admin_password(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()

    with app.app_context():
        from conferencia_app.models import Usuario

        admin = Usuario.query.filter_by(username="ADMIN").first()
        admin.password = "admin1234"
        db.session.commit()

    response = login_admin(client)

    assert response.status_code == 200
    assert response.get_json()["sucesso"] is True

    with app.app_context():
        from conferencia_app.models import Usuario

        admin = Usuario.query.filter_by(username="ADMIN").first()
        assert admin.password != "admin1234"
        assert check_password_hash(admin.password, "admin1234")


def test_initialize_database_resets_truncated_admin_hash(tmp_path):
    app = build_test_app(tmp_path)

    with app.app_context():
        from conferencia_app.models import Usuario

        admin = Usuario.query.filter_by(username="ADMIN").first()
        admin.password = generate_password_hash("admin1234")[:120]
        admin.role = "Conferente"
        db.session.commit()

        initialize_database(app)
        db.session.expire_all()

        admin = Usuario.query.filter_by(username="ADMIN").first()
        assert admin.role == "Admin"
        assert check_password_hash(admin.password, "admin1234")


def test_initialize_database_adds_missing_usuario_ativo_column(tmp_path):
    app = build_test_app(tmp_path)

    with app.app_context():
        from sqlalchemy import text
        from conferencia_app.models import Usuario

        db.create_all()
        with db.engine.connect() as conn:
            conn.execute(text("ALTER TABLE usuario RENAME TO usuario_legacy"))
            conn.execute(
                text(
                    "CREATE TABLE usuario ("
                    "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                    "username VARCHAR(80) NOT NULL UNIQUE, "
                    "email VARCHAR(160), "
                    "password VARCHAR(255), "
                    "role VARCHAR(20), "
                    "criado_em DATETIME DEFAULT CURRENT_TIMESTAMP"
                    ")"
                )
            )
            conn.commit()

        initialize_database(app)

        cols = {c["name"] for c in db.inspect(db.engine).get_columns("usuario")}
        assert "ativo" in cols
        assert Usuario.query.filter(Usuario.ativo.is_(True)).count() >= 0


def test_check_active_session_retries_after_operational_error(tmp_path):
    app = build_test_app(tmp_path)
    sessao_mock = Mock()
    sessao_mock.is_active = True

    with app.test_request_context("/"):
        session["session_id"] = "sessao-teste"

        with patch(
            "conferencia_app.auth._load_and_touch_active_session",
            side_effect=[
                OperationalError("SELECT 1", {"session_id": "sessao-teste"}, Exception("lost connection")),
                sessao_mock,
            ],
        ) as load_mock, patch("conferencia_app.auth._recover_db_connection") as recover_mock:
            check_active_session()

    assert load_mock.call_count == 2
    recover_mock.assert_called_once()


def test_stats_requires_authentication(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()

    response = client.get("/api/stats")

    assert response.status_code == 302
    assert "/login" in response.location


def test_validar_payload_missing_fields_returns_400(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()
    set_logged_user(client, "ADMIN", "Admin")

    response = client.post("/validar", json={"nota": "123"})

    assert response.status_code == 400
    data = response.get_json()
    assert data["error"] == "Payload inválido"
    assert "contagens" in data["details"]


def test_reverter_conferencia_requires_reason(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()
    set_logged_user(client, "ADMIN", "Admin")

    response = client.post("/api/admin/resetar_nota", json={"nota": "123"})

    assert response.status_code == 400
    data = response.get_json()
    assert data["error"] == "Payload inválido"
    assert "motivo" in data["details"]


def test_nao_permite_reverter_conferencia_de_nota_lancada(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()
    set_logged_user(client, "ADMIN", "Admin")

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


def test_detalhes_nf_diferencia_estorno_de_lancamento_e_conferencia(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()
    login_admin(client)

    with app.app_context():
        db.session.add(
            ItemNota(
                numero_nota="3000",
                fornecedor="Fornecedor Z",
                codigo="XYZ",
                descricao="Item teste 3",
                qtd_real=1.0,
                status="Concluído",
            )
        )
        db.session.add(
            LogEstornoLancamento(
                numero_nota="3000",
                usuario_estorno="admin",
                motivo="Ajuste fiscal de cadastro",
            )
        )
        db.session.add(
            LogEstornoLancamento(
                numero_nota="3000",
                usuario_estorno="admin",
                motivo="[ESTORNO CONFERÊNCIA] Contagem divergente",
            )
        )
        db.session.commit()

    response_detalhe = client.get("/api/detalhes_nf/3000")
    assert response_detalhe.status_code == 200
    detalhe = response_detalhe.get_json()
    assert detalhe["ultimo_estorno"]["tipo"] == "Estorno de conferência"
    assert detalhe["ultimo_estorno"]["motivo"] == "Contagem divergente"


def test_listas_documento_entrada_retorna_resumo_leve_por_status(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()
    login_admin(client)

    with app.app_context():
        db.session.add(
            ItemNota(
                numero_nota="3100",
                fornecedor="Fornecedor Conferido",
                codigo="CF1",
                descricao="Item conferido",
                qtd_real=1.0,
                status="Concluído",
                remessa=True,
                cfop="5124",
            )
        )
        db.session.add(
            ItemNota(
                numero_nota="3101",
                fornecedor="Fornecedor Lancado",
                codigo="LC1",
                descricao="Item lancado",
                qtd_real=1.0,
                status="Lançado",
                numero_lancamento="ERP-3101",
                usuario_lancamento="admin",
            )
        )
        db.session.add(
            LogManifestacaoDestinatario(
                numero_nota="3101",
                manifestacao="confirmada",
                status="Falha",
                detalhe="SEFAZ indisponivel",
                usuario="admin",
            )
        )
        db.session.add(
            LogEstornoLancamento(
                numero_nota="3100",
                usuario_estorno="admin",
                motivo="Ajuste fiscal",
            )
        )
        db.session.commit()

    response_concluidas = client.get("/api/concluidas")
    assert response_concluidas.status_code == 200
    concluidas = {item["numero"]: item for item in response_concluidas.get_json()}
    assert concluidas["3100"]["status_atual"] == "Concluído"
    assert concluidas["3100"]["etapa_atual"] == "Estornada"
    assert concluidas["3100"]["exige_codigo_material_remessa"] is True

    response_lancadas = client.get("/api/notas_lancadas")
    assert response_lancadas.status_code == 200
    lancadas = {item["numero"]: item for item in response_lancadas.get_json()}
    assert lancadas["3101"]["status_atual"] == "Lançado"
    assert lancadas["3101"]["etapa_atual"] == "Lançada"
    assert lancadas["3101"]["codigo_erp"] == "ERP-3101"
    assert lancadas["3101"]["manifestacao"]["status"] == "Falha"


def test_documento_entrada_finalizado_inclui_recusado_como_historico_lancado(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()
    login_admin(client)

    with app.app_context():
        db.session.add(
            ItemNota(
                numero_nota="3110",
                fornecedor="Fornecedor Recusado",
                codigo="RC1",
                descricao="Item recusado historico",
                qtd_real=1.0,
                status="AguardandoLiberacao",
                auditor_decisao="XML Recusado",
                data_importacao=datetime.now() - timedelta(hours=3),
            )
        )
        db.session.add(
            ItemNota(
                numero_nota="3111",
                fornecedor="Fornecedor Lancado",
                codigo="LC2",
                descricao="Item lancado",
                qtd_real=1.0,
                status="Lançado",
                data_importacao=datetime.now() - timedelta(hours=2),
            )
        )
        db.session.commit()

    resp_finalizado = client.get("/api/documento_entrada/lista?etapa=finalizado&page=1&page_size=50")
    assert resp_finalizado.status_code == 200
    finalizado = {item["numero"]: item for item in resp_finalizado.get_json()["notas"]}
    assert "3110" in finalizado
    assert finalizado["3110"]["status"] == "Lançado"
    assert finalizado["3110"]["tem_recusa_historica"] is True

    resp_auditoria = client.get("/api/documento_entrada/lista?etapa=auditoria&page=1&page_size=50")
    assert resp_auditoria.status_code == 200
    numeros_auditoria = {item["numero"] for item in resp_auditoria.get_json()["notas"]}
    assert "3110" not in numeros_auditoria

    resp_lancamento = client.get("/api/documento_entrada/lista?etapa=lancamento&page=1&page_size=50")
    assert resp_lancamento.status_code == 200
    numeros_lancamento = {item["numero"] for item in resp_lancamento.get_json()["notas"]}
    assert "3110" not in numeros_lancamento

    resp_kpis = client.get("/api/documento_entrada/kpis")
    assert resp_kpis.status_code == 200
    kpis = resp_kpis.get_json()
    assert kpis["lancamento_finalizado"] >= 2


def test_erp_lancamento_bridge_401_tem_mensagem_configuravel():
    from conferencia_app.services.erp_lancamento_service import (
        ERPBridgeUnauthorizedError,
        _raise_for_bridge_status,
    )

    resposta_401 = Mock(status_code=401)
    resposta_401.raise_for_status = Mock()

    try:
        _raise_for_bridge_status(resposta_401, url="https://bridge.local/api/erp/lancamentos")
    except ERPBridgeUnauthorizedError as exc:
        mensagem = str(exc)
    else:
        raise AssertionError("401 da bridge deveria gerar ERPBridgeUnauthorizedError")

    assert "ERP_LANCAMENTO_API_TOKEN" in mensagem
    assert "ERP_BRIDGE_TOKEN" in mensagem


def test_erp_lancamento_status_retorna_scheduler(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()
    login_admin(client)

    response = client.get("/api/fiscal/erp_lancamento/status")

    assert response.status_code == 200
    data = response.get_json()
    assert data["sucesso"] is True
    assert "rodando" in data["scheduler"]
    assert "last_status" in data["scheduler"]


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


def test_confirmar_lancamento_trata_duplicidade_manifestacao_como_sucesso(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()
    login_admin(client)

    with app.app_context():
        db.session.add(
            ItemNota(
                numero_nota="2008",
                fornecedor="Fornecedor Duplicidade Manifestacao",
                codigo="MAN8",
                descricao="Item ja manifestado na sefaz",
                qtd_real=1.0,
                status="Concluído",
                chave_acesso="20082008200820082008200820082008200820082008",
            )
        )
        db.session.commit()

    with patch(
        "conferencia_app.routes.api_routes.manifestar_destinatario_consyste",
        return_value=(False, 400, {"cStat": "573", "motivo": "Duplicidade de Evento"}),
    ):
        response = client.post(
            "/api/confirmar_lancamento",
            json={"nota": "2008", "codigo": "ERP-2008", "manifestar_destinatario": True},
        )

    assert response.status_code == 200
    data = response.get_json()
    assert data["sucesso"] is True
    assert data["manifestacao"]["sucesso"] is True
    assert data["manifestacao"]["idempotente"] is True

    with app.app_context():
        item = ItemNota.query.filter_by(numero_nota="2008").first()
        log = LogManifestacaoDestinatario.query.filter_by(numero_nota="2008").order_by(LogManifestacaoDestinatario.id.desc()).first()
        assert item.status == "Lançado"
        assert log.status == "Sucesso"


def test_confirmar_lancamento_remessa_exige_codigo_material_por_item(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()
    login_admin(client)

    with app.app_context():
        db.session.add_all(
            [
                ItemNota(
                    numero_nota="2010",
                    fornecedor="Fornecedor Remessa",
                    cfop="5124",
                    codigo="",
                    descricao="Item Remessa A",
                    qtd_real=2.0,
                    status="Concluído",
                    remessa=True,
                ),
                ItemNota(
                    numero_nota="2010",
                    fornecedor="Fornecedor Remessa",
                    cfop="5124",
                    codigo="",
                    descricao="Item Remessa B",
                    qtd_real=1.0,
                    status="Concluído",
                    remessa=True,
                ),
            ]
        )
        db.session.commit()

    response = client.post(
        "/api/confirmar_lancamento",
        json={"nota": "2010", "codigo": "ERP-2010", "manifestar_destinatario": False},
    )

    assert response.status_code == 400
    data = response.get_json()
    assert data["sucesso"] is False
    assert "industrialização" in data["msg"].lower()


def test_confirmar_lancamento_remessa_persiste_codigo_material_por_item(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()
    login_admin(client)

    with app.app_context():
        item_a = ItemNota(
            numero_nota="2011",
            fornecedor="Fornecedor Remessa",
            cfop="5124",
            codigo="",
            descricao="Item Remessa A",
            qtd_real=2.0,
            status="Concluído",
            remessa=True,
        )
        item_b = ItemNota(
            numero_nota="2011",
            fornecedor="Fornecedor Remessa",
            cfop="5124",
            codigo="",
            descricao="Item Remessa B",
            qtd_real=1.0,
            status="Concluído",
            remessa=True,
        )
        db.session.add_all([item_a, item_b])
        db.session.commit()
        item_a_id = item_a.id
        item_b_id = item_b.id

    response = client.post(
        "/api/confirmar_lancamento",
        json={
            "nota": "2011",
            "codigo": "ERP-2011",
            "codigos_materiais": [
                {"item_id": item_a_id, "codigo_material": "MAT-REM-01"},
                {"item_id": item_b_id, "codigo_material": "MAT-REM-02"},
            ],
            "manifestar_destinatario": False,
        },
    )

    assert response.status_code == 200
    data = response.get_json()
    assert data["sucesso"] is True

    with app.app_context():
        itens = ItemNota.query.filter_by(numero_nota="2011").order_by(ItemNota.id.asc()).all()
        assert len(itens) == 2
        assert itens[0].status == "Lançado"
        assert itens[1].status == "Lançado"
        assert itens[0].codigo == "MAT-REM-01"
        assert itens[1].codigo == "MAT-REM-02"


def test_confirmar_lancamento_remessa_cfop_5902_nao_exige_codigo_material(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()
    login_admin(client)

    with app.app_context():
        db.session.add_all(
            [
                ItemNota(
                    numero_nota="2012",
                    fornecedor="Fornecedor Remessa 5902",
                    cfop="5902",
                    codigo="",
                    descricao="Item Remessa 5902 A",
                    qtd_real=1.0,
                    status="Concluído",
                    remessa=True,
                ),
                ItemNota(
                    numero_nota="2012",
                    fornecedor="Fornecedor Remessa 5902",
                    cfop="5902",
                    codigo="",
                    descricao="Item Remessa 5902 B",
                    qtd_real=3.0,
                    status="Concluído",
                    remessa=True,
                ),
            ]
        )
        db.session.commit()

    response = client.post(
        "/api/confirmar_lancamento",
        json={"nota": "2012", "codigo": "ERP-2012", "manifestar_destinatario": False},
    )

    assert response.status_code == 200
    data = response.get_json()
    assert data["sucesso"] is True

    with app.app_context():
        itens = ItemNota.query.filter_by(numero_nota="2012").order_by(ItemNota.id.asc()).all()
        assert len(itens) == 2
        assert all(item.status == "Lançado" for item in itens)


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


def test_confirmar_lancamento_idempotente_quando_nf_ja_lancada_com_mesmo_codigo(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()
    login_admin(client)

    with app.app_context():
        db.session.add(
            ItemNota(
                numero_nota="2006",
                fornecedor="Fornecedor Idempotente",
                codigo="IDEMP-01",
                descricao="Item idempotente",
                qtd_real=1.0,
                status="Lançado",
                numero_lancamento="ERP-2006",
                usuario_lancamento="admin",
                data_lancamento=datetime.now(),
            )
        )
        db.session.commit()

    response = client.post(
        "/api/confirmar_lancamento",
        json={"nota": "2006", "codigo": "ERP-2006", "manifestar_destinatario": False},
    )
    assert response.status_code == 200
    data = response.get_json()
    assert data["sucesso"] is True
    assert data["idempotente"] is True


def test_reenvio_manifestacao_retorna_idempotente_quando_ja_sucesso(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()
    login_admin(client)

    with app.app_context():
        db.session.add(
            ItemNota(
                numero_nota="2007",
                fornecedor="Fornecedor Manifestacao Idempotente",
                codigo="IDEMP-MANI",
                descricao="Item manifestado",
                qtd_real=1.0,
                status="Lançado",
                numero_lancamento="ERP-2007",
                usuario_lancamento="admin",
                data_lancamento=datetime.now(),
                chave_acesso="20072007200720072007200720072007200720072007",
            )
        )
        db.session.add(
            LogManifestacaoDestinatario(
                numero_nota="2007",
                chave_acesso="20072007200720072007200720072007200720072007",
                manifestacao="confirmada",
                status="Sucesso",
                detalhe="Confirmacao ja enviada anteriormente.",
                usuario="admin",
            )
        )
        db.session.commit()

    response = client.post("/api/fiscal/manifestar_destinatario", json={"nota": "2007"})
    assert response.status_code == 200
    data = response.get_json()
    assert data["sucesso"] is True
    assert data["idempotente"] is True

    with app.app_context():
        log = (
            LogEventoFiscalNota.query.filter_by(numero_nota="2007", evento="ManifestacaoIdempotente")
            .order_by(LogEventoFiscalNota.id.desc())
            .first()
        )
        assert log is not None


def test_documento_entrada_kpis_e_timeline_trazem_governanca(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()
    login_admin(client)

    agora = datetime.now()
    with app.app_context():
        db.session.add(
            ItemNota(
                numero_nota="3000",
                fornecedor="Fornecedor KPI",
                codigo="SKU-3000",
                descricao="Item KPI",
                qtd_real=2.0,
                status="Lançado",
                usuario_importacao="fiscal_import",
                data_importacao=agora - timedelta(days=2),
                usuario_conferencia="conferente_1",
                inicio_conferencia=agora - timedelta(days=2, hours=-2),
                fim_conferencia=agora - timedelta(days=1, hours=10),
                usuario_lancamento="admin",
                data_lancamento=agora - timedelta(days=1),
                numero_lancamento="ERP-3000",
                chave_acesso="30003000300030003000300030003000300030003000",
                auditor_status="Auditado",
                auditor_decisao="Aprovado",
                auditor_usuario="fiscal_admin",
                auditor_data=agora - timedelta(days=1, hours=12),
            )
        )
        db.session.add(
            LogManifestacaoDestinatario(
                numero_nota="3000",
                chave_acesso="30003000300030003000300030003000300030003000",
                manifestacao="confirmada",
                status="Sucesso",
                detalhe="Manifestacao transmitida.",
                usuario="admin",
                data=agora - timedelta(hours=20),
            )
        )
        db.session.add(
            LogEventoFiscalNota(
                numero_nota="3000",
                evento="LancamentoFiscal",
                etapa="Lancamento",
                status="Sucesso",
                detalhe="Lancamento registrado no cockpit fiscal.",
                usuario="admin",
                data=agora - timedelta(days=1),
            )
        )
        db.session.commit()

    response_kpi = client.get("/api/fiscal/documento_entrada/kpis?dias=30")
    assert response_kpi.status_code == 200
    data_kpi = response_kpi.get_json()
    assert data_kpi["total_notas"] >= 1
    assert data_kpi["notas_lancadas"] >= 1
    assert data_kpi["tempo_medio_importacao_lancamento_horas"] > 0
    assert data_kpi["produtividade_usuarios"][0]["usuario"] == "FELAZE"

    response_timeline = client.get("/api/conferencia/nota/3000/historico")
    assert response_timeline.status_code == 200
    timeline = response_timeline.get_json()
    assert any(item["tipo"] == "Governanca Fiscal" for item in timeline)


def test_historico_conferencia_restrito_ao_admin(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()
    with app.app_context():
        from conferencia_app.models import Usuario

        db.session.add(
            Usuario(
                username="conferente_teste",
                password=generate_password_hash("conferente123"),
                role="Conferente",
            )
        )
        db.session.commit()
    with client.session_transaction() as session:
        session["username"] = "conferente_teste"
        session["role"] = "Conferente"

    assert client.get("/api/historico_completo").status_code == 403
    assert client.get("/api/conferencia/nota/3000/historico").status_code == 403


def test_detalhes_nf_retorna_workflow_pendencias_e_motivos_estorno(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()
    login_admin(client)

    with app.app_context():
        db.session.add(
            ItemNota(
                numero_nota="3010",
                fornecedor="Fornecedor Detalhe",
                codigo="SKU-3010",
                descricao="Item detalhe",
                qtd_real=1.0,
                status="Concluído",
                pedido_compra="450001",
                data_importacao=datetime.now() - timedelta(hours=12),
                fim_conferencia=datetime.now() - timedelta(hours=2),
                usuario_conferencia="operador_1",
                remessa=True,
                cfop="5124",
            )
        )
        db.session.commit()

    response = client.get("/api/detalhes_nf/3010")
    assert response.status_code == 200
    data = response.get_json()
    assert data["numero"] == "3010"
    assert isinstance(data["workflow"], list)
    assert isinstance(data["pendencias"], list)
    assert len(data["motivos_estorno_padrao"]) >= 1
    assert isinstance(data["timeline"], list)
    assert any(
        evento["tipo"] == "Visualização"
        and "ADMIN" in evento["descricao"]
        and "Visualizou os detalhes" in evento["descricao"]
        for evento in data["timeline"]
    )

    with app.app_context():
        from conferencia_app.models import ProcessoRecebimentoEvento

        evento = ProcessoRecebimentoEvento.query.filter_by(
            numero_nota="3010",
            acao="detalhe_visualizado",
        ).one()
        assert evento.usuario == "ADMIN"
        assert evento.fornecedor == "Fornecedor Detalhe"
        assert evento.ip_address == "127.0.0.1"

    set_logged_user(client, "fiscal_teste", "Fiscal")
    response_fiscal = client.get("/api/detalhes_nf/3010")
    assert response_fiscal.status_code == 200
    data_fiscal = response_fiscal.get_json()
    assert data_fiscal["timeline"] == []


def test_detalhes_nf_nao_mistura_manifestacao_de_mesmo_numero_com_fornecedor_diferente(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()
    login_admin(client)

    with app.app_context():
        db.session.add(
            ItemNota(
                numero_nota="4001",
                fornecedor="Fornecedor Abril",
                cnpj_emitente="11111111000111",
                chave_acesso="11111111111111111111111111111111111111111111",
                codigo="SKU-OLD",
                descricao="Item antigo",
                qtd_real=1.0,
                status="Lançado",
                data_importacao=datetime.now() - timedelta(days=120),
            )
        )
        db.session.add(
            LogManifestacaoDestinatario(
                numero_nota="4001",
                chave_acesso="11111111111111111111111111111111111111111111",
                manifestacao="confirmada",
                status="Sucesso",
                detalhe="Manifestação nota antiga",
                usuario="ADMIN",
            )
        )

        db.session.add(
            ItemNota(
                numero_nota="4001",
                fornecedor="Fornecedor Atual",
                cnpj_emitente="22222222000122",
                chave_acesso="22222222222222222222222222222222222222222222",
                codigo="SKU-NEW",
                descricao="Item atual",
                qtd_real=1.0,
                status="Concluído",
                data_importacao=datetime.now() - timedelta(days=1),
            )
        )
        db.session.commit()

    response = client.get("/api/detalhes_nf/4001?cnpj_emitente=22222222000122")
    assert response.status_code == 200
    data = response.get_json()
    assert data["fornecedor"] == "Fornecedor Atual"
    assert data["manifestacao"] is None
    assert all(
        "Manifestacao" not in str(evento.get("tipo") or "")
        for evento in (data.get("timeline") or [])
    )


def test_conferencia_dashboard_e_visualizacao_respeitam_identidade_da_nf(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()
    login_admin(client)

    with app.app_context():
        db.session.add(
            ItemNota(
                numero_nota="4002",
                fornecedor="Fornecedor Antigo",
                cnpj_emitente="11111111000111",
                chave_acesso="33333333333333333333333333333333333333333333",
                codigo="SKU-OLD-4002",
                descricao="Item antigo",
                qtd_real=1.0,
                status="Lançado",
                data_importacao=datetime.now() - timedelta(days=60),
            )
        )
        db.session.add(
            ItemNota(
                numero_nota="4002",
                fornecedor="Fornecedor Atual",
                cnpj_emitente="22222222000122",
                chave_acesso="44444444444444444444444444444444444444444444",
                codigo="SKU-NEW-4002",
                descricao="Item atual",
                qtd_real=2.0,
                status="Pendente",
                data_importacao=datetime.now() - timedelta(hours=3),
            )
        )
        db.session.commit()

    response_dashboard = client.get("/api/conferencia/dashboard?status=pendente")
    assert response_dashboard.status_code == 200
    notas = response_dashboard.get_json()["notas"]
    nota_alvo = next((n for n in notas if n["numero"] == "4002" and n["cnpj_emitente"] == "22222222000122"), None)
    assert nota_alvo is not None
    assert nota_alvo["fornecedor"] == "Fornecedor Atual"

    response_visualizacao = client.get(
        "/api/conferencia/nota/4002/visualizacao?cnpj_emitente=22222222000122"
    )
    assert response_visualizacao.status_code == 200
    visualizacao = response_visualizacao.get_json()
    assert visualizacao["fornecedor"] == "Fornecedor Atual"


def test_conferencia_visualizacao_completa_retorna_itens_e_historico(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()
    login_admin(client)

    with app.app_context():
        from conferencia_app.models import LogTentativaConferencia

        item = ItemNota(
            numero_nota="3011",
            fornecedor="Fornecedor Visao Completa",
            codigo="SKU-3011",
            descricao="Item visão",
            qtd_real=10.0,
            status="Concluído",
            pedido_compra="450010",
            unidade_comercial="UN",
            cnpj_emitente="12345678000199",
        )
        db.session.add(item)
        db.session.flush()
        db.session.add(
            LogTentativaConferencia(
                numero_nota="3011",
                item_id=item.id,
                tentativa_numero=1,
                qtd_esperada=10.0,
                qtd_digitada=10.0,
                qtd_convertida=10.0,
                unidade_informada="UN",
                fator_conversao=1.0,
                status_item="OK",
                motivo="Conferido.",
                usuario="ADMIN",
            )
        )
        db.session.commit()

    response = client.get("/api/conferencia/nota/3011/visualizacao")
    assert response.status_code == 200
    data = response.get_json()
    assert data["numero"] == "3011"
    assert isinstance(data["itens"], list)
    assert len(data["itens"]) == 1
    assert data["itens"][0]["codigo"] == "SKU-3011"
    assert data["itens"][0]["status_ultima"] == "OK"

    timeline = client.get("/api/conferencia/nota/3011/historico")
    assert timeline.status_code == 200
    eventos = timeline.get_json()
    assert any(
        evento["tipo"] == "Visualização"
        and "visualização completa da conferência" in evento["descricao"].lower()
        for evento in eventos
    )


def test_abrir_conferencia_pendente_registra_logs_de_abertura_e_inicio(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()
    login_admin(client)

    with app.app_context():
        db.session.add(
            ItemNota(
                numero_nota="3012",
                fornecedor="Fornecedor Conferencia Pendente",
                codigo="SKU-3012",
                descricao="Item pendente",
                qtd_real=5.0,
                status="Pendente",
                pedido_compra="450011",
                unidade_comercial="UN",
                cnpj_emitente="22345678000199",
            )
        )
        db.session.commit()

    response = client.get("/api/itens/3012")
    assert response.status_code == 200
    itens = response.get_json()
    assert isinstance(itens, list)
    assert len(itens) == 1

    historico = client.get("/api/conferencia/nota/3012/historico")
    assert historico.status_code == 200
    eventos = historico.get_json()
    assert any(
        evento["tipo"] == "Visualização"
        and "abriu a conferência de recebimento" in evento["descricao"].lower()
        for evento in eventos
    )
    assert any(
        evento["tipo"] == "Conferência"
        and "iniciou a conferência" in evento["descricao"].lower()
        and "admin" in evento["descricao"].lower()
        for evento in eventos
    )

    with app.app_context():
        from conferencia_app.models import ProcessoRecebimentoEvento

        abertura = ProcessoRecebimentoEvento.query.filter_by(
            numero_nota="3012",
            acao="conferencia_aberta",
        ).one()
        inicio = ProcessoRecebimentoEvento.query.filter_by(
            numero_nota="3012",
            acao="conferencia_iniciada",
        ).one()
        assert abertura.usuario == "ADMIN"
        assert inicio.usuario == "ADMIN"


def test_timeline_ordenada_por_etapa_mantem_lancamento_antes_de_visualizacao(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()
    login_admin(client)

    with app.app_context():
        item = ItemNota(
            numero_nota="3013",
            fornecedor="Fornecedor Ordem",
            codigo="SKU-3013",
            descricao="Item ordem",
            qtd_real=1.0,
            status="Lançado",
            pedido_compra="450012",
            data_importacao=datetime.now() - timedelta(days=2),
            fim_conferencia=datetime.now() - timedelta(days=1, hours=4),
            data_lancamento=datetime.now() - timedelta(days=1),
            usuario_conferencia="OPERADOR",
            usuario_lancamento="FISCAL",
            numero_lancamento="L-3013",
            cnpj_emitente="33345678000199",
        )
        db.session.add(item)
        db.session.commit()

    # Gera evento de visualizacao posterior ao lancamento.
    vis = client.get("/api/conferencia/nota/3013/visualizacao")
    assert vis.status_code == 200

    historico = client.get("/api/conferencia/nota/3013/historico")
    assert historico.status_code == 200
    eventos = historico.get_json()
    tipos = [str(evento.get("tipo") or "") for evento in eventos]
    assert "Lançamento" in tipos
    assert "Visualização" in tipos
    assert tipos.index("Lançamento") < tipos.index("Visualização")


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

    with app.app_context():
        from conferencia_app.models import ProcessoRecebimentoEvento

        evento = ProcessoRecebimentoEvento.query.filter_by(
            numero_nota="321",
            acao="download_documento",
        ).one()
        assert evento.usuario == "ADMIN"
        assert "PDF" in evento.descricao


def test_gravar_checklist_registra_evento_processo(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()
    login_admin(client)

    with app.app_context():
        db.session.add(
            ItemNota(
                numero_nota="330",
                fornecedor="Fornecedor Checklist",
                codigo="CK1",
                descricao="Item checklist",
                qtd_real=1.0,
                status="Pendente",
            )
        )
        db.session.commit()

    response = client.post(
        "/api/checklist",
        json={
            "nota": "330",
            "lacre_ok": True,
            "volumes_ok": True,
            "avaria_visual": False,
            "observacao": "Sem avarias.",
        },
    )
    assert response.status_code == 200
    assert response.get_json()["sucesso"] is True

    with app.app_context():
        from conferencia_app.models import ProcessoRecebimentoEvento

        evento = ProcessoRecebimentoEvento.query.filter_by(
            numero_nota="330",
            acao="checklist_preenchido",
        ).one()
        assert evento.usuario == "ADMIN"
        assert evento.fornecedor == "Fornecedor Checklist"

    detalhe = client.get("/api/detalhes_nf/330")
    assert detalhe.status_code == 200
    timeline = detalhe.get_json()["timeline"]
    assert any(evento["tipo"] == "Checklist" for evento in timeline)


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


def test_financeiro_classificacao_contabil_page_e_api_filtram_desde_2026(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()
    set_logged_user(client, "contador_teste", "Financeiro")

    with app.app_context():
        db.session.add(
            ClassificacaoContabilPadrao(
                fornecedor_norm="FORNECEDOR PADRAO",
                cfop="1102",
                codigo_norm="MAT001",
                descricao_norm="PARAFUSO",
                conta="12503",
                nome_conta="MATERIAIS SECUNDÁRIOS",
                ocorrencias=4,
                origem="Teste",
            )
        )
        db.session.add(
            ItemNota(
                numero_nota="2026A",
                fornecedor="Fornecedor Padrão",
                codigo="MAT001",
                descricao="Parafuso",
                cfop="1102",
                qtd_real=1,
                status="Lançado",
                data_lancamento=datetime(2026, 2, 10, 8, 30),
                valor_produto=50,
            )
        )
        db.session.add(
            ItemNota(
                numero_nota="2025A",
                fornecedor="Fornecedor Padrão",
                codigo="MAT001",
                descricao="Parafuso",
                cfop="1102",
                qtd_real=1,
                status="Lançado",
                data_lancamento=datetime(2025, 12, 30, 8, 30),
                valor_produto=50,
            )
        )
        db.session.commit()

    page = client.get("/financeiro/classificacao-contabil")
    assert page.status_code == 200
    assert "Classificação contábil".encode("utf-8") in page.data

    response = client.get("/api/financeiro/classificacao-contabil?inicio=2025-01-01")
    assert response.status_code == 200
    data = response.get_json()
    assert data["paginacao"]["page"] == 1
    assert data["paginacao"]["per_page"] == 50
    assert data["paginacao"]["total"] == 1
    numeros = {item["numero_nota"] for item in data["itens"]}
    assert "2026A" in numeros
    assert "2025A" not in numeros
    item_2026 = next(item for item in data["itens"] if item["numero_nota"] == "2026A")
    assert item_2026["conta"] == "12503"
    assert item_2026["status"] == "Classificado"


def test_financeiro_classificacao_contabil_revisao_manual_aprende_padrao(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()
    set_logged_user(client, "contador_teste", "Financeiro")

    with app.app_context():
        item = ItemNota(
            numero_nota="2026B",
            fornecedor="Fornecedor Novo",
            codigo="SERV01",
            descricao="Servico especializado",
            cfop="1933",
            qtd_real=1,
            status="Lançado",
            data_lancamento=datetime(2026, 3, 5, 9, 0),
            valor_produto=300,
        )
        db.session.add(item)
        db.session.commit()

    lista = client.get("/api/financeiro/classificacao-contabil?inicio=2026-01-01").get_json()
    classificacao = next(item for item in lista["itens"] if item["numero_nota"] == "2026B")
    assert classificacao["status"] in {"Pendente", "Revisar"}

    response = client.patch(
        f"/api/financeiro/classificacao-contabil/{classificacao['id']}",
        json={
            "conta": "94901",
            "nome_conta": "MANUTENÇÃO MÁQUINAS - CUSTO - GERAL",
            "comentario": "ajuste contador",
        },
    )
    assert response.status_code == 200
    data = response.get_json()
    assert data["item"]["status"] == "Revisado"
    assert data["item"]["conta"] == "94901"

    with app.app_context():
        assert ClassificacaoContabilItem.query.filter_by(numero_nota="2026B", status="Revisado").count() == 1
        assert ClassificacaoContabilPadrao.query.filter_by(conta="94901").count() >= 1


def test_financeiro_classificacao_contabil_preenche_nome_conta_pelo_plano(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()
    set_logged_user(client, "contador_teste", "Financeiro")

    with app.app_context():
        db.session.add(PlanoContaDominio(codigo_conta="94901", nome_conta="MANUTENCAO MAQUINAS - CUSTO - GERAL"))
        item = ItemNota(
            numero_nota="2026PLANO",
            fornecedor="Fornecedor Plano",
            codigo="PL1",
            descricao="Item plano",
            cfop="1102",
            qtd_real=1,
            status="Lançado",
            data_lancamento=datetime(2026, 3, 10, 9, 0),
            valor_nf=123.45,
            icms_base_calculo=100,
            icms_aliquota=18,
            cst_icms="000",
            icms_valor=18,
            pis_base_calculo=100,
            pis_aliquota=1.65,
            cst_pis="50",
            pis_valor_credito=1.65,
            cofins_base_calculo=100,
            cofins_aliquota=7.6,
            cst_cofins="50",
            cofins_valor_credito=7.6,
            tributos_origem="GRV",
            tributos_grv_atualizado_em=datetime(2026, 3, 10, 9, 5),
        )
        db.session.add(item)
        db.session.commit()

    lista = client.get("/api/financeiro/classificacao-contabil?competencia=2026-03").get_json()
    classificacao = next(item for item in lista["itens"] if item["numero_nota"] == "2026PLANO")

    response = client.patch(
        f"/api/financeiro/classificacao-contabil/{classificacao['id']}",
        json={"conta": "94901", "nome_conta": "NOME DIGITADO ERRADO", "comentario": "conta pelo plano"},
    )
    assert response.status_code == 200
    data = response.get_json()
    assert data["item"]["nome_conta"] == "MANUTENCAO MAQUINAS - CUSTO - GERAL"
    assert data["item"]["valor_nf"] == 123.45
    assert data["item"]["icms_cst"] == "000"
    assert data["item"]["icms_valor"] == 18
    assert data["item"]["pis_cst"] == "50"
    assert data["item"]["cofins_aliquota"] == 7.6


def test_financeiro_classificacao_contabil_nao_exibe_tributo_xml_sem_grv(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()
    set_logged_user(client, "contador_teste", "Financeiro")

    with app.app_context():
        item = ItemNota(
            numero_nota="2026XMLTRIB",
            fornecedor="Fornecedor XML",
            codigo="XMLTRIB",
            descricao="Item com imposto do XML",
            cfop="1102",
            qtd_real=1,
            status="LanÃ§ado",
            data_lancamento=datetime(2026, 3, 11, 9, 0),
            valor_nf=999,
            icms_base_calculo=999,
            icms_aliquota=18,
            cst_icms="000",
            icms_valor=179.82,
            pis_base_calculo=999,
            pis_aliquota=1.65,
            cst_pis="50",
            pis_valor_credito=16.48,
            cofins_base_calculo=999,
            cofins_aliquota=7.6,
            cst_cofins="50",
            cofins_valor_credito=75.92,
        )
        db.session.add(item)
        db.session.flush()
        db.session.add(
            ClassificacaoContabilItem(
                item_nota_id=item.id,
                numero_nota=item.numero_nota,
                fornecedor=item.fornecedor,
                codigo_item=item.codigo,
                descricao_item=item.descricao,
                cfop=item.cfop,
                status="Pendente",
                metodo="Sem padrão",
            )
        )
        db.session.commit()

    response = client.get("/api/financeiro/classificacao-contabil?competencia=2026-03")
    assert response.status_code == 200
    data = response.get_json()
    item = next(row for row in data["itens"] if row["numero_nota"] == "2026XMLTRIB")
    assert item["tributos_origem"] == ""
    assert item["icms_base_calculo"] == 0
    assert item["icms_cst"] == ""
    assert item["pis_valor_credito"] == 0
    assert item["cofins_aliquota"] == 0


def test_financeiro_relatorio_custos_agrupa_lancamentos_reais(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()
    set_logged_user(client, "contador_teste", "Controladoria")

    with app.app_context():
        db.session.add_all(
            [
                ItemNota(
                    numero_nota="CUSTO1",
                    fornecedor="Fornecedor Usinagem",
                    codigo="INS-10",
                    descricao="Inserto metal duro para centro de usinagem",
                    qtd_real=10,
                    unidade_comercial="UN",
                    status="Lançado",
                    data_lancamento=datetime(2026, 5, 12, 10, 0),
                    valor_produto=500,
                    cfop="1556",
                ),
                ItemNota(
                    numero_nota="CUSTO2",
                    fornecedor="CPFL",
                    codigo="ENERGIA",
                    descricao="Energia eletrica fabrica maio",
                    qtd_real=1,
                    unidade_comercial="UN",
                    status="Lançado",
                    data_lancamento=datetime(2026, 5, 15, 10, 0),
                    valor_produto=1200,
                    cfop="1252",
                ),
                ItemNota(
                    numero_nota="CUSTO3",
                    fornecedor="Outro",
                    codigo="MAT",
                    descricao="Material sem familia de custo",
                    qtd_real=1,
                    status="Lançado",
                    data_lancamento=datetime(2026, 5, 20, 10, 0),
                    valor_produto=999,
                ),
                ItemNota(
                    numero_nota="CUSTO4",
                    fornecedor="Fornecedor Ignorado",
                    codigo="28-11-00131",
                    descricao="Fresa cadastro ignorado",
                    qtd_real=2,
                    status="Lançado",
                    data_lancamento=datetime(2026, 5, 21, 10, 0),
                    valor_produto=777,
                ),
                ItemNota(
                    numero_nota="CUSTO5",
                    fornecedor="Fornecedor Solda",
                    codigo="AR-01",
                    descricao="Arame tubular 1,6 mm (15kg cada unidade)",
                    qtd_real=15,
                    unidade_comercial="KG",
                    status="Lançado",
                    data_lancamento=datetime(2026, 5, 22, 10, 0),
                    valor_produto=300,
                ),
                ItemNota(
                    numero_nota="CUSTO6",
                    fornecedor="Retorno Conserto",
                    codigo="INS-RET",
                    descricao="Inserto metal duro retorno conserto",
                    qtd_real=1,
                    unidade_comercial="UN",
                    status="LanÃ§ado",
                    data_lancamento=datetime(2026, 5, 23, 10, 0),
                    valor_produto=9999,
                    cfop="1916",
                ),
            ]
        )
        db.session.commit()

    assert client.get("/financeiro/relatorio-custos").status_code == 200

    response = client.get("/api/financeiro/relatorio-custos?competencia=2026-05")
    assert response.status_code == 200
    data = response.get_json()
    assert data["resumo"]["valor_total"] == 2000
    assert data["resumo"]["itens"] == 3
    assert data["resumo"]["fornecedores"] == 3
    assert data["resumo"]["custo_medio_unitario"] == 76.92
    assert data["resumo"]["custo_medio_lancamento"] == 666.67
    assert data["resumo"]["custo_medio_nf"] == 666.67
    assert data["resumo"]["top_categoria"] == "Energia elétrica"
    assert data["resumo"]["top_categoria_participacao"] == 60
    assert data["resumo"]["top_fornecedor"] == "CPFL"
    assert data["resumo"]["top_fornecedor_participacao"] == 60
    assert data["resumo"]["participacao_top_3"] == 100
    categorias = {row["id"]: row for row in data["categorias"]}
    assert categorias["insertos_ferramentas"]["valor"] == 500
    assert categorias["insertos_ferramentas"]["custo_medio_unitario"] == 50
    assert categorias["insertos_ferramentas"]["custo_medio_lancamento"] == 500
    assert categorias["energia_eletrica"]["valor"] == 1200
    assert categorias["energia_eletrica"]["custo_medio_unitario"] == 1200
    assert categorias["arames_solda"]["valor"] == 300
    assert categorias["arames_solda"]["custo_medio_unitario"] == 20
    assert data["familias_custo_medio"][0]["custo_medio_unitario"] == 1200
    assert data["top_fornecedores"][0]["participacao"] == 60
    assert data["top_itens"][0]["valor_unitario_medio"] == 1200
    assert all(linha["numero_nota"] != "CUSTO3" for linha in data["linhas"])
    assert all(linha["numero_nota"] != "CUSTO6" for linha in data["linhas"])
    assert all(linha["codigo"] != "28-11-00131" for linha in data["linhas"])

    filtrado = client.get("/api/financeiro/relatorio-custos?competencia=2026-05&categoria=energia_eletrica")
    assert filtrado.status_code == 200
    assert filtrado.get_json()["resumo"]["valor_total"] == 1200


def test_financeiro_relatorio_custos_aceita_intervalo_competencias(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()
    set_logged_user(client, "contador_teste", "Controladoria")

    with app.app_context():
        db.session.add_all(
            [
                ItemNota(
                    numero_nota="CUSTO-MAI",
                    fornecedor="Fornecedor Usinagem",
                    codigo="INS-10",
                    descricao="Inserto metal duro",
                    qtd_real=2,
                    unidade_comercial="UN",
                    status="Lançado",
                    data_lancamento=datetime(2026, 5, 10, 10, 0),
                    valor_produto=200,
                    cfop="1556",
                ),
                ItemNota(
                    numero_nota="CUSTO-JUN",
                    fornecedor="CPFL",
                    codigo="ENERGIA",
                    descricao="Energia eletrica fabrica junho",
                    qtd_real=1,
                    unidade_comercial="UN",
                    status="Lançado",
                    data_lancamento=datetime(2026, 6, 8, 10, 0),
                    valor_produto=800,
                    cfop="1252",
                ),
            ]
        )
        db.session.commit()

    response = client.get(
        "/api/financeiro/relatorio-custos?competencia_inicio=2026-05&competencia_fim=2026-06"
    )

    assert response.status_code == 200
    data = response.get_json()
    assert data["competencia"] == "2026-05_a_2026-06"
    assert data["resumo"]["valor_total"] == 1000
    assert {row["competencia"]: row["valor"] for row in data["mensal"]} == {"2026-05": 200, "2026-06": 800}


def test_financeiro_relatorio_custos_prefere_grv_postgres(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()
    set_logged_user(client, "contador_teste", "Controladoria")

    cfg = {"api_url": "https://bridge.local", "api_token": "token", "api_timeout": 30}
    lancamentos = [
        {"numero_nota": "312312", "codigo": "AR123", "dt_lancamento": "2026-05-22T10:00:00", "chave_acesso": "NFE"}
    ]
    entradas = [
        {
            "numero_nota": "312312",
            "codigo_lancamento": "AR123",
            "dt_lancamento": "2026-05-22T10:00:00",
            "parceiro_nome": "Fornecedor Ferramentas",
            "itens": [
                {
                    "cod_interno": "F-001",
                    "descricao": "Fresa topo metal duro",
                    "quantidade": 4,
                    "unidade": "UN",
                    "valor_unitario": 25,
                    "valor_total_linha": 100,
                    "cfop": "1556",
                },
                {
                    "cod_interno": "28-11-00131",
                    "descricao": "Fresa cadastro ignorado",
                    "quantidade": 1,
                    "unidade": "UN",
                    "valor_total_linha": 999,
                    "cfop": "1556",
                },
                {
                    "cod_interno": "AR-10",
                    "descricao": "Arame tubular 1,6 mm (15kg cada unidade)",
                    "familia": "N - 06 - INSUMOS DA PRODUCAO",
                    "localizacao_estoque": "SOLDA",
                    "quantidade": 18,
                    "unidade": "KG",
                    "valor_total_linha": 360,
                    "cfop": "1556",
                },
                {
                    "cod_interno": "28-11-00342",
                    "descricao": "Cadastro especial centro usinagem",
                    "quantidade": 20,
                    "unidade": "LT",
                    "valor_total_linha": 800,
                    "cfop": "1556",
                },
                {
                    "cod_interno": "F-RET",
                    "descricao": "Fresa topo metal duro retorno conserto",
                    "quantidade": 1,
                    "unidade": "UN",
                    "valor_total_linha": 9999,
                    "cfop": "1916",
                },
            ],
        }
    ]

    with patch("conferencia_app.services.erp_lancamento_service._resolver_config", return_value=cfg), patch(
        "conferencia_app.services.erp_lancamento_service._consultar_lancamentos_periodo_via_api",
        return_value=lancamentos,
    ), patch(
        "conferencia_app.services.erp_lancamento_service._consultar_entradas_grv_payload_via_api",
        return_value=entradas,
    ):
        response = client.get("/api/financeiro/relatorio-custos?competencia=2026-05")

    assert response.status_code == 200
    data = response.get_json()
    assert data["fonte"] == "grv_postgres"
    assert data["resumo"]["valor_total"] == 1260
    assert data["linhas"][0]["numero_nota"] == "312312"
    assert any(linha["categoria_id"] == "arames_solda" for linha in data["linhas"])
    assert any(linha["categoria_id"] == "oleo_soluvel" and linha["codigo"] == "28-11-00342" for linha in data["linhas"])
    assert all(linha["cfop"] != "1916" for linha in data["linhas"])
    assert all(linha["codigo"] != "28-11-00131" for linha in data["linhas"])
    categorias = {row["id"]: row for row in data["categorias"]}
    assert categorias["insertos_ferramentas"]["custo_medio_unitario"] == 25
    assert categorias["arames_solda"]["custo_medio_unitario"] == 20
    assert categorias["oleo_soluvel"]["valor"] == 800
    assert categorias["oleo_soluvel"]["custo_medio_unitario"] == 40
    arame = next(linha for linha in data["linhas"] if linha["categoria_id"] == "arames_solda")
    assert arame["familia"] == "N - 06 - INSUMOS DA PRODUCAO"
    assert arame["localizacao_estoque"] == "SOLDA"


def test_financeiro_relatorio_custos_nao_cai_no_local_quando_grv_configurado_sem_linhas(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()
    set_logged_user(client, "contador_teste", "Controladoria")

    with app.app_context():
        db.session.add(
            ItemNota(
                numero_nota="LOCAL1",
                fornecedor="CPFL",
                codigo="ENERGIA",
                descricao="Energia eletrica local antiga",
                qtd_real=1,
                unidade_comercial="UN",
                status="LanÃ§ado",
                data_lancamento=datetime(2026, 5, 15, 10, 0),
                valor_produto=1200,
            )
        )
        db.session.commit()

    cfg = {"api_url": "https://bridge.local", "api_token": "token", "api_timeout": 30}
    with patch("conferencia_app.services.erp_lancamento_service._resolver_config", return_value=cfg), patch(
        "conferencia_app.services.erp_lancamento_service._consultar_lancamentos_periodo_via_api",
        return_value=[],
    ), patch(
        "conferencia_app.services.erp_lancamento_service._consultar_entradas_grv_payload_via_api",
        return_value=[],
    ):
        response = client.get("/api/financeiro/relatorio-custos?competencia=2026-05")

    assert response.status_code == 200
    data = response.get_json()
    assert data["fonte"] == "grv_postgres"
    assert data["resumo"]["valor_total"] == 0
    assert data["linhas"] == []


def test_financeiro_relatorio_custos_energia_usa_media_por_lancamento_do_grv(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()
    set_logged_user(client, "contador_teste", "Controladoria")

    cfg = {"api_url": "https://bridge.local", "api_token": "token", "api_timeout": 30}
    lancamentos = [{"numero_nota": "ENERGIA1", "codigo": "EN123", "dt_lancamento": "2026-05-10T10:00:00"}]
    entradas = [
        {
            "numero_nota": "ENERGIA1",
            "codigo_lancamento": "EN123",
            "dt_lancamento": "2026-05-10T10:00:00",
            "parceiro_nome": "CPFL",
            "itens": [
                {
                    "cod_interno": "ENERGIA",
                    "descricao": "Energia eletrica fabrica",
                    "quantidade": 14.6999,
                    "unidade": "UN",
                    "valor_total_linha": 7674.25,
                    "cfop": "1252",
                }
            ],
        }
    ]

    with patch("conferencia_app.services.erp_lancamento_service._resolver_config", return_value=cfg), patch(
        "conferencia_app.services.erp_lancamento_service._consultar_lancamentos_periodo_via_api",
        return_value=lancamentos,
    ), patch(
        "conferencia_app.services.erp_lancamento_service._consultar_entradas_grv_payload_via_api",
        return_value=entradas,
    ):
        response = client.get("/api/financeiro/relatorio-custos?competencia=2026-05&categoria=energia_eletrica")

    assert response.status_code == 200
    data = response.get_json()
    energia = next(row for row in data["familias_custo_medio"] if row["id"] == "energia_eletrica")
    assert energia["custo_medio"] == 7674.25
    assert energia["custo_medio_lancamento"] == 7674.25
    assert energia["custo_medio_unitario"] == 522.06


def test_financeiro_classificacao_contabil_prefere_codigo_grv_postgres(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()
    set_logged_user(client, "contador_teste", "Financeiro")

    with app.app_context():
        db.session.add(
            ClassificacaoContabilPadrao(
                fornecedor_norm="FORNECEDOR GRV",
                cfop="1102",
                codigo_norm="GRV001",
                descricao_norm="ITEM GRV",
                conta="12503",
                nome_conta="MATERIAIS SECUNDARIOS",
                ocorrencias=4,
                origem="Postgres GRV",
            )
        )
        db.session.add(
            ItemNota(
                numero_nota="2026GRV",
                fornecedor="Fornecedor GRV",
                codigo="XML-001",
                codigo_grv="GRV-001",
                descricao="Item GRV",
                cfop="1102",
                cfop_descricao_grv="Compra para comercializacao lancada no GRV",
                qtd_real=1,
                status="Lançado",
                data_lancamento=datetime(2026, 3, 12, 9, 0),
                numero_lancamento="777",
            )
        )
        db.session.commit()

    response = client.get("/api/financeiro/classificacao-contabil?competencia=2026-03")
    assert response.status_code == 200
    data = response.get_json()
    item = next(row for row in data["itens"] if row["numero_nota"] == "2026GRV")
    assert item["codigo_item"] == "GRV-001"
    assert item["cfop"] == "1102"
    assert item["cfop_entrada"] == "1102"
    assert item["descricao_cfop_entrada"] == "Compra para comercializacao lancada no GRV"
    assert item["conta"] == "12503"


def test_financeiro_classificacao_contabil_cfop_exato_grv_prevalece(tmp_path):
    app = build_test_app(tmp_path)
    with app.app_context():
        item = ItemNota(
            numero_nota="2026CFOPGRV",
            fornecedor="Fornecedor GRV",
            codigo="XML-CFOP",
            descricao="Material consumo",
            cfop="1102",
            qtd_real=1,
            status="Lançado",
            data_lancamento=datetime(2026, 5, 20, 13, 30),
            numero_lancamento="888",
        )
        db.session.add(item)
        db.session.commit()

        from conferencia_app.services.erp_lancamento_service import _aplicar_codigos_grv

        total = _aplicar_codigos_grv(
            "2026CFOPGRV",
            {
                "numero_nota": "2026CFOPGRV",
                "dt_lancamento": "2026-05-21T12:40:00",
                "itens": [
                    {
                        "cod_interno": "GRV-CFOP",
                        "descricao": "Material consumo",
                        "quantidade": 1,
                        "cfop": "3102",
                        "natureza_operacao": "Compra de material para uso ou consumo",
                    }
                ],
            },
        )
        db.session.commit()

        atualizado = ItemNota.query.filter_by(numero_nota="2026CFOPGRV").first()
        assert total == 1
        assert atualizado.codigo_grv == "GRV-CFOP"
        assert atualizado.cfop == "3102"
        assert atualizado.cfop_descricao_grv == "Compra de material para uso ou consumo"


def test_financeiro_classificacao_contabil_cria_item_ausente_pelo_grv(tmp_path):
    app = build_test_app(tmp_path)
    with app.app_context():
        from conferencia_app.services.erp_lancamento_service import _aplicar_codigos_grv

        total = _aplicar_codigos_grv(
            "11355",
            {
                "codigo_lancamento": "GRV-11355",
                "numero_nota": "11355",
                "dt_nf": "2026-05-21T00:00:00",
                "dt_lancamento": "2026-05-21T10:15:00",
                "parceiro_nome": "Fornecedor GRV",
                "itens": [
                    {
                        "cod_interno": "22-02-9999",
                        "descricao": "Item lancado apenas no GRV",
                        "quantidade": 3,
                        "cfop": "3551",
                        "natureza_operacao": "Compra de bem para o ativo imobilizado",
                        "icms_base_calculo": 123.45,
                        "icms_aliquota": 18,
                        "icms_cst": "101",
                        "icms_valor": 22.22,
                        "pis_base_calculo": 0,
                        "vbc_q07": 280.0,
                        "ppis_q08": 1.65,
                        "pis_cst": "50",
                        "pis_valor_credito": 0,
                        "vpis_q09": 5.775,
                        "cofins_base_calculo": 0,
                        "vbc_s07": 280.0,
                        "pcofins_s08": 7.6,
                        "cofins_cst": "50",
                        "cofins_valor_credito": 0,
                        "vcofins_s11": 26.6,
                    }
                ],
            },
        )
        db.session.commit()

        item = ItemNota.query.filter_by(numero_nota="11355").first()
        assert total == 1
        assert item is not None
        assert item.status == "Lançado"
        assert item.codigo_grv == "22-02-9999"
        assert item.cfop == "3551"
        assert item.icms_base_calculo == 123.45
        assert item.cst_icms == "101"
        assert item.pis_base_calculo == 280
        assert item.pis_valor_credito == 5.775
        assert item.cofins_base_calculo == 280
        assert item.cofins_aliquota == 7.6
        assert item.cofins_valor_credito == 26.6
        assert item.tributos_origem == "GRV"


def test_financeiro_classificacao_contabil_importa_excel_upload_para_banco(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()
    set_logged_user(client, "contador_teste", "Financeiro")

    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "ENTRADAS TESTE"
    ws.append(
        [
            "Entrada:Fornecedor",
            "Itens da Entrada de NF:CFOP",
            "Itens da Entrada de NF:Cód. interno /Cód. fabricante",
            "Itens da Entrada de NF:Descrição",
            "Conta",
            "Nome conta",
            "comentario",
        ]
    )
    ws.append(["Fornecedor Upload", "1102", "UP001", "Item upload", "12503", "MATERIAIS SECUNDÁRIOS", "excel"])
    arquivo = io.BytesIO()
    wb.save(arquivo)
    arquivo.seek(0)

    response = client.post(
        "/api/financeiro/classificacao-contabil/padroes/upload",
        data={"arquivos": (arquivo, "padroes.xlsx")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 200
    data = response.get_json()
    assert data["resultado"]["padroes_criados"] == 1

    with app.app_context():
        padrao = ClassificacaoContabilPadrao.query.filter_by(conta="12503").first()
        assert padrao is not None
        assert padrao.fornecedor_norm == "FORNECEDOR UPLOAD"


def test_financeiro_classificacao_contabil_usa_base_interna_sem_excel_upload(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()
    set_logged_user(client, "contador_teste", "Financeiro")

    with app.app_context():
        db.session.add(
            ItemNota(
                numero_nota="2026BASE",
                fornecedor="K VOLTS RESISTENCIAS",
                codigo="26-06-00016",
                descricao="Servico manutencao",
                cfop="1933",
                qtd_real=1,
                status="Lançado",
                data_lancamento=datetime(2026, 4, 2, 9, 0),
                valor_produto=300,
            )
        )
        db.session.commit()

    response = client.get("/api/financeiro/classificacao-contabil?inicio=2026-01-01")
    assert response.status_code == 200
    data = response.get_json()
    item = next(row for row in data["itens"] if row["numero_nota"] == "2026BASE")
    assert item["conta"] == "94901"
    assert item["status"] == "Classificado"

    with app.app_context():
        assert ClassificacaoContabilPadrao.query.count() > 100


def test_financeiro_classificacao_contabil_reprocessa_pendente_existente(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()
    set_logged_user(client, "contador_teste", "Financeiro")

    with app.app_context():
        item = ItemNota(
            numero_nota="2026REP",
            fornecedor="Fornecedor Reprocesso",
            codigo="COD-BANCO-1",
            descricao="Item vindo do banco",
            cfop="1102",
            qtd_real=1,
            status="Lançado",
            data_lancamento=datetime(2026, 5, 2, 9, 0),
        )
        db.session.add(item)
        db.session.commit()
        db.session.add(
            ClassificacaoContabilItem(
                item_nota_id=item.id,
                numero_nota=item.numero_nota,
                fornecedor=item.fornecedor,
                codigo_item=item.codigo,
                descricao_item=item.descricao,
                cfop=item.cfop,
                status="Pendente",
                metodo="Sem padrão",
            )
        )
        db.session.add(
            ClassificacaoContabilPadrao(
                fornecedor_norm="FORNECEDOR REPROCESSO",
                cfop="1102",
                codigo_norm="CODBANCO1",
                descricao_norm="ITEM VINDO DO BANCO",
                conta="12503",
                nome_conta="MATERIAIS SECUNDÁRIOS",
                ocorrencias=5,
            )
        )
        db.session.commit()

    response = client.post(
        "/api/financeiro/classificacao-contabil/reprocessar",
        json={"inicio": "2026-05-01", "fim": "2026-05-31"},
    )
    assert response.status_code == 200

    with app.app_context():
        classificacao = ClassificacaoContabilItem.query.filter_by(numero_nota="2026REP").first()
        assert classificacao.conta == "12503"
        assert classificacao.codigo_item == "COD-BANCO-1"
        assert classificacao.status == "Classificado"


def test_financeiro_classificacao_contabil_equivale_cfop_saida_para_entrada(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()
    set_logged_user(client, "contador_teste", "Financeiro")

    with app.app_context():
        item = ItemNota(
            numero_nota="2026CFOP",
            fornecedor="Fornecedor CFOP",
            codigo="EQ001",
            descricao="Item equivalente",
            cfop="5102",
            qtd_real=1,
            status="Lançado",
            data_lancamento=datetime(2026, 7, 2, 9, 0),
        )
        db.session.add(item)
        db.session.add(
            ClassificacaoContabilPadrao(
                fornecedor_norm="FORNECEDOR CFOP",
                cfop="1102",
                codigo_norm="EQ001",
                descricao_norm="ITEM EQUIVALENTE",
                conta="12503",
                nome_conta="MATERIAIS SECUNDÁRIOS",
                ocorrencias=3,
            )
        )
        db.session.commit()

    response = client.post(
        "/api/financeiro/classificacao-contabil/reprocessar",
        json={"inicio": "2026-07-01", "fim": "2026-07-31"},
    )
    assert response.status_code == 200

    with app.app_context():
        classificacao = ClassificacaoContabilItem.query.filter_by(numero_nota="2026CFOP").first()
        assert classificacao.conta == "12503"
        assert classificacao.metodo == "Fornecedor + codigo + CFOP"


def test_financeiro_classificacao_contabil_aprovacao_bloqueia_reprocessamento(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()
    set_logged_user(client, "contador_teste", "Financeiro")

    with app.app_context():
        item = ItemNota(
            numero_nota="2026APR",
            fornecedor="Fornecedor Aprovar",
            codigo="APR1",
            descricao="Item aprovado",
            cfop="1102",
            qtd_real=1,
            status="Lançado",
            data_lancamento=datetime(2026, 6, 10, 9, 0),
        )
        db.session.add(item)
        db.session.commit()
        db.session.add(
            ClassificacaoContabilItem(
                item_nota_id=item.id,
                numero_nota=item.numero_nota,
                fornecedor=item.fornecedor,
                codigo_item=item.codigo,
                descricao_item=item.descricao,
                cfop=item.cfop,
                conta="12503",
                nome_conta="MATERIAIS SECUNDÁRIOS",
                status="Classificado",
                confianca=98,
            )
        )
        db.session.add(
            ClassificacaoContabilPadrao(
                fornecedor_norm="FORNECEDOR APROVAR",
                cfop="1102",
                codigo_norm="APR1",
                descricao_norm="ITEM APROVADO",
                conta="99999",
                nome_conta="CONTA NOVA",
                ocorrencias=10,
            )
        )
        db.session.commit()

    aprovacao = client.post(
        "/api/financeiro/classificacao-contabil/aprovar",
        json={"inicio": "2026-06-01", "fim": "2026-06-30"},
    )
    assert aprovacao.status_code == 200
    assert aprovacao.get_json()["aprovadas"] == 1

    reprocessar = client.post(
        "/api/financeiro/classificacao-contabil/reprocessar",
        json={"inicio": "2026-06-01", "fim": "2026-06-30"},
    )
    assert reprocessar.status_code == 200

    with app.app_context():
        classificacao = ClassificacaoContabilItem.query.filter_by(numero_nota="2026APR").first()
        assert classificacao.status == "Aprovado"
        assert classificacao.conta == "12503"
        assert classificacao.aprovado_por == "contador_teste"


def test_financeiro_classificacao_contabil_aprova_somente_competencia(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()
    set_logged_user(client, "contador_teste", "Financeiro")

    with app.app_context():
        for numero, data_lanc in [("JAN1", datetime(2026, 1, 20, 9, 0)), ("FEV1", datetime(2026, 2, 3, 9, 0))]:
            item = ItemNota(
                numero_nota=numero,
                fornecedor="Fornecedor Competencia",
                codigo=numero,
                descricao="Item competencia",
                cfop="1102",
                qtd_real=1,
                status="Lançado",
                data_lancamento=data_lanc,
            )
            db.session.add(item)
            db.session.flush()
            db.session.add(
                ClassificacaoContabilItem(
                    item_nota_id=item.id,
                    numero_nota=item.numero_nota,
                    fornecedor=item.fornecedor,
                    codigo_item=item.codigo,
                    descricao_item=item.descricao,
                    cfop=item.cfop,
                    conta="12503",
                    nome_conta="MATERIAIS SECUNDÁRIOS",
                    status="Classificado",
                    confianca=98,
                )
            )
        db.session.commit()

    response = client.post(
        "/api/financeiro/classificacao-contabil/aprovar",
        json={"competencia": "2026-01"},
    )
    assert response.status_code == 200
    assert response.get_json()["aprovadas"] == 1

    with app.app_context():
        jan = ClassificacaoContabilItem.query.filter_by(numero_nota="JAN1").first()
        fev = ClassificacaoContabilItem.query.filter_by(numero_nota="FEV1").first()
        assert jan.status == "Aprovado"
        assert fev.status == "Classificado"


def test_financeiro_classificacao_contabil_aprovar_nao_fecha_e_fechar_bloqueia(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()
    set_logged_user(client, "contador_teste", "Financeiro")

    with app.app_context():
        item = ItemNota(
            numero_nota="JANFECHA",
            fornecedor="Fornecedor Fecha",
            codigo="FEC1",
            descricao="Item fecha",
            cfop="1102",
            qtd_real=1,
            status="Lançado",
            data_lancamento=datetime(2026, 1, 10, 9, 0),
        )
        db.session.add(item)
        db.session.flush()
        db.session.add(
            ClassificacaoContabilItem(
                item_nota_id=item.id,
                numero_nota=item.numero_nota,
                fornecedor=item.fornecedor,
                codigo_item=item.codigo,
                descricao_item=item.descricao,
                cfop=item.cfop,
                conta="12503",
                nome_conta="MATERIAIS SECUNDARIOS",
                status="Classificado",
                confianca=98,
            )
        )
        db.session.commit()

    aprovacao = client.post("/api/financeiro/classificacao-contabil/aprovar", json={"competencia": "2026-01"})
    assert aprovacao.status_code == 200
    assert aprovacao.get_json()["status_competencia"] == "Aprovada"

    with app.app_context():
        competencia = ClassificacaoContabilCompetencia.query.filter_by(competencia="2026-01").first()
        classificacao = ClassificacaoContabilItem.query.filter_by(numero_nota="JANFECHA").first()
        classificacao_id = classificacao.id
        assert competencia.status == "Aprovada"

    editar_aprovada = client.patch(
        f"/api/financeiro/classificacao-contabil/{classificacao_id}",
        json={"conta": "12503", "nome_conta": "MATERIAIS SECUNDARIOS AJUSTADO"},
    )
    assert editar_aprovada.status_code == 200
    with app.app_context():
        competencia = ClassificacaoContabilCompetencia.query.filter_by(competencia="2026-01").first()
        assert competencia.status == "Aberta"

    fechar = client.post("/api/financeiro/classificacao-contabil/fechar", json={"competencia": "2026-01"})
    assert fechar.status_code == 200
    assert fechar.get_json()["status_competencia"] == "Fechada"

    editar_fechada = client.patch(
        f"/api/financeiro/classificacao-contabil/{classificacao_id}",
        json={"conta": "99999", "nome_conta": "BLOQUEADA"},
    )
    assert editar_fechada.status_code == 409

    reprocessar_fechada = client.post("/api/financeiro/classificacao-contabil/reprocessar", json={"competencia": "2026-01"})
    assert reprocessar_fechada.status_code == 409


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
                vencimento_pagamento_xml=datetime(2026, 4, 30),
            )
        )
        db.session.commit()

    gera = client.post(
        "/api/financeiro/contas-receber/gerar-boleto",
        json={
            "nota": "CR200",
            "cpf_cnpj": "12.345.678/0001-99",
            "nome_pagador": "Cliente BB",
        },
    )
    assert gera.status_code == 200
    payload = gera.get_json()
    assert payload["sucesso"] is True
    assert payload["boleto"]["banco"] == "Banco do Brasil"

    with app.app_context():
        boleto = BoletoContaReceber.query.filter_by(numero_nota="CR200").first()
        assert boleto is not None
        assert boleto.cpf_cnpj_pagador == "12345678000199"
        assert boleto.nome_pagador == "Cliente BB"
        assert boleto.vencimento.strftime("%d/%m/%Y") == "30/04/2026"

    lista = client.get("/api/financeiro/contas-receber/notas")
    assert lista.status_code == 200
    data = lista.get_json()
    item = next((x for x in data["itens"] if x["numero_nota"] == "CR200"), None)
    assert item is not None
    assert item["boleto_gerado"] is True

def test_api_boletos_consulta_publica_por_cpf_cnpj_retorna_boleto_local(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()
    set_logged_user(client, "fiscal_teste", "Fiscal")

    with app.app_context():
        db.session.add(
            ItemNota(
                numero_nota="CR210",
                fornecedor="Fornecedor portal",
                codigo="CR-4",
                descricao="Item portal cliente",
                qtd_real=1.0,
                status="Lançado",
                pagamento_xml=True,
                tipo_pagamento_xml="15",
                valor_pagamento_xml=321.45,
                vencimento_pagamento_xml=datetime(2026, 5, 5),
            )
        )
        db.session.commit()

    gera = client.post(
        "/api/financeiro/contas-receber/gerar-boleto",
        json={
            "nota": "CR210",
            "cpf_cnpj": "123.456.789-01",
            "nome_pagador": "Cliente Portal",
        },
    )
    assert gera.status_code == 200

    consulta = client.post(
        "/api/boletos/consultar",
        json={"modo": "cpf_cnpj", "cpf_cnpj": "12345678901"},
    )
    assert consulta.status_code == 200
    payload = consulta.get_json()
    assert payload["sucesso"] is True
    assert payload["total"] == 1
    assert payload["fonte"] == "local"
    assert payload["boletos"][0]["banco"] == "Banco do Brasil"
    assert payload["boletos"][0]["cpf_cnpj_pagador"] == "12345678901"
    assert payload["boletos"][0]["nome_pagador"] == "Cliente Portal"


def test_api_boletos_consulta_publica_por_cnpj_agrupa_por_orcamento(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()

    titulos = [
        {
            "fonte": "grv_postgres",
            "tipo": "titulo_aberto",
            "id_grv": "1",
            "nome_pagador": "Cliente Grupo",
            "cpf_cnpj_pagador": "12345678000199",
            "numero_nota": "9001",
            "documento": "9001-A",
            "orcamento": "3",
            "valor": 100.0,
            "valor_original": 100.0,
            "valor_pago": 0.0,
            "vencimento": "10/06/2026",
            "status": "Em aberto",
            "banco": "Banco do Brasil",
            "url_boleto": "https://bb.example/boleto/1.pdf",
        },
        {
            "fonte": "grv_postgres",
            "tipo": "titulo_aberto",
            "id_grv": "2",
            "nome_pagador": "Cliente Grupo",
            "cpf_cnpj_pagador": "12345678000199",
            "numero_nota": "9002",
            "documento": "9002-A",
            "orcamento": "3",
            "valor": 250.5,
            "valor_original": 250.5,
            "valor_pago": 0.0,
            "vencimento": "05/06/2026",
            "status": "Em aberto",
            "banco": "Banco do Brasil",
            "linha_digitavel": "00190.00009 00000.000000 00000.000000 1 00000000025050",
        },
    ]

    with patch(
        "conferencia_app.routes.boleto_routes.BBBoletoService.consultar_boletos",
        return_value={"fonte": "grv_postgres", "boletos": titulos},
    ):
        consulta = client.post(
            "/api/boletos/consultar",
            json={"modo": "cpf_cnpj", "cpf_cnpj": "12.345.678/0001-99"},
        )

    assert consulta.status_code == 200
    payload = consulta.get_json()
    assert payload["sucesso"] is True
    assert payload["agrupado"] is True
    assert payload["total"] == 1
    assert payload["total_titulos"] == 2
    grupo = payload["boletos"][0]
    assert grupo["tipo"] == "grupo_aberto"
    assert grupo["titulo"] == "Orçamento 3"
    assert grupo["quantidade_titulos"] == 2
    assert grupo["valor"] == 350.5
    assert grupo["vencimento"] == "05/06/2026"
    assert len(grupo["titulos"]) == 2
    assert grupo["titulos"][0]["url_boleto"] == "https://bb.example/boleto/1.pdf"
    assert grupo["titulos"][1]["linha_digitavel"]


def test_api_boletos_consulta_publica_por_cnpj_inclui_boleto_local_sem_documento_via_nf_grv(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()

    with app.app_context():
        db.session.add(
            BoletoContaReceber(
                numero_nota="NFLOCALDOC",
                chave_acesso="",
                banco="Banco do Brasil",
                valor=500.0,
                nosso_numero="LOCALDOC1",
                linha_digitavel="00190.00009 00000.000000 00000.000000 1 00000000050000",
                codigo_barras="0019000000000050000",
                status="Gerado",
                usuario_geracao="teste",
                cpf_cnpj_pagador=None,
                nome_pagador=None,
            )
        )
        db.session.commit()

    titulos = [
        {
            "fonte": "grv_postgres",
            "tipo": "titulo_aberto",
            "id_grv": "100",
            "nome_pagador": "Cliente Antigo",
            "cpf_cnpj_pagador": "12345678000199",
            "numero_nota": "NFLOCALDOC",
            "documento": "NFLOCALDOC-1/1",
            "orcamento": "90",
            "valor": 500.0,
            "valor_original": 500.0,
            "valor_pago": 0.0,
            "vencimento": "10/06/2026",
            "status": "Em aberto",
            "banco": "Banco do Brasil",
            "pode_gerar_boleto": True,
        }
    ]

    with patch(
        "conferencia_app.services.bb_boleto_service.GRVContasReceberService.consultar_abertos",
        return_value=titulos,
    ), patch("conferencia_app.services.bb_boleto_service.BBBoletoService.is_configured", return_value=False):
        consulta = client.post(
            "/api/boletos/consultar",
            json={"modo": "cpf_cnpj", "cpf_cnpj": "12.345.678/0001-99"},
        )

    assert consulta.status_code == 200
    payload = consulta.get_json()
    assert payload["sucesso"] is True
    assert payload["total_titulos"] == 2
    assert any(
        titulo.get("nosso_numero") == "LOCALDOC1"
        for grupo in payload["boletos"]
        for titulo in grupo.get("titulos", [])
    )


def test_api_boletos_consulta_publica_por_cnpj_e_nf_encontra_boleto_local_sem_documento(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()

    with app.app_context():
        db.session.add(
            BoletoContaReceber(
                numero_nota="NFCPFNF",
                chave_acesso="",
                banco="Banco do Brasil",
                valor=780.0,
                nosso_numero="NFCPFNF1",
                linha_digitavel="00190.00009 00000.000000 00000.000000 1 00000000078000",
                codigo_barras="0019000000000078000",
                status="Gerado",
                usuario_geracao="teste",
                cpf_cnpj_pagador=None,
                nome_pagador=None,
            )
        )
        db.session.commit()

    with patch(
        "conferencia_app.services.bb_boleto_service.GRVContasReceberService.consultar_abertos",
        return_value=[],
    ), patch("conferencia_app.services.bb_boleto_service.BBBoletoService.is_configured", return_value=False):
        consulta = client.post(
            "/api/boletos/consultar",
            json={
                "modo": "cpf_cnpj",
                "cpf_cnpj": "12.345.678/0001-99",
                "numero_nota": "NFCPFNF",
            },
        )

    assert consulta.status_code == 200
    payload = consulta.get_json()
    assert payload["sucesso"] is True
    assert payload["total_titulos"] == 1
    assert payload["boletos"][0]["titulos"][0]["nosso_numero"] == "NFCPFNF1"


def test_api_boletos_consulta_publica_por_cnpj_usa_api_bb(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()
    app.config.update(
        BB_CLIENT_ID="client-test",
        BB_CLIENT_SECRET="secret-test",
        BB_DEVELOPER_APPLICATION_KEY="app-key-test",
        BB_CONVENIO="1234567",
    )

    class FakeResp:
        def __init__(self, payload, status_code=200):
            self._payload = payload
            self.status_code = status_code

        def raise_for_status(self):
            if self.status_code >= 400:
                raise RuntimeError("erro bb")

        def json(self):
            return self._payload

    chamadas_get = []

    def fake_bb_request(method, url, **kwargs):
        if method == "POST":
            return FakeResp({"access_token": "token-bb", "expires_in": 3600})
        chamadas_get.append(kwargs.get("params") or {})
        return FakeResp(
            {
                "boletos": [
                    {
                        "numeroTituloCliente": "1234567890",
                        "numeroTituloBeneficiario": "NF900",
                        "valorOriginal": 450.25,
                        "dataVencimento": "2026-06-15",
                        "codigoLinhaDigitavel": "00190.00009 01234.567890 12345.678901 1 00000000045025",
                        "urlImagemBoleto": "https://bb.example/boleto/NF900.pdf",
                        "descricaoEstadoTituloCobranca": "Registrado",
                        "pagador": {"nome": "Cliente API", "numeroInscricao": "12345678000199"},
                    }
                ]
            }
        )

    with app.app_context(), patch(
        "conferencia_app.services.bb_boleto_service.GRVContasReceberService.consultar_abertos",
        return_value=[],
    ), patch(
        "conferencia_app.services.bb_boleto_service.BBBoletoService._request",
        side_effect=fake_bb_request,
    ):
        from conferencia_app.services.bb_boleto_service import BBBoletoService

        BBBoletoService._token_cache = {}
        consulta = client.post(
            "/api/boletos/consultar",
            json={"modo": "cpf_cnpj", "cpf_cnpj": "12.345.678/0001-99"},
        )

    assert consulta.status_code == 200
    payload = consulta.get_json()
    assert payload["sucesso"] is True
    assert payload["fonte"] == "bb_api"
    assert payload["total"] == 1
    assert payload["boletos"][0]["numero_nota"] == "NF900"
    assert payload["boletos"][0]["nome_pagador"] == "Cliente API"
    assert payload["boletos"][0]["titulos"][0]["url_boleto"] == "https://bb.example/boleto/NF900.pdf"
    assert any(
        params.get("cnpjPagador") == "123456780001"
        and params.get("digitoCNPJPagador") == "99"
        and params.get("numeroConvenio") == "1234567"
        for params in chamadas_get
    )


def test_api_boletos_consulta_publica_por_nf_usa_api_bb(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()
    app.config.update(
        BB_CLIENT_ID="client-test",
        BB_CLIENT_SECRET="secret-test",
        BB_DEVELOPER_APPLICATION_KEY="app-key-test",
        BB_CONVENIO="1234567",
    )

    class FakeResp:
        def __init__(self, payload, status_code=200):
            self._payload = payload
            self.status_code = status_code

        def raise_for_status(self):
            if self.status_code >= 400:
                raise RuntimeError("erro bb")

        def json(self):
            return self._payload

    chamadas_get = []

    def fake_bb_request(method, url, **kwargs):
        if method == "POST":
            return FakeResp({"access_token": "token-bb", "expires_in": 3600})
        chamadas_get.append(kwargs.get("params") or {})
        return FakeResp(
            {
                "boletos": [
                    {
                        "numeroTituloCliente": "99887766",
                        "numeroTituloBeneficiario": "11435",
                        "valorOriginal": 5239.5,
                        "dataVencimento": "2026-06-20",
                        "descricaoEstadoTituloCobranca": "Registrado",
                        "pagador": {"nome": "Cliente NF", "numeroInscricao": "30482274000125"},
                    }
                ]
            }
        )

    with app.app_context(), patch(
        "conferencia_app.services.bb_boleto_service.GRVContasReceberService.consultar_abertos",
        return_value=[],
    ), patch(
        "conferencia_app.services.bb_boleto_service.BBBoletoService._request",
        side_effect=fake_bb_request,
    ):
        from conferencia_app.services.bb_boleto_service import BBBoletoService

        BBBoletoService._token_cache = {}
        consulta = client.post("/api/boletos/consultar", json={"modo": "nota", "numero_nota": "11435"})

    assert consulta.status_code == 200
    payload = consulta.get_json()
    assert payload["sucesso"] is True
    assert payload["total"] == 1
    assert payload["boletos"][0]["nosso_numero"] == "99887766"
    assert payload["boletos"][0]["valor"] == 5239.5
    assert any(
        "numeroTituloBeneficiario" not in params
        and params.get("numeroConvenio") == "1234567"
        for params in chamadas_get
    )


def test_api_boletos_consulta_publica_por_nf_ignora_sufixo_parcela_bb(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()
    app.config.update(
        BB_CLIENT_ID="client-test",
        BB_CLIENT_SECRET="secret-test",
        BB_DEVELOPER_APPLICATION_KEY="app-key-test",
        BB_CONVENIO="1234567",
    )

    class FakeResp:
        def __init__(self, payload, status_code=200):
            self._payload = payload
            self.status_code = status_code

        def raise_for_status(self):
            if self.status_code >= 400:
                raise RuntimeError("erro bb")

        def json(self):
            return self._payload

    def fake_bb_request(method, url, **kwargs):
        if method == "POST":
            return FakeResp({"access_token": "token-bb", "expires_in": 3600})
        return FakeResp(
            {
                "boletos": [
                    {
                        "numeroTituloCliente": "PARCELA11439",
                        "numeroTituloBeneficiario": "11439-1/2",
                        "valorOriginal": 1000.0,
                        "dataVencimento": "2026-06-20",
                        "urlImagemBoleto": "https://bb.example/boleto/11439-1.pdf",
                        "descricaoEstadoTituloCobranca": "Registrado",
                        "pagador": {"nome": "Cliente Parcela", "numeroInscricao": "30482274000125"},
                    }
                ]
            }
        )

    with app.app_context(), patch(
        "conferencia_app.services.bb_boleto_service.GRVContasReceberService.consultar_abertos",
        return_value=[],
    ), patch(
        "conferencia_app.services.bb_boleto_service.BBBoletoService._request",
        side_effect=fake_bb_request,
    ):
        from conferencia_app.services.bb_boleto_service import BBBoletoService

        BBBoletoService._token_cache = {}
        consulta = client.post("/api/boletos/consultar", json={"modo": "nota", "numero_nota": "11439-1/2"})

    assert consulta.status_code == 200
    payload = consulta.get_json()
    assert payload["sucesso"] is True
    assert payload["total"] == 1
    assert payload["boletos"][0]["numero_nota"] == "11439"
    assert payload["boletos"][0]["documento"] == "11439-1/2"
    assert payload["boletos"][0]["url_boleto"] == "https://bb.example/boleto/11439-1.pdf"


def test_portal_cobranca_token_lista_documentos_e_boletos(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()

    with app.app_context():
        db.session.add(
            BoletoContaReceber(
                numero_nota="NFPORTAL",
                chave_acesso="4" * 44,
                banco="Banco do Brasil",
                valor=199.9,
                nosso_numero="PORTAL123",
                linha_digitavel="00190.00009 00000.000000 00000.000000 1 00000000019990",
                codigo_barras="00190000000000000000000000000000000000000000",
                status="Gerado",
                usuario_geracao="teste",
                cpf_cnpj_pagador="12345678000199",
                nome_pagador="Cliente Portal Token",
            )
        )
        db.session.commit()
        from conferencia_app.services.cliente_portal_service import gerar_token_nf

        token = gerar_token_nf("NFPORTAL", "4" * 44, "12345678000199")

    page = client.get(f"/portal/cobranca/{token}")
    assert page.status_code == 200

    dados = client.get(f"/api/portal/cobranca/{token}")
    assert dados.status_code == 200
    payload = dados.get_json()
    assert payload["sucesso"] is True
    assert payload["nota"]["numero"] == "NFPORTAL"
    assert payload["total_boletos"] == 1
    assert payload["boletos"][0]["nosso_numero"] == "PORTAL123"


def test_envio_nfe_gera_danfe_quando_erp_nao_tem_pdf(tmp_path):
    app = build_test_app(tmp_path)
    app.config.update(
        MAIL_SENDER="fiscal@teste.com",
        MAIL_PASSWORD="senha",
        MAIL_SENDER_NAME="Fiscal Teste",
        NFE_EMAIL_MODO_TESTE=False,
        NFE_EMAIL_PEDIDOS_PDF_DIR=str(tmp_path),
    )
    (tmp_path / "PEDIDO 11560 27.05.26.pdf").write_bytes(b"%PDF-1.7 pedido oficial erp")

    xml_bytes = build_test_nfe_xml(
        "9100",
        [{"codigo": "P1", "descricao": "Produto teste", "cfop": "5102", "quantidade": "1.0000"}],
    )
    chave = "1" * 44
    pdf_gerado = b"%PDF-1.4 DANFE teste"

    with app.app_context(), patch(
        "conferencia_app.services.nfe_email_service.buscar_nfe_emitida_erp",
        return_value={
            "numero": "9100",
            "chave": chave,
            "autorizada": True,
            "dest_nome": "Cliente Teste",
            "dest_cnpj": "11222333000144",
            "email_danfe": "",
            "xml_bytes": xml_bytes,
            "pdf_bytes": None,
        },
    ), patch(
        "conferencia_app.services.nfe_email_service.gerar_danfe",
        return_value=pdf_gerado,
    ) as gerar_danfe_mock, patch("conferencia_app.services.nfe_email_service._send_async"):
        from conferencia_app.services.nfe_email_service import enviar_nfe_por_email

        resultado = enviar_nfe_por_email(
            "9100",
            chave=chave,
            override_email="cliente@teste.com",
            envio_assincrono=True,
        )

        assert resultado["sucesso"] is True
        assert resultado["anexou_xml"] is True
        assert resultado["anexou_pdf"] is True
        gerar_danfe_mock.assert_called_once()
        log = EmailNFEnviado.query.filter_by(numero_nf="9100").first()
        assert log is not None
        assert log.anexou_xml is True
        assert log.anexou_pdf is True


def test_envio_nfe_industrializacao_anexa_pdf_pedido_compra(tmp_path):
    app = build_test_app(tmp_path)
    app.config.update(
        MAIL_SENDER="fiscal@teste.com",
        MAIL_PASSWORD="senha",
        MAIL_SENDER_NAME="Fiscal Teste",
        NFE_EMAIL_MODO_TESTE=False,
        NFE_EMAIL_PEDIDOS_PDF_DIR=str(tmp_path),
    )
    (tmp_path / "PEDIDO 11560 27.05.26.pdf").write_bytes(b"%PDF-1.7 pedido oficial erp")

    chave = "9" * 44
    xml_bytes = f"""
    <nfeProc xmlns="http://www.portalfiscal.inf.br/nfe">
        <NFe>
            <infNFe Id="NFe{chave}">
                <ide><nNF>9150</nNF></ide>
                <emit><xNome>COLUMBIA MACHINE BRASIL</xNome></emit>
                <dest>
                    <xNome>Fornecedor Industrializacao</xNome>
                    <CNPJ>11222333000144</CNPJ>
                </dest>
                <det nItem="1">
                    <prod>
                        <cProd>IND-1</cProd>
                        <xProd>Peca enviada para industrializacao</xProd>
                        <CFOP>5901</CFOP>
                        <qCom>2.0000</qCom>
                    </prod>
                </det>
                <infAdic><infCpl>ORDEM DE COMPRA: 11560</infCpl></infAdic>
            </infNFe>
        </NFe>
    </nfeProc>
    """.encode("utf-8")

    mensagens = []

    def fake_enviar_smtp(_app, msg, **_kwargs):
        mensagens.append(msg)

    with app.app_context(), patch(
        "conferencia_app.services.nfe_email_service.buscar_nfe_emitida_erp",
        return_value={
            "numero": "9150",
            "chave": chave,
            "autorizada": True,
            "dest_nome": "Fornecedor Industrializacao",
            "dest_cnpj": "11222333000144",
            "email_danfe": "",
            "xml_bytes": xml_bytes,
            "pdf_bytes": b"%PDF-1.4 DANFE ERP",
        },
    ), patch(
        "conferencia_app.services.nfe_email_service.buscar_linhas_pedido",
    ) as buscar_pedido_mock, patch(
        "conferencia_app.services.nfe_email_service.enviar_mensagem_smtp",
        side_effect=fake_enviar_smtp,
    ):
        from conferencia_app.services.nfe_email_service import enviar_nfe_por_email

        resultado = enviar_nfe_por_email(
            "9150",
            chave=chave,
            override_email="cliente@teste.com",
            envio_assincrono=False,
        )

        assert resultado["sucesso"] is True
        assert resultado["anexou_pedido_compra"] is True
        assert resultado["pedidos_compra_anexados"] == ["11560"]
        buscar_pedido_mock.assert_not_called()
        assert mensagens
        filenames = [part.get_filename() for part in mensagens[0].walk() if part.get_filename()]
        assert f"NFe-{chave}.xml" in filenames
        assert "DANFE-9150.pdf" in filenames
        assert "PedidoCompra-11560.pdf" in filenames


def test_envio_nfe_nao_duplica_quando_chamado_duas_vezes(tmp_path):
    app = build_test_app(tmp_path)
    app.config.update(
        MAIL_SENDER="fiscal@teste.com",
        MAIL_PASSWORD="senha",
        MAIL_SENDER_NAME="Fiscal Teste",
        NFE_EMAIL_MODO_TESTE=False,
    )
    xml_bytes = build_test_nfe_xml(
        "9400",
        [{"codigo": "P1", "descricao": "Produto teste", "cfop": "5102", "quantidade": "1.0000"}],
    )
    chave = "5" * 44

    mensagens = []

    def fake_enviar_smtp(_app, msg, **_kwargs):
        mensagens.append(msg)

    with app.app_context(), patch(
        "conferencia_app.services.nfe_email_service.buscar_nfe_emitida_erp",
        return_value={
            "numero": "9400",
            "chave": chave,
            "autorizada": True,
            "dest_nome": "Cliente Teste",
            "dest_cnpj": "11222333000144",
            "email_danfe": "",
            "xml_bytes": xml_bytes,
            "pdf_bytes": b"%PDF-1.4 DANFE ERP",
        },
    ), patch(
        "conferencia_app.services.nfe_email_service.enviar_mensagem_smtp",
        side_effect=fake_enviar_smtp,
    ):
        from conferencia_app.services.nfe_email_service import enviar_nfe_por_email

        # Simula clique duplo / retry de rede: a mesma NF chamada 2x seguidas,
        # como aconteceria se o botao de faturar/enviar for acionado 2x ou se
        # o gatilho automatico do faturamento sobrepuser o scheduler.
        primeiro = enviar_nfe_por_email(
            "9400", chave=chave, override_email="cliente@teste.com", origem="Auto", envio_assincrono=False,
        )
        segundo = enviar_nfe_por_email(
            "9400", chave=chave, override_email="cliente@teste.com", origem="Auto", envio_assincrono=False,
        )

        assert primeiro["sucesso"] is True
        assert segundo["sucesso"] is True
        assert segundo.get("ja_enviado") is True
        assert len(mensagens) == 1
        assert EmailNFEnviado.query.filter_by(numero_nf="9400").count() == 1


def test_scheduler_nfe_nao_repete_nota_duplicada_ou_aguardando_manual(tmp_path):
    app = build_test_app(tmp_path)
    app.config.update(NFE_EMAIL_AUTO_DESDE="2026-05-13")
    documentos = [
        {"numero": "9200", "chave": "2" * 44, "autorizada": True, "emitido_em": "2026-05-20T10:00:00"},
        {"numero": "9200", "chave": "2" * 44, "autorizada": True, "emitido_em": "2026-05-20T10:00:00"},
    ]
    chamadas = []

    def fake_enviar_nfe_por_email(numero_nf, **kwargs):
        chamadas.append(numero_nf)
        db.session.add(
            EmailNFEnviado(
                numero_nf=numero_nf,
                chave_acesso=kwargs.get("chave"),
                destinatario_email="",
                origem=kwargs.get("origem", "Auto"),
                status="AguardandoManual",
                erro_mensagem="Sem e-mail no XML, planilha ou cadastro.",
                disparado_por=kwargs.get("disparado_por"),
            )
        )
        db.session.commit()
        return {"sucesso": False, "status": "AguardandoManual"}

    with app.app_context(), patch(
        "conferencia_app.services.erp_nfe_emitidas_service.listar_nfes_emitidas_erp",
        return_value=documentos,
    ), patch(
        "conferencia_app.services.nfe_email_service.enviar_nfe_por_email",
        side_effect=fake_enviar_nfe_por_email,
    ):
        from conferencia_app.services.nfe_email_scheduler import executar_ciclo

        primeiro = executar_ciclo(app)
        segundo = executar_ciclo(app)

    assert primeiro["pendentes"] == 1
    assert primeiro["ignoradas"] == 1
    assert segundo["pendentes"] == 0
    assert segundo["ignoradas"] == 2
    assert chamadas == ["9200"]
    with app.app_context():
        assert EmailNFEnviado.query.filter_by(numero_nf="9200").count() == 1


def test_scheduler_nfe_nao_reprocessa_falha_automaticamente(tmp_path):
    app = build_test_app(tmp_path)
    app.config.update(NFE_EMAIL_AUTO_DESDE="2026-05-13")
    documentos = [{"numero": "9300", "chave": "3" * 44, "autorizada": True, "emitido_em": "2026-05-20T10:00:00"}]

    with app.app_context():
        db.session.add(
            EmailNFEnviado(
                numero_nf="9300",
                chave_acesso="3" * 44,
                destinatario_email="cliente@teste.com",
                origem="Auto",
                status="Falha",
                erro_mensagem="SMTPAuthenticationError",
            )
        )
        db.session.commit()

    with app.app_context(), patch(
        "conferencia_app.services.erp_nfe_emitidas_service.listar_nfes_emitidas_erp",
        return_value=documentos,
    ), patch("conferencia_app.services.nfe_email_service.enviar_nfe_por_email") as enviar_mock:
        from conferencia_app.services.nfe_email_scheduler import executar_ciclo

        resumo = executar_ciclo(app)

    assert resumo["ignoradas"] == 1
    assert resumo["pendentes"] == 0
    enviar_mock.assert_not_called()
    with app.app_context():
        assert EmailNFEnviado.query.filter_by(numero_nf="9300").count() == 1


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


def test_importacao_xml_cfop_5902_sozinho_vai_direto_para_documento_entrada(tmp_path):
    app = build_test_app(tmp_path)

    xml_bytes = build_test_nfe_xml(
        "4001-5902",
        [{"codigo": "RET5902", "descricao": "Remessa para industrializacao", "cfop": "5902", "quantidade": "1.0000"}],
    )

    with app.app_context():
        assert process_xml_and_store(xml_bytes, "admin", status_inicial="Pendente") == 1
        db.session.commit()
        item = ItemNota.query.filter_by(numero_nota="4001-5902").one()

    assert item.status == "Concluído"
    assert item.sem_conferencia_logistica is True
    assert item.usuario_conferencia == "admin"
    assert item.inicio_conferencia is not None
    assert item.fim_conferencia is not None


def test_importacao_xml_ignora_insumo_utilizado_servico_mesmo_com_cfop_5124(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()
    login_admin(client)

    xml_bytes = build_test_nfe_xml(
        "4001-INSUMO",
        [
            {"codigo": "INSUMO", "descricao": "  Insumo   Utilizado Serviço - taxa aplicada ", "cfop": "5124", "quantidade": "1.0000"},
            {"codigo": "VEN4", "descricao": "Item conferível", "cfop": "5124", "quantidade": "2.0000"},
        ],
    )

    with app.app_context():
        assert process_xml_and_store(xml_bytes, "admin", status_inicial="Pendente") == 1
        db.session.commit()
        itens = ItemNota.query.filter_by(numero_nota="4001-INSUMO").all()

    assert len(itens) == 1
    assert itens[0].codigo == "VEN4"

    resposta_auditor = client.get("/api/xml_auditor/nota/4001-INSUMO")
    assert resposta_auditor.status_code == 200
    assert [item["codigo"] for item in resposta_auditor.get_json()["itens"]] == ["VEN4"]


def test_importacao_xml_aceita_mesmo_numero_para_emitentes_diferentes(tmp_path):
    app = build_test_app(tmp_path)

    xml_fornecedor_a = build_test_nfe_xml(
        "1",
        [{"codigo": "MAT-A", "descricao": "ITEM TESTE A", "cfop": "5124", "quantidade": "1.0000"}],
        fornecedor="Fornecedor A",
        cnpj_emitente="11111111000111",
        chave_acesso="1" * 44,
    )
    xml_fornecedor_b = build_test_nfe_xml(
        "1",
        [{"codigo": "MAT-B", "descricao": "ITEM TESTE B", "cfop": "5124", "quantidade": "1.0000"}],
        fornecedor="Fornecedor B",
        cnpj_emitente="22222222000122",
        chave_acesso="2" * 44,
    )

    with app.app_context():
        assert process_xml_and_store(xml_fornecedor_a, "admin", status_inicial="Pendente") == 1
        assert process_xml_and_store(xml_fornecedor_b, "admin", status_inicial="Pendente") == 1
        db.session.commit()

        itens = ItemNota.query.filter_by(numero_nota="1", tipo_documento="NFE").order_by(ItemNota.id.asc()).all()

    assert len(itens) == 2
    assert {i.cnpj_emitente for i in itens} == {"11111111000111", "22222222000122"}


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


def test_retirar_nf_do_auditor_remove_nf_definitivamente(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()
    login_admin(client)

    with app.app_context():
        db.session.add(
            ItemNota(
                numero_nota="AUX900",
                fornecedor="Fornecedor Auditor",
                codigo="AUD-1",
                descricao="Item Auditor",
                qtd_real=1.0,
                status="AguardandoLiberacao",
            )
        )
        db.session.commit()

    retirar = client.post(
        "/api/xml_auditor/retirar",
        json={"nota": "AUX900", "motivo": "Devolver para ajuste de pre-nota"},
    )
    assert retirar.status_code == 200
    assert retirar.get_json()["sucesso"] is True

    fila_auditor = client.get("/api/xml_auditor/notas")
    assert fila_auditor.status_code == 200
    notas_auditor = fila_auditor.get_json()
    assert not any(str(n.get("numero")) == "AUX900" for n in notas_auditor)

    aguardando = client.get("/api/notas_aguardando_liberacao")
    assert aguardando.status_code == 200
    notas_aguardando = aguardando.get_json()
    assert not any(str(n.get("numero")) == "AUX900" for n in notas_aguardando)

    hist = client.get("/api/historico_completo?nota=AUX900")
    assert hist.status_code == 200
    data_hist = hist.get_json()
    assert isinstance(data_hist, list)
    assert len(data_hist) == 1
    assert data_hist[0]["nota"] == "AUX900"
    assert data_hist[0]["status"] == "Excluída"


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
    set_logged_user(client, "ADMIN", "Admin")

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
    set_logged_user(client, "ADMIN", "Admin")

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


def test_bootstrap_corrige_schema_legado_expedicao_conferencia_simples(tmp_path):
    db_path = tmp_path / "legacy_expedicao.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE expedicao_conferencia_simples (
            id INTEGER PRIMARY KEY,
            orcamento VARCHAR(80) NOT NULL,
            conferente VARCHAR(100) NOT NULL,
            data_conferencia DATETIME NOT NULL
        )
        """
    )
    conn.execute(
        """
        INSERT INTO expedicao_conferencia_simples (id, orcamento, conferente, data_conferencia)
        VALUES (1, 'ORC-1001', 'felipe', '2026-04-06 09:00:00')
        """
    )
    conn.commit()
    conn.close()

    app = create_app(
        {
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{db_path}",
        }
    )
    client = app.test_client()
    set_logged_user(client, "ADMIN", "Admin")

    response = client.get("/api/expedicao/conferencia-simples")

    assert response.status_code == 200
    data = response.get_json()
    assert data["resumo"]["total"] == 1
    assert data["registros"][0]["orcamento"] == "ORC-1001"
    assert data["registros"][0]["status_slug"] == "pendente_expedicao"
    assert data["registros"][0]["estorno_pendente"] is None

    conn = sqlite3.connect(db_path)
    cols_conf = {row[1] for row in conn.execute("PRAGMA table_info(expedicao_conferencia_simples)")}
    cols_foto = {row[1] for row in conn.execute("PRAGMA table_info(expedicao_conferencia_simples_foto)")}
    cols_estorno = {row[1] for row in conn.execute("PRAGMA table_info(expedicao_conferencia_simples_estorno)")}
    conn.close()

    assert {
        "tipo_referencia",
        "numero_os",
        "ordem_compra",
        "numero_nf",
        "nome_cliente",
        "cliente_origem",
        "nf_origem",
        "consyste_document_id",
        "consyste_chave",
        "transportadora",
        "placa",
        "motorista",
        "status",
        "created_at",
        "updated_at",
        "expedido_at",
        "expedido_by",
    }.issubset(cols_conf)
    assert {"conferencia_id", "file_name", "file_path", "created_at"}.issubset(cols_foto)
    assert {
        "conferencia_id",
        "solicitante",
        "motivo",
        "status",
        "admin_usuario",
        "admin_observacao",
        "resolvido_at",
        "created_at",
    }.issubset(cols_estorno)


def test_bootstrap_corrige_solicitante_nome_facilities_legado(tmp_path):
    db_path = tmp_path / "legacy_facilities.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE facilities_epi_solicitacao (
            id INTEGER PRIMARY KEY,
            colaborador_id INTEGER NOT NULL,
            tipo VARCHAR(20) NOT NULL,
            codigo_item VARCHAR(30) NOT NULL,
            nome_item VARCHAR(150) NOT NULL,
            tamanho VARCHAR(20),
            quantidade INTEGER NOT NULL DEFAULT 1,
            motivo TEXT,
            status VARCHAR(20) NOT NULL DEFAULT 'solicitado',
            solicitado_em DATETIME NOT NULL,
            criado_em DATETIME NOT NULL
        )
        """
    )
    conn.commit()
    conn.close()

    create_app(
        {
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{db_path}",
        }
    )

    conn = sqlite3.connect(db_path)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(facilities_epi_solicitacao)")}
    conn.close()

    assert "solicitante_nome" in cols


def test_cria_registro_expedicao_com_ordem_de_compra_e_cliente_manual(tmp_path):
    fotos_dir = tmp_path / "expedicao_fotos"
    app = create_app(
        {
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'test.db'}",
            "EXPEDICAO_CONFERENCIA_FOTOS_DIR": str(fotos_dir),
        }
    )
    client = app.test_client()
    set_logged_user(client, "ADMIN", "Admin")

    with patch(
        "conferencia_app.routes.api_routes._consultar_nf_emitida_exp_conferencia",
        return_value={"encontrada": False},
    ):
        response = client.post(
            "/api/expedicao/conferencia-simples",
            data={
                "tipo_referencia": "OrdemCompra",
                "ordem_compra": "OC-9001",
                "numero_nf": "12345",
                "nome_cliente": "Fornecedor Manual",
                "transportadora": "Trans XPTO",
                "placa": "abc1234",
                "motorista": "Joao",
                "fotos": (io.BytesIO(b"fake-image"), "foto.jpg"),
            },
            content_type="multipart/form-data",
        )

    assert response.status_code == 201
    data = response.get_json()
    assert data["registro"]["tipo_referencia"] == "OrdemCompra"
    assert data["registro"]["ordem_compra"] == "OC-9001"
    assert data["registro"]["orcamento"] == ""
    assert data["registro"]["numero_nf"] == "12345"
    assert data["registro"]["nome_cliente"] == "Fornecedor Manual"
    assert data["registro"]["cliente_origem"] == "Manual"
    assert len(data["registro"]["fotos"]) == 1

    with app.app_context():
        registro = ExpedicaoConferenciaSimples.query.one()
        assert registro.tipo_referencia == "OrdemCompra"
        assert registro.ordem_compra == "OC-9001"
        assert registro.orcamento == ""
        assert registro.numero_os is None
        assert registro.nome_cliente == "Fornecedor Manual"


def test_consulta_nf_expedicao_usa_erp_emitidas(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()
    set_logged_user(client, "conferente_teste", "Conferente")

    with patch(
        "conferencia_app.routes.api_routes.buscar_nfe_emitida_erp",
        return_value={
            "autorizada": True,
            "numero": "99881",
            "dest_nome": "Cliente ERP Saida",
            "dest_cnpj": "11.222.333/0001-44",
            "chave": "3" * 44,
            "cod_cliente": "C123",
        },
    ):
        response = client.get("/api/expedicao/conferencia-simples/consultar-nf?numero_nf=99881")

    assert response.status_code == 200
    data = response.get_json()
    assert data["encontrada"] is True
    assert data["origem"] == "ERP"
    assert data["nome_cliente"] == "Cliente ERP Saida"
    assert data["cnpj"] == "11222333000144"
    assert data["nfs"][0]["chave"] == "3" * 44


def test_cria_registro_expedicao_aceita_multiplas_nfs_do_mesmo_cnpj(tmp_path):
    fotos_dir = tmp_path / "expedicao_fotos"
    app = create_app(
        {
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'test.db'}",
            "EXPEDICAO_CONFERENCIA_FOTOS_DIR": str(fotos_dir),
        }
    )
    client = app.test_client()
    set_logged_user(client, "ADMIN", "Admin")

    respostas_consyste = {
        "12345": {
            "encontrada": True,
            "numero_nf": "12345",
            "nome_cliente": "Cliente XPTO",
            "cnpj_cliente": "11222333000144",
            "documento_id": "doc-12345",
        },
        "67890": {
            "encontrada": True,
            "numero_nf": "67890",
            "nome_cliente": "Cliente XPTO",
            "cnpj_cliente": "11222333000144",
            "documento_id": "doc-67890",
        },
    }

    with patch(
        "conferencia_app.routes.api_routes._consultar_nf_emitida_exp_conferencia",
        side_effect=lambda numero_nf: respostas_consyste[numero_nf],
    ):
        response = client.post(
            "/api/expedicao/conferencia-simples",
            data={
                "tipo_referencia": "OrdemCompra",
                "ordem_compra": "OC-9010",
                "numero_nf": "12345, 67890",
                "nome_cliente": "Cliente Manual",
                "transportadora": "Trans Multi",
                "placa": "abc1234",
                "motorista": "Carlos",
                "fotos": (io.BytesIO(b"fake-image"), "foto.jpg"),
            },
            content_type="multipart/form-data",
        )

    assert response.status_code == 201
    data = response.get_json()
    assert data["registro"]["numero_nf"] == "12345, 67890"
    assert data["registro"]["nome_cliente"] == "Cliente Manual"
    assert data["registro"]["cliente_origem"] == "Manual"

    with app.app_context():
        registro = ExpedicaoConferenciaSimples.query.one()
        assert registro.numero_nf == "12345, 67890"
        assert registro.nome_cliente == "Cliente Manual"
        assert registro.nf_origem == "Manual"


def test_completa_registro_expedicao_com_orcamento_e_os(tmp_path):
    fotos_dir = tmp_path / "expedicao_fotos"
    app = create_app(
        {
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'test.db'}",
            "EXPEDICAO_CONFERENCIA_FOTOS_DIR": str(fotos_dir),
        }
    )
    client = app.test_client()
    set_logged_user(client, "ADMIN", "Admin")

    with app.app_context():
        agora = datetime.now()
        registro = ExpedicaoConferenciaSimples(
            orcamento="",
            tipo_referencia="Orcamento",
            conferente="admin",
            data_conferencia=agora,
            status="Pendente de expedição",
            created_at=agora,
            updated_at=agora,
        )
        db.session.add(registro)
        db.session.commit()
        registro_id = registro.id

    with patch(
        "conferencia_app.routes.api_routes._consultar_nf_emitida_exp_conferencia",
        return_value={"encontrada": False},
    ):
        response = client.post(
            f"/api/expedicao/conferencia-simples/{registro_id}/completar",
            data={
                "tipo_referencia": "Orcamento",
                "orcamento": "ORC-7788",
                "numero_os": "OS-991",
                "numero_nf": "99887",
                "nome_cliente": "Cliente Manual",
                "transportadora": "Transportadora Azul",
                "placa": "def5678",
                "motorista": "Maria",
                "fotos": (io.BytesIO(b"fake-image-2"), "saida.jpg"),
            },
            content_type="multipart/form-data",
        )

    assert response.status_code == 200
    data = response.get_json()
    assert data["registro"]["tipo_referencia"] == "Orcamento"
    assert data["registro"]["orcamento"] == "ORC-7788"
    assert data["registro"]["numero_os"] == "OS-991"
    assert data["registro"]["ordem_compra"] == ""
    assert data["registro"]["numero_nf"] == "99887"
    assert data["registro"]["nome_cliente"] == "Cliente Manual"
    assert len(data["registro"]["fotos"]) == 1

    with app.app_context():
        registro = ExpedicaoConferenciaSimples.query.get(registro_id)
        assert registro.orcamento == "ORC-7788"
        assert registro.numero_os == "OS-991"
        assert registro.ordem_compra is None
        assert registro.nome_cliente == "Cliente Manual"
        assert registro.placa == "DEF5678"
        assert registro.status == "Pendente de expedição"
        assert registro.expedido_at is None
        assert registro.expedido_by is None


def test_completar_registro_expedicao_bloqueia_multiplas_nfs_com_cnpj_diferente(tmp_path):
    fotos_dir = tmp_path / "expedicao_fotos"
    app = create_app(
        {
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'test.db'}",
            "EXPEDICAO_CONFERENCIA_FOTOS_DIR": str(fotos_dir),
        }
    )
    client = app.test_client()
    set_logged_user(client, "ADMIN", "Admin")

    with app.app_context():
        agora = datetime.now()
        registro = ExpedicaoConferenciaSimples(
            orcamento="",
            tipo_referencia="Orcamento",
            conferente="admin",
            data_conferencia=agora,
            status="Pendente de expedição",
            created_at=agora,
            updated_at=agora,
        )
        db.session.add(registro)
        db.session.commit()
        registro_id = registro.id

    respostas_consyste = {
        "99887": {
            "encontrada": True,
            "numero_nf": "99887",
            "nome_cliente": "Cliente A",
            "cnpj_cliente": "11222333000144",
            "documento_id": "doc-99887",
        },
        "99888": {
            "encontrada": True,
            "numero_nf": "99888",
            "nome_cliente": "Cliente B",
            "cnpj_cliente": "55666777000188",
            "documento_id": "doc-99888",
        },
    }

    with patch(
        "conferencia_app.routes.api_routes._consultar_nf_emitida_exp_conferencia",
        side_effect=lambda numero_nf: respostas_consyste[numero_nf],
    ):
        response = client.post(
            f"/api/expedicao/conferencia-simples/{registro_id}/completar",
            data={
                "tipo_referencia": "Orcamento",
                "orcamento": "ORC-7799",
                "numero_os": "OS-992",
                "numero_nf": "99887, 99888",
                "transportadora": "Transportadora Azul",
                "placa": "def5678",
                "motorista": "Maria",
                "fotos": (io.BytesIO(b"fake-image-2"), "saida.jpg"),
            },
            content_type="multipart/form-data",
        )

    assert response.status_code == 409
    data = response.get_json()
    assert "CNPJ" in data["error"]

    with app.app_context():
        registro = ExpedicaoConferenciaSimples.query.get(registro_id)
        assert not registro.numero_nf
        assert registro.orcamento == ""


def test_excluir_registro_expedicao_remove_vinculos_mesmo_com_foto_perdida(tmp_path):
    app = create_app(
        {
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'test.db'}",
        }
    )
    enable_sqlite_foreign_keys(app)
    client = app.test_client()
    login_admin(client)

    with app.app_context():
        agora = datetime.now()
        registro = ExpedicaoConferenciaSimples(
            orcamento="ORC-404",
            tipo_referencia="Orcamento",
            conferente="admin",
            data_conferencia=agora,
            status="Aguardando estorno",
            created_at=agora,
            updated_at=agora,
        )
        db.session.add(registro)
        db.session.flush()

        db.session.add(
            ExpedicaoConferenciaSimplesFoto(
                conferencia_id=registro.id,
                file_name="foto-perdida.jpg",
                file_path=str((tmp_path / "arquivo_nao_existe.jpg").resolve()),
            )
        )
        db.session.add(
            ExpedicaoConferenciaSimplesEstorno(
                conferencia_id=registro.id,
                solicitante="admin",
                motivo="teste",
                status="Pendente",
            )
        )
        db.session.commit()
        registro_id = registro.id

    response = client.delete(f"/api/expedicao/conferencia-simples/{registro_id}")

    assert response.status_code == 200
    assert response.get_json()["sucesso"] is True

    with app.app_context():
        assert ExpedicaoConferenciaSimples.query.get(registro_id) is None
        assert ExpedicaoConferenciaSimplesFoto.query.filter_by(conferencia_id=registro_id).count() == 0
        assert ExpedicaoConferenciaSimplesEstorno.query.filter_by(conferencia_id=registro_id).count() == 0


def test_foto_expedicao_drive_e_servida_pela_rota_do_sistema(tmp_path):
    app = create_app(
        {
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'test.db'}",
        }
    )
    client = app.test_client()
    login_admin(client)

    with app.app_context():
        agora = datetime.now()
        registro = ExpedicaoConferenciaSimples(
            orcamento="ORC-DRIVE",
            tipo_referencia="Orcamento",
            conferente="admin",
            data_conferencia=agora,
            status="Pendente de expediÃ§Ã£o",
            created_at=agora,
            updated_at=agora,
        )
        db.session.add(registro)
        db.session.flush()
        foto = ExpedicaoConferenciaSimplesFoto(
            conferencia_id=registro.id,
            file_name="foto-drive.jpg",
            file_path="https://drive.google.com/thumbnail?id=drive-file-123&sz=w1600",
        )
        db.session.add(foto)
        db.session.commit()
        registro_id = registro.id
        foto_id = foto.id

    list_response = client.get("/api/expedicao/conferencia-simples")
    assert list_response.status_code == 200
    foto_payload = list_response.get_json()["registros"][0]["fotos"][0]
    assert foto_payload["url"] == f"/api/expedicao/conferencia-simples/{registro_id}/foto/{foto_id}"
    assert "drive.google.com" not in foto_payload["url"]

    downloaded = Mock(data=b"fake-drive-image", mimetype="image/jpeg", file_name="foto-drive.jpg")
    with patch("conferencia_app.routes.api_routes.download_drive_url", return_value=downloaded) as mocked_download:
        photo_response = client.get(f"/api/expedicao/conferencia-simples/{registro_id}/foto/{foto_id}")

    assert photo_response.status_code == 200
    assert photo_response.data == b"fake-drive-image"
    assert photo_response.mimetype == "image/jpeg"
    mocked_download.assert_called_once_with(
        "https://drive.google.com/thumbnail?id=drive-file-123&sz=w1600",
        default_name="foto-drive.jpg",
    )


def test_drive_credentials_prefere_oauth_quando_oauth_e_service_account_existem(tmp_path):
    app = create_app(
        {
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'test.db'}",
            "GOOGLE_DRIVE_SERVICE_ACCOUNT_JSON": '{"type":"service_account","client_email":"svc@test","private_key":"key"}',
            "GOOGLE_DRIVE_OAUTH_TOKEN_JSON": '{"client_id":"oauth-antigo"}',
        }
    )

    with app.app_context():
        oauth_creds = Mock(expired=False)
        with patch(
            "google.oauth2.service_account.Credentials.from_service_account_info",
            return_value="service-creds",
        ) as mocked_service, patch(
            "google.oauth2.credentials.Credentials.from_authorized_user_info",
            return_value=oauth_creds,
        ) as mocked_oauth:
            from conferencia_app.services.expedicao_photo_storage import _drive_credentials

            assert _drive_credentials() is oauth_creds

    mocked_oauth.assert_called_once()
    mocked_service.assert_not_called()


def test_canhoto_expedicao_faz_fallback_local_quando_drive_sem_cota(tmp_path):
    fotos_dir = tmp_path / "expedicao_fotos"
    app = create_app(
        {
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'test.db'}",
            "EXPEDICAO_CONFERENCIA_FOTOS_DIR": str(fotos_dir),
        }
    )
    client = app.test_client()
    set_logged_user(client, "ADMIN", "Admin")

    with app.app_context():
        registro = ExpedicaoConferenciaSimples(
            orcamento="ORC-CANHOTO",
            conferente="felipe",
            status="Expedido",
            expedido_at=datetime.now(),
            expedido_by="felipe",
        )
        db.session.add(registro)
        db.session.commit()
        registro_id = registro.id

    with patch("conferencia_app.routes.api_routes.using_drive", return_value=True), patch(
        "conferencia_app.routes.api_routes.upload_to_drive",
        side_effect=RuntimeError("service account sem storage quota"),
    ):
        response = client.post(
            f"/api/expedicao/conferencia-simples/{registro_id}/canhoto",
            data={"canhoto": (io.BytesIO(b"fake-canhoto-image"), "canhoto.jpg")},
            content_type="multipart/form-data",
        )

    assert response.status_code == 200
    data = response.get_json()
    assert data["registro"]["status"] == "Finalizado"

    with app.app_context():
        atualizado = ExpedicaoConferenciaSimples.query.get(registro_id)
        assert atualizado.canhoto_file_name
        assert atualizado.canhoto_file_path
        assert Path(atualizado.canhoto_file_path).exists()
        assert atualizado.status == "Finalizado"

    arquivo = client.get(f"/api/expedicao/conferencia-simples/{registro_id}/canhoto")
    assert arquivo.status_code == 200
    assert arquivo.data == b"fake-canhoto-image"


def test_inventario_logistica_comparacao_grv_e_cega_por_padrao(tmp_path):
    """A listagem de inventario so retorna a comparacao com o GRV quando
    comparar_grv=1 e passado explicitamente (usado so pela tela de
    consulta) - a tela de contagem nunca deve receber esse campo, para nao
    vesar a conferencia com o saldo esperado. O valor comparado e' o
    snapshot gravado no momento de CADA contagem (POST), nao uma nova
    consulta ao GRV feita na hora da listagem."""
    app = build_test_app(tmp_path)
    client = app.test_client()
    login_admin(client)

    estoque_fake = {
        "por_local": {
            "SKU-OK|A01-02": {"qtde_total": 10},
            "SKU-DIV|A01-03": {"qtde_total": 8},
        },
        "por_codigo": {
            "SKU-OK": {"qtde_total": 10.0},
            "SKU-DIV": {"qtde_total": 8.0},
        },
    }
    with patch(
        "conferencia_app.routes.logistica_inventario_routes.buscar_estoque_grv",
        return_value=estoque_fake,
    ):
        client.post("/api/logistica/inventario-inicial", json={
            "local_codigo": "A01-02", "codigo_produto": "SKU-OK",
            "unidade_medida": "UN", "quantidade": 10,
        })
        client.post("/api/logistica/inventario-inicial", json={
            "local_codigo": "A01-03", "codigo_produto": "SKU-DIV",
            "unidade_medida": "UN", "quantidade": 5,
        })

    # Tela de contagem (Novo Inventario): sem comparar_grv, sem vazamento.
    resp_cego = client.get("/api/logistica/inventario-inicial?limit=10")
    for registro in resp_cego.get_json()["registros"]:
        assert "qtde_grv" not in registro
        assert "divergente" not in registro

    # Tela de consulta: pede o snapshot gravado na contagem - sem precisar
    # (nem poder) reconsultar o GRV agora.
    resp_comparado = client.get("/api/logistica/inventario-inicial?limit=10&comparar_grv=1")

    registros = {r["codigo_produto"]: r for r in resp_comparado.get_json()["registros"]}
    assert registros["SKU-OK"]["qtde_grv"] == 10.0
    assert registros["SKU-OK"]["divergente"] is False
    assert registros["SKU-DIV"]["qtde_grv"] == 8.0
    assert registros["SKU-DIV"]["divergente"] is True


def test_inventario_ajuste_calcula_impacto_financeiro_com_custo_medio_do_grv(tmp_path):
    """O ajuste automatico criado quando uma contagem diverge do GRV grava
    o custo medio (tproduto.preco_custo, vindo do bridge do ERP) junto com a quantidade -
    a API de ajustes calcula diferenca_valor (R$) = diferenca * custo_medio
    na hora de responder, sem armazenar o valor derivado. Sem custo no GRV
    pro codigo, custo_medio/diferenca_valor ficam None (nao adivinha valor)."""
    app = build_test_app(tmp_path)
    client = app.test_client()
    login_admin(client)

    estoque_fake = {
        "por_local": {
            "SKU-CUSTO|A01-05": {"qtde_total": 8, "custo_medio": 12.5},
            "SKU-SEM-CUSTO|A01-06": {"qtde_total": 3, "custo_medio": None},
        },
        "por_codigo": {
            "SKU-CUSTO": {"qtde_total": 8.0, "custo_medio": 12.5},
            "SKU-SEM-CUSTO": {"qtde_total": 3.0, "custo_medio": None},
        },
    }
    with patch(
        "conferencia_app.routes.logistica_inventario_routes.buscar_estoque_grv",
        return_value=estoque_fake,
    ):
        resp_com_custo = client.post("/api/logistica/inventario-inicial", json={
            "local_codigo": "A01-05", "codigo_produto": "SKU-CUSTO",
            "unidade_medida": "UN", "quantidade": 10,
        })
        resp_sem_custo = client.post("/api/logistica/inventario-inicial", json={
            "local_codigo": "A01-06", "codigo_produto": "SKU-SEM-CUSTO",
            "unidade_medida": "UN", "quantidade": 5,
        })

    assert resp_com_custo.get_json()["ajuste_aberto"] is not None
    assert resp_sem_custo.get_json()["ajuste_aberto"] is not None

    ajustes = {a["codigo_produto"]: a for a in client.get("/api/logistica/inventario-ajustes").get_json()["ajustes"]}

    com_custo = ajustes["SKU-CUSTO"]
    assert com_custo["diferenca"] == 2.0
    assert com_custo["custo_medio"] == 12.5
    assert com_custo["diferenca_valor"] == 25.0

    sem_custo = ajustes["SKU-SEM-CUSTO"]
    assert sem_custo["diferenca"] == 2.0
    assert sem_custo["custo_medio"] is None
    assert sem_custo["diferenca_valor"] is None


def test_inventario_ajuste_pular_etapa_exige_permissao_extra_e_avanca_uma_etapa_por_vez(tmp_path):
    """"Pular Etapa" (espelho do "Pular Status" do Comex) so avanca com a
    permissao extra de gerencia (PAGE_LOGISTICA_INVENTARIO_PULAR_ETAPA) - o
    acesso normal ao modulo (role Logística) nao basta - e sempre avanca UMA
    etapa por chamada, ignorando quem normalmente faria aquela etapa."""
    app = build_test_app(tmp_path)
    client = app.test_client()

    with app.app_context():
        from conferencia_app.models import LogisticaInventarioAjuste

        ajuste = LogisticaInventarioAjuste(
            codigo_produto="SKU-PULA", local_codigo="A01-09", unidade_medida="UN",
            qtde_contada=12, qtde_estoque_no_momento=10, diferenca=2,
            status_modulo="Validacao", status_slug="validacao",
        )
        db.session.add(ajuste)
        db.session.commit()
        ajuste_id = ajuste.id

    # Role "Logística" tem acesso ao modulo, mas nao a permissao extra de
    # Pular Etapa - deve ser barrado.
    set_logged_user(client, "logistica_teste", "Logística")
    resp_negado = client.post(f"/api/logistica/inventario-ajustes/{ajuste_id}/pular-etapa")
    assert resp_negado.status_code == 403

    with app.app_context():
        from conferencia_app.models import LogisticaInventarioAjuste
        assert LogisticaInventarioAjuste.query.get(ajuste_id).status_modulo == "Validacao"

    # Admin tem a permissao extra por padrao (catalogo inteiro) - avanca uma
    # etapa por chamada: Validacao -> Finance -> Fiscal -> Concluido.
    login_admin(client)
    for esperado in ("Finance", "Fiscal", "Concluido"):
        resp = client.post(f"/api/logistica/inventario-ajustes/{ajuste_id}/pular-etapa")
        assert resp.status_code == 200, resp.get_json()
        assert resp.get_json()["ajuste"]["status_modulo"] == esperado

    resp_fim = client.post(f"/api/logistica/inventario-ajustes/{ajuste_id}/pular-etapa")
    assert resp_fim.status_code == 400


def test_expedicao_fat_sync_preserva_id_dos_itens_entre_ciclos(tmp_path):
    """Regressao: sincronizar_ordens() apagava e recriava os itens a cada
    ciclo (o poll automatico roda a cada poucos minutos), trocando o id de
    cada item no meio de uma conferencia em andamento. O conferente
    preenchia as quantidades com os ids antigos (ja carregados no
    navegador) e, ao finalizar, TODOS os itens apareciam como "sem
    quantidade informada" mesmo estando preenchidos - porque o id enviado
    nao batia mais com o id (novo) no banco."""
    app = build_test_app(tmp_path)

    linhas = [
        {"cod_ordem_fat": 5001, "cliente": "Cliente A", "orcamento": "ORC1", "status": "em_aberto",
         "cod_interno": "MAT1", "item": "Material 1", "n_os": "OS1", "qtde_a_faturar": 10},
        {"cod_ordem_fat": 5001, "cliente": "Cliente A", "orcamento": "ORC1", "status": "em_aberto",
         "cod_interno": "MAT2", "item": "Material 2", "n_os": "OS1", "qtde_a_faturar": 5},
    ]

    with app.app_context():
        from conferencia_app.models import ExpedicaoOrdemFat, ExpedicaoOrdemFatItem
        from conferencia_app.services import expedicao_fat_service as svc

        with patch.object(svc, "buscar_ordens_api", return_value=linhas):
            svc.sincronizar_ordens()
            ordem = ExpedicaoOrdemFat.query.filter_by(cod_ordem_fat=5001).first()
            ids_antes = sorted(it.id for it in ordem.itens)

            # Simula o poll automatico rodando de novo enquanto o conferente
            # ainda esta com a tela aberta (mesmos dados vindos da origem).
            svc.sincronizar_ordens()
            ordem_depois = ExpedicaoOrdemFat.query.filter_by(cod_ordem_fat=5001).first()
            ids_depois = sorted(it.id for it in ordem_depois.itens)

        assert ids_antes == ids_depois


def test_adicionar_nf_manual_ao_romaneio_busca_dados_na_bridge_erp(tmp_path):
    """NF que nao passou pela Conferencia de Expedicao (sem ExpedicaoOrdemFat/
    ST correspondente) deve puxar cliente/peso/volumes direto do XML da NF-e
    retornado pela bridge do ERP, em vez de depender so do que foi digitado."""
    app = build_test_app(tmp_path)
    client = app.test_client()
    login_admin(client)

    xml_bytes = """
    <nfeProc xmlns="http://www.portalfiscal.inf.br/nfe">
        <NFe>
            <infNFe Id="NFe12345678901234567890123456789012345678901234">
                <ide><nNF>8800</nNF></ide>
                <emit><xNome>COLUMBIA MACHINE BRASIL</xNome></emit>
                <dest><xNome>Cliente Fora Da Conferencia</xNome><CNPJ>11222333000144</CNPJ></dest>
                <transp>
                    <vol><qVol>3</qVol><esp>Caixa</esp><pesoB>45.500</pesoB></vol>
                </transp>
                <total><ICMSTot><vNF>100.00</vNF></ICMSTot></total>
            </infNFe>
        </NFe>
    </nfeProc>
    """.encode("utf-8")

    with app.app_context():
        from conferencia_app.models import ExpedicaoRomaneio

        romaneio = ExpedicaoRomaneio(
            numero_romaneio="ROM-TESTE-MANUAL",
            data_romaneio=datetime.now(),
            tipo_frete="FOB",
            status="Rascunho",
            criado_por="ADMIN",
        )
        db.session.add(romaneio)
        db.session.commit()
        romaneio_id = romaneio.id

    with patch(
        "conferencia_app.routes.expedicao_romaneio_routes.buscar_nfe_emitida_erp",
        return_value={"numero": "8800", "chave": "1" * 44, "autorizada": True, "xml_bytes": xml_bytes},
    ):
        resp = client.post(
            f"/api/expedicao/romaneio-fat/{romaneio_id}/nf",
            json={"numero_nf": "8800"},
        )

    assert resp.status_code == 201
    with app.app_context():
        from conferencia_app.models import ExpedicaoRomaneioNF

        nf = ExpedicaoRomaneioNF.query.filter_by(romaneio_id=romaneio_id, numero_nf="8800").first()
        assert nf is not None
        assert nf.cliente == "Cliente Fora Da Conferencia"
        assert nf.qtde_volumes == 3
        assert nf.especie_volumes == "Caixa"
        assert round(nf.peso_bruto, 1) == 45.5


def test_expedicao_faturamento_parcial_total(tmp_path):
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


def test_process_xml_store_nfse_com_tipo_documento(tmp_path):
    app = build_test_app(tmp_path)

    with app.app_context():
        added = process_xml_and_store(build_test_nfse_xml(numero_nota="7010"), "admin", status_inicial="AguardandoLiberacao")
        db.session.commit()

        assert added == 1
        item = ItemNota.query.filter_by(numero_nota="7010").first()
        assert item is not None
        assert item.tipo_documento == "NFSE"
        assert item.sem_conferencia_logistica is True
        assert item.documento_externo_id == "NFSE7010"
        assert item.codigo_verificacao == "ABCD1234"


def test_consyste_download_nfse_importa_por_documento_id(tmp_path):
    app = build_test_app(tmp_path)
    app.config["CONSYSTE_TOKEN"] = "token_teste_valido"
    client = app.test_client()
    login_admin(client)

    xml_nfse = build_test_nfse_xml(numero_nota="7020")
    with patch(
        "conferencia_app.routes.api_routes.download_documento_consyste",
        return_value=(True, 200, xml_nfse),
    ) as download_mock:
        response = client.post(
            "/api/consyste/download",
            json={"modelo": "nfse", "documento_id": "12345"},
        )

    assert response.status_code == 200
    download_mock.assert_called_once()

    with app.app_context():
        item = ItemNota.query.filter_by(numero_nota="7020").first()
        assert item is not None
        assert item.tipo_documento == "NFSE"
        assert item.status == "AguardandoLiberacao"


def test_liberar_nfse_envia_direto_entrada_concluido(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()
    login_admin(client)

    with app.app_context():
        db.session.add(
            ItemNota(
                numero_nota="7030",
                fornecedor="Prestador Servicos",
                tipo_documento="NFSE",
                documento_externo_id="NFSE7030",
                codigo="SERVICO",
                descricao="Servico tecnico",
                qtd_real=1.0,
                status="AguardandoLiberacao",
                auditor_status="SemInconsistencia",
                sem_conferencia_logistica=True,
                pedido_compra="PO-123",
            )
        )
        db.session.commit()

    response = client.post(
        "/api/xml_auditor/liberar",
        json={"nota": "7030"},
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["sucesso"] is True

    with app.app_context():
        item = ItemNota.query.filter_by(numero_nota="7030").first()
        assert item.status == "Concluído"


def test_auditor_preserva_codigo_material_do_erp_sem_formatar(tmp_path):
    app = build_test_app(tmp_path)

    with app.app_context():
        db.session.add(
            ItemNota(
                numero_nota="7040",
                fornecedor="Fornecedor ERP",
                codigo="COD-XML",
                descricao="Item XML",
                qtd_real=1.0,
                valor_produto=10.0,
                status="AguardandoLiberacao",
            )
        )
        db.session.commit()
        item_id = ItemNota.query.filter_by(numero_nota="7040").first().id

        resultado = {
            "pares": [
                {
                    "item_id": item_id,
                    "po_index": 0,
                    "po_pedido": "11560",
                    "po_codigo_material": "190300016",
                    "po_descricao_material": "PARAFUSO ALLEN",
                }
            ]
        }

        from conferencia_app.routes.api_routes import _sincronizar_codigo_interno_por_pedido

        _sincronizar_codigo_interno_por_pedido("7040", "11560", resultado)

        item = ItemNota.query.filter_by(numero_nota="7040").first()
        assert item.codigo == "190300016"
        assert item.descricao == "PARAFUSO ALLEN"


def test_process_xml_store_nfse_sem_numero_usa_fallback(tmp_path):
    app = build_test_app(tmp_path)

    with app.app_context():
        added = process_xml_and_store(
            build_test_nfse_xml_sem_numero(id_externo="NFSESEMNUM123456"),
            "admin",
            status_inicial="AguardandoLiberacao",
        )
        db.session.commit()

        assert added == 1
        item = ItemNota.query.filter_by(tipo_documento="NFSE", documento_externo_id="NFSESEMNUM123456").first()
        assert item is not None
        assert item.status == "AguardandoLiberacao"
        assert str(item.numero_nota or "").strip() != ""


def test_motorista_nao_realizada_exige_justificativa(tmp_path):
    app = build_test_app(tmp_path)
    with app.app_context():
        db.create_all()
        veiculo = AgendamentoVeiculo(codigo="VAN-TST", nome_exibicao="Van Teste")
        db.session.add(veiculo)
        db.session.flush()
        viagem = Viagem(
            codigo="VG-TST-NAO-REALIZADA",
            veiculo_id=veiculo.id,
            status="EmAndamento",
            liberada=True,
        )
        db.session.add(viagem)
        db.session.flush()
        parada = ViagemParada(
            viagem_id=viagem.id,
            sequencia=1,
            tipo="ENTREGA",
            parceiro_nome="Cliente Teste",
            status="Pendente",
        )
        db.session.add(parada)
        db.session.commit()

        secret = (app.config.get("SECRET_KEY") or "dev").encode("utf-8")
        token = hmac.new(secret, f"viagem:{viagem.id}".encode("utf-8"), hashlib.sha256).hexdigest()[:16]
        viagem_id = viagem.id
        parada_id = parada.id

    client = app.test_client()
    url = f"/motorista/viagem/{viagem_id}/{token}/parada/{parada_id}/concluir"

    sem_motivo = client.post(url, json={"resultado": "NaoRealizada"})
    assert sem_motivo.status_code == 400
    assert "justificativa" in sem_motivo.get_json()["msg"].lower()

    com_motivo = client.post(url, json={"resultado": "NaoRealizada", "observacao": "Cliente fechado no horario."})
    assert com_motivo.status_code == 200
    assert com_motivo.get_json()["status"] == "Nao_realizada"

    with app.app_context():
        atualizada = db.session.get(ViagemParada, parada_id)
        assert atualizada.status == "Nao_realizada"
        assert atualizada.observacao == "Cliente fechado no horario."
