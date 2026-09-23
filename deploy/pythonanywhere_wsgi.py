"""
Copie este conteudo para o arquivo WSGI do PythonAnywhere.

Troque apenas:
- PA_USERNAME
- PA_PROJECT_DIR, se a pasta do projeto tiver outro nome
- SECRET_KEY
- CONSYSTE_TOKEN

Se quiser usar MySQL no PythonAnywhere, preencha DATABASE_URL e deixe DB_PATH
comentado. Se quiser subir rapido com o SQLite atual, deixe DB_PATH ativo.
"""

import os
import sys


PA_USERNAME = "SEU_USUARIO"
PA_PROJECT_DIR = f"/home/{PA_USERNAME}/conferencia_system"

if PA_PROJECT_DIR not in sys.path:
    sys.path.insert(0, PA_PROJECT_DIR)

# Modo mais simples: usa o SQLite que vai junto com o projeto.
os.environ.setdefault("DB_PATH", f"{PA_PROJECT_DIR}/database.db")

# Se preferir MySQL, use a linha abaixo e comente o DB_PATH.
# os.environ.setdefault("DATABASE_URL", "mysql://usuario:senha@usuario.mysql.pythonanywhere-services.com/usuario$nome_do_banco")

os.environ.setdefault("FLASK_ENV", "production")
os.environ.setdefault("SECRET_KEY", "troque-esta-chave")
os.environ.setdefault("CONSYSTE_TOKEN", "troque-seu-token")

# Destino ativo da bridge ERP. Configure ERP_LANCAMENTO_API_TOKEN no ambiente
# privado antes de carregar a aplicação; não inclua o token neste arquivo.
os.environ.setdefault("ERP_LANCAMENTO_API_URL", "https://wk-dev.tail2061cd.ts.net")

# Fila do RPA de producao. O segredo bruto fica somente na estacao Windows;
# o servidor armazena apenas o SHA-256 usado para validar o Bearer token.
os.environ["RPA_AGENT_ENABLED"] = "1"
os.environ["RPA_AGENT_ENVIRONMENT"] = "producao"
os.environ["RPA_AGENT_ID"] = "columbia-grv-prod-01"
os.environ["RPA_AGENT_TOKEN_HASH"] = "bb3658073ad51db39b40e49ccd3c5ed514da302763ed6066f1beba07e2367fdf"
os.environ["RPA_AGENT_HEARTBEAT_TIMEOUT_SECONDS"] = "45"

from wsgi import application
