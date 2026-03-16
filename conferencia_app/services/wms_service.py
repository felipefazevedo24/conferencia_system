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
    ItemNota
)


class WMSService:
    """Serviço centralizado para operações de WMS"""

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
            
        # Usa quantidade da nota se não foi passada
        if qtd_recebida is None:
            qtd_recebida = item_nota.qtd_real or 0

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
            
        if qtd_movimentada > item_wms.qtd_atual:
            return None  # Quantidade indisponível
            
        # Estoca origem
        if item_wms.localizacao_id:
            estoque_origem = EstoqueWMS.query.filter_by(
                codigo_item=item_wms.codigo_item,
                localizacao_id=item_wms.localizacao_id
            ).first()
            if estoque_origem:
                estoque_origem.qtd_total -= qtd_movimentada
                
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
                
            localizacao_destino = LocalizacaoArmazem.query.get(localizacao_destino_id)
            if localizacao_destino:
                localizacao_destino.capacidade_atual += qtd_movimentada
                
            item_wms.localizacao_id = localizacao_destino_id
            
        # Atualiza status baseado no tipo de movimentação
        if tipo_movimentacao == 'Separacao':
            item_wms.status = 'Separado'
        elif tipo_movimentacao == 'Devolucao':
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
        localizacoes = LocalizacaoArmazem.query.filter(
            LocalizacaoArmazem.ativo == True
        ).order_by(
            LocalizacaoArmazem.data_criacao.asc()
        ).first()
        
        return localizacoes

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

        if not item_wms or not localizacao:
            return None

        if item_wms.localizacao_id is not None:
            return None

        qtd = item_wms.qtd_atual or 0

        if not codigo_grv:
            return None

        if not ordem_servico and not ordem_compra:
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
