"""
Rotas API do WMS - Warehouse Management System
"""
from flask import Blueprint, request, jsonify, session
from datetime import datetime
from functools import wraps
from sqlalchemy import func, or_

from ..extensions import db
from ..models import (
    LocalizacaoArmazem,
    ItemWMS,
    MovimentacaoWMS,
    EstoqueWMS,
    ItemNota,
    WMSAlertaOperacional,
    WMSReconciliacaoDivergencia,
    WMSSkuMestre,
    WMSIntegracaoEvento,
    DepositoWMS,
)
from ..services import WMSService


wms_bp = Blueprint('wms_api', __name__, url_prefix='/api/wms')


def requer_admin(f):
    """Decorator para proteger rotas WMS que requerem admin"""
    @wraps(f)
    def wrapper(*args, **kwargs):
        if session.get('role') != 'Admin':
            return jsonify({'erro': 'Acesso restrito a Administrador'}), 403
        return f(*args, **kwargs)
    return wrapper


def requer_wms_operacao(f):
    """Permite operacao WMS para Fiscal e Admin."""
    @wraps(f)
    def wrapper(*args, **kwargs):
        if session.get('role') not in ('Admin', 'Fiscal'):
            return jsonify({'erro': 'Acesso restrito ao modulo WMS (Fiscal/Admin).'}), 403
        return f(*args, **kwargs)
    return wrapper


# ============================================================================
# LOCALIZAÇÕES
# ============================================================================

@wms_bp.route('/localizacoes', methods=['GET'])
@requer_wms_operacao
def listar_localizacoes():
    """Lista todas as localizações do armazém"""
    deposito_codigo = (request.args.get('deposito_codigo') or '').strip().upper()

    query = db.session.query(LocalizacaoArmazem, DepositoWMS).outerjoin(
        DepositoWMS, LocalizacaoArmazem.deposito_id == DepositoWMS.id
    ).filter(
        LocalizacaoArmazem.ativo == True,
        or_(DepositoWMS.id.is_(None), DepositoWMS.ativo == True),
    )

    if deposito_codigo:
        query = query.filter(func.upper(DepositoWMS.codigo) == deposito_codigo)

    localizacoes = query.order_by(
        DepositoWMS.codigo,
        LocalizacaoArmazem.rua,
        LocalizacaoArmazem.predio,
        LocalizacaoArmazem.nivel,
        LocalizacaoArmazem.apartamento,
        LocalizacaoArmazem.codigo,
    ).all()
    
    resultado = [
        {
            'id': loc.id,
            'codigo': loc.codigo,
            'deposito_id': loc.deposito_id,
            'deposito_codigo': dep.codigo if dep else None,
            'deposito_nome': dep.nome if dep else None,
            'rua': loc.rua,
            'predio': loc.predio,
            'nivel': loc.nivel,
            'apartamento': loc.apartamento,
        }
        for loc, dep in localizacoes
    ]
    
    return jsonify(resultado), 200


@wms_bp.route('/localizacoes', methods=['POST'])
@requer_admin
def criar_localizacao():
    """Cria uma nova localização"""
    data = request.get_json() or {}

    rua = (data.get('rua') or '').strip()
    predio = (data.get('predio') or '').strip()
    nivel = (data.get('nivel') or '').strip()
    apartamento = (data.get('apartamento') or '').strip()
    deposito_id = data.get('deposito_id')

    if deposito_id is not None:
        try:
            deposito_id = int(deposito_id)
        except Exception:
            return jsonify({'erro': 'Depósito inválido'}), 400

    if not all([rua, predio, nivel]):
        return jsonify({'erro': 'Campos obrigatórios: rua, predio (coluna) e nivel'}), 400
    
    localizacao = WMSService.criar_localizacao(
        rua=rua,
        predio=predio,
        nivel=nivel,
        apartamento=apartamento,
        deposito_id=deposito_id,
    )
    
    if not localizacao:
        return jsonify({'erro': 'Localização já existe'}), 409
    
    return jsonify({
        'id': localizacao.id,
        'codigo': localizacao.codigo,
        'deposito_id': localizacao.deposito_id,
        'rua': localizacao.rua,
        'predio': localizacao.predio,
        'nivel': localizacao.nivel,
        'apartamento': localizacao.apartamento,
        'mensagem': 'Localização criada com sucesso'
    }), 201


@wms_bp.route('/localizacoes/<int:localizacao_id>', methods=['DELETE'])
@requer_admin
def excluir_localizacao(localizacao_id):
    """Desativa (soft-delete) uma localização, desde que não tenha estoque ou itens ativos."""
    loc = LocalizacaoArmazem.query.get(localizacao_id)
    if not loc:
        return jsonify({'erro': 'Localização não encontrada'}), 404
    if not loc.ativo:
        return jsonify({'erro': 'Localização já está inativa'}), 409

    # Bloqueia exclusão se houver ItemWMS em aberto (pendente ou endereçado)
    itens_ativos = ItemWMS.query.filter(
        ItemWMS.localizacao_id == localizacao_id,
        ItemWMS.status.in_(['Pendente', 'Endereçado', 'Separacao', 'Armazenado', 'Separado']),
        ItemWMS.ativo == True,
        ItemWMS.qtd_atual > 0,
    ).count()
    if itens_ativos:
        return jsonify({
            'erro': f'Localização possui {itens_ativos} item(ns) ativo(s). Remova ou transfira-os antes de excluir.'
        }), 409

    # Bloqueia exclusão se houver saldo de estoque
    estoque_ativo = EstoqueWMS.query.filter(
        EstoqueWMS.localizacao_id == localizacao_id,
        EstoqueWMS.qtd_total > 0,
    ).count()
    if estoque_ativo:
        return jsonify({
            'erro': f'Localização possui saldo de estoque em {estoque_ativo} SKU(s). Zere o estoque antes de excluir.'
        }), 409

    loc.ativo = False
    db.session.commit()
    return jsonify({'sucesso': True, 'mensagem': f'Localização {loc.codigo} excluída com sucesso.'}), 200


