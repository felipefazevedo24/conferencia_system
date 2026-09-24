"""Nucleo nativo do modulo de Producao, usando o banco/bridge do Sync."""
from __future__ import annotations

from collections import OrderedDict, defaultdict
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
import base64
import copy
from difflib import SequenceMatcher
import hashlib
import heapq
import io
import math
import re
import threading
import time
import unicodedata
from datetime import date, datetime
from typing import Any, cast

from flask import Flask, current_app, has_app_context
from werkzeug.local import LocalProxy

from ..compras import queries
from ..compras.db import fetch_all, fetch_one
from .producao_bridge import obter_previews_bridge
from ..tempo import agora_br

try:
    import pymupdf as fitz
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

_PREVIEW_CACHE_VERSION = "isometric-cutout-v8"
_MAX_DOCUMENT_BYTES = 25 * 1024 * 1024
_PREVIEW_CACHE_LIMIT = 64
_PREVIEW_CACHE: OrderedDict[str, dict[str, bytes]] = OrderedDict()
_PREVIEW_JOBS: dict[str, Future[dict[str, bytes]]] = {}
_PREVIEW_FAILURES: OrderedDict[str, tuple[float, str]] = OrderedDict()
_PREVIEW_FAILURE_TTL_SECONDS = 10.0
_PREVIEW_LOCK = threading.RLock()
_PREVIEW_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="production-preview")
_PREVIEW_REQUEST_CACHE_TTL_SECONDS = 10.0
_PREVIEW_REQUEST_CACHE_LIMIT = 128
_PREVIEW_REQUEST_CACHE: OrderedDict[str, tuple[float, tuple[bytes, str, str] | Exception]] = OrderedDict()
_PREVIEW_REQUEST_JOBS: dict[str, Future[tuple[bytes, str, str]]] = {}
_PREVIEW_REQUEST_LOCK = threading.RLock()
_PREVIEW_REQUEST_CONCURRENCY = 4
_PREVIEW_DETAIL_CONCURRENCY = 2
_PREVIEW_REQUEST_EXECUTOR = ThreadPoolExecutor(max_workers=_PREVIEW_REQUEST_CONCURRENCY, thread_name_prefix="production-preview-request")
_PREVIEW_DETAIL_EXECUTOR = ThreadPoolExecutor(max_workers=_PREVIEW_DETAIL_CONCURRENCY, thread_name_prefix="production-preview-detail")
_STRUCTURE_CACHE_TTL_SECONDS = 10.0
_STRUCTURE_CACHE_LIMIT = 16
_STRUCTURE_CACHE: OrderedDict[str, tuple[float, dict[str, Any]]] = OrderedDict()
_STRUCTURE_LOCK = threading.RLock()
_STRUCTURE_JOBS = {}
_QUERY_CACHE = OrderedDict()
_QUERY_JOBS = {}
_QUERY_LOCK = threading.RLock()
_STRUCTURE_EXECUTOR = ThreadPoolExecutor(max_workers=3, thread_name_prefix="production-structure")


def _scope() -> Flask | None:
    return cast(LocalProxy[Flask], current_app)._get_current_object() if has_app_context() else None


def _cached_read(cache, jobs, lock, key, loader, ttl=10.0, limit=128):
    # Only identical reads share work; unrelated OS requests never hold this lock.
    key = (_scope(), key)
    with lock:
        cached = cache.get(key)
        if cached and time.monotonic() - cached[0] <= ttl:
            cache.move_to_end(key)
            return copy.deepcopy(cached[1])
        future = jobs.get(key)
        owner = future is None
        if owner:
            future = Future()
            jobs[key] = future
    if not owner:
        return copy.deepcopy(future.result())
    try:
        result = loader()
        with lock:
            cache[key] = (time.monotonic(), copy.deepcopy(result))
            cache.move_to_end(key)
            while len(cache) > limit:
                cache.popitem(last=False)
        future.set_result(result)
        return copy.deepcopy(result)
    except BaseException as error:
        future.set_exception(error)
        raise
    finally:
        with lock:
            jobs.pop(key, None)


def _metadata_read(fetch, sql, params):
    key = (sql, tuple(sorted(params.items())))
    return _cached_read(_QUERY_CACHE, _QUERY_JOBS, _QUERY_LOCK, key,
                        lambda: fetch(sql, params))


def _remember_order(row):
    numero_os = _texto(row.get("n_os"))
    if not numero_os:
        return
    params = {"cod_empresa": 1, "numero_os": numero_os}
    key = (_scope(), (queries.SQL_PRODUCAO_OBTER_OS, tuple(sorted(params.items()))))
    with _QUERY_LOCK:
        _QUERY_CACHE[key] = (time.monotonic(), copy.deepcopy(row))
        _QUERY_CACHE.move_to_end(key)
        while len(_QUERY_CACHE) > 128:
            _QUERY_CACHE.popitem(last=False)


def _run_with_app(app, function, *args):
    if app is None:
        return function(*args)
    with app.app_context():
        return function(*args)


def _iso(value: Any) -> str | None:
    return value.isoformat() if isinstance(value, (datetime, date)) else (str(value) if value else None)


def _texto(value: Any) -> str:
    return str(value or "").strip()


def buscar_os(termo: str, limite: int = 20) -> list[dict[str, Any]]:
    termo = _texto(termo)
    if not termo:
        return []
    rows = _metadata_read(fetch_all,
        queries.SQL_PRODUCAO_BUSCAR_OS,
        {
            "cod_empresa": 1,
            "busca": f"%{termo}%",
            "termo": termo,
            "limite": max(1, min(int(limite or 20), 50)),
        },
    )
    for row in rows:
        _remember_order(row)
    results = []
    for row in rows:
        result = _os_payload(row)
        exact_order = result["numero"].casefold() == termo.casefold()
        item_code = None if exact_order else row.get("matched_item_code")
        result.update(
            matched_item_code=item_code,
            matched_item_description=row.get("matched_item_description") if item_code else None,
            matched_budget_number=None if exact_order else row.get("matched_budget_number"),
        )
        results.append(result)
    return results


