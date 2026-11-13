"""Assistencia Tecnica > Solicitacao de Producao de Pecas.

Duas portas de entrada:
- EDI (sistema externo), sem login, autenticado pelo token no header
  X-Integracao-Token:
    POST /api/edi/assistencia-tecnica/producao-pecas           cria (JSON ou multipart com anexos)
    GET  /api/edi/assistencia-tecnica/producao-pecas           consulta status (?id_externo= / ?protocolo= / ?desde=)
    POST /api/edi/assistencia-tecnica/producao-pecas/anexos    acrescenta documentos (?id_externo= / ?protocolo=)
    GET  /api/edi/assistencia-tecnica/producao-pecas/anexos/<id>  baixa um documento
- Tela da equipe (com login e permissao): lista, movimenta o status e anexa.
"""
from __future__ import annotations

import json
from io import BytesIO
from urllib.parse import quote

from flask import Blueprint, Response, jsonify, render_template, request, send_file, session, stream_with_context

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
        "anexos": [_fmt_anexo(a) for a in s.anexos],
        "anexos_usado_bytes": svc.usado_bytes(s),
        "anexos_limite_bytes": svc.MAX_ANEXOS_BYTES,
    }


def _fmt_anexo(a, url: str | None = None) -> dict:
    return {
        "id": a.id, "nome": a.nome, "tamanho": a.tamanho, "content_type": a.content_type,
        "origem": a.origem, "enviado_por": a.enviado_por, "enviado_em": _dt(a.enviado_em),
        **({"url": url} if url else {}),
    }


def _iso(valor):
    return valor.isoformat(timespec="seconds") if valor else None


def _fmt_edi(s) -> dict:
    """O que o sistema de origem le pra replicar: status (com a posicao no
    fluxo), historico e documentos, datas em ISO (horario de Brasilia)."""
    return {
        "id": s.id,
        "protocolo": svc.protocolo(s),
        "id_externo": s.id_externo,
        "status": s.status,
        "etapa": (svc.FLUXO.index(s.status) + 1) if s.status in svc.FLUXO else None,
        "fluxo": list(svc.FLUXO),
        "proximo_status": svc.proximo_status(s.status),
        "finalizada": s.status in svc.STATUS_FINAIS,
        "motivo_cancelamento": s.motivo_cancelamento,
        "prazo": s.prazo.isoformat() if s.prazo else None,
        "atrasada": svc.atrasada(s),
        "recebido_em": _iso(s.recebido_em),
        "atualizado_em": _iso(s.atualizado_em),
        "atualizado_por": s.atualizado_por,
        "historico": [
            {"acao": h.acao, "status_de": h.status_de, "status_para": h.status_para,
             "motivo": h.motivo, "usuario": h.usuario, "em": _iso(h.criado_em)}
            for h in s.historico
        ],
        "anexos": [
            {**_fmt_anexo(a, f"/api/edi/assistencia-tecnica/producao-pecas/anexos/{a.id}"), "enviado_em": _iso(a.enviado_em)}
            for a in s.anexos
        ],
        "anexos_usado_bytes": svc.usado_bytes(s),
        "anexos_limite_bytes": svc.MAX_ANEXOS_BYTES,
    }


# O app nao tem MAX_CONTENT_LENGTH: recusa antes de ler um corpo maior que
# o limite de anexos (+ folga do base64 e dos campos).
_LIMITE_CORPO = int(svc.MAX_ANEXOS_BYTES * 1.4) + 1024 * 1024


def _corpo_grande_demais():
    if (request.content_length or 0) > _LIMITE_CORPO:
        return jsonify({"error": "Envio maior que o limite de 100 MB de anexos por solicitação."}), 413
    return None


def _arquivos_multipart() -> list:
    return [(f.filename, f.stream) for f in request.files.getlist("anexos") if f and f.filename]


def _enviar_anexo(anexo):
    inline = svc.extensao(anexo.nome) in svc.ABRE_NO_NAVEGADOR
    resp = Response(stream_with_context(svc.conteudo_anexo(anexo.id)),
                    mimetype=anexo.content_type if inline else "application/octet-stream")
    resp.headers["Content-Length"] = str(anexo.tamanho)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Content-Disposition"] = (
        f"{'inline' if inline else 'attachment'}; filename*=UTF-8''{quote(anexo.nome)}"
    )
    return resp


def _token_informado() -> str:
    token = str(request.headers.get("X-Integracao-Token") or "").strip()
    if token:
        return token
    auth = str(request.headers.get("Authorization") or "").strip()
    return auth[7:].strip() if auth.lower().startswith("bearer ") else ""


# ── EDI ─────────────────────────────────────────────────────────────────
def _autenticar_edi():
    if not svc.token_edi_configurado():
        return jsonify({"error": "Integração não configurada no Sync."}), 503
    if not svc.token_valido(_token_informado()):
        return jsonify({"error": "Token inválido."}), 401
    return None