@wms_bp.route('/localizacoes/validar', methods=['POST'])
@requer_wms_operacao
def validar_localizacao_por_endereco():
    data = request.get_json() or {}
    deposito_id = data.get('deposito_id')
    rua = (data.get('rua') or '').strip()
    predio = (data.get('predio') or '').strip()
    nivel = (data.get('nivel') or '').strip()
    apartamento = (data.get('apartamento') or '').strip()

    if not all([deposito_id, rua, predio, nivel]):
        return jsonify({'erro': 'Informe deposito_id, rua, predio e nivel.'}), 400

    try:
        deposito_id = int(deposito_id)
    except Exception:
        return jsonify({'erro': 'deposito_id invalido.'}), 400

    deposito = DepositoWMS.query.get(deposito_id)
    if not deposito:
        return jsonify({'erro': 'Deposito informado nao encontrado.'}), 404

    codigo = WMSService.formatar_codigo_localizacao(
        deposito_codigo=deposito.codigo,
        rua=rua,
        coluna=predio,
        nivel=nivel,
    )

    query = LocalizacaoArmazem.query.filter_by(
        deposito_id=deposito_id,
        rua=rua,
        predio=predio,
        nivel=nivel,
        ativo=True,
    )
    if apartamento:
        query = query.filter(LocalizacaoArmazem.apartamento == apartamento)

    localizacao = query.first()
    return jsonify(
        {
            'existe': bool(localizacao),
            'codigo_esperado': codigo,
            'localizacao': (
                {
                    'id': localizacao.id,
                    'codigo': localizacao.codigo,
                    'deposito_id': localizacao.deposito_id,
                }
                if localizacao
                else None
            ),
        }
    ), 200


@wms_bp.route('/localizacoes/opcoes', methods=['GET'])
@requer_wms_operacao
def listar_opcoes_localizacoes():
    deposito_id = request.args.get('deposito_id')
    rua = (request.args.get('rua') or '').strip()
    predio = (request.args.get('predio') or '').strip()

    try:
        deposito_id = int(deposito_id)
    except Exception:
        return jsonify({'erro': 'deposito_id inválido.'}), 400

    base = LocalizacaoArmazem.query.filter_by(ativo=True, deposito_id=deposito_id)

    ruas = [
        r[0]
        for r in base.with_entities(LocalizacaoArmazem.rua)
        .filter(LocalizacaoArmazem.rua.isnot(None), LocalizacaoArmazem.rua != '')
        .distinct()
        .order_by(LocalizacaoArmazem.rua.asc())
        .all()
    ]

    colunas_query = base
    if rua:
        colunas_query = colunas_query.filter(LocalizacaoArmazem.rua == rua)
    colunas = [
        r[0]
        for r in colunas_query.with_entities(LocalizacaoArmazem.predio)
        .filter(LocalizacaoArmazem.predio.isnot(None), LocalizacaoArmazem.predio != '')
        .distinct()
        .order_by(LocalizacaoArmazem.predio.asc())
        .all()
    ]

    niveis_query = base
    if rua:
        niveis_query = niveis_query.filter(LocalizacaoArmazem.rua == rua)
    if predio:
        niveis_query = niveis_query.filter(LocalizacaoArmazem.predio == predio)
    niveis = [
        r[0]
        for r in niveis_query.with_entities(LocalizacaoArmazem.nivel)
        .filter(LocalizacaoArmazem.nivel.isnot(None), LocalizacaoArmazem.nivel != '')
        .distinct()
        .order_by(LocalizacaoArmazem.nivel.asc())
        .all()
    ]

    return jsonify({'ruas': ruas, 'colunas': colunas, 'niveis': niveis}), 200


@wms_bp.route('/localizacoes/lote', methods=['POST'])
@requer_admin
def criar_localizacoes_em_lote():
    data = request.get_json() or {}
    deposito_id = data.get('deposito_id')
    rua = (data.get('rua') or '').strip()

    try:
        deposito_id = int(deposito_id)
        coluna_inicio = int(data.get('coluna_inicio'))
        coluna_fim = int(data.get('coluna_fim'))
        nivel_inicio = int(data.get('nivel_inicio'))
        nivel_fim = int(data.get('nivel_fim'))
    except Exception:
        return jsonify({'erro': 'Preencha depósito e intervalos válidos de coluna/nível.'}), 400

    if not rua:
        return jsonify({'erro': 'Rua é obrigatória para criação em lote.'}), 400

    if coluna_fim < coluna_inicio or nivel_fim < nivel_inicio:
        return jsonify({'erro': 'Intervalos inválidos: fim deve ser maior ou igual ao início.'}), 400

    total = 0
    criadas = 0
    ignoradas = 0

    for coluna in range(coluna_inicio, coluna_fim + 1):
        for nivel in range(nivel_inicio, nivel_fim + 1):
            total += 1
            loc = WMSService.criar_localizacao(
                rua=rua,
                predio=str(coluna).zfill(2),
                nivel=str(nivel).zfill(2),
                apartamento='',
                deposito_id=deposito_id,
            )
            if loc:
                criadas += 1
            else:
                ignoradas += 1

    return jsonify(
        {
            'sucesso': True,
            'total_processado': total,
            'criadas': criadas,
            'ignoradas': ignoradas,
            'mensagem': f'Lote finalizado: {criadas} criadas, {ignoradas} já existentes/ignoradas.',
        }
    ), 200


