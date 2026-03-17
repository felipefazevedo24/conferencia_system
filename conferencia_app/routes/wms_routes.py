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
    ItemNota,
    WMSAlertaOperacional,
    WMSReconciliacaoDivergencia,
    WMSSkuMestre,
    WMSIntegracaoEvento,
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
    localizacoes = LocalizacaoArmazem.query.filter_by(ativo=True).order_by(
        LocalizacaoArmazem.rua,
        LocalizacaoArmazem.predio,
        LocalizacaoArmazem.nivel,
        LocalizacaoArmazem.apartamento,
        LocalizacaoArmazem.codigo,
    ).all()
    
    resultado = [{
        'id': loc.id,
        'codigo': loc.codigo,
        'rua': loc.rua,
        'predio': loc.predio,
        'nivel': loc.nivel,
        'apartamento': loc.apartamento,
    } for loc in localizacoes]
    
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

    if not all([rua, predio, nivel, apartamento]):
        return jsonify({'erro': 'Campos obrigatórios: rua, predio, nivel, apartamento'}), 400
    
    localizacao = WMSService.criar_localizacao(
        rua=rua,
        predio=predio,
        nivel=nivel,
        apartamento=apartamento,
    )
    
    if not localizacao:
        return jsonify({'erro': 'Localização já existe'}), 409
    
    return jsonify({
        'id': localizacao.id,
        'codigo': localizacao.codigo,
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
        ItemWMS.status.in_(['Pendente', 'Endereçado', 'Separacao'])
    ).count()
    if itens_ativos:
        return jsonify({
            'erro': f'Localização possui {itens_ativos} item(ns) ativo(s). Remova ou transfira-os antes de excluir.'
        }), 409

    # Bloqueia exclusão se houver saldo de estoque
    estoque_ativo = EstoqueWMS.query.filter(
        EstoqueWMS.localizacao_id == localizacao_id,
        EstoqueWMS.quantidade > 0
    ).count()
    if estoque_ativo:
        return jsonify({
            'erro': f'Localização possui saldo de estoque em {estoque_ativo} SKU(s). Zere o estoque antes de excluir.'
        }), 409

    loc.ativo = False
    db.session.commit()
    return jsonify({'sucesso': True, 'mensagem': f'Localização {loc.codigo} excluída com sucesso.'}), 200


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
    pendentes = WMSService.listar_pendentes_enderecamento(numero_nota=numero_nota)
    return jsonify([
        {
            'id': item.id,
            'numero_nota': item.numero_nota,
            'codigo_item': item.codigo_item,
            'descricao': item.descricao,
            'qtd_atual': item.qtd_atual,
            'status': item.status,
            'data_criacao': item.data_criacao.isoformat() if item.data_criacao else None,
        }
        for item in pendentes
    ]), 200


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

    if not codigo_grv:
        return jsonify({'erro': 'Informe o codigo do produto no GRV.'}), 400

    if not ordem_servico and not ordem_compra:
        return jsonify({'erro': 'Informe OS e/ou OC para enderecar.'}), 400

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


def registrar_rotas_wms(app):
    """Registra o blueprint WMS na aplicação"""
    app.register_blueprint(wms_bp)
