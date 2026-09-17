"""Fila de endereçamento do recebimento e leituras pela câmera."""
from datetime import datetime, timedelta

from flask import Blueprint, jsonify, request, session, render_template, current_app
from sqlalchemy import or_

from ..auth import permission_required, roles_required, is_admin_session, has_permission
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
            "erro": t.erro, "enviado": t.enderecos_enviados,
            "impedimento": svc.impedimento(t) if t.status == 'Pendente' else '',
            "recebimento_status": t.item.status}


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
    inicio = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    concluidos_hoje = query.filter(Tarefa.status == "Concluído",
        Tarefa.concluido_em >= inicio, Tarefa.concluido_em < inicio + timedelta(days=1)).count()
    pagina = max(1, request.args.get("pagina", 1, type=int))
    tarefas = query.filter(Tarefa.status == status).order_by(Tarefa.criado_em.asc(), Tarefa.id.asc()).offset((pagina-1)*40).limit(40).all()
    return jsonify(itens=[serializar(t) for t in tarefas], contadores=contadores,
                   concluidos_hoje=concluidos_hoje, pagina=pagina)


def proximo_da_nota(tarefa):
    item = tarefa.item
    query = Tarefa.query.join(ItemNota).filter(Tarefa.status == "Pendente", Tarefa.id != tarefa.id,
        ItemNota.status.in_(['Concluído', 'Lançado']), ItemNota.codigo_grv.isnot(None), ItemNota.codigo_grv != '')
    if item.chave_acesso:
        query = query.filter(ItemNota.chave_acesso == item.chave_acesso)
    elif item.fornecedor:
        query = query.filter(ItemNota.numero_nota == item.numero_nota,
                             ItemNota.fornecedor == item.fornecedor,
                             or_(ItemNota.chave_acesso.is_(None), ItemNota.chave_acesso == ""))
    else:
        return None  # Sem identidade suficiente para avançar com segurança.
    proximo = query.order_by(Tarefa.criado_em.desc(), Tarefa.id.desc()).first()
    return serializar(proximo) if proximo else None


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
            return jsonify(svc.registrar_leitura(tarefa, dados.get("tipo"), dados.get("codigo"),
                                                 manual=bool(dados.get("manual"))))
        if acao == "confirmar":
            svc.confirmar(tarefa, dados, has_permission(MANAGE))
            svc.sincronizar(tarefa)
        elif acao == "sincronizar":
            svc.sincronizar(tarefa)
        elif acao == "reabrir":
            if not is_admin_session():
                return jsonify(erro="Somente administradores podem revisar ou estornar o endereçamento."), 403
            svc.reabrir(tarefa, dados.get("justificativa"))
        else:
            return jsonify(erro="Ação inválida."), 404
        return jsonify(item=serializar(tarefa),
                       proximo=proximo_da_nota(tarefa) if acao == "confirmar" else None)
    except ValueError as exc:
        db.session.rollback()
        return jsonify(erro=str(exc)), 409
    except Exception:
        db.session.rollback()
        from flask import current_app
        current_app.logger.exception("Falha na operação de endereçamento")
        if tarefa.status == 'Aguardando sincronização':
            return jsonify(item=serializar(tarefa), proximo=None,
                           aviso='Leituras salvas. Aguardando sincronização com o GRV.'), 200
        return jsonify(erro="Não foi possível concluir a operação. Atualize a fila e tente novamente."), 502


@recebimento_enderecamento_bp.get('/wms/enderecamento')
@recebimento_enderecamento_bp.get('/recebimento/enderecamento')
@permission_required(PERMISSION)
def modulo():
    return render_template('enderecamento.html', user=session.get('username'))


def movimento_json(m):
    return dict(id=m.id, sku=m.sku, unidade=m.unidade, tipo=m.tipo,
        origem=m.origem, destino=m.destino, quantidade=str(m.quantidade),
        usuario=m.usuario, motivo=m.motivo, detalhes=m.detalhes,
        criado_em=m.criado_em.isoformat(),
        sincronizado=bool(m.sincronizado_em), erro=m.erro)


