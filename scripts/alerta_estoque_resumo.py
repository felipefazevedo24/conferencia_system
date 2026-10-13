"""Resumo por e-mail dos itens de curva A em alerta e sem OC aberta: sem
estoque disponível, críticos e abaixo do mínimo sugerido, com a sugestão de
compra de cada um. Lista vazia não gera e-mail.

Rodar às 7h e às 15h: são DUAS scheduled tasks diárias na aba "Tasks" do
PythonAnywhere. O horário de lá é UTC, então cadastre 10:00 e 18:00. As duas
com o mesmo comando:

    cd ~/conferencia_system && source ~/estoque_alerta_env.sh && <python do venv> scripts/alerta_estoque_resumo.py

O ~/estoque_alerta_env.sh é o mesmo do scripts/alerta_estoque_reservas.py
(ver lá): sem o DATABASE_URL o script roda contra um SQLite local vazio e
marca todo item como novo em todo envio.

Pra só ver o e-mail (manda pra um endereço, sem gravar nada): acrescente
--teste fulano@colmac.com.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from conferencia_app import create_app
from conferencia_app.services.estoque_alerta_service import enviar_resumo_curva_a

app = create_app()
with app.app_context():
    r = enviar_resumo_curva_a(teste_para=sys.argv[sys.argv.index("--teste") + 1] if "--teste" in sys.argv else None)
    print(
        f"Curva A: {r['zerado']} sem estoque, {r['critico']} críticos, {r['abaixo_minimo']} abaixo do mínimo "
        f"({r['novos']} novos, {r['com_oc']} de fora por ter OC) | e-mail enviado: {r['enviado']} | sem resposta do GRV: {r['sem_resposta']}"
    )
