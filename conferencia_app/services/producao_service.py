"""Nucleo nativo do modulo de Producao, usando o banco/bridge do Sync."""
from __future__ import annotations

from collections import OrderedDict, defaultdict
from concurrent.futures import Future, ThreadPoolExecutor
import base64
import copy
from difflib import SequenceMatcher
import hashlib
import io
import math
import re
import threading
import time
import unicodedata
from datetime import date, datetime
from typing import Any

from ..compras import queries
from ..compras.db import fetch_all, fetch_one

try:
    import fitz
except ImportError:  # pragma: no cover
    fitz = None

STATUS_LABELS = {
    "bloqueado": "Bloqueado",
    "concluido": "Concluido",
    "disponivel": "Disponivel",
    "montagem": "Em montagem",
    "fabricacao": "Em fabricacao",
    "nao_iniciado": "Nao iniciado",
}

_PREVIEW_CACHE_VERSION = "isometric-v4"
_MAX_DOCUMENT_BYTES = 25 * 1024 * 1024
_PREVIEW_CACHE_LIMIT = 64
_PREVIEW_CACHE: OrderedDict[str, dict[str, bytes]] = OrderedDict()
_PREVIEW_JOBS: dict[str, Future[dict[str, bytes]]] = {}
_PREVIEW_FAILURES: dict[str, str] = {}
_PREVIEW_LOCK = threading.RLock()
_PREVIEW_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="production-preview")
_PREVIEW_REQUEST_CACHE_TTL_SECONDS = 10.0
_PREVIEW_REQUEST_CACHE_LIMIT = 128
_PREVIEW_REQUEST_CACHE: OrderedDict[str, tuple[float, tuple[bytes, str, str] | Exception]] = OrderedDict()
_PREVIEW_REQUEST_JOBS: dict[str, Future[tuple[bytes, str, str]]] = {}
_PREVIEW_REQUEST_LOCK = threading.RLock()
_PREVIEW_REQUEST_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="production-preview-request")
_STRUCTURE_CACHE_TTL_SECONDS = 10.0
_STRUCTURE_CACHE_LIMIT = 16
_STRUCTURE_CACHE: OrderedDict[str, tuple[float, dict[str, Any]]] = OrderedDict()
_STRUCTURE_LOCK = threading.RLock()


def _iso(value: Any) -> str | None:
    return value.isoformat() if isinstance(value, (datetime, date)) else (str(value) if value else None)


def _texto(value: Any) -> str:
    return str(value or "").strip()


def buscar_os(termo: str, limite: int = 20) -> list[dict[str, Any]]:
    termo = _texto(termo)
    if not termo:
        return []
    rows = fetch_all(
        queries.SQL_PRODUCAO_BUSCAR_OS,
        {
            "cod_empresa": 1,
            "busca": f"%{termo}%",
            "termo": termo,
            "limite": max(1, min(int(limite or 20), 50)),
        },
    )
    return [_os_payload(row) for row in rows]


def listar_os_abertas(limite: int = 100) -> list[dict[str, Any]]:
    rows = fetch_all(
        queries.SQL_PRODUCAO_OS_ABERTAS,
        {"cod_empresa": 1, "limite": max(1, min(int(limite or 100), 200))},
    )
    return [_os_payload(row) for row in rows]


def obter_estrutura(numero_os: str) -> dict[str, Any]:
    cache_key = _texto(numero_os)
    now = time.monotonic()
    with _STRUCTURE_LOCK:
        cached = _STRUCTURE_CACHE.get(cache_key)
        if cached and now - cached[0] <= _STRUCTURE_CACHE_TTL_SECONDS:
            _STRUCTURE_CACHE.move_to_end(cache_key)
            return copy.deepcopy(cached[1])
        if cached:
            _STRUCTURE_CACHE.pop(cache_key, None)
    ordem = fetch_one(
        queries.SQL_PRODUCAO_BUSCAR_OS,
        {"cod_empresa": 1, "busca": numero_os, "termo": numero_os, "limite": 1},
    )
    if not ordem:
        raise LookupError(f"OS {numero_os} nao encontrada.")
    itens = fetch_all(
        queries.SQL_PRODUCAO_ESTRUTURA_OS,
        {"cod_empresa": 1, "cod_os": ordem["codigo"]},
    )
    operacoes = fetch_all(
        queries.SQL_PRODUCAO_OPERACOES_OS,
        {"cod_empresa": 1, "cod_os": ordem["codigo"]},
    )
    payload = _estrutura_payload(ordem, itens, operacoes)
    payload["rncs"] = _rncs(ordem["codigo"])
    with _STRUCTURE_LOCK:
        _STRUCTURE_CACHE[cache_key] = (time.monotonic(), copy.deepcopy(payload))
        _STRUCTURE_CACHE.move_to_end(cache_key)
        while len(_STRUCTURE_CACHE) > _STRUCTURE_CACHE_LIMIT:
            _STRUCTURE_CACHE.popitem(last=False)
    return copy.deepcopy(payload)