@wms_bp.route('/localizacoes/sugestao', methods=['GET'])
@requer_wms_operacao
def sugerir_localizacao_para_sku():
    deposito_id = request.args.get('deposito_id')
    codigo_item = (request.args.get('codigo_item') or '').strip()

    try:
        deposito_id = int(deposito_id)
    except Exception:
        return jsonify({'erro': 'deposito_id inválido.'}), 400

    if not codigo_item:
        return jsonify({'erro': 'codigo_item é obrigatório.'}), 400

    # 1) Preferência cadastrada no SKU mestre.
    sku_mestre = WMSSkuMestre.query.filter_by(codigo_item=codigo_item, ativo=True).first()
    if sku_mestre and sku_mestre.endereco_preferencial:
        loc_pref = LocalizacaoArmazem.query.filter_by(
            codigo=str(sku_mestre.endereco_preferencial).strip(),
            deposito_id=deposito_id,
            ativo=True,
        ).first()
        if loc_pref:
            return jsonify(
                {
                    'fonte': 'sku_mestre',
                    'localizacao': {
                        'id': loc_pref.id,
                        'codigo': loc_pref.codigo,
                        'rua': loc_pref.rua,
                        'predio': loc_pref.predio,
                        'nivel': loc_pref.nivel,
                    },
                }
            ), 200

    # 2) Último endereço usado para o SKU no mesmo depósito.
    ultimo = (
        db.session.query(ItemWMS, LocalizacaoArmazem)
        .join(LocalizacaoArmazem, ItemWMS.localizacao_id == LocalizacaoArmazem.id)
        .filter(
            ItemWMS.ativo == True,
            func.lower(func.trim(ItemWMS.codigo_item)) == codigo_item.lower(),
            LocalizacaoArmazem.ativo == True,
            LocalizacaoArmazem.deposito_id == deposito_id,
        )
        .order_by(ItemWMS.data_armazenamento.desc(), ItemWMS.id.desc())
        .first()
    )
    if ultimo:
        _, loc_hist = ultimo
        return jsonify(
            {
                'fonte': 'historico_sku',
                'localizacao': {
                    'id': loc_hist.id,
                    'codigo': loc_hist.codigo,
                    'rua': loc_hist.rua,
                    'predio': loc_hist.predio,
                    'nivel': loc_hist.nivel,
                },
            }
        ), 200

    # 3) Menor ocupação atual no depósito.
    loc_livre = (
        LocalizacaoArmazem.query.filter_by(ativo=True, deposito_id=deposito_id)
        .order_by(LocalizacaoArmazem.capacidade_atual.asc(), LocalizacaoArmazem.codigo.asc())
        .first()
    )
    if loc_livre:
        return jsonify(
            {
                'fonte': 'menor_ocupacao',
                'localizacao': {
                    'id': loc_livre.id,
                    'codigo': loc_livre.codigo,
                    'rua': loc_livre.rua,
                    'predio': loc_livre.predio,
                    'nivel': loc_livre.nivel,
                },
            }
        ), 200

    return jsonify({'fonte': 'sem_sugestao', 'localizacao': None}), 200


# ============================================================================
# ARMAZENAMENTO DE ITENS
# ============================================================================

@wms_bp.route('/armazenar', methods=['POST'])
@requer_admin
def armazenar_item():
    """
    Armazena um item de nota em uma localização.
    POST Body:
    {
        'numero_nota': 'NF001',
        'codigo_item': 'SKU123',
        'localizacao_id': 1,
        'lote': 'LOTE001',
        'data_validade': '2026-12-31'
    }
    """
    data = request.get_json() or {}
    usuario = session.get('username', 'Sistema')
    
    numero_nota = data.get('numero_nota')
    codigo_item = data.get('codigo_item')
    localizacao_id = data.get('localizacao_id')
    lote = data.get('lote')
    data_validade = data.get('data_validade')
    
    if not all([numero_nota, codigo_item, localizacao_id]):
        return jsonify({'erro': 'Campos obrigatórios: numero_nota, codigo_item, localizacao_id'}), 400
    
    # Valida se localização existe
    localizacao = LocalizacaoArmazem.query.get(localizacao_id)
    if not localizacao:
        return jsonify({'erro': 'Localização não encontrada'}), 404
    
    item_wms = WMSService.armazenar_item_nota(
        numero_nota=numero_nota,
        codigo_item=codigo_item,
        localizacao_id=localizacao_id,
        usuario=usuario,
        lote=lote,
        data_validade=data_validade if data_validade else None
    )
    
    if not item_wms:
        return jsonify({'erro': 'Item de nota não encontrado'}), 404
    
    return jsonify({
        'id': item_wms.id,
        'numero_nota': item_wms.numero_nota,
        'codigo_item': item_wms.codigo_item,
        'localizacao': localizacao.codigo,
        'qtd_armazenada': item_wms.qtd_recebida,
        'mensagem': 'Item armazenado com sucesso'
    }), 201


