"""Fila de endereçamento do recebimento e leituras pela câmera."""
from flask import Blueprint, jsonify, render_template, request, session
from sqlalchemy import or_

from ..auth import permission_required, has_permission
from ..extensions import db
from ..models import (ItemNota, LocalizacaoArmazem, RecebimentoEnderecamento as Tarefa,
                      RecebimentoEnderecamentoEvento as Evento)
from ..services import recebimento_enderecamento_service as svc

recebimento_enderecamento_bp = Blueprint("recebimento_enderecamento", __name__)
PERMISSION = "PAGE_RECEBIMENTO_ENDERECAMENTO"
MANAGE = "MANAGE_RECEBIMENTO_ENDERECAMENTO"


def serializar(t):
    return {"id": t.id, "item_id": t.item_nota_id, "nota": t.item.numero_nota,
            "chave": t.item.chave_acesso, "fornecedor": t.item.fornecedor,
            "descricao": t.item.descricao, "sku": t.sku, "quantidade": t.quantidade,
            "conferencia_por": t.item.usuario_conferencia,
            "conferencia_em": t.item.fim_conferencia.isoformat() if t.item.fim_conferencia else None,
            "unidade": t.unidade, "status": t.status, "alocacoes": t.alocacoes,
            "criado_em": t.criado_em.isoformat(), "confirmado_por": t.confirmado_por,
            "concluido_em": t.concluido_em.isoformat() if t.concluido_em else None,
            "erro": t.erro, "enviado": t.enderecos_enviados}


@recebimento_enderecamento_bp.get("/recebimento/enderecamento")
@permission_required(PERMISSION)
def pagina():
    return render_template("recebimento_enderecamento.html", pode_gerenciar=has_permission(MANAGE))


@recebimento_enderecamento_bp.get("/api/recebimento/enderecamento")
@permission_required(PERMISSION)
def listar():
    query = Tarefa.query.join(ItemNota)
    busca = str(request.args.get("busca") or "").strip()[:100]
    if busca:
        query = query.filter(or_(ItemNota.numero_nota.contains(busca, autoescape=True),
                                 Tarefa.sku.contains(busca, autoescape=True),
                                 ItemNota.descricao.contains(busca, autoescape=True),
                                 ItemNota.fornecedor.contains(busca, autoescape=True)))
    ids = request.args.get("ids", "")
    if ids:
        query = query.filter(Tarefa.id.in_([int(x) for x in ids.split(",") if x.isdigit()][:100]))
    status = request.args.get("status", "Pendente")
    if status not in ("Pendente", "Aguardando sincronização", "Concluído"):
        return jsonify(erro="Status inválido."), 400
    contadores = {s: query.filter(Tarefa.status == s).count() for s in (
        "Pendente", "Aguardando sincronização", "Concluído")}
    pagina = max(1, request.args.get("pagina", 1, type=int))
    tarefas = query.filter(Tarefa.status == status).order_by(Tarefa.criado_em, Tarefa.id).offset((pagina-1)*40).limit(40).all()
    return jsonify(itens=[serializar(t) for t in tarefas], contadores=contadores, pagina=pagina)


@recebimento_enderecamento_bp.get("/api/recebimento/enderecamento/<int:tarefa_id>/historico")
@permission_required(PERMISSION)
def historico(tarefa_id):
    t = db.get_or_404(Tarefa, tarefa_id)
    eventos = Evento.query.filter_by(tarefa_id=t.id).order_by(Evento.id).all()
    return jsonify(item=serializar(t), eventos=[{"tipo": e.tipo, "usuario": e.usuario,
                   "data": e.criado_em.isoformat(), "detalhes": e.detalhes} for e in eventos])


@recebimento_enderecamento_bp.post("/api/recebimento/enderecamento/<int:tarefa_id>/<acao>")
@permission_required(PERMISSION)
def operar(tarefa_id, acao):
    tarefa = db.get_or_404(Tarefa, tarefa_id)
    dados = request.get_json(silent=True) or {}
    if not isinstance(dados, dict):
        return jsonify(erro="Dados inválidos."), 400
    try:
        if acao == "leitura":
            return jsonify(svc.registrar_leitura(tarefa, dados.get("tipo"), dados.get("codigo")))
        if acao == "confirmar":
            svc.confirmar(tarefa, dados, has_permission(MANAGE))
            svc.sincronizar(tarefa)
        elif acao == "sincronizar":
            svc.sincronizar(tarefa)
        elif acao == "reabrir":
            if not has_permission(MANAGE):
                return jsonify(erro="Sem permissão para revisar o endereçamento."), 403
            svc.reabrir(tarefa, dados.get("justificativa"))
        else:
            return jsonify(erro="Ação inválida."), 404
        return jsonify(item=serializar(tarefa))
    except ValueError as exc:
        db.session.rollback()
        return jsonify(erro=str(exc)), 409
    except Exception:
        db.session.rollback()
        from flask import current_app
        current_app.logger.exception("Falha na operação de endereçamento")
        return jsonify(erro="Não foi possível consultar o GRV. Nenhum endereçamento foi confirmado. Tente novamente."), 502


@recebimento_enderecamento_bp.route("/api/recebimento/enderecamento/locais", methods=["GET", "POST"])
@permission_required(MANAGE)
def locais():
    if request.method == "POST":
        dados = request.get_json(silent=True) or {}
        codigo = str(dados.get("codigo") or "").strip()
        if not codigo or len(codigo) > 80 or ";" in codigo:
            return jsonify(erro="Informe um código de endereço de até 80 caracteres, sem ponto e vírgula."), 400
        local = LocalizacaoArmazem.query.filter_by(codigo=codigo).first()
        if not local:
            local = LocalizacaoArmazem(codigo=codigo, corredor="", prateleira="", posicao="")
            db.session.add(local)
        local.ativo = dados.get("ativo") is True
        db.session.commit()
    return jsonify(locais=[{"codigo": l.codigo, "ativo": l.ativo} for l in
                          LocalizacaoArmazem.query.order_by(LocalizacaoArmazem.codigo).all()])