def obter_materiais(numero_os: str, aux_code: int) -> dict[str, Any]:
    ordem = _obter_ordem(numero_os)
    rows = fetch_all(
        queries.SQL_PRODUCAO_MATERIAIS_ITEM,
        {"cod_empresa": 1, "cod_os": ordem["codigo"], "cod_os_aux": aux_code},
    )
    return {
        "numero_os": numero_os,
        "aux_code": aux_code,
        "materiais": [
            {
                "id": _texto(row.get("line_id")),
                "codigo": _texto(row.get("cod_interno") or row.get("produto")),
                "descricao": _texto(row.get("produto")),
                "unidade": _texto(row.get("unidade")),
                "necessario": row.get("qtde") or 0,
                "utilizado": row.get("qtde_utilizada") or 0,
                "disponivel": row.get("qtde_disponivel"),
                "restante": max(float(row.get("qtde") or 0) - float(row.get("qtde_utilizada") or 0), 0),
            }
            for row in rows
        ],
    }


def obter_apontamentos(numero_os: str, aux_code: int) -> dict[str, Any]:
    ordem = _obter_ordem(numero_os)
    rows = fetch_all(
        queries.SQL_PRODUCAO_APONTAMENTOS_ITEM,
        {"cod_empresa": 1, "cod_os": ordem["codigo"], "cod_os_aux": aux_code},
    )
    return {"aux_code": aux_code, "atualizado_em": datetime.now().isoformat(), "apontamentos": [
        {"operacao": _texto(row.get("operation_code")), "sequencia": row.get("seq_processo_prod"), "operador": _texto(row.get("operator_name")), "inicio": _iso(row.get("started_at")), "maquina": _texto(row.get("machine")), "pausado": bool(row.get("paused"))}
        for row in rows
    ]}


