from flask import Flask
from sqlalchemy import inspect
from sqlalchemy.exc import OperationalError
from werkzeug.security import generate_password_hash

from .extensions import db
from .models import (
    AgendamentoMotorista,
    AgendamentoVeiculo,
    FacilitiesColaborador,
    FacilitiesEpiMaterial,
    Usuario,
)


DEFAULT_ADMIN_USERNAME = "ADMIN"
DEFAULT_ADMIN_PASSWORD = "admin1234"


def _is_mysql_table_exists_error(exc: OperationalError) -> bool:
    """Detecta erro idempotente de DDL em MySQL (table already exists)."""
    original = getattr(exc, "orig", None)
    args = getattr(original, "args", ()) if original is not None else ()
    if args and str(args[0]) == "1050":
        return True
    mensagem = str(exc).lower()
    return "already exists" in mensagem and "create table" in mensagem


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
        if "cfop_descricao_grv" not in cols:
            conn.execute(db.text("ALTER TABLE item_nota ADD COLUMN cfop_descricao_grv VARCHAR(180)"))
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
        if "tributos_origem" not in cols:
            conn.execute(db.text("ALTER TABLE item_nota ADD COLUMN tributos_origem VARCHAR(20)"))
            conn.commit()
        if "tributos_grv_atualizado_em" not in cols:
            conn.execute(db.text("ALTER TABLE item_nota ADD COLUMN tributos_grv_atualizado_em DATETIME"))
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
        if "qtd_chapas_und" not in cols:
            conn.execute(db.text("ALTER TABLE item_nota ADD COLUMN qtd_chapas_und FLOAT"))
            conn.commit()
        if "anexo_path" not in cols:
            conn.execute(db.text("ALTER TABLE item_nota ADD COLUMN anexo_path VARCHAR(300)"))
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


def _ensure_qualidade_certificado_columns() -> None:
    conn = db.engine.connect()
    try:
        cols = _get_column_names("qualidade_certificado")
        if not cols:
            return

        if "grid_os" not in cols:
            conn.execute(db.text("ALTER TABLE qualidade_certificado ADD COLUMN grid_os VARCHAR(120)"))
            conn.commit()
        if "sapatas_os" not in cols:
            conn.execute(db.text("ALTER TABLE qualidade_certificado ADD COLUMN sapatas_os VARCHAR(120)"))
            conn.commit()
        if "grid_numero_certificado" not in cols:
            conn.execute(db.text("ALTER TABLE qualidade_certificado ADD COLUMN grid_numero_certificado VARCHAR(120)"))
            conn.commit()
        if "sapatas_numero_certificado" not in cols:
            conn.execute(db.text("ALTER TABLE qualidade_certificado ADD COLUMN sapatas_numero_certificado VARCHAR(120)"))
            conn.commit()
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
                "origem",
                "ALTER TABLE expedicao_conferencia_simples ADD COLUMN origem VARCHAR(20) NOT NULL DEFAULT 'Manual'",
            ),
            (
                "consyste_document_id",
                "ALTER TABLE expedicao_conferencia_simples ADD COLUMN consyste_document_id VARCHAR(120)",
            ),
            ("consyste_chave", "ALTER TABLE expedicao_conferencia_simples ADD COLUMN consyste_chave VARCHAR(50)"),
            ("transportadora", "ALTER TABLE expedicao_conferencia_simples ADD COLUMN transportadora VARCHAR(160)"),
            ("placa", "ALTER TABLE expedicao_conferencia_simples ADD COLUMN placa VARCHAR(20)"),
            ("motorista", "ALTER TABLE expedicao_conferencia_simples ADD COLUMN motorista VARCHAR(160)"),
            ("sem_conferencia", "ALTER TABLE expedicao_conferencia_simples ADD COLUMN sem_conferencia BOOLEAN NOT NULL DEFAULT 0"),
            ("sem_conferencia_motivo", "ALTER TABLE expedicao_conferencia_simples ADD COLUMN sem_conferencia_motivo VARCHAR(60)"),
            ("retirado_por", "ALTER TABLE expedicao_conferencia_simples ADD COLUMN retirado_por VARCHAR(160)"),
            ("retirada_justificativa", "ALTER TABLE expedicao_conferencia_simples ADD COLUMN retirada_justificativa VARCHAR(500)"),
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
            ("foto_cliente_file_name", "ALTER TABLE expedicao_conferencia_simples ADD COLUMN foto_cliente_file_name VARCHAR(260)"),
            ("foto_cliente_file_path", "ALTER TABLE expedicao_conferencia_simples ADD COLUMN foto_cliente_file_path VARCHAR(500)"),
            ("foto_cliente_uploaded_at", "ALTER TABLE expedicao_conferencia_simples ADD COLUMN foto_cliente_uploaded_at DATETIME"),
            ("foto_cliente_uploaded_by", "ALTER TABLE expedicao_conferencia_simples ADD COLUMN foto_cliente_uploaded_by VARCHAR(100)"),
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


