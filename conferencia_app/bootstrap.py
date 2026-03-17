from flask import Flask
from werkzeug.security import generate_password_hash

from .extensions import db
from .models import Usuario


def _ensure_item_nota_columns() -> None:
    conn = db.engine.connect()
    try:
        res = conn.execute(db.text("PRAGMA table_info('item_nota')")).fetchall()
        cols = [row[1] for row in res]

        if "numero_lancamento" not in cols:
            conn.execute(db.text("ALTER TABLE item_nota ADD COLUMN numero_lancamento VARCHAR"))
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
        if "pedido_compra" not in cols:
            conn.execute(db.text("ALTER TABLE item_nota ADD COLUMN pedido_compra VARCHAR(50)"))
            conn.commit()
        if "material_cliente" not in cols:
            conn.execute(db.text("ALTER TABLE item_nota ADD COLUMN material_cliente BOOLEAN NOT NULL DEFAULT 0"))
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

        res_log_div = conn.execute(db.text("PRAGMA table_info('log_divergencia')")).fetchall()
        cols_log_div = [row[1] for row in res_log_div]
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

        res_lock = conn.execute(db.text("PRAGMA table_info('conferencia_lock')")).fetchall()
        cols_lock = [row[1] for row in res_lock]
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
        conn.execute(
            db.text(
                "CREATE UNIQUE INDEX IF NOT EXISTS ux_permissao_scope_key ON permissao_acesso (scope_type, scope_id, permission_key)"
            )
        )
        conn.commit()

        res_fat = conn.execute(db.text("PRAGMA table_info('expedicao_faturamento')")).fetchall()
        cols_fat = [row[1] for row in res_fat]
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


def initialize_database(app: Flask) -> None:
    with app.app_context():
        db.create_all()
        
        # Criar tabelas WMS se não existirem
        try:
            _ensure_wms_tables()
        except Exception:
            pass

        if not Usuario.query.filter_by(username="admin").first():
            admin = Usuario(
                username="admin",
                password=generate_password_hash("admin123"),
                role="Admin",
            )
            db.session.add(admin)
            db.session.commit()

        try:
            _ensure_item_nota_columns()
        except Exception:
            # Mantem compatibilidade com bancos antigos sem impedir startup.
            pass


def _ensure_wms_tables() -> None:
    """Garante que as tabelas WMS existam no banco de dados"""
    conn = db.engine.connect()
    try:
        # Tabela localizacao_armazem
        conn.execute(
            db.text(
                """
                CREATE TABLE IF NOT EXISTS localizacao_armazem (
                    id INTEGER PRIMARY KEY,
                    codigo VARCHAR(50) UNIQUE NOT NULL,
                    corredor VARCHAR(10) NOT NULL,
                    prateleira VARCHAR(10) NOT NULL,
                    posicao VARCHAR(10) NOT NULL,
                    capacidade_maxima FLOAT NOT NULL DEFAULT 100.0,
                    capacidade_atual FLOAT NOT NULL DEFAULT 0.0,
                    ativo BOOLEAN NOT NULL DEFAULT 1,
                    data_criacao DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
        )
        conn.commit()
        
        # Create indexes for localizacao_armazem
        conn.execute(db.text(
            "CREATE INDEX IF NOT EXISTS ix_localizacao_armazem_codigo ON localizacao_armazem (codigo)"
        ))
        conn.commit()

        res_loc = conn.execute(db.text("PRAGMA table_info('localizacao_armazem')")).fetchall()
        cols_loc = [row[1] for row in res_loc]
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
                    ativo BOOLEAN NOT NULL DEFAULT 1,
                    data_criacao DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (localizacao_id) REFERENCES localizacao_armazem(id)
                )
                """
            )
        )
        conn.commit()
        
        # Create indexes for item_wms
        conn.execute(db.text(
            "CREATE INDEX IF NOT EXISTS ix_item_wms_numero_nota ON item_wms (numero_nota)"
        ))
        conn.execute(db.text(
            "CREATE INDEX IF NOT EXISTS ix_item_wms_codigo_item ON item_wms (codigo_item)"
        ))
        conn.execute(db.text(
            "CREATE INDEX IF NOT EXISTS ix_item_wms_localizacao_id ON item_wms (localizacao_id)"
        ))
        conn.execute(db.text(
            "CREATE INDEX IF NOT EXISTS ix_item_wms_status ON item_wms (status)"
        ))
        conn.commit()

        res_item_wms = conn.execute(db.text("PRAGMA table_info('item_wms')")).fetchall()
        cols_item_wms = [row[1] for row in res_item_wms]
        if "codigo_grv" not in cols_item_wms:
            conn.execute(db.text("ALTER TABLE item_wms ADD COLUMN codigo_grv VARCHAR(80)"))
            conn.commit()
        if "ordem_servico" not in cols_item_wms:
            conn.execute(db.text("ALTER TABLE item_wms ADD COLUMN ordem_servico VARCHAR(80)"))
            conn.commit()
        if "ordem_compra" not in cols_item_wms:
            conn.execute(db.text("ALTER TABLE item_wms ADD COLUMN ordem_compra VARCHAR(80)"))
            conn.commit()
        
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
        
        # Create indexes for movimentacao_wms
        conn.execute(db.text(
            "CREATE INDEX IF NOT EXISTS ix_movimentacao_wms_item_wms_id ON movimentacao_wms (item_wms_id)"
        ))
        conn.execute(db.text(
            "CREATE INDEX IF NOT EXISTS ix_movimentacao_wms_numero_nota ON movimentacao_wms (numero_nota)"
        ))
        conn.commit()
        
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
        
        # Create indexes for estoque_wms
        conn.execute(db.text(
            "CREATE INDEX IF NOT EXISTS ix_estoque_wms_codigo_item ON estoque_wms (codigo_item)"
        ))
        conn.execute(db.text(
            "CREATE INDEX IF NOT EXISTS ix_estoque_wms_localizacao_id ON estoque_wms (localizacao_id)"
        ))
        conn.commit()

        res_estoque_wms = conn.execute(db.text("PRAGMA table_info('estoque_wms')")).fetchall()
        cols_estoque_wms = [row[1] for row in res_estoque_wms]
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
        conn.execute(db.text("CREATE INDEX IF NOT EXISTS ix_wms_integracao_status ON wms_integracao_evento (status)"))
        conn.execute(db.text("CREATE INDEX IF NOT EXISTS ix_wms_integracao_referencia ON wms_integracao_evento (referencia)"))
        conn.commit()

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
        conn.execute(db.text("CREATE INDEX IF NOT EXISTS ix_wms_sku_mestre_codigo_erp ON wms_sku_mestre (codigo_erp)"))
        conn.commit()

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
        conn.execute(db.text("CREATE INDEX IF NOT EXISTS ix_wms_reconciliacao_nota ON wms_reconciliacao_divergencia (numero_nota)"))
        conn.execute(db.text("CREATE INDEX IF NOT EXISTS ix_wms_reconciliacao_status ON wms_reconciliacao_divergencia (status)"))
        conn.commit()

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
        conn.execute(db.text("CREATE INDEX IF NOT EXISTS ix_wms_alerta_status ON wms_alerta_operacional (status)"))
        conn.commit()

    finally:
        conn.close()
