from __future__ import annotations

import base64
from datetime import datetime

from flask import Blueprint, current_app, jsonify, render_template, request, send_from_directory, session

from ..auth import permission_required
from ..extensions import db
from ..models import ProducaoObservacao, ProducaoSequencia
from ..services import producao_service

producao_bp = Blueprint("producao", __name__)

_PREVIEW_PENDING_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


@producao_bp.get("/producao")
@permission_required("PAGE_PRODUCAO")
def producao_page():
    return render_template("producao_shell.html")


@producao_bp.get("/producao-original/")
@permission_required("PAGE_PRODUCAO")
def producao_original_page():
    return send_from_directory(
        current_app.static_folder + "/producao_original",
        "index.html",
    )


@producao_bp.get("/columbia-logo.png")
@permission_required("PAGE_PRODUCAO")
def producao_logo():
    return send_from_directory(current_app.static_folder + "/producao_original", "columbia-logo.png")


@producao_bp.get("/producao-original/assets/<path:filename>")
@permission_required("PAGE_PRODUCAO")
def producao_asset(filename: str):
    return send_from_directory(current_app.static_folder + "/producao_original/assets", filename)


@producao_bp.get("/producao-original/<path:filename>")
@permission_required("PAGE_PRODUCAO")
def producao_static(filename: str):
    return send_from_directory(current_app.static_folder + "/producao_original", filename)


@producao_bp.get("/api/producao/os")
@permission_required("PAGE_PRODUCAO")
def buscar_os():
    termo = str(request.args.get("q") or "").strip()
    if len(termo) < 1:
        return jsonify({"resultados": []})
    try:
        return jsonify({"resultados": producao_service.buscar_os(termo)})
    except Exception:
        return jsonify({"error": "Nao foi possivel consultar as OS no GRV."}), 503


@producao_bp.get("/api/producao/os-abertas")
@permission_required("PAGE_PRODUCAO")
def listar_os_abertas():
    try:
        return jsonify({"resultados": producao_service.listar_os_abertas()})
    except Exception:
        return jsonify({"error": "Nao foi possivel consultar as OS abertas no GRV."}), 503


@producao_bp.get("/api/producao/os/<path:numero_os>")
@permission_required("PAGE_PRODUCAO")
def estrutura_os(numero_os: str):
    try:
        return jsonify(producao_service.obter_estrutura(numero_os))
    except LookupError as exc:
        return jsonify({"error": str(exc)}), 404
    except Exception:
        return jsonify({"error": "Nao foi possivel carregar a estrutura da OS."}), 503


@producao_bp.get("/api/producao/os/<path:numero_os>/itens/<int:aux_code>/materiais")
@permission_required("PAGE_PRODUCAO")
def materiais_item(numero_os: str, aux_code: int):
    try:
        return jsonify(producao_service.obter_materiais(numero_os, aux_code))
    except LookupError as exc:
        return jsonify({"error": str(exc)}), 404
    except Exception:
        return jsonify({"error": "Nao foi possivel carregar os materiais do item."}), 503


@producao_bp.get("/api/producao/os/<path:numero_os>/itens/<int:aux_code>/apontamentos")
@permission_required("PAGE_PRODUCAO")
def apontamentos_item(numero_os: str, aux_code: int):
    try:
        return jsonify(producao_service.obter_apontamentos(numero_os, aux_code))
    except LookupError as exc:
        return jsonify({"error": str(exc)}), 404
    except Exception:
        return jsonify({"error": "Nao foi possivel carregar os apontamentos do item."}), 503


@producao_bp.get("/api/producao/os/<path:numero_os>/itens/<int:aux_code>/observacoes")
@permission_required("PAGE_PRODUCAO")
def listar_observacoes(numero_os: str, aux_code: int):
    rows = ProducaoObservacao.query.filter_by(numero_os=numero_os, aux_code=aux_code).order_by(ProducaoObservacao.criado_em.desc()).all()
    return jsonify({"observacoes": [{"id": row.id, "texto": row.texto, "autor": row.autor, "criado_em": row.criado_em.isoformat()} for row in rows]})


@producao_bp.post("/api/producao/os/<path:numero_os>/itens/<int:aux_code>/observacoes")
@permission_required("PAGE_PRODUCAO")
def criar_observacao(numero_os: str, aux_code: int):
    texto = str((request.get_json(silent=True) or {}).get("texto") or "").strip()
    if not texto:
        return jsonify({"error": "Informe a observacao."}), 400
    row = ProducaoObservacao(numero_os=numero_os, aux_code=aux_code, texto=texto[:4000], autor=session.get("username", "desconhecido"))
    db.session.add(row)
    db.session.commit()
    return jsonify({"id": row.id, "texto": row.texto, "autor": row.autor, "criado_em": row.criado_em.isoformat()}), 201