def _ensure_expedicao_ordem_fat_columns() -> None:
    """Garante colunas novas na tabela de ordens de faturamento."""
    if not _has_table("expedicao_ordem_fat"):
        return
    cols = _get_column_names("expedicao_ordem_fat")
    conn = db.engine.connect()
    try:
        if "peso_bruto" not in cols:
            conn.execute(db.text("ALTER TABLE expedicao_ordem_fat ADD COLUMN peso_bruto VARCHAR(40)"))
        if "dt_previsao_entrega" not in cols:
            conn.execute(db.text("ALTER TABLE expedicao_ordem_fat ADD COLUMN dt_previsao_entrega DATETIME"))
        if "conferido_pos_faturamento" not in cols:
            conn.execute(db.text(
                "ALTER TABLE expedicao_ordem_fat ADD COLUMN conferido_pos_faturamento BOOLEAN NOT NULL DEFAULT 0"
            ))
        if "operacao_tipo" not in cols:
            conn.execute(db.text(
                "ALTER TABLE expedicao_ordem_fat ADD COLUMN operacao_tipo VARCHAR(20) NOT NULL DEFAULT 'nacional'"
            ))
        if "codigo_interno" not in cols:
            conn.execute(db.text("ALTER TABLE expedicao_ordem_fat ADD COLUMN codigo_interno VARCHAR(20)"))
        if "excluido" not in cols:
            conn.execute(db.text("ALTER TABLE expedicao_ordem_fat ADD COLUMN excluido BOOLEAN NOT NULL DEFAULT 0"))
        if "excluido_at" not in cols:
            conn.execute(db.text("ALTER TABLE expedicao_ordem_fat ADD COLUMN excluido_at DATETIME"))
        if "excluido_by" not in cols:
            conn.execute(db.text("ALTER TABLE expedicao_ordem_fat ADD COLUMN excluido_by VARCHAR(100)"))
        if "excluido_motivo" not in cols:
            conn.execute(db.text("ALTER TABLE expedicao_ordem_fat ADD COLUMN excluido_motivo VARCHAR(300)"))
        conn.commit()

        _backfill_codigo_interno(conn, "expedicao_ordem_fat", "OF-")
        _create_index_if_missing(
            conn, "expedicao_ordem_fat", "ix_expedicao_ordem_fat_codigo_interno",
            "CREATE UNIQUE INDEX ix_expedicao_ordem_fat_codigo_interno ON expedicao_ordem_fat (codigo_interno)",
        )
    finally:
        conn.close()


def _ensure_expedicao_ordem_st_columns() -> None:
    """Garante colunas novas na tabela de ordens de Servico de Terceiro (ST)."""
    if not _has_table("expedicao_ordem_st"):
        return
    cols = _get_column_names("expedicao_ordem_st")
    conn = db.engine.connect()
    try:
        if "dt_prevista_entrega" not in cols:
            conn.execute(db.text("ALTER TABLE expedicao_ordem_st ADD COLUMN dt_prevista_entrega DATETIME"))
        if "conferido_pos_faturamento" not in cols:
            conn.execute(db.text(
                "ALTER TABLE expedicao_ordem_st ADD COLUMN conferido_pos_faturamento BOOLEAN NOT NULL DEFAULT 0"
            ))
        if "codigo_interno" not in cols:
            conn.execute(db.text("ALTER TABLE expedicao_ordem_st ADD COLUMN codigo_interno VARCHAR(20)"))
        if "excluido" not in cols:
            conn.execute(db.text("ALTER TABLE expedicao_ordem_st ADD COLUMN excluido BOOLEAN NOT NULL DEFAULT 0"))
        if "excluido_at" not in cols:
            conn.execute(db.text("ALTER TABLE expedicao_ordem_st ADD COLUMN excluido_at DATETIME"))
        if "excluido_by" not in cols:
            conn.execute(db.text("ALTER TABLE expedicao_ordem_st ADD COLUMN excluido_by VARCHAR(100)"))
        if "excluido_motivo" not in cols:
            conn.execute(db.text("ALTER TABLE expedicao_ordem_st ADD COLUMN excluido_motivo VARCHAR(300)"))
        conn.commit()

        _backfill_codigo_interno(conn, "expedicao_ordem_st", "OC-")
        _create_index_if_missing(
            conn, "expedicao_ordem_st", "ix_expedicao_ordem_st_codigo_interno",
            "CREATE UNIQUE INDEX ix_expedicao_ordem_st_codigo_interno ON expedicao_ordem_st (codigo_interno)",
        )
    finally:
        conn.close()