@wms_bp.route('/armazenar-automatico', methods=['POST'])
@requer_admin
def armazenar_automatico():
    """
    Armazena item em melhor localização disponível automaticamente.
    """
    data = request.get_json() or {}
    usuario = session.get('username', 'Sistema')
    
    numero_nota = data.get('numero_nota')
    codigo_item = data.get('codigo_item')
    
    if not all([numero_nota, codigo_item]):
        return jsonify({'erro': 'Campos obrigatórios: numero_nota, codigo_item'}), 400
    
    # Obtém item de nota para saber quantidade
    item_nota = ItemNota.query.filter_by(
        numero_nota=numero_nota,
        codigo=codigo_item
    ).first()
    
    if not item_nota:
        return jsonify({'erro': 'Item de nota não encontrado'}), 404
    
    qtd = item_nota.qtd_real or 0
    
    # Requisita localização automática
    localizacao = WMSService.requisitar_localizacao_automatica(
        codigo_item=codigo_item,
        qtd=qtd,
        usuario=usuario
    )
    
    if not localizacao:
        return jsonify({'erro': 'Sem espaço disponível no armazém'}), 507
    
    # Armazena
    item_wms = WMSService.armazenar_item_nota(
        numero_nota=numero_nota,
        codigo_item=codigo_item,
        localizacao_id=localizacao.id,
        usuario=usuario
    )
    
    return jsonify({
        'id': item_wms.id,
        'numero_nota': item_wms.numero_nota,
        'codigo_item': item_wms.codigo_item,
        'localizacao': localizacao.codigo,
        'qtd_armazenada': item_wms.qtd_recebida,
        'mensagem': 'Item armazenado automaticamente com sucesso'
    }), 201


# ============================================================================
# MOVIMENTAÇÕES
# ============================================================================

@wms_bp.route('/movimentar', methods=['POST'])
@requer_admin
def movimentar_item():
    """
    Move um item entre localizações.
    POST Body:
    {
        'item_wms_id': 1,
        'localizacao_destino_id': 2,
        'qtd_movimentada': 50.0,
        'tipo_movimentacao': 'Reposicionamento|Separacao|Devolucao',
        'motivo': 'Reorganização de estoque'
    }
    """
    data = request.get_json() or {}
    usuario = session.get('username', 'Sistema')
    
    item_wms_id = data.get('item_wms_id')
    localizacao_destino_id = data.get('localizacao_destino_id')
    qtd_movimentada = float(data.get('qtd_movimentada', 0))
    tipo_movimentacao = data.get('tipo_movimentacao')
    motivo = data.get('motivo')
    
    if not all([item_wms_id, tipo_movimentacao]) or qtd_movimentada <= 0:
        return jsonify({'erro': 'Campos obrigatórios: item_wms_id, tipo_movimentacao, qtd_movimentada > 0'}), 400
    
    movimentacao = WMSService.movimentar_item(
        item_wms_id=item_wms_id,
        localizacao_destino_id=localizacao_destino_id,
        qtd_movimentada=qtd_movimentada,
        tipo_movimentacao=tipo_movimentacao,
        usuario=usuario,
        motivo=motivo
    )
    
    if not movimentacao:
        return jsonify({'erro': 'Falha ao movimentar item (quantidade indisponível ou item não encontrado)'}), 400
    
    return jsonify({
        'id': movimentacao.id,
        'tipo': tipo_movimentacao,
        'qtd_movimentada': qtd_movimentada,
        'mensagem': 'Item movimentado com sucesso'
    }), 201


# ============================================================================
# ESTOQUE (CONSULTAS)
# ============================================================================

@wms_bp.route('/estoque/sku/<codigo_item>', methods=['GET'])
@requer_wms_operacao
def obter_estoque_sku(codigo_item):
    """Obtém saldo consolidado de um SKU em todas as localizações"""
    estoques = WMSService.obter_estoque_por_sku(codigo_item)
    
    if not estoques:
        return jsonify({'mensagem': 'SKU sem estoque registrado', 'skus': []}), 200
    
    return jsonify({
        'codigo_item': codigo_item,
        'skus': estoques,
        'qtd_total': sum(e['qtd_total'] for e in estoques),
        'qtd_disponivel': sum(e['qtd_disponivel'] for e in estoques)
    }), 200


@wms_bp.route('/estoque/localizacao/<int:localizacao_id>', methods=['GET'])
@requer_wms_operacao
def obter_estoque_localizacao(localizacao_id):
    """Obtém todos os SKUs em uma localização específica"""
    localizacao = LocalizacaoArmazem.query.get(localizacao_id)
    if not localizacao:
        return jsonify({'erro': 'Localização não encontrada'}), 404
    
    estoques = WMSService.obter_estoque_por_localizacao(localizacao_id)
    
    return jsonify({
        'localizacao': {
            'id': localizacao.id,
            'codigo': localizacao.codigo,
            'capacidade_maxima': localizacao.capacidade_maxima,
            'capacidade_atual': localizacao.capacidade_atual,
            'ocupacao_percentual': round(localizacao.capacidade_atual / localizacao.capacidade_maxima * 100, 2) if localizacao.capacidade_maxima > 0 else 0
        },
        'itens': estoques
    }), 200