@recebimento_enderecamento_bp.get('/api/enderecamento/saldos')
@permission_required(PERMISSION)
def saldos():
    from ..models import EnderecoSaldo as Saldo, EnderecoMovimento as Movimento
    from sqlalchemy import func
    busca = str(request.args.get('busca') or '').strip()[:100]
    pagina = max(1, request.args.get('pagina', 1, type=int))
    query = Saldo.query
    if busca:
        query = query.filter(or_(Saldo.sku.contains(busca, autoescape=True), Saldo.endereco.contains(busca, autoescape=True)))
    total = query.count()
    registros = query.order_by(Saldo.sku, Saldo.endereco).offset((pagina-1)*40).limit(40).all()
    return jsonify(itens=[dict(id=s.id, sku=s.sku, endereco=s.endereco, unidade=s.unidade,
        quantidade=str(s.quantidade), conferido=s.conferido) for s in registros], total=total,
        pagina=pagina, metricas=dict(
            materiais=db.session.query(func.count(func.distinct(Saldo.sku))).scalar(),
            enderecos=db.session.query(func.count(func.distinct(Saldo.endereco))).filter(Saldo.quantidade > 0).scalar(),
            conferir=Saldo.query.filter_by(conferido=False).count(),
            sincronizar=Movimento.query.filter_by(sincronizado_em=None).count()))


@recebimento_enderecamento_bp.get('/api/enderecamento/historico')
@permission_required(PERMISSION)
def movimentos():
    from ..models import EnderecoMovimento as Movimento
    query = Movimento.query
    busca = str(request.args.get('busca') or '').strip()[:100]
    if busca:
        query = query.filter(or_(Movimento.sku.contains(busca, autoescape=True),
            Movimento.origem.contains(busca, autoescape=True), Movimento.destino.contains(busca, autoescape=True)))
    if request.args.get('pendentes') == '1':
        query = query.filter(Movimento.sincronizado_em.is_(None))
    pagina = max(1, request.args.get('pagina', 1, type=int))
    total = query.count()
    return jsonify(itens=[movimento_json(m) for m in query.order_by(Movimento.id.desc()).offset((pagina-1)*40).limit(40)],
                   total=total, pagina=pagina)


@recebimento_enderecamento_bp.post('/api/enderecamento/movimentar')
@permission_required(PERMISSION)
def movimentar():
    from ..models import EnderecoMovimento as Movimento
    from ..services import enderecamento_service as modulo_svc
    dados = request.get_json(silent=True)
    if not isinstance(dados, dict):
        return jsonify(erro='Dados inválidos.'), 400
    try:
        mov_id = modulo_svc.registrar(dados, session['username'], has_permission(MANAGE))
    except PermissionError as exc:
        db.session.rollback()
        return jsonify(erro=str(exc)), 403
    except ValueError as exc:
        db.session.rollback()
        return jsonify(erro=str(exc)), 409
    except Exception:
        db.session.rollback()
        current_app.logger.exception('Falha ao registrar movimentação')
        return jsonify(erro='Não foi possível registrar. Tente novamente mantendo os dados desta operação.'), 503
    mov = db.session.get(Movimento, mov_id)
    try:
        modulo_svc.sincronizar(mov.sku)
    except Exception:
        db.session.rollback()
        current_app.logger.exception('Movimentação salva; sincronização será repetida')
    return jsonify(item=movimento_json(db.session.get(Movimento, mov_id)))


@recebimento_enderecamento_bp.post('/api/enderecamento/sincronizar')
@permission_required(PERMISSION)
def sincronizar_movimentos():
    from ..services import enderecamento_service as modulo_svc
    from ..models import EnderecoMovimento as Movimento
    dados = request.get_json(silent=True)
    if not isinstance(dados, dict):
        return jsonify(erro='Dados inválidos.'), 400
    try:
        sku = modulo_svc.texto(dados.get('sku'), 'o SKU', 80)
        modulo_svc.sincronizar(sku)
        return jsonify(pendente=Movimento.query.filter_by(sku=sku, sincronizado_em=None).count() > 0)
    except ValueError as exc:
        db.session.rollback()
        return jsonify(erro=str(exc)), 409


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