def _ensure_expedicao_conferencia_log_columns() -> None:
    """Garante a coluna de codigo interno na trilha de auditoria da expedicao."""
    if not _has_table("expedicao_conferencia_log"):
        return
    cols = _get_column_names("expedicao_conferencia_log")
    conn = db.engine.connect()
    try:
        if "codigo_interno" not in cols:
            conn.execute(db.text("ALTER TABLE expedicao_conferencia_log ADD COLUMN codigo_interno VARCHAR(20)"))
        conn.commit()

        _backfill_codigo_interno(conn, "expedicao_conferencia_log", "CNF-")
        _create_index_if_missing(
            conn, "expedicao_conferencia_log", "ix_expedicao_conferencia_log_codigo_interno",
            "CREATE UNIQUE INDEX ix_expedicao_conferencia_log_codigo_interno ON expedicao_conferencia_log (codigo_interno)",
        )
    finally:
        conn.close()


def _backfill_codigo_interno(conn, table_name: str, prefixo: str) -> None:
    """Preenche codigo_interno das linhas antigas (NULL) com {prefixo}{id:06d}.

    Feito linha a linha via ORM/parametros (nao concatenacao de string) para
    ficar portavel entre SQLite/Postgres/MySQL."""
    rows = conn.execute(
        db.text(f"SELECT id FROM {table_name} WHERE codigo_interno IS NULL")
    ).fetchall()
    for (row_id,) in rows:
        codigo = f"{prefixo}{row_id:06d}"
        conn.execute(
            db.text(f"UPDATE {table_name} SET codigo_interno = :codigo WHERE id = :id"),
            {"codigo": codigo, "id": row_id},
        )
    if rows:
        conn.commit()


def _ensure_solicitacao_nf_columns() -> None:
    """Garante as colunas de retorno (Faturamento avulso) na solicitacao_nf."""
    if not _has_table("solicitacao_nf"):
        return
    cols = _get_column_names("solicitacao_nf")
    conn = db.engine.connect()
    try:
        if "numero_nf_retorno" not in cols:
            conn.execute(db.text("ALTER TABLE solicitacao_nf ADD COLUMN numero_nf_retorno VARCHAR(80)"))
        if "retorno_por" not in cols:
            conn.execute(db.text("ALTER TABLE solicitacao_nf ADD COLUMN retorno_por VARCHAR(100)"))
        if "retorno_at" not in cols:
            conn.execute(db.text("ALTER TABLE solicitacao_nf ADD COLUMN retorno_at DATETIME"))
        if "observacoes_retorno" not in cols:
            conn.execute(db.text("ALTER TABLE solicitacao_nf ADD COLUMN observacoes_retorno VARCHAR(500)"))
        if "nf_parceiro_nome" not in cols:
            conn.execute(db.text("ALTER TABLE solicitacao_nf ADD COLUMN nf_parceiro_nome VARCHAR(200)"))
        if "nf_parceiro_endereco" not in cols:
            conn.execute(db.text("ALTER TABLE solicitacao_nf ADD COLUMN nf_parceiro_endereco VARCHAR(400)"))
        if "ordem_faturamento" not in cols:
            conn.execute(db.text("ALTER TABLE solicitacao_nf ADD COLUMN ordem_faturamento INTEGER"))
        conn.commit()
    finally:
        conn.close()

    if _has_table("solicitacao_nf_item"):
        item_cols = _get_column_names("solicitacao_nf_item")
        conn = db.engine.connect()
        try:
            if "material_local" not in item_cols:
                conn.execute(db.text("ALTER TABLE solicitacao_nf_item ADD COLUMN material_local VARCHAR(160)"))
            conn.commit()
        finally:
            conn.close()


