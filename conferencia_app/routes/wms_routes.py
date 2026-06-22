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
    WMSTarefaOperacional,
    WMSIntegracaoEvento,
    DepositoWMS,
    WMSUnidadeLogistica,
    WMSInventarioCiclico,
    WMSPedidoSeparacao,
)
from ..services import InventreeService, WMSService


wms_bp = Blueprint('wms_api', __name__, url_prefix='/api/wms')


def _processar_evento_integracao_imediato(evento):
    if not evento:
        return None
    from .api_routes import _processar_evento_integracao_wms
    return _processar_evento_integracao_wms(evento)


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
            return jsonify({'erro': 'Acesso restrito ao módulo WMS (Fiscal/Admin).'}), 403
        return f(*args, **kwargs)
    return wrapper


def _normalizar_bip(valor):
    return str(valor or '').strip().upper()


def _localizacao_por_codigo(codigo):
    codigo = _normalizar_bip(codigo)
    if codigo.startswith('LOC:'):
        codigo = codigo[4:].strip()
    if not codigo:
        return None
    return LocalizacaoArmazem.query.filter(
        func.upper(func.trim(LocalizacaoArmazem.codigo)) == codigo,
        LocalizacaoArmazem.ativo == True,
    ).first()


def _item_wms_payload(item):
    if not item:
        return None
    deposito = DepositoWMS.query.get(item.deposito_id) if item.deposito_id else None
    localizacao = LocalizacaoArmazem.query.get(item.localizacao_id) if item.localizacao_id else None
    return {
        'id': item.id,
        'numero_nota': item.numero_nota,
        'codigo_item': item.codigo_item,
        'descricao': item.descricao,
        'qtd_recebida': item.qtd_recebida,
        'qtd_atual': item.qtd_atual,
        'unidade': item.unidade,
        'status': item.status,
        'codigo_grv': item.codigo_grv,
        'ordem_servico': item.ordem_servico,
        'ordem_compra': item.ordem_compra,
        'unidade_logistica_id': item.unidade_logistica_id,
        'status_estoque': getattr(item, 'status_estoque', 'Disponivel'),
        'deposito_id': item.deposito_id,
        'deposito_codigo': deposito.codigo if deposito else None,
        'deposito_nome': deposito.nome if deposito else None,
        'localizacao_id': item.localizacao_id,
        'localizacao_codigo': localizacao.codigo if localizacao else None,
    }


def _unidade_logistica_payload(unidade):
    if not unidade:
        return None
    loc = LocalizacaoArmazem.query.get(unidade.localizacao_id) if unidade.localizacao_id else None
    itens = ItemWMS.query.filter_by(unidade_logistica_id=unidade.id, ativo=True).all()
    return {
        'id': unidade.id,
        'codigo': unidade.codigo,
        'tipo': unidade.tipo,
        'status': unidade.status,
        'localizacao_id': unidade.localizacao_id,
        'localizacao_codigo': loc.codigo if loc else None,
        'qtd_itens': len(itens),
        'qtd_total': sum(float(i.qtd_atual or 0) for i in itens),
        'criado_por': unidade.criado_por,
        'criado_em': unidade.criado_em.isoformat() if unidade.criado_em else None,
    }


def _inventario_payload(inv):
    if not inv:
        return None
    loc = LocalizacaoArmazem.query.get(inv.localizacao_id) if inv.localizacao_id else None
    return {
        'id': inv.id,
        'codigo': inv.codigo,
        'status': inv.status,
        'codigo_item': inv.codigo_item,
        'localizacao_id': inv.localizacao_id,
        'localizacao_codigo': loc.codigo if loc else None,
        'qtd_sistema': inv.qtd_sistema,
        'qtd_contada': inv.qtd_contada,
        'diferenca': inv.diferenca,
        'tarefa_id': inv.tarefa_id,
        'motivo': inv.motivo,
        'criado_em': inv.criado_em.isoformat() if inv.criado_em else None,
    }


def _separacao_payload(pedido):
    if not pedido:
        return None
    loc = LocalizacaoArmazem.query.get(pedido.localizacao_id) if pedido.localizacao_id else None
    return {
        'id': pedido.id,
        'referencia': pedido.referencia,
        'status': pedido.status,
        'codigo_item': pedido.codigo_item,
        'qtd_solicitada': pedido.qtd_solicitada,
        'qtd_separada': pedido.qtd_separada,
        'localizacao_id': pedido.localizacao_id,
        'localizacao_codigo': loc.codigo if loc else None,
        'tarefa_id': pedido.tarefa_id,
        'criado_em': pedido.criado_em.isoformat() if pedido.criado_em else None,
    }


def _localizacao_payload(loc):
    if not loc:
        return None
    deposito = DepositoWMS.query.get(loc.deposito_id) if loc.deposito_id else None
    return {
        'id': loc.id,
        'codigo': loc.codigo,
        'deposito_id': loc.deposito_id,
        'deposito_codigo': deposito.codigo if deposito else None,
        'deposito_nome': deposito.nome if deposito else None,
        'rua': loc.rua,
        'predio': loc.predio,
        'nivel': loc.nivel,
        'apartamento': loc.apartamento,
        'capacidade_maxima': loc.capacidade_maxima,
        'capacidade_atual': loc.capacidade_atual,
        'capacidade_disponivel': max(float(loc.capacidade_maxima or 0) - float(loc.capacidade_atual or 0), 0),
    }