def obter_documentos(
    numero_os: str,
    aux_code: int,
    *,
    ordem: dict[str, Any] | None = None,
    context: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    ordem = ordem or _obter_ordem(numero_os)
    if context is None:
        context = fetch_one(
            queries.SQL_PRODUCAO_ITEM_PREVIEW_CONTEXT,
            {"cod_empresa": 1, "cod_os": ordem["codigo"], "cod_os_aux": aux_code},
        )
    if not context:
        raise LookupError("Item nao encontrado")
    selected, documents = _resolver_documento_previa(ordem, numero_os, aux_code, context)
    for document in documents:
        document["is_primary"] = bool(
            selected
            and document.get("source_kind") == selected.get("source_kind")
            and document.get("id") == selected.get("id")
            and document.get("source_cod_os") == selected.get("source_cod_os")
            and document.get("source_aux_code") == selected.get("source_aux_code")
        )
    return documents


def _obter_documentos_ordem(ordem: dict[str, Any], numero_os: str, aux_code: int) -> list[dict[str, Any]]:
    rows = fetch_all(queries.SQL_PRODUCAO_DOCUMENTOS_ITEM, {"cod_empresa": 1, "cod_os": ordem["codigo"], "cod_os_aux": aux_code})
    return [_documento_payload(row, numero_os, aux_code, int(ordem["codigo"]), aux_code) for row in rows]


def _documento_payload(
    row: dict[str, Any],
    numero_os: str,
    display_aux_code: int,
    source_cod_os: int,
    source_aux_code: int,
    *,
    from_origin: bool = False,
) -> dict[str, Any]:
    kind = _texto(row.get("kind"))
    route_kind = {"drawing": "drawings", "attachment": "attachments", "image": "images"}
    source_query = ""
    if from_origin or int(source_aux_code) != int(display_aux_code):
        source_query = f"?source_cod_os={int(source_cod_os)}&source_aux_code={int(source_aux_code)}"
    return {
        "id": row.get("document_id"),
        "kind": kind,
        "source_kind": kind,
        "filename": _texto(row.get("nome_arquivo")) or "documento",
        "description": _texto(row.get("descricao")),
        "size_bytes": row.get("size_bytes") or 0,
        "content_revision": row.get("content_revision"),
        "aux_code": int(display_aux_code),
        "source_cod_os": int(source_cod_os),
        "source_aux_code": int(source_aux_code),
        "from_origin": bool(from_origin),
        "open_url": f"/api/v1/orders/{numero_os}/items/{display_aux_code}/{route_kind.get(kind, kind)}/{row.get('document_id')}{source_query}",
    }


def obter_arquivo(
    numero_os: str,
    aux_code: int,
    kind: str,
    document_id: int,
    *,
    ordem: dict[str, Any] | None = None,
    source_cod_os: int | None = None,
    source_aux_code: int | None = None,
) -> tuple[bytes, str]:
    ordem = ordem or _obter_ordem(numero_os)
    query = {"drawing": queries.SQL_PRODUCAO_DESENHO_ARQUIVO, "attachment": queries.SQL_PRODUCAO_ANEXO_ARQUIVO, "image": queries.SQL_PRODUCAO_IMAGEM_ARQUIVO}.get(kind)
    if query is None:
        raise LookupError("Tipo de documento invalido")
    row = fetch_one(query, {
        "cod_empresa": 1,
        "cod_os": int(source_cod_os or ordem["codigo"]),
        "cod_os_aux": int(source_aux_code or aux_code),
        "document_id": document_id,
        "max_bytes": _MAX_DOCUMENT_BYTES,
    })
    if row and int(row.get("size_bytes") or 0) > _MAX_DOCUMENT_BYTES:
        raise LookupError("Documento excede o limite permitido para previa")
    if not row or row.get("anexo") is None:
        raise LookupError("Documento nao encontrado")
    content = row["anexo"]
    if isinstance(content, str):
        try:
            content = base64.b64decode(content, validate=True)
        except (ValueError, TypeError) as exc:
            raise LookupError("Documento recebido da Bridge esta corrompido") from exc
    if not isinstance(content, (bytes, bytearray, memoryview)):
        raise LookupError("Formato de documento invalido recebido da Bridge")
    return bytes(content), _texto(row.get("nome_arquivo")) or "documento"


def _normalizar_identificador(value: Any) -> str:
    text = unicodedata.normalize("NFKD", _texto(value))
    text = "".join(char for char in text if not unicodedata.combining(char)).upper()
    return " ".join(text.split())


def _texto_comparavel(value: Any) -> str:
    return re.sub(r"[^A-Z0-9]+", "", _normalizar_identificador(value))


def _pontuar_documento(document: dict[str, Any], context: dict[str, Any]) -> int:
    metadata = _texto_comparavel(f"{document.get('filename', '')} {document.get('description', '')}")
    if not metadata:
        return 0
    score = 0
    drawing = _texto_comparavel(context.get("n_desenho"))
    item_code = _texto_comparavel(context.get("cod_os_completo"))
    position = _texto_comparavel(context.get("posicao_desenho"))
    revision = _texto_comparavel(context.get("revisao_desenho"))
    item_description = _texto_comparavel(context.get("subtitulo"))
    if drawing and drawing in metadata:
        score += 100
    if item_code and item_code in metadata:
        score += 35
    if position and len(position) >= 2 and position in metadata:
        score += 15
    if revision and any(marker in metadata for marker in (f"REV{revision}", f"REVISAO{revision}", f"R{revision}")):
        score += 30
    if item_description:
        similarity = SequenceMatcher(None, item_description, metadata).ratio()
        if similarity >= 0.55:
            score += round(similarity * 60)
    return score


def _revisao_conflitante(document: dict[str, Any], context: dict[str, Any]) -> bool:
    expected = _texto_comparavel(context.get("revisao_desenho"))
    if not expected:
        return False
    metadata = _normalizar_identificador(f"{document.get('filename', '')} {document.get('description', '')}")
    revisions = {
        _texto_comparavel(match)
        for match in re.findall(r"(?:^|[^A-Z0-9])(?:REV(?:ISAO)?|R)[\s_.-]*([A-Z0-9]+)", metadata)
    }
    return bool(revisions and expected not in revisions)


def _segmento_cms(context: dict[str, Any]) -> bool:
    return _normalizar_identificador(context.get("segmento")).startswith("CMS")


def _selecionar_documento_previa(documents: list[dict[str, Any]], context: dict[str, Any]) -> dict[str, Any] | None:
    expected_kind = "attachment" if _segmento_cms(context) else "drawing"
    candidates = [
        document
        for document in documents
        if document.get("kind") == expected_kind and not _revisao_conflitante(document, context)
    ]
    if not candidates:
        return None
    ranked = sorted((( _pontuar_documento(document, context), document) for document in candidates), key=lambda item: (item[0], int(item[1].get("id") or 0)), reverse=True)
    best_score, best = ranked[0]
    if expected_kind == "attachment" and best_score < 45:
        return None
    if len(candidates) == 1:
        return best
    if best_score <= 0 or best_score == ranked[1][0]:
        return None
    return best


def _resolver_documento_previa(
    ordem: dict[str, Any],
    numero_os: str,
    aux_code: int,
    context: dict[str, Any],
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """Resolve a fonte real sem trocar ANEXO/DESENHO nem aceitar outro desenho."""
    direct_documents = _obter_documentos_ordem(ordem, numero_os, aux_code)
    selected = _selecionar_documento_previa(direct_documents, context)
    if selected or _segmento_cms(context):
        return selected, direct_documents

    drawing_number = _texto_comparavel(context.get("n_desenho"))
    if drawing_number:
        item_rows = fetch_all(
            queries.SQL_PRODUCAO_ESTRUTURA_OS,
            {"cod_empresa": 1, "cod_os": ordem["codigo"]},
        )
        matching_aux_codes = {
            int(item["aux_code"])
            for item in item_rows
            if int(item["aux_code"]) != int(aux_code)
            and _texto_comparavel(item.get("n_desenho")) == drawing_number
            and not _revisao_contexto_conflitante(item, context)
        }
        if matching_aux_codes:
            rows = fetch_all(
                queries.SQL_PRODUCAO_DOCUMENTOS_OS,
                {"cod_empresa": 1, "cod_os": ordem["codigo"]},
            )
            candidates = []
            for row in rows:
                source_aux = int(row.get("cod_os_aux") or 0)
                if source_aux not in matching_aux_codes:
                    continue
                candidate = _documento_payload(
                    row, numero_os, aux_code, int(ordem["codigo"]), source_aux
                )
                if candidate["kind"] == "drawing" and not _revisao_conflitante(candidate, context):
                    candidates.append(candidate)
            selected = _selecionar_documento_previa(candidates, context)
            if selected:
                return selected, direct_documents + [selected]

    origin = _origem_documento(ordem, aux_code)
    if origin:
        if _texto_comparavel(origin.get("n_desenho")) not in {"", drawing_number}:
            return None, direct_documents
        if _revisao_contexto_conflitante(origin, context):
            return None, direct_documents
        rows = fetch_all(
            queries.SQL_PRODUCAO_DOCUMENTOS_ITEM,
            {"cod_empresa": 1, **origin},
        )
        origin_documents = [
            _documento_payload(
                row,
                numero_os,
                aux_code,
                int(origin["cod_os"]),
                int(origin["cod_os_aux"]),
                from_origin=True,
            )
            for row in rows
        ]
        selected = _selecionar_documento_previa(origin_documents, context)
        if selected:
            return selected, direct_documents + [selected]
    return None, direct_documents


def _revisao_contexto_conflitante(item: dict[str, Any], context: dict[str, Any]) -> bool:
    expected = _texto_comparavel(context.get("revisao_desenho"))
    candidate = _texto_comparavel(item.get("revisao_desenho"))
    return bool(expected and candidate and expected != candidate)


def _origem_documento(ordem: dict[str, Any], aux_code: int) -> dict[str, Any] | None:
    schema = fetch_one(queries.SQL_PRODUCAO_ORIGEM_SCHEMA, {"cod_empresa": 1})
    if not schema or not schema.get("supported"):
        return None
    return fetch_one(
        queries.SQL_PRODUCAO_ITEM_ORIGEM,
        {"cod_empresa": 1, "cod_os": ordem["codigo"], "cod_os_aux": aux_code},
    )


def _line_stats(items: list[Any]) -> tuple[int, int, int]:
    lines = diagonals = curves = 0
    for item in items:
        if not item:
            continue
        if item[0] == "l" and len(item) >= 3:
            lines += 1
            delta_x = abs(float(item[2].x) - float(item[1].x))
            delta_y = abs(float(item[2].y) - float(item[1].y))
            if delta_x > 1 and delta_y > 1:
                diagonals += 1
        elif item[0] in {"c", "qu"}:
            curves += 1
    return lines, diagonals, curves


def _expand_rect(rect: Any, margin: float, bounds: Any) -> Any:
    return fitz.Rect(
        max(bounds.x0, rect.x0 - margin),
        max(bounds.y0, rect.y0 - margin),
        min(bounds.x1, rect.x1 + margin),
        min(bounds.y1, rect.y1 + margin),
    )


def _merge_drawing_records(records: list[dict[str, Any]], page_rect: Any, margin: float) -> list[dict[str, Any]]:
    groups = [{**record, "rect": fitz.Rect(record["rect"])} for record in records]
    changed = True
    while changed:
        changed = False
        for index, group in enumerate(groups):
            for other_index in range(index + 1, len(groups)):
                other = groups[other_index]
                if not _expand_rect(group["rect"], margin, page_rect).intersects(other["rect"]):
                    continue
                group["rect"] |= other["rect"]
                for key in ("lines", "diagonals", "curves", "paths"):
                    group[key] += other[key]
                groups.pop(other_index)
                changed = True
                break
            if changed:
                break
    return groups


def _isometric_label_score(rect: Any, labels: list[Any], page_rect: Any) -> tuple[float, bool]:
    if not labels:
        return 0.0, False
    maximum_distance = max(page_rect.width, page_rect.height) * 0.22
    best_distance = maximum_distance + 1
    for label in labels:
        delta_x = max(label.x0 - rect.x1, rect.x0 - label.x1, 0)
        delta_y = max(label.y0 - rect.y1, rect.y0 - label.y1, 0)
        best_distance = min(best_distance, math.hypot(delta_x, delta_y))
    if best_distance > maximum_distance:
        return 0.0, False
    return 4.0 + 6.0 * (1.0 - best_distance / maximum_distance), True


def _overlap_ratio(first: Any, second: Any) -> float:
    intersection = first & second
    if intersection.is_empty:
        return 0.0
    intersection_area = intersection.width * intersection.height
    smaller_area = min(first.width * first.height, second.width * second.height)
    return intersection_area / max(smaller_area, 1)


def _page_isometric_candidates(page: Any) -> list[tuple[float, Any, bool]]:
    page_rect = page.rect
    page_area = max(page_rect.width * page_rect.height, 1)
    records = []
    for drawing in page.get_drawings():
        rect = fitz.Rect(drawing.get("rect")) & page_rect
        coverage = (rect.width * rect.height) / page_area
        if rect.is_empty or coverage < 0.00002 or coverage > 0.82:
            continue
        lines, diagonals, curves = _line_stats(drawing.get("items") or [])
        records.append({"rect": rect, "lines": lines, "diagonals": diagonals, "curves": curves, "paths": 1})

    join_margin = max(7.0, min(page_rect.width, page_rect.height) * 0.02)
    groups = _merge_drawing_records(records, page_rect, join_margin)
    labels = [
        fitz.Rect(block[:4])
        for block in page.get_text("blocks")
        if len(block) > 4 and "ISOMETR" in _normalizar_identificador(block[4])
    ]
    candidates = []
    for group in groups:
        rect = group["rect"]
        coverage = (rect.width * rect.height) / page_area
        diagonal_ratio = group["diagonals"] / max(group["lines"], 1)
        if coverage < 0.008 or coverage > 0.72:
            continue
        if group["diagonals"] < 2 and group["curves"] < 1 and group["paths"] < 6:
            continue
        label_score, labeled = _isometric_label_score(rect, labels, page_rect)
        score = diagonal_ratio * 8 + min(group["paths"], 40) / 12 + min(group["curves"], 8) * 0.35 + min(coverage, 0.35) * 4 + label_score
        if rect.y0 > page_rect.height * 0.72 and rect.x0 > page_rect.width * 0.55:
            score -= 4
        candidates.append((score, _expand_rect(rect, max(rect.width, rect.height) * 0.06, page_rect), labeled))

    for image in page.get_image_info():
        rect = fitz.Rect(image.get("bbox")) & page_rect
        coverage = (rect.width * rect.height) / page_area
        if not rect.is_empty and 0.06 <= coverage <= 0.72:
            label_score, labeled = _isometric_label_score(rect, labels, page_rect)
            score = 3 + min(coverage, 0.4) * 3 + label_score
            candidates.append((score, _expand_rect(rect, max(rect.width, rect.height) * 0.04, page_rect), labeled))

    unique_candidates = []
    for candidate in sorted(candidates, key=lambda item: item[0], reverse=True):
        if any(_overlap_ratio(candidate[1], existing[1]) >= 0.65 for existing in unique_candidates):
            continue
        unique_candidates.append(candidate)
    return unique_candidates


def _render_pdf_previews(content: bytes) -> dict[str, bytes]:
    if fitz is None:
        raise LookupError("Renderizador de PDF nao instalado")
    try:
        with fitz.open(stream=content, filetype="pdf") as pdf:
            if pdf.page_count == 0:
                raise LookupError("Documento sem paginas")
            candidates = []
            for page_index in range(pdf.page_count):
                page = pdf.load_page(page_index)
                candidates.extend((score, page_index, rect, labeled) for score, rect, labeled in _page_isometric_candidates(page))
            candidates.sort(key=lambda item: item[0], reverse=True)
            ambiguous = len(candidates) > 1 and candidates[0][0] - candidates[1][0] < 0.08 and not candidates[0][3]
            if not candidates or candidates[0][0] < 1.5 or ambiguous:
                raise LookupError("Vista isometrica nao identificada com confianca")
            _, page_index, clip, _ = candidates[0]
            page = pdf.load_page(page_index)
            return _render_clip_previews(page, clip)
    except LookupError:
        raise
    except Exception as exc:
        raise LookupError("Documento PDF invalido ou ilegivel") from exc


def _render_clip_previews(page: Any, clip: Any) -> dict[str, bytes]:
    rendered = {}
    for variant, max_pixels in (("thumbnail", 720), ("detail", 1600)):
        scale = max(1.5, min(4.0, max_pixels / max(clip.width, clip.height)))
        pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), clip=clip, alpha=False)
        rendered[variant] = pixmap.tobytes("png")
    return rendered


