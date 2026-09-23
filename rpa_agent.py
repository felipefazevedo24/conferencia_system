"""Agente Windows que busca execuções no Sync e controla a M83 localmente."""

from __future__ import annotations

import ctypes
import argparse
from contextlib import contextmanager
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
AGENT_VERSION = "1.1.0"
_INSTANCE_MUTEX_HANDLE = None


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


def _instance_env(name: str, instance: str, default: str = "") -> str:
    """Lê a configuração isolada do ambiente sem expor o segredo na tarefa."""
    if instance == "producao":
        value = _user_env(f"{name}_PRODUCAO")
        if value:
            return value
    return _user_env(name, default)


def _configure_logging(instance: str) -> None:
    log_dir = ROOT / "logs"
    log_dir.mkdir(exist_ok=True)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    LOGGER.setLevel(logging.INFO)
    stream = logging.StreamHandler()
    stream.setFormatter(formatter)
    log_name = "rpa_agent.log" if instance == "homologacao" else f"rpa_agent_{instance}.log"
    file_handler = RotatingFileHandler(
        log_dir / log_name, maxBytes=10_000_000, backupCount=5, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    LOGGER.handlers[:] = [stream, file_handler]


def _single_instance(instance: str) -> None:
    global _INSTANCE_MUTEX_HANDLE
    if os.name != "nt":
        raise RuntimeError("O agente RPA somente pode ser executado no Windows.")
    safe_instance = "".join(character for character in instance if character.isalnum())
    handle = ctypes.windll.kernel32.CreateMutexW(
        None, False, f"Local\\ColumbiaSyncRpaAgent_{safe_instance}"
    )
    if not handle or ctypes.windll.kernel32.GetLastError() == 183:
        raise RuntimeError("Já existe um agente RPA em execução nesta sessão.")
    _INSTANCE_MUTEX_HANDLE = handle


@contextmanager
def _exclusive_grv_execution():
    """Serializa o acesso ao CPS entre agentes de ambientes diferentes."""
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.CreateMutexW(None, False, "Local\\ColumbiaSyncRpaExecution")
    if not handle:
        raise RuntimeError("Não foi possível criar o bloqueio de execução do GRV.")
    wait_result = kernel32.WaitForSingleObject(handle, 0xFFFFFFFF)
    if wait_result not in (0, 0x80):
        kernel32.CloseHandle(handle)
        raise RuntimeError("Não foi possível obter exclusividade para executar o GRV.")
    try:
        yield
    finally:
        kernel32.ReleaseMutex(handle)
        kernel32.CloseHandle(handle)


class Agent:
    def __init__(self, instance: str) -> None:
        self.instance = instance
        default_url = (
            "https://sync.columbiamachine.com.br"
            if instance == "producao"
            else "https://homologacao.columbiamachine.com.br"
        )
        default_agent_id = (
            "columbia-grv-prod-01"
            if instance == "producao"
            else "columbia-grv-hml-01"
        )
        self.base_url = _instance_env(
            "RPA_AGENT_SERVER_URL", instance, default_url
        ).rstrip("/")
        self.agent_id = _instance_env("RPA_AGENT_ID", instance, default_agent_id)
        self.token = _instance_env("RPA_AGENT_TOKEN", instance)
        if not self.token:
            raise RuntimeError("RPA_AGENT_TOKEN não está configurado no usuário Windows.")
        self.poll_seconds = max(
            2.0, float(_instance_env("RPA_AGENT_POLL_SECONDS", instance, "5"))
        )
        self.session = requests.Session()
        self.heartbeat_session = requests.Session()
        headers = {
            "Authorization": f"Bearer {self.token}",
            "X-RPA-Agent-ID": self.agent_id,
            "User-Agent": f"Columbia-Sync-RPA-Agent/{AGENT_VERSION}",
        }
        self.session.headers.update(headers)
        self.heartbeat_session.headers.update(headers)
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
            "versao": AGENT_VERSION,
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

    def _post(
        self, path: str, payload: dict, timeout: int = 20, *, heartbeat: bool = False
    ) -> dict:
        session = self.heartbeat_session if heartbeat else self.session
        response = session.post(f"{self.base_url}{path}", json=payload, timeout=timeout)
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
        failed = False
        while not self.stop_event.wait(10):
            try:
                with self.state_lock:
                    payload = dict(self.state)
                self._post("/api/rpa/agent/heartbeat", payload, heartbeat=True)
                if failed:
                    LOGGER.info("Heartbeat restabelecido | ambiente=%s", self.instance)
                failed = False
            except Exception as exc:
                if not failed:
                    LOGGER.warning("Heartbeat interrompido; nova tentativa automatica: %s", exc)
                failed = True

    def run(self) -> None:
        LOGGER.info(
            "Agente iniciado | versao=%s | pid=%s | ambiente=%s | id=%s | servidor=%s | hostname=%s | usuario=%s",
            AGENT_VERSION,
            os.getpid(),
            self.instance,
            self.agent_id,
            self.base_url,
            socket.gethostname(),
            getpass.getuser(),
        )
        threading.Thread(target=self._heartbeat_loop, daemon=True).start()
        failures = 0
        backoff = (2, 5, 10, 30, 60)
        while not self.stop_event.is_set():
            try:
                current = self._diagnose()
                with self.state_lock:
                    self.state = current
                response = self._post("/api/rpa/agent/claim", current)
                job = response.get("job")
                if failures:
                    LOGGER.info("Conexao restabelecida | ambiente=%s", self.instance)
                failures = 0
                if not job:
                    self.stop_event.wait(self.poll_seconds)
                    continue
                self._execute(job)
            except KeyboardInterrupt:
                self.stop_event.set()
            except Exception as exc:
                LOGGER.exception("Falha no ciclo do agente: %s", exc)
                delay = backoff[min(failures, len(backoff) - 1)]
                failures += 1
                LOGGER.warning("Reconexao em %ss | ambiente=%s", delay, self.instance)
                self.stop_event.wait(delay)

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
        with _exclusive_grv_execution():
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
    parser = argparse.ArgumentParser(description="Agente Windows do RPA de agrupamento")
    parser.add_argument("--instance", default="homologacao")
    args = parser.parse_args()
    instance = str(args.instance).strip().lower() or "homologacao"
    _configure_logging(instance)
    try:
        _single_instance(instance)
        Agent(instance).run()
        return 0
    except Exception:
        LOGGER.exception("Agente RPA não pôde ser iniciado")
        return 1
    finally:
        LOGGER.info("Agente encerrado | ambiente=%s | pid=%s", instance, os.getpid())


if __name__ == "__main__":
    raise SystemExit(main())
