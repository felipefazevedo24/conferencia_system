"""Assistencia Tecnica > Solicitacao de Producao de Pecas.

Duas portas de entrada:
- EDI (sistema externo): POST /api/edi/assistencia-tecnica/producao-pecas,
  sem login, autenticado pelo token no header X-Integracao-Token.
- Tela da equipe (com login e permissao): lista e movimenta o status.
"""
from __future__ import annotations

from io import BytesIO

from flask import Blueprint, jsonify, render_template, request, send_file, session

from ..auth import permission_required
from ..services import assistencia_producao_peca_service as svc

assistencia_producao_peca_bp = Blueprint("assistencia_producao_peca", __name__)

PERMISSION = "PAGE_ASSISTENCIA_PRODUCAO_PECAS"


def _dt(valor):
    return valor.strftime("%d/%m/%Y %H:%M") if valor else None


def _fmt(s) -> dict:
    return {
        "id": s.id,
        "protocolo": svc.protocolo(s),
        "id_externo": s.id_externo,
        "cliente": s.cliente,
        "cliente_codigo": s.cliente_codigo,
        "cliente_nome_erp": s.cliente_nome_erp,
        "maquina": s.maquina,
        "codigo": s.codigo,
        "descricao": s.descricao,
        "descricao_erp": s.descricao_erp,
        "situacao_erp": svc.situacao_erp(s),
        "motivo": s.motivo,
        "comentario": s.comentario,
        "prazo": s.prazo.isoformat() if s.prazo else None,
        "atrasada": svc.atrasada(s),
        "solicitante": s.solicitante,
        "tem_imagem": bool(s.imagem_content_type),
        "status": s.status,
        "proximo_status": svc.proximo_status(s.status),
        "status_anterior": svc.status_anterior(s.status),
        "pode_cancelar": s.status not in svc.STATUS_FINAIS,
        "motivo_cancelamento": s.motivo_cancelamento,
        "recebido_em": _dt(s.recebido_em),
        "atualizado_em": _dt(s.atualizado_em),
        "atualizado_por": s.atualizado_por,
        "historico": [
            {
                "acao": h.acao,
                "status_de": h.status_de,
                "status_para": h.status_para,
                "motivo": h.motivo,
                "usuario": h.usuario,
                "criado_em": _dt(h.criado_em),
            }
            for h in s.historico
        ],
    }


def _token_informado() -> str:
    token = str(request.headers.get("X-Integracao-Token") or "").strip()
    if token:
        return token
    auth = str(request.headers.get("Authorization") or "").strip()
    return auth[7:].strip() if auth.lower().startswith("bearer ") else ""


# ── EDI ─────────────────────────────────────────────────────────────────
@assistencia_producao_peca_bp.route("/api/edi/assistencia-tecnica/producao-pecas", methods=["POST"])
def edi_receber_solicitacao():
    if not svc.token_edi_configurado():
        return jsonify({"error": "Integração não configurada no Sync."}), 503
    if not svc.token_valido(_token_informado()):
        return jsonify({"error": "Token inválido."}), 401
    try:
        solicitacao, criada = svc.receber_edi(request.get_json(silent=True))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({
        "id": solicitacao.id,
        "protocolo": svc.protocolo(solicitacao),
        "status": solicitacao.status,
        "duplicada": not criada,
    }), (201 if criada else 200)


# ── Tela ────────────────────────────────────────────────────────────────
@assistencia_producao_peca_bp.route("/assistencia-tecnica/producao-pecas")
@permission_required(PERMISSION)
def page_producao_pecas():
    return render_template("assistencia_producao_pecas.html", user=session.get("username"))


@assistencia_producao_peca_bp.route("/api/assistencia-tecnica/producao-pecas", methods=["GET"])
@permission_required(PERMISSION)
def api_listar():
    return jsonify({"solicitacoes": [_fmt(s) for s in svc.listar()], "fluxo": list(svc.FLUXO)})


@assistencia_producao_peca_bp.route("/api/assistencia-tecnica/producao-pecas/<int:solicitacao_id>/imagem", methods=["GET"])
@permission_required(PERMISSION)
def api_imagem(solicitacao_id):
    s = svc.obter(solicitacao_id)
    if s is None or not s.imagem:
        return jsonify({"error": "Imagem não encontrada."}), 404
    return send_file(BytesIO(s.imagem), mimetype=s.imagem_content_type, download_name=s.imagem_nome or "imagem")


def _acao(solicitacao_id, fn):
    s = svc.obter(solicitacao_id)
    if s is None:
        return jsonify({"error": "Solicitação não encontrada."}), 404
    try:
        s = fn(s, request.get_json(silent=True) or {}, session.get("username", "desconhecido"))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"solicitacao": _fmt(s)})


@assistencia_producao_peca_bp.route("/api/assistencia-tecnica/producao-pecas/<int:solicitacao_id>/efetivar", methods=["POST"])
@permission_required(PERMISSION)
def api_efetivar(solicitacao_id):
    return _acao(solicitacao_id, lambda s, d, u: svc.efetivar(s, u, d.get("status_atual")))


@assistencia_producao_peca_bp.route("/api/assistencia-tecnica/producao-pecas/<int:solicitacao_id>/estornar", methods=["POST"])
@permission_required(PERMISSION)
def api_estornar(solicitacao_id):
    return _acao(solicitacao_id, lambda s, d, u: svc.estornar(s, u, d.get("motivo")))


@assistencia_producao_peca_bp.route("/api/assistencia-tecnica/producao-pecas/<int:solicitacao_id>/cancelar", methods=["POST"])
@permission_required(PERMISSION)
def api_cancelar(solicitacao_id):
    return _acao(solicitacao_id, lambda s, d, u: svc.cancelar(s, u, d.get("motivo")))
