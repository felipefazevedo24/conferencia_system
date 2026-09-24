"""Transporte de previas de Producao processadas junto ao ERP."""
from __future__ import annotations

from collections import OrderedDict
import hashlib
import io
import json
import logging
import os
from pathlib import Path
import tempfile
import threading
import time
from typing import Any
import zipfile

from flask import current_app, has_app_context
import requests

from ..compras.config import get_settings


_logger = logging.getLogger(__name__)
_HTTP = threading.local()
_UNAVAILABLE: OrderedDict[tuple[str, str], float] = OrderedDict()
_UNAVAILABLE_LOCK = threading.Lock()
_CAPABILITY_RETRY_SECONDS = 300.0
_MAX_RESPONSE_BYTES = 16 * 1024 * 1024
_MAX_IMAGE_BYTES = 8 * 1024 * 1024
_DISK_CACHE_LIMIT = 256
_DISK_CACHE_MAX_BYTES = 128 * 1024 * 1024
_DISK_CACHE_MAX_AGE = 7 * 24 * 60 * 60


def _session() -> requests.Session:
    session = getattr(_HTTP, "session", None)
    if session is None:
        session = requests.Session()
        for scheme in ("http://", "https://"):
            session.mount(scheme, requests.adapters.HTTPAdapter(pool_connections=2, pool_maxsize=2, max_retries=0))
        _HTTP.session = session
    return session


def _mark_unavailable(key: tuple[str, str]) -> None:
    with _UNAVAILABLE_LOCK:
        _UNAVAILABLE[key] = time.monotonic() + _CAPABILITY_RETRY_SECONDS
        _UNAVAILABLE.move_to_end(key)
        while len(_UNAVAILABLE) > 16:
            _UNAVAILABLE.popitem(last=False)
    _logger.info("producao_bridge_preview indisponivel; usando renderizacao local ate nova verificacao")


def _decode_archive(content: bytes | bytearray) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        entries = archive.infolist()
        if len(entries) != 2 or {entry.filename for entry in entries} != {"thumbnail.png", "detail.png"}:
            raise ValueError("Pacote de previas invalido")
        previews = {}
        for entry in entries:
            if entry.compress_type != zipfile.ZIP_STORED or entry.file_size > _MAX_IMAGE_BYTES or entry.flag_bits & 1:
                raise ValueError("Imagem de previa invalida")
            image = archive.read(entry)
            if not image.startswith(b"\x89PNG\r\n\x1a\n"):
                raise ValueError("Formato de previa invalido")
            previews[entry.filename[:-4]] = image
    return previews


def _decode_previews(response: requests.Response) -> tuple[dict[str, bytes], int]:
    if int(response.headers.get("Content-Length") or 0) > _MAX_RESPONSE_BYTES:
        raise ValueError("Resposta de previa excede o limite")
    content = bytearray()
    for chunk in response.iter_content(chunk_size=65536):
        content.extend(chunk)
        if len(content) > _MAX_RESPONSE_BYTES:
            raise ValueError("Resposta de previa excede o limite")
    return _decode_archive(content), len(content)


def _disk_cache_path(payload: dict[str, Any], target: tuple[str, str]) -> Path | None:
    if not has_app_context():
        return None
    identity = json.dumps({"target": target, "document": payload}, sort_keys=True, separators=(",", ":"))
    cache_key = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    return Path(current_app.instance_path) / "producao_previews" / f"{cache_key}.zip"


def _read_disk_cache(path: Path | None) -> dict[str, bytes] | None:
    if path is None:
        return None
    try:
        info = path.stat()
        if info.st_size > _MAX_RESPONSE_BYTES or time.time() - info.st_mtime > _DISK_CACHE_MAX_AGE:
            return None
        previews = _decode_archive(path.read_bytes())
        os.utime(path, None)
        return previews
    except (OSError, ValueError, zipfile.BadZipFile):
        return None


