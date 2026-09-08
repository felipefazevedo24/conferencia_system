"""Serviço para gerenciar detalhes de solicitações de coleta."""

from datetime import datetime
from flask import current_app
from sqlalchemy import text
from ..extensions import db


def criar_ou_atualizar_detalhes_coleta(solicitacao_id, data_liberacao=None, observacao=None, usuario=None):
    """Cria ou atualiza os detalhes de uma solicitação de coleta."""
    try:
        query = "SELECT id FROM solicitacao_coleta_detalhes WHERE solicitacao_id = :solicitacao_id"
        result = db.session.execute(text(query), {"solicitacao_id": solicitacao_id}).first()
        
        agora = datetime.now()
        
        if result:
            # Atualizar
            update_query = """
                UPDATE solicitacao_coleta_detalhes 
                SET data_liberacao = :data_liberacao, observacao = :observacao,
                    atualizado_por = :atualizado_por, atualizado_em = :atualizado_em
                WHERE solicitacao_id = :solicitacao_id
            """
            db.session.execute(text(update_query), {
                "data_liberacao": data_liberacao,
                "observacao": observacao,
                "atualizado_por": usuario,
                "atualizado_em": agora,
                "solicitacao_id": solicitacao_id
            })
        else:
            # Inserir
            insert_query = """
                INSERT INTO solicitacao_coleta_detalhes 
                (solicitacao_id, data_liberacao, status_liberacao, observacao, criado_por, criado_em, atualizado_em)
                VALUES (:solicitacao_id, :data_liberacao, :status_liberacao, :observacao, :criado_por, :criado_em, :atualizado_em)
            """
            status = "Liberada" if data_liberacao and data_liberacao <= agora else "Pendente"
            db.session.execute(text(insert_query), {
                "solicitacao_id": solicitacao_id,
                "data_liberacao": data_liberacao,
                "status_liberacao": status,
                "observacao": observacao,
                "criado_por": usuario,
                "criado_em": agora,
                "atualizado_em": agora
            })
        
        db.session.commit()
        return True
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Erro ao criar/atualizar detalhes coleta: {str(e)}")
        return False


def obter_detalhes_coleta(solicitacao_id):
    """Obtém os detalhes de uma solicitação de coleta."""
    try:
        query = """
            SELECT id, solicitacao_id, data_liberacao, status_liberacao, observacao, criado_por, criado_em
            FROM solicitacao_coleta_detalhes 
            WHERE solicitacao_id = :solicitacao_id
        """
        result = db.session.execute(text(query), {"solicitacao_id": solicitacao_id}).first()
        
        if result:
            data_liberacao = result[2]
            if isinstance(data_liberacao, str):
                try:
                    data_liberacao = datetime.fromisoformat(data_liberacao)
                except ValueError:
                    data_liberacao = None
            return {
                "id": result[0],
                "solicitacao_id": result[1],
                "data_liberacao": data_liberacao,
                "status_liberacao": result[3],
                "observacao": result[4],
                "criado_por": result[5],
                "criado_em": result[6]
            }
        return None
    except Exception as e:
        current_app.logger.error(f"Erro ao obter detalhes coleta: {str(e)}")
        return None


def adicionar_anexo(solicitacao_coleta_id, arquivo_nome, arquivo_path, tipo_arquivo=None, tamanho_bytes=None, usuario=None):
    """Adiciona um anexo a uma solicitação de coleta."""
    try:
        insert_query = """
            INSERT INTO solicitacao_coleta_anexo 
            (solicitacao_coleta_id, arquivo_nome, arquivo_path, tipo_arquivo, tamanho_bytes, uploadado_por, uploadado_em)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """
        db.session.execute(text(insert_query), {
            "solicitacao_coleta_id": solicitacao_coleta_id,
            "arquivo_nome": arquivo_nome,
            "arquivo_path": arquivo_path,
            "tipo_arquivo": tipo_arquivo,
            "tamanho_bytes": tamanho_bytes,
            "uploadado_por": usuario,
            "uploadado_em": datetime.now()
        })
        db.session.commit()
        return True
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Erro ao adicionar anexo coleta: {str(e)}")
        return False


def obter_anexos(solicitacao_coleta_id):
    """Obtém todos os anexos de uma solicitação de coleta."""
    try:
        query = """
            SELECT id, arquivo_nome, arquivo_path, tipo_arquivo, tamanho_bytes, uploadado_por, uploadado_em
            FROM solicitacao_coleta_anexo 
            WHERE solicitacao_coleta_id = %s
            ORDER BY uploadado_em DESC
        """
        results = db.session.execute(text(query), {"solicitacao_coleta_id": solicitacao_coleta_id}).fetchall()
        
        return [
            {
                "id": r[0],
                "arquivo_nome": r[1],
                "arquivo_path": r[2],
                "tipo_arquivo": r[3],
                "tamanho_bytes": r[4],
                "uploadado_por": r[5],
                "uploadado_em": r[6]
            }
            for r in results
        ]
    except Exception as e:
        current_app.logger.error(f"Erro ao obter anexos: {str(e)}")
        return []


def tem_estoque_critico(solicitacao_id):
    """Verifica se a solicitação tem itens com estoque crítico/baixo."""
    try:
        # Busca a OC associada à solicitação de coleta
        query = """
            SELECT asi.numero_oc 
            FROM agendamento_solicitacao asi
            WHERE asi.id = %s AND asi.tipo = 'COLETA'
            LIMIT 1
        """
        result = db.session.execute(text(query), {"solicitacao_id": solicitacao_id}).first()
        
        if not result or not result[0]:
            return False
        
        numero_oc = result[0]
        
        # Busca itens da OC no WMS que têm estoque baixo/crítico
        query_estoque = """
            SELECT COUNT(*)
            FROM wms_sku_mestre wsm
            WHERE wsm.codigo_erp IN (
                SELECT DISTINCT codigo_item FROM estoque_wms 
                WHERE codigo_item IN (
                    SELECT DISTINCT codigo_item 
                    FROM item_nota 
                    WHERE numero_nota IN (
                        SELECT numero_nota FROM agendamento_solicitacao 
                        WHERE numero_oc = %s
                    )
                )
            )
            AND wsm.estoque_minimo > 0
            AND (SELECT SUM(COALESCE(qtd_total, 0)) FROM estoque_wms WHERE codigo_item = wsm.codigo_item) < wsm.estoque_minimo
        """
        
        result = db.session.execute(text(query_estoque), {"numero_oc": numero_oc}).first()
        return result and result[0] > 0
    except Exception as e:
        current_app.logger.warning(f"Erro ao verificar estoque crítico: {str(e)}")
        return False
