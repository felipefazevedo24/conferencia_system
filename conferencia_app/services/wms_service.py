"""
Serviço WMS - Warehouse Management System
Gerencia localização, estoque e movimentação de itens no armazém
"""
from datetime import datetime
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
    DepositoWMS,
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
        'WMS_RECON_DIVERGENCIA_MINIMA': ('0.01', 'Diferenca minima para registrar divergencia de reconciliacao.'),
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
    def criar_localizacao(rua, predio, nivel, apartamento):
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
        apartamento = str(apartamento).strip()
        codigo = f"{rua}-{predio}-{nivel}-{apartamento}"
        
        # Verifica se já existe
        localizacao = LocalizacaoArmazem.query.filter_by(codigo=codigo).first()
        if localizacao:
            return None
            
        localizacao = LocalizacaoArmazem(
            codigo=codigo,
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
    def transferir_entre_depositos(item_wms_id, deposito_destino_id, usuario, motivo=None):
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
        
        # Se já está no mesmo depósito, apenas retorna sucesso
        if item.deposito_id == deposito_destino_id:
            return {'sucesso': True, 'mensagem': 'Item já está neste depósito'}
        
        # Registrar movimentação no histórico
        deposito_origem_id = item.deposito_id
        localizacao_origem_id = item.localizacao_id
        
        movimentacao = MovimentacaoWMS(
            item_wms_id=item_wms_id,
            numero_nota=item.numero_nota,
            tipo_movimentacao='Transferencia Deposito',
            localizacao_origem_id=localizacao_origem_id,
            localizacao_destino_id=None,  # Será endereçado depois no novo depósito
            qtd_movimentada=item.qtd_atual,
            motivo=motivo if motivo else f'Transferência DEP {deposito_origem_id} -> {deposito_destino_id}',
            usuario=usuario,
            data_movimentacao=datetime.now()
        )
        
        # Atualizar item: novo depósito, limpar localização, marcar como pendente
        item.deposito_id = deposito_destino_id
        item.localizacao_id = None  # Será endereçado no novo depósito
        item.status = 'Pendente Enderecamento'
        
        db.session.add(movimentacao)
        db.session.commit()
        
        return {
            'sucesso': True,
            'mensagem': 'Transferência realizada. Item pendente de endereçamento no novo depósito.',
            'item_id': item_wms_id,
            'deposito_origem': deposito_origem_id,
            'deposito_destino': deposito_destino_id,
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
    def listar_pendentes_enderecamento(numero_nota=None):
        WMSService._sanear_pendencias_enderecamento()

        query = ItemWMS.query.filter_by(localizacao_id=None, ativo=True)
        if numero_nota:
            query = query.filter(ItemWMS.numero_nota == str(numero_nota))
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
    def enderecar_item_pendente(item_wms_id, localizacao_id, usuario, codigo_grv, ordem_servico=None, ordem_compra=None):
        item_wms = ItemWMS.query.get(item_wms_id)
        localizacao = LocalizacaoArmazem.query.get(localizacao_id)

        if not item_wms or not localizacao or not item_wms.ativo or not localizacao.ativo:
            return None

        if item_wms.localizacao_id is not None:
            return None

        qtd = float(item_wms.qtd_atual or 0)
        if qtd <= 0:
            return None

        capacidade_disponivel = float(localizacao.capacidade_maxima or 0) - float(localizacao.capacidade_atual or 0)
        if qtd > capacidade_disponivel:
            return None

        if not codigo_grv:
            return None

        if not ordem_servico and not ordem_compra:
            return None

        if not WMSService._can_transition_status(item_wms.status, 'Armazenado'):
            return None

        item_wms.localizacao_id = localizacao_id
        item_wms.usuario_armazenamento = usuario
        item_wms.data_armazenamento = datetime.now()
        item_wms.codigo_grv = str(codigo_grv).strip()
        item_wms.ordem_servico = str(ordem_servico).strip() if ordem_servico else None
        item_wms.ordem_compra = str(ordem_compra).strip() if ordem_compra else None
        item_wms.status = 'Armazenado'

        movimentacao = MovimentacaoWMS(
            item_wms_id=item_wms.id,
            numero_nota=item_wms.numero_nota,
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
            codigo_item=item_wms.codigo_item,
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
        return item_wms

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
