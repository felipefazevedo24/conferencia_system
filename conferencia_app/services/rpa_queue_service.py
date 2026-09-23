"""Fila servidor -> agente Windows para execução do RPA da M83."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import platform
import socket
import uuid
from datetime import datetime, timedelta
from typing import Any

from flask import current_app, request

from ..extensions import db
from ..models import RpaExecucao, RpaExecutor
from . import rpa_grv_service


FINAL_STATUSES = {"SUCCEEDED", "FAILED", "CANCELLED"}
ACTIVE_STATUSES = {"PENDING", "CLAIMED", "RUNNING"}


class RpaQueueError(RuntimeError):
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message


def agent_mode_enabled() -> bool:
    return bool(current_app.config.get("RPA_AGENT_ENABLED", False))


def _environment() -> str:
    return str(current_app.config.get("RPA_AGENT_ENVIRONMENT") or "homologacao").strip()


def _expected_agent_id() -> str:
    return str(current_app.config.get("RPA_AGENT_ID") or "").strip()


def _configured() -> bool:
    return agent_mode_enabled() and bool(
        str(current_app.config.get("RPA_AGENT_TOKEN_HASH") or "").strip()
    ) and bool(_expected_agent_id())


def authenticate_agent() -> str:
    if not _configured():
        raise RpaQueueError(503, "O executor RPA ainda não foi configurado neste ambiente.")
    authorization = str(request.headers.get("Authorization") or "")
    token = authorization[7:].strip() if authorization.lower().startswith("bearer ") else ""
    received_hash = hashlib.sha256(token.encode("utf-8")).hexdigest() if token else ""
    expected_hash = str(current_app.config.get("RPA_AGENT_TOKEN_HASH") or "").strip().lower()
    if not token or not hmac.compare_digest(received_hash, expected_hash):
        raise RpaQueueError(401, "Credencial do executor RPA inválida.")
    agent_id = str(request.headers.get("X-RPA-Agent-ID") or "").strip()
    if not hmac.compare_digest(agent_id, _expected_agent_id()):
        raise RpaQueueError(403, "Esta estação não está autorizada para este ambiente.")
    return agent_id


def _online(executor: RpaExecutor | None, now: datetime | None = None) -> bool:
    if executor is None:
        return False
    timeout = int(current_app.config.get("RPA_AGENT_HEARTBEAT_TIMEOUT_SECONDS") or 45)
    return executor.ultima_comunicacao >= (now or datetime.now()) - timedelta(seconds=timeout)


def status(usuario: str, pode_executar: bool) -> dict[str, Any]:
    configured = _configured()
    executor = db.session.get(RpaExecutor, _expected_agent_id()) if configured else None
    online = configured and _online(executor)
    grv_available = bool(online and executor and executor.grv_disponivel)
    return {
        "ok": True,
        "modo": "agente_windows",
        "configurado": configured,
        "executor_online": online,
        "grv_disponivel": grv_available,
        "rpa_habilitado": configured,
        "rpa_disponivel": bool(grv_available and pode_executar),
        "desktop_interativo": bool(online and executor and executor.desktop_interativo),
        "usuario": usuario,
        "hostname": socket.gethostname(),
        "sistema_operacional": platform.system(),
        "ambiente": _environment(),
        "executor": None if executor is None else {
            "id": executor.id,
            "hostname": executor.hostname,
            "usuario": executor.usuario_windows,
            "ultima_comunicacao": executor.ultima_comunicacao.isoformat(),
            "versao": executor.versao,
            "erro": executor.erro,
        },
        "permissoes": {
            "consulta": True,
            "simulacao": True,
            "execucao_grv": pode_executar,
        },
    }


def diagnostico_janela() -> dict[str, Any]:
    current = status("agente", True)
    executor = db.session.get(RpaExecutor, _expected_agent_id()) if current["configurado"] else None
    return {
        "ok": True,
        "modo": "agente_windows",
        "executor_online": current["executor_online"],
        "janela_grv_encontrada": current["grv_disponivel"],
        "janela_cps_encontrada": current["grv_disponivel"],
        "tela_m83_encontrada": current["grv_disponivel"],
        "hwnd": executor.janela_hwnd if executor else None,
        "titulo": executor.janela_titulo if executor else None,
        "erro": executor.erro if executor else None,
    }


def enqueue(data: dict[str, Any], usuario: str) -> dict[str, Any]:
    current = status(usuario, True)
    if not current["configurado"]:
        raise RpaQueueError(503, "O executor do GRV não está configurado em homologação.")
    if not current["executor_online"]:
        raise RpaQueueError(
            503, "Executor do GRV indisponível. Inicie o agente de automação na estação autorizada."
        )
    if not current["grv_disponivel"]:
        raise RpaQueueError(
            409,
            "O executor está conectado, mas o CPS/GRV não está disponível na estação de automação.",
        )
    if data.get("confirmed") is not True:
        raise RpaQueueError(400, "A confirmação explícita é obrigatória.")

    grouping = rpa_grv_service.montar_agrupamento(data, usuario)
    payload = grouping["payload"]
    description = str(data.get("description") or "").strip()[:80]
    if not description:
        raise RpaQueueError(422, "A descrição do agrupamento é obrigatória.")
    payload["descricao_agrupamento"] = description
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    fingerprint = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    recent = datetime.now() - timedelta(seconds=60)
    duplicate = (
        RpaExecucao.query.filter_by(ambiente=_environment(), fingerprint=fingerprint)
        .filter(RpaExecucao.criada_em >= recent)
        .filter(RpaExecucao.status.in_(tuple(ACTIVE_STATUSES | {"SUCCEEDED"})))
        .first()
    )
    if duplicate:
        raise RpaQueueError(
            409, f"Este agrupamento já foi solicitado recentemente. Execução: {duplicate.id}"
        )

    execution = RpaExecucao(
        id=uuid.uuid4().hex,
        ambiente=_environment(),
        usuario_solicitante=usuario,
        descricao=description,
        fingerprint=fingerprint,
        payload_json=canonical,
        status="PENDING",
        criada_em=datetime.now(),
        atualizada_em=datetime.now(),
    )
    db.session.add(execution)
    db.session.commit()
    current_app.logger.warning(
        "RPA enfileirado | execution_id=%s | ambiente=%s | usuario=%s | quantidade=%s | codigos=%s",
        execution.id,
        execution.ambiente,
        usuario,
        payload.get("quantidade_itens"),
        ",".join(payload.get("codigos_destacados_para_agrupamento") or []),
    )
    return {
        "ok": True,
        "queued": True,
        "execution_id": execution.id,
        "status": execution.status,
    }


def _serialize_execution(execution: RpaExecucao, include_payload: bool = False) -> dict[str, Any]:
    result = json.loads(execution.resultado_json) if execution.resultado_json else None
    data = {
        "ok": True,
        "execution_id": execution.id,
        "status": execution.status,
        "executor_id": execution.executor_id,
        "error": execution.erro,
        "result": result,
        "created_at": execution.criada_em.isoformat(),
        "started_at": execution.iniciada_em.isoformat() if execution.iniciada_em else None,
        "finished_at": execution.finalizada_em.isoformat() if execution.finalizada_em else None,
    }
    if include_payload:
        data["payload"] = json.loads(execution.payload_json)
        data["requested_by"] = execution.usuario_solicitante
    return data


def user_execution(execution_id: str, usuario: str, is_admin: bool = False) -> dict[str, Any]:
    execution = db.session.get(RpaExecucao, execution_id)
    if execution is None:
        raise RpaQueueError(404, "Execução RPA não encontrada.")
    if not is_admin and execution.usuario_solicitante.casefold() != usuario.casefold():
        raise RpaQueueError(403, "Esta execução pertence a outro usuário.")
    return _serialize_execution(execution)


def heartbeat(agent_id: str, data: dict[str, Any]) -> dict[str, Any]:
    now = datetime.now()
    executor = db.session.get(RpaExecutor, agent_id)
    if executor is None:
        executor = RpaExecutor(
            id=agent_id,
            ambiente=_environment(),
            hostname=str(data.get("hostname") or "")[:160],
            usuario_windows=str(data.get("usuario") or "")[:160],
        )
        db.session.add(executor)
    executor.ambiente = _environment()
    executor.hostname = str(data.get("hostname") or executor.hostname or "")[:160]
    executor.usuario_windows = str(data.get("usuario") or executor.usuario_windows or "")[:160]
    executor.versao = str(data.get("versao") or "")[:80]
    executor.desktop_interativo = data.get("desktop_interativo") is True
    executor.grv_disponivel = data.get("grv_disponivel") is True
    executor.janela_titulo = str(data.get("janela_titulo") or "")[:300] or None
    executor.janela_hwnd = str(data.get("janela_hwnd") or "")[:40] or None
    executor.erro = str(data.get("erro") or "")[:2000] or None
    executor.ultima_comunicacao = now
    executor.atualizado_em = now
    db.session.commit()
    return {"ok": True, "server_time": now.isoformat(), "agent_id": agent_id}


def claim(agent_id: str, heartbeat_data: dict[str, Any]) -> dict[str, Any]:
    heartbeat(agent_id, heartbeat_data)
    existing = (
        RpaExecucao.query.filter_by(
            ambiente=_environment(), executor_id=agent_id, status="CLAIMED"
        )
        .order_by(RpaExecucao.reivindicada_em.asc())
        .first()
    )
    if existing:
        return {"ok": True, "job": _serialize_execution(existing, include_payload=True)}

    candidate = (
        RpaExecucao.query.filter_by(ambiente=_environment(), status="PENDING")
        .order_by(RpaExecucao.criada_em.asc())
        .first()
    )
    if candidate is None:
        return {"ok": True, "job": None}
    now = datetime.now()
    updated = (
        RpaExecucao.query.filter_by(id=candidate.id, status="PENDING")
        .update(
            {
                "status": "CLAIMED",
                "executor_id": agent_id,
                "reivindicada_em": now,
                "atualizada_em": now,
                "tentativas": RpaExecucao.tentativas + 1,
            },
            synchronize_session=False,
        )
    )
    db.session.commit()
    if updated != 1:
        return {"ok": True, "job": None}
    claimed = db.session.get(RpaExecucao, candidate.id)
    current_app.logger.warning(
        "RPA reivindicado | execution_id=%s | executor=%s", claimed.id, agent_id
    )
    return {"ok": True, "job": _serialize_execution(claimed, include_payload=True)}


def mark_running(agent_id: str, execution_id: str) -> dict[str, Any]:
    execution = db.session.get(RpaExecucao, execution_id)
    if execution is None or execution.executor_id != agent_id:
        raise RpaQueueError(404, "Execução não atribuída a este agente.")
    if execution.status != "CLAIMED":
        raise RpaQueueError(409, f"Execução está no estado {execution.status}.")
    execution.status = "RUNNING"
    execution.iniciada_em = datetime.now()
    execution.atualizada_em = datetime.now()
    db.session.commit()
    current_app.logger.warning(
        "RPA iniciado | execution_id=%s | executor=%s", execution.id, agent_id
    )
    return _serialize_execution(execution)


def finish(agent_id: str, execution_id: str, data: dict[str, Any]) -> dict[str, Any]:
    execution = db.session.get(RpaExecucao, execution_id)
    if execution is None or execution.executor_id != agent_id:
        raise RpaQueueError(404, "Execução não atribuída a este agente.")
    if execution.status not in {"CLAIMED", "RUNNING"}:
        raise RpaQueueError(409, f"Execução está no estado {execution.status}.")
    success = data.get("success") is True and data.get("result", {}).get("gravado") is True
    execution.status = "SUCCEEDED" if success else "FAILED"
    execution.erro = None if success else str(data.get("error") or "Falha informada pelo executor.")[:4000]
    execution.resultado_json = json.dumps(data.get("result") or {}, ensure_ascii=False, default=str)
    execution.finalizada_em = datetime.now()
    execution.atualizada_em = datetime.now()
    db.session.commit()
    current_app.logger.warning(
        "RPA finalizado | execution_id=%s | executor=%s | status=%s | gravado=%s",
        execution.id,
        agent_id,
        execution.status,
        success,
    )
    return _serialize_execution(execution)
