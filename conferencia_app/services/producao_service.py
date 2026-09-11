"""Nucleo nativo do modulo de Producao, usando o banco/bridge do Sync."""
from __future__ import annotations

from collections import OrderedDict, defaultdict
from concurrent.futures import Future, ThreadPoolExecutor
import base64
import hashlib
import json
import logging
import threading
from datetime import date, datetime
from typing import Any

from ..compras import queries
from ..compras.db import fetch_all, fetch_one
from ..compras.config import get_settings
from . import producao_documentos as documents_domain
from . import producao_render

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

_PREVIEW_CACHE_VERSION = documents_domain.RENDER_VERSION
_logger = logging.getLogger(__name__)
_PREVIEW_CACHE_LIMIT = 64
_PREVIEW_CACHE: OrderedDict[str, dict[str, bytes]] = OrderedDict()
_PREVIEW_JOBS: dict[str, Future[dict[str, bytes]]] = {}
_PREVIEW_LOCK = threading.RLock()
_DOCUMENT_LOCKS = tuple(threading.RLock() for _index in range(64))
_PREVIEW_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="production-preview")


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
            "cod_empresa": _empresa(),
            "busca": f"%{termo}%",
            "termo": termo,
            "limite": max(1, min(int(limite or 20), 50)),
        },
    )
    return [_os_payload(row) for row in rows]


def listar_os_abertas(limite: int = 100) -> list[dict[str, Any]]:
    rows = fetch_all(
        queries.SQL_PRODUCAO_OS_ABERTAS,
        {"cod_empresa": _empresa(), "limite": max(1, min(int(limite or 100), 200))},
    )
    return [_os_payload(row) for row in rows]


def obter_estrutura(numero_os: str) -> dict[str, Any]:
    ordem = _obter_ordem(numero_os)
    itens = fetch_all(
        queries.SQL_PRODUCAO_ESTRUTURA_OS,
        {"cod_empresa": _empresa(), "cod_os": ordem["codigo"]},
    )
    operacoes = fetch_all(
        queries.SQL_PRODUCAO_OPERACOES_OS,
        {"cod_empresa": _empresa(), "cod_os": ordem["codigo"]},
    )
    payload = _estrutura_payload(ordem, itens, operacoes)
    payload["documents_available"] = True
    try:
        _, sources = _document_sources(ordem, numero_os, itens)
    except Exception as exc:
        sources = {}
        payload["documents_available"] = False
        _logger.warning("producao_documentos_indisponiveis error_type=%s", type(exc).__name__)
    for node in payload["nos"]:
        source = sources.get(node["aux_code"])
        node["has_drawing"] = source is not None
        node["thumbnail_url"] = documents_domain.thumbnail_url(numero_os, node["aux_code"], source)
    payload["rncs"] = _rncs(ordem["codigo"])
    return payload


def obter_materiais(numero_os: str, aux_code: int) -> dict[str, Any]:
    ordem = _obter_ordem(numero_os)
    rows = fetch_all(
        queries.SQL_PRODUCAO_MATERIAIS_ITEM,
        {"cod_empresa": _empresa(), "cod_os": ordem["codigo"], "cod_os_aux": aux_code},
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
        {"cod_empresa": _empresa(), "cod_os": ordem["codigo"], "cod_os_aux": aux_code},
    )
    return {"aux_code": aux_code, "atualizado_em": datetime.now().isoformat(), "apontamentos": [
        {"operacao": _texto(row.get("operation_code")), "sequencia": row.get("seq_processo_prod"), "operador": _texto(row.get("operator_name")), "inicio": _iso(row.get("started_at")), "maquina": _texto(row.get("machine")), "pausado": bool(row.get("paused"))}
        for row in rows
    ]}