def _ensure_expedicao_romaneio_columns() -> None:
    """Garante as colunas de assinatura do conferente na expedicao_romaneio."""
    if not _has_table("expedicao_romaneio"):
        return
    cols = _get_column_names("expedicao_romaneio")
    conn = db.engine.connect()
    try:
        if "assinatura_conferente_file_name" not in cols:
            conn.execute(db.text("ALTER TABLE expedicao_romaneio ADD COLUMN assinatura_conferente_file_name VARCHAR(260)"))
        if "assinatura_conferente_file_path" not in cols:
            conn.execute(db.text("ALTER TABLE expedicao_romaneio ADD COLUMN assinatura_conferente_file_path VARCHAR(500)"))
        if "assinatura_conferente_uploadado_em" not in cols:
            conn.execute(db.text("ALTER TABLE expedicao_romaneio ADD COLUMN assinatura_conferente_uploadado_em DATETIME"))
        if "assinatura_conferente_uploadado_por" not in cols:
            conn.execute(db.text("ALTER TABLE expedicao_romaneio ADD COLUMN assinatura_conferente_uploadado_por VARCHAR(100)"))
        if "transportadora" not in cols:
            conn.execute(db.text("ALTER TABLE expedicao_romaneio ADD COLUMN transportadora VARCHAR(160)"))
        if "placa" not in cols:
            conn.execute(db.text("ALTER TABLE expedicao_romaneio ADD COLUMN placa VARCHAR(20)"))
        if "motorista" not in cols:
            conn.execute(db.text("ALTER TABLE expedicao_romaneio ADD COLUMN motorista VARCHAR(160)"))
        if "motorista_documento" not in cols:
            conn.execute(db.text("ALTER TABLE expedicao_romaneio ADD COLUMN motorista_documento VARCHAR(40)"))
        if "transportadora_documento" not in cols:
            conn.execute(db.text("ALTER TABLE expedicao_romaneio ADD COLUMN transportadora_documento VARCHAR(40)"))
        if "transportadora_dados_json" not in cols:
            conn.execute(db.text("ALTER TABLE expedicao_romaneio ADD COLUMN transportadora_dados_json TEXT"))
        if "foto_carregamento_file_name" not in cols:
            conn.execute(db.text("ALTER TABLE expedicao_romaneio ADD COLUMN foto_carregamento_file_name VARCHAR(260)"))
        if "foto_carregamento_file_path" not in cols:
            conn.execute(db.text("ALTER TABLE expedicao_romaneio ADD COLUMN foto_carregamento_file_path VARCHAR(500)"))
        if "foto_carregamento_uploadado_em" not in cols:
            conn.execute(db.text("ALTER TABLE expedicao_romaneio ADD COLUMN foto_carregamento_uploadado_em DATETIME"))
        if "foto_carregamento_uploadado_por" not in cols:
            conn.execute(db.text("ALTER TABLE expedicao_romaneio ADD COLUMN foto_carregamento_uploadado_por VARCHAR(100)"))
        if "cce_modalidade_pendente" not in cols:
            conn.execute(db.text("ALTER TABLE expedicao_romaneio ADD COLUMN cce_modalidade_pendente BOOLEAN NOT NULL DEFAULT 0"))
        if "cce_modalidade_aprovado_por" not in cols:
            conn.execute(db.text("ALTER TABLE expedicao_romaneio ADD COLUMN cce_modalidade_aprovado_por VARCHAR(100)"))
        if "cce_modalidade_aprovado_em" not in cols:
            conn.execute(db.text("ALTER TABLE expedicao_romaneio ADD COLUMN cce_modalidade_aprovado_em DATETIME"))
        if "cce_modalidade_detalhe" not in cols:
            conn.execute(db.text("ALTER TABLE expedicao_romaneio ADD COLUMN cce_modalidade_detalhe VARCHAR(1000)"))
        conn.commit()
    finally:
        conn.close()


