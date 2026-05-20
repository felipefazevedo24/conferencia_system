from flask import Flask
from sqlalchemy import inspect
from werkzeug.security import generate_password_hash

from .extensions import db
from .models import (
    AgendamentoMotorista,
    AgendamentoVeiculo,
    DepositoWMS,
    FacilitiesColaborador,
    FacilitiesEpiMaterial,
    Usuario,
)


DEFAULT_ADMIN_USERNAME = "ADMIN"
DEFAULT_ADMIN_PASSWORD = "admin1234"


def _has_table(table_name: str) -> bool:
    return inspect(db.engine).has_table(table_name)


def _get_column_names(table_name: str) -> set[str]:
    if not _has_table(table_name):
        return set()
    return {column["name"] for column in inspect(db.engine).get_columns(table_name)}


def _get_index_names(table_name: str) -> set[str]:
    if not _has_table(table_name):
        return set()
    return {index["name"] for index in inspect(db.engine).get_indexes(table_name)}


def _get_column_details(table_name: str, column_name: str) -> dict | None:
    if not _has_table(table_name):
        return None
    for column in inspect(db.engine).get_columns(table_name):
        if column["name"] == column_name:
            return column
    return None


def _create_index_if_missing(conn, table_name: str, index_name: str, ddl: str) -> None:
    if index_name in _get_index_names(table_name):
        return
    conn.execute(db.text(ddl))
    conn.commit()


def _ensure_usuario_password_capacity() -> None:
    column = _get_column_details("usuario", "password")
    if not column:
        return

    col_type = column.get("type")
    current_length = getattr(col_type, "length", None)
    if current_length is not None and current_length >= 255:
        return

    if db.engine.dialect.name == "mysql":
        with db.engine.connect() as conn:
            conn.execute(db.text("ALTER TABLE usuario MODIFY COLUMN password VARCHAR(255) NOT NULL"))
            conn.commit()


def _admin_password_needs_reset(password: str | None) -> bool:
    stored = str(password or "").strip()
    if not stored:
        return True
    if stored == DEFAULT_ADMIN_PASSWORD:
        return True
    if stored.startswith("scrypt:"):
        return stored.count("$") < 2 or len(stored) < 140
    if stored.startswith("pbkdf2:"):
        return stored.count("$") < 2 or len(stored) < 80
    return ":" not in stored and "$" not in stored


def _ensure_default_admin_user() -> None:
    existing_admin = Usuario.query.filter_by(username=DEFAULT_ADMIN_USERNAME).first()
    if not existing_admin:
        old_admin = Usuario.query.filter_by(username="admin").first()
        if old_admin:
            old_admin.username = DEFAULT_ADMIN_USERNAME
            old_admin.role = "Admin"
            old_admin.password = generate_password_hash(DEFAULT_ADMIN_PASSWORD)
            db.session.commit()
            return

        admin = Usuario(
            username=DEFAULT_ADMIN_USERNAME,
            email=None,
            password=generate_password_hash(DEFAULT_ADMIN_PASSWORD),
            role="Admin",
        )
        db.session.add(admin)
        db.session.commit()
        return

    updated = False
    if existing_admin.role != "Admin":
        existing_admin.role = "Admin"
        updated = True
    if _admin_password_needs_reset(existing_admin.password):
        existing_admin.password = generate_password_hash(DEFAULT_ADMIN_PASSWORD)
        updated = True

    if updated:
        db.session.commit()


