"""Rotas da Homologacao de Fornecedores (Compras) - formulario F-COM-001-01."""
from __future__ import annotations

from io import BytesIO

from flask import Blueprint, jsonify, render_template, request, send_file, session

from ..auth import permission_required
from ..extensions import db
from ..models import (
    ComprasHomologacaoFornecedor as Homologacao,
    ComprasHomologacaoFoto,
)
from ..services import compras_homologacao_form as form
from ..services import compras_homologacao_pdf as pdf_svc
from ..services import compras_homologacao_service as svc

compras_homologacao_bp = Blueprint("compras_homologacao", __name__)

PERMISSION = "PAGE_COMPRAS_HOMOLOGACAO"


def _dt(valor):
    return valor.strftime("%d/%m/%Y %H:%M") if valor else None


def _fmt(homologacao: Homologacao, completo: bool = False) -> dict:
    validade = svc.situacao_validade(homologacao)
    dados = {
        "id": homologacao.id,
        "razao_social": homologacao.razao_social,
        "nome_fantasia": homologacao.nome_fantasia,
        "cnpj": homologacao.cnpj,
        "categoria_compra": homologacao.categoria_compra,
        "status": homologacao.status,
        "nota": homologacao.nota,
        "classificacao": homologacao.classificacao,
        "validade_meses": homologacao.validade_meses,
        "valido_ate": homologacao.valido_ate.isoformat() if homologacao.valido_ate else None,
        "validade_situacao": validade["situacao"],
        "validade_dias_restantes": validade["dias_restantes"],
        "criado_em": _dt(homologacao.criado_em),
        "criado_por": homologacao.criado_por,
        "enviado_em": _dt(homologacao.enviado_em),
        "enviado_por": homologacao.enviado_por,
        "decidido_em": _dt(homologacao.decidido_em),
        "decidido_por": homologacao.decidido_por,
        "justificativa_decisao": homologacao.justificativa_decisao,
        "qtd_fotos": len(homologacao.fotos),
    }
    if not completo:
        return dados

    dados.update({
        "inscricao_estadual": homologacao.inscricao_estadual,
        "endereco": homologacao.endereco,
        "cidade_estado": homologacao.cidade_estado,
        "contato_principal": homologacao.contato_principal,
        "telefone": homologacao.telefone,
        "email": homologacao.email,
        "website": homologacao.website,
        "descricao_produto_servico": homologacao.descricao_produto_servico,
        "resultado_auditoria": homologacao.resultado_auditoria,
        "obs_conformidade_legal": homologacao.obs_conformidade_legal,
        "comentario": homologacao.comentario,
    })

    respostas = {(r.secao, r.item): r for r in homologacao.respostas}
    _, _, detalhe = svc.calcular_nota({c: r.resposta for c, r in respostas.items()})
    dados["detalhe_secoes"] = detalhe
    dados["secoes"] = [
        {
            "chave": secao["chave"],
            "titulo": secao["titulo"],
            "peso": secao["peso"],
            "escala": list(secao["escala"]),
            "itens": [
                {
                    "item": i,
                    "texto": texto,
                    "resposta": (respostas.get((secao["chave"], i)).resposta
                                 if respostas.get((secao["chave"], i)) else None),
                    "comentario": (respostas.get((secao["chave"], i)).comentario
                                   if respostas.get((secao["chave"], i)) else None),
                }
                for i, texto in enumerate(secao["itens"], start=1)
            ],
        }
        for secao in form.SECOES
    ]
    dados["fotos"] = [
        {
            "id": f.id,
            "nome_arquivo": f.nome_arquivo,
            "legenda": f.legenda,
            "tamanho_bytes": f.tamanho_bytes,
            "enviado_em": _dt(f.enviado_em),
            "enviado_por": f.enviado_por,
            "url": f"/api/compras/homologacao/fotos/{f.id}",
        }
        for f in homologacao.fotos
    ]
    dados["itens_faltando"] = len(svc.itens_faltando(homologacao))
    return dados


def _obter(homologacao_id: int) -> Homologacao | None:
    return db.session.get(Homologacao, homologacao_id)


