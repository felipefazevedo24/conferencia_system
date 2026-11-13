"""Central de Viagens > Retiradas: termo de retirada de veículo por funcionário."""
from __future__ import annotations

from io import BytesIO

from flask import Blueprint, abort, jsonify, render_template, request, send_file, session

from ..auth import permission_required
from ..extensions import db
from ..models import AgendamentoVeiculo, Usuario
from ..services import viagem_retirada_service as retirada_svc

retirada_bp = Blueprint("viagem_retirada", __name__, url_prefix="/api/viagem/retiradas")

PERM = "PAGE_LOGISTICA_VIAGEM"


def _user():
    return session.get("username", "sistema")


def _nome_usuario() -> str:
    u = Usuario.query.filter_by(username=_user()).first()
    return (u.nome_exibicao if u and u.nome_exibicao else _user()) or ""


@retirada_bp.route("", methods=["GET"])
@permission_required(PERM)
def listar():
    return jsonify({"retiradas": retirada_svc.listar()})


@retirada_bp.route("/form", methods=["GET"])
@permission_required(PERM)
def dados_form():
    # Veículos vêm daqui e não do painel de solicitações: aquele exige
    # PAGE_LOGISTICA_AGENDAMENTO e, sem ela, o formulário ficava sem veículos.
    return jsonify({"veiculos": retirada_svc.veiculos_ativos(), "usuario_nome": _nome_usuario()})


@retirada_bp.route("", methods=["POST"])
@permission_required(PERM)
def criar():
    try:
        r = retirada_svc.criar(request.get_json(silent=True) or {}, _user())
    except ValueError as e:
        return jsonify({"msg": str(e)}), 400
    return jsonify({"id": r.id}), 201


@retirada_bp.route("/<int:rid>/termo", methods=["GET"])
@permission_required(PERM)
def termo(rid: int):
    r = retirada_svc.obter(rid)
    if not r:
        abort(404)
    veiculo = db.session.get(AgendamentoVeiculo, r.veiculo_id)
    return render_template(
        "viagem_retirada_termo.html",
        r=r,
        veiculo_label=retirada_svc.veiculo_label(veiculo),
        clausulas=retirada_svc.clausulas(),
    )


@retirada_bp.route("/<int:rid>/assinado", methods=["POST"])
@permission_required(PERM)
def enviar_assinado(rid: int):
    arquivo = request.files.get("arquivo")
    if not arquivo:
        return jsonify({"msg": "Selecione o arquivo do termo assinado."}), 400
    try:
        r = retirada_svc.salvar_assinado(rid, arquivo.read(), arquivo.filename or "", arquivo.mimetype, _user())
    except ValueError as e:
        return jsonify({"msg": str(e)}), 400
    if not r:
        return jsonify({"msg": "Retirada não encontrada."}), 404
    return jsonify({"ok": True})


@retirada_bp.route("/<int:rid>/assinado", methods=["GET"])
@permission_required(PERM)
def ver_assinado(rid: int):
    r = retirada_svc.obter(rid)
    if not r or not r.assinado_arquivo:
        abort(404)
    return send_file(
        BytesIO(r.assinado_arquivo),
        mimetype=r.assinado_mime or "application/octet-stream",
        as_attachment=False,
        download_name=r.assinado_nome or f"retirada_{rid}",
    )