def _ensure_item_nota_columns() -> None:
    conn = db.engine.connect()
    try:
        cols = _get_column_names("item_nota")

        if "numero_lancamento" not in cols:
            conn.execute(db.text("ALTER TABLE item_nota ADD COLUMN numero_lancamento VARCHAR"))
            conn.commit()
        if "tipo_documento" not in cols:
            conn.execute(db.text("ALTER TABLE item_nota ADD COLUMN tipo_documento VARCHAR(10) NOT NULL DEFAULT 'NFE'"))
            conn.commit()
        if "documento_externo_id" not in cols:
            conn.execute(db.text("ALTER TABLE item_nota ADD COLUMN documento_externo_id VARCHAR(120)"))
            conn.commit()
        if "codigo_verificacao" not in cols:
            conn.execute(db.text("ALTER TABLE item_nota ADD COLUMN codigo_verificacao VARCHAR(40)"))
            conn.commit()
        if "valor_total" not in cols:
            conn.execute(db.text("ALTER TABLE item_nota ADD COLUMN valor_total VARCHAR"))
            conn.commit()
        if "valor_imposto" not in cols:
            conn.execute(db.text("ALTER TABLE item_nota ADD COLUMN valor_imposto VARCHAR"))
            conn.commit()
        if "chave_acesso" not in cols:
            conn.execute(db.text("ALTER TABLE item_nota ADD COLUMN chave_acesso VARCHAR(44)"))
            conn.commit()
        if "cfop" not in cols:
            conn.execute(db.text("ALTER TABLE item_nota ADD COLUMN cfop VARCHAR(4)"))
            conn.commit()
        if "codigo_grv" not in cols:
            conn.execute(db.text("ALTER TABLE item_nota ADD COLUMN codigo_grv VARCHAR(80)"))
            conn.commit()
        if "unidade_comercial" not in cols:
            conn.execute(db.text("ALTER TABLE item_nota ADD COLUMN unidade_comercial VARCHAR(20)"))
            conn.commit()
        if "cnpj_emitente" not in cols:
            conn.execute(db.text("ALTER TABLE item_nota ADD COLUMN cnpj_emitente VARCHAR(14)"))
            conn.commit()
        if "cnpj_destinatario" not in cols:
            conn.execute(db.text("ALTER TABLE item_nota ADD COLUMN cnpj_destinatario VARCHAR(14)"))
            conn.commit()
        if "ncm" not in cols:
            conn.execute(db.text("ALTER TABLE item_nota ADD COLUMN ncm VARCHAR(8)"))
            conn.commit()
        if "cst_icms" not in cols:
            conn.execute(db.text("ALTER TABLE item_nota ADD COLUMN cst_icms VARCHAR(3)"))
            conn.commit()
        if "cst_pis" not in cols:
            conn.execute(db.text("ALTER TABLE item_nota ADD COLUMN cst_pis VARCHAR(2)"))
            conn.commit()
        if "cst_cofins" not in cols:
            conn.execute(db.text("ALTER TABLE item_nota ADD COLUMN cst_cofins VARCHAR(2)"))
            conn.commit()
        if "valor_produto" not in cols:
            conn.execute(db.text("ALTER TABLE item_nota ADD COLUMN valor_produto FLOAT"))
            conn.commit()
        if "valor_nf" not in cols:
            conn.execute(db.text("ALTER TABLE item_nota ADD COLUMN valor_nf FLOAT"))
            conn.commit()
        if "icms_base_calculo" not in cols:
            conn.execute(db.text("ALTER TABLE item_nota ADD COLUMN icms_base_calculo FLOAT"))
            conn.commit()
        if "icms_aliquota" not in cols:
            conn.execute(db.text("ALTER TABLE item_nota ADD COLUMN icms_aliquota FLOAT"))
            conn.commit()
        if "icms_valor" not in cols:
            conn.execute(db.text("ALTER TABLE item_nota ADD COLUMN icms_valor FLOAT"))
            conn.commit()
        if "pis_base_calculo" not in cols:
            conn.execute(db.text("ALTER TABLE item_nota ADD COLUMN pis_base_calculo FLOAT"))
            conn.commit()
        if "pis_aliquota" not in cols:
            conn.execute(db.text("ALTER TABLE item_nota ADD COLUMN pis_aliquota FLOAT"))
            conn.commit()
        if "pis_valor_credito" not in cols:
            conn.execute(db.text("ALTER TABLE item_nota ADD COLUMN pis_valor_credito FLOAT"))
            conn.commit()
        if "cofins_base_calculo" not in cols:
            conn.execute(db.text("ALTER TABLE item_nota ADD COLUMN cofins_base_calculo FLOAT"))
            conn.commit()
        if "cofins_aliquota" not in cols:
            conn.execute(db.text("ALTER TABLE item_nota ADD COLUMN cofins_aliquota FLOAT"))
            conn.commit()
        if "cofins_valor_credito" not in cols:
            conn.execute(db.text("ALTER TABLE item_nota ADD COLUMN cofins_valor_credito FLOAT"))
            conn.commit()
        if "pagamento_xml" not in cols:
            conn.execute(db.text("ALTER TABLE item_nota ADD COLUMN pagamento_xml BOOLEAN NOT NULL DEFAULT 0"))
            conn.commit()
        if "tipo_pagamento_xml" not in cols:
            conn.execute(db.text("ALTER TABLE item_nota ADD COLUMN tipo_pagamento_xml VARCHAR(100)"))
            conn.commit()
        if "valor_pagamento_xml" not in cols:
            conn.execute(db.text("ALTER TABLE item_nota ADD COLUMN valor_pagamento_xml FLOAT"))
            conn.commit()
        if "vencimento_pagamento_xml" not in cols:
            conn.execute(db.text("ALTER TABLE item_nota ADD COLUMN vencimento_pagamento_xml DATETIME"))
            conn.commit()
        if "pedido_compra" not in cols:
            conn.execute(db.text("ALTER TABLE item_nota ADD COLUMN pedido_compra VARCHAR(50)"))
            conn.commit()
        if "material_cliente" not in cols:
            conn.execute(db.text("ALTER TABLE item_nota ADD COLUMN material_cliente BOOLEAN NOT NULL DEFAULT 0"))
            conn.commit()
        if "remessa" not in cols:
            conn.execute(db.text("ALTER TABLE item_nota ADD COLUMN remessa BOOLEAN NOT NULL DEFAULT 0"))
            conn.commit()
        if "sem_conferencia_logistica" not in cols:
            conn.execute(db.text("ALTER TABLE item_nota ADD COLUMN sem_conferencia_logistica BOOLEAN NOT NULL DEFAULT 0"))
            conn.commit()
        if "auditor_status" not in cols:
            conn.execute(db.text("ALTER TABLE item_nota ADD COLUMN auditor_status VARCHAR(30) DEFAULT 'NaoAuditado'"))
            conn.commit()
        if "auditor_decisao" not in cols:
            conn.execute(db.text("ALTER TABLE item_nota ADD COLUMN auditor_decisao VARCHAR(20) DEFAULT 'PendenteDecisao'"))
            conn.commit()
        if "auditor_diagnostico" not in cols:
            conn.execute(db.text("ALTER TABLE item_nota ADD COLUMN auditor_diagnostico VARCHAR(4000)"))
            conn.commit()
        if "auditor_inconsistencias" not in cols:
            conn.execute(db.text("ALTER TABLE item_nota ADD COLUMN auditor_inconsistencias VARCHAR(1000)"))
            conn.commit()
        if "auditor_justificativa" not in cols:
            conn.execute(db.text("ALTER TABLE item_nota ADD COLUMN auditor_justificativa VARCHAR(500)"))
            conn.commit()
        if "auditor_observacao" not in cols:
            conn.execute(db.text("ALTER TABLE item_nota ADD COLUMN auditor_observacao VARCHAR(500)"))
            conn.commit()
        if "auditor_usuario" not in cols:
            conn.execute(db.text("ALTER TABLE item_nota ADD COLUMN auditor_usuario VARCHAR(100)"))
            conn.commit()
        if "auditor_data" not in cols:
            conn.execute(db.text("ALTER TABLE item_nota ADD COLUMN auditor_data DATETIME"))
            conn.commit()
        if "data_emissao" not in cols:
            conn.execute(db.text("ALTER TABLE item_nota ADD COLUMN data_emissao DATETIME"))
            conn.commit()

        cols_log_div = _get_column_names("log_divergencia")
        if "motivo_tipo" not in cols_log_div:
            conn.execute(db.text("ALTER TABLE log_divergencia ADD COLUMN motivo_tipo VARCHAR(80)"))
            conn.commit()
        if "destino_fisico" not in cols_log_div:
            conn.execute(db.text("ALTER TABLE log_divergencia ADD COLUMN destino_fisico VARCHAR(80)"))
            conn.commit()
        if "evidencia_path" not in cols_log_div:
            conn.execute(db.text("ALTER TABLE log_divergencia ADD COLUMN evidencia_path VARCHAR(300)"))
            conn.commit()
        if "tentativa_numero" not in cols_log_div:
            conn.execute(db.text("ALTER TABLE log_divergencia ADD COLUMN tentativa_numero INTEGER DEFAULT 1"))
            conn.commit()

        cols_lock = _get_column_names("conferencia_lock")
        if "heartbeat_at" not in cols_lock:
            conn.execute(db.text("ALTER TABLE conferencia_lock ADD COLUMN heartbeat_at DATETIME"))
            conn.execute(db.text("UPDATE conferencia_lock SET heartbeat_at = lock_until WHERE heartbeat_at IS NULL"))
            conn.commit()

        conn.execute(
            db.text(
                """
                CREATE TABLE IF NOT EXISTS checklist_recebimento (
                    id INTEGER PRIMARY KEY,
                    numero_nota VARCHAR(20) UNIQUE NOT NULL,
                    usuario VARCHAR(100) NOT NULL,
                    lacre_ok BOOLEAN NOT NULL DEFAULT 0,
                    volumes_ok BOOLEAN NOT NULL DEFAULT 0,
                    avaria_visual BOOLEAN NOT NULL DEFAULT 0,
                    etiqueta_ok BOOLEAN NOT NULL DEFAULT 0,
                    observacao VARCHAR(500),
                    data DATETIME NOT NULL
                )
                """
            )
        )
        conn.commit()
        conn.execute(
            db.text(
                """
                CREATE TABLE IF NOT EXISTS etiqueta_recebimento (
                    id INTEGER PRIMARY KEY,
                    numero_nota VARCHAR(20) UNIQUE NOT NULL,
                    usuario_impressao VARCHAR(100) NOT NULL,
                    data_impressao DATETIME NOT NULL,
                    quantidade_impressao INTEGER NOT NULL DEFAULT 1
                )
                """
            )
        )
        conn.commit()
        conn.execute(
            db.text(
                """
                CREATE TABLE IF NOT EXISTS log_tentativa_conferencia (
                    id INTEGER PRIMARY KEY,
                    numero_nota VARCHAR(20) NOT NULL,
                    item_id INTEGER NOT NULL,
                    tentativa_numero INTEGER NOT NULL,
                    qtd_esperada FLOAT NOT NULL,
                    qtd_digitada FLOAT,
                    qtd_convertida FLOAT,
                    unidade_informada VARCHAR(20),
                    fator_conversao FLOAT DEFAULT 1.0,
                    status_item VARCHAR(20) NOT NULL,
                    motivo VARCHAR(500),
                    usuario VARCHAR(100) NOT NULL,
                    data DATETIME NOT NULL
                )
                """
            )
        )
        conn.commit()
        conn.execute(
            db.text(
                """
                CREATE TABLE IF NOT EXISTS solicitacao_devolucao_recebimento (
                    id INTEGER PRIMARY KEY,
                    numero_nota VARCHAR(20) NOT NULL,
                    fornecedor VARCHAR(100),
                    chave_acesso VARCHAR(44),
                    usuario_solicitante VARCHAR(100) NOT NULL,
                    motivo VARCHAR(500) NOT NULL,
                    status VARCHAR(20) NOT NULL DEFAULT 'Pendente',
                    observacao_admin VARCHAR(500),
                    usuario_aprovador VARCHAR(100),
                    data_solicitacao DATETIME NOT NULL,
                    data_decisao DATETIME,
                    ativa BOOLEAN NOT NULL DEFAULT 1
                )
                """
            )
        )
        conn.commit()
        conn.execute(
            db.text(
                """
                CREATE TABLE IF NOT EXISTS log_manifestacao_destinatario (
                    id INTEGER PRIMARY KEY,
                    numero_nota VARCHAR(20) NOT NULL,
                    chave_acesso VARCHAR(44),
                    manifestacao VARCHAR(40) NOT NULL DEFAULT 'confirmada',
                    status VARCHAR(20) NOT NULL DEFAULT 'Sucesso',
                    detalhe VARCHAR(500),
                    usuario VARCHAR(100) NOT NULL,
                    data DATETIME NOT NULL
                )
                """
            )
        )
        conn.commit()
        conn.execute(
            db.text(
                """
                CREATE TABLE IF NOT EXISTS log_evento_fiscal_nota (
                    id INTEGER PRIMARY KEY,
                    numero_nota VARCHAR(20) NOT NULL,
                    evento VARCHAR(60) NOT NULL,
                    etapa VARCHAR(30),
                    status VARCHAR(20),
                    detalhe VARCHAR(1000),
                    payload_json TEXT,
                    usuario VARCHAR(100) NOT NULL,
                    data DATETIME NOT NULL
                )
                """
            )
        )
        conn.commit()
        _create_index_if_missing(
            conn,
            "log_evento_fiscal_nota",
            "ix_log_evento_fiscal_nota_numero_nota",
            "CREATE INDEX ix_log_evento_fiscal_nota_numero_nota ON log_evento_fiscal_nota (numero_nota)",
        )
        _create_index_if_missing(
            conn,
            "log_evento_fiscal_nota",
            "ix_log_evento_fiscal_nota_evento",
            "CREATE INDEX ix_log_evento_fiscal_nota_evento ON log_evento_fiscal_nota (evento)",
        )
        _create_index_if_missing(
            conn,
            "log_evento_fiscal_nota",
            "ix_log_evento_fiscal_nota_etapa",
            "CREATE INDEX ix_log_evento_fiscal_nota_etapa ON log_evento_fiscal_nota (etapa)",
        )
        _create_index_if_missing(
            conn,
            "log_evento_fiscal_nota",
            "ix_log_evento_fiscal_nota_status",
            "CREATE INDEX ix_log_evento_fiscal_nota_status ON log_evento_fiscal_nota (status)",
        )
        _create_index_if_missing(
            conn,
            "log_evento_fiscal_nota",
            "ix_log_evento_fiscal_nota_data",
            "CREATE INDEX ix_log_evento_fiscal_nota_data ON log_evento_fiscal_nota (data)",
        )

        conn.execute(
            db.text(
                """
                CREATE TABLE IF NOT EXISTS permissao_acesso (
                    id INTEGER PRIMARY KEY,
                    scope_type VARCHAR(10) NOT NULL,
                    scope_id VARCHAR(80) NOT NULL,
                    permission_key VARCHAR(80) NOT NULL,
                    allow BOOLEAN NOT NULL DEFAULT 1,
                    updated_by VARCHAR(100),
                    updated_at DATETIME NOT NULL
                )
                """
            )
        )
        conn.commit()
        _create_index_if_missing(
            conn,
            "permissao_acesso",
            "ux_permissao_scope_key",
            "CREATE UNIQUE INDEX ux_permissao_scope_key ON permissao_acesso (scope_type, scope_id, permission_key)",
        )

        conn.execute(
            db.text(
                """
                CREATE TABLE IF NOT EXISTS boleto_conta_receber (
                    id INTEGER PRIMARY KEY,
                    numero_nota VARCHAR(20) NOT NULL UNIQUE,
                    chave_acesso VARCHAR(44),
                    banco VARCHAR(80) NOT NULL DEFAULT 'Banco do Brasil',
                    valor FLOAT NOT NULL DEFAULT 0,
                    nosso_numero VARCHAR(40) NOT NULL UNIQUE,
                    linha_digitavel VARCHAR(120) NOT NULL,
                    codigo_barras VARCHAR(120) NOT NULL,
                    status VARCHAR(20) NOT NULL DEFAULT 'Gerado',
                    usuario_geracao VARCHAR(100) NOT NULL,
                    data_geracao DATETIME NOT NULL
                )
                """
            )
        )
        conn.commit()
        cols_boleto = _get_column_names("boleto_conta_receber")
        if "cpf_cnpj_pagador" not in cols_boleto:
            conn.execute(db.text("ALTER TABLE boleto_conta_receber ADD COLUMN cpf_cnpj_pagador VARCHAR(18)"))
            conn.commit()
        if "nome_pagador" not in cols_boleto:
            conn.execute(db.text("ALTER TABLE boleto_conta_receber ADD COLUMN nome_pagador VARCHAR(200)"))
            conn.commit()
        if "vencimento" not in cols_boleto:
            conn.execute(db.text("ALTER TABLE boleto_conta_receber ADD COLUMN vencimento DATE"))
            conn.commit()
        if "data_pagamento" not in cols_boleto:
            conn.execute(db.text("ALTER TABLE boleto_conta_receber ADD COLUMN data_pagamento DATE"))
            conn.commit()
        if "bofa_id" not in cols_boleto:
            conn.execute(db.text("ALTER TABLE boleto_conta_receber ADD COLUMN bofa_id VARCHAR(100)"))
            conn.commit()
        _create_index_if_missing(
            conn,
            "boleto_conta_receber",
            "ix_boleto_conta_receber_cpf_cnpj_pagador",
            "CREATE INDEX ix_boleto_conta_receber_cpf_cnpj_pagador ON boleto_conta_receber (cpf_cnpj_pagador)",
        )

        cols_classificacao = _get_column_names("classificacao_contabil_item")
        if cols_classificacao:
            if "aprovado_por" not in cols_classificacao:
                conn.execute(db.text("ALTER TABLE classificacao_contabil_item ADD COLUMN aprovado_por VARCHAR(100)"))
                conn.commit()
            if "aprovado_em" not in cols_classificacao:
                conn.execute(db.text("ALTER TABLE classificacao_contabil_item ADD COLUMN aprovado_em DATETIME"))
                conn.commit()
            if "motivo_pendencia" not in cols_classificacao:
                conn.execute(db.text("ALTER TABLE classificacao_contabil_item ADD COLUMN motivo_pendencia VARCHAR(80)"))
                conn.commit()
        if "tipo_regra" not in cols_classificacao:
            conn.execute(db.text("ALTER TABLE classificacao_contabil_item ADD COLUMN tipo_regra VARCHAR(30)"))
            conn.commit()

        cols_competencia = _get_column_names("classificacao_contabil_competencia")
        if cols_competencia:
            if "aprovado_por" not in cols_competencia:
                conn.execute(db.text("ALTER TABLE classificacao_contabil_competencia ADD COLUMN aprovado_por VARCHAR(100)"))
                conn.commit()
            if "aprovado_em" not in cols_competencia:
                conn.execute(db.text("ALTER TABLE classificacao_contabil_competencia ADD COLUMN aprovado_em DATETIME"))
                conn.commit()

        conn.execute(
            db.text(
                """
                CREATE TABLE IF NOT EXISTS email_entrada_chapa (
                    id INTEGER PRIMARY KEY,
                    numero_nota VARCHAR(60) NOT NULL,
                    chave_acesso VARCHAR(44),
                    numero_ar VARCHAR(80) NOT NULL,
                    parceiro_nome VARCHAR(220),
                    cfops VARCHAR(120),
                    destinatarios VARCHAR(800) NOT NULL,
                    assunto VARCHAR(300),
                    status VARCHAR(20) NOT NULL DEFAULT 'Pendente',
                    tentativas INTEGER NOT NULL DEFAULT 0,
                    erro_mensagem VARCHAR(800),
                    disparado_por VARCHAR(100),
                    origem VARCHAR(20) NOT NULL DEFAULT 'Sistema',
                    criado_em DATETIME NOT NULL,
                    enviado_em DATETIME,
                    CONSTRAINT ux_email_entrada_chapa_nota_ar UNIQUE (numero_nota, numero_ar)
                )
                """
            )
        )
        conn.commit()
        _create_index_if_missing(
            conn,
            "email_entrada_chapa",
            "ix_email_entrada_chapa_status",
            "CREATE INDEX ix_email_entrada_chapa_status ON email_entrada_chapa (status)",
        )
        _create_index_if_missing(
            conn,
            "email_entrada_chapa",
            "ix_email_entrada_chapa_criado_em",
            "CREATE INDEX ix_email_entrada_chapa_criado_em ON email_entrada_chapa (criado_em)",
        )

        cols_fat = _get_column_names("expedicao_faturamento")
        if "transporte_tipo" not in cols_fat:
            conn.execute(db.text("ALTER TABLE expedicao_faturamento ADD COLUMN transporte_tipo VARCHAR(20) DEFAULT 'Proprio'"))
            conn.commit()
        if "transportadora" not in cols_fat:
            conn.execute(db.text("ALTER TABLE expedicao_faturamento ADD COLUMN transportadora VARCHAR(120)"))
            conn.commit()
        if "placa" not in cols_fat:
            conn.execute(db.text("ALTER TABLE expedicao_faturamento ADD COLUMN placa VARCHAR(20)"))
            conn.commit()
        if "motorista" not in cols_fat:
            conn.execute(db.text("ALTER TABLE expedicao_faturamento ADD COLUMN motorista VARCHAR(120)"))
            conn.commit()
        if "peso_bruto" not in cols_fat:
            conn.execute(db.text("ALTER TABLE expedicao_faturamento ADD COLUMN peso_bruto FLOAT"))
            conn.commit()
        if "observacao" not in cols_fat:
            conn.execute(db.text("ALTER TABLE expedicao_faturamento ADD COLUMN observacao VARCHAR(300)"))
            conn.commit()
    finally:
        conn.close()


