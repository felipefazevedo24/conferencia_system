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
    SQLALCHEMY_ENGINE_OPTIONS = {} if SQLALCHEMY_DATABASE_URI.startswith("sqlite:") else {"pool_pre_ping": True}

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
