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


def skus_que_nao_enderecam(query, so_cache=False):
    """Dos SKUs com tarefa aberta, quais são de família/grupo que não endereça.

    A consulta é restrita aos SKUs que têm tarefa, para o filtro não virar um
    IN gigante. Falha do GRV não filtra nada: esconder trabalho da fila é pior
    que deixar passar um item que não seria endereçado. No modo `so_cache`, da
    atualização automática da tela, não se espera a bridge em nenhum caso."""
    from ..services import erp_estoque_service
    from ..services import enderecamento_service as modulo_svc
    skus = [s for (s,) in query.with_entities(Tarefa.sku).distinct() if s]
    if not skus:
        return set()
    try:
        estoque = (erp_estoque_service.estoque_grv_em_cache() if so_cache
                   else erp_estoque_service.buscar_estoque_grv())
        por_codigo = (estoque or {}).get('por_codigo') or {}
    except Exception:
        current_app.logger.warning('Fila de endereçamento sem o GRV: nada foi filtrado por família.',
                                   exc_info=True)
        return set()
    return {sku for sku in skus
            if modulo_svc.sem_enderecamento(
                (por_codigo.get(str(sku).strip().upper()) or {}).get('familia'),
                (por_codigo.get(str(sku).strip().upper()) or {}).get('grupo'))}


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
    # Item sem vínculo de SKU não tem como ser endereçado, e material de
    # família/grupo que não endereça nunca vai ter endereço: nenhum dos dois é
    # pendência de endereçamento. Os dois voltam sozinhos se o cadastro mudar.
    excluidos = skus_que_nao_enderecam(query.filter(Tarefa.status == "Pendente"),
                                       so_cache=request.args.get("auto") == "1")

    def pendencia_real(consulta):
        consulta = consulta.filter(ItemNota.codigo_grv.isnot(None), ItemNota.codigo_grv != "")
        return consulta.filter(~Tarefa.sku.in_(excluidos)) if excluidos else consulta

    contadores = {s: (pendencia_real(query) if s == "Pendente" else query)
                  .filter(Tarefa.status == s).count()
                  for s in ("Pendente", "Aguardando sincronização", "Concluído")}
    inicio = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    concluidos_hoje = query.filter(Tarefa.status == "Concluído",
        Tarefa.concluido_em >= inicio, Tarefa.concluido_em < inicio + timedelta(days=1)).count()
    pagina = max(1, request.args.get("pagina", 1, type=int))
    listagem = pendencia_real(query) if status == "Pendente" else query
    tarefas = listagem.filter(Tarefa.status == status).order_by(Tarefa.criado_em.asc(), Tarefa.id.asc()).offset((pagina-1)*40).limit(40).all()
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
    """Endereços ocupados segundo o GRV, que é quem controla o saldo.

    O Sync não guarda cópia disso: a lista é montada do snapshot do estoque do
    GRV, e a falha da bridge não pode derrubar a tela."""
    from ..models import EnderecoMovimento as Movimento
    from ..services.erp_estoque_service import buscar_estoque_grv
    busca = str(request.args.get('busca') or '').strip()[:100].upper()
    pagina = max(1, request.args.get('pagina', 1, type=int))
    pendentes = Movimento.query.filter_by(sincronizado_em=None).count()
    try:
        estoque = buscar_estoque_grv(forcar_atualizacao=request.args.get('atualizar') == '1')
    except Exception:
        current_app.logger.exception('Falha ao consultar os endereços no GRV')
        return jsonify(itens=[], total=0, pagina=pagina, indisponivel=True,
                       metricas=dict(materiais='—', enderecos='—', sincronizar=pendentes),
                       erro='Endereços indisponíveis: a consulta ao GRV falhou. Tente atualizar em instantes.')
    from ..services import enderecamento_service as modulo_svc
    filtro = request.args.get('filtro') or 'todos'
    so_com_saldo = request.args.get('saldo') == '1'
    registros, sem_endereco = [], []
    for sku, agregado in (estoque.get('por_codigo') or {}).items():
        # Serviço e uso-e-consumo sem estoque nunca vão ter endereço: listar
        # esses itens como pendência seria ruído permanente.
        if modulo_svc.sem_enderecamento(agregado.get('familia'), agregado.get('grupo')):
            continue
        descricao = str(agregado.get('item') or '').strip()
        unidade = str(agregado.get('unidade') or '').strip()
        saldo = float(agregado.get('qtde_total') or 0)
        if so_com_saldo and saldo <= 0:
            continue
        # O GRV guarda os endereços do produto num campo único, separados por ';'.
        vistos = set()
        for bruto in agregado.get('localizacoes') or []:
            for endereco in str(bruto).split(';'):
                endereco = endereco.strip()
                if endereco and endereco not in vistos:
                    vistos.add(endereco)
                    registros.append((endereco, sku, descricao, unidade, saldo))
        if not vistos:
            sem_endereco.append(('', sku, descricao, unidade, saldo))
    materiais = len({r[1] for r in registros})
    enderecos = len({r[0] for r in registros})
    if filtro == 'sem_endereco':
        registros = sem_endereco
    elif filtro == 'com_endereco':
        pass
    else:
        registros = registros + sem_endereco
    if busca:
        registros = [r for r in registros if busca in r[0] or busca in r[1] or busca in r[2].upper()]
    # Sem endereço vai para o fim, como nas outras listagens do sistema.
    registros.sort(key=lambda r: (r[0] == '', r[0], r[1]))
    total = len(registros)
    return jsonify(itens=[dict(endereco=e, sku=s, descricao=d, unidade=u, saldo=saldo)
                          for e, s, d, u, saldo in registros[(pagina-1)*40:pagina*40]],
                   total=total, pagina=pagina,
                   metricas=dict(materiais=materiais, enderecos=enderecos,
                                 sem_endereco=len(sem_endereco), sincronizar=pendentes))


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
        mov_id = modulo_svc.registrar(dados, session['username'])
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