def _ensure_conserto_columns() -> None:
    conn = db.engine.connect()
    try:
        cols_estoque = _get_column_names("conserto_estoque")
        if cols_estoque and "tipo_controle" not in cols_estoque:
            conn.execute(
                db.text(
                    "ALTER TABLE conserto_estoque ADD COLUMN tipo_controle VARCHAR(50) NOT NULL DEFAULT 'Meu em poder de terceiros'"
                )
            )
            conn.commit()
            _create_index_if_missing(
                conn,
                "conserto_estoque",
                "ix_conserto_estoque_tipo_controle",
                "CREATE INDEX ix_conserto_estoque_tipo_controle ON conserto_estoque (tipo_controle)",
            )
        if cols_estoque and "tipo_operacao" not in cols_estoque:
            conn.execute(
                db.text(
                    "ALTER TABLE conserto_estoque ADD COLUMN tipo_operacao VARCHAR(30) NOT NULL DEFAULT 'Conserto'"
                )
            )
            conn.commit()
            _create_index_if_missing(
                conn,
                "conserto_estoque",
                "ix_conserto_estoque_tipo_operacao",
                "CREATE INDEX ix_conserto_estoque_tipo_operacao ON conserto_estoque (tipo_operacao)",
            )
        if cols_estoque and "cfop_remessa" not in cols_estoque:
            conn.execute(db.text("ALTER TABLE conserto_estoque ADD COLUMN cfop_remessa VARCHAR(4)"))
            conn.commit()
            _create_index_if_missing(
                conn,
                "conserto_estoque",
                "ix_conserto_estoque_cfop_remessa",
                "CREATE INDEX ix_conserto_estoque_cfop_remessa ON conserto_estoque (cfop_remessa)",
            )
        if cols_estoque and "numero_nf_remessa" not in cols_estoque:
            conn.execute(db.text("ALTER TABLE conserto_estoque ADD COLUMN numero_nf_remessa VARCHAR(20)"))
            conn.commit()
            _create_index_if_missing(
                conn,
                "conserto_estoque",
                "ix_conserto_estoque_numero_nf_remessa",
                "CREATE INDEX ix_conserto_estoque_numero_nf_remessa ON conserto_estoque (numero_nf_remessa)",
            )

        cols_baixa = _get_column_names("conserto_baixa")
        if cols_baixa and "cfop_retorno" not in cols_baixa:
            conn.execute(db.text("ALTER TABLE conserto_baixa ADD COLUMN cfop_retorno VARCHAR(4)"))
            conn.commit()
            _create_index_if_missing(
                conn,
                "conserto_baixa",
                "ix_conserto_baixa_cfop_retorno",
                "CREATE INDEX ix_conserto_baixa_cfop_retorno ON conserto_baixa (cfop_retorno)",
            )
        if cols_baixa and "numero_nf_retorno" not in cols_baixa:
            conn.execute(db.text("ALTER TABLE conserto_baixa ADD COLUMN numero_nf_retorno VARCHAR(20)"))
            conn.commit()
            _create_index_if_missing(
                conn,
                "conserto_baixa",
                "ix_conserto_baixa_numero_nf_retorno",
                "CREATE INDEX ix_conserto_baixa_numero_nf_retorno ON conserto_baixa (numero_nf_retorno)",
            )
    finally:
        conn.close()


