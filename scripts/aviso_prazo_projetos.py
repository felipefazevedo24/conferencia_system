"""Aviso no Teams das tarefas de projeto vencendo (em ate' 2 dias) ou vencidas.

Um card por projeto, cada tarefa no maximo uma vez por dia. Rodar UMA vez por
dia pela aba "Tasks" do PythonAnywhere (thread em segundo plano no WSGI pode
nascer em mais de um worker e duplicar o aviso):

    cd ~/conferencia_system && DATABASE_URL='...' <python do venv> scripts/aviso_prazo_projetos.py

(SEMPRE com DATABASE_URL='...' copiado do arquivo WSGI - sem ele o script
roda contra um SQLite local vazio e nao avisa nada.)

O canal vem de TEAMS_WEBHOOK_PROJETOS_URL ou da chave "webhook_projetos" do
instance/teams_config.json.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from conferencia_app import create_app
from conferencia_app.services.projetos_service import enviar_avisos_prazo

app = create_app()
with app.app_context():
    resultado = enviar_avisos_prazo()
    print(f"Avisos de prazo: {resultado['projetos']} projeto(s) em {resultado['data']}")