def _original_node(node: dict, all_nodes: list[dict], order_number: str = "") -> dict:
    parent = next((item for item in all_nodes if item["id"] == node.get("parent_id")), None)
    path = []
    cursor = parent
    while cursor:
        path.insert(0, cursor)
        cursor = next((item for item in all_nodes if item["id"] == cursor.get("parent_id")), None)
    return {
        "id": node["id"],
        "aux_code": node["aux_code"],
        "code": node.get("codigo") or node["id"],
        "description": node.get("descricao") or "Sem descricao",
        "drawing_number": node.get("desenho") or None,
        "drawing_revision": node.get("revisao") or None,
        "position": node.get("posicao") or None,
        "quantity": node.get("quantidade") or 0,
        "parent_id": node.get("parent_id"),
        "child_ids": node.get("child_ids") or [],
        "has_children": bool(node.get("child_ids")),
        "predecessor_ids": [],
        "path_ids": [item["id"] for item in path],
        "path_labels": [item.get("codigo") or item["id"] for item in path],
        "state": {"bloqueado": "blocked", "concluido": "completed", "disponivel": "available", "montagem": "assembling", "fabricacao": "manufacturing", "nao_iniciado": "not_started"}.get(node.get("estado"), "not_started"),
        "state_reason_code": "process_locked" if node.get("estado") == "bloqueado" else "no_execution_evidence",
        "state_reason": node.get("estado_motivo") or "Sem informacao",
        "operations_total": node.get("operacoes_total", 0),
        "operations_started": 0,
        "operations_completed": node.get("operacoes_concluidas", 0),
        "source_status": node.get("status_origem") or None,
        "current_operation": next((op.get("nome") for op in node.get("operacoes", []) if not op.get("finalizada")), None),
        "entry_date": None,
        "due_date": node.get("data_prevista"),
        "final_date": None,
        "has_drawing": bool(node.get("desenho")),
        "thumbnail_url": f"/api/v1/orders/{order_number}/items/{node['aux_code']}/thumbnail",
        "detail_url": f"/api/v1/orders/{{order}}/items/{node['aux_code']}",
    }


def _original_structure(data: dict) -> dict:
    nodes = [_original_node(node, data.get("nos", []), data["ordem"]["numero"]) for node in data.get("nos", [])]
    for node in nodes:
        node["detail_url"] = node["detail_url"].format(order=data["ordem"]["numero"])
    return {
        "order": {"number": data["ordem"]["numero"], "title": data["ordem"].get("titulo", ""), "source_status": data["ordem"].get("status_origem"), "due_date": data["ordem"].get("data_prevista"), "drawing_number": data["ordem"].get("desenho")},
        "roots": data.get("raizes", []),
        "nodes": nodes,
        "progress": {"percentage": data.get("progresso", 0), "finalized_operations": data.get("operacoes_concluidas", 0), "total_operations": data.get("operacoes_total", 0)},
        "pending_count": data.get("bloqueados", 0),
        "current_stage": "Producao",
        "source": {"calculated_at": datetime.now().isoformat()},
    }


@producao_bp.get("/api/v1/orders/search")
@permission_required("PAGE_PRODUCAO")
def original_search_orders():
    termo = str(request.args.get("q") or "").strip()
    return jsonify([_original_node_result(item) for item in producao_service.buscar_os(termo, int(request.args.get("limit", 20)))])


def _original_node_result(item: dict) -> dict:
    return {"number": item.get("numero"), "title": item.get("titulo"), "source_status": item.get("status_origem") or None, "due_date": item.get("data_prevista"), "drawing_number": item.get("desenho") or None, "matched_item_code": None, "matched_item_description": None, "matched_budget_number": None}


@producao_bp.get("/api/v1/orders/<path:numero_os>/structure")
@permission_required("PAGE_PRODUCAO")
def original_structure(numero_os: str):
    try:
        return jsonify(_original_structure(producao_service.obter_estrutura(numero_os)))
    except LookupError as exc:
        return jsonify({"detail": str(exc)}), 404
    except Exception:
        return jsonify({"detail": "Falha ao consultar o GRV"}), 503


@producao_bp.get("/api/v1/orders/<path:numero_os>/dependencies")
@permission_required("PAGE_PRODUCAO")
def original_dependencies(numero_os: str):
    try:
        data = producao_service.obter_estrutura(numero_os)
        return jsonify({"selected_order_number": numero_os, "budget_number": None, "nodes": [{"number": data["ordem"]["numero"], "title": data["ordem"].get("titulo", ""), "source_status": data["ordem"].get("status_origem"), "due_date": data["ordem"].get("data_prevista"), "drawing_number": data["ordem"].get("desenho"), "is_budget_order": False}], "edges": [], "source": {"calculated_at": datetime.now().isoformat()}})
    except Exception:
        return jsonify({"detail": "Falha ao consultar as dependencias da OS"}), 503