def _ensure_expedicao_conferencia_simples_schema() -> None:
    conn = db.engine.connect()
    try:
        for table_name in (
            "expedicao_conferencia_simples",
            "expedicao_conferencia_simples_foto",
            "expedicao_conferencia_simples_estorno",
        ):
            table = db.metadata.tables.get(table_name)
            if table is not None:
                table.create(bind=conn, checkfirst=True)

        cols_conf = _get_column_names("expedicao_conferencia_simples")
        missing_conf_columns = [
            (
                "tipo_referencia",
                "ALTER TABLE expedicao_conferencia_simples ADD COLUMN tipo_referencia VARCHAR(20) NOT NULL DEFAULT 'Orcamento'",
            ),
            ("numero_os", "ALTER TABLE expedicao_conferencia_simples ADD COLUMN numero_os VARCHAR(80)"),
            ("ordem_compra", "ALTER TABLE expedicao_conferencia_simples ADD COLUMN ordem_compra VARCHAR(80)"),
            ("numero_nf", "ALTER TABLE expedicao_conferencia_simples ADD COLUMN numero_nf VARCHAR(160)"),
            ("nome_cliente", "ALTER TABLE expedicao_conferencia_simples ADD COLUMN nome_cliente VARCHAR(160)"),
            (
                "cliente_origem",
                "ALTER TABLE expedicao_conferencia_simples ADD COLUMN cliente_origem VARCHAR(20) NOT NULL DEFAULT 'Manual'",
            ),
            (
                "nf_origem",
                "ALTER TABLE expedicao_conferencia_simples ADD COLUMN nf_origem VARCHAR(20) NOT NULL DEFAULT 'Manual'",
            ),
            (
                "consyste_document_id",
                "ALTER TABLE expedicao_conferencia_simples ADD COLUMN consyste_document_id VARCHAR(120)",
            ),
            ("consyste_chave", "ALTER TABLE expedicao_conferencia_simples ADD COLUMN consyste_chave VARCHAR(50)"),
            ("transportadora", "ALTER TABLE expedicao_conferencia_simples ADD COLUMN transportadora VARCHAR(160)"),
            ("placa", "ALTER TABLE expedicao_conferencia_simples ADD COLUMN placa VARCHAR(20)"),
            ("motorista", "ALTER TABLE expedicao_conferencia_simples ADD COLUMN motorista VARCHAR(160)"),
            (
                "status",
                "ALTER TABLE expedicao_conferencia_simples ADD COLUMN status VARCHAR(30) NOT NULL DEFAULT 'Pendente de expedicao'",
            ),
            ("created_at", "ALTER TABLE expedicao_conferencia_simples ADD COLUMN created_at DATETIME"),
            ("updated_at", "ALTER TABLE expedicao_conferencia_simples ADD COLUMN updated_at DATETIME"),
            ("expedido_at", "ALTER TABLE expedicao_conferencia_simples ADD COLUMN expedido_at DATETIME"),
            ("expedido_by", "ALTER TABLE expedicao_conferencia_simples ADD COLUMN expedido_by VARCHAR(100)"),
            ("canhoto_file_name", "ALTER TABLE expedicao_conferencia_simples ADD COLUMN canhoto_file_name VARCHAR(260)"),
            ("canhoto_file_path", "ALTER TABLE expedicao_conferencia_simples ADD COLUMN canhoto_file_path VARCHAR(500)"),
            ("canhoto_uploaded_at", "ALTER TABLE expedicao_conferencia_simples ADD COLUMN canhoto_uploaded_at DATETIME"),
            ("canhoto_uploaded_by", "ALTER TABLE expedicao_conferencia_simples ADD COLUMN canhoto_uploaded_by VARCHAR(100)"),
            ("finalizado_at", "ALTER TABLE expedicao_conferencia_simples ADD COLUMN finalizado_at DATETIME"),
            ("finalizado_by", "ALTER TABLE expedicao_conferencia_simples ADD COLUMN finalizado_by VARCHAR(100)"),
        ]
        for column_name, ddl in missing_conf_columns:
            if column_name not in cols_conf:
                conn.execute(db.text(ddl))
                conn.commit()

        _create_index_if_missing(
            conn,
            "expedicao_conferencia_simples",
            "ix_expedicao_conferencia_simples_numero_os",
            "CREATE INDEX ix_expedicao_conferencia_simples_numero_os ON expedicao_conferencia_simples (numero_os)",
        )
        _create_index_if_missing(
            conn,
            "expedicao_conferencia_simples",
            "ix_expedicao_conferencia_simples_ordem_compra",
            "CREATE INDEX ix_expedicao_conferencia_simples_ordem_compra ON expedicao_conferencia_simples (ordem_compra)",
        )
        _create_index_if_missing(
            conn,
            "expedicao_conferencia_simples",
            "ix_expedicao_conferencia_simples_numero_nf",
            "CREATE INDEX ix_expedicao_conferencia_simples_numero_nf ON expedicao_conferencia_simples (numero_nf)",
        )
        _create_index_if_missing(
            conn,
            "expedicao_conferencia_simples",
            "ix_expedicao_conferencia_simples_consyste_document_id",
            "CREATE INDEX ix_expedicao_conferencia_simples_consyste_document_id ON expedicao_conferencia_simples (consyste_document_id)",
        )
        _create_index_if_missing(
            conn,
            "expedicao_conferencia_simples",
            "ix_expedicao_conferencia_simples_consyste_chave",
            "CREATE INDEX ix_expedicao_conferencia_simples_consyste_chave ON expedicao_conferencia_simples (consyste_chave)",
        )

        numero_nf_details = _get_column_details("expedicao_conferencia_simples", "numero_nf")
        numero_nf_type = numero_nf_details.get("type") if numero_nf_details else None
        numero_nf_length = getattr(numero_nf_type, "length", None)
        if db.engine.dialect.name == "mysql" and numero_nf_length is not None and numero_nf_length < 160:
            conn.execute(db.text("ALTER TABLE expedicao_conferencia_simples MODIFY COLUMN numero_nf VARCHAR(160)"))
            conn.commit()

        cols_foto = _get_column_names("expedicao_conferencia_simples_foto")
        missing_foto_columns = [
            (
                "conferencia_id",
                "ALTER TABLE expedicao_conferencia_simples_foto ADD COLUMN conferencia_id INTEGER",
            ),
            ("file_name", "ALTER TABLE expedicao_conferencia_simples_foto ADD COLUMN file_name VARCHAR(260)"),
            ("file_path", "ALTER TABLE expedicao_conferencia_simples_foto ADD COLUMN file_path VARCHAR(500)"),
            ("created_at", "ALTER TABLE expedicao_conferencia_simples_foto ADD COLUMN created_at DATETIME"),
        ]
        for column_name, ddl in missing_foto_columns:
            if column_name not in cols_foto:
                conn.execute(db.text(ddl))
                conn.commit()

        _create_index_if_missing(
            conn,
            "expedicao_conferencia_simples_foto",
            "ix_expedicao_conferencia_simples_foto_conferencia_id",
            "CREATE INDEX ix_expedicao_conferencia_simples_foto_conferencia_id ON expedicao_conferencia_simples_foto (conferencia_id)",
        )

        cols_estorno = _get_column_names("expedicao_conferencia_simples_estorno")
        missing_estorno_columns = [
            (
                "conferencia_id",
                "ALTER TABLE expedicao_conferencia_simples_estorno ADD COLUMN conferencia_id INTEGER",
            ),
            ("solicitante", "ALTER TABLE expedicao_conferencia_simples_estorno ADD COLUMN solicitante VARCHAR(100)"),
            ("motivo", "ALTER TABLE expedicao_conferencia_simples_estorno ADD COLUMN motivo VARCHAR(500)"),
            (
                "status",
                "ALTER TABLE expedicao_conferencia_simples_estorno ADD COLUMN status VARCHAR(30) NOT NULL DEFAULT 'Pendente'",
            ),
            ("admin_usuario", "ALTER TABLE expedicao_conferencia_simples_estorno ADD COLUMN admin_usuario VARCHAR(100)"),
            (
                "admin_observacao",
                "ALTER TABLE expedicao_conferencia_simples_estorno ADD COLUMN admin_observacao VARCHAR(500)",
            ),
            ("resolvido_at", "ALTER TABLE expedicao_conferencia_simples_estorno ADD COLUMN resolvido_at DATETIME"),
            ("created_at", "ALTER TABLE expedicao_conferencia_simples_estorno ADD COLUMN created_at DATETIME"),
        ]
        for column_name, ddl in missing_estorno_columns:
            if column_name not in cols_estorno:
                conn.execute(db.text(ddl))
                conn.commit()

        _create_index_if_missing(
            conn,
            "expedicao_conferencia_simples_estorno",
            "ix_expedicao_conferencia_simples_estorno_conferencia_id",
            "CREATE INDEX ix_expedicao_conferencia_simples_estorno_conferencia_id ON expedicao_conferencia_simples_estorno (conferencia_id)",
        )
    finally:
        conn.close()