@assistencia_producao_peca_bp.route("/api/edi/assistencia-tecnica/producao-pecas", methods=["POST"])
def edi_receber_solicitacao():
    erro = _autenticar_edi() or _corpo_grande_demais()
    if erro:
        return erro
    # JSON (anexos em base64) ou multipart: campo "dados" com o JSON e os
    # arquivos em "anexos" (varios).
    if request.mimetype == "multipart/form-data":
        try:
            dados = json.loads(request.form.get("dados") or "{}")
        except ValueError:
            return jsonify({"error": "Campo dados não é um JSON válido."}), 400
        arquivos = _arquivos_multipart()
    else:
        dados, arquivos = request.get_json(silent=True), []
    try:
        solicitacao, criada = svc.receber_edi(dados, arquivos)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({
        "id": solicitacao.id,
        "protocolo": svc.protocolo(solicitacao),
        "status": solicitacao.status,
        "duplicada": not criada,
        "anexos": len(solicitacao.anexos),
    }), (201 if criada else 200)


def _referencia_edi():
    a = request.args
    if not (a.get("id_externo") or a.get("protocolo")):
        return None, (jsonify({"error": "Informe id_externo ou protocolo."}), 400)
    s = svc.obter_por_referencia(a.get("id_externo"), a.get("protocolo"))
    if s is None:
        return None, (jsonify({"error": "Solicitação não encontrada."}), 404)
    return s, None


@assistencia_producao_peca_bp.route("/api/edi/assistencia-tecnica/producao-pecas", methods=["GET"])
def edi_consultar_status():
    erro = _autenticar_edi()
    if erro:
        return erro
    a = request.args
    if a.get("id_externo") or a.get("protocolo"):
        s, erro = _referencia_edi()
        if erro:
            return erro
        return jsonify({"solicitacoes": [_fmt_edi(s)]})
    if not a.get("desde"):
        return jsonify({"error": "Informe id_externo, protocolo ou desde (AAAA-MM-DDTHH:MM:SS)."}), 400
    try:
        lista = svc.alteradas_desde(a.get("desde"))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"solicitacoes": [_fmt_edi(s) for s in lista]})


@assistencia_producao_peca_bp.route("/api/edi/assistencia-tecnica/producao-pecas/anexos", methods=["POST"])
def edi_anexar():
    erro = _autenticar_edi() or _corpo_grande_demais()
    if erro:
        return erro
    s, erro = _referencia_edi()
    if erro:
        return erro
    try:
        arquivos = _arquivos_multipart() if request.mimetype == "multipart/form-data" \
            else svc.anexos_base64(request.get_json(silent=True) or {})
        svc.anexar(s, arquivos, svc.Anexo.ORIGEM_EDI, svc.USUARIO_EDI)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"solicitacoes": [_fmt_edi(s)]}), 201


@assistencia_producao_peca_bp.route("/api/edi/assistencia-tecnica/producao-pecas/anexos/<int:anexo_id>", methods=["GET"])
def edi_baixar_anexo(anexo_id):
    erro = _autenticar_edi()
    if erro:
        return erro
    anexo = svc.obter_anexo(None, anexo_id)
    if anexo is None:
        return jsonify({"error": "Anexo não encontrado."}), 404
    return _enviar_anexo(anexo)


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


@assistencia_producao_peca_bp.route("/api/assistencia-tecnica/producao-pecas/<int:solicitacao_id>/anexos", methods=["POST"])
@permission_required(PERMISSION)
def api_anexar(solicitacao_id):
    erro = _corpo_grande_demais()
    if erro:
        return erro
    s = svc.obter(solicitacao_id)
    if s is None:
        return jsonify({"error": "Solicitação não encontrada."}), 404
    try:
        svc.anexar(s, _arquivos_multipart(), svc.Anexo.ORIGEM_SYNC, session.get("username", "desconhecido"))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"solicitacao": _fmt(s)})


@assistencia_producao_peca_bp.route("/api/assistencia-tecnica/producao-pecas/<int:solicitacao_id>/anexos/<int:anexo_id>", methods=["GET"])
@permission_required(PERMISSION)
def api_baixar_anexo(solicitacao_id, anexo_id):
    anexo = svc.obter_anexo(solicitacao_id, anexo_id)
    if anexo is None:
        return jsonify({"error": "Anexo não encontrado."}), 404
    return _enviar_anexo(anexo)


@assistencia_producao_peca_bp.route("/api/assistencia-tecnica/producao-pecas/<int:solicitacao_id>/anexos/<int:anexo_id>", methods=["DELETE"])
@permission_required(PERMISSION)
def api_remover_anexo(solicitacao_id, anexo_id):
    anexo = svc.obter_anexo(solicitacao_id, anexo_id)
    if anexo is None:
        return jsonify({"error": "Anexo não encontrado."}), 404
    s = anexo.solicitacao
    try:
        svc.remover_anexo(s, anexo, session.get("username", "desconhecido"))
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