def _write_disk_cache(path: Path | None, previews: dict[str, bytes]) -> None:
    if path is None:
        return
    temporary_path = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=path.parent, suffix=".tmp", delete=False) as output:
            temporary_path = Path(output.name)
            with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED) as archive:
                for variant in ("thumbnail", "detail"):
                    archive.writestr(f"{variant}.png", previews[variant])
        os.replace(temporary_path, path)
        entries = []
        for cached_path in path.parent.glob("*.zip"):
            try:
                entries.append((cached_path.stat(), cached_path))
            except OSError:
                continue
        total_bytes = 0
        for index, (info, cached_path) in enumerate(sorted(entries, key=lambda entry: entry[0].st_mtime, reverse=True)):
            total_bytes += info.st_size
            if index >= _DISK_CACHE_LIMIT or total_bytes > _DISK_CACHE_MAX_BYTES or time.time() - info.st_mtime > _DISK_CACHE_MAX_AGE:
                try:
                    cached_path.unlink(missing_ok=True)
                except OSError:
                    continue
    except OSError:
        _logger.warning("producao_preview_cache_disco indisponivel; mantendo cache em memoria")
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass


def obter_previews_bridge(
    document: dict[str, Any],
    renderer_version: str,
    context: dict[str, Any] | None = None,
) -> dict[str, bytes] | None:
    settings = get_settings()
    if not settings.USE_API_BRIDGE or not settings.API_URL:
        return None

    from ..compras.db import ProducaoSourceError

    key = (settings.API_URL, hashlib.sha256(settings.API_TOKEN.encode("utf-8")).hexdigest())
    headers = {
        "Accept": "application/zip",
        "Authorization": f"Bearer {settings.API_TOKEN}",
        "User-Agent": "ColumbiaSync/Producao",
        "ngrok-skip-browser-warning": "true",
    }
    payload = {
        "cod_empresa": 1,
        "cod_os": int(document["source_cod_os"]),
        "cod_os_aux": int(document["source_aux_code"]),
        "document_id": int(document["id"]),
        "kind": str(document["source_kind"]),
        "content_revision": str(document["content_revision"]),
        "renderer_version": renderer_version,
        "render_context": {
            key: str((context or {}).get(key) or "")[:500]
            for key in (
                "subtitulo", "n_desenho", "cod_os_completo", "revisao_desenho",
                "posicao_desenho", "segmento",
            )
        },
    }
    cache_path = _disk_cache_path(payload, key)
    cached = _read_disk_cache(cache_path)
    if cached is not None:
        return cached
    with _UNAVAILABLE_LOCK:
        if _UNAVAILABLE.get(key, 0) > time.monotonic():
            return None
        _UNAVAILABLE.pop(key, None)
    timeout = max(1, settings.API_TIMEOUT)
    started = time.perf_counter()
    response = None
    try:
        response = _session().post(
            f"{settings.API_URL}/api/erp/producao/preview",
            headers=headers, json=payload, stream=True, allow_redirects=False,
            timeout=(min(5, timeout), min(30, timeout)),
        )
        if response.status_code in {401, 403}:
            raise ProducaoSourceError("bridge_access_denied")
        if response.status_code != 200:
            try:
                error = response.json().get("erro")
            except (ValueError, AttributeError):
                error = None
            if error in {"documento_nao_encontrado", "documento_alterado", "documento_muito_grande", "previa_indisponivel"}:
                raise LookupError("Previa de Producao indisponivel ou documento alterado na bridge")
            if response.status_code in {404, 405, 501} or error == "renderizador_incompativel":
                _mark_unavailable(key)
            else:
                _logger.warning("producao_bridge_preview fallback status=%s", response.status_code)
            return None
        if response.headers.get("X-ERP-Read-Only") != "true":
            raise ProducaoSourceError("bridge_readonly_required")
        if response.headers.get("X-Production-Preview-Version") != renderer_version:
            _mark_unavailable(key)
            return None
        previews, transferred = _decode_previews(response)
        _write_disk_cache(cache_path, previews)
        _logger.info(
            "producao_bridge_preview elapsed_ms=%.2f preview_bytes=%s original_bytes=%s",
            (time.perf_counter() - started) * 1000, transferred, response.headers.get("X-Original-Bytes", "0"),
        )
        return previews
    except (requests.RequestException, ValueError, zipfile.BadZipFile) as error:
        _logger.warning("producao_bridge_preview fallback error_type=%s", type(error).__name__)
        return None
    finally:
        if response is not None:
            response.close()