def initialize_database(app: Flask) -> None:
    with app.app_context():
        try:
            _ensure_usuario_password_capacity()
        except Exception:
            pass
        db.create_all()
        
        # Criar tabelas WMS se não existirem
        try:
            _ensure_wms_tables()
        except Exception:
            pass

        try:
            _ensure_usuario_email_column()
        except Exception:
            pass

        _ensure_default_admin_user()

        # Criar 5 depósitos fixos se não existirem
        _ensure_depositos_wms()
        _ensure_agendamento_veiculos()

        try:
            _ensure_item_nota_columns()
        except Exception:
            # Mantem compatibilidade com bancos antigos sem impedir startup.
            pass

        try:
            _ensure_conserto_columns()
        except Exception:
            pass

        try:
            _ensure_expedicao_conferencia_simples_schema()
        except Exception:
            pass

        try:
            _ensure_facilities_extra_columns()
        except Exception:
            pass

        try:
            _ensure_facilities_seed()
        except Exception:
            pass

        if not app.config.get("TESTING"):
            try:
                from .services.classificacao_contabil_service import importar_padroes_internos, importar_plano_contas_dominio
                importar_plano_contas_dominio()
                importar_padroes_internos()
            except Exception:
                pass


def _ensure_facilities_extra_columns() -> None:
    """Adiciona colunas Fase 2+3 (estoque, retirada, evidencia, auditoria, cancelamento)."""
    from sqlalchemy import text

    additions = {
        "facilities_colaborador": [
            ("email", "VARCHAR(160)"),
        ],
        "facilities_epi_material": [
            ("numero_ca", "VARCHAR(20)"),
            ("qtd_estoque", "INTEGER NOT NULL DEFAULT 0"),
            ("qtd_minima", "INTEGER NOT NULL DEFAULT 0"),
        ],
        "facilities_epi_solicitacao": [
            ("motivo_recusa", "TEXT"),
            ("retirado_em", "DATETIME"),
            ("retirado_por", "VARCHAR(100)"),
            ("numero_ca_entregue", "VARCHAR(20)"),
            ("assinatura_path", "VARCHAR(500)"),
            ("solicitante_id", "INTEGER"),
            ("liberador_id", "INTEGER"),
            ("liberado_em", "DATETIME"),
            ("liberado_por_username", "VARCHAR(100)"),
            ("cancelado_em", "DATETIME"),
            ("cancelado_por", "VARCHAR(100)"),
            ("motivo_cancelamento", "TEXT"),
            ("proxima_troca_em", "DATE"),
            ("lembrete_retirada_enviado_em", "DATETIME"),
        ],
        "facilities_limpeza": [
            ("concluido_em", "DATETIME"),
            ("concluido_por", "VARCHAR(100)"),
            ("evidencia_foto_path", "VARCHAR(500)"),
            ("template_id", "INTEGER"),
            ("checklist_status_json", "TEXT"),
        ],
    }

    is_mysql = db.engine.dialect.name == "mysql"
    for tabela, cols in additions.items():
        if not _has_table(tabela):
            continue
        existentes = _get_column_names(tabela)
        for nome, tipo in cols:
            if nome in existentes:
                continue
            tipo_db = tipo
            if is_mysql:
                tipo_db = tipo_db.replace("DATETIME", "DATETIME NULL").replace(" DATE", " DATE NULL")
            with db.engine.connect() as conn:
                conn.execute(text(f"ALTER TABLE {tabela} ADD COLUMN {nome} {tipo_db}"))
                conn.commit()


DEFAULT_EPI_MATERIAIS = [
    # (codigo_interno, nome, tipo)
    ("EPI-001", "Capacete de Seguranca", "epi"),
    ("EPI-002", "Oculos de Protecao Incolor", "epi"),
    ("EPI-003", "Oculos de Protecao Escuro", "epi"),
    ("EPI-004", "Luva de Vaqueta", "epi"),
    ("EPI-005", "Luva Nitrilica", "epi"),
    ("EPI-006", "Luva Latex", "epi"),
    ("EPI-007", "Protetor Auricular de Insercao", "epi"),
    ("EPI-008", "Protetor Auricular Concha", "epi"),
    ("EPI-009", "Mascara PFF2", "epi"),
    ("EPI-010", "Mascara Descartavel", "epi"),
    ("EPI-011", "Botina de Seguranca com Biqueira", "epi"),
    ("EPI-012", "Bota de PVC Cano Longo", "epi"),
    ("EPI-013", "Cinto de Seguranca Paraquedista", "epi"),
    ("EPI-014", "Protetor Facial", "epi"),
    ("UNI-001", "Camisa Manga Curta", "uniforme"),
    ("UNI-002", "Camisa Manga Longa", "uniforme"),
    ("UNI-003", "Calca Operacional", "uniforme"),
    ("UNI-004", "Jaqueta de Frio", "uniforme"),
    ("UNI-005", "Colete Refletivo", "uniforme"),
    ("UNI-006", "Avental de Raspa", "uniforme"),
]


