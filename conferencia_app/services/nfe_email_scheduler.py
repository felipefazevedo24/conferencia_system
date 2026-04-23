"""Scheduler simples que verifica periodicamente NFs emitidas e envia por e-mail.

Rule of thumb:
- Executa a cada NFE_EMAIL_POLL_INTERVAL_SECONDS.
- Considera apenas NFs emitidas >= NFE_EMAIL_AUTO_DESDE.
- Nao reenvia: usa EmailNFEnviado (qualquer status != AguardandoManual) como dedupe.
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
    from .consyste_service import listar_nfes_consyste_por_caixa
    from .nfe_email_service import enviar_nfe_por_email
    from ..models import EmailNFEnviado

    with app.app_context():
        cutoff = (app.config.get("NFE_EMAIL_AUTO_DESDE") or "").strip()
        if not cutoff:
            cutoff = date.today().isoformat()

        try:
            ok, status_code, payload = listar_nfes_consyste_por_caixa(
                caixa="emitidos",
                q=f"emitido_em:>={cutoff}",
                campos="id,chave,numero,emitido_em,dest_cnpj,dest_nome",
                timeout=30,
            )
        except Exception as exc:
            app.logger.error("Scheduler NF-e: falha Consyste: %s", exc)
            _set_status(last_run=datetime.now(), last_status="erro",
                        last_message=f"Consyste erro: {exc}")
            return {"ok": False, "erro": str(exc)}

        if not ok:
            _set_status(last_run=datetime.now(), last_status="erro",
                        last_message=f"Consyste status {status_code}")
            return {"ok": False, "erro": f"Consyste status {status_code}"}

        documentos = payload.get("documentos", []) if isinstance(payload, dict) else []

        # Dedupe: NFs que ja tem log (exceto AguardandoManual, que PODE ser reprocessada
        # se o usuario tiver preenchido o e-mail no cadastro/planilha e quiser reenvio).
        ja_processadas = {
            row.numero_nf
            for row in EmailNFEnviado.query.filter(
                EmailNFEnviado.status.in_(["Enviado", "Pendente", "Falha"])
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
            if not numero or not chave:
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
            "total_consyste": len(documentos),
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
