"""
Serviço WMS - Warehouse Management System
Gerencia localização, estoque e movimentação de itens no armazém
"""
from datetime import datetime, timedelta
from flask import current_app
from sqlalchemy import func
from ..extensions import db
from ..models import (
    LocalizacaoArmazem,
    ItemWMS,
    MovimentacaoWMS,
    EstoqueWMS,
    ItemNota,
    WMSParametroOperacional,
    WMSReconciliacaoDivergencia,
    WMSAlertaOperacional,
    WMSSkuMestre,
    DepositoWMS,
    WMSIntegracaoEvento,
    WMSUnidadeLogistica,
    WMSInventarioCiclico,
    WMSPedidoSeparacao,
    WMSTarefaOperacional,
)


class WMSService:
    """Serviço centralizado para operações de WMS"""

    STATUS_TRANSITIONS = {
        'Pendente Enderecamento': {'Armazenado', 'Cancelado'},
        'Armazenado': {'Separado', 'Devolvido', 'Pendente Enderecamento'},
        'Separado': {'Armazenado', 'Devolvido'},
        'Devolvido': set(),
        'Cancelado': set(),
    }

    PARAMETROS_PADRAO = {
        'WMS_FIFO_PADRAO': ('FIFO', 'Politica padrao de validade para SKUs sem regra propria (FIFO/FEFO).'),
        'WMS_QUARENTENA_ATIVA': ('1', 'Ativa processo de quarentena para divergencias/avarias.'),
        'WMS_PENDENCIA_ALERTA_HORAS': ('24', 'Horas para alerta de pendencia antiga de enderecamento.'),
        'WMS_OCUPACAO_ALERTA_PERCENTUAL': ('90', 'Percentual de ocupacao para alerta de capacidade.'),
        'WMS_RECON_DIVERGENCIA_MINIMA': ('0.01', 'Diferenca mínima para registrar divergencia de reconciliacao.'),
    }

    @staticmethod
    def _can_transition_status(status_atual, status_novo):
        status_atual = str(status_atual or '').strip()
        status_novo = str(status_novo or '').strip()
        if status_atual == status_novo:
            return True
        permitidos = WMSService.STATUS_TRANSITIONS.get(status_atual)
        return status_novo in (permitidos or set())

    @staticmethod
    def _normalizar_bloco_endereco(valor, tamanho=2):
        txt = ''.join(ch for ch in str(valor or '').upper().strip() if ch.isalnum())
        if not txt:
            return ''
        if txt.isdigit():
            return txt.zfill(tamanho)[-tamanho:]
        return txt[:tamanho].ljust(tamanho, 'X')

    @staticmethod
    def _obter_deposito_padrao_al():
        return DepositoWMS.query.filter_by(codigo='AL', ativo=True).first()

    @staticmethod
    def _float(valor, padrao=0.0):
        try:
            return float(valor if valor is not None else padrao)
        except (TypeError, ValueError):
            return float(padrao)

    @staticmethod
    def _normalizar_linhas_entrada_erp(entrada):
        linhas_por_sku = {}
        for item in (entrada or {}).get('itens') or []:
            if not isinstance(item, dict):
                continue
            codigo = str(item.get('cod_interno') or item.get('codigo_item') or '').strip()
            if not codigo:
                continue
            qtd = WMSService._float(item.get('quantidade') or item.get('qtd') or item.get('qtde'), 0.0)
            if qtd <= 0:
                continue
            atual = linhas_por_sku.setdefault(
                codigo,
                {
                    'codigo_item': codigo,
                    'descricao': str(item.get('descricao') or codigo).strip(),
                    'qtd': 0.0,
                    'unidade': str(item.get('unidade') or '').strip() or None,
                    'lote': str(item.get('lote') or '').strip() or None,
                    'localizacao_estoque': str(item.get('localizacao_estoque') or '').strip() or None,
                },
            )
            atual['qtd'] += qtd
            if not atual.get('descricao') or atual['descricao'] == codigo:
                atual['descricao'] = str(item.get('descricao') or codigo).strip()
            if not atual.get('unidade') and item.get('unidade'):
                atual['unidade'] = str(item.get('unidade')).strip()
            if not atual.get('lote') and item.get('lote'):
                atual['lote'] = str(item.get('lote')).strip()
            if not atual.get('localizacao_estoque') and item.get('localizacao_estoque'):
                atual['localizacao_estoque'] = str(item.get('localizacao_estoque')).strip()
        return list(linhas_por_sku.values())

    @staticmethod
    def _entrada_local_para_testes(numero_nota):
        itens = ItemNota.query.filter_by(numero_nota=str(numero_nota), status='Lançado').all()
        if not itens:
            return None
        return {
            'numero_nota': str(numero_nota),
            'codigo_lancamento': str(itens[0].numero_lancamento or '').strip(),
            'chave_acesso': str(itens[0].chave_acesso or '').strip(),
            'parceiro_nome': str(itens[0].fornecedor or '').strip(),
            'fonte': 'ItemNotaLocal',
            'itens': [
                {
                    'cod_interno': item.codigo,
                    'descricao': item.descricao,
                    'quantidade': item.qtd_real,
                    'unidade': getattr(item, 'unidade_comercial', None),
                }
                for item in itens
            ],
        }

    @staticmethod
    def consultar_entrada_erp_para_wms(numero_nota):
        """Busca no ERP/Postgres os itens oficiais da entrada que alimenta o WMS."""
        numero_nota = str(numero_nota or '').strip()
        if not numero_nota:
            return {'encontrada': False, 'entrada': None, 'linhas': [], 'fonte': None}

        try:
            from .erp_lancamento_service import (
                _consultar_entrada_grv_direto,
                _consultar_entrada_grv_via_api,
                _resolver_config,
            )

            cfg = _resolver_config()
            item_ref = (
                ItemNota.query.filter_by(numero_nota=numero_nota)
                .order_by(ItemNota.data_lancamento.desc(), ItemNota.id.desc())
                .first()
            )
            codigo_lancamento = str(getattr(item_ref, 'numero_lancamento', '') or '').strip()
            chave = str(getattr(item_ref, 'chave_acesso', '') or '').strip()
            entrada = (
                _consultar_entrada_grv_via_api(cfg, numero_nota, codigo_lancamento, chave)
                if cfg.get('api_url')
                else _consultar_entrada_grv_direto(cfg, numero_nota, codigo_lancamento, chave)
            )
            if entrada:
                linhas = WMSService._normalizar_linhas_entrada_erp(entrada)
                return {
                    'encontrada': True,
                    'entrada': entrada,
                    'linhas': linhas,
                    'fonte': 'ERPPostgres',
                }
        except Exception as exc:
            current_app.logger.warning("WMS: falha ao consultar NF %s no ERP/Postgres: %s", numero_nota, exc)
            if not current_app.config.get('TESTING') and not current_app.config.get('WMS_ALLOW_LOCAL_FALLBACK', False):
                raise

        if current_app.config.get('TESTING') or current_app.config.get('WMS_ALLOW_LOCAL_FALLBACK', False):
            entrada_local = WMSService._entrada_local_para_testes(numero_nota)
            if entrada_local:
                return {
                    'encontrada': True,
                    'entrada': entrada_local,
                    'linhas': WMSService._normalizar_linhas_entrada_erp(entrada_local),
                    'fonte': 'ItemNotaLocal',
                }

        return {'encontrada': False, 'entrada': None, 'linhas': [], 'fonte': None}

    @staticmethod
    def sincronizar_nota_lancada_erp(numero_nota, usuario):
        """Cria/atualiza pendencias WMS usando a entrada oficial do ERP/Postgres."""
        numero_nota = str(numero_nota or '').strip()
        usuario = str(usuario or 'Integrador').strip() or 'Integrador'
        consulta = WMSService.consultar_entrada_erp_para_wms(numero_nota)
        if not consulta.get('encontrada'):
            return {
                'sucesso': False,
                'itens_pendentes': 0,
                'itens_atualizados': 0,
                'fonte': consulta.get('fonte'),
                'mensagem': f'NF {numero_nota} nao encontrada no ERP/Postgres para gerar pendencias WMS.',
            }

        entrada = consulta.get('entrada') or {}
        linhas = consulta.get('linhas') or []
        if not linhas:
            return {
                'sucesso': True,
                'itens_pendentes': 0,
                'itens_atualizados': 0,
                'fonte': consulta.get('fonte'),
                'mensagem': f'NF {numero_nota} encontrada no ERP, mas sem itens validos para WMS.',
            }

        deposito = WMSService._obter_deposito_padrao_al()
        deposito_id = deposito.id if deposito else None
        codigo_lancamento = str(entrada.get('codigo_lancamento') or entrada.get('numero_ar') or '').strip()
        fornecedor = str(entrada.get('parceiro_nome') or entrada.get('fornecedor_nome') or '').strip()
        chave_acesso = str(entrada.get('chave_acesso') or '').strip()
        agora = datetime.now()
        criados = 0
        atualizados = 0

        for linha in linhas:
            codigo_item = str(linha.get('codigo_item') or '').strip()
            qtd = WMSService._float(linha.get('qtd'), 0.0)
            if not codigo_item or qtd <= 0:
                continue

            existente = ItemWMS.query.filter_by(
                numero_nota=numero_nota,
                codigo_item=codigo_item,
                ativo=True,
            ).first()
            if existente:
                if existente.localizacao_id is None:
                    existente.qtd_recebida = qtd
                    existente.qtd_atual = qtd
                    existente.descricao = linha.get('descricao') or existente.descricao
                    existente.unidade = linha.get('unidade') or existente.unidade
                    existente.lote = linha.get('lote') or existente.lote
                    existente.codigo_grv = codigo_lancamento or existente.codigo_grv or codigo_item
                    existente.chave_acesso = chave_acesso or existente.chave_acesso
                    existente.fornecedor = fornecedor or existente.fornecedor
                    existente.deposito_id = existente.deposito_id or deposito_id
                    existente.status = 'Pendente Enderecamento'
                    atualizados += 1
                continue

            db.session.add(
                ItemWMS(
                    numero_nota=numero_nota,
                    chave_acesso=chave_acesso or None,
                    fornecedor=fornecedor or None,
                    codigo_item=codigo_item,
                    descricao=linha.get('descricao') or codigo_item,
                    qtd_recebida=qtd,
                    qtd_atual=qtd,
                    unidade=linha.get('unidade'),
                    lote=linha.get('lote'),
                    codigo_grv=codigo_lancamento or codigo_item,
                    localizacao_id=None,
                    usuario_armazenamento=usuario,
                    data_armazenamento=None,
                    status='Pendente Enderecamento',
                    deposito_id=deposito_id,
                    ativo=True,
                    data_criacao=agora,
                )
            )
            criados += 1

        db.session.commit()
        return {
            'sucesso': True,
            'itens_pendentes': criados,
            'itens_atualizados': atualizados,
            'fonte': consulta.get('fonte'),
            'mensagem': f'WMS: {criados} itens pendentes criados e {atualizados} atualizados com base {consulta.get("fonte")}.',
        }

    @staticmethod
    def formatar_codigo_localizacao(deposito_codigo, rua, coluna, nivel):
        dep = WMSService._normalizar_bloco_endereco(deposito_codigo, 2)
        rua_fmt = WMSService._normalizar_bloco_endereco(rua, 2)
        coluna_fmt = WMSService._normalizar_bloco_endereco(coluna, 2)
        nivel_fmt = WMSService._normalizar_bloco_endereco(nivel, 2)
        if not all([dep, rua_fmt, coluna_fmt, nivel_fmt]):
            return ''
        return f"{dep}-{rua_fmt}-{coluna_fmt}-{nivel_fmt}"

    @staticmethod
    def garantir_parametros_operacionais():
        alterado = False
        for chave, (valor, descricao) in WMSService.PARAMETROS_PADRAO.items():
            existente = WMSParametroOperacional.query.filter_by(chave=chave).first()
            if existente:
                continue
            db.session.add(
                WMSParametroOperacional(
                    chave=chave,
                    valor=str(valor),
                    descricao=descricao,
                    atualizado_por='Sistema',
                )
            )
            alterado = True
        if alterado:
            db.session.commit()

    @staticmethod
    def obter_parametros_operacionais():
        WMSService.garantir_parametros_operacionais()
        params = WMSParametroOperacional.query.order_by(WMSParametroOperacional.chave.asc()).all()
        return [
            {
                'chave': p.chave,
                'valor': p.valor,
                'descricao': p.descricao,
                'atualizado_por': p.atualizado_por,
                'atualizado_em': p.atualizado_em.isoformat() if p.atualizado_em else None,
            }
            for p in params
        ]

    @staticmethod
    def atualizar_parametros_operacionais(parametros, usuario):
        if not isinstance(parametros, dict):
            return 0
        WMSService.garantir_parametros_operacionais()
        atualizados = 0
        for chave, valor in parametros.items():
            registro = WMSParametroOperacional.query.filter_by(chave=str(chave)).first()
            if not registro:
                continue
            novo_valor = str(valor).strip()
            if registro.valor == novo_valor:
                continue
            registro.valor = novo_valor
            registro.atualizado_por = usuario
            registro.atualizado_em = datetime.now()
            atualizados += 1
        if atualizados:
            db.session.commit()
        return atualizados

    @staticmethod
    def criar_localizacao(rua, predio, nivel, apartamento, deposito_id=None):
        """
        Cria uma nova localização no armazém.
        
        Args:
            rua: Rua do endereco
            predio: Predio do endereco
            nivel: Nivel do endereco
            apartamento: Apartamento do endereco
            
        Returns:
            LocalizacaoArmazem ou None se já existe
        """
        rua = str(rua).strip()
        predio = str(predio).strip()
        nivel = str(nivel).strip()
        apartamento = str(apartamento or '').strip()
        deposito = DepositoWMS.query.get(deposito_id) if deposito_id else WMSService._obter_deposito_padrao_al()
        if not deposito or not deposito.ativo:
            return None

        codigo = WMSService.formatar_codigo_localizacao(deposito.codigo, rua, predio, nivel)
        if not codigo:
            return None
        
        # Verifica se já existe
        localizacao = LocalizacaoArmazem.query.filter_by(codigo=codigo).first()
        if localizacao:
            if localizacao.ativo:
                return None

            # Soft-delete: se já existia inativo, reativa o mesmo endereço.
            localizacao.ativo = True
            localizacao.deposito_id = deposito.id
            localizacao.rua = rua
            localizacao.predio = predio
            localizacao.nivel = nivel
            localizacao.apartamento = apartamento
            localizacao.corredor = rua[:10]
            localizacao.prateleira = predio[:10]
            localizacao.posicao = f"{nivel}-{apartamento}"[:10]
            db.session.commit()
            return localizacao
            
        localizacao = LocalizacaoArmazem(
            codigo=codigo,
            deposito_id=deposito.id,
            rua=rua,
            predio=predio,
            nivel=nivel,
            apartamento=apartamento,
            # Mantidos por compatibilidade com estrutura existente.
            corredor=rua[:10],
            prateleira=predio[:10],
            posicao=f"{nivel}-{apartamento}"[:10],
            capacidade_maxima=999999.0,
            capacidade_atual=0.0,
            ativo=True
        )
        db.session.add(localizacao)
        db.session.commit()
        
        return localizacao

    @staticmethod
    def armazenar_item_nota(numero_nota, codigo_item, localizacao_id, usuario, 
                           qtd_recebida=None, lote=None, data_validade=None):
        """
        Armazena um item de nota em uma localização.
        Cria registro em ItemWMS e registra a movimentação.
        
        Args:
            numero_nota: Número da nota fiscal
            codigo_item: Código do item/SKU
            localizacao_id: ID da localização destino
            usuario: Usuário que está armazenando
            qtd_recebida: Quantidade recebida (se None, busca na ItemNota)
            lote: Lote do item
            data_validade: Data de validade
            
        Returns:
            ItemWMS ou sinaliza erro
        """
        # Obtém dados do item de nota
        item_nota = ItemNota.query.filter_by(
            numero_nota=numero_nota,
            codigo=codigo_item
        ).first()
        
        if not item_nota:
            return None

        codigo_item = str(codigo_item or '').strip()
            
        # Usa quantidade da nota se não foi passada
        if qtd_recebida is None:
            qtd_recebida = item_nota.qtd_real or 0

        qtd_recebida = float(qtd_recebida or 0)
        if qtd_recebida <= 0:
            return None

        deposito_padrao = WMSService._obter_deposito_padrao_al()
        deposito_padrao_id = deposito_padrao.id if deposito_padrao else None

        localizacao_destino = None
        if localizacao_id:
            localizacao_destino = LocalizacaoArmazem.query.get(localizacao_id)
            if not localizacao_destino or not localizacao_destino.ativo:
                return None

            capacidade_disponivel = float(localizacao_destino.capacidade_maxima or 0) - float(localizacao_destino.capacidade_atual or 0)
            if qtd_recebida > capacidade_disponivel:
                return None

        status_inicial = 'Armazenado' if localizacao_id else 'Pendente Enderecamento'
            
        # Cria registro WMS
        item_wms = ItemWMS(
            numero_nota=numero_nota,
            chave_acesso=getattr(item_nota, 'chave_acesso', None),
            fornecedor=item_nota.fornecedor,
            codigo_item=codigo_item,
            descricao=item_nota.descricao,
            qtd_recebida=qtd_recebida,
            qtd_atual=qtd_recebida,
            unidade=getattr(item_nota, 'unidade_comercial', None),
            lote=lote,
            data_validade=data_validade,
            localizacao_id=localizacao_id,
            usuario_armazenamento=usuario,
            data_armazenamento=datetime.now() if localizacao_id else None,
            status=status_inicial,
            deposito_id=(localizacao_destino.deposito_id if localizacao_id else deposito_padrao_id),
            ativo=True
        )
        db.session.add(item_wms)
        db.session.flush()  # Garante que item_wms.id é gerado

        if not localizacao_id:
            db.session.commit()
            return item_wms
        
        # Registra movimentação de armazenamento
        movimentacao = MovimentacaoWMS(
            item_wms_id=item_wms.id,
            numero_nota=numero_nota,
            tipo_movimentacao='Armazenamento',
            localizacao_origem_id=None,
            localizacao_destino_id=localizacao_id,
            qtd_movimentada=qtd_recebida,
            motivo='Armazenamento inicial do recebimento',
            usuario=usuario,
            data_movimentacao=datetime.now()
        )
        db.session.add(movimentacao)
        
        # Atualiza estoque WMS
        estoque = EstoqueWMS.query.filter_by(
            codigo_item=codigo_item,
            localizacao_id=localizacao_id
        ).first()
        
        if estoque:
            estoque.qtd_total += qtd_recebida
            estoque.data_atualizacao = datetime.now()
        else:
            estoque = EstoqueWMS(
                codigo_item=codigo_item,
                localizacao_id=localizacao_id,
                qtd_total=qtd_recebida,
                qtd_separada=0.0,
                data_atualizacao=datetime.now()
            )
            db.session.add(estoque)
            
        # Atualiza capacidade da localização
        localizacao = LocalizacaoArmazem.query.get(localizacao_id)
        if localizacao:
            localizacao.capacidade_atual += qtd_recebida
            
        db.session.commit()
        
        return item_wms

    @staticmethod
    def movimentar_item(item_wms_id, localizacao_destino_id, qtd_movimentada, 
                       tipo_movimentacao, usuario, motivo=None):
        """
        Move um item entre localizações ou muda seu status.
        
        Args:
            item_wms_id: ID do item WMS
            localizacao_destino_id: ID da nova localização (None se apenas muda status)
            qtd_movimentada: Quantidade a movimentar
            tipo_movimentacao: 'Reposicionamento', 'Separacao', 'Devolucao'
            usuario: Usuário responsável
            motivo: Motivo da movimentação
            
        Returns:
            MovimentacaoWMS ou None se falhar
        """
        item_wms = ItemWMS.query.get(item_wms_id)
        if not item_wms:
            return None

        qtd_movimentada = float(qtd_movimentada or 0)
        if qtd_movimentada <= 0:
            return None

        localizacao_destino = None
        if localizacao_destino_id:
            if item_wms.localizacao_id == localizacao_destino_id:
                return None

            localizacao_destino = LocalizacaoArmazem.query.get(localizacao_destino_id)
            if not localizacao_destino or not localizacao_destino.ativo:
                return None

            capacidade_disponivel = float(localizacao_destino.capacidade_maxima or 0) - float(localizacao_destino.capacidade_atual or 0)
            if qtd_movimentada > capacidade_disponivel:
                return None
            
        if qtd_movimentada > item_wms.qtd_atual:
            return None  # Quantidade indisponível
            
        # Estoca origem
        if item_wms.localizacao_id:
            estoque_origem = EstoqueWMS.query.filter_by(
                codigo_item=item_wms.codigo_item,
                localizacao_id=item_wms.localizacao_id
            ).first()
            if estoque_origem:
                estoque_origem.qtd_total = max((estoque_origem.qtd_total or 0) - qtd_movimentada, 0)
                
            localizacao_origem = LocalizacaoArmazem.query.get(item_wms.localizacao_id)
            if localizacao_origem:
                localizacao_origem.capacidade_atual -= qtd_movimentada
                
        # Registra movimentação
        movimentacao = MovimentacaoWMS(
            item_wms_id=item_wms_id,
            numero_nota=item_wms.numero_nota,
            tipo_movimentacao=tipo_movimentacao,
            localizacao_origem_id=item_wms.localizacao_id,
            localizacao_destino_id=localizacao_destino_id,
            qtd_movimentada=qtd_movimentada,
            motivo=motivo,
            usuario=usuario,
            data_movimentacao=datetime.now()
        )
        db.session.add(movimentacao)
        
        # Atualiza item WMS
        item_wms.qtd_atual -= qtd_movimentada
        
        if localizacao_destino_id:
            # Estoque destino
            estoque_destino = EstoqueWMS.query.filter_by(
                codigo_item=item_wms.codigo_item,
                localizacao_id=localizacao_destino_id
            ).first()
            if estoque_destino:
                estoque_destino.qtd_total += qtd_movimentada
            else:
                estoque_destino = EstoqueWMS(
                    codigo_item=item_wms.codigo_item,
                    localizacao_id=localizacao_destino_id,
                    qtd_total=qtd_movimentada,
                    qtd_separada=0.0,
                    data_atualizacao=datetime.now()
                )
                db.session.add(estoque_destino)
                
            if localizacao_destino:
                localizacao_destino.capacidade_atual += qtd_movimentada
                
            item_wms.localizacao_id = localizacao_destino_id
            
        # Atualiza status baseado no tipo de movimentação com transições válidas.
        if tipo_movimentacao == 'Separacao' and WMSService._can_transition_status(item_wms.status, 'Separado'):
            item_wms.status = 'Separado'
        elif tipo_movimentacao == 'Devolucao' and WMSService._can_transition_status(item_wms.status, 'Devolvido'):
            item_wms.status = 'Devolvido'
            
        db.session.commit()
        
        return movimentacao

    @staticmethod
    def obter_estoque_por_sku(codigo_item):
        """
        Retorna saldo consolidado de um SKU em todas as localizações.
        
        Args:
            codigo_item: Código do item/SKU
            
        Returns:
            List[{localizacao_codigo, qtd_total, qtd_separada, ...}]
        """
        codigo_item = (codigo_item or '').strip()
        if not codigo_item:
            return []

        estoques = db.session.query(
            EstoqueWMS,
            LocalizacaoArmazem.codigo.label('localizacao_codigo')
        ).join(
            LocalizacaoArmazem
        ).filter(
            func.lower(func.trim(EstoqueWMS.codigo_item)) == codigo_item.lower(),
            LocalizacaoArmazem.ativo == True
        ).all()
        
        resultado = []
        for estoque, localizacao_codigo in estoques:
            resultado.append({
                'estoque_id': estoque.id,
                'localizacao_id': estoque.localizacao_id,
                'localizacao_codigo': localizacao_codigo,
                'qtd_total': estoque.qtd_total,
                'qtd_separada': estoque.qtd_separada,
                'qtd_disponivel': estoque.qtd_total - estoque.qtd_separada,
                'data_atualizacao': estoque.data_atualizacao.isoformat() if estoque.data_atualizacao else None
            })
            
        return resultado

    @staticmethod
    def transferir_entre_depositos(item_wms_id, deposito_destino_id, usuario, motivo=None, localizacao_destino_id=None):
        """
        Transfere um item de um depósito para outro.
        Limpa a localização e marca como Pendente Enderecamento no novo depósito.
        Registra a movimentação no histórico.
        
        Args:
            item_wms_id: ID do item WMS
            deposito_destino_id: ID do depósito de destino
            usuario: Usuário que realiza a transferência
            motivo: Motivo da transferência (opcional)
            
        Returns:
            dict com sucesso/erro e detalhes
        """
        item = ItemWMS.query.get(item_wms_id)
        if not item:
            return {'sucesso': False, 'erro': 'Item WMS não encontrado'}
        
        deposito_dest = DepositoWMS.query.get(deposito_destino_id)
        if not deposito_dest:
            return {'sucesso': False, 'erro': 'Depósito destino não encontrado'}
        
        if not deposito_dest.ativo:
            return {'sucesso': False, 'erro': 'Depósito destino inativo'}
        
        localizacao_destino = None
        if localizacao_destino_id:
            localizacao_destino = LocalizacaoArmazem.query.get(localizacao_destino_id)
            if not localizacao_destino or not localizacao_destino.ativo:
                return {'sucesso': False, 'erro': 'Endereço destino não encontrado'}
            if int(localizacao_destino.deposito_id or 0) != int(deposito_destino_id):
                return {'sucesso': False, 'erro': 'Endereço destino não pertence ao depósito selecionado'}

        # Se já está no mesmo depósito e sem mudança de endereço, apenas retorna sucesso
        if item.deposito_id == deposito_destino_id and not localizacao_destino_id:
            return {'sucesso': True, 'mensagem': 'Item já está neste depósito'}
        
        # Registrar movimentação no histórico
        deposito_origem_id = item.deposito_id
        localizacao_origem_id = item.localizacao_id
        
        qtd_item = float(item.qtd_atual or 0)

        if item.localizacao_id:
            estoque_origem = EstoqueWMS.query.filter_by(
                codigo_item=item.codigo_item,
                localizacao_id=item.localizacao_id,
            ).first()
            if estoque_origem:
                estoque_origem.qtd_total = max(float(estoque_origem.qtd_total or 0) - qtd_item, 0)
                estoque_origem.data_atualizacao = datetime.now()

            loc_origem = LocalizacaoArmazem.query.get(item.localizacao_id)
            if loc_origem:
                loc_origem.capacidade_atual = max(float(loc_origem.capacidade_atual or 0) - qtd_item, 0)

        movimentacao = MovimentacaoWMS(
            item_wms_id=item_wms_id,
            numero_nota=item.numero_nota,
            tipo_movimentacao='Transferencia Deposito',
            localizacao_origem_id=localizacao_origem_id,
            localizacao_destino_id=localizacao_destino_id,
            qtd_movimentada=qtd_item,
            motivo=motivo if motivo else f'Transferencia {deposito_origem_id} -> {deposito_destino_id}',
            usuario=usuario,
            data_movimentacao=datetime.now()
        )
        
        # Atualizar item: novo depósito e opcionalmente já com endereço destino.
        item.deposito_id = deposito_destino_id
        item.localizacao_id = localizacao_destino_id if localizacao_destino else None
        if localizacao_destino:
            capacidade_disp = float(localizacao_destino.capacidade_maxima or 0) - float(localizacao_destino.capacidade_atual or 0)
            if qtd_item > capacidade_disp:
                return {'sucesso': False, 'erro': 'Endereço destino sem capacidade disponível'}

            estoque_destino = EstoqueWMS.query.filter_by(
                codigo_item=item.codigo_item,
                localizacao_id=localizacao_destino.id,
            ).first()
            if estoque_destino:
                estoque_destino.qtd_total = float(estoque_destino.qtd_total or 0) + qtd_item
                estoque_destino.data_atualizacao = datetime.now()
            else:
                db.session.add(
                    EstoqueWMS(
                        codigo_item=item.codigo_item,
                        localizacao_id=localizacao_destino.id,
                        qtd_total=qtd_item,
                        qtd_separada=0.0,
                        data_atualizacao=datetime.now(),
                    )
                )
            localizacao_destino.capacidade_atual = float(localizacao_destino.capacidade_atual or 0) + qtd_item
            item.status = 'Armazenado'
            item.data_armazenamento = datetime.now()
        else:
            item.status = 'Pendente Enderecamento'
            item.data_armazenamento = None
        
        db.session.add(movimentacao)
        db.session.commit()

        mensagem = 'Transferência realizada. Item pendente de endereçamento no novo depósito.'
        if localizacao_destino:
            mensagem = 'Transferência realizada com sucesso para o novo depósito e endereço.'
        
        return {
            'sucesso': True,
            'mensagem': mensagem,
            'item_id': item_wms_id,
            'deposito_origem': deposito_origem_id,
            'deposito_destino': deposito_destino_id,
            'localizacao_destino_id': localizacao_destino_id,
        }

    @staticmethod
    def obter_estoque_por_localizacao(localizacao_id):
        """
        Retorna todos os SKUs armazenados em uma localização.
        
        Args:
            localizacao_id: ID da localização
            
        Returns:
            List[{codigo_item, qtd_total, qtd_separada, ...}]
        """
        estoques = EstoqueWMS.query.filter_by(
            localizacao_id=localizacao_id
        ).all()
        
        resultado = []
        for estoque in estoques:
            resultado.append({
                'codigo_item': estoque.codigo_item,
                'qtd_total': estoque.qtd_total,
                'qtd_separada': estoque.qtd_separada,
                'qtd_disponivel': estoque.qtd_total - estoque.qtd_separada,
                'data_atualizacao': estoque.data_atualizacao.isoformat() if estoque.data_atualizacao else None
            })
            
        return resultado

    @staticmethod
    def obter_movimentacoes_item(item_wms_id, limitado=False):
        """
        Retorna histórico de movimentações de um item.
        
        Args:
            item_wms_id: ID do item WMS
            limitado: Se True, retorna apenas últimas 10 movimentações
            
        Returns:
            List[MovimentacaoWMS]
        """
        query = MovimentacaoWMS.query.filter_by(
            item_wms_id=item_wms_id
        ).order_by(MovimentacaoWMS.data_movimentacao.desc())
        
        if limitado:
            query = query.limit(10)
            
        return query.all()

    @staticmethod
    def obter_denso_armazem():
        """
        Retorna um sumário de utilização do armazém (para dashboard).
        
        Returns:
            {
                'localizacoes_total': int,
                'localizacoes_ativas': int,
                'ocupacao_media': float % ,
                'qtd_itens_armazenados': int,
                'qtd_itens_separados': int,
                'skus_unicos': int
            }
        """
        localizacoes = LocalizacaoArmazem.query.all()
        localizacoes_ativas = [loc for loc in localizacoes if loc.ativo]
        
        ocupacao_media = 0
        
        estoques = EstoqueWMS.query.all()
        qtd_itens = sum(e.qtd_total for e in estoques)
        qtd_separada = sum(e.qtd_separada for e in estoques)
        skus_unicos = len(set(e.codigo_item for e in estoques))
        
        return {
            'localizacoes_total': len(localizacoes),
            'localizacoes_ativas': len(localizacoes_ativas),
            'ocupacao_media': round(ocupacao_media, 2),
            'qtd_itens_armazenados': qtd_itens,
            'qtd_itens_separados': qtd_separada,
            'skus_unicos': skus_unicos
        }

    @staticmethod
    def requisitar_localizacao_automatica(codigo_item, qtd, usuario):
        """
        Encontra a melhor localização disponível para armazenar um item.
        Critério: localização com mais espaço disponível (round-robin simples).
        
        Args:
            codigo_item: Código do item a armazenar
            qtd: Quantidade a armazenar
            usuario: Usuário fazendo a requisição
            
        Returns:
            LocalizacaoArmazem ou None
        """
        qtd = float(qtd or 0)
        if qtd <= 0:
            return None

        localizacoes = LocalizacaoArmazem.query.filter(
            LocalizacaoArmazem.ativo == True
        ).all()

        candidatas = []
        for loc in localizacoes:
            capacidade_disponivel = float(loc.capacidade_maxima or 0) - float(loc.capacidade_atual or 0)
            if capacidade_disponivel >= qtd:
                candidatas.append((capacidade_disponivel, float(loc.capacidade_atual or 0), loc))

        if not candidatas:
            return None

        # Prioriza maior capacidade disponível; em empate, menor ocupação atual.
        candidatas.sort(key=lambda x: (-x[0], x[1], x[2].id or 0))
        return candidatas[0][2]

    @staticmethod
    def cadastrar_estoque_inicial_pendente(
        codigo_item,
        descricao,
        qtd,
        usuario,
        unidade='UN',
        deposito_id=None,
        localizacao_id=None,
        numero_nota='ESTOQUE_INICIAL',
    ):
        codigo_item = str(codigo_item or '').strip()
        descricao = str(descricao or '').strip()
        unidade = str(unidade or 'UN').strip().upper() or 'UN'
        numero_nota = str(numero_nota or 'ESTOQUE_INICIAL').strip() or 'ESTOQUE_INICIAL'

        try:
            qtd = float(qtd or 0)
        except Exception:
            return {'sucesso': False, 'erro': 'Quantidade inválida.'}

        if not codigo_item:
            return {'sucesso': False, 'erro': 'Codigo do material e obrigatório.'}
        if qtd <= 0:
            return {'sucesso': False, 'erro': 'Quantidade deve ser maior que zero.'}

        deposito = DepositoWMS.query.get(deposito_id) if deposito_id else WMSService._obter_deposito_padrao_al()
        if not deposito or not deposito.ativo:
            return {'sucesso': False, 'erro': 'Deposito informado não existe ou esta inativo.'}

        localizacao = None
        if localizacao_id:
            localizacao = LocalizacaoArmazem.query.get(localizacao_id)
            if not localizacao or not localizacao.ativo:
                return {'sucesso': False, 'erro': 'Endereco informado não existe.'}
            if int(localizacao.deposito_id or 0) != int(deposito.id):
                return {'sucesso': False, 'erro': 'Endereco informado não pertence ao deposito selecionado.'}
            capacidade_disp = float(localizacao.capacidade_maxima or 0) - float(localizacao.capacidade_atual or 0)
            if qtd > capacidade_disp:
                return {'sucesso': False, 'erro': 'Endereco sem capacidade suficiente para este cadastro.'}

        item_existente = ItemWMS.query.filter_by(
            numero_nota=numero_nota,
            codigo_item=codigo_item,
            localizacao_id=(localizacao.id if localizacao else None),
            origem_estoque_inicial=True,
            ativo=True,
        ).first()

        if item_existente:
            item_existente.qtd_recebida = float(item_existente.qtd_recebida or 0) + qtd
            item_existente.qtd_atual = float(item_existente.qtd_atual or 0) + qtd
            if descricao and not item_existente.descricao:
                item_existente.descricao = descricao
            item_existente.deposito_id = deposito.id
            item_existente.unidade = unidade
            if localizacao:
                item_existente.localizacao_id = localizacao.id
                item_existente.status = 'Armazenado'
                item_existente.data_armazenamento = datetime.now()
                item_existente.usuario_armazenamento = usuario
            item_id = item_existente.id
        else:
            novo = ItemWMS(
                numero_nota=numero_nota,
                chave_acesso=None,
                fornecedor='Estoque Inicial',
                codigo_item=codigo_item,
                descricao=descricao or codigo_item,
                qtd_recebida=qtd,
                qtd_atual=qtd,
                unidade=unidade,
                lote=None,
                data_validade=None,
                localizacao_id=(localizacao.id if localizacao else None),
                usuario_armazenamento=(usuario if localizacao else None),
                data_armazenamento=(datetime.now() if localizacao else None),
                status=('Armazenado' if localizacao else 'Pendente Enderecamento'),
                deposito_id=deposito.id,
                origem_estoque_inicial=True,
                ativo=True,
            )
            db.session.add(novo)
            db.session.flush()
            item_id = novo.id

        db.session.add(
            MovimentacaoWMS(
                item_wms_id=item_id,
                numero_nota=numero_nota,
                tipo_movimentacao='CadastroEstoqueInicial',
                localizacao_origem_id=None,
                localizacao_destino_id=(localizacao.id if localizacao else None),
                qtd_movimentada=qtd,
                motivo='Cadastro manual de estoque legado',
                usuario=usuario,
                data_movimentacao=datetime.now(),
            )
        )

        if localizacao:
            estoque = EstoqueWMS.query.filter_by(
                codigo_item=codigo_item,
                localizacao_id=localizacao.id,
            ).first()
            if estoque:
                estoque.qtd_total = float(estoque.qtd_total or 0) + qtd
                estoque.data_atualizacao = datetime.now()
            else:
                db.session.add(
                    EstoqueWMS(
                        codigo_item=codigo_item,
                        localizacao_id=localizacao.id,
                        qtd_total=qtd,
                        qtd_separada=0.0,
                        data_atualizacao=datetime.now(),
                    )
                )
            localizacao.capacidade_atual = float(localizacao.capacidade_atual or 0) + qtd

        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
            return {'sucesso': False, 'erro': 'Falha ao salvar cadastro de estoque inicial.'}

        return {
            'sucesso': True,
            'item_id': item_id,
            'codigo_item': codigo_item,
            'numero_nota': numero_nota,
            'mensagem': 'Item de estoque inicial cadastrado para enderecamento.',
        }

    @staticmethod
    def listar_pendentes_enderecamento(numero_nota=None, codigo_item=None):
        WMSService._sanear_pendencias_enderecamento()

        query = ItemWMS.query.filter_by(localizacao_id=None, ativo=True)
        if numero_nota:
            query = query.filter(ItemWMS.numero_nota == str(numero_nota))
        if codigo_item:
            query = query.filter(func.lower(func.trim(ItemWMS.codigo_item)) == str(codigo_item).strip().lower())
        return query.order_by(ItemWMS.data_criacao.desc()).all()

    @staticmethod
    def _sanear_pendencias_enderecamento():
        """
        Higieniza pendências WMS:
        1) Desativa pendências de NF que não esteja mais em status Lançado.
        2) Consolida duplicidades de mesma NF+SKU em um único registro ativo.
        """
        alterado = False

        notas_lancadas = {
            n[0]
            for n in db.session.query(ItemNota.numero_nota)
            .filter(ItemNota.status == 'Lançado')
            .distinct()
            .all()
        }

        pendentes = (
            ItemWMS.query.filter_by(localizacao_id=None, ativo=True)
            .order_by(ItemWMS.id.asc())
            .all()
        )

        agregador = {}
        for item in pendentes:
            if bool(getattr(item, 'origem_estoque_inicial', False)):
                continue

            # Se nota não está lançada, não deve aparecer em pendências de endereçamento.
            if item.numero_nota not in notas_lancadas:
                item.ativo = False
                alterado = True
                continue

            chave = (str(item.numero_nota or '').strip(), str(item.codigo_item or '').strip())
            if chave not in agregador:
                agregador[chave] = item
                continue

            principal = agregador[chave]
            principal.qtd_recebida = float(principal.qtd_recebida or 0) + float(item.qtd_recebida or 0)
            principal.qtd_atual = float(principal.qtd_atual or 0) + float(item.qtd_atual or 0)
            item.ativo = False
            alterado = True

        if alterado:
            db.session.commit()

    @staticmethod
    def enderecar_item_pendente(item_wms_id, localizacao_id, usuario, codigo_grv, ordem_servico=None, ordem_compra=None, qtd_guardada=None):
        item_wms = ItemWMS.query.get(item_wms_id)
        localizacao = LocalizacaoArmazem.query.get(localizacao_id)

        if not item_wms or not localizacao or not item_wms.ativo or not localizacao.ativo:
            return None

        if item_wms.deposito_id and localizacao.deposito_id and int(item_wms.deposito_id) != int(localizacao.deposito_id):
            if item_wms.localizacao_id is None and str(item_wms.status or '').strip() == 'Pendente Enderecamento':
                item_wms.deposito_id = localizacao.deposito_id
            else:
                return None
        if not item_wms.deposito_id and localizacao.deposito_id:
            item_wms.deposito_id = localizacao.deposito_id

        if item_wms.localizacao_id is not None:
            return None

        qtd_pendente = float(item_wms.qtd_atual or 0)
        qtd = WMSService._float(qtd_guardada, qtd_pendente) if qtd_guardada is not None else qtd_pendente
        if qtd <= 0 or qtd_pendente <= 0 or qtd > qtd_pendente:
            return None

        capacidade_disponivel = float(localizacao.capacidade_maxima or 0) - float(localizacao.capacidade_atual or 0)
        if qtd > capacidade_disponivel:
            return None

        codigo_grv_final = str(codigo_grv or item_wms.codigo_grv or item_wms.codigo_item or '').strip()
        if not codigo_grv_final:
            return None

        if not WMSService._can_transition_status(item_wms.status, 'Armazenado'):
            return None

        agora = datetime.now()
        if qtd < qtd_pendente:
            item_wms.qtd_atual = qtd_pendente - qtd
            item_wms.qtd_recebida = max(float(item_wms.qtd_recebida or 0) - qtd, item_wms.qtd_atual)
            item_enderecado = ItemWMS(
                numero_nota=item_wms.numero_nota,
                chave_acesso=item_wms.chave_acesso,
                fornecedor=item_wms.fornecedor,
                codigo_item=item_wms.codigo_item,
                descricao=item_wms.descricao,
                qtd_recebida=qtd,
                qtd_atual=qtd,
                unidade=item_wms.unidade,
                lote=item_wms.lote,
                data_validade=item_wms.data_validade,
                codigo_grv=codigo_grv_final,
                ordem_servico=str(ordem_servico).strip() if ordem_servico else None,
                ordem_compra=str(ordem_compra).strip() if ordem_compra else None,
                status_estoque=item_wms.status_estoque,
                localizacao_id=localizacao_id,
                usuario_armazenamento=usuario,
                data_armazenamento=agora,
                status='Armazenado',
                deposito_id=item_wms.deposito_id,
                origem_estoque_inicial=item_wms.origem_estoque_inicial,
                ativo=True,
                data_criacao=agora,
            )
            db.session.add(item_enderecado)
            db.session.flush()
        else:
            item_wms.localizacao_id = localizacao_id
            item_wms.usuario_armazenamento = usuario
            item_wms.data_armazenamento = agora
            item_wms.codigo_grv = codigo_grv_final
            item_wms.ordem_servico = str(ordem_servico).strip() if ordem_servico else None
            item_wms.ordem_compra = str(ordem_compra).strip() if ordem_compra else None
            item_wms.status = 'Armazenado'
            item_enderecado = item_wms

        movimentacao = MovimentacaoWMS(
            item_wms_id=item_enderecado.id,
            numero_nota=item_enderecado.numero_nota,
            tipo_movimentacao='Armazenamento',
            localizacao_origem_id=None,
            localizacao_destino_id=localizacao_id,
            qtd_movimentada=qtd,
            motivo='Enderecamento manual apos lancamento',
            usuario=usuario,
            data_movimentacao=datetime.now(),
        )
        db.session.add(movimentacao)

        estoque = EstoqueWMS.query.filter_by(
            codigo_item=item_enderecado.codigo_item,
            localizacao_id=localizacao_id,
        ).first()
        if estoque:
            estoque.qtd_total += qtd
            estoque.data_atualizacao = datetime.now()
        else:
            db.session.add(
                EstoqueWMS(
                    codigo_item=item_wms.codigo_item,
                    localizacao_id=localizacao_id,
                    qtd_total=qtd,
                    qtd_separada=0.0,
                    data_atualizacao=datetime.now(),
                )
            )

        localizacao.capacidade_atual += qtd
        db.session.commit()
        return item_enderecado

    @staticmethod
    def listar_itens_enderecados(numero_nota=None):
        query = ItemWMS.query.filter(ItemWMS.ativo == True, ItemWMS.localizacao_id.isnot(None))
        if numero_nota:
            query = query.filter(ItemWMS.numero_nota == str(numero_nota).strip())
        return query.order_by(ItemWMS.data_armazenamento.desc(), ItemWMS.id.desc()).all()

    @staticmethod
    def estornar_enderecamento(item_wms_id, usuario, motivo=None):
        item_wms = ItemWMS.query.get(item_wms_id)
        if not item_wms or not item_wms.localizacao_id:
            return None

        localizacao_id_origem = item_wms.localizacao_id
        qtd = item_wms.qtd_atual or 0

        estoque = EstoqueWMS.query.filter_by(
            codigo_item=item_wms.codigo_item,
            localizacao_id=localizacao_id_origem,
        ).first()
        if estoque:
            estoque.qtd_total = max((estoque.qtd_total or 0) - qtd, 0)
            estoque.data_atualizacao = datetime.now()

        localizacao = LocalizacaoArmazem.query.get(localizacao_id_origem)
        if localizacao:
            localizacao.capacidade_atual = max((localizacao.capacidade_atual or 0) - qtd, 0)

        if not WMSService._can_transition_status(item_wms.status, 'Pendente Enderecamento'):
            return None

        item_wms.localizacao_id = None
        item_wms.status = 'Pendente Enderecamento'
        item_wms.usuario_armazenamento = None
        item_wms.data_armazenamento = None
        item_wms.codigo_grv = None
        item_wms.ordem_servico = None
        item_wms.ordem_compra = None

        db.session.add(
            MovimentacaoWMS(
                item_wms_id=item_wms.id,
                numero_nota=item_wms.numero_nota,
                tipo_movimentacao='EstornoEnderecamento',
                localizacao_origem_id=localizacao_id_origem,
                localizacao_destino_id=None,
                qtd_movimentada=qtd,
                motivo=(motivo or 'Estorno de endereçamento')[:300],
                usuario=usuario,
                data_movimentacao=datetime.now(),
            )
        )

        db.session.commit()
        return item_wms

    @staticmethod
    def executar_reconciliacao_erp_wms(usuario='Sistema', numero_nota=None):
        WMSService.garantir_parametros_operacionais()
        min_dif = float(
            (WMSParametroOperacional.query.filter_by(chave='WMS_RECON_DIVERGENCIA_MINIMA').first() or WMSParametroOperacional(valor='0.01')).valor
            or 0.01
        )

        query_erp = db.session.query(
            ItemNota.numero_nota,
            ItemNota.codigo,
            func.sum(ItemNota.qtd_real).label('qtd_erp')
        ).filter(ItemNota.status == 'Lançado')

        if numero_nota:
            query_erp = query_erp.filter(ItemNota.numero_nota == str(numero_nota).strip())

        registros_erp = query_erp.group_by(ItemNota.numero_nota, ItemNota.codigo).all()

        mapa_wms = {
            (str(r.numero_nota), str(r.codigo_item)): float(r.qtd or 0)
            for r in db.session.query(
                ItemWMS.numero_nota.label('numero_nota'),
                ItemWMS.codigo_item.label('codigo_item'),
                func.sum(ItemWMS.qtd_atual).label('qtd')
            )
            .filter(ItemWMS.ativo == True)
            .group_by(ItemWMS.numero_nota, ItemWMS.codigo_item)
            .all()
        }

        novas = 0
        analisadas = 0
        for reg in registros_erp:
            chave = (str(reg.numero_nota), str(reg.codigo))
            qtd_erp = float(reg.qtd_erp or 0)
            qtd_wms = float(mapa_wms.get(chave, 0.0))
            diferenca = round(qtd_erp - qtd_wms, 6)
            analisadas += 1

            if abs(diferenca) < min_dif:
                continue

            existente = WMSReconciliacaoDivergencia.query.filter_by(
                numero_nota=chave[0],
                codigo_item=chave[1],
                status='Aberta',
            ).first()
            if existente:
                existente.qtd_erp = qtd_erp
                existente.qtd_wms = qtd_wms
                existente.diferenca = diferenca
                existente.observacao = f'Atualizada em {datetime.now().strftime("%d/%m/%Y %H:%M")}'
                continue

            db.session.add(
                WMSReconciliacaoDivergencia(
                    numero_nota=chave[0],
                    codigo_item=chave[1],
                    qtd_erp=qtd_erp,
                    qtd_wms=qtd_wms,
                    diferenca=diferenca,
                    status='Aberta',
                    origem='ReconAutomatica',
                    observacao=f'Gerada por {usuario}',
                )
            )
            novas += 1

        if analisadas:
            db.session.commit()

        return {'analisadas': analisadas, 'novas_divergencias': novas}

    @staticmethod
    def gerar_alertas_operacionais(usuario='Sistema'):
        WMSService.garantir_parametros_operacionais()
        params = {
            p.chave: p.valor
            for p in WMSParametroOperacional.query.all()
        }
        horas_pend = float(params.get('WMS_PENDENCIA_ALERTA_HORAS', '24') or 24)
        limite_ocup = float(params.get('WMS_OCUPACAO_ALERTA_PERCENTUAL', '90') or 90)

        novos_alertas = 0
        agora = datetime.now()

        pendentes = ItemWMS.query.filter_by(ativo=True, localizacao_id=None).all()
        for item in pendentes:
            idade_h = ((agora - item.data_criacao).total_seconds() / 3600) if item.data_criacao else 0
            if idade_h < horas_pend:
                continue
            ref = f"{item.numero_nota}:{item.codigo_item}"
            existe = WMSAlertaOperacional.query.filter_by(
                tipo='PendenciaAntiga',
                referencia=ref,
                status='Aberto',
            ).first()
            if existe:
                continue
            db.session.add(
                WMSAlertaOperacional(
                    tipo='PendenciaAntiga',
                    severidade='ALTA',
                    referencia=ref,
                    descricao=f'Pendencia de enderecamento acima de {int(horas_pend)}h.',
                    status='Aberto',
                )
            )
            novos_alertas += 1

        localizacoes = LocalizacaoArmazem.query.filter_by(ativo=True).all()
        for loc in localizacoes:
            capacidade = float(loc.capacidade_maxima or 0)
            if capacidade <= 0:
                continue
            ocup = (float(loc.capacidade_atual or 0) / capacidade) * 100
            if ocup < limite_ocup:
                continue
            ref = str(loc.codigo)
            existe = WMSAlertaOperacional.query.filter_by(
                tipo='CapacidadeCritica',
                referencia=ref,
                status='Aberto',
            ).first()
            if existe:
                continue
            db.session.add(
                WMSAlertaOperacional(
                    tipo='CapacidadeCritica',
                    severidade='MEDIA' if ocup < 98 else 'ALTA',
                    referencia=ref,
                    descricao=f'Ocupacao em {ocup:.1f}% da capacidade.',
                    status='Aberto',
                )
            )
            novos_alertas += 1

        if novos_alertas:
            db.session.commit()

        return novos_alertas

    @staticmethod
    def obter_painel_governanca():
        WMSService.garantir_parametros_operacionais()
        WMSService.gerar_alertas_operacionais()

        alertas_abertos = WMSAlertaOperacional.query.filter_by(status='Aberto').order_by(WMSAlertaOperacional.criado_em.desc()).limit(20).all()
        divergencias_abertas = WMSReconciliacaoDivergencia.query.filter_by(status='Aberta').order_by(WMSReconciliacaoDivergencia.criado_em.desc()).limit(30).all()

        pendentes = ItemWMS.query.filter_by(ativo=True, localizacao_id=None).count()
        enderecados = ItemWMS.query.filter(ItemWMS.ativo == True, ItemWMS.localizacao_id.isnot(None)).count()

        return {
            'kpis': {
                'pendentes_enderecamento': pendentes,
                'itens_enderecados': enderecados,
                'alertas_abertos': len(alertas_abertos),
                'divergencias_abertas': len(divergencias_abertas),
            },
            'alertas': [
                {
                    'id': a.id,
                    'tipo': a.tipo,
                    'severidade': a.severidade,
                    'referencia': a.referencia,
                    'descricao': a.descricao,
                    'criado_em': a.criado_em.isoformat() if a.criado_em else None,
                }
                for a in alertas_abertos
            ],
            'divergencias': [
                {
                    'id': d.id,
                    'numero_nota': d.numero_nota,
                    'codigo_item': d.codigo_item,
                    'qtd_erp': d.qtd_erp,
                    'qtd_wms': d.qtd_wms,
                    'diferenca': d.diferenca,
                    'status': d.status,
                    'criado_em': d.criado_em.isoformat() if d.criado_em else None,
                }
                for d in divergencias_abertas
            ],
        }

    @staticmethod
    def sugerir_localizacao_inteligente(item_wms):
        """Sugere endereco por preferencia de SKU, agrupamento existente e capacidade."""
        if not item_wms:
            return None
        deposito_id = item_wms.deposito_id
        qtd = float(item_wms.qtd_atual or 0)
        sku = WMSSkuMestre.query.filter_by(codigo_item=item_wms.codigo_item, ativo=True).first()

        candidatos = []
        if sku and sku.endereco_preferencial:
            loc = LocalizacaoArmazem.query.filter_by(codigo=str(sku.endereco_preferencial).strip(), ativo=True).first()
            if loc:
                candidatos.append((0, loc, "Endereco preferencial do SKU"))

        existentes = (
            db.session.query(LocalizacaoArmazem, func.sum(EstoqueWMS.qtd_total).label("saldo"))
            .join(EstoqueWMS, EstoqueWMS.localizacao_id == LocalizacaoArmazem.id)
            .filter(
                EstoqueWMS.codigo_item == item_wms.codigo_item,
                LocalizacaoArmazem.ativo == True,
            )
            .group_by(LocalizacaoArmazem.id)
            .order_by(func.sum(EstoqueWMS.qtd_total).desc())
            .limit(5)
            .all()
        )
        for loc, saldo in existentes:
            candidatos.append((1, loc, f"Mesmo SKU ja armazenado ({float(saldo or 0):.3f})"))

        vazios = (
            LocalizacaoArmazem.query
            .filter(LocalizacaoArmazem.ativo == True)
            .order_by(LocalizacaoArmazem.capacidade_atual.asc(), LocalizacaoArmazem.codigo.asc())
            .limit(20)
            .all()
        )
        for loc in vazios:
            candidatos.append((2, loc, "Menor ocupacao disponivel"))

        vistos = set()
        for prioridade, loc, motivo in candidatos:
            if not loc or loc.id in vistos:
                continue
            vistos.add(loc.id)
            if deposito_id and loc.deposito_id and int(loc.deposito_id) != int(deposito_id):
                continue
            capacidade = float(loc.capacidade_maxima or 0) - float(loc.capacidade_atual or 0)
            if qtd > capacidade:
                continue
            return {
                "localizacao": loc,
                "motivo": motivo,
                "prioridade": prioridade,
                "capacidade_disponivel": capacidade,
            }
        return None

    @staticmethod
    def criar_unidade_logistica(tipo, usuario, item_ids=None, codigo=None, localizacao_id=None, observacao=None):
        tipo = str(tipo or "PALLET").strip().upper()
        if tipo not in {"PALLET", "CAIXA", "GAIOLA", "VOLUME"}:
            tipo = "VOLUME"
        if not codigo:
            codigo = f"{tipo[:3]}-{datetime.now().strftime('%Y%m%d%H%M%S')}"
        codigo = str(codigo).strip().upper()
        if not codigo or WMSUnidadeLogistica.query.filter_by(codigo=codigo).first():
            return None

        itens = []
        if item_ids:
            itens = ItemWMS.query.filter(ItemWMS.id.in_([int(i) for i in item_ids])).all()
        deposito_id = next((i.deposito_id for i in itens if i.deposito_id), None)
        unidade = WMSUnidadeLogistica(
            codigo=codigo,
            tipo=tipo,
            status="Aberta",
            deposito_id=deposito_id,
            localizacao_id=localizacao_id,
            observacao=observacao,
            criado_por=usuario,
        )
        db.session.add(unidade)
        db.session.flush()
        for item in itens:
            item.unidade_logistica_id = unidade.id
            if localizacao_id and not item.localizacao_id:
                item.localizacao_id = localizacao_id
        db.session.commit()
        return unidade

    @staticmethod
    def abrir_inventario_ciclico(localizacao_id, codigo_item, usuario, motivo=None):
        loc = LocalizacaoArmazem.query.get(localizacao_id)
        codigo_item = str(codigo_item or "").strip()
        if not loc or not codigo_item:
            return None
        estoque = EstoqueWMS.query.filter_by(codigo_item=codigo_item, localizacao_id=loc.id).first()
        qtd_sistema = float(estoque.qtd_total if estoque else 0)
        inv = WMSInventarioCiclico(
            codigo=f"INV-{datetime.now().strftime('%Y%m%d%H%M%S%f')}",
            localizacao_id=loc.id,
            codigo_item=codigo_item,
            qtd_sistema=qtd_sistema,
            criado_por=usuario,
            motivo=motivo,
        )
        tarefa = WMSTarefaOperacional(
            tipo="INVENTARIO",
            status="PENDENTE",
            localizacao_origem_id=loc.id,
            qtd_planejada=qtd_sistema,
            prioridade="ALTA",
            criado_por=usuario,
            observacao=f"Contar {codigo_item} em {loc.codigo}",
        )
        db.session.add_all([inv, tarefa])
        db.session.flush()
        inv.tarefa_id = tarefa.id
        db.session.commit()
        return inv

    @staticmethod
    def registrar_contagem_inventario(inventario_id, qtd_contada, usuario, aprovar=False):
        inv = WMSInventarioCiclico.query.get(inventario_id)
        if not inv or inv.status not in {"Aberto", "Contado"}:
            return None
        qtd_contada = float(qtd_contada or 0)
        inv.qtd_contada = qtd_contada
        inv.diferenca = qtd_contada - float(inv.qtd_sistema or 0)
        inv.status = "Aprovado" if aprovar else "Contado"
        inv.contado_por = usuario
        inv.contado_em = datetime.now()
        if inv.tarefa_id:
            tarefa = WMSTarefaOperacional.query.get(inv.tarefa_id)
            if tarefa:
                tarefa.status = "CONCLUIDA" if aprovar else "EM_EXECUCAO"
                tarefa.qtd_executada = qtd_contada
                tarefa.concluido_por = usuario if aprovar else None
                tarefa.concluido_em = datetime.now() if aprovar else None
        if abs(float(inv.diferenca or 0)) > 0:
            db.session.add(
                WMSReconciliacaoDivergencia(
                    numero_nota="INVENTARIO",
                    codigo_item=inv.codigo_item,
                    qtd_erp=inv.qtd_sistema,
                    qtd_wms=qtd_contada,
                    diferenca=inv.diferenca,
                    origem="InventarioCiclico",
                    observacao=f"Contagem {inv.codigo}",
                )
            )
        if aprovar:
            estoque = EstoqueWMS.query.filter_by(codigo_item=inv.codigo_item, localizacao_id=inv.localizacao_id).first()
            if estoque:
                estoque.qtd_total = qtd_contada
                estoque.data_atualizacao = datetime.now()
            else:
                db.session.add(EstoqueWMS(codigo_item=inv.codigo_item, localizacao_id=inv.localizacao_id, qtd_total=qtd_contada))
            inv.aprovado_por = usuario
            inv.aprovado_em = datetime.now()
        db.session.commit()
        return inv

    @staticmethod
    def bloquear_estoque(codigo_item, localizacao_id, qtd, usuario, motivo=None, status="Bloqueado"):
        estoque = EstoqueWMS.query.filter_by(codigo_item=str(codigo_item).strip(), localizacao_id=int(localizacao_id)).first()
        qtd = float(qtd or 0)
        if not estoque or qtd <= 0 or qtd > float((estoque.qtd_total or 0) - (estoque.qtd_bloqueada or 0)):
            return None
        item = ItemWMS.query.filter_by(codigo_item=estoque.codigo_item, localizacao_id=localizacao_id).first()
        if not item:
            return None
        estoque.qtd_bloqueada = float(estoque.qtd_bloqueada or 0) + qtd
        estoque.data_atualizacao = datetime.now()
        db.session.add(
            MovimentacaoWMS(
                item_wms_id=item.id,
                numero_nota="BLOQUEIO",
                tipo_movimentacao=status,
                localizacao_origem_id=localizacao_id,
                localizacao_destino_id=localizacao_id,
                qtd_movimentada=qtd,
                motivo=motivo or status,
                usuario=usuario,
            )
        )
        db.session.commit()
        return estoque

    @staticmethod
    def movimentar_estoque_guiado(codigo_item, origem_id, destino_id, qtd, usuario, motivo=None):
        origem = EstoqueWMS.query.filter_by(codigo_item=str(codigo_item).strip(), localizacao_id=int(origem_id)).first()
        destino_loc = LocalizacaoArmazem.query.get(destino_id)
        qtd = float(qtd or 0)
        if not origem or not destino_loc or qtd <= 0 or qtd > float(origem.qtd_total or 0):
            return None
        capacidade = float(destino_loc.capacidade_maxima or 0) - float(destino_loc.capacidade_atual or 0)
        if qtd > capacidade:
            return None
        item = ItemWMS.query.filter_by(codigo_item=origem.codigo_item, localizacao_id=int(origem_id)).first()
        if not item:
            return None
        destino = EstoqueWMS.query.filter_by(codigo_item=origem.codigo_item, localizacao_id=int(destino_id)).first()
        if not destino:
            destino = EstoqueWMS(codigo_item=origem.codigo_item, localizacao_id=int(destino_id), qtd_total=0, qtd_separada=0, qtd_bloqueada=0)
            db.session.add(destino)
        origem.qtd_total = float(origem.qtd_total or 0) - qtd
        destino.qtd_total = float(destino.qtd_total or 0) + qtd
        loc_origem = LocalizacaoArmazem.query.get(origem_id)
        if loc_origem:
            loc_origem.capacidade_atual = max(float(loc_origem.capacidade_atual or 0) - qtd, 0)
        destino_loc.capacidade_atual = float(destino_loc.capacidade_atual or 0) + qtd
        db.session.add(
            MovimentacaoWMS(
                item_wms_id=item.id,
                numero_nota=item.numero_nota,
                tipo_movimentacao="Reposicionamento",
                localizacao_origem_id=int(origem_id),
                localizacao_destino_id=int(destino_id),
                qtd_movimentada=qtd,
                motivo=motivo or "Movimentacao guiada por coletor",
                usuario=usuario,
            )
        )
        db.session.commit()
        return destino

    @staticmethod
    def criar_pedido_separacao(referencia, codigo_item, qtd, usuario, observacao=None):
        codigo_item = str(codigo_item or "").strip()
        qtd = float(qtd or 0)
        if not referencia or not codigo_item or qtd <= 0:
            return None
        estoque = (
            EstoqueWMS.query
            .filter(EstoqueWMS.codigo_item == codigo_item)
            .order_by((EstoqueWMS.qtd_total - EstoqueWMS.qtd_separada - EstoqueWMS.qtd_bloqueada).desc())
            .first()
        )
        if not estoque or float((estoque.qtd_total or 0) - (estoque.qtd_separada or 0) - (estoque.qtd_bloqueada or 0)) < qtd:
            return None
        estoque.qtd_separada = float(estoque.qtd_separada or 0) + qtd
        pedido = WMSPedidoSeparacao(
            referencia=str(referencia).strip(),
            codigo_item=codigo_item,
            qtd_solicitada=qtd,
            localizacao_id=estoque.localizacao_id,
            criado_por=usuario,
            observacao=observacao,
        )
        tarefa = WMSTarefaOperacional(
            tipo="SEPARACAO",
            status="PENDENTE",
            localizacao_origem_id=estoque.localizacao_id,
            qtd_planejada=qtd,
            prioridade="MEDIA",
            criado_por=usuario,
            observacao=f"Separar {qtd:g} de {codigo_item} para {referencia}",
        )
        db.session.add_all([pedido, tarefa])
        db.session.flush()
        pedido.tarefa_id = tarefa.id
        db.session.commit()
        return pedido

    @staticmethod
    def confirmar_separacao(pedido_id, usuario):
        pedido = WMSPedidoSeparacao.query.get(pedido_id)
        if not pedido or pedido.status in {"Separado", "Cancelado"}:
            return None
        estoque = EstoqueWMS.query.filter_by(codigo_item=pedido.codigo_item, localizacao_id=pedido.localizacao_id).first()
        if not estoque:
            return None
        qtd = float(pedido.qtd_solicitada or 0)
        estoque.qtd_total = max(float(estoque.qtd_total or 0) - qtd, 0)
        estoque.qtd_separada = max(float(estoque.qtd_separada or 0) - qtd, 0)
        pedido.qtd_separada = qtd
        pedido.status = "Separado"
        pedido.separado_por = usuario
        pedido.separado_em = datetime.now()
        if pedido.tarefa_id:
            tarefa = WMSTarefaOperacional.query.get(pedido.tarefa_id)
            if tarefa:
                tarefa.status = "CONCLUIDA"
                tarefa.qtd_executada = qtd
                tarefa.concluido_por = usuario
                tarefa.concluido_em = datetime.now()
        item = ItemWMS.query.filter_by(codigo_item=pedido.codigo_item, localizacao_id=pedido.localizacao_id).first()
        if not item:
            return None
        db.session.add(
            MovimentacaoWMS(
                item_wms_id=item.id,
                numero_nota=pedido.referencia,
                tipo_movimentacao="Separacao",
                localizacao_origem_id=pedido.localizacao_id,
                qtd_movimentada=qtd,
                motivo="Picking confirmado",
                usuario=usuario,
            )
        )
        db.session.commit()
        return pedido

    @staticmethod
    def produtividade_operadores(dias=7):
        inicio = datetime.now() - timedelta(days=int(dias or 7))
        movs = (
            db.session.query(
                MovimentacaoWMS.usuario,
                func.count(MovimentacaoWMS.id).label("movimentacoes"),
                func.sum(MovimentacaoWMS.qtd_movimentada).label("qtd_total"),
            )
            .filter(MovimentacaoWMS.data_movimentacao >= inicio)
            .group_by(MovimentacaoWMS.usuario)
            .all()
        )
        tarefas = (
            db.session.query(
                WMSTarefaOperacional.concluido_por,
                func.count(WMSTarefaOperacional.id).label("tarefas"),
            )
            .filter(WMSTarefaOperacional.concluido_em >= inicio, WMSTarefaOperacional.concluido_por.isnot(None))
            .group_by(WMSTarefaOperacional.concluido_por)
            .all()
        )
        mapa = {}
        for row in movs:
            mapa[row.usuario or "Sistema"] = {
                "usuario": row.usuario or "Sistema",
                "movimentacoes": int(row.movimentacoes or 0),
                "qtd_total": float(row.qtd_total or 0),
                "tarefas_concluidas": 0,
            }
        for row in tarefas:
            usuario = row.concluido_por or "Sistema"
            mapa.setdefault(usuario, {"usuario": usuario, "movimentacoes": 0, "qtd_total": 0.0, "tarefas_concluidas": 0})
            mapa[usuario]["tarefas_concluidas"] = int(row.tarefas or 0)
        return sorted(mapa.values(), key=lambda x: (x["tarefas_concluidas"], x["movimentacoes"], x["qtd_total"]), reverse=True)

    @staticmethod
    def analisar_pendencia_enderecamento(item):
        data_criacao = getattr(item, 'data_criacao', None)
        idade_horas = 0.0
        if data_criacao:
            idade_horas = max((datetime.now() - data_criacao).total_seconds() / 3600, 0.0)

        ordem_vinculada = bool(getattr(item, 'ordem_servico', None) or getattr(item, 'ordem_compra', None))
        if idade_horas >= 24 or float(getattr(item, 'qtd_atual', 0) or 0) >= 50:
            prioridade = 'Critica'
        elif idade_horas >= 8 or ordem_vinculada:
            prioridade = 'Alta'
        elif idade_horas >= 2:
            prioridade = 'Media'
        else:
            prioridade = 'Baixa'

        return {
            'idade_horas': round(idade_horas, 2),
            'prioridade': prioridade,
            'ordem_vinculada': ordem_vinculada,
        }

    @staticmethod
    def obter_cockpit_operacional():
        WMSService.garantir_parametros_operacionais()
        WMSService.gerar_alertas_operacionais()

        pendentes = ItemWMS.query.filter_by(ativo=True, localizacao_id=None).all()
        enderecados = ItemWMS.query.filter(ItemWMS.ativo == True, ItemWMS.localizacao_id.isnot(None)).all()
        alertas_abertos = WMSAlertaOperacional.query.filter_by(status='Aberto').count()
        divergencias_abertas = WMSReconciliacaoDivergencia.query.filter_by(status='Aberta').count()

        prioridade_contagem = {'Critica': 0, 'Alta': 0, 'Media': 0, 'Baixa': 0}
        for item in pendentes:
            prioridade = WMSService.analisar_pendencia_enderecamento(item)['prioridade']
            prioridade_contagem[prioridade] = prioridade_contagem.get(prioridade, 0) + 1

        depositos = DepositoWMS.query.filter_by(ativo=True).order_by(DepositoWMS.codigo.asc()).all()
        ocupacao_por_deposito = []
        for deposito in depositos:
            localizacoes = LocalizacaoArmazem.query.filter_by(deposito_id=deposito.id, ativo=True).all()
            capacidade_total = sum(float(loc.capacidade_maxima or 0) for loc in localizacoes)
            capacidade_ocupada = sum(float(loc.capacidade_atual or 0) for loc in localizacoes)
            percentual = round((capacidade_ocupada / capacidade_total) * 100, 2) if capacidade_total > 0 else 0.0
            locais_criticos = sum(
                1
                for loc in localizacoes
                if float(loc.capacidade_maxima or 0) > 0
                and ((float(loc.capacidade_atual or 0) / float(loc.capacidade_maxima or 1)) * 100) >= 90
            )
            ocupacao_por_deposito.append(
                {
                    'deposito_id': deposito.id,
                    'deposito_codigo': deposito.codigo,
                    'deposito_nome': deposito.nome,
                    'capacidade_total': round(capacidade_total, 2),
                    'capacidade_ocupada': round(capacidade_ocupada, 2),
                    'ocupacao_percentual': percentual,
                    'localizacoes_criticas': locais_criticos,
                }
            )

        movimentacoes_recentes = (
            MovimentacaoWMS.query.order_by(MovimentacaoWMS.data_movimentacao.desc(), MovimentacaoWMS.id.desc()).limit(8).all()
        )

        top_pendentes = (
            db.session.query(
                ItemWMS.codigo_item,
                func.max(ItemWMS.descricao).label('descricao'),
                func.sum(ItemWMS.qtd_atual).label('qtd_total'),
                func.count(ItemWMS.id).label('linhas'),
            )
            .filter(ItemWMS.ativo == True, ItemWMS.localizacao_id.is_(None))
            .group_by(ItemWMS.codigo_item)
            .order_by(func.sum(ItemWMS.qtd_atual).desc(), ItemWMS.codigo_item.asc())
            .limit(8)
            .all()
        )

        notas_pendentes = (
            db.session.query(
                ItemWMS.numero_nota,
                func.count(ItemWMS.id).label('linhas'),
                func.sum(ItemWMS.qtd_atual).label('qtd_total'),
                func.max(ItemWMS.data_criacao).label('ultima_entrada'),
            )
            .filter(ItemWMS.ativo == True, ItemWMS.localizacao_id.is_(None))
            .group_by(ItemWMS.numero_nota)
            .order_by(func.max(ItemWMS.data_criacao).desc(), ItemWMS.numero_nota.desc())
            .limit(8)
            .all()
        )

        movimentacoes_24h = MovimentacaoWMS.query.filter(
            MovimentacaoWMS.data_movimentacao >= datetime.now() - timedelta(hours=24)
        ).count()
        integracoes_pendentes = WMSIntegracaoEvento.query.filter(
            WMSIntegracaoEvento.status.in_(['Pendente', 'Processando', 'Falha'])
        ).count()
        integracoes_falha = WMSIntegracaoEvento.query.filter(
            WMSIntegracaoEvento.status.in_(['Falha', 'DeadLetter'])
        ).count()
        integracoes_sucesso_24h = WMSIntegracaoEvento.query.filter(
            WMSIntegracaoEvento.status == 'Sucesso',
            WMSIntegracaoEvento.processado_em.isnot(None),
            WMSIntegracaoEvento.processado_em >= datetime.now() - timedelta(hours=24),
        ).count()
        ultimo_evento = (
            WMSIntegracaoEvento.query
            .order_by(WMSIntegracaoEvento.criado_em.desc(), WMSIntegracaoEvento.id.desc())
            .first()
        )

        disponibilidade_total = sum(float(item.qtd_atual or 0) for item in enderecados)
        idade_media = round(
            (sum(WMSService.analisar_pendencia_enderecamento(item)['idade_horas'] for item in pendentes) / len(pendentes)),
            2,
        ) if pendentes else 0.0

        return {
            'cards': {
                'pendentes_enderecamento': len(pendentes),
                'itens_enderecados': len(enderecados),
                'saldo_enderecado': round(disponibilidade_total, 2),
                'idade_media_pendente_horas': idade_media,
                'movimentacoes_24h': movimentacoes_24h,
                'alertas_abertos': alertas_abertos,
                'divergencias_abertas': divergencias_abertas,
                'integracoes_pendentes': integracoes_pendentes,
                'integracoes_falha': integracoes_falha,
                'integracoes_sucesso_24h': integracoes_sucesso_24h,
                'notas_pendentes_fila': len(notas_pendentes),
            },
            'integracao_fiscal': {
                'fila_pendente': integracoes_pendentes,
                'falhas_abertas': integracoes_falha,
                'sucesso_24h': integracoes_sucesso_24h,
                'ultimo_evento': {
                    'id': ultimo_evento.id,
                    'tipo_evento': ultimo_evento.tipo_evento,
                    'referencia': ultimo_evento.referencia,
                    'status': ultimo_evento.status,
                    'criado_em': ultimo_evento.criado_em.isoformat() if ultimo_evento and ultimo_evento.criado_em else None,
                } if ultimo_evento else None,
            },
            'prioridades': prioridade_contagem,
            'ocupacao_por_deposito': ocupacao_por_deposito,
            'movimentacoes_recentes': [
                {
                    'id': m.id,
                    'numero_nota': m.numero_nota,
                    'tipo_movimentacao': m.tipo_movimentacao,
                    'qtd_movimentada': float(m.qtd_movimentada or 0),
                    'usuario': m.usuario,
                    'data_movimentacao': m.data_movimentacao.isoformat() if m.data_movimentacao else None,
                }
                for m in movimentacoes_recentes
            ],
            'top_pendentes': [
                {
                    'codigo_item': row.codigo_item,
                    'descricao': row.descricao,
                    'qtd_total': float(row.qtd_total or 0),
                    'linhas': int(row.linhas or 0),
                }
                for row in top_pendentes
            ],
            'notas_pendentes': [
                {
                    'numero_nota': row.numero_nota,
                    'linhas': int(row.linhas or 0),
                    'qtd_total': float(row.qtd_total or 0),
                    'ultima_entrada': row.ultima_entrada.isoformat() if row.ultima_entrada else None,
                }
                for row in notas_pendentes
            ],
        }
