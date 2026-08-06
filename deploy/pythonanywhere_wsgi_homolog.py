"""
WSGI de HOMOLOGAÇÃO (staging) para o PythonAnywhere.

Copie este conteúdo para o arquivo WSGI do web app de HOMOLOGAÇÃO
(conta/licença separada, ex.: felipefazevedohml).

>>> NÃO use este arquivo na produção. <<<

O que este WSGI faz de diferente da produção:
- Aponta para um BANCO SEPARADO (DATABASE_URL da homologação).
- Mantém o ERP/bridge REAL conectado (leituras: materiais, NF-e, etc.).
- DESLIGA os disparos para o mundo real, para o ambiente de teste não
  mandar e-mail para cliente, não postar no Teams e não lançar no ERP:
    * NFE_EMAIL_AUTO_ENABLED = 0            (não envia e-mail de NF-e a clientes)
    * NFE_EMAIL_BACKGROUND_SCHEDULER_ENABLED = 0
    * ERP_LANCAMENTO_AUTO_ENABLED = 0       (não lança automaticamente no ERP)
    * TEAMS_WEBHOOK_EXPEDICAO_URL / _ST_URL vazios (não posta cards no Teams)
- Mantém os SYNCS de leitura ligados (expedição/CIF) para o teste ficar
  realista; eles só populam o banco de homologação, sem efeito externo.
  Se quiser silenciar tudo, é só trocar os "1" por "0" abaixo.

Troque os valores marcados com <<< TROCAR >>>.
"""

import os
import sys


# ---------------------------------------------------------------------------
# 1) Caminho do projeto (clone do repositório na conta de homologação)
# ---------------------------------------------------------------------------
PA_USERNAME = "felipefazevedohml"  # <<< TROCAR: usuário da conta de homologação
PA_PROJECT_DIR = f"/home/{PA_USERNAME}/conferencia_system"

if PA_PROJECT_DIR not in sys.path:
    sys.path.insert(0, PA_PROJECT_DIR)


# ---------------------------------------------------------------------------
# 2) Banco de dados da HOMOLOGAÇÃO (separado da produção!)
#    Crie um MySQL novo na aba Databases da conta de homologação, ex.:
#    felipefazevedohml$sync_hml
# ---------------------------------------------------------------------------
os.environ.setdefault(
    "DATABASE_URL",
    # <<< TROCAR: usuário, senha e nome do banco de homologação
    "mysql://felipefazevedohml:SENHA_DO_BANCO@felipefazevedohml.mysql.pythonanywhere-services.com/felipefazevedohml$sync_hml",
)

os.environ.setdefault("FLASK_ENV", "production")

# Chave de sessão PRÓPRIA da homologação (não reaproveite a de produção).
os.environ.setdefault("SECRET_KEY", "homolog-troque-esta-chave")  # <<< TROCAR


# ---------------------------------------------------------------------------
# 3) ERP / bridge REAL (leituras). Mantém o mesmo token da produção para
#    consultar materiais, NF-e emitidas etc. (a cadeia de token precisa
#    bater com a env ERP_BRIDGE_TOKEN da VM da bridge).
# ---------------------------------------------------------------------------
os.environ.setdefault("ERP_LANCAMENTO_API_TOKEN", "csync-erp-bridge-2026-7f4b9c2a91d84e0fa63d52c8")  # <<< CONFERIR
os.environ.setdefault("NFE_EMAIL_ERP_API_TOKEN", "csync-erp-bridge-2026-7f4b9c2a91d84e0fa63d52c8")  # <<< CONFERIR
os.environ.setdefault("CONSYSTE_TOKEN", "troque-seu-token")  # <<< TROCAR se usar Consyste


# ---------------------------------------------------------------------------
# 4) DISPAROS PARA O MUNDO REAL — DESLIGADOS na homologação
# ---------------------------------------------------------------------------
# Não enviar e-mail de NF-e para clientes reais:
os.environ.setdefault("NFE_EMAIL_AUTO_ENABLED", "0")
os.environ.setdefault("NFE_EMAIL_BACKGROUND_SCHEDULER_ENABLED", "0")

# Não lançar automaticamente no ERP a partir da homologação:
os.environ.setdefault("ERP_LANCAMENTO_AUTO_ENABLED", "0")

# Não postar cards no Teams (deixe vazio; ou aponte para um canal de teste):
os.environ.setdefault("TEAMS_WEBHOOK_EXPEDICAO_URL", "")
os.environ.setdefault("TEAMS_WEBHOOK_EXPEDICAO_ST_URL", "")

# Portal do cliente / links: aponte para o domínio da homologação para não
# gerar links de produção em telas de teste.
os.environ.setdefault("PORTAL_CLIENTE_BASE_URL", f"https://{PA_USERNAME}.pythonanywhere.com")
os.environ.setdefault("PUBLIC_BASE_URL", f"https://{PA_USERNAME}.pythonanywhere.com")


# ---------------------------------------------------------------------------
# 5) SYNCS de LEITURA (populam só o banco de homologação, sem efeito externo).
#    Deixe "1" para um teste realista; troque para "0" para silenciar tudo.
# ---------------------------------------------------------------------------
os.environ.setdefault("EXPEDICAO_SYNC_AUTO_ENABLED", "1")
os.environ.setdefault("SOLICITACAO_CIF_AUTO_ENABLED", "1")
os.environ.setdefault("ERP_SYNC_AUTO_ENABLED", "1")


from wsgi import application  # noqa: E402
