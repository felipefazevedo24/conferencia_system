"""Transporte de previas de Producao processadas junto ao ERP."""
from __future__ import annotations

from collections import OrderedDict
import hashlib
import io
import logging
import threading
import time
from typing import Any
import zipfile

import requests

from ..compras.config import get_settings


_logger = logging.getLogger(__name__)
_HTTP = threading.local()
_UNAVAILABLE: OrderedDict[tuple[str, str], float] = OrderedDict()
_UNAVAILABLE_LOCK = threading.Lock()
_CAPABILITY_RETRY_SECONDS = 300.0
_MAX_RESPONSE_BYTES = 16 * 1024 * 1024
_MAX_IMAGE_BYTES = 8 * 1024 * 1024


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


def _decode_previews(response: requests.Response) -> tuple[dict[str, bytes], int]:
    if int(response.headers.get("Content-Length") or 0) > _MAX_RESPONSE_BYTES:
        raise ValueError("Resposta de previa excede o limite")
    content = bytearray()
    for chunk in response.iter_content(chunk_size=65536):
        content.extend(chunk)
        if len(content) > _MAX_RESPONSE_BYTES:
            raise ValueError("Resposta de previa excede o limite")
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
    return previews, len(content)


def obter_previews_bridge(document: dict[str, Any], renderer_version: str) -> dict[str, bytes] | None:
    settings = get_settings()
    if not settings.USE_API_BRIDGE or not settings.API_URL:
        return None

    from ..compras.db import ProducaoSourceError

    key = (settings.API_URL, hashlib.sha256(settings.API_TOKEN.encode("utf-8")).hexdigest())
    with _UNAVAILABLE_LOCK:
        if _UNAVAILABLE.get(key, 0) > time.monotonic():
            return None
        _UNAVAILABLE.pop(key, None)
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
    }
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