def _ensure_facilities_seed() -> None:
    """Popula catalogo EPI padrao e sincroniza colaboradores com tabela Usuario."""
    if not _has_table("facilities_epi_material") or not _has_table("facilities_colaborador"):
        return

    # 1) Catalogo EPI/Uniforme
    existentes = {m.codigo_interno for m in FacilitiesEpiMaterial.query.all()}
    novos = 0
    for codigo, nome, tipo in DEFAULT_EPI_MATERIAIS:
        if codigo in existentes:
            continue
        db.session.add(FacilitiesEpiMaterial(codigo_interno=codigo, nome=nome, tipo=tipo, ativo=True))
        novos += 1
    if novos:
        db.session.commit()

    # 2) Sincronizar colaboradores a partir de Usuario (solicitante por padrao;
    # admins viram gestor automaticamente). Nao sobrescreve nivel_acesso se ja existir.
    usuarios = Usuario.query.all()
    nomes_existentes = {c.nome.strip().lower(): c for c in FacilitiesColaborador.query.all()}
    criados = 0
    for u in usuarios:
        if not u.username:
            continue
        chave = u.username.strip().lower()
        role = (u.role or "").strip().lower()
        nivel = "gestor" if role == "admin" else "solicitante"
        if chave in nomes_existentes:
            colab = nomes_existentes[chave]
            # promove a gestor se virar admin e ainda nao for
            if nivel == "gestor" and colab.nivel_acesso != "gestor":
                colab.nivel_acesso = "gestor"
            continue
        db.session.add(FacilitiesColaborador(
            nome=u.username,
            cargo=u.role or "",
            setor="",
            telefone="",
            nivel_acesso=nivel,
            ativo=True,
        ))
        criados += 1
    if criados:
        db.session.commit()

    # 3) Seed ciclos de troca (NR-6) baseado em palavras-chave
    _seed_ciclos_troca_epi()


DEFAULT_CICLOS_TROCA = [
    # (palavra_chave, meses_validade, descricao)
    ("BOTINA", 6, "Botina de seguranca - troca semestral"),
    ("BOTA", 6, "Bota - troca semestral"),
    ("CAPACETE", 24, "Capacete - troca a cada 2 anos"),
    ("OCULOS", 12, "Oculos de protecao - troca anual"),
    ("PROTETOR AURICULAR", 3, "Protetor auricular de insercao - troca trimestral"),
    ("PROTETOR FACIAL", 12, "Protetor facial - troca anual"),
    ("LUVA", 1, "Luva - troca mensal"),
    ("MASCARA", 1, "Mascara descartavel - uso diario"),
    ("CINTO", 12, "Cinto de seguranca - troca anual"),
    ("AVENTAL", 12, "Avental de raspa - troca anual"),
    ("CAMISETA", 6, "Camiseta - troca semestral"),
    ("CAMISA", 6, "Camisa - troca semestral"),
    ("CALCA", 6, "Calca - troca semestral"),
    ("JAQUETA", 24, "Jaqueta - troca bienal"),
    ("COLETE", 12, "Colete refletivo - troca anual"),
]


def _seed_ciclos_troca_epi() -> None:
    """Popula ciclos padrao (idempotente)."""
    from .models import FacilitiesEpiCicloTroca
    if not _has_table("facilities_epi_ciclo_troca"):
        return
    existentes = {(c.palavra_chave or "").upper() for c in FacilitiesEpiCicloTroca.query.all()}
    novos = 0
    for palavra, meses, desc in DEFAULT_CICLOS_TROCA:
        if palavra.upper() in existentes:
            continue
        db.session.add(FacilitiesEpiCicloTroca(
            palavra_chave=palavra,
            meses_validade=meses,
            descricao=desc,
            ativo=True,
        ))
        novos += 1
    if novos:
        db.session.commit()


def _ensure_usuario_email_column() -> None:
    """Add email column to usuario table if missing, and remove non-admin users.
    Also ensures password column is nullable (SQLite requires table recreation)."""
    from sqlalchemy import text, inspect as sa_inspect
    insp = sa_inspect(db.engine)
    cols = {c["name"] for c in insp.get_columns("usuario")}
    if "email" not in cols:
        with db.engine.connect() as conn:
            conn.execute(text("ALTER TABLE usuario ADD COLUMN email VARCHAR(160)"))
            conn.commit()
        # Remove all non-admin users so they can be re-registered with email
        with db.engine.connect() as conn:
            conn.execute(text("DELETE FROM usuario WHERE UPPER(username) != 'ADMIN'"))
            conn.commit()

    # Ensure password is nullable (SQLite needs table recreation, MySQL uses ALTER)
    col_details = _get_column_details("usuario", "password")
    if col_details and col_details.get("nullable") is False:
        if db.engine.dialect.name == "mysql":
            with db.engine.connect() as conn:
                conn.execute(text("ALTER TABLE usuario MODIFY COLUMN password VARCHAR(255) NULL"))
                conn.commit()
        else:
            with db.engine.connect() as conn:
                conn.execute(text(
                    "CREATE TABLE usuario_tmp ("
                    "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                    "username VARCHAR(80) NOT NULL UNIQUE, "
                    "password VARCHAR(120), "
                    "role VARCHAR(20), "
                    "email VARCHAR(160) UNIQUE)"
                ))
                conn.execute(text(
                    "INSERT INTO usuario_tmp (id, username, password, role, email) "
                    "SELECT id, username, password, role, email FROM usuario"
                ))
                conn.execute(text("DROP TABLE usuario"))
                conn.execute(text("ALTER TABLE usuario_tmp RENAME TO usuario"))
                conn.commit()


def _ensure_agendamento_veiculos() -> None:
    conn = db.engine.connect()
    try:
        for table_name in (
            "agendamento_veiculo",
            "agendamento_motorista",
            "agendamento_fornecedor",
            "agendamento_cliente",
            "agendamento_solicitacao",
            "agendamento_solicitacao_item",
            "agendamento_solicitacao_historico",
        ):
            table = db.metadata.tables.get(table_name)
            if table is not None:
                table.create(bind=conn, checkfirst=True)

        cols_solicitacao = _get_column_names("agendamento_solicitacao")
        missing_columns = [
            ("motorista_id", "ALTER TABLE agendamento_solicitacao ADD COLUMN motorista_id INTEGER"),
            ("motorista_nome", "ALTER TABLE agendamento_solicitacao ADD COLUMN motorista_nome VARCHAR(160)"),
            ("origem_latitude", "ALTER TABLE agendamento_solicitacao ADD COLUMN origem_latitude FLOAT"),
            ("origem_longitude", "ALTER TABLE agendamento_solicitacao ADD COLUMN origem_longitude FLOAT"),
            ("destino_latitude", "ALTER TABLE agendamento_solicitacao ADD COLUMN destino_latitude FLOAT"),
            ("destino_longitude", "ALTER TABLE agendamento_solicitacao ADD COLUMN destino_longitude FLOAT"),
            ("km_estimado", "ALTER TABLE agendamento_solicitacao ADD COLUMN km_estimado FLOAT"),
            ("km_estimado_retorno", "ALTER TABLE agendamento_solicitacao ADD COLUMN km_estimado_retorno FLOAT"),
        ]
        for column_name, ddl in missing_columns:
            if column_name not in cols_solicitacao:
                conn.execute(db.text(ddl))
                conn.commit()

        _create_index_if_missing(
            conn,
            "agendamento_solicitacao",
            "ix_agendamento_solicitacao_motorista_id",
            "CREATE INDEX ix_agendamento_solicitacao_motorista_id ON agendamento_solicitacao (motorista_id)",
        )
        _create_index_if_missing(
            conn,
            "agendamento_solicitacao",
            "ix_agendamento_solicitacao_motorista_nome",
            "CREATE INDEX ix_agendamento_solicitacao_motorista_nome ON agendamento_solicitacao (motorista_nome)",
        )

        cols_motorista = _get_column_names("agendamento_motorista")
        if "ativo" not in cols_motorista:
            conn.execute(db.text("ALTER TABLE agendamento_motorista ADD COLUMN ativo BOOLEAN NOT NULL DEFAULT 1"))
            conn.commit()
        if "usuario_username" not in cols_motorista:
            conn.execute(db.text("ALTER TABLE agendamento_motorista ADD COLUMN usuario_username VARCHAR(80)"))
            conn.commit()

        # Campos novos na solicitacao
        extra_sol_cols = [
            ("data_desejada", "ALTER TABLE agendamento_solicitacao ADD COLUMN data_desejada DATETIME"),
            ("cancelamento_pendente", "ALTER TABLE agendamento_solicitacao ADD COLUMN cancelamento_pendente BOOLEAN NOT NULL DEFAULT 0"),
            ("cancelamento_solicitado_por", "ALTER TABLE agendamento_solicitacao ADD COLUMN cancelamento_solicitado_por VARCHAR(100)"),
            ("cancelamento_motivo_pendente", "ALTER TABLE agendamento_solicitacao ADD COLUMN cancelamento_motivo_pendente VARCHAR(500)"),
            ("tempo_estimado_min", "ALTER TABLE agendamento_solicitacao ADD COLUMN tempo_estimado_min INTEGER"),
        ]
        for col_name, ddl in extra_sol_cols:
            if col_name not in cols_solicitacao:
                conn.execute(db.text(ddl))
                conn.commit()
    finally:
        conn.close()

    veiculos = [
        {
            "codigo": "IVECO",
            "nome_exibicao": "IVECO",
            "placa": "",
            "cor_kanban": "blue",
            "janela_conflito_min": 30,
            "duracao_padrao_min": 180,
            "ordem_exibicao": 10,
        },
        {
            "codigo": "SAVEIRO",
            "nome_exibicao": "SAVEIRO",
            "placa": "",
            "cor_kanban": "gold",
            "janela_conflito_min": 30,
            "duracao_padrao_min": 120,
            "ordem_exibicao": 20,
        },
    ]

    for payload in veiculos:
        registro = AgendamentoVeiculo.query.filter_by(codigo=payload["codigo"]).first()
        if not registro:
            registro = AgendamentoVeiculo(codigo=payload["codigo"])
            db.session.add(registro)
        registro.nome_exibicao = payload["nome_exibicao"]
        registro.placa = payload["placa"]
        registro.cor_kanban = payload["cor_kanban"]
        registro.janela_conflito_min = payload["janela_conflito_min"]
        registro.duracao_padrao_min = payload["duracao_padrao_min"]
        registro.ordem_exibicao = payload["ordem_exibicao"]
        registro.ativo = True

    db.session.commit()

    # Auto-sync: criar AgendamentoMotorista para usuarios com role Motorista que não tem registro
    from .models import Usuario
    motorista_users = Usuario.query.filter_by(role="Motorista").all()
    for u in motorista_users:
        existing = AgendamentoMotorista.query.filter_by(usuario_username=u.username).first()
        if not existing:
            db.session.add(AgendamentoMotorista(
                nome=u.username,
                usuario_username=u.username,
                ativo=True,
            ))
    db.session.commit()


