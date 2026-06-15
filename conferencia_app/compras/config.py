import os
from dataclasses import dataclass
from functools import lru_cache

from flask import current_app, has_app_context


def _cfg(name: str, default=None):
    if has_app_context():
        return current_app.config.get(name, os.environ.get(name, default))
    return os.environ.get(name, default)


def _cfg_int(name: str, default: int) -> int:
    try:
        return int(_cfg(name, default))
    except (TypeError, ValueError):
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

    @property
    def dsn(self) -> str:
        return (
            f"host={self.PG_HOST} port={self.PG_PORT} dbname={self.PG_DATABASE} "
            f"user={self.PG_USER} password={self.PG_PASSWORD} "
            f"connect_timeout={self.PG_CONNECT_TIMEOUT}"
        )


@lru_cache(maxsize=1)
def get_settings() -> ComprasSettings:
    return ComprasSettings(
        PG_HOST=str(_cfg("COMPRAS_PG_HOST", _cfg("ERP_LANCAMENTO_PG_HOST", "localhost")) or "localhost"),
        PG_PORT=_cfg_int("COMPRAS_PG_PORT", _cfg_int("ERP_LANCAMENTO_PG_PORT", 5432)),
        PG_DATABASE=str(_cfg("COMPRAS_PG_DATABASE", _cfg("ERP_LANCAMENTO_PG_DB", "erp")) or "erp"),
        PG_USER=str(_cfg("COMPRAS_PG_USER", _cfg("ERP_LANCAMENTO_PG_USER", "postgres")) or "postgres"),
        PG_PASSWORD=str(_cfg("COMPRAS_PG_PASSWORD", _cfg("ERP_LANCAMENTO_PG_PASSWORD", "")) or ""),
        PG_COD_EMPRESA=_cfg_int("COMPRAS_PG_COD_EMPRESA", _cfg_int("ERP_ESTOQUE_PG_COMPANY", 1)),
        PG_CONNECT_TIMEOUT=_cfg_int("COMPRAS_PG_CONNECT_TIMEOUT", 8),
        APP_DB_SLOW_MS=_cfg_int("COMPRAS_DB_SLOW_MS", 900),
    )


def clear_settings_cache() -> None:
    get_settings.cache_clear()

