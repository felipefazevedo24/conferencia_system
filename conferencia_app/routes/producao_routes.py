from __future__ import annotations

from flask import Blueprint, jsonify, render_template, request, session

from ..auth import permission_required
from ..extensions import db
from ..models import ProducaoObservacao
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