def _render_raster_previews(content: bytes, filetype: str) -> dict[str, bytes]:
    if fitz is None:
        raise LookupError("Renderizador de imagem nao instalado")
    try:
        with fitz.open(stream=content, filetype=filetype) as image_document:
            page = image_document.load_page(0)
            page_rect = page.rect
            analysis_scale = min(1.0, 800 / max(page_rect.width, page_rect.height))
            pixmap = page.get_pixmap(matrix=fitz.Matrix(analysis_scale, analysis_scale), alpha=False)
            samples = pixmap.samples
            channels = pixmap.n
            min_x, min_y, max_x, max_y = pixmap.width, pixmap.height, -1, -1
            ink_pixels = 0
            for y_coord in range(pixmap.height):
                row_start = y_coord * pixmap.stride
                for x_coord in range(pixmap.width):
                    offset = row_start + x_coord * channels
                    if min(samples[offset:offset + min(channels, 3)]) >= 238:
                        continue
                    ink_pixels += 1
                    min_x = min(min_x, x_coord)
                    min_y = min(min_y, y_coord)
                    max_x = max(max_x, x_coord)
                    max_y = max(max_y, y_coord)
            if ink_pixels < 80 or max_x < min_x or max_y < min_y:
                raise LookupError("Vista isometrica nao identificada com confianca")
            width = max_x - min_x + 1
            height = max_y - min_y + 1
            coverage = (width * height) / max(pixmap.width * pixmap.height, 1)
            if coverage < 0.008 or coverage > 0.76:
                raise LookupError("Vista isometrica nao identificada com confianca")
            clip = fitz.Rect(
                min_x / analysis_scale,
                min_y / analysis_scale,
                (max_x + 1) / analysis_scale,
                (max_y + 1) / analysis_scale,
            )
            clip = _expand_rect(clip, max(clip.width, clip.height) * 0.06, page_rect)
            return _render_clip_previews(page, clip)
    except LookupError:
        raise
    except Exception as exc:
        raise LookupError("Imagem invalida ou ilegivel") from exc


