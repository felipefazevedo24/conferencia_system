"""Scheduler periodico que lanca NFs automaticamente via ERP (tcompras).

- Executa a cada ERP_LANCAMENTO_POLL_INTERVAL_SECONDS (default 300 = 5 min).
- Pode ser desligado via config ERP_LANCAMENTO_AUTO_ENABLED = False.
- Estrutura espelhada do erp_sync_scheduler.
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
    from .erp_lancamento_service import executar_ciclo as _exec

    with app.app_context():
        try:
            resultado = _exec()
            status = "ok" if not resultado.get("erros") else "parcial"
            _set_status(
                last_run=datetime.now(),
                last_status=status,
                last_message=resultado.get("mensagem") or "",
                last_resultado=resultado,
            )
            return {"ok": True, "resultado": resultado}
        except Exception as exc:
            app.logger.exception("Scheduler ERP Lancamento: falha no ciclo")
            _set_status(
                last_run=datetime.now(),
                last_status="erro",
                last_message=f"Erro: {exc}",
            )
            return {"ok": False, "erro": str(exc)}


def snapshot_status() -> dict[str, Any]:
    t = _STATE.get("thread")
    return {
        "rodando": bool(t and t.is_alive()),
        "last_run": _STATE["last_run"].isoformat() if _STATE.get("last_run") else None,
        "last_status": _STATE.get("last_status"),
        "last_message": _STATE.get("last_message"),
        "last_resultado": _STATE.get("last_resultado"),
    }


def iniciar_scheduler(app: Flask) -> None:
    with _LOCK:
        t = _STATE.get("thread")
        if t and t.is_alive():
            return
        stop_event = threading.Event()

        def _loop():
            app.logger.info("Scheduler ERP Lancamento: iniciado.")
            # Pequeno delay no boot para nao concorrer com inicializacao.
            if stop_event.wait(45):
                return
            while not stop_event.is_set():
                if app.config.get("ERP_LANCAMENTO_AUTO_ENABLED", True):
                    try:
                        executar_ciclo(app)
                    except Exception:
                        app.logger.exception("Scheduler ERP Lancamento: excecao no ciclo")
                intervalo = int(app.config.get("ERP_LANCAMENTO_POLL_INTERVAL_SECONDS", 300))
                if stop_event.wait(max(60, intervalo)):
                    break
            app.logger.info("Scheduler ERP Lancamento: parado.")

        thread = threading.Thread(target=_loop, daemon=True, name="erp-lancamento-scheduler")
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