def _ensure_expedicao_romaneio_nf_columns() -> None:
    """Garante colunas adicionais na expedicao_romaneio_nf: modfrete_nf
    (modalidade de frete declarada na NF-e) e ordem_compra (fluxo ST)."""
    if not _has_table("expedicao_romaneio_nf"):
        return
    cols = _get_column_names("expedicao_romaneio_nf")
    conn = db.engine.connect()
    try:
        if "modfrete_nf" not in cols:
            conn.execute(db.text("ALTER TABLE expedicao_romaneio_nf ADD COLUMN modfrete_nf VARCHAR(4)"))
        if "ordem_compra" not in cols:
            conn.execute(db.text("ALTER TABLE expedicao_romaneio_nf ADD COLUMN ordem_compra VARCHAR(80)"))
        conn.commit()
    finally:
        conn.close()


def _ensure_cadastro_atualizacao_publica_columns() -> None:
    """Garante as colunas de ICMS/beneficios fiscais na cadastro_atualizacao_publica."""
    if not _has_table("cadastro_atualizacao_publica"):
        return
    cols = _get_column_names("cadastro_atualizacao_publica")
    conn = db.engine.connect()
    try:
        if "contribuinte_icms" not in cols:
            conn.execute(db.text("ALTER TABLE cadastro_atualizacao_publica ADD COLUMN contribuinte_icms VARCHAR(60)"))
        if "possui_beneficios_fiscais" not in cols:
            conn.execute(db.text("ALTER TABLE cadastro_atualizacao_publica ADD COLUMN possui_beneficios_fiscais BOOLEAN NOT NULL DEFAULT 0"))
        if "beneficios_fiscais_descricao" not in cols:
            conn.execute(db.text("ALTER TABLE cadastro_atualizacao_publica ADD COLUMN beneficios_fiscais_descricao VARCHAR(500)"))
        conn.commit()
    finally:
        conn.close()


def _ensure_perfil_columns() -> None:
    """Adiciona colunas de self-service de perfil (usuario) e rastreio de sessao."""
    conn = db.engine.connect()
    try:
        usuario_cols = _get_column_names("usuario")
        if "nome_exibicao" not in usuario_cols:
            conn.execute(db.text("ALTER TABLE usuario ADD COLUMN nome_exibicao VARCHAR(120)"))
            conn.commit()
        if "telefone" not in usuario_cols:
            conn.execute(db.text("ALTER TABLE usuario ADD COLUMN telefone VARCHAR(40)"))
            conn.commit()
        if "tema" not in usuario_cols:
            conn.execute(db.text("ALTER TABLE usuario ADD COLUMN tema VARCHAR(10)"))
            conn.commit()
        if "senha_atualizada_em" not in usuario_cols:
            conn.execute(db.text("ALTER TABLE usuario ADD COLUMN senha_atualizada_em DATETIME"))
            conn.commit()

        if _has_table("active_session"):
            sessao_cols = _get_column_names("active_session")
            if "ip_address" not in sessao_cols:
                conn.execute(db.text("ALTER TABLE active_session ADD COLUMN ip_address VARCHAR(64)"))
                conn.commit()
            if "user_agent" not in sessao_cols:
                conn.execute(db.text("ALTER TABLE active_session ADD COLUMN user_agent VARCHAR(400)"))
                conn.commit()
    finally:
        conn.close()


