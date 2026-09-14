import logging
import threading
from contextlib import contextmanager, nullcontext
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


class ProducaoSourceError(RuntimeError):
    _MESSAGES = {
        "bridge_catalog_outdated": "Bridge ERP desatualizada: consulta de Producao nao cadastrada.",
        "bridge_readonly_required": "A Bridge ERP nao confirmou a consulta somente leitura.",
        "bridge_access_denied": "A Bridge ERP recusou o acesso de Producao.",
        "bridge_unavailable": "Nao foi possivel consultar a Bridge ERP.",
        "grv_unavailable": "Nao foi possivel consultar o PostgreSQL do GRV.",
    }

    def __init__(self, code: str):
        self.code = code if code in self._MESSAGES else "bridge_unavailable"
        super().__init__(self._MESSAGES[self.code])


@contextmanager
def get_connection(*, readonly: bool = False) -> Iterable[psycopg2.extensions.connection]:
    settings = get_settings()
    options = {"options": "-c default_transaction_read_only=on -c statement_timeout=15000"} if readonly else {}
    conn = psycopg2.connect(settings.dsn, cursor_factory=RealDictCursor, **options)
    conn.autocommit = not readonly
    try:
        if readonly:
            conn.set_session(readonly=True)
        yield conn
    finally:
        conn.close()


def _exec_fetch(sql: str, params: tuple | dict | None, *, one: bool):
    started = perf_counter()
    settings = get_settings()
    query_name = _QUERY_NAMES.get(sql, "")
    production = query_name.startswith("SQL_PRODUCAO_")
    if settings.API_URL and settings.USE_API_BRIDGE:
        try:
            return _exec_fetch_bridge(sql, params, one=one)
        except ProducaoSourceError:
            raise
        except Exception as exc:
            if production:
                _logger.warning("producao_bridge_unavailable query=%s error_type=%s", query_name, type(exc).__name__)
                raise ProducaoSourceError("bridge_unavailable") from None
            _logger.warning(
                "compras_bridge_unavailable: fallback para Postgres direto (%s)",
                type(exc).__name__,
            )
    try:
        with _PRODUCTION_READ_SLOTS if production else nullcontext():
            with get_connection(readonly=production) as conn, conn.cursor() as cur:
                cur.execute(sql, params or {})
                result = cur.fetchone() if one else cur.fetchall()
    except psycopg2.Error:
        if production:
            raise ProducaoSourceError("grv_unavailable") from None
        raise
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
    if query_name.startswith("SQL_PRODUCAO_"):
        if response.status_code in {401, 403}:
            raise ProducaoSourceError("bridge_access_denied")
        try:
            payload = response.json()
        except ValueError:
            raise ProducaoSourceError("bridge_unavailable") from None
        if response.status_code == 400 and payload.get("erro") == "query_nao_permitida":
            raise ProducaoSourceError("bridge_catalog_outdated")
        if response.status_code != 200 or payload.get("sucesso") is not True:
            raise ProducaoSourceError("bridge_unavailable")
        if payload.get("read_only") is not True:
            raise ProducaoSourceError("bridge_readonly_required")
    else:
        response.raise_for_status()
        payload = response.json()
    if not payload.get("sucesso"):
        raise RuntimeError(str(payload.get("erro") or "Falha na bridge de Compras"))
    return payload.get("row") if one else (payload.get("rows") or [])


def fetch_all(sql: str, params: tuple | dict | None = None) -> list[dict[str, Any]]:
    return _exec_fetch(sql, params, one=False)


def fetch_one(sql: str, params: tuple | dict | None = None) -> dict[str, Any] | None:
    return _exec_fetch(sql, params, one=True)
