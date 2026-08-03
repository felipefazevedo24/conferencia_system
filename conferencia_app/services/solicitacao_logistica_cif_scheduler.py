"""Scheduler da automacao de Solicitacoes Logisticas por frete CIF.

Executa periodicamente as duas regras (Coleta a partir de OC CIF e Entrega a
partir de Romaneio CIF). Roda sem intervencao do usuario; o reprocessamento
manual usa as mesmas funcoes do service.
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


def executar_ciclo(app: Flask) -> dict[str, Any]:
    from .solicitacao_logistica_cif_service import executar_ciclo as _executar

    resultado = _executar(app)
    with _LOCK:
        _STATE.update(
            last_run=datetime.now(),
            last_status="ok",
            last_message="Ciclo CIF concluido.",
            last_resultado=resultado,
        )
    return resultado


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
            app.logger.info("Scheduler Solicitacoes CIF: iniciado.")
            if stop_event.wait(60):
                return
            while not stop_event.is_set():
                if app.config.get("SOLICITACAO_CIF_AUTO_ENABLED", True):
                    try:
                        executar_ciclo(app)
                    except Exception:
                        app.logger.exception("Scheduler Solicitacoes CIF: excecao no ciclo")
                intervalo = int(app.config.get("SOLICITACAO_CIF_POLL_INTERVAL_SECONDS", 1800))
                if stop_event.wait(max(300, intervalo)):
                    break
            app.logger.info("Scheduler Solicitacoes CIF: parado.")

        thread = threading.Thread(target=_loop, daemon=True, name="solicitacao-cif-scheduler")
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