def initialize_database(app: Flask) -> None:
    with app.app_context():
        try:
            _ensure_usuario_password_capacity()
        except Exception:
            pass
        try:
            db.create_all()
        except OperationalError as exc:
            # Em produção (ex.: PythonAnywhere), múltiplos workers podem subir
            # ao mesmo tempo e disputar o CREATE TABLE no primeiro boot.
            if not _is_mysql_table_exists_error(exc):
                raise

        try:
            _ensure_usuario_email_column()
        except Exception:
            pass

        try:
            _ensure_perfil_columns()
        except Exception:
            pass

        _ensure_default_admin_user()

        _ensure_agendamento_veiculos()

        try:
            _ensure_viagem_columns()
        except Exception:
            pass

        try:
            _ensure_item_nota_columns()
        except Exception:
            # Mantem compatibilidade com bancos antigos sem impedir startup.
            pass

        try:
            _ensure_qualidade_certificado_columns()
        except Exception:
            pass

        try:
            _ensure_expedicao_conferencia_simples_schema()
        except Exception:
            pass

        try:
            _ensure_expedicao_ordem_fat_columns()
        except Exception:
            pass

        try:
            _ensure_expedicao_ordem_st_columns()
        except Exception:
            pass

        try:
            _ensure_expedicao_conferencia_log_columns()
        except Exception:
            pass

        try:
            _ensure_solicitacao_nf_columns()
        except Exception:
            pass

        try:
            _ensure_expedicao_romaneio_columns()
        except Exception:
            pass

        try:
            _ensure_expedicao_romaneio_nf_columns()
        except Exception:
            pass

        try:
            _ensure_cadastro_atualizacao_publica_columns()
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
            ("origem", "VARCHAR(20) NOT NULL DEFAULT 'Local'"),
            ("grv_cod_empresa", "INTEGER"),
            ("grv_codigo", "INTEGER"),
            ("grv_identificacao", "VARCHAR(30)"),
            ("grv_apelido", "VARCHAR(100)"),
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
            ("solicitante_nome", "VARCHAR(120)"),
            ("liberador_id", "INTEGER"),
            ("liberado_em", "DATETIME"),
            ("liberado_por_username", "VARCHAR(100)"),
            ("cancelado_em", "DATETIME"),
            ("cancelado_por", "VARCHAR(100)"),
            ("motivo_cancelamento", "TEXT"),
            ("proxima_troca_em", "DATE"),
            ("lembrete_retirada_enviado_em", "DATETIME"),
            ("estoque_grv_antes", "FLOAT"),
            ("estoque_grv_depois", "FLOAT"),
            ("estoque_grv_baixado", "BOOLEAN"),
            ("estoque_grv_verificado_em", "DATETIME"),
            ("estoque_grv_mensagem", "VARCHAR(300)"),
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
    """Compatibiliza a tabela `usuario` com o schema atual do modelo.

    Banco legado pode faltar colunas como `ativo`, `ultimo_login_em`,
    `convite_token_hash`, etc. Essas colunas precisam existir antes que qualquer
    consulta do modelo `Usuario` execute, porque o ORM usa todas elas na query.
    """
    from sqlalchemy import text, inspect as sa_inspect
    insp = sa_inspect(db.engine)
    cols = {c["name"] for c in insp.get_columns("usuario")}
    if not cols:
        return

    def add_column(name: str, ddl_sql: str) -> None:
        if name in cols:
            return
        with db.engine.connect() as conn:
            conn.execute(text(ddl_sql))
            conn.commit()
        cols.add(name)

    if db.engine.dialect.name == "mysql":
        add_column("ativo", "ALTER TABLE usuario ADD COLUMN ativo BOOLEAN NOT NULL DEFAULT TRUE")
        add_column("nome_exibicao", "ALTER TABLE usuario ADD COLUMN nome_exibicao VARCHAR(120)")
        add_column("telefone", "ALTER TABLE usuario ADD COLUMN telefone VARCHAR(40)")
        add_column("tema", "ALTER TABLE usuario ADD COLUMN tema VARCHAR(10)")
        add_column("senha_atualizada_em", "ALTER TABLE usuario ADD COLUMN senha_atualizada_em DATETIME")
        add_column("ultimo_login_em", "ALTER TABLE usuario ADD COLUMN ultimo_login_em DATETIME")
        add_column("convite_token_hash", "ALTER TABLE usuario ADD COLUMN convite_token_hash VARCHAR(64)")
        add_column("convite_expires_at", "ALTER TABLE usuario ADD COLUMN convite_expires_at DATETIME")
        add_column("convite_enviado_em", "ALTER TABLE usuario ADD COLUMN convite_enviado_em DATETIME")
        add_column("convite_aceito_em", "ALTER TABLE usuario ADD COLUMN convite_aceito_em DATETIME")
        add_column("forcar_troca_senha", "ALTER TABLE usuario ADD COLUMN forcar_troca_senha BOOLEAN NOT NULL DEFAULT FALSE")
        add_column("criado_em", "ALTER TABLE usuario ADD COLUMN criado_em DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP")
        add_column("criado_por", "ALTER TABLE usuario ADD COLUMN criado_por VARCHAR(100)")
        add_column("atualizado_em", "ALTER TABLE usuario ADD COLUMN atualizado_em DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP")
        add_column("atualizado_por", "ALTER TABLE usuario ADD COLUMN atualizado_por VARCHAR(100)")
        add_column("email", "ALTER TABLE usuario ADD COLUMN email VARCHAR(160)")
    else:
        add_column("ativo", "ALTER TABLE usuario ADD COLUMN ativo BOOLEAN NOT NULL DEFAULT 1")
        add_column("nome_exibicao", "ALTER TABLE usuario ADD COLUMN nome_exibicao VARCHAR(120)")
        add_column("telefone", "ALTER TABLE usuario ADD COLUMN telefone VARCHAR(40)")
        add_column("tema", "ALTER TABLE usuario ADD COLUMN tema VARCHAR(10)")
        add_column("senha_atualizada_em", "ALTER TABLE usuario ADD COLUMN senha_atualizada_em DATETIME")
        add_column("ultimo_login_em", "ALTER TABLE usuario ADD COLUMN ultimo_login_em DATETIME")
        add_column("convite_token_hash", "ALTER TABLE usuario ADD COLUMN convite_token_hash VARCHAR(64)")
        add_column("convite_expires_at", "ALTER TABLE usuario ADD COLUMN convite_expires_at DATETIME")
        add_column("convite_enviado_em", "ALTER TABLE usuario ADD COLUMN convite_enviado_em DATETIME")
        add_column("convite_aceito_em", "ALTER TABLE usuario ADD COLUMN convite_aceito_em DATETIME")
        add_column("forcar_troca_senha", "ALTER TABLE usuario ADD COLUMN forcar_troca_senha BOOLEAN NOT NULL DEFAULT 0")
        add_column("criado_em", "ALTER TABLE usuario ADD COLUMN criado_em DATETIME DEFAULT CURRENT_TIMESTAMP")
        add_column("criado_por", "ALTER TABLE usuario ADD COLUMN criado_por VARCHAR(100)")
        add_column("atualizado_em", "ALTER TABLE usuario ADD COLUMN atualizado_em DATETIME DEFAULT CURRENT_TIMESTAMP")
        add_column("atualizado_por", "ALTER TABLE usuario ADD COLUMN atualizado_por VARCHAR(100)")
        add_column("email", "ALTER TABLE usuario ADD COLUMN email VARCHAR(160)")

    if "email" not in cols:
        with db.engine.connect() as conn:
            conn.execute(text("ALTER TABLE usuario ADD COLUMN email VARCHAR(160)"))
            conn.commit()
        cols.add("email")

    # Remove non-admin users only when creating email migration on a legacy table,
    # to force re-registration of the remaining users with proper email data.
    if "email" in cols and "email" not in {c["name"] for c in sa_inspect(db.engine).get_columns("usuario")}:
        pass

    # Para serializar users antigos sem email, mantemos os admins e limpamos os demais
    # durante a migração de email apenas se a coluna realmente acabou de ser criada.
    if "email" in cols:
        with db.engine.connect() as conn:
            current_columns = {c["name"] for c in sa_inspect(db.engine).get_columns("usuario")}
            if "email" in current_columns and "email" not in cols:
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
                    "email VARCHAR(160) UNIQUE, "
                    "ativo BOOLEAN NOT NULL DEFAULT 1, "
                    "nome_exibicao VARCHAR(120), "
                    "telefone VARCHAR(40), "
                    "tema VARCHAR(10), "
                    "senha_atualizada_em DATETIME, "
                    "ultimo_login_em DATETIME, "
                    "convite_token_hash VARCHAR(64), "
                    "convite_expires_at DATETIME, "
                    "convite_enviado_em DATETIME, "
                    "convite_aceito_em DATETIME, "
                    "forcar_troca_senha BOOLEAN NOT NULL DEFAULT 0, "
                    "criado_em DATETIME DEFAULT CURRENT_TIMESTAMP, "
                    "criado_por VARCHAR(100), "
                    "atualizado_em DATETIME DEFAULT CURRENT_TIMESTAMP, "
                    "atualizado_por VARCHAR(100))"
                ))
                conn.execute(text(
                    "INSERT INTO usuario_tmp (id, username, password, role, email, ativo, nome_exibicao, telefone, tema, senha_atualizada_em, ultimo_login_em, convite_token_hash, convite_expires_at, convite_enviado_em, convite_aceito_em, forcar_troca_senha, criado_em, criado_por, atualizado_em, atualizado_por) "
                    "SELECT id, username, password, role, email, ativo, nome_exibicao, telefone, tema, senha_atualizada_em, ultimo_login_em, convite_token_hash, convite_expires_at, convite_enviado_em, convite_aceito_em, forcar_troca_senha, criado_em, criado_por, atualizado_em, atualizado_por FROM usuario"
                ))
                conn.execute(text("DROP TABLE usuario"))
                conn.execute(text("ALTER TABLE usuario_tmp RENAME TO usuario"))
                conn.commit()