@producao_bp.get("/api/v1/orders/<path:numero_os>/items/<int:aux_code>")
@permission_required("PAGE_PRODUCAO")
def original_item(numero_os: str, aux_code: int):
    data = producao_service.obter_estrutura(numero_os)
    node = next((item for item in data["nos"] if item["aux_code"] == aux_code), None)
    if not node:
        return jsonify({"detail": "Item nao encontrado"}), 404
    original = _original_node(node, data["nos"])
    operations = [{"code": op.get("codigo"), "name": op.get("nome"), "sequence": op.get("sequencia"), "finalized": op.get("finalizada"), "locked": op.get("travada"), "started_at": op.get("inicio"), "planned_start": None, "planned_end": None, "finished_at": op.get("fim"), "machine": op.get("maquina"), "first_report_at": None, "last_report_at": None} for op in node.get("operacoes", [])]
    documents = producao_service.obter_documentos(numero_os, aux_code)
    drawings = [item for item in documents if item["kind"] == "drawing"]
    primary_document = next((item for item in documents if item.get("is_primary")), None)
    return jsonify({"node": original, "parent": None, "path": [], "operations": operations, "categories": {"ph": 0, "lm": 0, "st": 0, "pp": 0}, "predecessors": [], "drawings": drawings, "documents": [item for item in documents if item["kind"] != "drawing"], "observations_count": 0, "information_origin": "GRV", "document_path": primary_document["filename"] if primary_document else None})


@producao_bp.get("/api/v1/orders/<path:numero_os>/items/<int:aux_code>/operations/live")
@permission_required("PAGE_PRODUCAO")
def original_live(numero_os: str, aux_code: int):
    data = producao_service.obter_apontamentos(numero_os, aux_code)
    return jsonify({"item_aux_code": aux_code, "operations": [{"code": item.get("operacao"), "sequence": item.get("sequencia"), "state": "paused" if item.get("pausado") else "running", "pointings": [{"operator_name": item.get("operador"), "started_at": item.get("inicio"), "machine": item.get("maquina"), "paused": item.get("pausado")}]} for item in data.get("apontamentos", [])], "source": {"calculated_at": data.get("atualizado_em")}, "refresh_after_seconds": 10})


@producao_bp.get("/api/v1/orders/<path:numero_os>/items/<int:aux_code>/materials")
@permission_required("PAGE_PRODUCAO")
def original_materials(numero_os: str, aux_code: int):
    data = producao_service.obter_materiais(numero_os, aux_code)
    return jsonify({"order_number": numero_os, "item_aux_code": aux_code, "item_code": str(aux_code), "material_used": any(float(item.get("utilizado") or 0) > 0 for item in data.get("materiais", [])), "materials": [{"id": item.get("id"), "code": item.get("codigo"), "description": item.get("descricao"), "required_quantity": item.get("necessario", 0), "consumed_quantity": item.get("utilizado", 0), "remaining_quantity": item.get("restante", 0), "available_quantity": item.get("disponivel"), "unit": item.get("unidade"), "used": float(item.get("utilizado") or 0) > 0, "consumption_status": "consumed" if float(item.get("restante") or 0) <= 0 else ("partial" if float(item.get("utilizado") or 0) > 0 else "not_used"), "item_code": str(aux_code)} for item in data.get("materiais", [])], "source": {"calculated_at": datetime.now().isoformat()}})


def _serve_original_document(numero_os: str, aux_code: int, kind: str, document_id: int):
    try:
        content, filename = producao_service.obter_arquivo(numero_os, aux_code, kind, document_id)
    except LookupError as exc:
        return jsonify({"detail": str(exc)}), 404
    except Exception:
        current_app.logger.exception("Falha ao buscar documento de producao %s/%s/%s/%s", numero_os, aux_code, kind, document_id)
        return jsonify({"detail": "Bridge de documentos indisponivel ou desatualizada."}), 503
    media_type = "application/pdf" if filename.lower().endswith(".pdf") else ("image/png" if filename.lower().endswith(".png") else "application/octet-stream")
    return current_app.response_class(content, mimetype=media_type, headers={"Content-Disposition": f"inline; filename=\"{filename}\""})


@producao_bp.get("/api/v1/orders/<path:numero_os>/items/<int:aux_code>/drawings/<int:document_id>")
@permission_required("PAGE_PRODUCAO")
def original_drawing(numero_os: str, aux_code: int, document_id: int):
    return _serve_original_document(numero_os, aux_code, "drawing", document_id)