def listar_classificacoes_cronograma() -> list[str]:
    rows = _metadata_read(fetch_all, queries.SQL_CLASSIFICACOES, {"cod_empresa": 1})
    return sorted({_texto(row.get("classificacao")) for row in rows if _texto(row.get("classificacao"))}, key=str.casefold)


def listar_entregas_cronograma(mes: int, ano: int, classificacao: str = "", pesquisa: str = "") -> list[dict[str, Any]]:
    inicio = date(ano, mes, 1)
    fim = date(ano + (mes == 12), mes % 12 + 1, 1)
    rows = _metadata_read(fetch_all, queries.SQL_PRODUCAO_CRONOGRAMA_ENTREGAS, {
        "cod_empresa": 1,
        "inicio": inicio.isoformat(),
        "fim": fim.isoformat(),
        "classificacao": _texto(classificacao) or None,
        "pesquisa": f"%{_texto(pesquisa)}%" if _texto(pesquisa) else None,
    })
    deliveries: dict[int, dict[str, Any]] = {}
    for row in rows:
        budget_id = int(row["cod_orcamento"])
        delivery = deliveries.setdefault(budget_id, {
            "orcamento": _texto(row.get("n_orcamento")),
            "versao": _texto(row.get("versao")),
            "data_entrega": _iso(row.get("dt_previsao_entrega")),
            "cliente": "", "descricao": "", "classificacoes": [],
            "status": "", "percentual": None,
            "operacoes_total": 0, "operacoes_concluidas": 0,
            "os": [],
        })
        numero = _texto(row.get("n_os"))
        if not numero:
            continue
        if not delivery["cliente"]:
            delivery["cliente"] = _texto(row.get("cliente"))
        if not delivery["descricao"]:
            delivery["descricao"] = _texto(row.get("titulo"))
        classification = _texto(row.get("u_classificacao"))
        if classification and classification not in delivery["classificacoes"]:
            delivery["classificacoes"].append(classification)
        delivery["os"].append({
            "numero": numero,
            "descricao": _texto(row.get("titulo")),
            "principal": bool(row.get("principal")),
            "status": _texto(row.get("status_servico")),
        })
        delivery["operacoes_total"] += int(row.get("operacoes_total") or 0)
        delivery["operacoes_concluidas"] += int(row.get("operacoes_concluidas") or 0)
    for delivery in deliveries.values():
        total = delivery["operacoes_total"]
        completed = delivery["operacoes_concluidas"]
        if total:
            delivery["percentual"] = round(completed * 100 / total)
            delivery["status"] = "Concluído" if completed == total else "Em produção"
        elif delivery["os"]:
            delivery["status"] = delivery["os"][0]["status"] or "Sem operações"
        else:
            delivery["status"] = "OS não gerada"
    return list(deliveries.values())


def obter_dependencias(numero_os: str) -> dict[str, Any]:
    """Rastreia o orcamento e as OS geradas por solicitacoes reais do GRV."""
    ordem = _obter_ordem(_texto(numero_os))
    params = {"cod_empresa": 1, "cod_os": ordem["codigo"]}
    budget = _metadata_read(fetch_one, queries.SQL_PRODUCAO_ORCAMENTO_OS, params)
    budget_number = budget.get("budget_number") if budget else None
    rows = [dict(ordem, is_budget_order=False)]
    if budget_number is not None:
        rows = _metadata_read(fetch_all, queries.SQL_PRODUCAO_DEPENDENCIAS_OS,
                              {"cod_empresa": 1, "numero_orcamento": budget_number})
    nodes = {}
    links = set()
    for row in rows:
        code = int(row["codigo"])
        is_budget_order = bool(row.get("is_budget_order"))
        if code not in nodes or is_budget_order:
            nodes[code] = {
                "number": _texto(row.get("n_os")), "title": _texto(row.get("titulo")),
                "source_status": row.get("status_servico"), "due_date": _iso(row.get("dt_prevista")),
                "drawing_number": row.get("n_desenho"), "is_budget_order": is_budget_order,
            }
        dependent = row.get("dependent_cod_os")
        if dependent is not None and int(dependent) != code:
            links.add((int(dependent), code))
    return {
        "selected_order_number": _texto(ordem.get("n_os")),
        "budget_number": budget_number,
        "nodes": list(nodes.values()),
        "edges": [
            {"dependent_order_number": nodes[dependent]["number"],
             "prerequisite_order_number": nodes[prerequisite]["number"]}
            for dependent, prerequisite in sorted(links)
            if dependent in nodes and prerequisite in nodes
        ],
        "source": {"calculated_at": agora_br().isoformat()},
    }


def listar_os_abertas(limite: int = 100) -> list[dict[str, Any]]:
    rows = fetch_all(
        queries.SQL_PRODUCAO_OS_ABERTAS,
        {"cod_empresa": 1, "limite": max(1, min(int(limite or 100), 200))},
    )
    for row in rows:
        _remember_order(row)
    return [_os_payload(row) for row in rows]


def obter_estrutura(numero_os: str) -> dict[str, Any]:
    numero_os = _texto(numero_os)
    return _cached_read(
        _STRUCTURE_CACHE, _STRUCTURE_JOBS, _STRUCTURE_LOCK, numero_os,
        lambda: _carregar_estrutura(numero_os),
        _STRUCTURE_CACHE_TTL_SECONDS, _STRUCTURE_CACHE_LIMIT,
    )


