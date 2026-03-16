"""
Rotas API do WMS - Warehouse Management System
"""
from flask import Blueprint, request, jsonify, session
from datetime import datetime
from functools import wraps
from sqlalchemy import func

from ..extensions import db
from ..models import (
    LocalizacaoArmazem,
    ItemWMS,
    MovimentacaoWMS,
    EstoqueWMS,
    ItemNota
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


# ============================================================================
# LOCALIZAÇÕES
# ============================================================================

@wms_bp.route('/localizacoes', methods=['GET'])
@requer_admin
def listar_localizacoes():
    """Lista todas as localizações do armazém"""
    localizacoes = LocalizacaoArmazem.query.filter_by(ativo=True).order_by(
        LocalizacaoArmazem.corredor,
        LocalizacaoArmazem.prateleira,
        LocalizacaoArmazem.posicao
    ).all()
    
    resultado = [{
        'id': loc.id,
        'codigo': loc.codigo,
        'corredor': loc.corredor,
        'prateleira': loc.prateleira,
        'posicao': loc.posicao,
        'capacidade_maxima': loc.capacidade_maxima,
        'capacidade_atual': loc.capacidade_atual,
        'ocupacao_percentual': round(loc.capacidade_atual / loc.capacidade_maxima * 100, 2) if loc.capacidade_maxima > 0 else 0,
        'disponivel': loc.capacidade_maxima - loc.capacidade_atual
    } for loc in localizacoes]
    
    return jsonify(resultado), 200


@wms_bp.route('/localizacoes', methods=['POST'])
@requer_admin
def criar_localizacao():
    """Cria uma nova localização"""
    data = request.get_json() or {}
    
    corredor = data.get('corredor')
    prateleira = data.get('prateleira')
    posicao = data.get('posicao')
    capacidade_maxima = float(data.get('capacidade_maxima', 100.0))
    
    if not all([corredor, prateleira, posicao]):
        return jsonify({'erro': 'Campos obrigatórios: corredor, prateleira, posicao'}), 400
    
    localizacao = WMSService.criar_localizacao(
        corredor=corredor,
        prateleira=prateleira,
        posicao=posicao,
        capacidade_maxima=capacidade_maxima
    )
    
    if not localizacao:
        return jsonify({'erro': 'Localização já existe'}), 409
    
    return jsonify({
        'id': localizacao.id,
        'codigo': localizacao.codigo,
        'mensagem': 'Localização criada com sucesso'
    }), 201


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
    usuario = session.get('user', 'Sistema')
    
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
    usuario = session.get('user', 'Sistema')
    
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
    usuario = session.get('user', 'Sistema')
    
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
@requer_admin
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


def registrar_rotas_wms(app):
    """Registra o blueprint WMS na aplicação"""
    app.register_blueprint(wms_bp)
