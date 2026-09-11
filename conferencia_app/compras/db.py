import logging
import threading
from contextlib import contextmanager
from time import perf_counter
from typing import Any, Iterable

import psycopg2
import requests
from psycopg2.extras import RealDictCursor

from . import queries
from .config import get_settings

_logger = logging.getLogger(__name__)
_PRODUCTION_READ_SLOTS = threading.BoundedSemaphore(4)

_QUERY_NAMES = {
    value: name
    for name, value in vars(queries).items()
    if name.startswith("SQL_") and isinstance(value, str)
}


@contextmanager
def get_connection(*, readonly: bool = False) -> Iterable[psycopg2.extensions.connection]:
    settings = get_settings()
    options = {"options": "-c default_transaction_read_only=on -c statement_timeout=15000"} if readonly else {}
    conn = psycopg2.connect(settings.dsn, cursor_factory=RealDictCursor, **options)
    conn.autocommit = not readonly
    try:
        if readonly:
            conn.set_session(readonly=True)
            with conn.cursor() as cursor:
                cursor.execute("SET TRANSACTION READ ONLY")
        yield conn
    finally:
        conn.close()


def _exec_fetch(sql: str, params: tuple | dict | None, *, one: bool):
    started = perf_counter()
    settings = get_settings()
    production = _QUERY_NAMES.get(sql, "").startswith("SQL_PRODUCAO_")
    if settings.API_URL and settings.USE_API_BRIDGE:
        try:
            return _exec_fetch_bridge(sql, params, one=one)
        except Exception as exc:
            if production:
                raise RuntimeError("Fonte de documentos indisponivel") from exc
            _logger.warning(
                "compras_bridge_unavailable: fallback para Postgres direto (%s)",
                type(exc).__name__,
            )
    from contextlib import nullcontext

    with _PRODUCTION_READ_SLOTS if production else nullcontext():
        connection = get_connection(readonly=True) if production else get_connection()
        with connection as conn, conn.cursor() as cur:
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


def _exec_fetch_bridge(sql: str, params: tuple | dict | None, *, one: bool):
    settings = get_settings()
    query_name = _QUERY_NAMES.get(sql)
    if not query_name:
        raise ValueError("Query de Compras nao registrada para uso via bridge")
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "ngrok-skip-browser-warning": "true",
        "User-Agent": "ColumbiaSync/Compras",
    }
    if settings.API_TOKEN:
        headers["Authorization"] = f"Bearer {settings.API_TOKEN}"
    response = requests.post(
        f"{settings.API_URL}/api/erp/compras/query",
        headers=headers,
        json={"query": query_name, "params": params or {}, "one": one},
        timeout=settings.API_TIMEOUT,
    )
    response.raise_for_status()
    payload = response.json()
    if query_name.startswith("SQL_PRODUCAO_") and payload.get("read_only") is not True:
        raise RuntimeError("Bridge sem contrato de leitura de documentos")
    if not payload.get("sucesso"):
        raise RuntimeError(str(payload.get("erro") or "Falha na bridge de Compras"))
    return payload.get("row") if one else (payload.get("rows") or [])


def fetch_all(sql: str, params: tuple | dict | None = None) -> list[dict[str, Any]]:
    return _exec_fetch(sql, params, one=False)


def fetch_one(sql: str, params: tuple | dict | None = None) -> dict[str, Any] | None:
    return _exec_fetch(sql, params, one=True)