def _carregar_estrutura(numero_os):
    ordem = _obter_ordem(numero_os)
    params = {"cod_empresa": 1, "cod_os": ordem["codigo"]}
    app = _scope()
    items_job = _STRUCTURE_EXECUTOR.submit(
        _run_with_app, app, _metadata_read, fetch_all,
        queries.SQL_PRODUCAO_ESTRUTURA_OS, params,
    )
    operations_job = _STRUCTURE_EXECUTOR.submit(
        _run_with_app, app, fetch_all, queries.SQL_PRODUCAO_OPERACOES_OS, params,
    )
    rncs_job = _STRUCTURE_EXECUTOR.submit(_run_with_app, app, _rncs, ordem["codigo"])
    payload = _estrutura_payload(ordem, items_job.result(), operations_job.result())
    if payload.get("avisos_estrutura") and has_app_context():
        current_app.logger.warning(
            "Estrutura GRV da OS %s: %s", numero_os, "; ".join(payload["avisos_estrutura"])
        )
    payload["rncs"] = rncs_job.result()
    return payload


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
    return {"aux_code": aux_code, "atualizado_em": agora_br().isoformat(), "apontamentos": [
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
        context = _contexto_previa(ordem, aux_code)
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
    rows = _metadata_read(fetch_all, queries.SQL_PRODUCAO_DOCUMENTOS_OS, {"cod_empresa": 1, "cod_os": ordem["codigo"]})
    return [
        _documento_payload(row, numero_os, aux_code, int(ordem["codigo"]), aux_code)
        for row in rows if row.get("cod_os_aux") is not None and int(row["cod_os_aux"]) == aux_code
    ]


def _contexto_previa(ordem: dict[str, Any], aux_code: int) -> dict[str, Any] | None:
    rows = _metadata_read(fetch_all, queries.SQL_PRODUCAO_ESTRUTURA_OS, {"cod_empresa": 1, "cod_os": ordem["codigo"]})
    item = next((row for row in rows if int(row["aux_code"]) == aux_code), None)
    if item is None:
        return None
    return {**item, "segmento": ordem.get("u_classificacao") or ordem.get("classificacao")}


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
        item_rows = _metadata_read(fetch_all,
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
            rows = _metadata_read(fetch_all,
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
        rows = _metadata_read(fetch_all,
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
    schema = _metadata_read(fetch_one, queries.SQL_PRODUCAO_ORIGEM_SCHEMA, {"cod_empresa": 1})
    if not schema or not schema.get("supported"):
        return None
    return fetch_one(
        queries.SQL_PRODUCAO_ITEM_ORIGEM,
        {"cod_empresa": 1, "cod_os": ordem["codigo"], "cod_os_aux": aux_code},
    )


def _drawing_segments(items: list[Any]) -> list[tuple[Any, Any]]:
    segments = []
    for item in items:
        if not item:
            continue
        if item[0] == "l" and len(item) >= 3:
            segments.append((item[1], item[2]))
        elif item[0] in {"re", "qu"} and len(item) >= 2:
            shape = item[1]
            vertices = (shape.tl, shape.tr, shape.br, shape.bl) if item[0] == "re" else (shape.ul, shape.ur, shape.lr, shape.ll)
            segments.extend(zip(vertices, vertices[1:] + vertices[:1]))
    return segments


def _line_stats(items: list[Any]) -> tuple[int, int, int]:
    segments = _drawing_segments(items)
    diagonals = sum(abs(float(end.x) - float(start.x)) > 1 and abs(float(end.y) - float(start.y)) > 1 for start, end in segments)
    curves = sum(bool(item) and item[0] == "c" for item in items)
    return len(segments), diagonals, curves


def _expand_rect(rect: Any, margin: float, bounds: Any) -> Any:
    if fitz is None:
        raise LookupError("Renderizador de PDF nao instalado")
    return fitz.Rect(
        max(bounds.x0, rect.x0 - margin),
        max(bounds.y0, rect.y0 - margin),
        min(bounds.x1, rect.x1 + margin),
        min(bounds.y1, rect.y1 + margin),
    )


def _projection_axes(items: list[Any]) -> set[int]:
    axes = set()
    for start, end in _drawing_segments(items):
        delta_x = float(end.x) - float(start.x)
        delta_y = float(end.y) - float(start.y)
        if math.hypot(delta_x, delta_y) < 8:
            continue
        direction = round((math.atan2(delta_y, delta_x) % math.pi) * 12 / math.pi) % 12
        if direction not in {0, 6}:
            axes.add(direction)
    return axes


def _curve_tangent_at(point: Any, direction: Any, curves: list[Any], tolerance: float) -> bool:
    length = abs(direction)
    if length == 0:
        return False
    for curve in curves:
        for endpoint, handle in ((curve[0], curve[1]), (curve[3], curve[2])):
            tangent = handle - endpoint
            tangent_length = abs(tangent)
            if abs(point - endpoint) <= tolerance and tangent_length > 0:
                alignment = abs(direction.x * tangent.x + direction.y * tangent.y) / (length * tangent_length)
                if alignment >= 0.97:
                    return True
    return False


def _is_cylindrical_view(segments: list[Any], curves: list[Any], bounds: Any) -> bool:
    if len(segments) < 2 or len(curves) < 4:
        return False
    tolerance = max(1, min(bounds.width, bounds.height) * 0.01)
    remaining = set(range(len(curves)))
    rims = []
    while remaining:
        initial = curves[remaining.pop()]
        vertices = [initial[0]]
        endpoint = initial[3]
        while abs(endpoint - vertices[0]) > tolerance:
            vertices.append(endpoint)
            following = next((index for index in remaining if min(abs(curves[index][0] - endpoint), abs(curves[index][3] - endpoint)) <= tolerance), None)
            if following is None:
                break
            curve = curves[following]
            remaining.remove(following)
            endpoint = curve[3] if abs(curve[0] - endpoint) <= tolerance else curve[0]
        if abs(endpoint - vertices[0]) > tolerance or len(vertices) < 4:
            continue
        center_x = sum(vertex.x for vertex in vertices) / len(vertices)
        center_y = sum(vertex.y for vertex in vertices) / len(vertices)
        spread_x = sum((vertex.x - center_x) ** 2 for vertex in vertices)
        spread_y = sum((vertex.y - center_y) ** 2 for vertex in vertices)
        covariance = sum((vertex.x - center_x) * (vertex.y - center_y) for vertex in vertices)
        if math.hypot(spread_x - spread_y, 2 * covariance) > (spread_x + spread_y) * 0.2:
            rims.append(vertices)
    if not rims:
        return False
    generators = []
    for start, end in segments:
        direction = end - start
        length = abs(direction)
        if length < max(16, max(bounds.width, bounds.height) * 0.2):
            continue
        if not all(_curve_tangent_at(endpoint, direction, curves, tolerance) for endpoint in (start, end)):
            continue
        touched_rims = {index for index, rim in enumerate(rims) if any(min(abs(start - vertex), abs(end - vertex)) <= tolerance for vertex in rim)}
        if not touched_rims:
            continue
        axis = direction / length
        for previous_start, previous_end, previous_rims in generators:
            previous = previous_end - previous_start
            previous_length = abs(previous)
            if not touched_rims.intersection(previous_rims) or min(length, previous_length) < max(length, previous_length) * 0.6:
                continue
            if abs(axis.x * previous.y - axis.y * previous.x) > previous_length * 0.05:
                continue
            offset = previous_start - start
            separation = abs(axis.x * offset.y - axis.y * offset.x)
            if separation < max(4, min(bounds.width, bounds.height) * 0.15):
                continue
            projections = [axis.x * (point.x - start.x) + axis.y * (point.y - start.y) for point in (previous_start, previous_end)]
            overlap = min(length, max(projections)) - max(0, min(projections))
            if overlap >= min(length, previous_length) * 0.7:
                return True
        generators.append((start, end, touched_rims))
    return False


def _dimension_segments(drawings: list[dict[str, Any]], labels: list[Any]) -> list[tuple[Any, Any]]:
    curves = [item[1:5] for drawing in drawings for item in drawing.get("items") or [] if item[0] == "c"]
    segments = []
    for drawing in drawings:
        for item in drawing.get("items") or []:
            if item[0] != "l":
                continue
            start, end = item[1:3]
            if abs(end - start) >= 12 and any(label.contains(start) or label.contains(end) for label in labels):
                if not all(_curve_tangent_at(endpoint, end - start, curves, 1.5) for endpoint in (start, end)):
                    segments.append((start, end))
    return segments


def _without_dimension_items(items: list[Any], dimensions: list[tuple[Any, Any]]) -> list[Any]:
    filtered = []
    for item in items:
        if item[0] == "l":
            start, end = item[1:3]
            dimension = any(
                (start == first and end == last)
                or (abs(end - start) <= 12 and min(abs(start - first), abs(start - last), abs(end - first), abs(end - last)) <= 1)
                for first, last in dimensions
            )
            if dimension:
                continue
        filtered.append(item)
    return filtered


def _merge_drawing_records(records: list[dict[str, Any]], page_rect: Any, margin: float) -> list[dict[str, Any]]:
    if fitz is None:
        raise LookupError("Renderizador de PDF nao instalado")
    groups = [{**record, "rect": fitz.Rect(record["rect"])} for record in records]
    if len(groups) < 2:
        return groups
    cell_size = max(1.0, margin * 2, min(page_rect.width, page_rect.height) / math.sqrt(len(groups)))
    cells = defaultdict(set)
    occupied = {}
    active = set(range(len(groups)))
    pending = list(range(len(groups)))
    queued = set(pending)

    def cell_keys(rect):
        return {
            (cell_x, cell_y)
            for cell_x in range(math.floor(rect.x0 / cell_size), math.floor(rect.x1 / cell_size) + 1)
            for cell_y in range(math.floor(rect.y0 / cell_size), math.floor(rect.y1 / cell_size) + 1)
        }

    def register(index):
        occupied[index] = cell_keys(groups[index]["rect"])
        for cell in occupied[index]:
            cells[cell].add(index)

    def remove(index):
        for cell in occupied.pop(index):
            cells[cell].discard(index)
            if not cells[cell]:
                del cells[cell]

    def neighbors(rect):
        return {index for cell in cell_keys(rect) for index in cells.get(cell, ())}

    for index in pending:
        register(index)
    while pending:
        index = heapq.heappop(pending)
        queued.remove(index)
        if index not in active:
            continue
        group = groups[index]
        expanded = _expand_rect(group["rect"], margin, page_rect)
        other_index = next((
            candidate for candidate in sorted(neighbors(expanded))
            if candidate > index and expanded.intersects(groups[candidate]["rect"])
        ), None)
        if other_index is None:
            continue
        other = groups[other_index]
        remove(index)
        remove(other_index)
        active.remove(other_index)
        group["rect"] |= other["rect"]
        for key in ("lines", "diagonals", "curves", "paths"):
            group[key] += other[key]
        if "axes" in group:
            group["axes"] = group["axes"] | other["axes"]
        if "segments" in group:
            group["segments"] = group["segments"] + other["segments"]
            group["beziers"] = group["beziers"] + other["beziers"]
        register(index)
        expanded = _expand_rect(group["rect"], margin, page_rect)
        for candidate in neighbors(expanded) | {index}:
            if candidate <= index and candidate not in queued:
                heapq.heappush(pending, candidate)
                queued.add(candidate)
    return [groups[index] for index in sorted(active)]


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


def _projection_label(value: Any) -> bool:
    normalized = _normalizar_identificador(value)
    return "ISOMETR" in normalized or "EXPLOD" in normalized


def _assembly_view_rect(group: dict[str, Any], groups: list[dict[str, Any]], page_rect: Any) -> tuple[Any, int]:
    if fitz is None:
        raise LookupError("Renderizador de PDF nao instalado")
    bounds = group["rect"]
    if len(group["axes"]) < 2 or group["diagonals"] < 3:
        return bounds, 0
    combined = fitz.Rect(bounds)
    area = bounds.width * bounds.height
    included = set()
    changed = True
    while changed:
        changed = False
        for index, other in enumerate(groups):
            if other is group or index in included or other["word_count"]:
                continue
            rect = other["rect"]
            other_area = rect.width * rect.height
            if other_area > area * 0.65:
                continue
            compatible_axes = len(group["axes"] & other["axes"]) >= 2 and other["diagonals"] >= 2
            small_component = other["curves"] >= 1 and other_area <= area * 0.1
            if not compatible_axes and not small_component:
                continue
            overlap_x = max(0, min(combined.x1, rect.x1) - max(combined.x0, rect.x0))
            overlap_y = max(0, min(combined.y1, rect.y1) - max(combined.y0, rect.y0))
            gap_x = max(rect.x0 - combined.x1, combined.x0 - rect.x1, 0)
            gap_y = max(rect.y0 - combined.y1, combined.y0 - rect.y1, 0)
            aligned = (
                overlap_x >= min(bounds.width, rect.width) * 0.7 and gap_y <= max(bounds.width, bounds.height) * 0.55
            ) or (
                overlap_y >= min(bounds.height, rect.height) * 0.7 and gap_x <= max(bounds.width, bounds.height) * 0.55
            )
            joined = combined | rect
            if not aligned or max(joined.width, joined.height) > max(bounds.width, bounds.height) * 2.5:
                continue
            if joined.width * joined.height > page_rect.width * page_rect.height * 0.55:
                continue
            combined = joined
            included.add(index)
            changed = True
    return combined, len(included)


def _page_isometric_candidates(page: Any) -> list[tuple[float, Any, bool]]:
    if fitz is None:
        raise LookupError("Renderizador de PDF nao instalado")
    page_rect = page.rect
    page_area = max(page_rect.width * page_rect.height, 1)
    text_blocks = page.get_text("blocks")
    text_words = page.get_text("words")
    dimension_labels = [
        _expand_rect(fitz.Rect(word[:4]), max(4, (word[3] - word[1]) * 0.35), page_rect)
        for word in text_words
        if re.fullmatch(r"(?:[\u00d8\u00f8\u2300\u2205RrDdMm]\s*)?\d+(?:[.,]\d+)?(?:mm|\u00b0)?", str(word[4]).strip())
    ]
    drawings = page.get_drawings()
    dimensions = _dimension_segments(drawings, dimension_labels)
    records = []
    for drawing in drawings:
        original_items = drawing.get("items") or []
        items = _without_dimension_items(original_items, dimensions)
        if not items:
            continue
        rect = fitz.Rect(drawing.get("rect"))
        if len(items) != len(original_items):
            points = [point for segment in _drawing_segments(items) for point in segment]
            points.extend(point for item in items if item[0] == "c" for point in item[1:5])
            if not points:
                continue
            rect = fitz.Rect(min(point.x for point in points), min(point.y for point in points),
                             max(point.x for point in points), max(point.y for point in points))
        if rect.width == 0 or rect.height == 0:
            rect = _expand_rect(rect, max(0.5, float(drawing.get("width") or 0) / 2), page_rect)
        rect &= page_rect
        coverage = (rect.width * rect.height) / page_area
        sheet_rule = (
            rect.width > page_rect.width * 0.8 and rect.height < 2
            and (rect.y0 < page_rect.y0 + page_rect.height * 0.1 or rect.y1 > page_rect.y1 - page_rect.height * 0.1)
        ) or (
            rect.height > page_rect.height * 0.8 and rect.width < 2
            and (rect.x0 < page_rect.x0 + page_rect.width * 0.1 or rect.x1 > page_rect.x1 - page_rect.width * 0.1)
        )
        if rect.is_empty or coverage < 0.00002 or coverage > 0.82 or sheet_rule:
            continue
        lines, diagonals, curves = _line_stats(items)
        color = drawing.get("color") or drawing.get("fill") or (0, 0, 0)
        annotation = (max(color) - min(color) > 0.3) or bool(re.search(r"\[\s*\d", str(drawing.get("dashes") or "")))
        records.append({"rect": rect, "lines": lines, "diagonals": diagonals, "curves": curves, "paths": 1,
                "axes": _projection_axes(items), "annotation": annotation,
                "segments": _drawing_segments(items), "beziers": [item[1:5] for item in items if item[0] == "c"]})

    neutral_records = [record for record in records if not record["annotation"]]
    if len(neutral_records) >= 3:
        records = neutral_records
    join_margin = max(7.0, min(page_rect.width, page_rect.height) * 0.02)
    groups = _merge_drawing_records(records, page_rect, join_margin)
    labels = [
        fitz.Rect(block[:4])
        for block in text_blocks
        if len(block) > 4 and _projection_label(block[4])
    ]
    for group in groups:
        group["word_count"] = sum(
            group["rect"].x0 <= (word[0] + word[2]) / 2 <= group["rect"].x1
            and group["rect"].y0 <= (word[1] + word[3]) / 2 <= group["rect"].y1
            for word in text_words
        )
    candidates = []
    for group in groups:
        rect = group["rect"]
        coverage = (rect.width * rect.height) / page_area
        diagonal_ratio = group["diagonals"] / max(group["lines"], 1)
        aspect_ratio = max(rect.width, rect.height) / max(min(rect.width, rect.height), 1)
        word_count = group["word_count"]
        if word_count >= 8 and group["diagonals"] < 3 and group["curves"] < 2:
            continue
        contains_text = word_count > 0
        main_profile = (
            aspect_ratio >= 4 and max(rect.width, rect.height) >= max(page_rect.width, page_rect.height) * 0.3
            and group["lines"] >= 3 and not contains_text
        )
        if coverage < 0.008 or coverage > 0.72:
            continue
        if group["diagonals"] < 2 and group["curves"] < 1 and group["paths"] < 6 and not main_profile:
            continue
        label_score, labeled = _isometric_label_score(rect, labels, page_rect)
        score = diagonal_ratio * 8 + min(group["paths"], 40) / 12 + min(group["curves"], 8) * 0.35 + min(coverage, 0.35) * 4 + label_score
        if len(group["axes"]) >= 2 and group["diagonals"] >= 3:
            score += 3
        if _is_cylindrical_view(group["segments"], group["beziers"], rect):
            score += 4
        score -= min(word_count / 4, 4)
        if main_profile:
            score += 5 + min(aspect_ratio, 20) / 5
        if rect.y0 > page_rect.height * 0.72 and rect.x0 > page_rect.width * 0.55:
            score -= 4
        rect, joined_components = _assembly_view_rect(group, groups, page_rect)
        score += min(joined_components * 3, 9)
        padding = max(2, min(rect.width, rect.height) * 0.1) if main_profile else max(rect.width, rect.height) * 0.06
        clip = _expand_rect(rect, padding, page_rect)
        for word in text_words:
            if not clip.intersects(fitz.Rect(word[:4])):
                continue
            if word[1] > rect.y1 + 1:
                clip.y1 = min(clip.y1, word[1] - 1)
            elif word[3] < rect.y0 - 1:
                clip.y0 = max(clip.y0, word[3] + 1)
            elif word[0] > rect.x1 + 1:
                clip.x1 = min(clip.x1, word[0] - 1)
            elif word[2] < rect.x0 - 1:
                clip.x0 = max(clip.x0, word[2] + 1)
        candidates.append((score, clip, labeled))

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
            from .production_images import _largest_placed_image, render_variants

            analyzed_pages = {}
            labeled = []
            for page_index in range(pdf.page_count):
                page = pdf.load_page(page_index)
                if any(len(block) > 4 and _projection_label(block[4]) for block in page.get_text("blocks")):
                    analyzed_pages[page_index] = _page_isometric_candidates(page)
                    labeled.extend((score, page_index, rect) for score, rect, is_labeled in analyzed_pages[page_index] if is_labeled)
            # CAD PDFs can contain a shaded rendering alongside vector dimensions.
            # Match Estrutura: an explicit isometric view takes priority, then the
            # embedded rendering, before scoring unlabelled technical linework.
            if labeled:
                _, page_index, clip = max(labeled, key=lambda candidate: candidate[0])
                return _render_clip_previews(pdf.load_page(page_index), clip)
            for page in pdf:
                image = _largest_placed_image(pdf, page)
                if image is not None:
                    return render_variants(image)
            candidates = []
            for page_index in range(pdf.page_count):
                if page_index not in analyzed_pages:
                    analyzed_pages[page_index] = _page_isometric_candidates(pdf.load_page(page_index))
                candidates.extend((score, page_index, rect, is_labeled) for score, rect, is_labeled in analyzed_pages[page_index])
            candidates.sort(key=lambda candidate: candidate[0], reverse=True)
            ambiguous = len(candidates) > 1 and candidates[0][0] - candidates[1][0] < 0.08 and not candidates[0][3]
            if not candidates or candidates[0][0] < 1.5 or ambiguous:
                for page in pdf:
                    if any(drawing.get("items") for drawing in (page.get_cdrawings() or [])):
                        return _render_document_previews(page)
                raise LookupError("Vista isometrica nao identificada com confianca")
            _, page_index, clip, _ = candidates[0]
            page = pdf.load_page(page_index)
            return _render_clip_previews(page, clip)
    except LookupError:
        raise
    except Exception as exc:
        raise LookupError("Documento PDF invalido ou ilegivel") from exc


def _render_document_previews(page: Any) -> dict[str, bytes]:
    if fitz is None:
        raise LookupError("Renderizador de PDF nao instalado")
    from PIL import Image, ImageDraw, ImageFont, PngImagePlugin

    scale = min(2.0, 1568 / max(page.rect.width, page.rect.height))
    pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
    source = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
    label = f"DESENHO ORIGINAL - PAGINA {page.number + 1}"
    metadata = PngImagePlugin.PngInfo()
    metadata.add_text("preview_kind", "original-document")
    metadata.add_text("page_number", str(page.number + 1))
    previews = {}
    for variant, maximum in (("thumbnail", 720), ("detail", 1600)):
        header_height = max(40, round(maximum * 0.06))
        font_size = max(16, round(maximum * 0.032))
        font = ImageFont.load_default(size=font_size)
        image = source.copy()
        image.thumbnail((maximum, maximum - header_height), Image.Resampling.LANCZOS)
        label_width = math.ceil(font.getlength(label)) + 16
        canvas = Image.new("RGB", (max(image.width, label_width), image.height + header_height), "white")
        canvas.paste(image, ((canvas.width - image.width) // 2, header_height))
        drawing = ImageDraw.Draw(canvas)
        drawing.text((8, (header_height - font_size) // 2), label, fill=(40, 50, 60), font=font)
        drawing.line((0, header_height - 1, canvas.width, header_height - 1), fill=(210, 215, 220))
        output = io.BytesIO()
        canvas.save(output, format="PNG", pnginfo=metadata)
        previews[variant] = output.getvalue()
    return previews


def _render_clip_previews(page: Any, clip: Any) -> dict[str, bytes]:
    if fitz is None:
        raise LookupError("Renderizador de PDF nao instalado")
    from PIL import Image
    from .production_images import render_variants

    scale = max(1.5, min(300 / 72, 1800 / max(clip.width, clip.height)))
    pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), clip=clip, alpha=False)
    image = Image.open(io.BytesIO(pixmap.tobytes("png"))).convert("RGB")
    return render_variants(image, preserve_components=True)


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
            _PREVIEW_FAILURES[cache_key] = (time.monotonic(), str(exc))
            _PREVIEW_FAILURES.move_to_end(cache_key)
            while len(_PREVIEW_FAILURES) > _PREVIEW_CACHE_LIMIT:
                _PREVIEW_FAILURES.popitem(last=False)
        return
    with _PREVIEW_LOCK:
        _PREVIEW_JOBS.pop(cache_key, None)
        _PREVIEW_FAILURES.pop(cache_key, None)
        _PREVIEW_CACHE[cache_key] = previews
        _PREVIEW_CACHE.move_to_end(cache_key)
        while len(_PREVIEW_CACHE) > _PREVIEW_CACHE_LIMIT:
            _PREVIEW_CACHE.popitem(last=False)


def _previews_em_cache(
    cache_key: str,
    content: bytes = b"",
    filename: str = "",
    wait: bool = True,
    *,
    document_loader: Callable[[], tuple[bytes, str]] | None = None,
    preview_loader: Callable[[], dict[str, bytes] | None] | None = None,
) -> dict[str, bytes] | None:
    with _PREVIEW_LOCK:
        cached = _PREVIEW_CACHE.get(cache_key)
        if cached is not None:
            _PREVIEW_CACHE.move_to_end(cache_key)
            return cached
        failure = _PREVIEW_FAILURES.get(cache_key)
        if failure:
            if time.monotonic() - failure[0] <= _PREVIEW_FAILURE_TTL_SECONDS:
                raise LookupError(failure[1])
            _PREVIEW_FAILURES.pop(cache_key, None)
        future = _PREVIEW_JOBS.get(cache_key)
        owner = future is None
        if future is None:
            future = Future()
            _PREVIEW_JOBS[cache_key] = future
            future.add_done_callback(lambda completed: _finalizar_preview(cache_key, completed))

    if owner:
        def render():
            try:
                result = preview_loader() if preview_loader is not None else None
                if result is None:
                    payload, name = document_loader() if document_loader is not None else (content, filename)
                    result = _PREVIEW_EXECUTOR.submit(_gerar_previews, payload, name).result()
                future.set_result(result)
            except BaseException as exc:
                future.set_exception(exc)

        if wait:
            render()
        else:
            _PREVIEW_REQUEST_EXECUTOR.submit(_run_with_app, _scope(), render)
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
    request_key = f"{id(_scope())}|{_texto(numero_os)}|{int(aux_code)}|{variant}"
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
        future = _PREVIEW_REQUEST_JOBS.get(request_key)
        if future is None:
            limit = _PREVIEW_DETAIL_CONCURRENCY if variant == "detail" else _PREVIEW_REQUEST_CONCURRENCY
            active = sum(key.endswith(f"|{variant}") for key in _PREVIEW_REQUEST_JOBS)
            if active < limit:
                executor = _PREVIEW_DETAIL_EXECUTOR if variant == "detail" else _PREVIEW_REQUEST_EXECUTOR
                future = executor.submit(
                    _run_with_app, _scope(), obter_preview, numero_os, aux_code, variant, True
                )
                _PREVIEW_REQUEST_JOBS[request_key] = future
                future.add_done_callback(lambda completed: _finalizar_requisicao_preview(request_key, completed))
    if future is not None and future.done():
        return future.result()
    pending_etag = hashlib.sha256(request_key.encode("utf-8")).hexdigest()
    return None, "image/png", pending_etag


def obter_preview(numero_os: str, aux_code: int, variant: str = "thumbnail", wait: bool = True) -> tuple[bytes | None, str, str]:
    if variant not in {"thumbnail", "detail"}:
        raise LookupError("Variante de previa invalida")
    if not wait:
        return _obter_preview_assincrono(numero_os, aux_code, variant)
    ordem = _obter_ordem(numero_os)
    context = _contexto_previa(ordem, aux_code)
    if not context:
        raise LookupError("Item nao encontrado")
    document, _ = _resolver_documento_previa(ordem, numero_os, aux_code, context)
    if not document:
        raise LookupError("Previa indisponivel: documento esperado ausente ou ambiguo")

    def load_document():
        return obter_arquivo(
            numero_os,
            aux_code,
            str(document["source_kind"]),
            int(document["id"]),
            ordem=ordem,
            source_cod_os=int(document["source_cod_os"]),
            source_aux_code=int(document["source_aux_code"]),
        )

    identity = (
        _PREVIEW_CACHE_VERSION,
        str(id(_scope())),
        str(document.get("source_kind")),
        str(document.get("id")),
        str(document.get("source_cod_os")),
        str(document.get("source_aux_code")),
        str(document.get("content_revision") or "unknown"),
        _texto(context.get("revisao_desenho")),
    )
    if document.get("content_revision"):
        cache_key = hashlib.sha256("|".join(identity).encode("utf-8")).hexdigest()
        previews = _previews_em_cache(
            cache_key, document_loader=load_document,
            preview_loader=lambda: obter_previews_bridge(document, _PREVIEW_CACHE_VERSION),
        )
    else:
        content, filename = load_document()
        identity += (hashlib.sha256(content).hexdigest(),)
        cache_key = hashlib.sha256("|".join(identity).encode("utf-8")).hexdigest()
        previews = _previews_em_cache(cache_key, content, filename)
    return previews[variant] if previews else None, "image/png", cache_key


def obter_thumbnail(numero_os: str, aux_code: int) -> tuple[bytes, str]:
    content, media_type, _ = obter_preview(numero_os, aux_code, "thumbnail")
    if content is None:  # pragma: no cover - a chamada bloqueante sempre produz conteudo
        raise LookupError("Previa ainda em processamento")
    return content, media_type


def _obter_ordem(numero_os: str) -> dict[str, Any]:
    ordem = _metadata_read(fetch_one, queries.SQL_PRODUCAO_OBTER_OS, {"cod_empresa": 1, "numero_os": numero_os})
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
    # os_pai e a chave estrutural do GRV. A consulta ja traz todos os itens da
    # OS; normalizamos a floresta uma vez, sem inferir parentesco pelo codigo.
    itens_por_id: dict[int, dict[str, Any]] = {}
    warnings: list[str] = []
    for item in itens:
        item_id = int(item["aux_code"])
        if item_id in itens_por_id:
            warnings.append(f"Item estrutural duplicado: {item_id}")
            continue
        itens_por_id[item_id] = item

    parent_by_id: dict[int, int | None] = {}
    for item_id, item in itens_por_id.items():
        raw_parent = item.get("os_pai")
        try:
            parent = int(raw_parent) if raw_parent not in (None, "", 0, "0") else None
        except (TypeError, ValueError):
            warnings.append(f"Pai inválido do item {item_id}: {raw_parent}")
            parent = None
        if parent == item_id:
            warnings.append(f"Item {item_id} aponta para si mesmo em os_pai")
            parent = None
        elif parent is not None and parent not in itens_por_id:
            warnings.append(f"Pai {parent} do item {item_id} ausente na OS")
            parent = None
        parent_by_id[item_id] = parent

    resolved: set[int] = set()
    for item_id in itens_por_id:
        chain: list[int] = []
        seen: set[int] = set()
        current: int | None = item_id
        while current is not None and current not in resolved:
            if current in seen:
                warnings.append(f"Ciclo estrutural em os_pai envolvendo o item {current}")
                parent_by_id[current] = None
                break
            seen.add(current)
            chain.append(current)
            current = parent_by_id[current]
        resolved.update(chain)

    filhos: dict[int, list[int]] = defaultdict(list)
    for item_id, parent in parent_by_id.items():
        if parent is not None:
            filhos[parent].append(item_id)
    roots = [item_id for item_id, parent in parent_by_id.items() if parent is None]
    if len(roots) > 1:
        warnings.append(f"OS com {len(roots)} raízes estruturais independentes")
    ops_por_item: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for operation in operacoes:
        ops_por_item[int(operation["cod_os_aux"])].append(operation)

    states: dict[int, str] = {}
    def derive(item_id: int) -> str:
        item = itens_por_id[item_id]
        item_ops = ops_por_item.get(item_id, [])
        finished = sum(bool(op.get("finalizado") or op.get("concluido") or op.get("dt_finalizacao")) for op in item_ops)
        children = [states[child_id] for child_id in filhos.get(item_id, [])]
        assembly_ops = [op for op in item_ops if "MONTAGEM" in re.sub(r"[^\w]+", " ", _texto(op.get("tiposervico")).upper()).split()]
        productive_ops = [op for op in item_ops if op not in assembly_ops]
        has_assembly = bool(assembly_ops)
        productive_finished = all(op.get("finalizado") or op.get("concluido") or op.get("dt_finalizacao") for op in productive_ops)
        def running(operations):
            return any((op.get("data_inicio") or op.get("pcp_dt_primeiro_apont") or _horas_positivas(op.get("hs_realizadas")))
                       and not (op.get("finalizado") or op.get("concluido") or op.get("dt_finalizacao"))
                       for op in operations)
        # O encerramento registrado na OS prevalece sobre o roteiro produtivo.
        if _item_concluido(item):
            state = "concluido"
        elif item_ops and finished == len(item_ops):
            state = "concluido" if has_assembly else "disponivel"
        elif any(op.get("processo_travado") and not (op.get("finalizado") or op.get("concluido") or op.get("dt_finalizacao")) for op in item_ops):
            state = "bloqueado"
        elif running(assembly_ops):
            state = "montagem"
        elif running(productive_ops):
            state = "fabricacao"
        elif has_assembly and productive_finished and children and all(child in {"disponivel", "concluido"} for child in children):
            state = "disponivel"
        elif finished:
            # Uma pausa entre etapas nao apaga o progresso da peca.
            state = "montagem" if any(op.get("finalizado") or op.get("concluido") or op.get("dt_finalizacao") for op in assembly_ops) else "fabricacao"
        elif item_ops:
            state = "nao_iniciado"
        else:
            state = "nao_iniciado"
        states[item_id] = state
        return state

    # Pós-ordem iterativa: suporta estruturas profundas sem estourar a pilha.
    stack = [(root_id, False) for root_id in reversed(roots)]
    while stack:
        item_id, ready = stack.pop()
        if ready:
            derive(item_id)
            continue
        stack.append((item_id, True))
        stack.extend((child_id, False) for child_id in reversed(filhos.get(item_id, [])))

    nodes = []
    for item_id, item in itens_por_id.items():
        state = states[item_id]
        item_operations = ops_por_item.get(item_id, [])
        parent_id = parent_by_id[item_id]
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
            "predecessor_ids": list(dict.fromkeys(str(item[key]) for key in ("predecessora1", "predecessora2")
                                                  if item.get(key) is not None and int(item[key]) in itens_por_id and int(item[key]) != item_id)),
            "estado": state,
            "estado_label": STATUS_LABELS[state],
            "estado_motivo": "Item concluido conforme status da OS" if _item_concluido(item) else _motivo(state, item_operations),
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
        "avisos_estrutura": warnings,
    }


def _item_concluido(item: dict[str, Any]) -> bool:
    status = unicodedata.normalize("NFKD", _texto(item.get("status")))
    status = " ".join(status.encode("ascii", "ignore").decode().upper().split())
    return status in {"CONCLUIDO", "CONCLUIDA", "FINALIZADO", "FINALIZADA"}


def _horas_positivas(value: Any) -> bool:
    try:
        return float(str(value).replace(",", ".")) > 0
    except (TypeError, ValueError):
        return False


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
        return "Fabricacao iniciada, com etapas ainda pendentes"
    return "Processo cadastrado, mas ainda nao iniciado"
