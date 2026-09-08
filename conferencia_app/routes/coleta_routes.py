"""Endpoints para gerenciamento de coleta (detalhes, anexos, estoque crítico)."""

from datetime import datetime
import os

from flask import Blueprint, current_app, jsonify, request, session
from werkzeug.utils import secure_filename

from ..auth import login_required, permission_required
from ..extensions import db
from ..models import AgendamentoSolicitacao
from ..services.solicitacao_coleta_service import (
    criar_ou_atualizar_detalhes_coleta,
    obter_detalhes_coleta,
    adicionar_anexo,
    obter_anexos,
    tem_estoque_critico,
)

coleta_bp = Blueprint("coleta", __name__)


@coleta_bp.route("/api/logistica/solicitacao/<int:solicitacao_id>/coleta-detalhes", methods=["POST"])
@login_required
@permission_required("PAGE_LOGISTICA_AGENDAMENTO")
def salvar_coleta_detalhes(solicitacao_id: int):
    """Salva ou atualiza detalhes de coleta (data de liberação e observação)."""
    solicitacao = AgendamentoSolicitacao.query.get(solicitacao_id)
    if not solicitacao:
        return jsonify({"error": "Solicitação não encontrada."}), 404
    
    data = request.get_json() or {}
    data_liberacao_str = data.get("data_liberacao")
    observacao = data.get("observacao", "").strip()
    usuario = session.get("username", "")
    
    try:
        data_liberacao = None
        if data_liberacao_str:
            # Esperado formato ISO (2025-01-15T10:30:00)
            data_liberacao = datetime.fromisoformat(data_liberacao_str.replace("Z", "+00:00"))
        
        resultado = criar_ou_atualizar_detalhes_coleta(
            solicitacao_id=solicitacao_id,
            data_liberacao=data_liberacao,
            observacao=observacao,
            usuario=usuario
        )
        
        return jsonify({
            "sucesso": True,
            "mensagem": "Detalhes de coleta salvos com sucesso.",
            "detalhes": resultado
        })
    except ValueError as e:
        return jsonify({"error": f"Erro ao processar data: {str(e)}"}), 400
    except Exception as e:
        current_app.logger.error(f"Erro ao salvar coleta detalhes: {str(e)}")
        return jsonify({"error": "Erro ao salvar detalhes de coleta."}), 500


@coleta_bp.route("/api/logistica/solicitacao/<int:solicitacao_id>/coleta-detalhes", methods=["GET"])
@login_required
@permission_required("PAGE_LOGISTICA_AGENDAMENTO")
def obter_coleta_detalhes(solicitacao_id: int):
    """Obtém detalhes de coleta de uma solicitação."""
    solicitacao = AgendamentoSolicitacao.query.get(solicitacao_id)
    if not solicitacao:
        return jsonify({"error": "Solicitação não encontrada."}), 404
    
    try:
        detalhes = obter_detalhes_coleta(solicitacao_id)
        return jsonify({
            "sucesso": True,
            "detalhes": detalhes if detalhes else None
        })
    except Exception as e:
        current_app.logger.error(f"Erro ao obter coleta detalhes: {str(e)}")
        return jsonify({"error": "Erro ao obter detalhes de coleta."}), 500


