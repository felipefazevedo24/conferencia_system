import os
from pathlib import Path
from datetime import timedelta


BASE_DIR = Path(__file__).resolve().parent.parent


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "fam_2026_sistema_total")
    _db_path = Path(os.environ.get("DB_PATH", BASE_DIR / "database.db"))
    SQLALCHEMY_DATABASE_URI = os.environ.get("DATABASE_URL", f"sqlite:///{_db_path}")
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    CONSYSTE_TOKEN = os.environ.get("CONSYSTE_TOKEN", "T-PsbZoTuzx1CAj1yYgz")
    CONSYSTE_API_BASE = "https://portal.consyste.com.br/api/v1"
    CONSYSTE_CONSULTA = "https://portal.consyste.com.br/app/nfe/lista/recebidos/o/emitido_em/desc"
    EXPEDICAO_REPORTS_DIR = os.environ.get("EXPEDICAO_REPORTS_DIR", r"Z:\PUBLICO\SNData\eReports")

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