def _gerar_previews(content: bytes, filename: str) -> dict[str, bytes]:
    suffix = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if suffix == "pdf":
        return _render_pdf_previews(content)
    if suffix in {"png", "jpg", "jpeg", "webp"}:
        return _render_raster_previews(content, "jpeg" if suffix in {"jpg", "jpeg"} else suffix)
    raise LookupError("Formato sem suporte para previa isometrica")


def _finalizar_preview(cache_key: str, future: Future[dict[str, bytes]]) -> None:
    try:
        previews = future.result()
    except Exception as exc:
        with _PREVIEW_LOCK:
            _PREVIEW_JOBS.pop(cache_key, None)
            _PREVIEW_FAILURES[cache_key] = str(exc)
        return
    with _PREVIEW_LOCK:
        _PREVIEW_JOBS.pop(cache_key, None)
        _PREVIEW_FAILURES.pop(cache_key, None)
        _PREVIEW_CACHE[cache_key] = previews
        _PREVIEW_CACHE.move_to_end(cache_key)
        while len(_PREVIEW_CACHE) > _PREVIEW_CACHE_LIMIT:
            _PREVIEW_CACHE.popitem(last=False)


def _previews_em_cache(cache_key: str, content: bytes, filename: str, wait: bool = True) -> dict[str, bytes] | None:
    with _PREVIEW_LOCK:
        cached = _PREVIEW_CACHE.get(cache_key)
        if cached is not None:
            _PREVIEW_CACHE.move_to_end(cache_key)
            return cached
        failure = _PREVIEW_FAILURES.get(cache_key)
        if failure:
            raise LookupError(failure)
        future = _PREVIEW_JOBS.get(cache_key)
        if future is None:
            future = _PREVIEW_EXECUTOR.submit(_gerar_previews, content, filename)
            _PREVIEW_JOBS[cache_key] = future
            future.add_done_callback(lambda completed: _finalizar_preview(cache_key, completed))
    return future.result() if wait else None