# ============================================================================
# DASHBOARD / RELATÓRIOS
# ============================================================================

@wms_bp.route('/dashboard', methods=['GET'])
@requer_wms_operacao
def obter_dashboard_wms():
    """Retorna sumário de utilização do armazém"""
    denso = WMSService.obter_denso_armazem()
    
    # Obtém últimas movimentações
    movimentacoes_recentes = MovimentacaoWMS.query.order_by(
        MovimentacaoWMS.data_movimentacao.desc()
    ).limit(10).all()
    
    return jsonify({
        'resumo': denso,
        'movimentacoes_recentes': [{
            'id': m.id,
            'tipo': m.tipo_movimentacao,
            'numero_nota': m.numero_nota,
            'qtd': m.qtd_movimentada,
            'usuario': m.usuario,
            'data': m.data_movimentacao.isoformat()
        } for m in movimentacoes_recentes]
    }), 200


@wms_bp.route('/historico/<int:item_wms_id>', methods=['GET'])
@requer_wms_operacao
def obter_historico_item(item_wms_id):
    """Retorna histórico completo de movimentações de um item"""
    item_wms = ItemWMS.query.get(item_wms_id)
    if not item_wms:
        return jsonify({'erro': 'Item WMS não encontrado'}), 404
    
    movimentacoes = WMSService.obter_movimentacoes_item(item_wms_id)
    
    return jsonify({
        'item': {
            'id': item_wms.id,
            'numero_nota': item_wms.numero_nota,
            'codigo_item': item_wms.codigo_item,
            'descricao': item_wms.descricao,
            'qtd_recebida': item_wms.qtd_recebida,
            'qtd_atual': item_wms.qtd_atual,
            'status': item_wms.status
        },
        'movimentacoes': [{
            'id': m.id,
            'tipo': m.tipo_movimentacao,
            'qtd': m.qtd_movimentada,
            'motivo': m.motivo,
            'usuario': m.usuario,
            'data': m.data_movimentacao.isoformat()
        } for m in movimentacoes]
    }), 200


@wms_bp.route('/pendentes-enderecamento', methods=['GET'])
@requer_wms_operacao
def listar_pendentes_enderecamento():
    numero_nota = (request.args.get('nota') or '').strip() or None
    codigo_item = (request.args.get('codigo_item') or '').strip() or None
    pendentes = WMSService.listar_pendentes_enderecamento(numero_nota=numero_nota, codigo_item=codigo_item)

    # Cache local para evitar consultas repetidas da mesma NF+codigo.
    cache_pedido_compra = {}

    def _pedido_compra_sugerido(item):
        if item.ordem_compra:
            return item.ordem_compra
        chave = (str(item.numero_nota or '').strip(), str(item.codigo_item or '').strip())
        if chave in cache_pedido_compra:
            return cache_pedido_compra[chave]
        nota, codigo = chave
        if not nota or not codigo:
            cache_pedido_compra[chave] = None
            return None
        item_nota = ItemNota.query.filter(
            func.trim(ItemNota.numero_nota) == nota,
            func.trim(ItemNota.codigo) == codigo,
            ItemNota.pedido_compra.isnot(None),
            func.trim(ItemNota.pedido_compra) != '',
        ).order_by(ItemNota.id.desc()).first()
        sugestao = (item_nota.pedido_compra if item_nota else None)
        cache_pedido_compra[chave] = sugestao
        return sugestao

    return jsonify([
        {
            'id': item.id,
            'numero_nota': item.numero_nota,
            'codigo_item': item.codigo_item,
            'descricao': item.descricao,
            'qtd_atual': item.qtd_atual,
            'status': item.status,
            'deposito_id': item.deposito_id,
            'localizacao_id': item.localizacao_id,
            'ordem_servico_sugerida': item.ordem_servico,
            'ordem_compra_sugerida': _pedido_compra_sugerido(item),
            'data_criacao': item.data_criacao.isoformat() if item.data_criacao else None,
        }
        for item in pendentes
    ]), 200