@recebimento_enderecamento_bp.get('/api/enderecamento/material')
@permission_required(PERMISSION)
def material():
    """Onde o material está agora, para a tela mostrar o antes e o depois."""
    from ..services import enderecamento_service as modulo_svc
    try:
        sku = modulo_svc.texto(request.args.get('sku'), 'o SKU', 80)
        return jsonify(sku=sku, enderecos=modulo_svc.enderecos_atuais(sku))
    except ValueError as exc:
        return jsonify(erro=str(exc)), 409


@recebimento_enderecamento_bp.get('/api/enderecamento/locais')
@permission_required(PERMISSION)
def catalogo_de_locais():
    """Todos os endereços conhecidos, inclusive os que estão vazios agora.

    A ocupação vem do GRV; se a consulta falhar, a lista continua de pé sem ela."""
    from ..services.erp_estoque_service import buscar_estoque_grv
    from ..services import enderecamento_service as modulo_svc
    busca = str(request.args.get('busca') or '').strip()[:100].upper()
    ocupacao, indisponivel = {}, False
    try:
        for sku, agregado in (buscar_estoque_grv().get('por_codigo') or {}).items():
            for bruto in agregado.get('localizacoes') or []:
                for endereco in str(bruto).split(';'):
                    endereco = modulo_svc.normalizar(endereco)
                    if endereco:
                        ocupacao.setdefault(endereco, set()).add(sku)
    except Exception:
        current_app.logger.exception('Falha ao consultar a ocupação dos endereços no GRV')
        indisponivel = True
    locais_query = LocalizacaoArmazem.query
    if busca:
        locais_query = locais_query.filter(LocalizacaoArmazem.codigo.contains(busca, autoescape=True))
    itens = [dict(codigo=l.codigo, ativo=bool(l.ativo),
                  materiais=len(ocupacao.get(modulo_svc.normalizar(l.codigo), ())))
             for l in locais_query.order_by(LocalizacaoArmazem.codigo).limit(500)]
    # Endereço que está no GRV mas nunca passou pelo Sync também é um endereço real.
    conhecidos = {modulo_svc.normalizar(l['codigo']) for l in itens}
    itens += [dict(codigo=endereco, ativo=True, materiais=len(skus), fora_do_catalogo=True)
              for endereco, skus in sorted(ocupacao.items())
              if endereco not in conhecidos and (not busca or busca in endereco)]
    itens.sort(key=lambda l: l['codigo'])
    return jsonify(itens=itens, total=len(itens), indisponivel=indisponivel)


@recebimento_enderecamento_bp.route("/api/recebimento/enderecamento/locais", methods=["GET", "POST"])
@permission_required(MANAGE)
def locais():
    if request.method == "POST":
        # Ativar/desativar é só do administrador: desativar tira o endereço de
        # todos os materiais que estão nele, direto no GRV.
        if not is_admin_session():
            return jsonify(erro="Somente administradores podem ativar ou desativar endereços."), 403
        from ..services import enderecamento_service as modulo_svc
        dados = request.get_json(silent=True) or {}
        codigo = str(dados.get("codigo") or "").strip()
        if not codigo or len(codigo) > 80 or ";" in codigo:
            return jsonify(erro="Informe um código de endereço de até 80 caracteres, sem ponto e vírgula."), 400
        ativo = dados.get("ativo") is True
        codigo = modulo_svc.normalizar(codigo)
        local = LocalizacaoArmazem.query.filter_by(codigo=codigo).first()
        if not local:
            local = LocalizacaoArmazem(codigo=codigo, corredor="", prateleira="", posicao="")
            db.session.add(local)
        local.ativo = ativo
        db.session.commit()
        if not ativo:
            try:
                relatorio = modulo_svc.esvaziar(codigo, session["username"])
            except ValueError as exc:
                return jsonify(erro=str(exc)), 409
            except Exception:
                current_app.logger.exception("Falha ao esvaziar o endereço %s", codigo)
                return jsonify(erro="Endereço desativado, mas não foi possível consultar o GRV para "
                                    "limpar os materiais. Tente desativar novamente."), 502
            return jsonify(relatorio=relatorio, locais=catalogo_simples())
    return jsonify(locais=catalogo_simples())


def catalogo_simples():
    return [{"codigo": l.codigo, "ativo": l.ativo} for l in
            LocalizacaoArmazem.query.order_by(LocalizacaoArmazem.codigo).all()]
