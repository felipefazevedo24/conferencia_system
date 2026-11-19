"""Atualiza o espelho do Faturamento x Custo (aba Faturamento) a partir da
API do ERP. A tela também atualiza sozinha quando o espelho passa de 6 h,
mas a busca leva uns 4 minutos: rodando de madrugada, quem abre de manhã já
encontra o dado do dia anterior.

Uma scheduled task diária na aba "Tasks" do PythonAnywhere. O horário de lá
é UTC: para 5h de Brasília, cadastre 08:00.

    cd ~/conferencia_system && source ~/estoque_alerta_env.sh && <python do venv> scripts/faturamento_sincronizar.py

O ~/estoque_alerta_env.sh é o mesmo do scripts/alerta_estoque_reservas.py
(ver lá): sem o DATABASE_URL o script grava num SQLite local vazio, "dá
certo" e a tela de produção continua velha.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from conferencia_app import create_app
from conferencia_app.services.faturamento_service import atualizar

app = create_app()
with app.app_context():
    r = atualizar("tarefa agendada")
    print(f"Faturamento: {r['linhas_faturamento']} linhas | Custo: {r['linhas_custo']} linhas")
