"""Fila de endereçamento do recebimento e leituras pela câmera."""
from datetime import datetime, timedelta

from flask import Blueprint, jsonify, request, session, render_template, current_app
from sqlalchemy import or_

from ..auth import permission_required, roles_required, is_admin_session, has_permission
from ..extensions import db
from ..models import (ItemNota, LocalizacaoArmazem, RecebimentoEnderecamento as Tarefa,
                      RecebimentoEnderecamentoEvento as Evento)
from ..services import recebimento_enderecamento_service as svc
from ..tempo import agora_br

recebimento_enderecamento_bp = Blueprint("recebimento_enderecamento", __name__)
PERMISSION = "PAGE_RECEBIMENTO_ENDERECAMENTO"
MANAGE = "MANAGE_RECEBIMENTO_ENDERECAMENTO"
PEDIR_INVENTARIO = "PEDIR_INVENTARIO_ENDERECAMENTO"


def serializar(t, inventariados=None):
    """`inventariados`: SKUs em análise de inventário, quando quem chama já
    consultou para a lista inteira (evita uma consulta por linha)."""
    from ..services import enderecamento_service as modulo_svc
    sku_vigente = str(t.item.codigo_grv or "").strip() or t.sku
    if inventariados is None:
        inventariados = modulo_svc.skus_em_inventario([sku_vigente])
    return {"id": t.id, "item_id": t.item_nota_id, "nota": t.item.numero_nota,
            "chave": t.item.chave_acesso, "fornecedor": t.item.fornecedor,
            # O SKU vigente é o do item: a tarefa guarda o da criação, que fica
            # vazio quando o vínculo veio depois (NF 24444: lançada no GRV após
            # a conferência e mostrada como "Sem vínculo").
            "descricao": t.item.descricao, "sku": str(t.item.codigo_grv or "").strip() or t.sku,
            "quantidade": t.quantidade, "unidade_nf": t.item.unidade_comercial, "qtd_nf": t.item.qtd_real,
            "conferencia_por": t.item.usuario_conferencia,
            "conferencia_em": t.item.fim_conferencia.isoformat() if t.item.fim_conferencia else None,
            "unidade": t.unidade, "status": t.status, "alocacoes": t.alocacoes,
            "criado_em": t.criado_em.isoformat(), "confirmado_por": t.confirmado_por,
            "concluido_em": t.concluido_em.isoformat() if t.concluido_em else None,
            "erro": t.erro, "enviado": t.enderecos_enviados,
            "impedimento": svc.impedimento(t) if t.status == 'Pendente' else '',
            "em_inventario": str(sku_vigente or "").strip().upper() in inventariados,
            "recebimento_status": t.item.status}


def skus_que_nao_enderecam(query, so_cache=False):
    """Dos SKUs com tarefa aberta, quais não entram na fila: família/grupo que
    não endereça ou sem saldo no GRV (depósito 1) - sem saldo não há o que
    endereçar (decisão da Logística, 06/10/2026).

    A consulta é restrita aos SKUs que têm tarefa, para o filtro não virar um
    IN gigante. Falha do GRV não filtra nada: esconder trabalho da fila é pior
    que deixar passar um item que não seria endereçado. No modo `so_cache`, da
    atualização automática da tela, não se espera a bridge em nenhum caso."""
    from ..services import erp_estoque_service
    from ..services import enderecamento_service as modulo_svc
    skus = [s for (s,) in query.with_entities(ItemNota.codigo_grv).distinct() if s]
    if not skus:
        return set()
    try:
        estoque = (erp_estoque_service.estoque_grv_em_cache() if so_cache
                   else erp_estoque_service.buscar_estoque_grv())
        if estoque is None:
            return None  # só acontece no modo so_cache, com o cache deste processo frio
        por_codigo = (estoque or {}).get('por_codigo') or {}
    except Exception:
        current_app.logger.warning('Fila de endereçamento sem o GRV: nada foi filtrado por família.',
                                   exc_info=True)
        return None
    def nao_endereca(sku):
        agregado = por_codigo.get(str(sku).strip().upper())
        if not agregado:
            return False  # SKU fora do snapshot: sem informação, não esconde
        if float(agregado.get('qtde_total') or 0) <= 0:
            return True
        return modulo_svc.sem_enderecamento(agregado.get('familia'), agregado.get('grupo'), agregado.get('controla_estoque'))

    return {sku for sku in skus if nao_endereca(sku)}