def _finalizar_requisicao_preview(cache_key: str, future: Future[tuple[bytes, str, str]]) -> None:
    try:
        result: tuple[bytes, str, str] | Exception = future.result()
    except Exception as exc:
        result = exc
    with _PREVIEW_REQUEST_LOCK:
        _PREVIEW_REQUEST_JOBS.pop(cache_key, None)
        _PREVIEW_REQUEST_CACHE[cache_key] = (time.monotonic(), result)
        _PREVIEW_REQUEST_CACHE.move_to_end(cache_key)
        while len(_PREVIEW_REQUEST_CACHE) > _PREVIEW_REQUEST_CACHE_LIMIT:
            _PREVIEW_REQUEST_CACHE.popitem(last=False)


def _obter_preview_assincrono(numero_os: str, aux_code: int, variant: str) -> tuple[bytes | None, str, str]:
    request_key = f"{_texto(numero_os)}|{int(aux_code)}|{variant}"
    now = time.monotonic()
    with _PREVIEW_REQUEST_LOCK:
        cached = _PREVIEW_REQUEST_CACHE.get(request_key)
        if cached and now - cached[0] <= _PREVIEW_REQUEST_CACHE_TTL_SECONDS:
            _PREVIEW_REQUEST_CACHE.move_to_end(request_key)
            if isinstance(cached[1], Exception):
                raise cached[1]
            return cached[1]
        if cached:
            _PREVIEW_REQUEST_CACHE.pop(request_key, None)
        if request_key not in _PREVIEW_REQUEST_JOBS:
            future = _PREVIEW_REQUEST_EXECUTOR.submit(obter_preview, numero_os, aux_code, variant, True)
            _PREVIEW_REQUEST_JOBS[request_key] = future
            future.add_done_callback(lambda completed: _finalizar_requisicao_preview(request_key, completed))
    pending_etag = hashlib.sha256(request_key.encode("utf-8")).hexdigest()
    return None, "image/png", pending_etag


