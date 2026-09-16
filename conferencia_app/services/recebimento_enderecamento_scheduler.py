"""Retry automático da sincronização de endereçamentos com o GRV.

Retoma tarefas em "Aguardando sincronização", inclusive após interrupções.
sincronizar() já é idempotente (trava por SKU + revalidações).
"""
from __future__ import annotations

import threading
from datetime import datetime, timedelta
from typing import Any

from flask import Flask
from sqlalchemy import or_

_LOCK = threading.Lock()
_STATE: dict[str, Any] = {
    "thread": None,
    "stop_event": None,
    "last_run": None,
    "last_status": "idle",
    "last_message": "",
}


def executar_ciclo(app: Flask) -> dict[str, Any]:
    """Retoma a fila, incluindo execuções interrompidas com trava expirada."""
    from ..extensions import db
    from ..models import RecebimentoEnderecamento as Tarefa
    from . import recebimento_enderecamento_service as svc

    # test_request_context: evento() lê a sessão (usuário fica "sistema").
    with app.test_request_context():
        limite_execucao = datetime.now() - timedelta(minutes=10)
        tarefas = (
            Tarefa.query
            .filter(Tarefa.status == "Aguardando sincronização")
            .filter(or_(Tarefa.executando_em.is_(None), Tarefa.executando_em < limite_execucao))
            .all()
        )
        ok, falhas, puladas = 0, 0, 0
        for tarefa in tarefas:
            tarefa_id = tarefa.id
            try:
                svc.sincronizar(tarefa)
                db.session.refresh(tarefa)
                if tarefa.status == "Concluído":
                    ok += 1
                else:
                    falhas += 1
            except ValueError:
                db.session.rollback()
                falhas += 1  # trava de SKU ocupada ou regra de negócio; fica pra próxima
            except Exception:
                db.session.rollback()
                falhas += 1
                app.logger.exception("Retry endereçamento: falha na tarefa %s", tarefa_id)

        _STATE.update(
            last_run=datetime.now(),
            last_status="ok",
            last_message=f"{ok} sincronizada(s), {falhas} ainda com falha, {puladas} em fluxo.",
        )
        return {"ok": True, "sincronizadas": ok, "falhas": falhas, "puladas": puladas}


def snapshot_status() -> dict[str, Any]:
    t = _STATE.get("thread")
    return {
        "rodando": bool(t and t.is_alive()),
        "last_run": _STATE["last_run"].isoformat() if _STATE.get("last_run") else None,
        "last_status": _STATE.get("last_status"),
        "last_message": _STATE.get("last_message"),
    }


def iniciar_scheduler(app: Flask) -> None:
    with _LOCK:
        t = _STATE.get("thread")
        if t and t.is_alive():
            return
        stop_event = threading.Event()

        def _loop():
            app.logger.info("Scheduler Endereçamento (retry GRV): iniciado.")
            if stop_event.wait(90):
                return
            while not stop_event.is_set():
                if app.config.get("ENDERECAMENTO_RETRY_AUTO_ENABLED", True):
                    try:
                        executar_ciclo(app)
                    except Exception:
                        app.logger.exception("Scheduler Endereçamento: exceção no ciclo")
                intervalo = int(app.config.get("ENDERECAMENTO_RETRY_POLL_INTERVAL_SECONDS", 600))
                if stop_event.wait(max(120, intervalo)):
                    break
            app.logger.info("Scheduler Endereçamento: parado.")

        thread = threading.Thread(target=_loop, daemon=True, name="recebimento-enderecamento-retry")
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
