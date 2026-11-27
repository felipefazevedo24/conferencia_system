"""Rotas do modulo Projetos (gestao de projetos com tarefas dependentes).

PAGE_PROJETOS: ve os projetos e atualiza as tarefas (iniciar, concluir,
reabrir, observacao). PAGE_PROJETOS_GESTAO: cria/edita/exclui projetos e
tarefas, dependencias e importa a planilha. Regra de negocio no service."""
from __future__ import annotations

from io import BytesIO

from flask import Blueprint, jsonify, render_template, request, send_file, session

from ..auth import has_permission, permission_required, permission_required_any
from ..services import projetos_service as svc

projetos_bp = Blueprint("projetos", __name__)

PERMISSION = "PAGE_PROJETOS"
PERMISSION_GESTAO = "PAGE_PROJETOS_GESTAO"


def _usuario() -> str:
    return session.get("username", "desconhecido")


def _executar(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs), None
    except ValueError as exc:
        return None, (jsonify({"error": str(exc)}), 400)


@projetos_bp.route("/projetos")
@permission_required_any(PERMISSION, PERMISSION_GESTAO)
def projetos_page():
    return render_template("projetos.html", user=_usuario(), meu_nome=svc.nome_usuario(_usuario()),
                           pode_gerir=has_permission(PERMISSION_GESTAO))


@projetos_bp.route("/api/projetos/painel")
@permission_required_any(PERMISSION, PERMISSION_GESTAO)
def api_painel():
    return jsonify(svc.painel(_usuario()))


@projetos_bp.route("/api/projetos")
@permission_required_any(PERMISSION, PERMISSION_GESTAO)
def api_listar():
    return jsonify({"projetos": svc.listar_projetos()})


@projetos_bp.route("/api/projetos/usuarios")
@permission_required_any(PERMISSION, PERMISSION_GESTAO)
def api_usuarios():
    return jsonify({"usuarios": svc.usuarios_ativos()})


@projetos_bp.route("/api/projetos/<int:projeto_id>")
@permission_required_any(PERMISSION, PERMISSION_GESTAO)
def api_obter(projeto_id):
    dados = svc.obter_projeto(projeto_id)
    if dados is None:
        return jsonify({"error": "Projeto não encontrado."}), 404
    return jsonify(dados)


@projetos_bp.route("/api/projetos", methods=["POST"])
@permission_required(PERMISSION_GESTAO)
def api_criar():
    p, erro = _executar(svc.salvar_projeto, request.get_json(silent=True) or {}, _usuario())
    return erro or (jsonify({"id": p.id}), 201)


@projetos_bp.route("/api/projetos/<int:projeto_id>", methods=["PUT"])
@permission_required(PERMISSION_GESTAO)
def api_editar(projeto_id):
    p, erro = _executar(svc.salvar_projeto, request.get_json(silent=True) or {}, _usuario(), projeto_id)
    return erro or jsonify({"id": p.id})


@projetos_bp.route("/api/projetos/<int:projeto_id>", methods=["DELETE"])
@permission_required(PERMISSION_GESTAO)
def api_excluir(projeto_id):
    _, erro = _executar(svc.excluir_projeto, projeto_id)
    return erro or jsonify({"ok": True})


@projetos_bp.route("/api/projetos/importar", methods=["POST"])
@permission_required(PERMISSION_GESTAO)
def api_importar():
    arquivo = request.files.get("arquivo")
    if not arquivo or not arquivo.filename:
        return jsonify({"error": "Envie a planilha (.xlsx)."}), 400
    res, erro = _executar(svc.importar_planilha, BytesIO(arquivo.read()), request.form.get("nome"), _usuario())
    if erro:
        return erro
    p, avisos = res
    return jsonify({"id": p.id, "tarefas": len(p.tarefas), "avisos": avisos}), 201


@projetos_bp.route("/api/projetos/<int:projeto_id>/excel")
@permission_required_any(PERMISSION, PERMISSION_GESTAO)
def api_excel(projeto_id):
    res, erro = _executar(svc.exportar_xlsx, projeto_id)
    if erro:
        return erro
    conteudo, nome = res
    return send_file(BytesIO(conteudo), as_attachment=True, download_name=nome,
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@projetos_bp.route("/api/projetos/<int:projeto_id>/tarefas", methods=["POST"])
@permission_required(PERMISSION_GESTAO)
def api_criar_tarefa(projeto_id):
    t, erro = _executar(svc.salvar_tarefa, projeto_id, request.get_json(silent=True) or {}, _usuario())
    return erro or (jsonify({"id": t.id, "numero": t.numero}), 201)


@projetos_bp.route("/api/projetos/<int:projeto_id>/tarefas/<int:tarefa_id>", methods=["PUT"])
@permission_required(PERMISSION_GESTAO)
def api_editar_tarefa(projeto_id, tarefa_id):
    t, erro = _executar(svc.salvar_tarefa, projeto_id, request.get_json(silent=True) or {}, _usuario(), tarefa_id)
    return erro or jsonify({"id": t.id, "numero": t.numero})


@projetos_bp.route("/api/projetos/tarefas/<int:tarefa_id>", methods=["DELETE"])
@permission_required(PERMISSION_GESTAO)
def api_excluir_tarefa(tarefa_id):
    _, erro = _executar(svc.excluir_tarefa, tarefa_id)
    return erro or jsonify({"ok": True})


@projetos_bp.route("/api/projetos/tarefas/<int:tarefa_id>/iniciar", methods=["POST"])
@permission_required_any(PERMISSION, PERMISSION_GESTAO)
def api_iniciar(tarefa_id):
    _, erro = _executar(svc.iniciar, tarefa_id, _usuario())
    return erro or jsonify({"ok": True})


@projetos_bp.route("/api/projetos/tarefas/<int:tarefa_id>/concluir", methods=["POST"])
@permission_required_any(PERMISSION, PERMISSION_GESTAO)
def api_concluir(tarefa_id):
    payload = request.get_json(silent=True) or {}
    res, erro = _executar(svc.concluir, tarefa_id, _usuario(), payload.get("termino_real"))
    if erro:
        return erro
    _, liberadas = res
    return jsonify({"ok": True, "liberadas": [t.numero for t in liberadas]})


@projetos_bp.route("/api/projetos/tarefas/<int:tarefa_id>/reabrir", methods=["POST"])
@permission_required_any(PERMISSION, PERMISSION_GESTAO)
def api_reabrir(tarefa_id):
    _, erro = _executar(svc.reabrir, tarefa_id)
    return erro or jsonify({"ok": True})


@projetos_bp.route("/api/projetos/tarefas/<int:tarefa_id>/observacao", methods=["POST"])
@permission_required_any(PERMISSION, PERMISSION_GESTAO)
def api_observacao(tarefa_id):
    _, erro = _executar(svc.salvar_observacao, tarefa_id, (request.get_json(silent=True) or {}).get("observacao"))
    return erro or jsonify({"ok": True})
