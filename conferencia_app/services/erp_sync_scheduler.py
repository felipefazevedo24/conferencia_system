"""Scheduler periodico que sincroniza estoque ERP -> WMS.

- Executa a cada ERP_SYNC_POLL_INTERVAL_SECONDS (default 600 = 10 min).
- Pode ser desligado via config ERP_SYNC_AUTO_ENABLED = False.
- Usa lock para nao rodar em paralelo.
"""
from __future__ import annotations

import threading
from datetime import datetime
from typing import Any

from flask import Flask

_LOCK = threading.Lock()
_STATE: dict[str, Any] = {
    "thread": None,
    "stop_event": None,
    "last_run": None,
    "last_status": "idle",
    "last_message": "",
    "last_resultado": None,
}


def _set_status(**kwargs):
    _STATE.update(kwargs)


def executar_ciclo(app: Flask) -> dict[str, Any]:
    """Um ciclo de sincronizacao. Retorna resumo."""
    from .erp_sync_service import ERPSyncService

    with app.app_context():
        try:
            resultado = ERPSyncService.executar_sync_completo()
            _set_status(
                last_run=datetime.now(),
                last_status="ok",
                last_message=(
                    f"{resultado.get('total_itens_erp', 0)} itens ERP, "
                    f"{resultado.get('enderecados', 0)} endereçados, "
                    f"{resultado.get('divergencias', 0)} divergências"
                ),
                last_resultado=resultado,
            )
            return {"ok": True, "resultado": resultado}
        except Exception as exc:
            # erro de rede/DNS: log curto; outros erros: completo
            import requests as _rq
            if isinstance(exc, (_rq.exceptions.ConnectionError, _rq.exceptions.Timeout)):
                app.logger.warning("Scheduler ERP Sync: sem conectividade com ERP (%s)", exc.__class__.__name__)
                msg = f"sem conectividade: {exc.__class__.__name__}"
            else:
                app.logger.error("Scheduler ERP Sync: falha: %s", exc)
                msg = f"Erro: {exc}"
            _set_status(
                last_run=datetime.now(),
                last_status="erro",
                last_message=msg,
            )
            return {"ok": False, "erro": str(exc)}


def snapshot_status() -> dict[str, Any]:
    """Snapshot do estado atual do scheduler."""
    t = _STATE.get("thread")
    return {
        "rodando": bool(t and t.is_alive()),
        "last_run": _STATE["last_run"].isoformat() if _STATE.get("last_run") else None,
        "last_status": _STATE.get("last_status"),
        "last_message": _STATE.get("last_message"),
        "last_resultado": _STATE.get("last_resultado"),
    }


def iniciar_scheduler(app: Flask) -> None:
    """Inicia o loop de polling se ainda nao estiver rodando."""
    with _LOCK:
        t = _STATE.get("thread")
        if t and t.is_alive():
            return
        stop_event = threading.Event()

        def _loop():
            app.logger.info("Scheduler ERP Sync: iniciado.")
            # Primeira execucao apos breve delay para nao bloquear boot
            if stop_event.wait(30):
                return
            while not stop_event.is_set():
                if app.config.get("ERP_SYNC_AUTO_ENABLED", True):
                    try:
                        executar_ciclo(app)
                    except Exception:
                        app.logger.exception("Scheduler ERP Sync: excecao no ciclo")
                intervalo = int(app.config.get("ERP_SYNC_POLL_INTERVAL_SECONDS", 600))
                if stop_event.wait(max(60, intervalo)):
                    break
            app.logger.info("Scheduler ERP Sync: parado.")

        thread = threading.Thread(target=_loop, daemon=True, name="erp-sync-scheduler")
        _STATE["stop_event"] = stop_event
        _STATE["thread"] = thread
        thread.start()


def parar_scheduler() -> None:
    with _LOCK:
        ev = _STATE.get("stop_event")
        if ev:
            ev.set()
        _STATE["thread"] = None
        _STATE["stop_event"] = None
