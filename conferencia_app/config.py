import os
from pathlib import Path
from datetime import timedelta


BASE_DIR = Path(__file__).resolve().parent.parent


def _normalize_database_url(raw_url: str) -> str:
    url = str(raw_url or "").strip()
    if not url:
        return ""
    if url.startswith("mysql://"):
        return url.replace("mysql://", "mysql+pymysql://", 1)
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql://", 1)
    return url


def _env_bool(name: str, default: str = "0") -> bool:
    return str(os.environ.get(name, default)).strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: str = "") -> int | None:
    raw_value = str(os.environ.get(name, default) or "").strip()
    if not raw_value:
        return None
    try:
        return int(raw_value)
    except (TypeError, ValueError):
        return None


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "fam_2026_sistema_total")
    _db_path = Path(os.environ.get("DB_PATH", BASE_DIR / "database.db")).expanduser()
    _database_url = _normalize_database_url(os.environ.get("DATABASE_URL", ""))
    SQLALCHEMY_DATABASE_URI = _database_url or f"sqlite:///{_db_path.as_posix()}"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = (
        {}
        if SQLALCHEMY_DATABASE_URI.startswith("sqlite:")
        else {
            "pool_pre_ping": True,
            "pool_recycle": int(os.environ.get("DB_POOL_RECYCLE_SECONDS", "280")),
        }
    )

    CONSYSTE_TOKEN = os.environ.get("CONSYSTE_TOKEN", "T-PsbZoTuzx1CAj1yYgz")
    CONSYSTE_API_BASE = "https://portal.consyste.com.br/api/v1"
    CONSYSTE_CONSULTA = "https://portal.consyste.com.br/app/nfe/lista/recebidos/o/emitido_em/desc"
    EMPRESA_CNPJ = os.environ.get("EMPRESA_CNPJ", "30482274000125")
    EXPEDICAO_REPORTS_DIR = os.environ.get("EXPEDICAO_REPORTS_DIR", r"Z:\PUBLICO\SNData\eReports")
    EXPEDICAO_CONFERENCIA_FOTOS_DIR = os.environ.get("EXPEDICAO_CONFERENCIA_FOTOS_DIR", "")
    EXPEDICAO_GOOGLE_DRIVE_FOLDER_ID = os.environ.get("EXPEDICAO_GOOGLE_DRIVE_FOLDER_ID", "1Kc5JBmmPlQGF8lwT4xU0dnnFijk9tYiu")
    GOOGLE_DRIVE_SERVICE_ACCOUNT_JSON = os.environ.get("GOOGLE_DRIVE_SERVICE_ACCOUNT_JSON", "")
    GOOGLE_DRIVE_SERVICE_ACCOUNT_FILE = os.environ.get("GOOGLE_DRIVE_SERVICE_ACCOUNT_FILE", "")
    INVENTREE_WMS_ENABLED = _env_bool("INVENTREE_WMS_ENABLED", "0")
    INVENTREE_API_BASE = str(os.environ.get("INVENTREE_API_BASE", "")).strip().rstrip("/")
    INVENTREE_API_TOKEN = str(os.environ.get("INVENTREE_API_TOKEN", "")).strip()
    INVENTREE_ROOT_LOCATION_ID = _env_int("INVENTREE_ROOT_LOCATION_ID")
    INVENTREE_PENDING_LOCATION_ID = _env_int("INVENTREE_PENDING_LOCATION_ID")
    INVENTREE_DEFAULT_PART_CATEGORY_ID = _env_int("INVENTREE_DEFAULT_PART_CATEGORY_ID")
    INVENTREE_TIMEOUT_SECONDS = int(os.environ.get("INVENTREE_TIMEOUT_SECONDS", "20"))
    INVENTREE_STOCK_NOTE_PREFIX = str(os.environ.get("INVENTREE_STOCK_NOTE_PREFIX", "ERP/WMS")).strip() or "ERP/WMS"

    ERP_ESTOQUE_URL = os.environ.get("ERP_ESTOQUE_URL", "https://superelevated-han-throughly.ngrok-free.dev/estoque")
    ERP_ESTOQUE_TIMEOUT = int(os.environ.get("ERP_ESTOQUE_TIMEOUT", "30"))

    # Sincronizacao automatica ERP -> WMS (estoque + enderecos)
    ERP_SYNC_AUTO_ENABLED = os.environ.get("ERP_SYNC_AUTO_ENABLED", "1") not in ("0", "false", "False", "")
    ERP_SYNC_POLL_INTERVAL_SECONDS = int(os.environ.get("ERP_SYNC_POLL_INTERVAL_SECONDS", "600"))

    BOLETO_PROVIDER = str(os.environ.get("BOLETO_PROVIDER", "BB")).strip().upper() or "BB"
    BOLETO_BANK_LABEL = str(os.environ.get("BOLETO_BANK_LABEL", "Banco do Brasil")).strip() or "Banco do Brasil"

    BB_CLIENT_ID = str(os.environ.get("BB_CLIENT_ID", "")).strip()
    BB_CLIENT_SECRET = str(os.environ.get("BB_CLIENT_SECRET", "")).strip()
    BB_DEVELOPER_APPLICATION_KEY = str(
        os.environ.get("BB_DEVELOPER_APPLICATION_KEY", os.environ.get("BB_DEV_APP_KEY", os.environ.get("BB_APP_KEY", "")))
    ).strip()
    BB_API_BASE = str(os.environ.get("BB_API_BASE", "https://api.hm.bb.com.br/cobrancas/v2")).strip().rstrip("/")
    BB_OAUTH_BASE = str(os.environ.get("BB_OAUTH_BASE", "https://oauth.hm.bb.com.br")).strip().rstrip("/")
    BB_OAUTH_TOKEN_PATH = str(os.environ.get("BB_OAUTH_TOKEN_PATH", "/oauth/token")).strip()
    BB_OAUTH_TOKEN_URL = str(os.environ.get("BB_OAUTH_TOKEN_URL", "")).strip()
    BB_SCOPE = str(os.environ.get("BB_SCOPE", "cobrancas.boletos-info")).strip()
    BB_CONVENIO = str(os.environ.get("BB_CONVENIO", "")).strip()
    BB_CERT_PATH = str(os.environ.get("BB_CERT_PATH", "")).strip()
    BB_KEY_PATH = str(os.environ.get("BB_KEY_PATH", "")).strip()
    BB_API_TIMEOUT_SECONDS = int(os.environ.get("BB_API_TIMEOUT_SECONDS", "30"))

    AGENDAMENTO_FORNECEDORES_XLSX = str(
        os.environ.get("AGENDAMENTO_FORNECEDORES_XLSX", BASE_DIR / "fornecedores.xlsx")
    ).strip()
    AGENDAMENTO_CLIENTES_XLSX = str(
        os.environ.get("AGENDAMENTO_CLIENTES_XLSX", BASE_DIR / "clientes.xlsx")
    ).strip()
    AGENDAMENTO_CONFLITO_MINUTOS = int(os.environ.get("AGENDAMENTO_CONFLITO_MINUTOS", "30"))
    AGENDAMENTO_DURACAO_PADRAO_MINUTOS = int(os.environ.get("AGENDAMENTO_DURACAO_PADRAO_MINUTOS", "120"))
    AGENDAMENTO_BASE_ORIGEM = str(os.environ.get("AGENDAMENTO_BASE_ORIGEM", "Avenida Carlos Roberto Prataviera, 600 - Jardim Nova Europa, Indaiatuba - SP, 13184-889")).strip()
    AGENDAMENTO_BASE_LATITUDE = float(os.environ.get("AGENDAMENTO_BASE_LATITUDE", "-23.0903") or 0)
    AGENDAMENTO_BASE_LONGITUDE = float(os.environ.get("AGENDAMENTO_BASE_LONGITUDE", "-47.2186") or 0)
    AGENDAMENTO_ESTIMATIVA_KM_FATOR = float(os.environ.get("AGENDAMENTO_ESTIMATIVA_KM_FATOR", "1.28") or 1.28)
    AGENDAMENTO_GEOCODE_TIMEOUT_SECONDS = int(os.environ.get("AGENDAMENTO_GEOCODE_TIMEOUT_SECONDS", "8"))
    AGENDAMENTO_GEOCODE_URL = str(
        os.environ.get("AGENDAMENTO_GEOCODE_URL", "https://nominatim.openstreetmap.org/search")
    ).strip().rstrip("/")

    # Email SMTP
    MAIL_SMTP_SERVER = os.environ.get("MAIL_SMTP_SERVER", "smtp.gmail.com")
    MAIL_SMTP_PORT = int(os.environ.get("MAIL_SMTP_PORT", "587"))
    MAIL_SENDER = os.environ.get("MAIL_SENDER", "sync.columbia@gmail.com")
    MAIL_PASSWORD = os.environ.get("MAIL_PASSWORD", "cvwu wwdq kbbw ridh")
    MAIL_SENDER_NAME = os.environ.get("MAIL_SENDER_NAME", "Columbia Sync")

    # Envio automatico de NF-e emitida para cliente/destinatario
    # Em modo teste, todos os envios sao redirecionados para NFE_EMAIL_TESTE_DESTINO.
    NFE_EMAIL_MODO_TESTE = os.environ.get("NFE_EMAIL_MODO_TESTE", "1") == "1"
    NFE_EMAIL_TESTE_DESTINO = os.environ.get("NFE_EMAIL_TESTE_DESTINO", "felaze@colmac.com")
    NFE_EMAIL_AUTO_NO_FATURAMENTO = os.environ.get("NFE_EMAIL_AUTO_NO_FATURAMENTO", "1") == "1"
    # Scheduler automatico de envio de NF-e emitidas (poll Consyste)
    NFE_EMAIL_AUTO_ENABLED = os.environ.get("NFE_EMAIL_AUTO_ENABLED", "1") == "1"
    NFE_EMAIL_AUTO_DESDE = os.environ.get("NFE_EMAIL_AUTO_DESDE", "")  # YYYY-MM-DD; vazio = define no primeiro boot
    NFE_EMAIL_POLL_INTERVAL_SECONDS = int(os.environ.get("NFE_EMAIL_POLL_INTERVAL_SECONDS", "300"))
    # E-mails sempre em copia em qualquer envio de NF-e (separados por virgula)
    NFE_EMAIL_CC = os.environ.get("NFE_EMAIL_CC", "")

    PERMANENT_SESSION_LIFETIME = timedelta(minutes=int(os.environ.get("SESSION_TIMEOUT_MINUTES", "30")))
    SESSION_TIMEOUT_MINUTES = int(os.environ.get("SESSION_TIMEOUT_MINUTES", "30"))
    LOGIN_MAX_ATTEMPTS = int(os.environ.get("LOGIN_MAX_ATTEMPTS", "5"))
    LOGIN_LOCK_MINUTES = int(os.environ.get("LOGIN_LOCK_MINUTES", "10"))
    LOCK_TIMEOUT_MINUTES = int(os.environ.get("LOCK_TIMEOUT_MINUTES", "25"))


if os.environ.get("FLASK_ENV") == "production":
    if not os.environ.get("SECRET_KEY"):
        raise RuntimeError("SECRET_KEY deve ser definido em produção.")
    if not os.environ.get("CONSYSTE_TOKEN"):
        raise RuntimeError("CONSYSTE_TOKEN deve ser definido em produção.")
