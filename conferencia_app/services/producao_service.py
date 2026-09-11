"""Nucleo nativo do modulo de Producao, usando o banco/bridge do Sync."""
from __future__ import annotations

from collections import OrderedDict, defaultdict
from concurrent.futures import Future, ThreadPoolExecutor
import base64
import hashlib
import io
import re
import threading
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

_PREVIEW_CACHE_VERSION = "isometric-v1"
_PREVIEW_CACHE_LIMIT = 64
_PREVIEW_CACHE: OrderedDict[str, dict[str, bytes]] = OrderedDict()
_PREVIEW_JOBS: dict[str, Future[dict[str, bytes]]] = {}
_PREVIEW_LOCK = threading.RLock()
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
    return {"aux_code": aux_code, "atualizado_em": datetime.now().isoformat(), "apontamentos": [
        {"operacao": _texto(row.get("operation_code")), "sequencia": row.get("seq_processo_prod"), "operador": _texto(row.get("operator_name")), "inicio": _iso(row.get("started_at")), "maquina": _texto(row.get("machine")), "pausado": bool(row.get("paused"))}
        for row in rows
    ]}


def obter_documentos(numero_os: str, aux_code: int) -> list[dict[str, Any]]:
    ordem = _obter_ordem(numero_os)
    return _obter_documentos_ordem(ordem, numero_os, aux_code)


def _obter_documentos_ordem(ordem: dict[str, Any], numero_os: str, aux_code: int) -> list[dict[str, Any]]:
    rows = fetch_all(queries.SQL_PRODUCAO_DOCUMENTOS_ITEM, {"cod_empresa": 1, "cod_os": ordem["codigo"], "cod_os_aux": aux_code})
    route_kind = {"drawing": "drawings", "attachment": "attachments", "image": "images"}
    return [{"id": row.get("document_id"), "kind": row.get("kind"), "filename": _texto(row.get("nome_arquivo")), "description": _texto(row.get("descricao")), "size_bytes": row.get("size_bytes") or 0, "open_url": f"/api/v1/orders/{numero_os}/items/{aux_code}/{route_kind.get(row.get('kind'), row.get('kind'))}/{row.get('document_id')}"} for row in rows]


def obter_arquivo(numero_os: str, aux_code: int, kind: str, document_id: int) -> tuple[bytes, str]:
    ordem = _obter_ordem(numero_os)
    query = {"drawing": queries.SQL_PRODUCAO_DESENHO_ARQUIVO, "attachment": queries.SQL_PRODUCAO_ANEXO_ARQUIVO, "image": queries.SQL_PRODUCAO_IMAGEM_ARQUIVO}.get(kind)
    if query is None:
        raise LookupError("Tipo de documento invalido")
    row = fetch_one(query, {"cod_empresa": 1, "cod_os": ordem["codigo"], "cod_os_aux": aux_code, "document_id": document_id})
    if not row or row.get("anexo") is None:
        raise LookupError("Documento nao encontrado")
    content = row["anexo"]
    if isinstance(content, str):
        content = base64.b64decode(content)
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
    if drawing and drawing in metadata:
        score += 100
    if item_code and item_code in metadata:
        score += 35
    if position and len(position) >= 2 and position in metadata:
        score += 15
    if revision and any(marker in metadata for marker in (f"REV{revision}", f"REVISAO{revision}", f"R{revision}")):
        score += 30
    return score


def _selecionar_documento_previa(documents: list[dict[str, Any]], context: dict[str, Any]) -> dict[str, Any] | None:
    expected_kind = "attachment" if _normalizar_identificador(context.get("segmento")) == "CMS" else "drawing"
    candidates = [document for document in documents if document.get("kind") == expected_kind]
    if not candidates:
        return None
    ranked = sorted((( _pontuar_documento(document, context), document) for document in candidates), key=lambda item: (item[0], int(item[1].get("id") or 0)), reverse=True)
    if len(candidates) == 1:
        score, document = ranked[0]
        if expected_kind == "attachment" and context.get("n_desenho") and score < 100:
            return None
        return document
    best_score, best = ranked[0]
    if best_score <= 0 or best_score == ranked[1][0]:
        return None
    return best


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


