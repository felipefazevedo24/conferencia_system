import os
import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from flask import current_app, has_app_context


def _is_filled(value) -> bool:
    return str(value or "").strip() != ""


def _cfg(name: str, default=None):
    values = [os.environ.get(name)]
    if has_app_context():
        values.append(current_app.config.get(name))
    values.append(default)
    for value in values:
        if _is_filled(value):
            return value
    return ""


def _cfg_int(name: str, default: int) -> int:
    try:
        return int(_cfg(name, default))
    except (TypeError, ValueError):
        return default


def _cfg_bool(name: str, default: bool = False) -> bool:
    raw = str(_cfg(name, "1" if default else "0")).strip().lower()
    return raw in {"1", "true", "yes", "on", "sim"}


def _load_erp_file_config() -> dict:
    if not has_app_context():
        return {}
    path = Path(current_app.instance_path) / "erp_lancamento_config.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        return data if isinstance(data, dict) else {}
    except Exception:
        current_app.logger.exception("Falha ao ler erp_lancamento_config.json para Compras")
        return {}


def _first_value(*values, default=""):
    for value in values:
        if _is_filled(value):
            return value
    return default


@dataclass(frozen=True)
class ComprasSettings:
    PG_HOST: str
    PG_PORT: int
    PG_DATABASE: str
    PG_USER: str
    PG_PASSWORD: str
    PG_COD_EMPRESA: int
    PG_CONNECT_TIMEOUT: int
    APP_DB_SLOW_MS: int
    API_URL: str
    API_TOKEN: str
    API_TIMEOUT: int
    USE_API_BRIDGE: bool

    @property
    def dsn(self) -> str:
        return (
            f"host={self.PG_HOST} port={self.PG_PORT} dbname={self.PG_DATABASE} "
            f"user={self.PG_USER} password={self.PG_PASSWORD} "
            f"connect_timeout={self.PG_CONNECT_TIMEOUT}"
        )


@lru_cache(maxsize=1)
def get_settings() -> ComprasSettings:
    arquivo = _load_erp_file_config()
    return ComprasSettings(
        PG_HOST=str(_first_value(_cfg("COMPRAS_PG_HOST"), _cfg("ERP_LANCAMENTO_PG_HOST"), arquivo.get("host"), default="localhost")),
        PG_PORT=int(_first_value(_cfg("COMPRAS_PG_PORT"), _cfg("ERP_LANCAMENTO_PG_PORT"), arquivo.get("port"), default=5432)),
        PG_DATABASE=str(_first_value(_cfg("COMPRAS_PG_DATABASE"), _cfg("ERP_LANCAMENTO_PG_DB"), arquivo.get("database"), default="erp")),
        PG_USER=str(_first_value(_cfg("COMPRAS_PG_USER"), _cfg("ERP_LANCAMENTO_PG_USER"), arquivo.get("user"), default="postgres")),
        PG_PASSWORD=str(_first_value(_cfg("COMPRAS_PG_PASSWORD"), _cfg("ERP_LANCAMENTO_PG_PASSWORD"), arquivo.get("password"), default="")),
        PG_COD_EMPRESA=_cfg_int("COMPRAS_PG_COD_EMPRESA", _cfg_int("ERP_ESTOQUE_PG_COMPANY", 1)),
        PG_CONNECT_TIMEOUT=_cfg_int("COMPRAS_PG_CONNECT_TIMEOUT", 8),
        APP_DB_SLOW_MS=_cfg_int("COMPRAS_DB_SLOW_MS", 900),
        API_URL=str(_first_value(_cfg("COMPRAS_API_URL"), _cfg("ERP_LANCAMENTO_API_URL"), arquivo.get("api_url"), default="")).rstrip("/"),
        API_TOKEN=str(_first_value(_cfg("COMPRAS_API_TOKEN"), _cfg("ERP_LANCAMENTO_API_TOKEN"), arquivo.get("api_token"), default="")),
        API_TIMEOUT=int(_first_value(_cfg("COMPRAS_API_TIMEOUT"), _cfg("ERP_LANCAMENTO_API_TIMEOUT"), arquivo.get("api_timeout"), default=60)),
        USE_API_BRIDGE=_cfg_bool("COMPRAS_USE_API_BRIDGE", True),
    )


def clear_settings_cache() -> None:
    get_settings.cache_clear()