def obter_documentos(numero_os: str, aux_code: int) -> list[dict[str, Any]]:
    ordem = _obter_ordem(numero_os)
    context = fetch_one(
        queries.SQL_PRODUCAO_ITEM_PREVIEW_CONTEXT,
        {"cod_empresa": _empresa(), "cod_os": ordem["codigo"], "cod_os_aux": aux_code},
    )
    if not context:
        raise LookupError("Item nao encontrado")
    all_documents, sources = _document_sources(ordem, numero_os)
    documents = [doc for doc in all_documents if doc["aux_code"] == aux_code]
    selected = sources.get(aux_code)
    if selected and selected["aux_code"] != aux_code:
        documents.append({**selected, "inherited": True})
    origin = _origem(ordem, aux_code)
    if origin:
        rows = fetch_all(queries.SQL_PRODUCAO_DOCUMENTOS_ITEM, {"cod_empresa": _empresa(), **origin})
        documents.extend(_document_payload(row, numero_os, aux_code, origin=True) for row in rows)
    for document in documents:
        document["is_primary"] = bool(
            selected
            and document["open_url"] == selected["open_url"]
        )
    return documents


def _obter_documentos_ordem(ordem: dict[str, Any], numero_os: str, aux_code: int) -> list[dict[str, Any]]:
    rows = fetch_all(queries.SQL_PRODUCAO_DOCUMENTOS_ITEM, {"cod_empresa": _empresa(), "cod_os": ordem["codigo"], "cod_os_aux": aux_code})
    return [_document_payload(row, numero_os, aux_code) for row in rows]


def _empresa() -> int:
    return get_settings().PG_COD_EMPRESA


def _document_payload(row: dict, numero_os: str, aux_code: int, origin: bool = False) -> dict:
    kind = row["kind"]
    return {
        "id": row["document_id"], "aux_code": aux_code, "source_kind": kind,
        "kind": "attachment" if kind == "image" else kind,
        "filename": documents_domain.safe_filename(row.get("nome_arquivo")),
        "description": _texto(row.get("descricao")), "size_bytes": row.get("size_bytes") or 0,
        "content_revision": row.get("content_revision"), "from_origin": origin,
        "open_url": documents_domain.document_url(numero_os, aux_code, kind, row["document_id"], origin),
    }


def _document_sources(ordem: dict, numero_os: str, itens: list[dict] | None = None) -> tuple[list[dict], dict]:
    params = {"cod_empresa": _empresa(), "cod_os": ordem["codigo"]}
    if itens is None:
        itens = fetch_all(queries.SQL_PRODUCAO_ESTRUTURA_OS, params)
    rows = fetch_all(queries.SQL_PRODUCAO_DOCUMENTOS_OS, params)
    documents = [_document_payload(row, numero_os, int(row["cod_os_aux"])) for row in rows]
    return documents, documents_domain.resolve_sources(itens, documents, ordem)


def _origem(ordem: dict, aux_code: int) -> dict | None:
    schema = fetch_one(queries.SQL_PRODUCAO_ORIGEM_SCHEMA, {"cod_empresa": _empresa()})
    if not schema or not schema.get("supported"):
        return None
    return fetch_one(queries.SQL_PRODUCAO_ITEM_ORIGEM, {"cod_empresa": _empresa(), "cod_os": ordem["codigo"], "cod_os_aux": aux_code})


