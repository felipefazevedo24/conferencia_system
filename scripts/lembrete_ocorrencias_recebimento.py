"""Lembrete no Teams das divergências de recebimento paradas (sem atualização
de Compras/Fiscal há N dias - padrão 3, config OCORRENCIA_RECEBIMENTO_LEMBRETE_DIAS).

Rodar UMA vez por dia pela aba "Tasks" do PythonAnywhere (thread em segundo
plano no WSGI pode nascer em mais de um worker e duplicar o aviso):

    cd ~/conferencia_system && DATABASE_URL='...' <python do venv> scripts/lembrete_ocorrencias_recebimento.py

(SEMPRE com DATABASE_URL='...' copiado do arquivo WSGI - sem ele o script
roda contra um SQLite local vazio e não avisa nada.)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from conferencia_app import create_app
from conferencia_app.services.ocorrencia_recebimento_service import enviar_lembretes

app = create_app()
with app.app_context():
    resultado = enviar_lembretes()
    print(f"Lembretes enviados: {resultado['enviados']} (paradas há {resultado['dias']}+ dias) {resultado['ids']}")