@coleta_bp.route("/api/logistica/solicitacao/<int:solicitacao_id>/coleta-anexo", methods=["POST"])
@login_required
@permission_required("PAGE_LOGISTICA_AGENDAMENTO")
def upload_coleta_anexo(solicitacao_id: int):
    """Upload de anexo (foto, documento) para coleta."""
    solicitacao = AgendamentoSolicitacao.query.get(solicitacao_id)
    if not solicitacao:
        return jsonify({"error": "Solicitacao nao encontrada."}), 404
    
    # Verificar se já existe detalhes de coleta
    detalhes_existente = obter_detalhes_coleta(solicitacao_id)
    if not detalhes_existente:
        return jsonify({"error": "Detalhes de coleta nao encontrados. Salve os detalhes primeiro."}), 400
    
    arquivo = request.files.get("arquivo")
    if not arquivo or not arquivo.filename:
        return jsonify({"error": "Nenhum arquivo fornecido."}), 400
    
    nome_original = secure_filename(arquivo.filename)
    extensao = nome_original.rsplit(".", 1)[-1].lower() if "." in nome_original else ""
    
    # Permitir apenas certos tipos de arquivo
    extensoes_permitidas = {"pdf", "jpg", "jpeg", "png", "gif", "webp", "doc", "docx"}
    if extensao not in extensoes_permitidas:
        return jsonify({"error": f"Tipo de arquivo nao permitido. Use: {', '.join(extensoes_permitidas)}"}), 400
    
    usuario = session.get("username", "")
    form_data = request.form.to_dict()
    tipo_arquivo = form_data.get("tipo_arquivo", "Documento")
    
    try:
        # Salvar arquivo
        pasta_anexos = os.path.join(current_app.instance_path, "coletas")
        os.makedirs(pasta_anexos, exist_ok=True)
        
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        nome_arquivo = f"coleta_{solicitacao_id}_{stamp}_{nome_original}"
        caminho_arquivo = os.path.join(pasta_anexos, nome_arquivo)
        arquivo.save(caminho_arquivo)
        
        # Registrar no banco
        tamanho_bytes = os.path.getsize(caminho_arquivo)
        caminho_relativo = f"coletas/{nome_arquivo}"
        
        resultado = adicionar_anexo(
            solicitacao_coleta_id=detalhes_existente.get("id"),
            arquivo_nome=nome_arquivo,
            arquivo_path=caminho_relativo,
            tipo_arquivo=tipo_arquivo,
            tamanho_bytes=tamanho_bytes,
            uploadado_por=usuario
        )
        
        return jsonify({
            "sucesso": True,
            "mensagem": "Arquivo anexado com sucesso.",
            "anexo": resultado
        })
    except Exception as e:
        current_app.logger.error(f"Erro ao fazer upload de anexo: {str(e)}")
        return jsonify({"error": "Erro ao fazer upload do arquivo."}), 500


@coleta_bp.route("/api/logistica/solicitacao/<int:solicitacao_id>/coleta-anexos", methods=["GET"])
@login_required
@permission_required("PAGE_LOGISTICA_AGENDAMENTO")
def listar_coleta_anexos(solicitacao_id: int):
    """Lista anexos de uma solicitacao de coleta."""
    solicitacao = AgendamentoSolicitacao.query.get(solicitacao_id)
    if not solicitacao:
        return jsonify({"error": "Solicitacao nao encontrada."}), 404
    
    detalhes = obter_detalhes_coleta(solicitacao_id)
    if not detalhes:
        return jsonify({
            "sucesso": True,
            "anexos": []
        })
    
    try:
        anexos = obter_anexos(detalhes.get("id"))
        return jsonify({
            "sucesso": True,
            "anexos": anexos or []
        })
    except Exception as e:
        current_app.logger.error(f"Erro ao listar anexos: {str(e)}")
        return jsonify({"error": "Erro ao listar anexos."}), 500


@coleta_bp.route("/api/logistica/solicitacao/<int:solicitacao_id>/tem-estoque-critico", methods=["GET"])
@login_required
@permission_required("PAGE_LOGISTICA_AGENDAMENTO")
def verificar_estoque_critico(solicitacao_id: int):
    """Verifica se ha itens com estoque critico para uma solicitacao."""
    solicitacao = AgendamentoSolicitacao.query.get(solicitacao_id)
    if not solicitacao:
        return jsonify({"error": "Solicitacao nao encontrada."}), 404
    
    try:
        critico = tem_estoque_critico(solicitacao_id)
        return jsonify({
            "sucesso": True,
            "tem_critico": critico
        })
    except Exception as e:
        current_app.logger.error(f"Erro ao verificar estoque critico: {str(e)}")
        return jsonify({"error": "Erro ao verificar estoque critico."}), 500
