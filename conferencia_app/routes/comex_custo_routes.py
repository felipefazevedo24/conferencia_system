"""Comex > Simulador de custo de importacao (Despachante x Courier) e o
cadastro de aliquotas por NCM. O calculo mora em
services/comex_custo_service.py (motor reutilizavel pela provisao)."""
from __future__ import annotations

from flask import Blueprint, jsonify, render_template, request, session

from ..auth import permission_required
from ..services import comex_custo_service as svc

comex_custo_bp = Blueprint("comex_custo", __name__)

PERMISSION = "PAGE_COMEX"

_CAMPOS_DESPACHANTE = ("frete_usd", "seguro_usd", "acrescimos_brl", "taxa_siscomex_brl", "afrmm_brl",
                       "despesas_desembaraco_brl")
_CAMPOS_COURIER = ("frete_usd", "seguro_usd", "servico_courier_brl", "tarifa_aeroportuaria_brl", "multas_brl")


def _usuario() -> str:
    return session.get("username", "desconhecido")


@comex_custo_bp.route("/comex/simulador-custo")
@permission_required(PERMISSION)
def page_simulador_custo():
    return render_template("comex_simulador_custo.html", user=session.get("username"),
                           icms_courier_padrao=svc.ICMS_COURIER_PADRAO * 100,
                           ii_courier_padrao=svc.II_COURIER_PADRAO * 100)


@comex_custo_bp.route("/api/comex/ncms", methods=["GET"])
@permission_required(PERMISSION)
def api_listar_ncms():
    return jsonify({"ncms": [svc.ncm_dict(n) for n in svc.listar_ncms()]})


@comex_custo_bp.route("/api/comex/ncms", methods=["POST"])
@comex_custo_bp.route("/api/comex/ncms/<int:ncm_id>", methods=["PUT"])
@permission_required(PERMISSION)
def api_salvar_ncm(ncm_id=None):
    try:
        registro = svc.salvar_ncm(request.get_json(silent=True) or {}, _usuario(), ncm_id)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"message": f"NCM {svc.formatar_ncm(registro.ncm)} salvo.", "ncm": svc.ncm_dict(registro)})


@comex_custo_bp.route("/api/comex/ncms/<int:ncm_id>", methods=["DELETE"])
@permission_required(PERMISSION)
def api_excluir_ncm(ncm_id):
    try:
        svc.excluir_ncm(ncm_id)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 404
    return jsonify({"message": "NCM excluído."})


@comex_custo_bp.route("/api/comex/ptax", methods=["GET"])
@permission_required(PERMISSION)
def api_ptax():
    ptax = svc.ptax_venda()
    if not ptax:
        return jsonify({"ptax": None, "message": "Não consegui buscar a PTAX no Banco Central - informe o câmbio."})
    return jsonify({"ptax": ptax})


@comex_custo_bp.route("/api/comex/simulador-custo", methods=["POST"])
@permission_required(PERMISSION)
def api_simular():
    payload = request.get_json(silent=True) or {}
    itens = [i for i in (payload.get("itens") or []) if any(str(v or "").strip() for v in i.values())]
    aliquotas = svc.aliquotas_do_cadastro(i.get("ncm") for i in itens)
    sem_cadastro = sorted({svc.formatar_ncm(i.get("ncm")) or "(vazio)" for i in itens
                           if svc.normalizar_ncm(i.get("ncm")) not in aliquotas})
    if sem_cadastro:
        return jsonify({"error": f"NCM sem alíquota cadastrada: {', '.join(sem_cadastro)}. Cadastre na aba NCMs.",
                        "ncms_sem_cadastro": sem_cadastro}), 400
    for item in itens:
        item["aliquotas"] = aliquotas[svc.normalizar_ncm(item.get("ncm"))]
    desp = payload.get("despachante") or {}
    cour = payload.get("courier") or {}
    try:
        icms_courier = svc._num(cour.get("icms_courier_pct") or svc.ICMS_COURIER_PADRAO * 100) / 100
        ii_courier = svc._num(cour.get("ii_courier_pct") or svc.II_COURIER_PADRAO * 100) / 100
        resultado = svc.comparar(
            itens, payload.get("cambio"),
            despachante={c: desp.get(c) for c in _CAMPOS_DESPACHANTE},
            courier={**{c: cour.get(c) for c in _CAMPOS_COURIER}, "icms_courier": icms_courier, "ii_courier": ii_courier},
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(resultado)