def _usuario() -> str:
    return session.get("username", "desconhecido")


@compras_homologacao_bp.route("/compras/homologacao")
@permission_required(PERMISSION)
def homologacao_page():
    return render_template(
        "compras_homologacao.html",
        user=session["username"],
        user_role=session.get("role", ""),
    )


@compras_homologacao_bp.route("/api/compras/homologacao", methods=["GET"])
@permission_required(PERMISSION)
def api_listar():
    registros = svc.listar(
        status=request.args.get("status") or "",
        busca=request.args.get("busca") or "",
        validade=request.args.get("validade") or "",
    )
    return jsonify({
        "homologacoes": [_fmt(r) for r in registros],
        "metricas": svc.metricas(svc.listar()),
        "formulario": {
            "codigo": form.CODIGO_FORMULARIO,
            "total_itens": form.total_itens(),
            "nota_minima_aprovado": form.NOTA_MINIMA_APROVADO,
            "nota_minima_ressalvas": form.NOTA_MINIMA_RESSALVAS,
        },
    })


@compras_homologacao_bp.route("/api/compras/homologacao/modelo", methods=["GET"])
@permission_required(PERMISSION)
def api_modelo():
    """Formulario em branco - usado pra montar a tela de cadastro."""
    return jsonify({
        "codigo": form.CODIGO_FORMULARIO,
        "obs_conformidade_legal": form.OBS_CONFORMIDADE_LEGAL,
        "secoes": [
            {
                "chave": s["chave"],
                "titulo": s["titulo"],
                "peso": s["peso"],
                "escala": list(s["escala"]),
                "itens": [{"item": i, "texto": t, "resposta": None, "comentario": None}
                          for i, t in enumerate(s["itens"], start=1)],
            }
            for s in form.SECOES
        ],
    })


@compras_homologacao_bp.route("/api/compras/homologacao/<int:homologacao_id>", methods=["GET"])
@permission_required(PERMISSION)
def api_detalhe(homologacao_id):
    homologacao = _obter(homologacao_id)
    if not homologacao:
        return jsonify({"error": "Homologação não encontrada."}), 404
    return jsonify({"homologacao": _fmt(homologacao, completo=True)})


@compras_homologacao_bp.route("/api/compras/homologacao", methods=["POST"])
@permission_required(PERMISSION)
def api_criar():
    payload = request.get_json(silent=True) or {}
    try:
        homologacao = svc.criar(payload, _usuario())
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"message": "Homologação criada.", "homologacao": _fmt(homologacao, completo=True)})


@compras_homologacao_bp.route("/api/compras/homologacao/<int:homologacao_id>", methods=["PUT"])
@permission_required(PERMISSION)
def api_atualizar(homologacao_id):
    homologacao = _obter(homologacao_id)
    if not homologacao:
        return jsonify({"error": "Homologação não encontrada."}), 404
    payload = request.get_json(silent=True) or {}
    try:
        homologacao = svc.atualizar(homologacao, payload)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"message": "Alterações salvas.", "homologacao": _fmt(homologacao, completo=True)})


@compras_homologacao_bp.route("/api/compras/homologacao/<int:homologacao_id>", methods=["DELETE"])
@permission_required(PERMISSION)
def api_excluir(homologacao_id):
    homologacao = _obter(homologacao_id)
    if not homologacao:
        return jsonify({"error": "Homologação não encontrada."}), 404
    try:
        svc.excluir(homologacao)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"message": "Homologação excluída."})


def _acao(homologacao_id, funcao, mensagem, **kwargs):
    homologacao = _obter(homologacao_id)
    if not homologacao:
        return jsonify({"error": "Homologação não encontrada."}), 404
    try:
        homologacao = funcao(homologacao, **kwargs)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"message": mensagem, "homologacao": _fmt(homologacao, completo=True)})


@compras_homologacao_bp.route("/api/compras/homologacao/<int:homologacao_id>/enviar", methods=["POST"])
@permission_required(PERMISSION)
def api_enviar(homologacao_id):
    return _acao(homologacao_id, svc.enviar_para_aprovacao,
                 "Enviado para aprovação.", usuario=_usuario())


