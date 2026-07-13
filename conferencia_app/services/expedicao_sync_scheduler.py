"""Scheduler periodico da Conferencia de Expedicao (Faturamento + ST).

Solucao satelite: como o app apenas LE do ERP, se ninguem buscar os dados no
intervalo entre a solicitacao e o faturamento a informacao pode ser perdida.
Por isso a busca roda no SERVIDOR, continuamente, independente de haver algum
navegador aberto.

- Executa a cada EXPEDICAO_SYNC_POLL_INTERVAL_SECONDS (default 240 = 4 min).
- Sincroniza as ordens de faturamento (FAT) e, se habilitado, as de ST.
- Pode ser desligado via config EXPEDICAO_SYNC_AUTO_ENABLED = False.
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
    """Um ciclo de sincronizacao (FAT + ST). Retorna resumo."""
    from . import expedicao_fat_service as fat_svc

    resumo: dict[str, Any] = {}
    with app.app_context():
        # --- Faturamento (FAT) ---
        try:
            resumo["fat"] = fat_svc.sincronizar_ordens()
        except Exception as exc:  # noqa: BLE001
            app.logger.warning("Scheduler Expedicao FAT: falha: %s", exc)
            resumo["fat_erro"] = str(exc)

        # --- Servico de Terceiro (ST) ---
        if app.config.get("EXPEDICAO_SYNC_ST_ENABLED", True):
            try:
                from . import expedicao_st_service as st_svc
                resumo["st"] = st_svc.sincronizar_ordens()
            except Exception as exc:  # noqa: BLE001
                app.logger.warning("Scheduler Expedicao ST: falha: %s", exc)
                resumo["st_erro"] = str(exc)

    ok = "fat_erro" not in resumo and "st_erro" not in resumo
    _set_status(
        last_run=datetime.now(),
        last_status="ok" if ok else "parcial",
        last_message=_montar_mensagem(resumo),
        last_resultado=resumo,
    )
    return resumo


def _montar_mensagem(resumo: dict[str, Any]) -> str:
    partes = []
    fat = resumo.get("fat")
    if fat:
        partes.append(
            f"FAT: {fat.get('total_ordens', 0)} ordens "
            f"({fat.get('criadas', 0)} novas, {fat.get('faturadas', 0)} faturadas)"
        )
    if resumo.get("fat_erro"):
        partes.append(f"FAT erro: {resumo['fat_erro']}")
    st = resumo.get("st")
    if st:
        partes.append(
            f"ST: {st.get('total_ordens', 0)} ordens "
            f"({st.get('criadas', 0)} novas, {st.get('faturadas', 0)} faturadas)"
        )
    if resumo.get("st_erro"):
        partes.append(f"ST erro: {resumo['st_erro']}")
    return " | ".join(partes) or "sem alteracoes"


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
            app.logger.info("Scheduler Expedicao: iniciado.")
            # Primeira execucao apos breve delay para nao bloquear o boot.
            if stop_event.wait(20):
                return
            while not stop_event.is_set():
                if app.config.get("EXPEDICAO_SYNC_AUTO_ENABLED", True):
                    try:
                        executar_ciclo(app)
                    except Exception:
                        app.logger.exception("Scheduler Expedicao: excecao no ciclo")
                intervalo = int(app.config.get("EXPEDICAO_SYNC_POLL_INTERVAL_SECONDS", 240))
                if stop_event.wait(max(60, intervalo)):
                    break
            app.logger.info("Scheduler Expedicao: parado.")

        thread = threading.Thread(target=_loop, daemon=True, name="expedicao-sync-scheduler")
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