@producao_bp.get("/api/v1/orders/<path:numero_os>/items/<int:aux_code>/attachments/<int:document_id>")
@permission_required("PAGE_PRODUCAO")
def original_attachment(numero_os: str, aux_code: int, document_id: int):
    return _serve_original_document(numero_os, aux_code, "attachment", document_id)


@producao_bp.get("/api/v1/orders/<path:numero_os>/items/<int:aux_code>/images/<int:document_id>")
@permission_required("PAGE_PRODUCAO")
def original_image(numero_os: str, aux_code: int, document_id: int):
    return _serve_original_document(numero_os, aux_code, "image", document_id)


@producao_bp.get("/api/v1/orders/<path:numero_os>/items/<int:aux_code>/thumbnail")
@permission_required("PAGE_PRODUCAO")
def original_thumbnail(numero_os: str, aux_code: int):
    try:
        variant = str(request.args.get("variant") or "thumbnail").strip().lower()
        content, media_type, etag = producao_service.obter_preview(numero_os, aux_code, variant, wait=False)
    except LookupError as exc:
        return jsonify({"detail": str(exc)}), 404
    except Exception:
        current_app.logger.exception("Falha ao gerar thumbnail de producao %s/%s", numero_os, aux_code)
        return jsonify({"detail": "Bridge de documentos indisponivel ou desatualizada."}), 503
    if content is None:
        return current_app.response_class(
            _PREVIEW_PENDING_PNG,
            mimetype="image/png",
            headers={"Cache-Control": "no-store", "X-Preview-State": "processing"},
        )
    if request.if_none_match.contains(etag):
        response = current_app.response_class(status=304)
    else:
        response = current_app.response_class(content, mimetype=media_type)
    response.set_etag(etag)
    response.headers["Cache-Control"] = "private, no-cache, must-revalidate"
    return response


@producao_bp.get("/api/v1/orders/<path:numero_os>/items/<int:aux_code>/observations")
@permission_required("PAGE_PRODUCAO")
def original_list_observations(numero_os: str, aux_code: int):
    rows = ProducaoObservacao.query.filter_by(numero_os=numero_os, aux_code=aux_code).order_by(ProducaoObservacao.criado_em.desc()).all()
    return jsonify([{"id": str(row.id), "order_number": numero_os, "item_aux_code": aux_code, "text": row.texto, "author": row.autor, "created_at": row.criado_em.isoformat()} for row in rows])


@producao_bp.post("/api/v1/orders/<path:numero_os>/items/<int:aux_code>/observations")
@permission_required("PAGE_PRODUCAO")
def original_create_observation(numero_os: str, aux_code: int):
    payload = request.get_json(silent=True) or {}
    text = str(payload.get("text") or "").strip()
    if not text:
        return jsonify({"detail": "Informe a observacao."}), 400
    row = ProducaoObservacao(numero_os=numero_os, aux_code=aux_code, texto=text[:4000], autor=session.get("username", "desconhecido"))
    db.session.add(row)
    db.session.commit()
    return jsonify({"id": str(row.id), "order_number": numero_os, "item_aux_code": aux_code, "text": row.texto, "author": row.autor, "created_at": row.criado_em.isoformat()}), 201


@producao_bp.get("/api/v1/orders/<path:numero_os>/sequence")
@permission_required("PAGE_PRODUCAO")
def original_list_sequence(numero_os: str):
    rows = ProducaoSequencia.query.filter_by(numero_os=numero_os).order_by(ProducaoSequencia.posicao).all()
    return jsonify([{"id": str(row.id), "order_number": row.numero_os, "item_aux_code": row.aux_code, "position": row.posicao, "title": row.titulo, "instructions": row.instrucoes, "source": "application", "created_by": row.criado_por, "created_at": row.criado_em.isoformat()} for row in rows])


@producao_bp.post("/api/v1/orders/<path:numero_os>/sequence")
@permission_required("PAGE_PRODUCAO")
def original_create_sequence(numero_os: str):
    payload = request.get_json(silent=True) or {}
    title = str(payload.get("title") or "").strip()
    if not title:
        return jsonify({"detail": "Informe o titulo da etapa."}), 400
    last = ProducaoSequencia.query.filter_by(numero_os=numero_os).order_by(ProducaoSequencia.posicao.desc()).first()
    row = ProducaoSequencia(numero_os=numero_os, aux_code=payload.get("item_aux_code"), posicao=(last.posicao + 1 if last else 1), titulo=title[:240], instrucoes=str(payload.get("instructions") or "").strip() or None, criado_por=session.get("username", "desconhecido"))
    db.session.add(row)
    db.session.commit()
    return jsonify({"id": str(row.id), "order_number": row.numero_os, "item_aux_code": row.aux_code, "position": row.posicao, "title": row.titulo, "instructions": row.instrucoes, "source": "application", "created_by": row.criado_por, "created_at": row.criado_em.isoformat()}), 201
