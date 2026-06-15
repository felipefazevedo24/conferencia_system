import logging
from contextlib import contextmanager
from time import perf_counter
from typing import Any, Iterable

import psycopg2
from psycopg2.extras import RealDictCursor

from .config import get_settings

_logger = logging.getLogger(__name__)


@contextmanager
def get_connection() -> Iterable[psycopg2.extensions.connection]:
    settings = get_settings()
    conn = psycopg2.connect(settings.dsn, cursor_factory=RealDictCursor)
    conn.autocommit = True
    try:
        yield conn
    finally:
        conn.close()


def _exec_fetch(sql: str, params: tuple | dict | None, *, one: bool):
    started = perf_counter()
    settings = get_settings()
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params or {})
        result = cur.fetchone() if one else cur.fetchall()
    elapsed_ms = (perf_counter() - started) * 1000
    if elapsed_ms >= max(0, int(settings.APP_DB_SLOW_MS)):
        _logger.warning(
            "compras_slow_query elapsed_ms=%.2f one=%s sql=%s",
            elapsed_ms,
            one,
            " ".join(str(sql).split())[:220],
        )
    if one:
        return dict(result) if result else None
    return [dict(row) for row in result]


def fetch_all(sql: str, params: tuple | dict | None = None) -> list[dict[str, Any]]:
    return _exec_fetch(sql, params, one=False)


def fetch_one(sql: str, params: tuple | dict | None = None) -> dict[str, Any] | None:
    return _exec_fetch(sql, params, one=True)