def _page_isometric_candidates(page: Any) -> list[tuple[float, Any]]:
    page_rect = page.rect
    page_area = max(page_rect.width * page_rect.height, 1)
    records = []
    for drawing in page.get_drawings():
        rect = fitz.Rect(drawing.get("rect")) & page_rect
        coverage = (rect.width * rect.height) / page_area
        if rect.is_empty or coverage < 0.0002 or coverage > 0.82:
            continue
        lines, diagonals, curves = _line_stats(drawing.get("items") or [])
        records.append({"rect": rect, "lines": lines, "diagonals": diagonals, "curves": curves, "paths": 1})

    groups: list[dict[str, Any]] = []
    join_margin = max(5.0, min(page_rect.width, page_rect.height) * 0.012)
    for record in records:
        matches = [group for group in groups if _expand_rect(group["rect"], join_margin, page_rect).intersects(record["rect"])]
        if not matches:
            groups.append(record)
            continue
        target = matches[0]
        target["rect"] |= record["rect"]
        for key in ("lines", "diagonals", "curves", "paths"):
            target[key] += record[key]
        for extra in matches[1:]:
            target["rect"] |= extra["rect"]
            for key in ("lines", "diagonals", "curves", "paths"):
                target[key] += extra[key]
            groups.remove(extra)

    page_text = _normalizar_identificador(page.get_text("text"))
    has_isometric_label = "ISOMETR" in page_text
    candidates = []
    for group in groups:
        rect = group["rect"]
        coverage = (rect.width * rect.height) / page_area
        diagonal_ratio = group["diagonals"] / max(group["lines"], 1)
        if coverage < 0.008 or coverage > 0.72:
            continue
        if group["diagonals"] < 2 and group["curves"] < 2:
            continue
        score = diagonal_ratio * 8 + min(group["paths"], 40) / 20 + min(coverage, 0.35) * 3
        if has_isometric_label:
            score += 1.5
        if rect.y0 > page_rect.height * 0.72 and rect.x0 > page_rect.width * 0.55:
            score -= 4
        candidates.append((score, _expand_rect(rect, max(rect.width, rect.height) * 0.06, page_rect)))

    for image in page.get_image_info():
        rect = fitz.Rect(image.get("bbox")) & page_rect
        coverage = (rect.width * rect.height) / page_area
        if not rect.is_empty and 0.06 <= coverage <= 0.72:
            score = 3 + min(coverage, 0.4) * 3 + (1.5 if has_isometric_label else 0)
            candidates.append((score, _expand_rect(rect, max(rect.width, rect.height) * 0.04, page_rect)))
    return candidates


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
                candidates.extend((score, page_index, rect) for score, rect in _page_isometric_candidates(page))
            candidates.sort(key=lambda item: item[0], reverse=True)
            if not candidates or (len(candidates) > 1 and candidates[0][0] - candidates[1][0] < 0.15):
                raise LookupError("Vista isometrica nao identificada com confianca")
            _, page_index, clip = candidates[0]
            page = pdf.load_page(page_index)
            rendered = {}
            for variant, max_pixels in (("thumbnail", 720), ("detail", 1600)):
                scale = max(1.5, min(4.0, max_pixels / max(clip.width, clip.height)))
                pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), clip=clip, alpha=False)
                rendered[variant] = pixmap.tobytes("png")
            return rendered
    except LookupError:
        raise
    except Exception as exc:
        raise LookupError("Documento PDF invalido ou ilegivel") from exc


def _gerar_previews(content: bytes, filename: str) -> dict[str, bytes]:
    if not filename.lower().endswith(".pdf"):
        raise LookupError("Vista isometrica nao identificada com confianca")
    return _render_pdf_previews(content)


def _previews_em_cache(cache_key: str, content: bytes, filename: str) -> dict[str, bytes]:
    with _PREVIEW_LOCK:
        cached = _PREVIEW_CACHE.get(cache_key)
        if cached is not None:
            _PREVIEW_CACHE.move_to_end(cache_key)
            return cached
        future = _PREVIEW_JOBS.get(cache_key)
        if future is None:
            future = _PREVIEW_EXECUTOR.submit(_gerar_previews, content, filename)
            _PREVIEW_JOBS[cache_key] = future
    try:
        previews = future.result()
    finally:
        with _PREVIEW_LOCK:
            _PREVIEW_JOBS.pop(cache_key, None)
    with _PREVIEW_LOCK:
        _PREVIEW_CACHE[cache_key] = previews
        _PREVIEW_CACHE.move_to_end(cache_key)
        while len(_PREVIEW_CACHE) > _PREVIEW_CACHE_LIMIT:
            _PREVIEW_CACHE.popitem(last=False)
    return previews


def obter_preview(numero_os: str, aux_code: int, variant: str = "thumbnail") -> tuple[bytes, str, str]:
    if variant not in {"thumbnail", "detail"}:
        raise LookupError("Variante de previa invalida")
    ordem = _obter_ordem(numero_os)
    params = {"cod_empresa": 1, "cod_os": ordem["codigo"], "cod_os_aux": aux_code}
    context = fetch_one(queries.SQL_PRODUCAO_ITEM_PREVIEW_CONTEXT, params)
    if not context:
        raise LookupError("Item nao encontrado")
    documents = _obter_documentos_ordem(ordem, numero_os, aux_code)
    document = _selecionar_documento_previa(documents, context)
    if not document:
        raise LookupError("Previa indisponivel: documento esperado ausente ou ambiguo")
    content, filename = obter_arquivo(numero_os, aux_code, str(document["kind"]), int(document["id"]))
    identity = "|".join((
        _PREVIEW_CACHE_VERSION,
        str(document.get("kind")),
        str(document.get("id")),
        _texto(context.get("revisao_desenho")),
        hashlib.sha256(content).hexdigest(),
    ))
    cache_key = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    previews = _previews_em_cache(cache_key, content, filename)
    return previews[variant], "image/png", cache_key


def obter_thumbnail(numero_os: str, aux_code: int) -> tuple[bytes, str]:
    content, media_type, _ = obter_preview(numero_os, aux_code, "thumbnail")
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