def _ensure_depositos_wms() -> None:
    """Garante os depósitos operacionais padrão (AL, CH, LOG)."""
    depositos = [
        ('AL', 'AL - Almoxarifado', 'Depósito padrão de recebimento e armazenagem'),
        ('CH', 'CH - Chaparia', 'Depósito de itens de chaparia'),
        ('LOG', 'LOG - Expedição', 'Depósito logístico de expedição'),
    ]
    
    codigos_validos = {codigo for codigo, _, _ in depositos}

    for codigo, nome, descricao in depositos:
        existe = DepositoWMS.query.filter_by(codigo=codigo).first()
        if not existe:
            novo_deposito = DepositoWMS(
                codigo=codigo,
                nome=nome,
                descricao=descricao,
                ativo=True,
            )
            db.session.add(novo_deposito)
        else:
            existe.nome = nome
            existe.descricao = descricao
            existe.ativo = True

    # Mantem historico, mas remove da operacao os depositos fora do padrao AL/CH/LOG.
    for dep in DepositoWMS.query.all():
        if dep.codigo not in codigos_validos:
            dep.ativo = False
    
    db.session.commit()


def _ensure_wms_tables() -> None:
    """Garante que as tabelas WMS existam no banco de dados"""
    conn = db.engine.connect()
    try:
        # Tabela deposito_wms
        conn.execute(
            db.text(
                """
                CREATE TABLE IF NOT EXISTS deposito_wms (
                    id INTEGER PRIMARY KEY,
                    codigo VARCHAR(30) UNIQUE NOT NULL,
                    nome VARCHAR(100) NOT NULL,
                    descricao VARCHAR(300),
                    ativo BOOLEAN NOT NULL DEFAULT 1,
                    data_criacao DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
        )
        conn.commit()

        _create_index_if_missing(
            conn,
            "deposito_wms",
            "ix_deposito_wms_codigo",
            "CREATE INDEX ix_deposito_wms_codigo ON deposito_wms (codigo)",
        )

        # Tabela localizacao_armazem
        conn.execute(
            db.text(
                """
                CREATE TABLE IF NOT EXISTS localizacao_armazem (
                    id INTEGER PRIMARY KEY,
                    codigo VARCHAR(50) UNIQUE NOT NULL,
                    deposito_id INTEGER,
                    corredor VARCHAR(10) NOT NULL,
                    prateleira VARCHAR(10) NOT NULL,
                    posicao VARCHAR(10) NOT NULL,
                    capacidade_maxima FLOAT NOT NULL DEFAULT 100.0,
                    capacidade_atual FLOAT NOT NULL DEFAULT 0.0,
                    ativo BOOLEAN NOT NULL DEFAULT 1,
                    data_criacao DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (deposito_id) REFERENCES deposito_wms(id)
                )
                """
            )
        )
        conn.commit()

        _create_index_if_missing(
            conn,
            "localizacao_armazem",
            "ix_localizacao_armazem_codigo",
            "CREATE INDEX ix_localizacao_armazem_codigo ON localizacao_armazem (codigo)",
        )

        cols_loc = _get_column_names("localizacao_armazem")
        if "rua" not in cols_loc:
            conn.execute(db.text("ALTER TABLE localizacao_armazem ADD COLUMN rua VARCHAR(30)"))
            conn.commit()
        if "predio" not in cols_loc:
            conn.execute(db.text("ALTER TABLE localizacao_armazem ADD COLUMN predio VARCHAR(30)"))
            conn.commit()
        if "nivel" not in cols_loc:
            conn.execute(db.text("ALTER TABLE localizacao_armazem ADD COLUMN nivel VARCHAR(30)"))
            conn.commit()
        if "apartamento" not in cols_loc:
            conn.execute(db.text("ALTER TABLE localizacao_armazem ADD COLUMN apartamento VARCHAR(30)"))
            conn.commit()
        if "deposito_id" not in cols_loc:
            conn.execute(db.text("ALTER TABLE localizacao_armazem ADD COLUMN deposito_id INTEGER"))
            conn.commit()
            _create_index_if_missing(
                conn,
                "localizacao_armazem",
                "ix_localizacao_armazem_deposito_id",
                "CREATE INDEX ix_localizacao_armazem_deposito_id ON localizacao_armazem (deposito_id)",
            )
        
        # Tabela item_wms
        conn.execute(
            db.text(
                """
                CREATE TABLE IF NOT EXISTS item_wms (
                    id INTEGER PRIMARY KEY,
                    numero_nota VARCHAR(20) NOT NULL,
                    chave_acesso VARCHAR(44),
                    fornecedor VARCHAR(100),
                    codigo_item VARCHAR(50) NOT NULL,
                    descricao VARCHAR(200),
                    qtd_recebida FLOAT NOT NULL,
                    qtd_atual FLOAT NOT NULL,
                    unidade VARCHAR(20),
                    lote VARCHAR(50),
                    data_validade DATE,
                    localizacao_id INTEGER,
                    usuario_armazenamento VARCHAR(100),
                    data_armazenamento DATETIME,
                    status VARCHAR(20) NOT NULL DEFAULT 'Armazenado',
                    origem_estoque_inicial BOOLEAN NOT NULL DEFAULT 0,
                    ativo BOOLEAN NOT NULL DEFAULT 1,
                    data_criacao DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (localizacao_id) REFERENCES localizacao_armazem(id)
                )
                """
            )
        )
        conn.commit()

        _create_index_if_missing(
            conn,
            "item_wms",
            "ix_item_wms_numero_nota",
            "CREATE INDEX ix_item_wms_numero_nota ON item_wms (numero_nota)",
        )
        _create_index_if_missing(
            conn,
            "item_wms",
            "ix_item_wms_codigo_item",
            "CREATE INDEX ix_item_wms_codigo_item ON item_wms (codigo_item)",
        )
        _create_index_if_missing(
            conn,
            "item_wms",
            "ix_item_wms_localizacao_id",
            "CREATE INDEX ix_item_wms_localizacao_id ON item_wms (localizacao_id)",
        )
        _create_index_if_missing(
            conn,
            "item_wms",
            "ix_item_wms_status",
            "CREATE INDEX ix_item_wms_status ON item_wms (status)",
        )

        cols_item_wms = _get_column_names("item_wms")
        if "codigo_grv" not in cols_item_wms:
            conn.execute(db.text("ALTER TABLE item_wms ADD COLUMN codigo_grv VARCHAR(80)"))
            conn.commit()
        if "ordem_servico" not in cols_item_wms:
            conn.execute(db.text("ALTER TABLE item_wms ADD COLUMN ordem_servico VARCHAR(80)"))
            conn.commit()
        if "ordem_compra" not in cols_item_wms:
            conn.execute(db.text("ALTER TABLE item_wms ADD COLUMN ordem_compra VARCHAR(80)"))
            conn.commit()
        if "deposito_id" not in cols_item_wms:
            conn.execute(db.text("ALTER TABLE item_wms ADD COLUMN deposito_id INTEGER"))
            conn.commit()
            _create_index_if_missing(
                conn,
                "item_wms",
                "ix_item_wms_deposito_id",
                "CREATE INDEX ix_item_wms_deposito_id ON item_wms (deposito_id)",
            )
        if "origem_estoque_inicial" not in cols_item_wms:
            conn.execute(db.text("ALTER TABLE item_wms ADD COLUMN origem_estoque_inicial BOOLEAN NOT NULL DEFAULT 0"))
            conn.commit()
            _create_index_if_missing(
                conn,
                "item_wms",
                "ix_item_wms_origem_estoque_inicial",
                "CREATE INDEX ix_item_wms_origem_estoque_inicial ON item_wms (origem_estoque_inicial)",
            )
        
        # Tabela movimentacao_wms
        conn.execute(
            db.text(
                """
                CREATE TABLE IF NOT EXISTS movimentacao_wms (
                    id INTEGER PRIMARY KEY,
                    item_wms_id INTEGER NOT NULL,
                    numero_nota VARCHAR(20) NOT NULL,
                    tipo_movimentacao VARCHAR(30) NOT NULL,
                    localizacao_origem_id INTEGER,
                    localizacao_destino_id INTEGER,
                    qtd_movimentada FLOAT NOT NULL,
                    motivo VARCHAR(300),
                    usuario VARCHAR(100) NOT NULL,
                    data_movimentacao DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (item_wms_id) REFERENCES item_wms(id),
                    FOREIGN KEY (localizacao_origem_id) REFERENCES localizacao_armazem(id),
                    FOREIGN KEY (localizacao_destino_id) REFERENCES localizacao_armazem(id)
                )
                """
            )
        )
        conn.commit()

        _create_index_if_missing(
            conn,
            "movimentacao_wms",
            "ix_movimentacao_wms_item_wms_id",
            "CREATE INDEX ix_movimentacao_wms_item_wms_id ON movimentacao_wms (item_wms_id)",
        )
        _create_index_if_missing(
            conn,
            "movimentacao_wms",
            "ix_movimentacao_wms_numero_nota",
            "CREATE INDEX ix_movimentacao_wms_numero_nota ON movimentacao_wms (numero_nota)",
        )
        
        # Tabela estoque_wms
        conn.execute(
            db.text(
                """
                CREATE TABLE IF NOT EXISTS estoque_wms (
                    id INTEGER PRIMARY KEY,
                    codigo_item VARCHAR(50) NOT NULL,
                    localizacao_id INTEGER NOT NULL,
                    qtd_total FLOAT NOT NULL DEFAULT 0.0,
                    qtd_separada FLOAT NOT NULL DEFAULT 0.0,
                    data_atualizacao DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE (codigo_item, localizacao_id),
                    FOREIGN KEY (localizacao_id) REFERENCES localizacao_armazem(id)
                )
                """
            )
        )
        conn.commit()

        _create_index_if_missing(
            conn,
            "estoque_wms",
            "ix_estoque_wms_codigo_item",
            "CREATE INDEX ix_estoque_wms_codigo_item ON estoque_wms (codigo_item)",
        )
        _create_index_if_missing(
            conn,
            "estoque_wms",
            "ix_estoque_wms_localizacao_id",
            "CREATE INDEX ix_estoque_wms_localizacao_id ON estoque_wms (localizacao_id)",
        )

        cols_estoque_wms = _get_column_names("estoque_wms")
        if "qtd_bloqueada" not in cols_estoque_wms:
            conn.execute(db.text("ALTER TABLE estoque_wms ADD COLUMN qtd_bloqueada FLOAT NOT NULL DEFAULT 0.0"))
            conn.commit()

        conn.execute(
            db.text(
                """
                CREATE TABLE IF NOT EXISTS wms_integracao_evento (
                    id INTEGER PRIMARY KEY,
                    idempotency_key VARCHAR(120) UNIQUE NOT NULL,
                    tipo_evento VARCHAR(40) NOT NULL,
                    referencia VARCHAR(80) NOT NULL,
                    origem VARCHAR(30) NOT NULL DEFAULT 'ERP',
                    payload_json TEXT,
                    status VARCHAR(20) NOT NULL DEFAULT 'Pendente',
                    tentativas INTEGER NOT NULL DEFAULT 0,
                    proxima_tentativa_em DATETIME,
                    ultima_erro VARCHAR(500),
                    processado_em DATETIME,
                    criado_em DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
        )
        _create_index_if_missing(
            conn,
            "wms_integracao_evento",
            "ix_wms_integracao_status",
            "CREATE INDEX ix_wms_integracao_status ON wms_integracao_evento (status)",
        )
        _create_index_if_missing(
            conn,
            "wms_integracao_evento",
            "ix_wms_integracao_referencia",
            "CREATE INDEX ix_wms_integracao_referencia ON wms_integracao_evento (referencia)",
        )

        conn.execute(
            db.text(
                """
                CREATE TABLE IF NOT EXISTS wms_sku_mestre (
                    id INTEGER PRIMARY KEY,
                    codigo_item VARCHAR(50) UNIQUE NOT NULL,
                    codigo_erp VARCHAR(50),
                    unidade VARCHAR(20),
                    fator_conversao FLOAT NOT NULL DEFAULT 1.0,
                    curva_abc VARCHAR(1) DEFAULT 'C',
                    politica_validade VARCHAR(10) DEFAULT 'FIFO',
                    estoque_minimo FLOAT DEFAULT 0.0,
                    estoque_maximo FLOAT DEFAULT 0.0,
                    endereco_preferencial VARCHAR(80),
                    ativo BOOLEAN NOT NULL DEFAULT 1,
                    atualizado_em DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
        )
        _create_index_if_missing(
            conn,
            "wms_sku_mestre",
            "ix_wms_sku_mestre_codigo_erp",
            "CREATE INDEX ix_wms_sku_mestre_codigo_erp ON wms_sku_mestre (codigo_erp)",
        )

        conn.execute(
            db.text(
                """
                CREATE TABLE IF NOT EXISTS wms_parametro_operacional (
                    id INTEGER PRIMARY KEY,
                    chave VARCHAR(80) UNIQUE NOT NULL,
                    valor VARCHAR(200) NOT NULL,
                    descricao VARCHAR(300),
                    atualizado_por VARCHAR(100),
                    atualizado_em DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
        )
        conn.commit()

        conn.execute(
            db.text(
                """
                CREATE TABLE IF NOT EXISTS wms_reconciliacao_divergencia (
                    id INTEGER PRIMARY KEY,
                    numero_nota VARCHAR(20) NOT NULL,
                    codigo_item VARCHAR(50) NOT NULL,
                    qtd_erp FLOAT NOT NULL DEFAULT 0.0,
                    qtd_wms FLOAT NOT NULL DEFAULT 0.0,
                    diferenca FLOAT NOT NULL DEFAULT 0.0,
                    status VARCHAR(20) NOT NULL DEFAULT 'Aberta',
                    origem VARCHAR(30) NOT NULL DEFAULT 'Recon',
                    observacao VARCHAR(400),
                    criado_em DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    resolvido_em DATETIME
                )
                """
            )
        )
        _create_index_if_missing(
            conn,
            "wms_reconciliacao_divergencia",
            "ix_wms_reconciliacao_nota",
            "CREATE INDEX ix_wms_reconciliacao_nota ON wms_reconciliacao_divergencia (numero_nota)",
        )
        _create_index_if_missing(
            conn,
            "wms_reconciliacao_divergencia",
            "ix_wms_reconciliacao_status",
            "CREATE INDEX ix_wms_reconciliacao_status ON wms_reconciliacao_divergencia (status)",
        )

        conn.execute(
            db.text(
                """
                CREATE TABLE IF NOT EXISTS wms_alerta_operacional (
                    id INTEGER PRIMARY KEY,
                    tipo VARCHAR(40) NOT NULL,
                    severidade VARCHAR(10) NOT NULL DEFAULT 'MEDIA',
                    referencia VARCHAR(100),
                    descricao VARCHAR(400) NOT NULL,
                    status VARCHAR(20) NOT NULL DEFAULT 'Aberto',
                    criado_em DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    resolvido_em DATETIME
                )
                """
            )
        )
        _create_index_if_missing(
            conn,
            "wms_alerta_operacional",
            "ix_wms_alerta_status",
            "CREATE INDEX ix_wms_alerta_status ON wms_alerta_operacional (status)",
        )

    finally:
        conn.close()