def obter_arquivo(numero_os: str, aux_code: int, kind: str, document_id: int, origin: bool = False) -> tuple[bytes, str]:
    ordem = _obter_ordem(numero_os)
    params = {"cod_empresa": _empresa(), "cod_os": ordem["codigo"], "cod_os_aux": aux_code}
    if not fetch_one(queries.SQL_PRODUCAO_ITEM_PREVIEW_CONTEXT, params):
        raise LookupError("Item nao encontrado")
    if origin:
        source = _origem(ordem, aux_code)
        if not source:
            raise LookupError("Origem invalida")
        params.update(source)
    query = {"drawing": queries.SQL_PRODUCAO_DESENHO_ARQUIVO, "attachment": queries.SQL_PRODUCAO_ANEXO_ARQUIVO, "image": queries.SQL_PRODUCAO_IMAGEM_ARQUIVO}.get(kind)
    if query is None:
        raise LookupError("Tipo de documento invalido")
    metadata = fetch_all(queries.SQL_PRODUCAO_DOCUMENTOS_ITEM, params)
    document = next((doc for doc in metadata if doc["kind"] == kind and doc["document_id"] == document_id), None)
    if document is None:
        raise LookupError("Documento nao encontrado")
    from flask import current_app, has_app_context
    limit = int(current_app.config.get("PRODUCAO_DOCUMENT_MAX_BYTES", 20 * 1024 * 1024)) if has_app_context() else 20 * 1024 * 1024
    if int(document.get("size_bytes") or 0) > limit:
        raise documents_domain.DocumentError("Arquivo acima do limite", 413)
    row = fetch_one(query, {**params, "document_id": document_id, "max_bytes": limit})
    if row and int(row.get("size_bytes") or 0) > limit:
        raise documents_domain.DocumentError("Arquivo acima do limite", 413)
    if not row or row.get("anexo") is None:
        raise LookupError("Documento nao encontrado")
    content = row["anexo"]
    if isinstance(content, str):
        if len(content) > (limit + 2) // 3 * 4:
            raise documents_domain.DocumentError("Arquivo acima do limite", 413)
        content = base64.b64decode(content, validate=True)
    if len(content) > limit:
        raise documents_domain.DocumentError("Arquivo acima do limite", 413)
    if not content:
        raise documents_domain.DocumentError("Arquivo vazio")
    return bytes(content), documents_domain.safe_filename(row.get("nome_arquivo"))


def _selecionar_documento_previa(documents: list[dict[str, Any]], context: dict[str, Any]) -> dict[str, Any] | None:
    return documents_domain.select_primary(documents, context)


def _render_pdf_previews(content: bytes) -> dict[str, bytes]:
    return producao_render.render(content, "documento.pdf")


def _gerar_previews(content: bytes, filename: str, settings: dict | None = None, description: str = "") -> dict[str, bytes]:
    suffix = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if f".{suffix}" not in documents_domain.PREVIEW_EXTENSIONS:
        raise documents_domain.DocumentError("Formato sem suporte", 415)
    return producao_render.isolated_render(content, filename, settings or producao_render.options(), description)


def _finalizar_preview(cache_key: str, future: Future[dict[str, bytes]]) -> None:
    try:
        previews = future.result()
    except Exception:
        with _PREVIEW_LOCK:
            _PREVIEW_JOBS.pop(cache_key, None)
        return
    with _PREVIEW_LOCK:
        _PREVIEW_JOBS.pop(cache_key, None)
        _PREVIEW_CACHE[cache_key] = previews
        _PREVIEW_CACHE.move_to_end(cache_key)
        while len(_PREVIEW_CACHE) > _PREVIEW_CACHE_LIMIT:
            _PREVIEW_CACHE.popitem(last=False)


def _previews_em_cache(cache_key: str, content: bytes, filename: str, wait: bool = True, settings: dict | None = None, description: str = "") -> dict[str, bytes] | None:
    with _PREVIEW_LOCK:
        cached = _PREVIEW_CACHE.get(cache_key)
        if cached is not None:
            _PREVIEW_CACHE.move_to_end(cache_key)
            return cached
        future = _PREVIEW_JOBS.get(cache_key)
        if future is None:
            if len(_PREVIEW_JOBS) >= 4:
                raise documents_domain.DocumentError("Renderizador ocupado", 503)
            future = _PREVIEW_EXECUTOR.submit(_gerar_previews, content, filename, settings, description)
            _PREVIEW_JOBS[cache_key] = future
            future.add_done_callback(lambda completed: _finalizar_preview(cache_key, completed))
    try:
        return future.result(timeout=(settings or producao_render.options())["timeout"]) if wait else None
    except TimeoutError:
        raise documents_domain.DocumentError("Tempo limite da miniatura excedido", 503) from None


