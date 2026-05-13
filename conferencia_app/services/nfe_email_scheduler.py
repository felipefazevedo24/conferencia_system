"""Scheduler simples que verifica periodicamente NFs emitidas e envia por e-mail.

Rule of thumb:
- Executa a cada NFE_EMAIL_POLL_INTERVAL_SECONDS.
- Considera apenas NFs emitidas >= 2026-05-13, mesmo se a config pedir antes.
- Nao reenvia o que ja foi Enviado ou ficou AguardandoManual.
- Notas sem e-mail geram status AguardandoManual (para o usuario completar via tela).
- Usa um lock para nao rodar em paralelo.
"""
from __future__ import annotations

import threading
from datetime import date, datetime
from typing import Any

from flask import Flask

_LOCK = threading.Lock()
_STATE: dict[str, Any] = {
    "thread": None,
    "stop_event": None,
    "last_run": None,
    "last_status": "idle",
    "last_message": "",
    "last_enviadas": 0,
    "last_pendentes": 0,
    "last_ignoradas": 0,
}


def _set_status(**kwargs):
    _STATE.update(kwargs)


def executar_ciclo(app: Flask) -> dict[str, Any]:
    """Um ciclo de verificacao + envio. Retorna resumo."""
    from .erp_nfe_emitidas_service import listar_nfes_emitidas_erp, normalizar_data_minima
    from .nfe_email_service import enviar_nfe_por_email
    from ..models import EmailNFEnviado

    with app.app_context():
        cutoff = (app.config.get("NFE_EMAIL_AUTO_DESDE") or "").strip()
        if not cutoff:
            cutoff = date.today().isoformat()
        cutoff = normalizar_data_minima(cutoff)

        try:
            documentos = listar_nfes_emitidas_erp(cutoff)
        except Exception as exc:
            import requests as _rq
            if isinstance(exc, (_rq.exceptions.ConnectionError, _rq.exceptions.Timeout)):
                app.logger.warning("Scheduler NF-e: sem conectividade com ERP bridge (%s)", exc.__class__.__name__)
                msg = f"ERP bridge offline: {exc.__class__.__name__}"
            else:
                app.logger.error("Scheduler NF-e: falha ERP bridge: %s", exc)
                msg = f"ERP bridge erro: {exc}"
            _set_status(last_run=datetime.now(), last_status="erro",
                        last_message=msg)
            return {"ok": False, "erro": str(exc)}

        # Dedupe: enviado nao reenvia; aguardando manual tambem nao fica criando
        # pendencias repetidas a cada ciclo. Falha/Pendente podem ser tentadas de novo.
        ja_processadas = {
            row.numero_nf
            for row in EmailNFEnviado.query.filter(
                EmailNFEnviado.status.in_(["Enviado", "AguardandoManual"])
            ).all()
            if row.numero_nf
        }

        enviadas = 0
        pendentes = 0
        ignoradas = 0

        for doc in documentos:
            numero = str(doc.get("numero") or "").strip()
            chave = str(doc.get("chave") or "").strip()
            emitido = str(doc.get("emitido_em") or "")[:10]
            if not numero or not chave or not doc.get("autorizada"):
                ignoradas += 1
                continue
            if emitido and emitido < cutoff:
                ignoradas += 1
                continue
            if numero in ja_processadas:
                ignoradas += 1
                continue

            try:
                resultado = enviar_nfe_por_email(
                    numero_nf=numero,
                    chave=chave,
                    origem="Auto",
                    disparado_por="scheduler",
                    envio_assincrono=False,
                )
                if resultado.get("sucesso"):
                    enviadas += 1
                else:
                    pendentes += 1
            except Exception as exc:
                app.logger.exception("Scheduler NF-e: erro enviando %s: %s", numero, exc)
                pendentes += 1

        _set_status(
            last_run=datetime.now(),
            last_status="ok",
            last_message=(
                f"{enviadas} enviada(s), {pendentes} pendente(s), {ignoradas} ignorada(s)."
            ),
            last_enviadas=enviadas,
            last_pendentes=pendentes,
            last_ignoradas=ignoradas,
        )
        return {
            "ok": True,
            "cutoff": cutoff,
            "total_erp": len(documentos),
            "enviadas": enviadas,
            "pendentes": pendentes,
            "ignoradas": ignoradas,
        }


def status_scheduler() -> dict[str, Any]:
    """Snapshot para a tela de configuracao."""
    t = _STATE.get("thread")
    return {
        "rodando": bool(t and t.is_alive()),
        "last_run": _STATE["last_run"].isoformat() if _STATE.get("last_run") else None,
        "last_status": _STATE.get("last_status"),
        "last_message": _STATE.get("last_message"),
        "last_enviadas": _STATE.get("last_enviadas", 0),
        "last_pendentes": _STATE.get("last_pendentes", 0),
        "last_ignoradas": _STATE.get("last_ignoradas", 0),
    }


def iniciar_scheduler(app: Flask) -> None:
    """Inicia o loop de polling se ainda nao estiver rodando."""
    with _LOCK:
        t = _STATE.get("thread")
        if t and t.is_alive():
            return
        stop_event = threading.Event()

        def _loop():
            app.logger.info("Scheduler NF-e: iniciado.")
            # Primeira execucao apos breve delay para nao bloquear boot
            if stop_event.wait(15):
                return
            while not stop_event.is_set():
                if app.config.get("NFE_EMAIL_AUTO_ENABLED", True):
                    try:
                        executar_ciclo(app)
                    except Exception:
                        app.logger.exception("Scheduler NF-e: excecao no ciclo")
                intervalo = int(app.config.get("NFE_EMAIL_POLL_INTERVAL_SECONDS", 300))
                if stop_event.wait(max(30, intervalo)):
                    break
            app.logger.info("Scheduler NF-e: parado.")

        thread = threading.Thread(target=_loop, daemon=True, name="nfe-email-scheduler")
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
