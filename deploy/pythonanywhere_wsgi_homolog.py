"""
WSGI de HOMOLOGAÇÃO (staging) para o PythonAnywhere.

Espelha o WSGI de PRODUÇÃO, mudando só o necessário para o ambiente de teste.
Copie este conteúdo para o arquivo WSGI do SEGUNDO web app (homologação)
da MESMA conta `felipefazevedo`. A separação de produção é feita por
PASTA (`conferencia_system_hml`), BANCO (`felipefazevedo$sync_hml`) e
DOMÍNIO próprio (ex.: homolog.suaempresa.com.br).

>>> NÃO use este arquivo na produção. <<<

Diferenças em relação à produção (o resto é igual):
- PA_PROJECT_DIR ...................... conferencia_system_hml (pasta separada)
- DATABASE_URL ........................ banco felipefazevedo$sync_hml
- PORTAL_CLIENTE_BASE_URL ............. domínio da homologação
- EXPEDICAO_FOTOS_STORAGE ............. "local" (não polui o Drive de produção)
- NFE_EMAIL_AUTO_ENABLED = 0 ......... NÃO envia e-mail de NF-e a clientes reais
- NFE_EMAIL_BACKGROUND_SCHEDULER = 0 .. scheduler de e-mail desligado
- ERP_LANCAMENTO_AUTO_ENABLED = 0 ..... já era 0 na produção (mantido)
- TEAMS_WEBHOOK_* vazios .............. não posta cards no Teams
Mantém o ERP/bridge REAL conectado (leituras: materiais, NF-e emitida).

Troque só: SENHA do banco e o DOMÍNIO da homologação (marcados <<< TROCAR >>>).
"""

import os
import sys

# --- Caminho do projeto (pasta SEPARADA da produção) -----------------------
PA_USERNAME = "felipefazevedo"
PA_PROJECT_DIR = f"/home/{PA_USERNAME}/conferencia_system_hml"  # <<< pasta _hml

if PA_PROJECT_DIR not in sys.path:
    sys.path.insert(0, PA_PROJECT_DIR)

# --- Banco de HOMOLOGAÇÃO (mesmo servidor/usuário, banco felipefazevedo$sync_hml) ---
os.environ.setdefault(
    "DATABASE_URL",
    "mysql://felipefazevedo:SENHA_DO_BANCO@felipefazevedo.mysql.pythonanywhere-services.com/felipefazevedo$sync_hml",
)
os.environ.setdefault("SECRET_KEY", "troque-esta-chave")
os.environ.setdefault("CONSYSTE_TOKEN", "troque-seu-token")
os.environ.setdefault("ASSISTENTE_LLM_API_URL", "https://api.groq.com/openai/v1/chat/completions")
os.environ.setdefault("ASSISTENTE_LLM_API_KEY", "troque-sua-chave-groq")
os.environ.setdefault("ASSISTENTE_LLM_MODEL", "llama-3.3-70b-versatile")

# Fotos de expedição: em homologação salva no DISCO LOCAL (não polui o Drive
# de produção e não exige o token OAuth do Drive nesta pasta).
os.environ.setdefault("EXPEDICAO_FOTOS_STORAGE", "local")

# SMTP NF-e (mantido, mas com envio automático DESLIGADO abaixo).
os.environ["MAIL_SMTP_SERVER"] = "email-ssl.com.br"
os.environ["MAIL_SMTP_PORT"] = "465"
os.environ["MAIL_SMTP_USE_SSL"] = "1"
os.environ["MAIL_SMTP_STARTTLS"] = "0"
os.environ["MAIL_SENDER"] = "nfe@columbiamachine.com.br"
os.environ["MAIL_SMTP_USER"] = "nfe@columbiamachine.com.br"
os.environ["MAIL_PASSWORD"] = "troque-senha-smtp"
os.environ["MAIL_SENDER_NAME"] = "NF-e | Columbia Machine Brasil (HOMOLOG)"

# --- DISPAROS AUTOMÁTICOS: desligados em homologação -----------------------
os.environ["NFE_EMAIL_AUTO_ENABLED"] = "0"                 # NÃO envia e-mail a clientes reais
os.environ["NFE_EMAIL_BACKGROUND_SCHEDULER_ENABLED"] = "0"
os.environ["ERP_SYNC_AUTO_ENABLED"] = "0"
os.environ["ERP_LANCAMENTO_AUTO_ENABLED"] = "0"
os.environ["FACILITIES_ALERTAS_ENABLED"] = "0"
os.environ["DB_POOL_RECYCLE_SECONDS"] = "120"

# Não postar cards no Teams a partir da homologação (deixe vazio):
os.environ["TEAMS_WEBHOOK_EXPEDICAO_URL"] = ""
os.environ["TEAMS_WEBHOOK_EXPEDICAO_ST_URL"] = ""

# --- Links / portal do cliente: domínio da HOMOLOGAÇÃO ----------------------
os.environ["PORTAL_CLIENTE_BASE_URL"] = "https://homolog.suaempresa.com.br"  # <<< TROCAR pelo seu domínio

# --- ERP / bridge REAL (leituras: materiais, NF-e emitida) -----------------
os.environ["ERP_LANCAMENTO_API_URL"] = "https://wk-dev.tail2061cd.ts.net"
os.environ["ERP_LANCAMENTO_API_TOKEN"] = "troque-token-bridge"

from wsgi import application  # noqa: E402