def _tarefa_payload(tarefa):
    if not tarefa:
        return None
    item = ItemWMS.query.get(tarefa.item_wms_id) if tarefa.item_wms_id else None
    destino = LocalizacaoArmazem.query.get(tarefa.localizacao_destino_id) if tarefa.localizacao_destino_id else None
    origem = LocalizacaoArmazem.query.get(tarefa.localizacao_origem_id) if tarefa.localizacao_origem_id else None
    return {
        'id': tarefa.id,
        'tipo': tarefa.tipo,
        'status': tarefa.status,
        'prioridade': tarefa.prioridade,
        'qtd_planejada': tarefa.qtd_planejada,
        'qtd_executada': tarefa.qtd_executada,
        'atribuido_para': tarefa.atribuido_para,
        'observacao': tarefa.observacao,
        'criado_em': tarefa.criado_em.isoformat() if tarefa.criado_em else None,
        'iniciado_em': tarefa.iniciado_em.isoformat() if tarefa.iniciado_em else None,
        'concluido_em': tarefa.concluido_em.isoformat() if tarefa.concluido_em else None,
        'item': _item_wms_payload(item),
        'localizacao_origem': _localizacao_payload(origem),
        'localizacao_destino': _localizacao_payload(destino),
    }


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
    
    vinculos = InventreeService.mapear_vinculos('localizacao', [loc.id for loc, _ in localizacoes])
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
            'inventree_location_id': vinculos.get(str(loc.id)).inventree_id if vinculos.get(str(loc.id)) else None,
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
    
    sync_inventree = None
    if InventreeService.is_enabled():
        try:
            remote_id = InventreeService._ensure_localizacao(localizacao.id)
            sync_inventree = {'sucesso': True, 'inventree_location_id': remote_id}
        except Exception as exc:
            sync_inventree = {'sucesso': False, 'erro': str(exc)}

    return jsonify({
        'id': localizacao.id,
        'codigo': localizacao.codigo,
        'deposito_id': localizacao.deposito_id,
        'rua': localizacao.rua,
        'predio': localizacao.predio,
        'nivel': localizacao.nivel,
        'apartamento': localizacao.apartamento,
        'integracao_inventree': sync_inventree,
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
        return jsonify({'erro': 'deposito_id inválido.'}), 400

    deposito = DepositoWMS.query.get(deposito_id)
    if not deposito:
        return jsonify({'erro': 'Deposito informado não encontrado.'}), 404

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
    sync_inventree = None

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

    if InventreeService.is_enabled() and criadas:
        try:
            sync_inventree = InventreeService.sincronizar_estrutura_local()
        except Exception as exc:
            sync_inventree = {'sucesso': False, 'erro': str(exc)}

    return jsonify(
        {
            'sucesso': True,
            'total_processado': total,
            'criadas': criadas,
            'ignoradas': ignoradas,
            'integracao_inventree': sync_inventree,
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
    inventree = None
    if InventreeService.is_enabled():
        try:
            inventree = InventreeService.obter_estoque_remoto_por_sku(codigo_item)
        except Exception as exc:
            inventree = {'sucesso': False, 'erro': str(exc)}

    if not estoques and not (inventree and inventree.get('itens')):
        return jsonify({'mensagem': 'SKU sem estoque registrado', 'skus': [], 'inventree': inventree}), 200

    return jsonify({
        'codigo_item': codigo_item,
        'skus': estoques,
        'qtd_total': sum(e['qtd_total'] for e in estoques),
        'qtd_disponivel': sum(e['qtd_disponivel'] for e in estoques),
        'inventree': inventree,
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
    prioridade_ordem = {'Critica': 0, 'Alta': 1, 'Media': 2, 'Baixa': 3}

    # Cache local para evitar consultas repetidas da mesma NF+codigo.
    cache_pedido_compra = {}
    cache_depositos = {
        deposito.id: deposito
        for deposito in DepositoWMS.query.filter_by(ativo=True).all()
    }

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

    payload = []
    for item in pendentes:
        analise = WMSService.analisar_pendencia_enderecamento(item)
        deposito = cache_depositos.get(item.deposito_id)
        payload.append(
            {
            'id': item.id,
            'numero_nota': item.numero_nota,
            'codigo_item': item.codigo_item,
            'descricao': item.descricao,
            'qtd_atual': item.qtd_atual,
            'status': item.status,
            'deposito_id': item.deposito_id,
            'deposito_codigo': deposito.codigo if deposito else None,
            'deposito_nome': deposito.nome if deposito else None,
            'localizacao_id': item.localizacao_id,
            'ordem_servico_sugerida': item.ordem_servico,
            'ordem_compra_sugerida': _pedido_compra_sugerido(item),
            'prioridade_operacional': analise['prioridade'],
            'idade_horas': analise['idade_horas'],
            'ordem_vinculada': analise['ordem_vinculada'],
            'data_criacao': item.data_criacao.isoformat() if item.data_criacao else None,
        }
        )

    payload.sort(
        key=lambda item: (
            prioridade_ordem.get(item.get('prioridade_operacional'), 99),
            -float(item.get('idade_horas') or 0),
            str(item.get('numero_nota') or ''),
            str(item.get('codigo_item') or ''),
        )
    )

    return jsonify(payload), 200


@wms_bp.route('/hub-resumo', methods=['GET'])
@requer_wms_operacao
def obter_hub_resumo():
    """Agrega cockpit operacional + alertas + resumo denso para a landing do WMS."""
    cockpit = WMSService.obter_cockpit_operacional()
    painel = WMSService.obter_painel_governanca()
    denso = WMSService.obter_denso_armazem()

    cockpit['alertas'] = painel.get('alertas', [])
    cockpit['divergencias'] = painel.get('divergencias', [])
    cockpit['resumo_denso'] = denso
    return jsonify(cockpit), 200


@wms_bp.route('/coletor/bipar', methods=['POST'])
@requer_wms_operacao
def coletor_bipar():
    data = request.get_json() or {}
    codigo = _normalizar_bip(data.get('codigo') or data.get('barcode') or data.get('bip'))
    if not codigo:
        return jsonify({'erro': 'Informe o codigo bipado.'}), 400

    # Endereco fisico do armazem.
    loc = _localizacao_por_codigo(codigo)
    if loc:
        estoque = EstoqueWMS.query.filter_by(localizacao_id=loc.id).order_by(EstoqueWMS.codigo_item.asc()).all()
        return jsonify({
            'tipo': 'LOCALIZACAO',
            'codigo': codigo,
            'localizacao': _localizacao_payload(loc),
            'estoque': [
                {
                    'codigo_item': e.codigo_item,
                    'qtd_total': e.qtd_total,
                    'qtd_disponivel': (e.qtd_total or 0) - (e.qtd_separada or 0) - (e.qtd_bloqueada or 0),
                }
                for e in estoque
            ],
            'proximas_acoes': ['ENDERECAR_AQUI', 'CONSULTAR_LOCAL'],
        }), 200

    bruto = codigo
    if ':' in bruto:
        prefixo, valor = bruto.split(':', 1)
        prefixo = prefixo.strip()
        valor = valor.strip()
    else:
        prefixo, valor = '', bruto

    # Item WMS por ID explicito ou GRV.
    item = None
    if prefixo in {'ITEM', 'WMS'} and valor.isdigit():
        item = ItemWMS.query.get(int(valor))
    if not item:
        item = ItemWMS.query.filter(
            ItemWMS.ativo == True,
            func.upper(func.trim(ItemWMS.codigo_grv)) == valor,
        ).order_by(ItemWMS.id.desc()).first()
    if item:
        sugestao = None
        sugestao_motivo = None
        if item.deposito_id:
            sugestao_resp = WMSService.sugerir_localizacao_inteligente(item)
            if sugestao_resp:
                sugestao = _localizacao_payload(sugestao_resp['localizacao'])
                sugestao_motivo = sugestao_resp.get('motivo')
        return jsonify({
            'tipo': 'ITEM_WMS',
            'codigo': codigo,
            'item': _item_wms_payload(item),
            'localizacao_sugerida': sugestao,
            'sugestao_motivo': sugestao_motivo,
            'proximas_acoes': ['BIPAR_LOCAL_DESTINO'] if item.localizacao_id is None else ['CONSULTAR_ITEM', 'MOVIMENTAR'],
        }), 200

    # Nota fiscal ou NF prefixada.
    nota = valor if prefixo in {'NF', 'NOTA'} else bruto
    itens_nota = ItemWMS.query.filter(
        ItemWMS.ativo == True,
        func.upper(func.trim(ItemWMS.numero_nota)) == nota,
    ).order_by(ItemWMS.localizacao_id.asc(), ItemWMS.codigo_item.asc()).limit(50).all()
    if itens_nota:
        return jsonify({
            'tipo': 'NOTA',
            'codigo': codigo,
            'numero_nota': nota,
            'pendentes': [_item_wms_payload(i) for i in itens_nota if i.localizacao_id is None],
            'enderecados': [_item_wms_payload(i) for i in itens_nota if i.localizacao_id is not None],
            'proximas_acoes': ['ENDERECAR_PENDENTES'],
        }), 200

    # SKU/material.
    sku = valor if prefixo in {'SKU', 'MAT', 'ITEM'} else bruto
    estoque_sku = EstoqueWMS.query.filter(
        func.upper(func.trim(EstoqueWMS.codigo_item)) == sku,
    ).order_by(EstoqueWMS.qtd_total.desc()).limit(50).all()
    pendentes_sku = ItemWMS.query.filter(
        ItemWMS.ativo == True,
        ItemWMS.localizacao_id.is_(None),
        func.upper(func.trim(ItemWMS.codigo_item)) == sku,
    ).order_by(ItemWMS.data_criacao.asc()).limit(50).all()
    if estoque_sku or pendentes_sku:
        return jsonify({
            'tipo': 'SKU',
            'codigo': codigo,
            'codigo_item': sku,
            'estoque': [
                {
                    'codigo_item': e.codigo_item,
                    'localizacao': _localizacao_payload(LocalizacaoArmazem.query.get(e.localizacao_id)),
                    'qtd_total': e.qtd_total,
                    'qtd_disponivel': (e.qtd_total or 0) - (e.qtd_separada or 0) - (e.qtd_bloqueada or 0),
                }
                for e in estoque_sku
            ],
            'pendentes': [_item_wms_payload(i) for i in pendentes_sku],
            'proximas_acoes': ['CONSULTAR_SKU', 'ENDERECAR_PENDENTES'],
        }), 200

    return jsonify({'tipo': 'DESCONHECIDO', 'codigo': codigo, 'mensagem': 'Codigo nao encontrado no WMS.'}), 404


def sugerir_localizacao_para_item(item):
    if not item or not item.deposito_id:
        return None
    sku_mestre = WMSSkuMestre.query.filter_by(codigo_item=item.codigo_item, ativo=True).first()
    if sku_mestre and sku_mestre.endereco_preferencial:
        loc_pref = LocalizacaoArmazem.query.filter_by(
            codigo=str(sku_mestre.endereco_preferencial).strip(),
            deposito_id=item.deposito_id,
            ativo=True,
        ).first()
        if loc_pref:
            return loc_pref
    ultimo = (
        db.session.query(ItemWMS, LocalizacaoArmazem)
        .join(LocalizacaoArmazem, ItemWMS.localizacao_id == LocalizacaoArmazem.id)
        .filter(
            ItemWMS.ativo == True,
            func.lower(func.trim(ItemWMS.codigo_item)) == str(item.codigo_item or '').strip().lower(),
            LocalizacaoArmazem.ativo == True,
            LocalizacaoArmazem.deposito_id == item.deposito_id,
        )
        .order_by(ItemWMS.data_armazenamento.desc(), ItemWMS.id.desc())
        .first()
    )
    if ultimo:
        return ultimo[1]
    return (
        LocalizacaoArmazem.query.filter_by(ativo=True, deposito_id=item.deposito_id)
        .order_by(LocalizacaoArmazem.capacidade_atual.asc(), LocalizacaoArmazem.codigo.asc())
        .first()
    )


@wms_bp.route('/coletor/tarefas', methods=['GET', 'POST'])
@requer_wms_operacao
def coletor_tarefas():
    usuario = session.get('username', 'Sistema')
    if request.method == 'POST':
        data = request.get_json() or {}
        tipo = _normalizar_bip(data.get('tipo') or 'ENDERECAMENTO')
        if tipo not in {'ENDERECAMENTO', 'MOVIMENTACAO', 'INVENTARIO', 'SEPARACAO'}:
            return jsonify({'erro': 'Tipo de tarefa invalido.'}), 400
        item_wms_id = data.get('item_wms_id')
        item = ItemWMS.query.get(int(item_wms_id)) if item_wms_id else None
        if tipo == 'ENDERECAMENTO' and not item:
            return jsonify({'erro': 'item_wms_id e obrigatorio para tarefa de enderecamento.'}), 400
        prioridade = _normalizar_bip(data.get('prioridade') or 'MEDIA')
        if prioridade not in {'BAIXA', 'MEDIA', 'ALTA', 'CRITICA'}:
            prioridade = 'MEDIA'
        tarefa = WMSTarefaOperacional(
            tipo=tipo,
            status='PENDENTE',
            item_wms_id=item.id if item else None,
            localizacao_origem_id=data.get('localizacao_origem_id') or None,
            localizacao_destino_id=data.get('localizacao_destino_id') or None,
            qtd_planejada=float(data.get('qtd_planejada') or (item.qtd_atual if item else 0) or 0),
            prioridade=prioridade,
            atribuido_para=(data.get('atribuido_para') or '').strip() or None,
            criado_por=usuario,
            observacao=(data.get('observacao') or '').strip() or None,
        )
        db.session.add(tarefa)
        db.session.commit()
        return jsonify({'sucesso': True, 'tarefa': _tarefa_payload(tarefa)}), 201

    status = _normalizar_bip(request.args.get('status') or 'PENDENTE')
    query = WMSTarefaOperacional.query
    if status and status != 'TODAS':
        query = query.filter(WMSTarefaOperacional.status == status)
    query = query.order_by(
        WMSTarefaOperacional.prioridade.desc(),
        WMSTarefaOperacional.criado_em.asc(),
    )
    return jsonify([_tarefa_payload(t) for t in query.limit(100).all()]), 200


@wms_bp.route('/coletor/proxima-tarefa', methods=['GET'])
@requer_wms_operacao
def coletor_proxima_tarefa():
    tarefa = (
        WMSTarefaOperacional.query
        .filter(WMSTarefaOperacional.status.in_(['PENDENTE', 'EM_EXECUCAO']))
        .order_by(WMSTarefaOperacional.criado_em.asc())
        .first()
    )
    if tarefa:
        return jsonify({'tarefa': _tarefa_payload(tarefa)}), 200

    item = (
        ItemWMS.query
        .filter(ItemWMS.ativo == True, ItemWMS.localizacao_id.is_(None))
        .order_by(ItemWMS.data_criacao.asc(), ItemWMS.id.asc())
        .first()
    )
    return jsonify({'tarefa': None, 'item_sugerido': _item_wms_payload(item)}), 200


@wms_bp.route('/coletor/enderecar', methods=['POST'])
@requer_wms_operacao
def coletor_enderecar():
    data = request.get_json() or {}
    usuario = session.get('username', 'Sistema')
    tarefa_id = data.get('tarefa_id')
    item_wms_id = data.get('item_wms_id')
    local_codigo = data.get('localizacao_codigo') or data.get('codigo_local') or data.get('local')
    localizacao_id = data.get('localizacao_id')

    tarefa = WMSTarefaOperacional.query.get(int(tarefa_id)) if tarefa_id else None
    if tarefa and not item_wms_id:
        item_wms_id = tarefa.item_wms_id
    if not item_wms_id:
        return jsonify({'erro': 'Informe item_wms_id ou tarefa_id.'}), 400

    loc = None
    if localizacao_id:
        loc = LocalizacaoArmazem.query.filter_by(id=int(localizacao_id), ativo=True).first()
    elif local_codigo:
        loc = _localizacao_por_codigo(local_codigo)
    if not loc:
        return jsonify({'erro': 'Localizacao bipada nao encontrada.'}), 404

    item = ItemWMS.query.get(int(item_wms_id))
    if not item:
        return jsonify({'erro': 'Item WMS nao encontrado.'}), 404

    codigo_grv = (data.get('codigo_grv') or item.codigo_grv or item.codigo_item or '').strip()
    ordem_servico = (data.get('ordem_servico') or item.ordem_servico or '').strip()
    ordem_compra = (data.get('ordem_compra') or item.ordem_compra or '').strip()
    if not ordem_compra:
        item_nota = ItemNota.query.filter(
            func.trim(ItemNota.numero_nota) == str(item.numero_nota or '').strip(),
            func.trim(ItemNota.codigo) == str(item.codigo_item or '').strip(),
            ItemNota.pedido_compra.isnot(None),
            func.trim(ItemNota.pedido_compra) != '',
        ).order_by(ItemNota.id.desc()).first()
        if item_nota and item_nota.pedido_compra:
            ordem_compra = str(item_nota.pedido_compra).strip()
    if tarefa and tarefa.status == 'PENDENTE':
        tarefa.status = 'EM_EXECUCAO'
        tarefa.iniciado_por = usuario
        tarefa.iniciado_em = datetime.now()

    item = WMSService.enderecar_item_pendente(
        item.id,
        loc.id,
        usuario,
        codigo_grv,
        ordem_servico=ordem_servico or None,
        ordem_compra=ordem_compra or None,
    )
    if not item:
        db.session.rollback()
        return jsonify({'erro': 'Nao foi possivel enderecar: item invalido, ja enderecado, deposito divergente ou sem capacidade.'}), 400

    if tarefa:
        tarefa.status = 'CONCLUIDA'
        tarefa.localizacao_destino_id = loc.id
        tarefa.qtd_executada = item.qtd_atual
        tarefa.concluido_por = usuario
        tarefa.concluido_em = datetime.now()
        db.session.commit()

    return jsonify({
        'sucesso': True,
        'mensagem': f'Item {item.codigo_item} enderecado em {loc.codigo}.',
        'item': _item_wms_payload(item),
        'localizacao': _localizacao_payload(loc),
        'tarefa': _tarefa_payload(tarefa) if tarefa else None,
    }), 200


@wms_bp.route('/unidades-logisticas', methods=['GET', 'POST'])
@requer_wms_operacao
def unidades_logisticas():
    usuario = session.get('username', 'Sistema')
    if request.method == 'POST':
        data = request.get_json() or {}
        unidade = WMSService.criar_unidade_logistica(
            tipo=data.get('tipo'),
            usuario=usuario,
            item_ids=data.get('item_ids') or [],
            codigo=data.get('codigo'),
            localizacao_id=data.get('localizacao_id') or None,
            observacao=data.get('observacao'),
        )
        if not unidade:
            return jsonify({'erro': 'Nao foi possivel criar unidade logistica. Verifique codigo, tipo e itens.'}), 400
        return jsonify({'sucesso': True, 'unidade': _unidade_logistica_payload(unidade)}), 201

    status = (request.args.get('status') or '').strip()
    query = WMSUnidadeLogistica.query
    if status:
        query = query.filter_by(status=status)
    return jsonify([_unidade_logistica_payload(u) for u in query.order_by(WMSUnidadeLogistica.criado_em.desc()).limit(100).all()]), 200


@wms_bp.route('/itens/<int:item_wms_id>/sugestao-endereco', methods=['GET'])
@requer_wms_operacao
def sugestao_endereco_item(item_wms_id):
    item = ItemWMS.query.get(item_wms_id)
    sugestao = WMSService.sugerir_localizacao_inteligente(item)
    if not sugestao:
        return jsonify({'sugestao': None, 'mensagem': 'Nenhum endereco compativel encontrado.'}), 200
    return jsonify({
        'sugestao': {
            'localizacao': _localizacao_payload(sugestao['localizacao']),
            'motivo': sugestao['motivo'],
            'capacidade_disponivel': sugestao['capacidade_disponivel'],
        }
    }), 200


@wms_bp.route('/inventarios-ciclicos', methods=['GET', 'POST'])
@requer_wms_operacao
def inventarios_ciclicos():
    usuario = session.get('username', 'Sistema')
    if request.method == 'POST':
        data = request.get_json() or {}
        loc = None
        if data.get('localizacao_id'):
            loc = LocalizacaoArmazem.query.get(int(data.get('localizacao_id')))
        elif data.get('localizacao_codigo'):
            loc = _localizacao_por_codigo(data.get('localizacao_codigo'))
        inv = WMSService.abrir_inventario_ciclico(
            localizacao_id=loc.id if loc else None,
            codigo_item=data.get('codigo_item'),
            usuario=usuario,
            motivo=data.get('motivo'),
        )
        if not inv:
            return jsonify({'erro': 'Informe SKU e localizacao validos para abrir contagem.'}), 400
        return jsonify({'sucesso': True, 'inventario': _inventario_payload(inv)}), 201

    status = (request.args.get('status') or '').strip()
    query = WMSInventarioCiclico.query
    if status:
        query = query.filter_by(status=status)
    return jsonify([_inventario_payload(i) for i in query.order_by(WMSInventarioCiclico.criado_em.desc()).limit(100).all()]), 200


@wms_bp.route('/inventarios-ciclicos/<int:inventario_id>/contar', methods=['POST'])
@requer_wms_operacao
def contar_inventario_ciclico(inventario_id):
    data = request.get_json() or {}
    inv = WMSService.registrar_contagem_inventario(
        inventario_id,
        data.get('qtd_contada'),
        session.get('username', 'Sistema'),
        aprovar=bool(data.get('aprovar')),
    )
    if not inv:
        return jsonify({'erro': 'Inventario nao encontrado ou ja finalizado.'}), 400
    return jsonify({'sucesso': True, 'inventario': _inventario_payload(inv)}), 200


@wms_bp.route('/estoque/bloquear', methods=['POST'])
@requer_wms_operacao
def bloquear_estoque_wms():
    data = request.get_json() or {}
    loc = LocalizacaoArmazem.query.get(int(data.get('localizacao_id'))) if data.get('localizacao_id') else _localizacao_por_codigo(data.get('localizacao_codigo'))
    if not loc:
        return jsonify({'erro': 'Localizacao nao encontrada.'}), 400
    estoque = WMSService.bloquear_estoque(
        data.get('codigo_item'),
        loc.id if loc else None,
        data.get('qtd'),
        session.get('username', 'Sistema'),
        motivo=data.get('motivo'),
        status=data.get('status') or 'Bloqueado',
    )
    if not estoque:
        return jsonify({'erro': 'Nao foi possivel bloquear: saldo insuficiente ou item sem rastreio local.'}), 400
    return jsonify({'sucesso': True, 'estoque': {'codigo_item': estoque.codigo_item, 'qtd_total': estoque.qtd_total, 'qtd_bloqueada': estoque.qtd_bloqueada}}), 200


@wms_bp.route('/estoque/movimentar', methods=['POST'])
@requer_wms_operacao
def movimentar_estoque_wms():
    data = request.get_json() or {}
    origem = LocalizacaoArmazem.query.get(int(data.get('origem_id'))) if data.get('origem_id') else _localizacao_por_codigo(data.get('origem_codigo'))
    destino = LocalizacaoArmazem.query.get(int(data.get('destino_id'))) if data.get('destino_id') else _localizacao_por_codigo(data.get('destino_codigo'))
    if not origem or not destino:
        return jsonify({'erro': 'Localizacao de origem ou destino nao encontrada.'}), 400
    estoque = WMSService.movimentar_estoque_guiado(
        data.get('codigo_item'),
        origem.id if origem else None,
        destino.id if destino else None,
        data.get('qtd'),
        session.get('username', 'Sistema'),
        motivo=data.get('motivo'),
    )
    if not estoque:
        return jsonify({'erro': 'Nao foi possivel movimentar: saldo, local ou item rastreavel invalido.'}), 400
    return jsonify({'sucesso': True, 'destino': {'codigo_item': estoque.codigo_item, 'localizacao_id': estoque.localizacao_id, 'qtd_total': estoque.qtd_total}}), 200


@wms_bp.route('/separacoes', methods=['GET', 'POST'])
@requer_wms_operacao
def separacoes_wms():
    usuario = session.get('username', 'Sistema')
    if request.method == 'POST':
        data = request.get_json() or {}
        pedido = WMSService.criar_pedido_separacao(
            data.get('referencia'),
            data.get('codigo_item'),
            data.get('qtd'),
            usuario,
            observacao=data.get('observacao'),
        )
        if not pedido:
            return jsonify({'erro': 'Saldo disponivel insuficiente para criar separacao.'}), 400
        return jsonify({'sucesso': True, 'separacao': _separacao_payload(pedido)}), 201

    status = (request.args.get('status') or '').strip()
    query = WMSPedidoSeparacao.query
    if status:
        query = query.filter_by(status=status)
    return jsonify([_separacao_payload(p) for p in query.order_by(WMSPedidoSeparacao.criado_em.desc()).limit(100).all()]), 200


@wms_bp.route('/separacoes/<int:pedido_id>/confirmar', methods=['POST'])
@requer_wms_operacao
def confirmar_separacao_wms(pedido_id):
    pedido = WMSService.confirmar_separacao(pedido_id, session.get('username', 'Sistema'))
    if not pedido:
        return jsonify({'erro': 'Nao foi possivel confirmar separacao.'}), 400
    return jsonify({'sucesso': True, 'separacao': _separacao_payload(pedido)}), 200


@wms_bp.route('/produtividade', methods=['GET'])
@requer_wms_operacao
def produtividade_wms():
    dias = int(request.args.get('dias') or 7)
    return jsonify({'dias': dias, 'operadores': WMSService.produtividade_operadores(dias=dias)}), 200


@wms_bp.route('/cockpit', methods=['GET'])
@requer_wms_operacao
def obter_cockpit_wms():
    payload = WMSService.obter_cockpit_operacional()
    payload['inventree'] = InventreeService.resumo_operacional_remoto()
    return jsonify(payload), 200


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
        return jsonify({'erro': 'Quantidade inválida.'}), 400

    try:
        deposito_id = int(deposito_id)
    except Exception:
        return jsonify({'erro': 'Deposito inválido.'}), 400

    if localizacao_id is not None and str(localizacao_id).strip() != '':
        try:
            localizacao_id = int(localizacao_id)
        except Exception:
            return jsonify({'erro': 'Endereco inválido.'}), 400

    localizacao = None
    if localizacao_id:
        localizacao = LocalizacaoArmazem.query.filter_by(id=localizacao_id, ativo=True).first()
        if not localizacao:
            return jsonify({'erro': 'Endereco selecionado não foi encontrado.'}), 400
        if int(localizacao.deposito_id or 0) != int(deposito_id):
            return jsonify({'erro': 'Endereco selecionado não pertence ao deposito informado.'}), 400
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
        return jsonify({'erro': resultado.get('erro') or 'Não foi possível cadastrar o estoque inicial.'}), 400

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
    # Se não for informado, reaproveita o que já existe no item ou cai para o codigo do material.
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

    item = WMSService.enderecar_item_pendente(
        item_wms_id,
        localizacao_id,
        usuario,
        codigo_grv,
        ordem_servico=ordem_servico or None,
        ordem_compra=ordem_compra or None,
    )
    if not item:
        return jsonify({'erro': 'Não foi possível enderecar item (item inválido, já enderecado ou sem capacidade).'}), 400

    movimentacao = (
        MovimentacaoWMS.query
        .filter_by(
            item_wms_id=item.id,
            tipo_movimentacao='Armazenamento',
            localizacao_destino_id=item.localizacao_id,
        )
        .order_by(MovimentacaoWMS.id.desc())
        .first()
    )
    evento, evento_criado = InventreeService.enfileirar_evento(
        InventreeService.EVENTO_ENDERECO_MANUAL,
        str(item.id),
        {
            'item_wms_id': item.id,
            'localizacao_id': item.localizacao_id,
            'usuario': usuario,
            'movimentacao_id': movimentacao.id if movimentacao else None,
        },
        origem='WMS',
        idempotency_key=f"enderecamento:{movimentacao.id if movimentacao else item.id}",
    )
    processamento = _processar_evento_integracao_imediato(evento) if evento_criado else {'sucesso': True, 'mensagem': 'Evento já sincronizado'}

    return jsonify(
        {
            'sucesso': True,
            'item_wms_id': item.id,
            'localizacao_id': item.localizacao_id,
            'mensagem': 'Item enderecado com sucesso.',
            'integracao_inventree': processamento,
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

    movimentacao = (
        MovimentacaoWMS.query
        .filter_by(
            item_wms_id=item.id,
            tipo_movimentacao='EstornoEnderecamento',
        )
        .order_by(MovimentacaoWMS.id.desc())
        .first()
    )
    evento, evento_criado = InventreeService.enfileirar_evento(
        InventreeService.EVENTO_ESTORNO_ENDERECO,
        str(item.id),
        {
            'item_wms_id': item.id,
            'usuario': usuario,
            'movimentacao_id': movimentacao.id if movimentacao else None,
            'motivo': motivo or None,
        },
        origem='WMS',
        idempotency_key=f"estorno_enderecamento:{movimentacao.id if movimentacao else item.id}",
    )
    processamento = _processar_evento_integracao_imediato(evento) if evento_criado else {'sucesso': True, 'mensagem': 'Evento já sincronizado'}

    return jsonify({
        'sucesso': True,
        'mensagem': 'Endereçamento estornado com sucesso.',
        'integracao_inventree': processamento,
    }), 200


@wms_bp.route('/governanca', methods=['GET'])
@requer_wms_operacao
def obter_governanca_wms():
    painel = WMSService.obter_painel_governanca()
    painel['inventree'] = InventreeService.resumo_operacional_remoto()
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
    vinculos = InventreeService.mapear_vinculos('item_wms', [item.id for item in itens])
    
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
            'inventree_stock_item_id': vinculos.get(str(item.id)).inventree_id if vinculos.get(str(item.id)) else None,
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

    movimentacao = (
        MovimentacaoWMS.query
        .filter_by(
            item_wms_id=item_wms_id,
            tipo_movimentacao='Transferencia Deposito',
        )
        .order_by(MovimentacaoWMS.id.desc())
        .first()
    )
    evento, evento_criado = InventreeService.enfileirar_evento(
        InventreeService.EVENTO_TRANSFERENCIA_DEPOSITO,
        str(item_wms_id),
        {
            'item_wms_id': item_wms_id,
            'deposito_destino_id': deposito_destino_id,
            'localizacao_destino_id': localizacao_destino_id,
            'usuario': usuario,
            'movimentacao_id': movimentacao.id if movimentacao else None,
            'motivo': motivo or None,
        },
        origem='WMS',
        idempotency_key=f"transferencia_deposito:{movimentacao.id if movimentacao else item_wms_id}",
    )
    resultado['integracao_inventree'] = _processar_evento_integracao_imediato(evento) if evento_criado else {'sucesso': True, 'mensagem': 'Evento já sincronizado'}

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


@wms_bp.route('/inventree/status', methods=['GET'])
@requer_admin
def inventree_status_admin():
    return jsonify(InventreeService.status()), 200


@wms_bp.route('/inventree/sincronizar-estrutura', methods=['POST'])
@requer_admin
def inventree_sincronizar_estrutura_admin():
    try:
        return jsonify(InventreeService.sincronizar_estrutura_local()), 200
    except Exception as exc:
        return jsonify({'sucesso': False, 'erro': str(exc)}), 502


@wms_bp.route('/inventree/sincronizar-nota', methods=['POST'])
@requer_admin
def inventree_sincronizar_nota_admin():
    data = request.get_json() or {}
    numero_nota = (data.get('numero_nota') or '').strip()
    usuario = session.get('username', 'Sistema')
    if not numero_nota:
        return jsonify({'erro': 'numero_nota e obrigatório'}), 400
    try:
        evento, evento_criado = InventreeService.enfileirar_evento(
            InventreeService.EVENTO_NOTA_LANCADA,
            numero_nota,
            {'numero_nota': numero_nota, 'usuario': usuario},
            origem='WMS',
            idempotency_key=f"nota_lancada:{numero_nota}",
        )
        processamento = _processar_evento_integracao_imediato(evento) if evento_criado else {'sucesso': True, 'mensagem': 'Evento já sincronizado'}
        return jsonify({'sucesso': True, 'evento_id': evento.id if evento else None, 'resultado': processamento}), 200
    except Exception as exc:
        return jsonify({'sucesso': False, 'erro': str(exc)}), 502


# ============================================================================
# ESTOQUE EM TEMPO REAL (LAYOUT)
# ============================================================================

@wms_bp.route('/estoque-tempo-real', methods=['GET'])
@requer_wms_operacao
def estoque_tempo_real():
    """Retorna visão consolidada do estoque por localização com dados do SKU mestre."""
    filtro = (request.args.get('filtro') or '').strip().lower()
    deposito_codigo = (request.args.get('deposito') or '').strip().upper()
    apenas_com_saldo = request.args.get('com_saldo', '1').strip() == '1'

    query = (
        db.session.query(EstoqueWMS, LocalizacaoArmazem, DepositoWMS, WMSSkuMestre)
        .join(LocalizacaoArmazem, EstoqueWMS.localizacao_id == LocalizacaoArmazem.id)
        .outerjoin(DepositoWMS, LocalizacaoArmazem.deposito_id == DepositoWMS.id)
        .outerjoin(
            WMSSkuMestre,
            func.lower(func.trim(WMSSkuMestre.codigo_item)) == func.lower(func.trim(EstoqueWMS.codigo_item)),
        )
        .filter(LocalizacaoArmazem.ativo == True)
    )

    if apenas_com_saldo:
        query = query.filter(EstoqueWMS.qtd_total > 0)

    if deposito_codigo:
        query = query.filter(func.upper(DepositoWMS.codigo) == deposito_codigo)

    if filtro:
        query = query.filter(
            or_(
                func.lower(EstoqueWMS.codigo_item).contains(filtro),
                func.lower(LocalizacaoArmazem.codigo).contains(filtro),
                func.lower(LocalizacaoArmazem.rua).contains(filtro),
            )
        )

    registros = query.order_by(
        LocalizacaoArmazem.rua.asc(),
        LocalizacaoArmazem.predio.asc(),
        LocalizacaoArmazem.nivel.asc(),
        EstoqueWMS.codigo_item.asc(),
    ).limit(1000).all()

    resultado = []
    for est, loc, dep, sku in registros:
        qtd_disponivel = (est.qtd_total or 0) - (est.qtd_separada or 0) - (est.qtd_bloqueada or 0)
        resultado.append({
            'estoque_id': est.id,
            'codigo_item': est.codigo_item,
            'localizacao_id': loc.id,
            'localizacao_codigo': loc.codigo,
            'rua': loc.rua,
            'predio': loc.predio,
            'nivel': loc.nivel,
            'deposito_codigo': dep.codigo if dep else None,
            'deposito_nome': dep.nome if dep else None,
            'qtd_total': est.qtd_total,
            'qtd_separada': est.qtd_separada,
            'qtd_bloqueada': est.qtd_bloqueada,
            'qtd_disponivel': round(qtd_disponivel, 2),
            'unidade': sku.unidade if sku else 'UN',
            'curva_abc': sku.curva_abc if sku else None,
            'estoque_minimo': sku.estoque_minimo if sku else None,
            'estoque_maximo': sku.estoque_maximo if sku else None,
            'data_atualizacao': est.data_atualizacao.isoformat() if est.data_atualizacao else None,
        })

    # Totais por localização para o resumo
    loc_resumo = {}
    for r in resultado:
        key = r['localizacao_codigo']
        if key not in loc_resumo:
            loc_resumo[key] = {'skus': 0, 'qtd_total': 0, 'rua': r['rua'], 'deposito': r['deposito_codigo']}
        loc_resumo[key]['skus'] += 1
        loc_resumo[key]['qtd_total'] += r['qtd_total']

    return jsonify({
        'itens': resultado,
        'total_registros': len(resultado),
        'total_localizacoes_com_saldo': len(loc_resumo),
        'resumo_por_localizacao': loc_resumo,
    }), 200


# ============================================================================
# SINCRONIZAÇÃO ERP → WMS
# ============================================================================

@wms_bp.route('/erp-sync', methods=['POST'])
@requer_wms_operacao
def executar_sync_erp():
    """Executa sincronização completa do estoque ERP → WMS"""
    from ..services.erp_sync_service import ERPSyncService
    try:
        resultado = ERPSyncService.executar_sync_completo()
        return jsonify({'sucesso': True, 'resultado': resultado}), 200
    except Exception as exc:
        return jsonify({'sucesso': False, 'erro': str(exc)}), 502


@wms_bp.route('/erp-sync/status', methods=['GET'])
@requer_wms_operacao
def status_sync_erp():
    """Retorna status da última sincronização ERP"""
    from ..services.erp_sync_service import ERPSyncService
    ultimo = ERPSyncService.obter_ultimo_sync()
    return jsonify({'ultimo_sync': ultimo}), 200


@wms_bp.route('/erp-sync/preview', methods=['GET'])
@requer_wms_operacao
def preview_estoque_erp():
    """Preview dos dados de estoque do ERP (somente leitura)"""
    from ..services.erp_sync_service import ERPSyncService
    filtro = (request.args.get('filtro') or '').strip() or None
    limite = min(int(request.args.get('limite') or 200), 500)
    try:
        resultado = ERPSyncService.preview_estoque_erp(filtro=filtro, limite=limite)
        return jsonify(resultado), 200
    except Exception as exc:
        return jsonify({'erro': str(exc)}), 502


@wms_bp.route('/erp-sync/diagnostico', methods=['GET'])
@requer_wms_operacao
def diagnostico_estoque_erp_wms():
    """Compara o estoque atual do ERP/Postgres com o consolidado local do WMS."""
    from ..services.erp_sync_service import ERPSyncService
    filtro = (request.args.get('filtro') or '').strip() or None
    limite = min(int(request.args.get('limite') or 100), 500)
    try:
        resultado = ERPSyncService.diagnosticar_estoque_postgres_vs_wms(filtro=filtro, limite=limite)
        return jsonify(resultado), 200
    except Exception as exc:
        return jsonify({'erro': str(exc)}), 502


def registrar_rotas_wms(app):
    """Registra o blueprint WMS na aplicação"""
    app.register_blueprint(wms_bp)
