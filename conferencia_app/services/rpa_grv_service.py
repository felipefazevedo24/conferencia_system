"""Integração do Sync com o RPA existente da tela M83 do GRV."""

from __future__ import annotations

import ctypes
import hashlib
import importlib.machinery
import importlib.util
import json
import os
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from flask import current_app

from . import rpa_agrupamento_service
from .rpa_grv_uia_automator import criar_automator_verificado


class RpaApiError(RuntimeError):
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message


_execution_lock = threading.Lock()
_last_execution: tuple[str, float] | None = None
_backend_lock = threading.Lock()
_backend_cache: tuple[Path, int, Any] | None = None


def obter_desktop_windows_atual() -> str:
    if os.name != "nt":
        return "nao_windows"
    try:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        kernel32.GetCurrentThreadId.restype = ctypes.c_ulong
        user32.GetThreadDesktop.argtypes = [ctypes.c_ulong]
        user32.GetThreadDesktop.restype = ctypes.c_void_p
        desktop = user32.GetThreadDesktop(kernel32.GetCurrentThreadId())
        if not desktop:
            return "desconhecido"
        size = ctypes.c_ulong()
        user32.GetUserObjectInformationW(desktop, 2, None, 0, ctypes.byref(size))
        if not size.value:
            return "desconhecido"
        buffer = ctypes.create_unicode_buffer(max(1, size.value // ctypes.sizeof(ctypes.c_wchar)))
        if not user32.GetUserObjectInformationW(
            desktop, 2, buffer, size.value, ctypes.byref(size)
        ):
            return "desconhecido"
        return buffer.value or "desconhecido"
    except (AttributeError, OSError):
        return "desconhecido"


def desktop_permite_rpa() -> bool:
    return os.name == "nt" and obter_desktop_windows_atual().casefold() == "default"


def _backend_path() -> Path:
    configured = str(current_app.config.get("GRV_RPA_BACKEND_PATH") or "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    projects_root = Path(__file__).resolve().parents[4]
    return projects_root / "RPA - Consulta no Banco de Dados - Agrupamento" / "RPA - Teste 02" / "Teste_RPA 01.PY"


def _load_backend() -> Any:
    global _backend_cache
    path = _backend_path()
    if not path.is_file():
        raise RpaApiError(503, f"Backend do RPA não encontrado em: {path}")
    mtime = path.stat().st_mtime_ns
    if _backend_cache and _backend_cache[:2] == (path, mtime):
        return _backend_cache[2]
    with _backend_lock:
        if _backend_cache and _backend_cache[:2] == (path, mtime):
            return _backend_cache[2]
        module_name = "columbia_grv_rpa_backend"
        loader = importlib.machinery.SourceFileLoader(module_name, str(path))
        spec = importlib.util.spec_from_loader(module_name, loader)
        if spec is None or spec.loader is None:
            raise RpaApiError(503, "Não foi possível carregar o backend existente do RPA.")
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        try:
            spec.loader.exec_module(module)
        except SystemExit as exc:
            raise RpaApiError(
                503, "As dependências do backend original do RPA não estão instaladas."
            ) from exc
        _backend_cache = (path, mtime, module)
        return module


def _enabled() -> bool:
    return bool(current_app.config.get("GRV_WEB_RPA_ENABLED", False))


def _window_title() -> str:
    return str(current_app.config.get("GRV_RPA_WINDOW_TITLE") or r".*CPS.*COLUMBIA.*")


def _delay() -> float:
    return max(0.1, float(current_app.config.get("GRV_RPA_DELAY", 0.55)))


def status(usuario: str, pode_executar: bool) -> dict[str, Any]:
    desktop = obter_desktop_windows_atual()
    backend_path = _backend_path()
    enabled = _enabled()
    interactive = desktop.casefold() == "default" and os.name == "nt"
    available = enabled and interactive and backend_path.is_file() and pode_executar
    return {
        "ok": True,
        "rpa_habilitado": enabled,
        "rpa_disponivel": available,
        "desktop_interativo": interactive,
        "desktop_windows": desktop,
        "usuario": usuario,
        "titulo_janela_esperado": _window_title(),
        "backend_rpa_disponivel": backend_path.is_file(),
        "server_pid": os.getpid(),
        "server_entrypoint": str(Path(sys.argv[0]).resolve()),
        "server_python": sys.executable,
        "server_host": os.environ.get("APP_HOST", ""),
        "server_port": os.environ.get("APP_PORT", ""),
        "server_launcher": os.environ.get("SYNC_LAUNCHER", ""),
        "habilitacao_cli": "--habilitar-rpa" in sys.argv,
        "habilitacao_env": os.environ.get("GRV_WEB_RPA_ENABLED", ""),
        "permissoes": {
            "consulta": True,
            "simulacao": True,
            "execucao_grv": pode_executar,
        },
    }


def _normalizar(value: Any) -> str:
    return str(value or "").strip()


def montar_agrupamento(data: dict[str, Any], usuario: str) -> dict[str, Any]:
    codes = data.get("codes")
    if not isinstance(codes, list) or not codes or len(codes) > 1000:
        raise RpaApiError(400, "O campo 'codes' deve ser uma lista de processos.")
    normalized_codes = {_normalizar(code) for code in codes if _normalizar(code)}
    if len(normalized_codes) < 2:
        raise RpaApiError(422, "Selecione pelo menos dois códigos de processo elegíveis.")

    search = _normalizar(data.get("busca"))[:80]
    rows = rpa_agrupamento_service.consultar(search, 1000)
    selected = [row for row in rows if row["codigo_processo"] in normalized_codes]
    found = {row["codigo_processo"] for row in selected}
    if found != normalized_codes:
        missing = ", ".join(sorted(normalized_codes - found))
        raise RpaApiError(409, f"A seleção mudou. Processos não encontrados: {missing}")

    material_key = _normalizar(data.get("materialKey"))
    if material_key and any(_normalizar(row.get("produto_chave")) != material_key for row in selected):
        raise RpaApiError(422, "Os processos não pertencem à chapa selecionada.")
    try:
        payload = rpa_agrupamento_service.montar_payload(selected)
    except ValueError as exc:
        raise RpaApiError(422, str(exc)) from exc

    description = _normalizar(data.get("description"))[:80]
    if description:
        payload["descricao_agrupamento"] = description
    current_app.logger.info(
        "Agrupamento validado | usuario=%s | quantidade=%s | codigos=%s",
        usuario,
        payload["quantidade_itens"],
        ",".join(payload["codigos_destacados_para_agrupamento"]),
    )
    return {"ok": True, "valid": True, "payload": payload}


def _m83_visible(windows: list[dict[str, Any]]) -> bool:
    return any(
        window.get("visivel")
        and window.get("tela_agrupamento")
        and int(window.get("largura") or 0) >= 600
        and int(window.get("altura") or 0) >= 400
        for window in windows
    )


def diagnosticar_janelas() -> dict[str, Any]:
    if not _enabled():
        raise RpaApiError(403, "A execução do RPA está desativada neste servidor.")
    if not desktop_permite_rpa():
        raise RpaApiError(
            409,
            "O módulo de automação precisa ser iniciado na mesma sessão do Windows em que o CPS/M83 está aberto.",
        )
    backend = _load_backend()
    automator = backend.GRVRpaAutomator(
        window_title=_window_title(), executable=None, delay=_delay(), gravar_sem_confirmar=True
    )
    windows = automator.listar_janelas()
    hwnd = automator.encontrar_janela(_window_title())
    m83_found = _m83_visible(windows)
    return {
        "ok": True,
        "janela_grv_encontrada": bool(hwnd) and m83_found,
        "janela_cps_encontrada": bool(hwnd),
        "tela_m83_encontrada": m83_found,
        "hwnd": hwnd,
        "janelas": [
            window
            for window in windows
            if window.get("visivel") or window.get("processo") == "cps.exe"
        ],
    }


def executar_agrupamento(data: dict[str, Any], usuario: str) -> dict[str, Any]:
    if not _enabled():
        raise RpaApiError(403, "A execução do RPA está desativada neste servidor.")
    if not desktop_permite_rpa():
        raise RpaApiError(
            409,
            "O módulo de automação precisa ser iniciado na mesma sessão do Windows em que o CPS/M83 está aberto.",
        )
    if data.get("confirmed") is not True:
        raise RpaApiError(400, "A confirmação explícita é obrigatória.")
    description = _normalizar(data.get("description"))[:80]
    if not description:
        raise RpaApiError(422, "A descrição do agrupamento é obrigatória para executar o RPA.")

    grouping = montar_agrupamento(data, usuario)
    payload = grouping["payload"]
    payload["descricao_agrupamento"] = description
    return executar_payload_validado(payload, usuario)


def executar_payload_validado(
    payload: dict[str, Any], usuario: str, execution_id: str | None = None
) -> dict[str, Any]:
    """Executa somente payload já validado e assinado pelo backend Sync."""
    global _last_execution
    if not _enabled():
        raise RpaApiError(403, "A execução do RPA está desativada neste executor.")
    if not desktop_permite_rpa():
        raise RpaApiError(
            409,
            "O módulo de automação precisa estar no desktop Windows interativo.",
        )
    if not isinstance(payload, dict):
        raise RpaApiError(400, "Payload do agrupamento inválido.")
    description = _normalizar(payload.get("descricao_agrupamento"))[:80]
    codes = payload.get("codigos_destacados_para_agrupamento")
    if not description or not isinstance(codes, list) or len(codes) < 2:
        raise RpaApiError(422, "Payload do agrupamento incompleto para execução.")
    normalized_codes = [_normalizar(code) for code in codes if _normalizar(code)]
    if len(normalized_codes) != len(codes) or len(set(normalized_codes)) != len(codes):
        raise RpaApiError(422, "Códigos de processo inválidos no payload do executor.")
    payload = dict(payload)
    payload["descricao_agrupamento"] = description
    payload["codigos_destacados_para_agrupamento"] = normalized_codes
    fingerprint = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()
    execution_id = execution_id or uuid.uuid4().hex

    with _execution_lock:
        now = time.monotonic()
        if _last_execution and _last_execution[0] == fingerprint and now - _last_execution[1] < 60:
            raise RpaApiError(409, "Este agrupamento já foi solicitado recentemente. Verifique o GRV.")
        backend = _load_backend()
        current_app.logger.warning(
            "Execução RPA autorizada | id=%s | usuario=%s | codigos=%s | descricao=%s | janela=%s",
            execution_id,
            usuario,
            ",".join(payload["codigos_destacados_para_agrupamento"]),
            description,
            _window_title(),
        )
        try:
            automator = criar_automator_verificado(backend.GRVRpaAutomator)(
                window_title=_window_title(),
                executable=None,
                delay=_delay(),
                gravar_sem_confirmar=True,
            )
            windows = automator.listar_janelas()
            if not _m83_visible(windows):
                raise RpaApiError(
                    409,
                    "Abra no CPS a tela Produção > Serviços > Apontamento Agrupado (M83) e tente novamente.",
                )
            m83_window = next(
                window
                for window in windows
                if window.get("visivel")
                and window.get("tela_agrupamento")
                and int(window.get("largura") or 0) >= 600
                and int(window.get("altura") or 0) >= 400
            )
            current_app.logger.warning(
                "Janela M83 localizada | id=%s | hwnd=%s | titulo=%s",
                execution_id,
                m83_window.get("hwnd"),
                m83_window.get("titulo"),
            )
            execution = automator.executar_apontamento_agrupamento(payload, dry_run=False)
        except RpaApiError:
            raise
        except RuntimeError as exc:
            current_app.logger.exception("Falha durante a execução do RPA | id=%s", execution_id)
            raise RpaApiError(409, str(exc)) from exc
        except Exception as exc:
            current_app.logger.exception("Falha durante a execução do RPA | id=%s", execution_id)
            raise RpaApiError(500, f"Falha ao executar o RPA: {exc}") from exc
        if not isinstance(execution, dict):
            raise RpaApiError(500, "O executor do GRV devolveu um resultado invalido.")
        expected_count = len(normalized_codes)
        confirmed = execution.get("gravacao_confirmada") is True
        inserted_codes = [
            _normalizar(code) for code in execution.get("codigos_inseridos", [])
        ]
        if (
            execution.get("gravado") is not True
            or execution.get("comando_gravar_enviado") is not True
            or not confirmed
            or execution.get("descricao_validada") is not True
            or execution.get("modo_edicao_encerrado") is not True
            or inserted_codes != normalized_codes
            or int(execution.get("quantidade_codigos") or 0) != expected_count
        ):
            current_app.logger.error(
                "RPA sem confirmacao de gravacao | id=%s | resultado=%s",
                execution_id,
                execution,
            )
            raise RpaApiError(
                409,
                "O GRV nao confirmou o preenchimento integral e a gravacao. "
                "O apontamento foi marcado como falha para evitar um falso sucesso.",
            )
        _last_execution = (fingerprint, now)
        current_app.logger.warning(
            "Execução RPA concluída | id=%s | gravado=%s | quantidade=%s",
            execution_id,
            execution.get("gravado"),
            execution.get("quantidade_codigos"),
        )
        return {
            "ok": True,
            "executed": True,
            "execution_id": execution_id,
            "result": execution,
            "payload": payload,
        }