@wms_bp.route('/estoque-inicial', methods=['POST'])
@requer_admin
def cadastrar_estoque_inicial():
    data = request.get_json() or {}
    usuario = session.get('username', 'Sistema')

    codigo_item = (data.get('codigo_item') or '').strip()
    descricao = (data.get('descricao') or '').strip()
    unidade = (data.get('unidade') or 'UN').strip() or 'UN'
    numero_nota = (data.get('numero_nota') or 'ESTOQUE_INICIAL').strip() or 'ESTOQUE_INICIAL'
    deposito_id = data.get('deposito_id')
    localizacao_id = data.get('localizacao_id')
    rua = (data.get('rua') or '').strip()
    predio = (data.get('predio') or '').strip()
    nivel = (data.get('nivel') or '').strip()
    apartamento = (data.get('apartamento') or '').strip()

    try:
        qtd = float(data.get('qtd') or 0)
    except Exception:
        return jsonify({'erro': 'Quantidade invalida.'}), 400

    try:
        deposito_id = int(deposito_id)
    except Exception:
        return jsonify({'erro': 'Deposito invalido.'}), 400

    if localizacao_id is not None and str(localizacao_id).strip() != '':
        try:
            localizacao_id = int(localizacao_id)
        except Exception:
            return jsonify({'erro': 'Endereco invalido.'}), 400

    localizacao = None
    if localizacao_id:
        localizacao = LocalizacaoArmazem.query.filter_by(id=localizacao_id, ativo=True).first()
        if not localizacao:
            return jsonify({'erro': 'Endereco selecionado nao foi encontrado.'}), 400
        if int(localizacao.deposito_id or 0) != int(deposito_id):
            return jsonify({'erro': 'Endereco selecionado nao pertence ao deposito informado.'}), 400
    else:
        if not all([deposito_id, rua, predio, nivel]):
            return jsonify({'erro': 'Informe depósito e selecione um endereço cadastrado.'}), 400

        localizacao_query = LocalizacaoArmazem.query.filter_by(
            deposito_id=deposito_id,
            rua=rua,
            predio=predio,
            nivel=nivel,
            ativo=True,
        )
        if apartamento:
            localizacao_query = localizacao_query.filter(LocalizacaoArmazem.apartamento == apartamento)
        localizacao = localizacao_query.first()
    if not localizacao:
        return jsonify({'erro': 'Endereço não encontrado no depósito selecionado. Cadastre o endereço antes de lançar o produto.'}), 400

    resultado = WMSService.cadastrar_estoque_inicial_pendente(
        codigo_item=codigo_item,
        descricao=descricao,
        qtd=qtd,
        usuario=usuario,
        unidade=unidade,
        deposito_id=deposito_id,
        localizacao_id=localizacao.id,
        numero_nota=numero_nota,
    )
    if not resultado.get('sucesso'):
        return jsonify({'erro': resultado.get('erro') or 'Nao foi possivel cadastrar o estoque inicial.'}), 400

    return jsonify(resultado), 201


@wms_bp.route('/enderecar-item', methods=['POST'])
@requer_wms_operacao
def enderecar_item_manual():
    data = request.get_json() or {}
    item_wms_id = data.get('item_wms_id')
    localizacao_id = data.get('localizacao_id')
    codigo_grv = (data.get('codigo_grv') or '').strip()
    ordem_servico = (data.get('ordem_servico') or '').strip()
    ordem_compra = (data.get('ordem_compra') or '').strip()
    usuario = session.get('username', 'Sistema')

    if not item_wms_id or not localizacao_id:
        return jsonify({'erro': 'Campos obrigatórios: item_wms_id e localizacao_id'}), 400

    try:
        item_wms_id = int(item_wms_id)
        localizacao_id = int(localizacao_id)
    except Exception:
        return jsonify({'erro': 'IDs inválidos para endereçamento.'}), 400

    item = ItemWMS.query.get(item_wms_id)
    if not item:
        return jsonify({'erro': 'Item não encontrado para endereçamento.'}), 404

    # Codigo GRV passa a ser opcional no fluxo do operador.
    # Se nao for informado, reaproveita o que ja existe no item ou cai para o codigo do material.
    if not codigo_grv:
        if item.codigo_grv:
            codigo_grv = str(item.codigo_grv).strip()
        else:
            codigo_grv = str(item.codigo_item or '').strip()

    # Reaproveita automaticamente informações já conhecidas para evitar retrabalho no operador.
    if not ordem_servico and item.ordem_servico:
        ordem_servico = str(item.ordem_servico).strip()
    if not ordem_compra and item.ordem_compra:
        ordem_compra = str(item.ordem_compra).strip()
    if not ordem_compra:
        item_nota = ItemNota.query.filter(
            func.trim(ItemNota.numero_nota) == str(item.numero_nota or '').strip(),
            func.trim(ItemNota.codigo) == str(item.codigo_item or '').strip(),
            ItemNota.pedido_compra.isnot(None),
            func.trim(ItemNota.pedido_compra) != '',
        ).order_by(ItemNota.id.desc()).first()
        if item_nota and item_nota.pedido_compra:
            ordem_compra = str(item_nota.pedido_compra).strip()

    if not ordem_servico and not ordem_compra:
        return jsonify({'erro': 'Não encontramos OS/OC para este item. Vincule o pedido na NF ou informe manualmente.'}), 400

    item = WMSService.enderecar_item_pendente(
        item_wms_id,
        localizacao_id,
        usuario,
        codigo_grv,
        ordem_servico=ordem_servico or None,
        ordem_compra=ordem_compra or None,
    )
    if not item:
        return jsonify({'erro': 'Nao foi possivel enderecar item (item invalido, ja enderecado ou sem capacidade).'}), 400

    return jsonify(
        {
            'sucesso': True,
            'item_wms_id': item.id,
            'localizacao_id': item.localizacao_id,
            'mensagem': 'Item enderecado com sucesso.',
        }
    ), 200


@wms_bp.route('/itens-enderecados', methods=['GET'])
@requer_admin
def listar_itens_enderecados_admin():
    numero_nota = (request.args.get('nota') or '').strip() or None
    itens = WMSService.listar_itens_enderecados(numero_nota=numero_nota)
    return jsonify([
        {
            'id': item.id,
            'numero_nota': item.numero_nota,
            'codigo_item': item.codigo_item,
            'descricao': item.descricao,
            'qtd_atual': item.qtd_atual,
            'localizacao_id': item.localizacao_id,
            'codigo_grv': item.codigo_grv,
            'ordem_servico': item.ordem_servico,
            'ordem_compra': item.ordem_compra,
            'data_armazenamento': item.data_armazenamento.isoformat() if item.data_armazenamento else None,
        }
        for item in itens
    ]), 200


