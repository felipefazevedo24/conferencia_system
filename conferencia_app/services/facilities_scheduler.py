"""Scheduler Facilities: roda diariamente e envia alertas de vencimento NR-6.

- Verifica EPIs com proxima_troca_em dentro de 30 dias ou já vencidos.
- Agrupa por setor e envia um e-mail consolidado ao gestor do setor.
- Roda às 08:00 todos os dias (ou assim que possível após o boot).
- Pode ser desligado via config FACILITIES_ALERTAS_ENABLED = False.
"""
from __future__ import annotations

import threading
from datetime import date, datetime, timedelta
from typing import Any

from flask import Flask

_LOCK = threading.Lock()
_STATE: dict[str, Any] = {
    "thread": None,
    "stop_event": None,
    "last_run": None,
    "last_status": "idle",
    "last_message": "",
}


def _set_status(**kwargs):
    _STATE.update(kwargs)


def _segundos_ate_prox_execucao() -> int:
    """Retorna segundos até as 08:00 do dia atual (ou seguinte se já passou)."""
    agora = datetime.now()
    alvo  = agora.replace(hour=8, minute=0, second=0, microsecond=0)
    if alvo <= agora:
        alvo += timedelta(days=1)
    return max(30, int((alvo - agora).total_seconds()))


def executar_ciclo_alertas(app: Flask) -> dict:
    """Envia e-mails de alerta de vencimento NR-6 para gestores de setor."""
    from ..models import FacilitiesColaborador, FacilitiesEpiSolicitacao
    from ..extensions import db
    from .email_service import enviar_email_vencimento_epi

    with app.app_context():
        hoje = date.today()
        limite = hoje + timedelta(days=30)

        # Busca retiradas com proxima_troca_em nos próximos 30 dias ou já vencidos
        proximas = (
            FacilitiesEpiSolicitacao.query
            .filter(FacilitiesEpiSolicitacao.status == "retirado")
            .filter(FacilitiesEpiSolicitacao.proxima_troca_em.isnot(None))
            .filter(FacilitiesEpiSolicitacao.proxima_troca_em <= limite)
            .all()
        )

        if not proximas:
            _set_status(last_run=datetime.now(), last_status="ok",
                        last_message="Nenhum EPI vencendo em 30 dias.")
            return {"alertas": 0}

        # Agrupa itens por setor
        from collections import defaultdict
        por_setor: dict[str, list] = defaultdict(list)
        for s in proximas:
            setor = (s.colaborador.setor if s.colaborador and s.colaborador.setor else "Sem setor")
            dias  = (s.proxima_troca_em - hoje).days
            por_setor[setor].append({
                "colaborador": s.colaborador.nome if s.colaborador else "?",
                "item": s.nome_item,
                "proxima_troca": s.proxima_troca_em.strftime("%d/%m/%Y"),
                "dias_restantes": dias,
            })

        # Para cada setor, busca gestores com e-mail cadastrado
        enviados = 0
        try:
            url_admin = app.config.get("SERVER_NAME") or "http://localhost"
            url_admin = f"{url_admin}/facilities/admin"
        except Exception:
            url_admin = "/facilities/admin"

        gestores_por_setor: dict[str, list[FacilitiesColaborador]] = defaultdict(list)
        gestores = (
            FacilitiesColaborador.query
            .filter_by(nivel_acesso="gestor", ativo=True)
            .filter(FacilitiesColaborador.email.isnot(None))
            .filter(FacilitiesColaborador.email != "")
            .all()
        )
        # Gestores com setor definido → recebem alertas do seu setor
        # Gestores sem setor → recebem tudo
        for g in gestores:
            if g.setor:
                gestores_por_setor[g.setor].append(g)
            else:
                for s in por_setor:
                    gestores_por_setor[s].append(g)

        # Também envia para admins Facilities (sem setor específico)
        from ..models import PermissaoAcesso
        try:
            admins_perm = (
                PermissaoAcesso.query
                .filter_by(permission_key="PAGE_FACILITIES_ADMIN", allow=True)
                .all()
            )
            admin_usernames = {p.scope_id for p in admins_perm if p.scope_type == "USER"}
            from ..models import Usuario
            for user in Usuario.query.filter(Usuario.username.in_(admin_usernames)).all():
                if user.email:
                    admin_colab = FacilitiesColaborador(
                        nome=user.username, email=user.email, nivel_acesso="gestor"
                    )
                    for s in por_setor:
                        gestores_por_setor[s].append(admin_colab)
        except Exception:
            pass

        emails_ja_enviados: set = set()
        for setor, itens in por_setor.items():
            destinatarios = gestores_por_setor.get(setor, [])
            for g in destinatarios:
                if not g.email or g.email in emails_ja_enviados:
                    continue
                try:
                    enviar_email_vencimento_epi(
                        destinatario_email=g.email,
                        gestor_nome=g.nome,
                        itens=itens,
                        url_admin=url_admin,
                    )
                    emails_ja_enviados.add(g.email)
                    enviados += 1
                except Exception as exc:
                    app.logger.warning("Facilities Scheduler: falha email vencimento: %s", exc)

        _set_status(
            last_run=datetime.now(),
            last_status="ok",
            last_message=f"{len(proximas)} EPIs alertados, {enviados} e-mails enviados.",
        )
        return {"alertas": len(proximas), "emails": enviados}


def iniciar_scheduler(app: Flask) -> None:
    """Inicia o loop diário de alertas NR-6."""
    with _LOCK:
        t = _STATE.get("thread")
        if t and t.is_alive():
            return
        stop_event = threading.Event()

        def _loop():
            app.logger.info("Facilities Scheduler NR-6: iniciado.")
            # Aguarda até às 08:00 (ou 30s no mínimo para não bloquear o boot)
            wait = _segundos_ate_prox_execucao()
            app.logger.info("Facilities Scheduler NR-6: próxima execução em %ds.", wait)
            if stop_event.wait(wait):
                return
            while not stop_event.is_set():
                if app.config.get("FACILITIES_ALERTAS_ENABLED", True):
                    try:
                        executar_ciclo_alertas(app)
                    except Exception:
                        app.logger.exception("Facilities Scheduler NR-6: excecao no ciclo")
                # Aguarda 24h para o próximo ciclo
                if stop_event.wait(86400):
                    break
            app.logger.info("Facilities Scheduler NR-6: parado.")

        thread = threading.Thread(target=_loop, daemon=True, name="facilities-nr6-scheduler")
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
