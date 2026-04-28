"""Rotas de rastreamento de veiculos (Locartrack + mapa interno)."""
from __future__ import annotations

from flask import Blueprint, jsonify, request, session

from ..auth import permission_required
from ..services import rastreamento_store

rastreamento_bp = Blueprint("rastreamento", __name__)


@rastreamento_bp.route("/api/rastreamento/estado", methods=["GET"])
@permission_required("PAGE_LOGISTICA_RASTREAMENTO")
def api_estado():
    return jsonify(rastreamento_store.estado_publico())


@rastreamento_bp.route("/api/rastreamento/veiculos/<veiculo_id>/posicao", methods=["POST"])
@permission_required("PAGE_LOGISTICA_RASTREAMENTO")
def api_atualizar_posicao(veiculo_id: str):
    payload = request.get_json(silent=True) or {}
    try:
        latitude = float(str(payload.get("latitude")).replace(",", "."))
        longitude = float(str(payload.get("longitude")).replace(",", "."))
    except (TypeError, ValueError):
        return jsonify({"sucesso": False, "msg": "Latitude e longitude obrigatorias."}), 400

    if not (-90 <= latitude <= 90) or not (-180 <= longitude <= 180):
        return jsonify({"sucesso": False, "msg": "Coordenadas fora do intervalo valido."}), 400

    velocidade = payload.get("velocidade_kmh")
    try:
        velocidade = float(str(velocidade).replace(",", ".")) if velocidade not in (None, "") else None
    except (TypeError, ValueError):
        velocidade = None

    obs = str(payload.get("observacao") or "")
    usuario = session.get("username", "sistema")
    if obs:
        obs = f"{obs} [por {usuario}]"
    else:
        obs = f"Atualizado por {usuario}"

    atualizado = rastreamento_store.atualizar_posicao(
        veiculo_id=veiculo_id,
        latitude=latitude,
        longitude=longitude,
        velocidade_kmh=velocidade,
        observacao=obs,
    )
    if not atualizado:
        return jsonify({"sucesso": False, "msg": "Veiculo nao encontrado."}), 404

    # --- Ponte Locartrack -> Viagem ---
    # Se existe viagem EmAndamento no veiculo (casando por placa),
    # grava tambem em ViagemPosicao com origem='locartrack'.
    viagem_alimentada = None
    try:
        from ..extensions import db
        from ..models import AgendamentoVeiculo, Viagem, ViagemPosicao
        placa = (atualizado.get("placa") or "").replace("-", "").replace(" ", "").upper()
        if placa:
            cand = (
                db.session.query(AgendamentoVeiculo)
                .filter(AgendamentoVeiculo.placa.isnot(None))
                .all()
            )
            alvo_frota = next(
                (v for v in cand if (v.placa or "").replace("-", "").replace(" ", "").upper() == placa),
                None,
            )
            if alvo_frota:
                viagem = (
                    Viagem.query
                    .filter_by(veiculo_id=alvo_frota.id, status="EmAndamento")
                    .order_by(Viagem.saida_real.desc().nullslast(), Viagem.id.desc())
                    .first()
                )
                if viagem:
                    pos = ViagemPosicao(
                        viagem_id=viagem.id,
                        latitude=latitude,
                        longitude=longitude,
                        velocidade_kmh=velocidade,
                        origem="locartrack",
                    )
                    db.session.add(pos)
                    db.session.commit()
                    viagem_alimentada = {"id": viagem.id, "codigo": viagem.codigo}
    except Exception as exc:
        current_app_logger = getattr(__import__("flask").current_app, "logger", None)
        if current_app_logger:
            current_app_logger.warning("Locartrack->Viagem falhou: %s", exc)

    return jsonify({"sucesso": True, "veiculo": atualizado, "viagem_alimentada": viagem_alimentada})


@rastreamento_bp.route("/api/rastreamento/veiculos/<veiculo_id>", methods=["PATCH"])
@permission_required("PAGE_LOGISTICA_RASTREAMENTO")
def api_editar_veiculo(veiculo_id: str):
    payload = request.get_json(silent=True) or {}
    atualizado = rastreamento_store.atualizar_veiculo(veiculo_id, payload)
    if not atualizado:
        return jsonify({"sucesso": False, "msg": "Veiculo nao encontrado."}), 404
    return jsonify({"sucesso": True, "veiculo": atualizado})


@rastreamento_bp.route("/api/rastreamento/credenciais", methods=["POST"])
@permission_required("PAGE_LOGISTICA_RASTREAMENTO")
def api_credenciais():
    payload = request.get_json(silent=True) or {}
    estado = rastreamento_store.atualizar_credenciais(
        portal_url=payload.get("portal_url"),
        login=payload.get("portal_login"),
        senha=payload.get("portal_senha"),
    )
    return jsonify({"sucesso": True, "estado": estado})