@wms_bp.route('/estornar-enderecamento', methods=['POST'])
@requer_admin
def estornar_enderecamento_admin():
    data = request.get_json() or {}
    item_wms_id = data.get('item_wms_id')
    motivo = (data.get('motivo') or '').strip()
    usuario = session.get('username', 'Sistema')

    if not item_wms_id:
        return jsonify({'erro': 'Campo obrigatório: item_wms_id'}), 400

    item = WMSService.estornar_enderecamento(item_wms_id, usuario, motivo=motivo or None)
    if not item:
        return jsonify({'erro': 'Não foi possível estornar este endereçamento.'}), 400

    return jsonify({'sucesso': True, 'mensagem': 'Endereçamento estornado com sucesso.'}), 200


@wms_bp.route('/governanca', methods=['GET'])
@requer_wms_operacao
def obter_governanca_wms():
    painel = WMSService.obter_painel_governanca()
    fila = WMSIntegracaoEvento.query.order_by(WMSIntegracaoEvento.criado_em.desc()).limit(20).all()
    painel['fila_integracao'] = [
        {
            'id': e.id,
            'tipo_evento': e.tipo_evento,
            'referencia': e.referencia,
            'status': e.status,
            'tentativas': e.tentativas,
            'ultima_erro': e.ultima_erro,
            'criado_em': e.criado_em.isoformat() if e.criado_em else None,
            'processado_em': e.processado_em.isoformat() if e.processado_em else None,
        }
        for e in fila
    ]
    return jsonify(painel), 200


@wms_bp.route('/governanca/reconciliar', methods=['POST'])
@requer_admin
def executar_reconciliacao_governanca():
    data = request.get_json() or {}
    numero_nota = (data.get('numero_nota') or '').strip() or None
    usuario = session.get('username', 'Sistema')
    resultado = WMSService.executar_reconciliacao_erp_wms(usuario=usuario, numero_nota=numero_nota)
    return jsonify({'sucesso': True, 'resultado': resultado}), 200


@wms_bp.route('/parametros-operacionais', methods=['GET'])
@requer_wms_operacao
def listar_parametros_operacionais():
    return jsonify(WMSService.obter_parametros_operacionais()), 200


@wms_bp.route('/parametros-operacionais', methods=['POST'])
@requer_admin
def atualizar_parametros_operacionais():
    data = request.get_json() or {}
    parametros = data.get('parametros') or {}
    usuario = session.get('username', 'Sistema')
    atualizados = WMSService.atualizar_parametros_operacionais(parametros, usuario)
    return jsonify({'sucesso': True, 'atualizados': atualizados}), 200


@wms_bp.route('/alertas/<int:alerta_id>/resolver', methods=['POST'])
@requer_admin
def resolver_alerta_operacional(alerta_id):
    alerta = WMSAlertaOperacional.query.get(alerta_id)
    if not alerta:
        return jsonify({'erro': 'Alerta não encontrado'}), 404
    alerta.status = 'Resolvido'
    alerta.resolvido_em = datetime.now()
    db.session.commit()
    return jsonify({'sucesso': True}), 200


@wms_bp.route('/reconciliacoes', methods=['GET'])
@requer_wms_operacao
def listar_reconciliacoes():
    status = (request.args.get('status') or '').strip() or 'Aberta'
    query = WMSReconciliacaoDivergencia.query
    if status and status != 'Todas':
        query = query.filter_by(status=status)
    registros = query.order_by(WMSReconciliacaoDivergencia.criado_em.desc()).limit(100).all()
    return jsonify([
        {
            'id': r.id,
            'numero_nota': r.numero_nota,
            'codigo_item': r.codigo_item,
            'qtd_erp': r.qtd_erp,
            'qtd_wms': r.qtd_wms,
            'diferenca': r.diferenca,
            'status': r.status,
            'origem': r.origem,
            'observacao': r.observacao,
            'criado_em': r.criado_em.isoformat() if r.criado_em else None,
        }
        for r in registros
    ]), 200


@wms_bp.route('/sku-mestre', methods=['GET'])
@requer_wms_operacao
def listar_sku_mestre():
    sku = (request.args.get('sku') or '').strip()
    query = WMSSkuMestre.query.filter_by(ativo=True)
    if sku:
        query = query.filter(func.lower(WMSSkuMestre.codigo_item).contains(sku.lower()))
    registros = query.order_by(WMSSkuMestre.codigo_item.asc()).limit(300).all()
    return jsonify([
        {
            'id': s.id,
            'codigo_item': s.codigo_item,
            'codigo_erp': s.codigo_erp,
            'unidade': s.unidade,
            'fator_conversao': s.fator_conversao,
            'curva_abc': s.curva_abc,
            'politica_validade': s.politica_validade,
            'estoque_minimo': s.estoque_minimo,
            'estoque_maximo': s.estoque_maximo,
            'endereco_preferencial': s.endereco_preferencial,
        }
        for s in registros
    ]), 200


