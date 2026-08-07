from __future__ import annotations

import json
import os
from datetime import datetime

from flask import current_app

from ..auth import is_admin_role
from ..extensions import db
from ..models import ActiveSession, Usuario


DEFAULT_MESSAGE = "O Columbia Sync está em manutenção no momento. Tente novamente em alguns minutos."


def _state_file() -> str:
    return os.path.join(current_app.instance_path, "maintenance_mode.json")


def _default_state() -> dict:
    return {
        "enabled": False,
        "message": DEFAULT_MESSAGE,
        "updated_at": None,
        "updated_by": None,
    }


def get_maintenance_state() -> dict:
    path = _state_file()
    if not os.path.isfile(path):
        return _default_state()
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh) or {}
    except Exception:
        return _default_state()
    state = _default_state()
    state["enabled"] = bool(data.get("enabled", False))
    state["message"] = str(data.get("message") or DEFAULT_MESSAGE).strip() or DEFAULT_MESSAGE
    state["updated_at"] = data.get("updated_at")
    state["updated_by"] = data.get("updated_by")
    return state


def set_maintenance_state(enabled: bool, updated_by: str, message: str | None = None) -> dict:
    state = {
        "enabled": bool(enabled),
        "message": str(message or DEFAULT_MESSAGE).strip() or DEFAULT_MESSAGE,
        "updated_at": datetime.now().isoformat(),
        "updated_by": str(updated_by or "admin").strip() or "admin",
    }
    os.makedirs(current_app.instance_path, exist_ok=True)
    with open(_state_file(), "w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False, indent=2)
    return state


def deactivate_non_admin_sessions(except_session_id: str | None = None) -> int:
    admins = {
        str(u.username or "").strip().casefold()
        for u in Usuario.query.all()
        if is_admin_role(getattr(u, "role", None))
    }
    if not admins:
        admins = set()

    encerradas = 0
    for sessao in ActiveSession.query.filter_by(is_active=True).all():
        if except_session_id and sessao.session_id == except_session_id:
            continue
        username = str(sessao.username or "").strip().casefold()
        if username in admins:
            continue
        sessao.is_active = False
        encerradas += 1
    if encerradas:
        db.session.commit()
    return encerradas