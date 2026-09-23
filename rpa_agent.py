"""Agente Windows que busca execuções no Sync e controla a M83 localmente."""

from __future__ import annotations

import ctypes
import getpass
import logging
import os
import socket
import sys
import threading
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

import requests
from flask import Flask

from conferencia_app.services import rpa_grv_service


ROOT = Path(__file__).resolve().parent
LOGGER = logging.getLogger("sync_rpa_agent")


def _user_env(name: str, default: str = "") -> str:
    value = str(os.environ.get(name) or "").strip()
    if value:
        return value
    if os.name == "nt":
        try:
            import winreg

            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
                value, _ = winreg.QueryValueEx(key, name)
                return str(value or "").strip() or default
        except (FileNotFoundError, OSError):
            pass
    return default


def _configure_logging() -> None:
    log_dir = ROOT / "logs"
    log_dir.mkdir(exist_ok=True)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    LOGGER.setLevel(logging.INFO)
    stream = logging.StreamHandler()
    stream.setFormatter(formatter)
    file_handler = RotatingFileHandler(
        log_dir / "rpa_agent.log", maxBytes=2_000_000, backupCount=5, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    LOGGER.handlers[:] = [stream, file_handler]


def _single_instance() -> None:
    if os.name != "nt":
        raise RuntimeError("O agente RPA somente pode ser executado no Windows.")
    handle = ctypes.windll.kernel32.CreateMutexW(None, False, "Local\\ColumbiaSyncRpaAgent")
    if not handle or ctypes.windll.kernel32.GetLastError() == 183:
        raise RuntimeError("Já existe um agente RPA em execução nesta sessão.")


class Agent:
    def __init__(self) -> None:
        self.base_url = _user_env(
            "RPA_AGENT_SERVER_URL", "https://homologacao.columbiamachine.com.br"
        ).rstrip("/")
        self.agent_id = _user_env("RPA_AGENT_ID", "columbia-grv-hml-01")
        self.token = _user_env("RPA_AGENT_TOKEN")
        if not self.token:
            raise RuntimeError("RPA_AGENT_TOKEN não está configurado no usuário Windows.")
        self.poll_seconds = max(2.0, float(_user_env("RPA_AGENT_POLL_SECONDS", "5")))
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {self.token}",
                "X-RPA-Agent-ID": self.agent_id,
                "User-Agent": "Columbia-Sync-RPA-Agent/1.0",
            }
        )
        self.app = Flask("sync_rpa_agent")
        self.app.config.update(
            GRV_WEB_RPA_ENABLED=True,
            GRV_RPA_BACKEND_PATH=os.environ.get("GRV_RPA_BACKEND_PATH", ""),
            GRV_RPA_WINDOW_TITLE=os.environ.get(
                "GRV_RPA_WINDOW_TITLE", r".*CPS.*COLUMBIA.*"
            ),
            GRV_RPA_DELAY=float(os.environ.get("GRV_RPA_DELAY", "0.55")),
        )
        self.state_lock = threading.Lock()
        self.state = self._diagnose()
        self.stop_event = threading.Event()

    def _diagnose(self) -> dict:
        base = {
            "hostname": socket.gethostname(),
            "usuario": getpass.getuser(),
            "versao": "1.0",
            "desktop_interativo": rpa_grv_service.desktop_permite_rpa(),
            "grv_disponivel": False,
            "janela_titulo": None,
            "janela_hwnd": None,
            "erro": None,
        }
        try:
            with self.app.app_context():
                diagnostic = rpa_grv_service.diagnosticar_janelas()
            base["grv_disponivel"] = diagnostic.get("janela_grv_encontrada") is True
            base["janela_hwnd"] = diagnostic.get("hwnd")
            matching = next(
                (item for item in diagnostic.get("janelas", []) if item.get("tela_agrupamento")),
                None,
            )
            base["janela_titulo"] = matching.get("titulo") if matching else None
            if not base["grv_disponivel"]:
                base["erro"] = "CPS aberto, mas a tela M83 não foi localizada."
        except Exception as exc:
            base["erro"] = str(exc)
        return base

    def _post(self, path: str, payload: dict, timeout: int = 20) -> dict:
        response = self.session.post(f"{self.base_url}{path}", json=payload, timeout=timeout)
        try:
            data = response.json()
        except ValueError:
            data = {"error": response.text[:500]}
        if not response.ok:
            raise RuntimeError(
                f"Sync respondeu HTTP {response.status_code}: {data.get('error') or data}"
            )
        return data

    def _heartbeat_loop(self) -> None:
        while not self.stop_event.wait(10):
            try:
                with self.state_lock:
                    payload = dict(self.state)
                self._post("/api/rpa/agent/heartbeat", payload)
            except Exception as exc:
                LOGGER.warning("Heartbeat falhou: %s", exc)

    def run(self) -> None:
        LOGGER.info(
            "Agente iniciado | id=%s | servidor=%s | hostname=%s | usuario=%s",
            self.agent_id,
            self.base_url,
            socket.gethostname(),
            getpass.getuser(),
        )
        threading.Thread(target=self._heartbeat_loop, daemon=True).start()
        while not self.stop_event.is_set():
            try:
                current = self._diagnose()
                with self.state_lock:
                    self.state = current
                response = self._post("/api/rpa/agent/claim", current)
                job = response.get("job")
                if not job:
                    self.stop_event.wait(self.poll_seconds)
                    continue
                self._execute(job)
            except KeyboardInterrupt:
                self.stop_event.set()
            except Exception as exc:
                LOGGER.exception("Falha no ciclo do agente: %s", exc)
                self.stop_event.wait(self.poll_seconds)

    def _execute(self, job: dict) -> None:
        execution_id = str(job.get("execution_id") or "")
        payload = job.get("payload")
        requested_by = str(job.get("requested_by") or "usuario_sync")
        if not execution_id or not isinstance(payload, dict):
            raise RuntimeError("Trabalho recebido sem execution_id ou payload.")
        LOGGER.warning(
            "Execução recebida | execution_id=%s | usuario=%s | codigos=%s",
            execution_id,
            requested_by,
            ",".join(payload.get("codigos_destacados_para_agrupamento") or []),
        )
        self._post(f"/api/rpa/agent/executions/{execution_id}/running", {})
        try:
            with self.app.app_context():
                execution = rpa_grv_service.executar_payload_validado(
                    payload, requested_by, execution_id=execution_id
                )
            result = execution.get("result") or {}
            self._post(
                f"/api/rpa/agent/executions/{execution_id}/result",
                {"success": result.get("gravado") is True, "result": result},
            )
            LOGGER.warning(
                "Execução concluída | execution_id=%s | gravado=%s",
                execution_id,
                result.get("gravado"),
            )
        except Exception as exc:
            LOGGER.exception("Execução falhou | execution_id=%s", execution_id)
            try:
                self._post(
                    f"/api/rpa/agent/executions/{execution_id}/result",
                    {"success": False, "error": str(exc), "result": {}},
                )
            except Exception:
                LOGGER.exception("Falha ao devolver erro | execution_id=%s", execution_id)
            raise
        finally:
            refreshed = self._diagnose()
            with self.state_lock:
                self.state = refreshed


def main() -> int:
    _configure_logging()
    try:
        _single_instance()
        Agent().run()
        return 0
    except Exception:
        LOGGER.exception("Agente RPA não pôde ser iniciado")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