@wms_bp.route('/sku-mestre', methods=['POST'])
@requer_admin
def upsert_sku_mestre():
    data = request.get_json() or {}
    codigo_item = (data.get('codigo_item') or '').strip()
    if not codigo_item:
        return jsonify({'erro': 'codigo_item é obrigatório'}), 400

    registro = WMSSkuMestre.query.filter_by(codigo_item=codigo_item).first()
    if not registro:
        registro = WMSSkuMestre(codigo_item=codigo_item)
        db.session.add(registro)

    registro.codigo_erp = (data.get('codigo_erp') or '').strip() or None
    registro.unidade = (data.get('unidade') or 'UN').strip() or 'UN'
    registro.fator_conversao = float(data.get('fator_conversao') or 1.0)
    registro.curva_abc = (data.get('curva_abc') or 'C').strip().upper()[:1]
    registro.politica_validade = (data.get('politica_validade') or 'FIFO').strip().upper()
    registro.estoque_minimo = float(data.get('estoque_minimo') or 0.0)
    registro.estoque_maximo = float(data.get('estoque_maximo') or 0.0)
    registro.endereco_preferencial = (data.get('endereco_preferencial') or '').strip() or None
    registro.atualizado_em = datetime.now()

    db.session.commit()
    return jsonify({'sucesso': True, 'id': registro.id}), 200


# ============================================================================
# ITENS ARMAZENADOS
# ============================================================================

@wms_bp.route('/itens-armazenados', methods=['GET'])
@requer_wms_operacao
def listar_itens_armazenados():
    """Lista itens armazenados com opção de filtro por NF ou SKU"""
    filtro = (request.args.get('filtro') or '').strip().lower()
    
    query = ItemWMS.query.filter_by(ativo=True).filter(
        ItemWMS.status.in_(['Armazenado', 'Pendente Enderecamento'])
    )
    
    if filtro:
        from sqlalchemy import or_
        query = query.filter(
            or_(
                func.lower(ItemWMS.numero_nota).contains(filtro),
                func.lower(ItemWMS.codigo_item).contains(filtro)
            )
        )
    
    itens = query.order_by(ItemWMS.data_criacao.desc()).limit(50).all()
    
    resultado = []
    for item in itens:
        deposito_nome = 'N/A'
        localizacao_codigo = None
        if item.deposito_id:
            dep = DepositoWMS.query.get(item.deposito_id)
            if dep:
                deposito_nome = dep.nome
        if item.localizacao_id:
            loc = LocalizacaoArmazem.query.get(item.localizacao_id)
            if loc:
                localizacao_codigo = loc.codigo
        
        resultado.append({
            'id': item.id,
            'numero_nota': item.numero_nota,
            'codigo_item': item.codigo_item,
            'descricao': item.descricao,
            'qtd_atual': item.qtd_atual,
            'deposito_id': item.deposito_id,
            'deposito_nome': deposito_nome,
            'localizacao_id': item.localizacao_id,
            'localizacao_codigo': localizacao_codigo,
            'status': item.status,
        })
    
    return jsonify(resultado), 200

@wms_bp.route('/transferir-deposito', methods=['POST'])
@requer_wms_operacao
def transferir_item_entre_depositos():
    """
    Transfere um item entre depósitos.
    Apenas GERÊNCIA ou quem tem permissão TRANSFERIR_DEPOSITO pode fazer.
    
    POST Body:
    {
        'item_wms_id': 123,
        'deposito_destino_id': 2,
        'motivo': 'Separação para pedido'  # opcional
    }
    """
    from ..auth import has_permission
    
    # Verificar permissão: GERÊNCIA ou TRANSFERIR_DEPOSITO
    usuario_role = session.get('role', '')
    usuario = session.get('username', 'Sistema')
    
    if usuario_role not in ('Gerência', 'Admin') and not has_permission('TRANSFERIR_DEPOSITO', username=usuario, role=usuario_role):
        return jsonify({'erro': 'Apenas GERÊNCIA ou usuários com permissão especial podem transferir.'}), 403
    
    data = request.get_json() or {}
    item_wms_id = data.get('item_wms_id')
    deposito_destino_id = data.get('deposito_destino_id')
    localizacao_destino_id = data.get('localizacao_destino_id')
    motivo = (data.get('motivo') or '').strip()
    
    if not item_wms_id or not deposito_destino_id:
        return jsonify({'erro': 'Campos obrigatórios: item_wms_id, deposito_destino_id'}), 400

    try:
        item_wms_id = int(item_wms_id)
        deposito_destino_id = int(deposito_destino_id)
        localizacao_destino_id = int(localizacao_destino_id) if localizacao_destino_id else None
    except Exception:
        return jsonify({'erro': 'IDs inválidos para transferência.'}), 400
    
    resultado = WMSService.transferir_entre_depositos(
        item_wms_id=item_wms_id,
        deposito_destino_id=deposito_destino_id,
        usuario=usuario,
        motivo=motivo if motivo else None,
        localizacao_destino_id=localizacao_destino_id,
    )
    
    if not resultado.get('sucesso'):
        return jsonify({'erro': resultado.get('erro')}), 400
    
    return jsonify(resultado), 200


@wms_bp.route('/depositos', methods=['GET'])
@requer_wms_operacao
def listar_depositos():
    """Lista todos os depósitos ativos"""
    from ..models import DepositoWMS
    depositos = DepositoWMS.query.filter_by(ativo=True).order_by(DepositoWMS.codigo).all()
    
    return jsonify([
        {
            'id': d.id,
            'codigo': d.codigo,
            'nome': d.nome,
            'descricao': d.descricao,
        }
        for d in depositos
    ]), 200


def registrar_rotas_wms(app):
    """Registra o blueprint WMS na aplicação"""
    app.register_blueprint(wms_bp)
