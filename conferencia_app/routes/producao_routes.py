from __future__ import annotations

from flask import Blueprint, jsonify, render_template, request

from ..auth import permission_required
from ..services import producao_service

producao_bp = Blueprint("producao", __name__)


@producao_bp.get("/producao")
@permission_required("PAGE_PRODUCAO")
def producao_page():
    return render_template("producao.html")


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


@producao_bp.get("/api/producao/os/<path:numero_os>")
@permission_required("PAGE_PRODUCAO")
def estrutura_os(numero_os: str):
    try:
        return jsonify(producao_service.obter_estrutura(numero_os))
    except LookupError as exc:
        return jsonify({"error": str(exc)}), 404
    except Exception:
        return jsonify({"error": "Nao foi possivel carregar a estrutura da OS."}), 503
