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