def obter_preview(numero_os: str, aux_code: int, variant: str = "thumbnail", wait: bool = True) -> tuple[bytes | None, str, str]:
    if variant not in {"thumbnail", "detail"}:
        raise LookupError("Variante de previa invalida")
    if not wait:
        return _obter_preview_assincrono(numero_os, aux_code, variant)
    ordem = _obter_ordem(numero_os)
    params = {"cod_empresa": 1, "cod_os": ordem["codigo"], "cod_os_aux": aux_code}
    context = fetch_one(queries.SQL_PRODUCAO_ITEM_PREVIEW_CONTEXT, params)
    if not context:
        raise LookupError("Item nao encontrado")
    document, _ = _resolver_documento_previa(ordem, numero_os, aux_code, context)
    if not document:
        raise LookupError("Previa indisponivel: documento esperado ausente ou ambiguo")
    content, filename = obter_arquivo(
        numero_os,
        aux_code,
        str(document["source_kind"]),
        int(document["id"]),
        ordem=ordem,
        source_cod_os=int(document["source_cod_os"]),
        source_aux_code=int(document["source_aux_code"]),
    )
    identity = "|".join((
        _PREVIEW_CACHE_VERSION,
        str(document.get("source_kind")),
        str(document.get("id")),
        str(document.get("source_cod_os")),
        str(document.get("source_aux_code")),
        str(document.get("content_revision") or "unknown"),
        _texto(context.get("revisao_desenho")),
        hashlib.sha256(content).hexdigest(),
    ))
    cache_key = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    previews = _previews_em_cache(cache_key, content, filename, wait=wait)
    return previews[variant] if previews else None, "image/png", cache_key


def obter_thumbnail(numero_os: str, aux_code: int) -> tuple[bytes, str]:
    content, media_type, _ = obter_preview(numero_os, aux_code, "thumbnail")
    if content is None:  # pragma: no cover - a chamada bloqueante sempre produz conteudo
        raise LookupError("Previa ainda em processamento")
    return content, media_type


def _obter_ordem(numero_os: str) -> dict[str, Any]:
    ordem = fetch_one(queries.SQL_PRODUCAO_BUSCAR_OS, {"cod_empresa": 1, "busca": numero_os, "termo": numero_os, "limite": 1})
    if not ordem:
        raise LookupError(f"OS {numero_os} nao encontrada.")
    return ordem


def _rncs(cod_os: int) -> list[dict[str, Any]]:
    rows = fetch_all(queries.SQL_PRODUCAO_RNCS_OS, {"cod_empresa": 1, "cod_os": cod_os})
    return [{"codigo": row.get("codigo"), "aux_code": row.get("cod_os_aux"), "titulo": _texto(row.get("titulo")), "status": _texto(row.get("status_rnc")), "fechada_em": _iso(row.get("dt_fechamento")), "aberta": not bool(row.get("dt_fechamento"))} for row in rows]


def _os_payload(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "numero": _texto(row.get("n_os")),
        "codigo": row.get("codigo"),
        "titulo": _texto(row.get("titulo")),
        "status_origem": _texto(row.get("status_servico")),
        "data_prevista": _iso(row.get("dt_prevista")),
        "desenho": _texto(row.get("n_desenho")),
        "classificacao": _texto(row.get("u_classificacao")),
    }