def _backfill_orcamento_solicitacao_entrega() -> None:
    """Preenche o orcamento das solicitacoes de ENTREGA geradas a partir de
    romaneios (payload_origem tem romaneio_id), para permitir auditar/filtrar
    por orcamento sem reprocessar as automacoes."""
    import json

    from .models import AgendamentoSolicitacao, ExpedicaoRomaneio

    rows = (
        AgendamentoSolicitacao.query
        .filter(
            AgendamentoSolicitacao.tipo == "ENTREGA",
            AgendamentoSolicitacao.payload_origem.isnot(None),
        )
        .all()
    )
    romaneios: dict = {}
    alterou = False
    for row in rows:
        if str(getattr(row, "orcamento", "") or "").strip():
            continue
        try:
            payload = json.loads(row.payload_origem or "{}")
        except Exception:
            continue
        rid = payload.get("romaneio_id")
        if not rid:
            continue
        if rid not in romaneios:
            romaneios[rid] = db.session.get(ExpedicaoRomaneio, rid)
        romaneio = romaneios[rid]
        orcamento = str(getattr(romaneio, "orcamento", "") or "").strip() if romaneio else ""
        if orcamento:
            row.orcamento = orcamento[:80]
            alterou = True
    if alterou:
        db.session.commit()


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
        orcamento_recem_criado = "orcamento" not in cols_solicitacao
        extra_sol_cols = [
            ("data_desejada", "ALTER TABLE agendamento_solicitacao ADD COLUMN data_desejada DATETIME"),
            ("cancelamento_pendente", "ALTER TABLE agendamento_solicitacao ADD COLUMN cancelamento_pendente BOOLEAN NOT NULL DEFAULT 0"),
            ("cancelamento_solicitado_por", "ALTER TABLE agendamento_solicitacao ADD COLUMN cancelamento_solicitado_por VARCHAR(100)"),
            ("cancelamento_motivo_pendente", "ALTER TABLE agendamento_solicitacao ADD COLUMN cancelamento_motivo_pendente VARCHAR(500)"),
            ("tempo_estimado_min", "ALTER TABLE agendamento_solicitacao ADD COLUMN tempo_estimado_min INTEGER"),
            ("orcamento", "ALTER TABLE agendamento_solicitacao ADD COLUMN orcamento VARCHAR(80)"),
        ]
        for col_name, ddl in extra_sol_cols:
            if col_name not in cols_solicitacao:
                try:
                    conn.execute(db.text(ddl))
                    conn.commit()
                except Exception:
                    # Coluna ja existe (ex.: adicionada em um reload anterior) ou
                    # inspecao retornou estado desatualizado: ignora com seguranca.
                    conn.rollback()
    finally:
        conn.close()

    if orcamento_recem_criado:
        try:
            _backfill_orcamento_solicitacao_entrega()
        except Exception:
            pass

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


def _ensure_viagem_columns() -> None:
    if not _has_table("viagem"):
        return
    conn = db.engine.connect()
    try:
        cols = _get_column_names("viagem")
        missing_columns = [
            ("avulsa", "ALTER TABLE viagem ADD COLUMN avulsa BOOLEAN NOT NULL DEFAULT 0"),
            ("funcionario_responsavel", "ALTER TABLE viagem ADD COLUMN funcionario_responsavel VARCHAR(160)"),
        ]
        for column_name, ddl in missing_columns:
            if column_name not in cols:
                conn.execute(db.text(ddl))
                conn.commit()
    finally:
        conn.close()