def obter_preview(numero_os: str, aux_code: int, variant: str = "thumbnail", wait: bool = True) -> tuple[bytes | None, str, str]:
    if variant not in {"thumbnail", "detail"}:
        raise documents_domain.DocumentError("Variante de previa invalida", 400)
    from flask import current_app

    company = _empresa()
    request_key = f"{company}:{numero_os}:{aux_code}"
    lock = _DOCUMENT_LOCKS[int(hashlib.sha256(request_key.encode()).hexdigest(), 16) % len(_DOCUMENT_LOCKS)]
    with lock:
        documents = obter_documentos(numero_os, aux_code)
        document = next((doc for doc in documents if doc.get("is_primary")), None)
        if not document:
            raise LookupError("Previa indisponivel: documento elegivel ausente")
        content, filename = obter_arquivo(numero_os, document["aux_code"], document["source_kind"], int(document["id"]))
        settings = producao_render.options(current_app.config)
        source_hash = hashlib.sha256(content).hexdigest()
        identity = json.dumps([company, source_hash, _PREVIEW_CACHE_VERSION, settings, filename.rsplit(".", 1)[-1].lower(), "aprova" in document["description"].lower()], sort_keys=True)
        render_key = hashlib.sha256(identity.encode()).hexdigest()
        cache_key = hashlib.sha256(f"{render_key}:{variant}".encode()).hexdigest()
        cached = _cached_asset(cache_key, company)
        if cached is not None:
            return cached, "image/png", cache_key
        previews = _previews_em_cache(render_key, content, filename, wait, settings, document["description"])
        if previews is None:
            return None, "image/png", cache_key
        binary = previews[variant]
        producao_render.validate_png(binary)
        binary = _store_asset(cache_key, company, numero_os, aux_code, filename, source_hash, binary)
        return binary, "image/png", cache_key


def _cached_asset(cache_key: str, company: int) -> bytes | None:
    from sqlalchemy.orm import Session
    from sqlalchemy.exc import SQLAlchemyError
    from ..extensions import db
    from ..models import ProducaoDerivedAsset

    try:
        with Session(db.engine) as cache_session:
            asset = cache_session.get(ProducaoDerivedAsset, cache_key)
            return bytes(asset.content) if asset and asset.company_code == company else None
    except SQLAlchemyError:
        raise documents_domain.DocumentError("Cache de miniaturas indisponivel", 503) from None


def _store_asset(cache_key: str, company: int, numero_os: str, aux_code: int, filename: str, source_hash: str, content: bytes) -> bytes:
    from sqlalchemy.orm import Session
    from sqlalchemy.exc import IntegrityError, SQLAlchemyError
    from ..extensions import db
    from ..models import ProducaoDerivedAsset

    try:
        with Session(db.engine) as cache_session:
            cache_session.add(ProducaoDerivedAsset(cache_key=cache_key, company_code=company, order_number=numero_os, item_aux_code=aux_code, source_filename=filename, source_sha256=source_hash, media_type="image/png", content=content))
            try:
                cache_session.commit()
            except IntegrityError:
                cache_session.rollback()
                existing = cache_session.get(ProducaoDerivedAsset, cache_key)
                if existing is None or existing.company_code != company:
                    raise
                return bytes(existing.content)
        return content
    except SQLAlchemyError:
        raise documents_domain.DocumentError("Falha ao persistir miniatura", 503) from None


def obter_thumbnail(numero_os: str, aux_code: int) -> tuple[bytes, str]:
    content, media_type, _ = obter_preview(numero_os, aux_code, "thumbnail")
    if content is None:  # pragma: no cover - a chamada bloqueante sempre produz conteudo
        raise LookupError("Previa ainda em processamento")
    return content, media_type


def _obter_ordem(numero_os: str) -> dict[str, Any]:
    ordem = fetch_one(queries.SQL_PRODUCAO_OBTER_OS, {"cod_empresa": _empresa(), "numero_os": numero_os})
    if not ordem:
        raise LookupError(f"OS {numero_os} nao encontrada.")
    return ordem


def _rncs(cod_os: int) -> list[dict[str, Any]]:
    rows = fetch_all(queries.SQL_PRODUCAO_RNCS_OS, {"cod_empresa": _empresa(), "cod_os": cod_os})
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