def _estrutura_payload(ordem: dict[str, Any], itens: list[dict[str, Any]], operacoes: list[dict[str, Any]]) -> dict[str, Any]:
    filhos: dict[int, list[int]] = defaultdict(list)
    itens_por_id = {int(item["aux_code"]): item for item in itens}
    for item in itens:
        parent = item.get("os_pai")
        if parent is not None and int(parent) in itens_por_id:
            filhos[int(parent)].append(int(item["aux_code"]))
    roots = [item_id for item_id, item in itens_por_id.items() if item.get("os_pai") is None or int(item.get("os_pai") or 0) not in itens_por_id]
    ops_por_item: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for operation in operacoes:
        ops_por_item[int(operation["cod_os_aux"])].append(operation)

    states: dict[int, str] = {}
    visiting: set[int] = set()

    def derive(item_id: int) -> str:
        if item_id in states:
            return states[item_id]
        if item_id in visiting:
            states[item_id] = "bloqueado"
            return states[item_id]
        visiting.add(item_id)
        item_ops = ops_por_item.get(item_id, [])
        finished = sum(bool(op.get("finalizado") or op.get("concluido") or op.get("dt_finalizacao")) for op in item_ops)
        started = sum(bool(op.get("data_inicio") or op.get("pcp_dt_primeiro_apont") or op.get("hs_realizadas")) for op in item_ops)
        children = [derive(child_id) for child_id in filhos.get(item_id, [])]
        has_assembly = any("MONTAGEM" in _texto(op.get("tiposervico")).upper().split() for op in item_ops)
        if item_ops and finished == len(item_ops):
            state = "concluido" if has_assembly else "disponivel"
        elif any(bool(op.get("processo_travado")) for op in item_ops):
            state = "bloqueado"
        elif started:
            state = "montagem" if has_assembly else "fabricacao"
        elif has_assembly and children and all(child in {"disponivel", "concluido"} for child in children):
            state = "disponivel"
        elif item_ops:
            state = "nao_iniciado"
        else:
            state = "disponivel" if _texto(item.get("status")).upper() in {"CONCLUIDO", "FINALIZADO"} else "nao_iniciado"
        visiting.remove(item_id)
        states[item_id] = state
        return state

    nodes = []
    for item_id, item in itens_por_id.items():
        state = derive(item_id)
        item_operations = ops_por_item.get(item_id, [])
        parent_id = item.get("os_pai") if item.get("os_pai") in itens_por_id else None
        nodes.append({
            "id": str(item_id),
            "aux_code": item_id,
            "codigo": _texto(item.get("cod_os_completo")),
            "descricao": _texto(item.get("subtitulo")),
            "desenho": _texto(item.get("n_desenho")),
            "revisao": _texto(item.get("revisao_desenho")),
            "posicao": _texto(item.get("posicao_desenho")),
            "quantidade": item.get("qtde_pecas") or 0,
            "parent_id": str(parent_id) if parent_id is not None else None,
            "child_ids": [str(child_id) for child_id in filhos.get(item_id, [])],
            "estado": state,
            "estado_label": STATUS_LABELS[state],
            "estado_motivo": _motivo(state, item_operations),
            "operacoes_total": len(item_operations),
            "operacoes_concluidas": sum(bool(op.get("finalizado") or op.get("concluido") or op.get("dt_finalizacao")) for op in item_operations),
            "operacoes": [_operacao_payload(op) for op in item_operations],
            "data_prevista": _iso(item.get("dt_prevista")),
            "status_origem": _texto(item.get("status")),
        })
    total = sum(len(ops) for ops in ops_por_item.values())
    concluidas = sum(bool(op.get("finalizado") or op.get("concluido") or op.get("dt_finalizacao")) for op in operacoes)
    return {
        "ordem": _os_payload(ordem),
        "raizes": [str(root_id) for root_id in roots],
        "nos": nodes,
        "progresso": round((concluidas / total) * 100, 1) if total else 0,
        "operacoes_concluidas": concluidas,
        "operacoes_total": total,
        "bloqueados": sum(state == "bloqueado" for state in states.values()),
    }


def _operacao_payload(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "codigo": _texto(row.get("codigo")),
        "nome": _texto(row.get("tiposervico")),
        "sequencia": row.get("seq"),
        "finalizada": bool(row.get("finalizado") or row.get("concluido") or row.get("dt_finalizacao")),
        "travada": bool(row.get("processo_travado")),
        "inicio": _iso(row.get("data_inicio")),
        "fim": _iso(row.get("dt_finalizacao")),
        "maquina": _texto(row.get("maquina_real") or row.get("maquina")),
    }


def _motivo(state: str, operations: list[dict[str, Any]]) -> str:
    if state == "bloqueado":
        return "Processo produtivo travado ou ciclo invalido"
    if state == "concluido":
        return "Operacoes de producao e montagem concluidas"
    if state == "disponivel":
        return "Item disponivel para a proxima etapa"
    if state == "montagem":
        return "Operacao de montagem em andamento"
    if state == "fabricacao":
        return "Operacao produtiva em andamento"
    return "Processo cadastrado, mas ainda nao iniciado"