@recebimento_enderecamento_bp.get("/api/recebimento/enderecamento")
@permission_required(PERMISSION)
def listar():
    query = Tarefa.query.join(ItemNota)
    busca = str(request.args.get("busca") or "").strip()[:100]
    if busca:
        query = query.filter(or_(ItemNota.numero_nota.contains(busca, autoescape=True),
                                 Tarefa.sku.contains(busca, autoescape=True),
                                 ItemNota.codigo_grv.contains(busca, autoescape=True),
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
    auto = request.args.get("auto") == "1"
    excluidos = skus_que_nao_enderecam(query.filter(Tarefa.status == "Pendente"), so_cache=auto)
    if excluidos is None:
        # Cada worker do PythonAnywhere tem o próprio cache: na atualização
        # automática, cair num worker frio devolvia a fila SEM o filtro de
        # família (06/10/2026: 10093/002-1, família 03/grupo 2, reaparecia).
        # A tela mantém a lista que já tem.
        if auto:
            return jsonify(sem_filtro=True)
        excluidos = set()

    def pendencia_real(consulta):
        consulta = consulta.filter(ItemNota.codigo_grv.isnot(None), ItemNota.codigo_grv != "")
        return consulta.filter(~ItemNota.codigo_grv.in_(excluidos)) if excluidos else consulta

    contadores = {s: (pendencia_real(query) if s == "Pendente" else query)
                  .filter(Tarefa.status == s).count()
                  for s in ("Pendente", "Aguardando sincronização", "Concluído")}
    inicio = agora_br().replace(hour=0, minute=0, second=0, microsecond=0)
    concluidos_hoje = query.filter(Tarefa.status == "Concluído",
        Tarefa.concluido_em >= inicio, Tarefa.concluido_em < inicio + timedelta(days=1)).count()
    pagina = max(1, request.args.get("pagina", 1, type=int))
    listagem = pendencia_real(query) if status == "Pendente" else query
    tarefas = listagem.filter(Tarefa.status == status).order_by(Tarefa.criado_em.asc(), Tarefa.id.asc()).offset((pagina-1)*40).limit(40).all()
    from ..services import enderecamento_service as modulo_svc
    skus_da_pagina = [str(t.item.codigo_grv or "").strip() or t.sku for t in tarefas]
    inventariados = modulo_svc.skus_em_inventario(skus_da_pagina)
    return jsonify(itens=[serializar(t, inventariados) for t in tarefas], contadores=contadores,
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


def _produto_acabado_da_os(busca):
    """'11078', 'OS 11078' ou '11078/001' -> {sku: {n_os, titulo, ...}}.

    Falha da bridge não derruba a busca: devolve vazio e a busca por texto segue."""
    import re

    achado = re.fullmatch(r'(?:OS\s*)?(\d{3,7})(?:/\d+)?', busca.strip().upper())
    if not achado:
        return {}
    try:
        from ..compras import queries
        from ..compras.db import fetch_all

        linhas = fetch_all(queries.SQL_OS_PRODUTO_ACABADO, {"cod_empresa": 1, "n_os": achado.group(1)})
    except Exception:
        current_app.logger.warning('Busca por OS no endereçamento: GRV indisponível.', exc_info=True)
        return {}
    return {str(l['codigo_interno']).strip().upper(): {
                'n_os': l['n_os'], 'titulo': l.get('titulo') or '',
                'situacao': 'Cancelada' if l.get('cancelado') else ('Concluída' if l.get('concluido') else 'Em aberto')}
            for l in linhas if l.get('codigo_interno')}


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
        if modulo_svc.sem_enderecamento(agregado.get('familia'), agregado.get('grupo'), agregado.get('controla_estoque')):
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
    os_achada = {}
    # "Buscar por": material (código/descrição), endereço (exato; com * no fim,
    # prefixo - "AL-BI*" traz a estante) ou OS (só o produto acabado da OS).
    # Sem o parâmetro (chamada antiga), vale a busca solta em tudo. A busca solta
    # por endereço achava mais do que o pedido: "AL-BI-A1" trazia A10, A11...
    por = request.args.get('por') or ('os' if request.args.get('os') == '1' else '')
    if busca:
        os_achada = _produto_acabado_da_os(busca) if por in ('', 'os') else {}
        if por == 'os':
            filtrados = []
        elif por == 'endereco':
            alvo = modulo_svc.normalizar(busca)
            if alvo.endswith('*'):
                prefixo = alvo[:-1].strip()
                filtrados = [r for r in registros if r[0] and modulo_svc.normalizar(r[0]).startswith(prefixo)]
            else:
                filtrados = [r for r in registros if modulo_svc.normalizar(r[0]) == alvo]
        elif por == 'material':
            # Todas as palavras em qualquer ordem: "chapa 3/8" acha "CHAPA A36 - 3/8''".
            termos = busca.split()
            filtrados = [r for r in registros if all(t in f'{r[1]} {r[2]}'.upper() for t in termos)]
        else:
            filtrados = [r for r in registros if busca in r[0] or busca in r[1] or busca in r[2].upper()]
        if os_achada:
            # O produto da OS entra mesmo sem saldo ou fora do filtro: a ideia é
            # achar o material para endereçar quando sai da produção.
            vistos = {(r[0], r[1]) for r in filtrados}
            por_codigo = estoque.get('por_codigo') or {}
            for sku in os_achada:
                agregado = por_codigo.get(sku) or {}
                enderecos = sorted({e.strip() for b in agregado.get('localizacoes') or [] for e in str(b).split(';') if e.strip()}) or ['']
                for endereco in enderecos:
                    if (endereco, sku) not in vistos:
                        filtrados.append((endereco, sku, str(agregado.get('item') or os_achada[sku]['titulo']).strip(),
                                          str(agregado.get('unidade') or '').strip(), float(agregado.get('qtde_total') or 0)))
        registros = filtrados
    # Sem endereço vai para o fim, como nas outras listagens do sistema.
    # Na busca por OS, o produto da OS vem primeiro.
    registros.sort(key=lambda r: (r[1] not in os_achada, r[0] == '', r[0], r[1]))
    total = len(registros)
    visiveis = registros[(pagina-1)*40:pagina*40]
    inventariados = modulo_svc.skus_em_inventario(r[1] for r in visiveis)
    return jsonify(itens=[dict(endereco=e, sku=s, descricao=d, unidade=u, saldo=saldo, os=os_achada.get(s),
                               em_inventario=str(s).strip().upper() in inventariados)
                          for e, s, d, u, saldo in visiveis],
                   total=total, pagina=pagina,
                   metricas=dict(materiais=materiais, enderecos=enderecos,
                                 sem_endereco=len(sem_endereco), sincronizar=pendentes))


@recebimento_enderecamento_bp.get('/api/enderecamento/historico')
@permission_required(PERMISSION)
def movimentos():
    if not is_admin_session():
        return jsonify(erro='Histórico e sincronização: acesso restrito ao administrador.'), 403
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
    # O conferente pediu inventário porque a quantidade não bateu com o GRV.
    # A movimentação já está salva: falha aqui só deixa de abrir o inventário.
    inventario = None
    if dados.get('abrir_inventario') is True:
        try:
            inventario = modulo_svc.abrir_inventario(mov_id, session['username'])
            if inventario and inventario.get('ajuste') and not inventario.get('repetido'):
                _avisar_gestor_do_inventario(inventario['ajuste'])
        except Exception:
            db.session.rollback()
            current_app.logger.exception('Movimentação %s salva; o inventário não foi aberto', mov_id)
            inventario = {'erro': True}
    return jsonify(item=movimento_json(db.session.get(Movimento, mov_id)), inventario=inventario)


def _avisar_gestor_do_inventario(ajuste_id):
    """Mesmo aviso no Teams que a contagem do inventário dispara."""
    from ..models import LogisticaInventarioAjuste
    from ..services import teams_service
    ajuste = db.session.get(LogisticaInventarioAjuste, ajuste_id)
    base = str(current_app.config.get('PUBLIC_BASE_URL') or '').strip().rstrip('/') or request.url_root.rstrip('/')
    teams_service.notificar_divergencia_inventario_gestor(
        ajuste.codigo_produto, ajuste.local_codigo, ajuste.diferenca,
        descricao=ajuste.descricao_produto, unidade=ajuste.unidade_medida,
        qtde_contada=ajuste.qtde_contada, qtde_sistema=ajuste.qtde_estoque_no_momento,
        custo_medio=ajuste.custo_medio, contado_por=session.get('username'),
        link=f'{base}/logistica/inventario/ajustes')


@recebimento_enderecamento_bp.post('/api/enderecamento/inventariar')
@permission_required(PEDIR_INVENTARIO)
def inventariar():
    """Inventário de um material endereçado, com a quantidade contada na hora."""
    from ..services import enderecamento_service as modulo_svc
    dados = request.get_json(silent=True)
    if not isinstance(dados, dict):
        return jsonify(erro='Dados inválidos.'), 400
    try:
        inventario = modulo_svc.inventariar_material(dados.get('sku'), dados.get('endereco'),
                                                     dados.get('quantidade'), session['username'])
    except ValueError as exc:
        db.session.rollback()
        return jsonify(erro=str(exc)), 409
    except Exception:
        db.session.rollback()
        current_app.logger.exception('Falha ao abrir o inventário pelo endereçamento')
        return jsonify(erro='Não foi possível consultar o GRV para abrir o inventário. Tente novamente em instantes.'), 502
    if inventario.get('ajuste'):
        try:
            _avisar_gestor_do_inventario(inventario['ajuste'])
        except Exception:
            current_app.logger.exception('Inventário aberto; o aviso ao gestor falhou')
    return jsonify(inventario=inventario)


@recebimento_enderecamento_bp.post('/api/enderecamento/sincronizar')
@permission_required(PERMISSION)
def sincronizar_movimentos():
    if not is_admin_session():
        return jsonify(erro='Histórico e sincronização: acesso restrito ao administrador.'), 403
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
        enderecos = modulo_svc.enderecos_atuais(sku)
    except ValueError as exc:
        return jsonify(erro=str(exc)), 409
    # Descrição/unidade/saldo vêm do snapshot do estoque (com cache); faltar
    # não impede a operação, só deixa o cartão do material mais pobre.
    info = {}
    try:
        from ..services.erp_estoque_service import buscar_estoque_grv
        info = (buscar_estoque_grv().get('por_codigo') or {}).get(sku.strip().upper()) or {}
    except Exception:
        current_app.logger.warning('Diálogo de movimentação sem o snapshot do GRV.', exc_info=True)
    return jsonify(sku=sku, enderecos=enderecos, descricao=str(info.get('item') or '').strip(),
                   unidade=str(info.get('unidade') or '').strip(),
                   saldo=float(info['qtde_total']) if info.get('qtde_total') is not None else None,
                   em_inventario=bool(modulo_svc.skus_em_inventario([sku])))


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
    # Mesma regra da busca de materiais: endereço exato; com * no fim, prefixo
    # ("AL-BI*" traz a estante). "contém" trazia AL-BI-A10 ao buscar AL-BI-A1.
    prefixo = busca.endswith('*')
    busca = busca.rstrip('*').strip()
    if busca:
        locais_query = locais_query.filter(LocalizacaoArmazem.codigo.startswith(busca, autoescape=True) if prefixo
                                           else LocalizacaoArmazem.codigo == busca)
    itens = [dict(codigo=l.codigo, ativo=bool(l.ativo),
                  materiais=len(ocupacao.get(modulo_svc.normalizar(l.codigo), ())))
             for l in locais_query.order_by(LocalizacaoArmazem.codigo).limit(500)]
    # Endereço que está no GRV mas nunca passou pelo Sync também é um endereço real.
    conhecidos = {modulo_svc.normalizar(l['codigo']) for l in itens}
    itens += [dict(codigo=endereco, ativo=True, materiais=len(skus), fora_do_catalogo=True)
              for endereco, skus in sorted(ocupacao.items())
              if endereco not in conhecidos and (not busca or (endereco.startswith(busca) if prefixo else endereco == busca))]
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