@compras_homologacao_bp.route("/api/compras/homologacao/<int:homologacao_id>/devolver", methods=["POST"])
@permission_required(PERMISSION)
def api_devolver(homologacao_id):
    payload = request.get_json(silent=True) or {}
    return _acao(homologacao_id, svc.devolver_para_rascunho,
                 "Devolvido para rascunho.", usuario=_usuario(), motivo=payload.get("motivo") or "")


@compras_homologacao_bp.route("/api/compras/homologacao/<int:homologacao_id>/homologar", methods=["POST"])
@permission_required(PERMISSION)
def api_homologar(homologacao_id):
    payload = request.get_json(silent=True) or {}
    return _acao(homologacao_id, svc.homologar, "Fornecedor homologado.",
                 usuario=_usuario(), justificativa=payload.get("justificativa") or "")


@compras_homologacao_bp.route("/api/compras/homologacao/<int:homologacao_id>/reprovar", methods=["POST"])
@permission_required(PERMISSION)
def api_reprovar(homologacao_id):
    payload = request.get_json(silent=True) or {}
    return _acao(homologacao_id, svc.reprovar, "Fornecedor reprovado.",
                 usuario=_usuario(), justificativa=payload.get("justificativa") or "")


@compras_homologacao_bp.route("/api/compras/homologacao/<int:homologacao_id>/reabrir", methods=["POST"])
@permission_required(PERMISSION)
def api_reabrir(homologacao_id):
    return _acao(homologacao_id, svc.reabrir, "Homologação reaberta como rascunho.", usuario=_usuario())


@compras_homologacao_bp.route("/api/compras/homologacao/<int:homologacao_id>/pdf", methods=["GET"])
@permission_required(PERMISSION)
def api_pdf(homologacao_id):
    homologacao = _obter(homologacao_id)
    if not homologacao:
        return jsonify({"error": "Homologação não encontrada."}), 404
    conteudo = pdf_svc.gerar_pdf_homologacao(homologacao)
    nome = f"{form.CODIGO_FORMULARIO}_{(homologacao.razao_social or 'fornecedor')[:40]}.pdf"
    return send_file(
        BytesIO(conteudo), mimetype="application/pdf",
        as_attachment=False, download_name=nome.replace("/", "-"),
    )


# ── Fotos (secao 8) ─────────────────────────────────────────────────────
@compras_homologacao_bp.route("/api/compras/homologacao/<int:homologacao_id>/fotos", methods=["POST"])
@permission_required(PERMISSION)
def api_anexar_foto(homologacao_id):
    homologacao = _obter(homologacao_id)
    if not homologacao:
        return jsonify({"error": "Homologação não encontrada."}), 404
    try:
        svc.anexar_foto(
            homologacao, request.files.get("arquivo"), _usuario(),
            legenda=request.form.get("legenda") or "",
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"message": "Foto anexada.", "homologacao": _fmt(homologacao, completo=True)})


@compras_homologacao_bp.route("/api/compras/homologacao/fotos/<int:foto_id>", methods=["GET"])
@permission_required(PERMISSION)
def api_baixar_foto(foto_id):
    foto = db.session.get(ComprasHomologacaoFoto, foto_id)
    if not foto or not foto.dados:
        return jsonify({"error": "Foto não encontrada."}), 404
    return send_file(
        BytesIO(foto.dados), mimetype=foto.content_type or "image/jpeg",
        as_attachment=False, download_name=foto.nome_arquivo or f"foto_{foto.id}.jpg",
    )


@compras_homologacao_bp.route("/api/compras/homologacao/fotos/<int:foto_id>", methods=["DELETE"])
@permission_required(PERMISSION)
def api_remover_foto(foto_id):
    foto = db.session.get(ComprasHomologacaoFoto, foto_id)
    if not foto:
        return jsonify({"error": "Foto não encontrada."}), 404
    try:
        svc.remover_foto(foto)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"message": "Foto removida."})
