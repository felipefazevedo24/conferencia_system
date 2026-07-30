"""Scheduler de lembretes FOB para romaneios nao expedidos.

Dispara o lembrete 2 dias apos o aviso inicial e repete a cada 2 dias ate o
romaneio mudar para status "Expedido".
"""
from __future__ import annotations

import threading
from datetime import datetime, timedelta
from typing import Any

from flask import Flask

_LOCK = threading.Lock()
_STATE: dict[str, Any] = {
    "thread": None,
    "stop_event": None,
    "last_run": None,
    "last_status": "idle",
    "last_message": "",
    "last_enviados": 0,
    "last_ignorados": 0,
}


def _set_status(**kwargs):
    _STATE.update(kwargs)


def executar_ciclo(app: Flask) -> dict[str, Any]:
    """Percorre romaneios FOB pendentes e envia lembretes quando vencidos."""
    from ..models import EmailNFEnviado, ExpedicaoRomaneio, ExpedicaoRomaneioNF
    from .nfe_email_service import enviar_lembrete_coleta_fob

    with app.app_context():
        agora = datetime.now()
        janela = timedelta(days=2)

        romaneios = (
            ExpedicaoRomaneio.query
            .filter(ExpedicaoRomaneio.tipo_frete == "FOB")
            .filter(ExpedicaoRomaneio.status != "Expedido")
            .all()
        )

        enviados = 0
        ignorados = 0

        for rom in romaneios:
            for nf in (rom.nfs or []):
                numero_nf = str(nf.numero_nf or "").strip()
                if not numero_nf:
                    ignorados += 1
                    continue

                inicial_enviado = (
                    EmailNFEnviado.query
                    .filter_by(numero_nf=numero_nf, origem="RomaneioFOB", status="Enviado")
                    .order_by(EmailNFEnviado.criado_em.desc())
                    .first()
                )
                if not inicial_enviado:
                    ignorados += 1
                    continue

                ultimo_evento = (
                    EmailNFEnviado.query
                    .filter(
                        EmailNFEnviado.numero_nf == numero_nf,
                        EmailNFEnviado.origem.in_(["RomaneioFOB", "RomaneioFOB-Lembrete"]),
                    )
                    .order_by(EmailNFEnviado.criado_em.desc())
                    .first()
                )
                if ultimo_evento:
                    data_evento = ultimo_evento.enviado_em or ultimo_evento.criado_em
                    if data_evento and (agora - data_evento) < janela:
                        ignorados += 1
                        continue

                try:
                    enviar_lembrete_coleta_fob(
                        numero_nf=numero_nf,
                        nome_cliente=nf.cliente or rom.cliente or "",
                        disparado_por="scheduler",
                        origem="RomaneioFOB-Lembrete",
                        envio_assincrono=True,
                    )
                    enviados += 1
                except Exception:
                    app.logger.exception(
                        "Romaneio FOB reminder: falha ao enviar lembrete para NF %s (romaneio %s).",
                        numero_nf,
                        rom.numero_romaneio,
                    )

        _set_status(
            last_run=datetime.now(),
            last_status="ok",
            last_message=f"{enviados} lembrete(s) enviado(s), {ignorados} ignorado(s).",
            last_enviados=enviados,
            last_ignorados=ignorados,
        )
        return {
            "ok": True,
            "enviados": enviados,
            "ignorados": ignorados,
            "total_romaneios": len(romaneios),
        }


def snapshot_status() -> dict[str, Any]:
    t = _STATE.get("thread")
    return {
        "rodando": bool(t and t.is_alive()),
        "last_run": _STATE["last_run"].isoformat() if _STATE.get("last_run") else None,
        "last_status": _STATE.get("last_status"),
        "last_message": _STATE.get("last_message"),
        "last_enviados": _STATE.get("last_enviados", 0),
        "last_ignorados": _STATE.get("last_ignorados", 0),
    }


def iniciar_scheduler(app: Flask) -> None:
    with _LOCK:
        t = _STATE.get("thread")
        if t and t.is_alive():
            return
        stop_event = threading.Event()

        def _loop():
            app.logger.info("Scheduler Romaneio FOB: iniciado.")
            if stop_event.wait(60):
                return
            while not stop_event.is_set():
                if app.config.get("ROMANEIO_FOB_REMINDER_ENABLED", True):
                    try:
                        executar_ciclo(app)
                    except Exception:
                        app.logger.exception("Scheduler Romaneio FOB: excecao no ciclo")
                intervalo = int(app.config.get("ROMANEIO_FOB_REMINDER_POLL_INTERVAL_SECONDS", 3600))
                if stop_event.wait(max(300, intervalo)):
                    break
            app.logger.info("Scheduler Romaneio FOB: parado.")

        thread = threading.Thread(target=_loop, daemon=True, name="romaneio-fob-reminder-scheduler")
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