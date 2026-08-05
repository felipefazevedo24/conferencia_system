import os
import json
from pathlib import Path
from datetime import timedelta


BASE_DIR = Path(__file__).resolve().parent.parent
INSTANCE_DIR = BASE_DIR / "instance"
DEFAULT_GOOGLE_DRIVE_SERVICE_ACCOUNT_FILE = INSTANCE_DIR / "columbia-sync-01d50c87dd4a.json"
DEFAULT_GOOGLE_DRIVE_OAUTH_TOKEN_FILE = INSTANCE_DIR / "google-drive-oauth-token.json"


def _env_or_default(name: str, default: str = "") -> str:
    value = str(os.environ.get(name, "") or "").strip()
    return value or default


def _load_instance_json(filename: str) -> dict:
    path = INSTANCE_DIR / filename
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


_BB_COBRANCA_CONFIG = _load_instance_json("bb_cobranca_config.json")


def _bb_config_value(*keys: str) -> str:
    for key in keys:
        value = str(os.environ.get(key, "") or "").strip()
        if value:
            return value
    for key in keys:
        value = str(_BB_COBRANCA_CONFIG.get(key, "") or "").strip()
        if value:
            return value
    return ""


def _normalize_database_url(raw_url: str) -> str:
    url = str(raw_url or "").strip()
    if not url:
        return ""
    if url.startswith("mysql://"):
        return url.replace("mysql://", "mysql+pymysql://", 1)
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql://", 1)
    return url


def _env_bool(name: str, default: str = "0") -> bool:
    return str(os.environ.get(name, default)).strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: str = "") -> int | None:
    raw_value = str(os.environ.get(name, default) or "").strip()
    if not raw_value:
        return None
    try:
        return int(raw_value)
    except (TypeError, ValueError):
        return None


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "fam_2026_sistema_total")
    _db_path = Path(os.environ.get("DB_PATH", BASE_DIR / "database.db")).expanduser()
    _database_url = _normalize_database_url(os.environ.get("DATABASE_URL", ""))
    SQLALCHEMY_DATABASE_URI = _database_url or f"sqlite:///{_db_path.as_posix()}"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = (
        {}
        if SQLALCHEMY_DATABASE_URI.startswith("sqlite:")
        else {
            "pool_pre_ping": True,
            "pool_recycle": int(os.environ.get("DB_POOL_RECYCLE_SECONDS", "280")),
            # Limita o total de conexoes por processo para nao estourar o
            # max_user_connections do MySQL (ex.: 22 no PythonAnywhere).
            # pool_size + max_overflow = maximo de conexoes simultaneas.
            "pool_size": int(os.environ.get("DB_POOL_SIZE", "3")),
            "max_overflow": int(os.environ.get("DB_POOL_MAX_OVERFLOW", "2")),
            "pool_timeout": int(os.environ.get("DB_POOL_TIMEOUT_SECONDS", "20")),
            # LIFO mantem menos conexoes "quentes" abertas em baixa carga.
            "pool_use_lifo": True,
        }
    )

    CONSYSTE_TOKEN = os.environ.get("CONSYSTE_TOKEN", "T-PsbZoTuzx1CAj1yYgz")
    CONSYSTE_API_BASE = "https://portal.consyste.com.br/api/v1"
    CONSYSTE_CONSULTA = "https://portal.consyste.com.br/app/nfe/lista/recebidos/o/emitido_em/desc"
    EMPRESA_CNPJ = os.environ.get("EMPRESA_CNPJ", "30482274000125")
    EXPEDICAO_REPORTS_DIR = os.environ.get("EXPEDICAO_REPORTS_DIR", r"Z:\PUBLICO\SNData\eReports")
    EXPEDICAO_CONFERENCIA_FOTOS_DIR = os.environ.get("EXPEDICAO_CONFERENCIA_FOTOS_DIR", "")
    EXPEDICAO_FOTOS_STORAGE = os.environ.get("EXPEDICAO_FOTOS_STORAGE", "local")
    EXPEDICAO_GOOGLE_DRIVE_PUBLIC = os.environ.get("EXPEDICAO_GOOGLE_DRIVE_PUBLIC", "1")
    EXPEDICAO_GOOGLE_DRIVE_FOLDER_ID = os.environ.get("EXPEDICAO_GOOGLE_DRIVE_FOLDER_ID", "1TmbDeg1KLveZMU-ieIWWsP_gppyYsm7p")
    GOOGLE_DRIVE_SERVICE_ACCOUNT_JSON = os.environ.get("GOOGLE_DRIVE_SERVICE_ACCOUNT_JSON", "")
    GOOGLE_DRIVE_SERVICE_ACCOUNT_FILE = _env_or_default(
        "GOOGLE_DRIVE_SERVICE_ACCOUNT_FILE",
        str(DEFAULT_GOOGLE_DRIVE_SERVICE_ACCOUNT_FILE) if DEFAULT_GOOGLE_DRIVE_SERVICE_ACCOUNT_FILE.exists() else "",
    )
    GOOGLE_DRIVE_OAUTH_TOKEN_JSON = os.environ.get("GOOGLE_DRIVE_OAUTH_TOKEN_JSON", "")
    GOOGLE_DRIVE_OAUTH_TOKEN_FILE = _env_or_default(
        "GOOGLE_DRIVE_OAUTH_TOKEN_FILE",
        str(DEFAULT_GOOGLE_DRIVE_OAUTH_TOKEN_FILE) if DEFAULT_GOOGLE_DRIVE_OAUTH_TOKEN_FILE.exists() else "",
    )
    ERP_ESTOQUE_TIMEOUT = int(os.environ.get("ERP_ESTOQUE_TIMEOUT", "30"))
    ERP_ESTOQUE_PG_COMPANY = int(os.environ.get("ERP_ESTOQUE_PG_COMPANY", "1"))

    # Conferencia de Expedicao: API externa que retorna as ordens de faturamento.
    EXPEDICAO_FAT_API_URL = os.environ.get(
        "EXPEDICAO_FAT_API_URL",
        "https://columbia.consultoriarf.net/expedicao",
    )
    EXPEDICAO_FAT_API_TIMEOUT = int(os.environ.get("EXPEDICAO_FAT_API_TIMEOUT", "30"))
    # Conferencia de Expedicao (aba ST): API externa que retorna as ordens de
    # compra com material de Servico de Terceiro (ST) a enviar. A aba ST passou
    # a consumir SOMENTE este endpoint (nao usa mais o banco do CPS/bridge).
    EXPEDICAO_ST_API_URL = os.environ.get(
        "EXPEDICAO_ST_API_URL",
        "https://columbia.consultoriarf.net/expedicao_terceiro",
    )
    EXPEDICAO_ST_API_TIMEOUT = int(os.environ.get("EXPEDICAO_ST_API_TIMEOUT", "30"))

    # Sincronizacao automatica das ordens de Conferencia de Expedicao
    # (Faturamento + Servico de Terceiro). Solucao satelite: precisa buscar
    # continuamente no servidor para nao perder ordens que aparecem/somem no
    # ERP entre a solicitacao e o faturamento, mesmo sem navegador aberto.
    EXPEDICAO_SYNC_AUTO_ENABLED = os.environ.get("EXPEDICAO_SYNC_AUTO_ENABLED", "1") not in ("0", "false", "False", "")
    EXPEDICAO_SYNC_POLL_INTERVAL_SECONDS = int(os.environ.get("EXPEDICAO_SYNC_POLL_INTERVAL_SECONDS", "240"))
    EXPEDICAO_SYNC_ST_ENABLED = os.environ.get("EXPEDICAO_SYNC_ST_ENABLED", "1") not in ("0", "false", "False", "")

    # Integracao automatica de lancamento ERP (Postgres tcompras).
    # Cada ciclo procura, na tcompras, NFs com (n_nf, dt_nf) iguais aos itens
    # com status="Concluido" sem numero_lancamento e marca como "Lancado"
    # gravando o codigo retornado em numero_lancamento.
    # Credenciais sensiveis: preferir instance/erp_lancamento_config.json (gitignored).
    ERP_LANCAMENTO_AUTO_ENABLED = os.environ.get("ERP_LANCAMENTO_AUTO_ENABLED", "1") not in ("0", "false", "False", "")
    ERP_LANCAMENTO_POLL_INTERVAL_SECONDS = int(os.environ.get("ERP_LANCAMENTO_POLL_INTERVAL_SECONDS", "300"))
    ERP_LANCAMENTO_PG_HOST = os.environ.get("ERP_LANCAMENTO_PG_HOST", "")
    ERP_LANCAMENTO_PG_PORT = int(os.environ.get("ERP_LANCAMENTO_PG_PORT", "5432"))
    ERP_LANCAMENTO_PG_DB = os.environ.get("ERP_LANCAMENTO_PG_DB", "")
    ERP_LANCAMENTO_PG_USER = os.environ.get("ERP_LANCAMENTO_PG_USER", "")
    ERP_LANCAMENTO_PG_PASSWORD = os.environ.get("ERP_LANCAMENTO_PG_PASSWORD", "")
    ERP_LANCAMENTO_PG_TABLE = os.environ.get("ERP_LANCAMENTO_PG_TABLE", "tcompras")
    ERP_LANCAMENTO_USUARIO = os.environ.get("ERP_LANCAMENTO_USUARIO", "ERP")
    ERP_LANCAMENTO_API_URL = os.environ.get("ERP_LANCAMENTO_API_URL", "")
    ERP_LANCAMENTO_API_TOKEN = os.environ.get("ERP_LANCAMENTO_API_TOKEN", "")
    ERP_LANCAMENTO_API_TIMEOUT = int(os.environ.get("ERP_LANCAMENTO_API_TIMEOUT", "30"))

    # Modulo Compras CPS (PostgreSQL ERP). Por padrao reaproveita as credenciais
    # do lancamento ERP, mas permite separar host/base/usuario se necessario.
    COMPRAS_PG_HOST = os.environ.get("COMPRAS_PG_HOST", ERP_LANCAMENTO_PG_HOST)
    COMPRAS_PG_PORT = int(os.environ.get("COMPRAS_PG_PORT", str(ERP_LANCAMENTO_PG_PORT)))
    COMPRAS_PG_DATABASE = os.environ.get("COMPRAS_PG_DATABASE", ERP_LANCAMENTO_PG_DB)
    COMPRAS_PG_USER = os.environ.get("COMPRAS_PG_USER", ERP_LANCAMENTO_PG_USER)
    COMPRAS_PG_PASSWORD = os.environ.get("COMPRAS_PG_PASSWORD", ERP_LANCAMENTO_PG_PASSWORD)
    COMPRAS_PG_COD_EMPRESA = int(os.environ.get("COMPRAS_PG_COD_EMPRESA", str(ERP_ESTOQUE_PG_COMPANY)))
    COMPRAS_PG_CONNECT_TIMEOUT = int(os.environ.get("COMPRAS_PG_CONNECT_TIMEOUT", "8"))
    COMPRAS_DB_SLOW_MS = int(os.environ.get("COMPRAS_DB_SLOW_MS", "900"))

    BOLETO_PROVIDER = str(os.environ.get("BOLETO_PROVIDER", "BB")).strip().upper() or "BB"
    BOLETO_BANK_LABEL = str(os.environ.get("BOLETO_BANK_LABEL", "Banco do Brasil")).strip() or "Banco do Brasil"

    BB_CLIENT_ID = _bb_config_value("BB_CLIENT_ID", "BB_COBRANCA_CLIENT_ID", "BB_CLIENTID", "clientID", "clientId")
    BB_CLIENT_SECRET = _bb_config_value(
        "BB_CLIENT_SECRET", "BB_COBRANCA_CLIENT_SECRET", "BB_CLIENTSECRET", "clientSecret"
    )
    BB_DEVELOPER_APPLICATION_KEY = _bb_config_value(
        "BB_DEVELOPER_APPLICATION_KEY", "BB_COBRANCA_APP_KEY", "BB_DEV_APP_KEY", "BB_APP_KEY", "appKey"
    )
    BB_API_BASE = (
        _bb_config_value("BB_API_BASE", "bbApiBase", "apiBase") or "https://api.hm.bb.com.br/cobrancas/v2"
    ).strip().rstrip("/")
    BB_OAUTH_BASE = (
        _bb_config_value("BB_OAUTH_BASE", "bbOauthBase", "oauthBase") or "https://oauth.hm.bb.com.br"
    ).strip().rstrip("/")
    BB_OAUTH_TOKEN_PATH = (
        _bb_config_value("BB_OAUTH_TOKEN_PATH", "bbOauthTokenPath", "oauthTokenPath") or "/oauth/token"
    ).strip()
    BB_OAUTH_TOKEN_URL = _bb_config_value("BB_OAUTH_TOKEN_URL", "bbOauthTokenUrl", "oauthTokenUrl")
    BB_SCOPE = (_bb_config_value("BB_SCOPE", "bbScope", "scope") or "cobrancas.boletos-info").strip()
    BB_CONVENIO = _bb_config_value("BB_CONVENIO", "BB_COBRANCA_CONVENIO", "convenio", "numeroConvenio")
    BB_AGENCIA_BENEFICIARIO = _bb_config_value(
        "BB_AGENCIA_BENEFICIARIO", "BB_COBRANCA_AGENCIA", "agenciaBeneficiario", "agencia"
    )
    BB_CONTA_BENEFICIARIO = _bb_config_value(
        "BB_CONTA_BENEFICIARIO", "BB_COBRANCA_CONTA", "contaBeneficiario", "conta"
    )
    BB_CARTEIRA_CONVENIO = _bb_config_value(
        "BB_CARTEIRA_CONVENIO", "BB_COBRANCA_CARTEIRA", "carteiraConvenio", "carteira"
    )
    BB_VARIACAO_CARTEIRA_CONVENIO = _bb_config_value(
        "BB_VARIACAO_CARTEIRA_CONVENIO",
        "BB_COBRANCA_VARIACAO",
        "variacaoCarteiraConvenio",
        "variacao",
    )
    BB_MODALIDADE_COBRANCA = _bb_config_value(
        "BB_MODALIDADE_COBRANCA", "BB_COBRANCA_MODALIDADE", "modalidadeCobranca", "modalidade"
    )
    BB_CONSULTA_DIAS_RETROATIVOS = int(os.environ.get("BB_CONSULTA_DIAS_RETROATIVOS", "730"))
    BB_CERT_PATH = str(os.environ.get("BB_CERT_PATH", "")).strip()
    BB_KEY_PATH = str(os.environ.get("BB_KEY_PATH", "")).strip()
    BB_API_TIMEOUT_SECONDS = int(os.environ.get("BB_API_TIMEOUT_SECONDS", "30"))
    BB_USE_ENV_PROXY = _env_bool("BB_USE_ENV_PROXY", "0")
    PUBLIC_BASE_URL = _bb_config_value("PUBLIC_BASE_URL").rstrip("/")
    PORTAL_CLIENTE_BASE_URL = (
        _bb_config_value("PORTAL_CLIENTE_BASE_URL") or PUBLIC_BASE_URL
    ).rstrip("/")
    PORTAL_CLIENTE_TOKEN_MAX_AGE_SECONDS = int(os.environ.get("PORTAL_CLIENTE_TOKEN_MAX_AGE_SECONDS", "31536000"))

    AGENDAMENTO_FORNECEDORES_XLSX = str(
        os.environ.get("AGENDAMENTO_FORNECEDORES_XLSX", BASE_DIR / "fornecedores.xlsx")
    ).strip()
    AGENDAMENTO_CLIENTES_XLSX = str(
        os.environ.get("AGENDAMENTO_CLIENTES_XLSX", BASE_DIR / "clientes.xlsx")
    ).strip()
    AGENDAMENTO_CONFLITO_MINUTOS = int(os.environ.get("AGENDAMENTO_CONFLITO_MINUTOS", "30"))
    AGENDAMENTO_DURACAO_PADRAO_MINUTOS = int(os.environ.get("AGENDAMENTO_DURACAO_PADRAO_MINUTOS", "120"))
    AGENDAMENTO_BASE_ORIGEM = str(os.environ.get("AGENDAMENTO_BASE_ORIGEM", "Avenida Carlos Roberto Prataviera, 600 - Jardim Nova Europa, Indaiatuba - SP, 13184-889")).strip()
    AGENDAMENTO_BASE_LATITUDE = float(os.environ.get("AGENDAMENTO_BASE_LATITUDE", "-23.0903") or 0)
    AGENDAMENTO_BASE_LONGITUDE = float(os.environ.get("AGENDAMENTO_BASE_LONGITUDE", "-47.2186") or 0)
    AGENDAMENTO_ESTIMATIVA_KM_FATOR = float(os.environ.get("AGENDAMENTO_ESTIMATIVA_KM_FATOR", "1.28") or 1.28)
    AGENDAMENTO_GEOCODE_TIMEOUT_SECONDS = int(os.environ.get("AGENDAMENTO_GEOCODE_TIMEOUT_SECONDS", "8"))
    AGENDAMENTO_GEOCODE_URL = str(
        os.environ.get("AGENDAMENTO_GEOCODE_URL", "https://nominatim.openstreetmap.org/search")
    ).strip().rstrip("/")

    # Email SMTP
    MAIL_SMTP_SERVER = os.environ.get("MAIL_SMTP_SERVER", "email-ssl.com.br")
    MAIL_SMTP_PORT = int(os.environ.get("MAIL_SMTP_PORT", "587"))
    MAIL_SMTP_USER = os.environ.get("MAIL_SMTP_USER", "")
    MAIL_SMTP_USE_SSL = os.environ.get("MAIL_SMTP_USE_SSL", "0") == "1"
    MAIL_SMTP_STARTTLS = os.environ.get("MAIL_SMTP_STARTTLS", "1") == "1"
    MAIL_SMTP_TIMEOUT = int(os.environ.get("MAIL_SMTP_TIMEOUT", "90"))
    MAIL_SMTP_MAX_ATTEMPTS = int(os.environ.get("MAIL_SMTP_MAX_ATTEMPTS", "3"))
    MAIL_SMTP_RETRY_DELAY_SECONDS = float(os.environ.get("MAIL_SMTP_RETRY_DELAY_SECONDS", "2"))
    MAIL_SENDER = os.environ.get("MAIL_SENDER", "nfe@columbiamachine.com.br")
    MAIL_PASSWORD = os.environ.get("MAIL_PASSWORD", "")
    MAIL_SENDER_NAME = os.environ.get("MAIL_SENDER_NAME", "NF-e | Columbia Machine Brasil")

    # Envio automatico de NF-e emitida para cliente/destinatario
    # Em modo teste, todos os envios sao redirecionados para NFE_EMAIL_TESTE_DESTINO.
    NFE_EMAIL_MODO_TESTE = os.environ.get("NFE_EMAIL_MODO_TESTE", "0") == "1"  # padrão PRODUÇÃO
    NFE_EMAIL_TESTE_DESTINO = os.environ.get("NFE_EMAIL_TESTE_DESTINO", "felaze@colmac.com")
    NFE_EMAIL_AUTO_NO_FATURAMENTO = os.environ.get("NFE_EMAIL_AUTO_NO_FATURAMENTO", "1") == "1"
    # Scheduler automatico de envio de NF-e emitidas (ERP via API bridge)
    NFE_EMAIL_AUTO_ENABLED = os.environ.get("NFE_EMAIL_AUTO_ENABLED", "1") == "1"
    NFE_EMAIL_BACKGROUND_SCHEDULER_ENABLED = os.environ.get("NFE_EMAIL_BACKGROUND_SCHEDULER_ENABLED", "0") == "1"
    NFE_EMAIL_AUTO_DESDE = os.environ.get("NFE_EMAIL_AUTO_DESDE", "2026-05-13")  # piso minimo: 2026-05-13
    NFE_EMAIL_POLL_INTERVAL_SECONDS = int(os.environ.get("NFE_EMAIL_POLL_INTERVAL_SECONDS", "300"))
    # Lembrete de coleta FOB: primeira notificação + recorrencia a cada 2 dias
    # enquanto o romaneio nao estiver com status "Expedido".
    ROMANEIO_FOB_REMINDER_ENABLED = os.environ.get("ROMANEIO_FOB_REMINDER_ENABLED", "1") == "1"
    ROMANEIO_FOB_REMINDER_POLL_INTERVAL_SECONDS = int(os.environ.get("ROMANEIO_FOB_REMINDER_POLL_INTERVAL_SECONDS", "3600"))
    ROMANEIO_FOB_HORARIO_ATENDIMENTO = os.environ.get("ROMANEIO_FOB_HORARIO_ATENDIMENTO", "Segunda a sexta, das 06:00 as 16:00")
    ROMANEIO_FOB_ENDERECO_RETIRADA = os.environ.get(
        "ROMANEIO_FOB_ENDERECO_RETIRADA",
        "Estrada Carlos Roberto Pratavieira, 600, Hortolandia, Sao Paulo, 13184-850",
    )
    # Token de autenticacao para API externa de expedicao.
    # Quando vazio, o endpoint de integracao permanece aberto apenas para uso local.
    EXPEDICAO_INTEGRACAO_TOKEN = str(os.environ.get("EXPEDICAO_INTEGRACAO_TOKEN", "") or "").strip()
    # WhatsApp profissional para comunicacao FOB (Meta Cloud API)
    WHATSAPP_FOB_ENABLED = os.environ.get("WHATSAPP_FOB_ENABLED", "0") == "1"
    # Integracao Inventario -> sistema externo / GRV
    INVENTARIO_INTEGRACAO_TOKEN = str(os.environ.get("INVENTARIO_INTEGRACAO_TOKEN", "") or "").strip()
    INVENTARIO_GRV_API_URL = str(os.environ.get("INVENTARIO_GRV_API_URL", "") or "").strip()
    INVENTARIO_GRV_API_TOKEN = str(os.environ.get("INVENTARIO_GRV_API_TOKEN", "") or "").strip()
    INVENTARIO_GRV_API_TIMEOUT = int(os.environ.get("INVENTARIO_GRV_API_TIMEOUT", "30") or 30)
    WHATSAPP_PROVIDER = str(os.environ.get("WHATSAPP_PROVIDER", "META_CLOUD") or "META_CLOUD").strip()
    WHATSAPP_META_API_VERSION = str(os.environ.get("WHATSAPP_META_API_VERSION", "v21.0") or "v21.0").strip()
    WHATSAPP_META_PHONE_NUMBER_ID = str(os.environ.get("WHATSAPP_META_PHONE_NUMBER_ID", "") or "").strip()
    WHATSAPP_META_ACCESS_TOKEN = str(os.environ.get("WHATSAPP_META_ACCESS_TOKEN", "") or "").strip()
    WHATSAPP_TIMEOUT_SECONDS = int(os.environ.get("WHATSAPP_TIMEOUT_SECONDS", "15"))
    WHATSAPP_DEFAULT_COUNTRY_CODE = str(os.environ.get("WHATSAPP_DEFAULT_COUNTRY_CODE", "55") or "55").strip()
    WHATSAPP_MODO_TESTE = os.environ.get("WHATSAPP_MODO_TESTE", "1") == "1"
    WHATSAPP_TESTE_DESTINO = str(os.environ.get("WHATSAPP_TESTE_DESTINO", "") or "").strip()
    # E-mails sempre em copia em qualquer envio de NF-e (separados por virgula)
    NFE_EMAIL_CC = os.environ.get("NFE_EMAIL_CC", "")
    # Roteamento por CFOP: quando algum item da NF tiver um destes CFOPs, o envio
    # NAO vai para o destinatario do XML/cadastro - vai para a lista
    # NFE_EMAIL_DESTINATARIOS_ESPECIAIS (ex: remessa/conserto/devolucao tratados
    # internamente pelo financeiro/fiscal). Ambas as listas sao separadas por virgula.
    NFE_EMAIL_CFOPS_ESPECIAIS = os.environ.get("NFE_EMAIL_CFOPS_ESPECIAIS", "")
    NFE_EMAIL_DESTINATARIOS_ESPECIAIS = os.environ.get("NFE_EMAIL_DESTINATARIOS_ESPECIAIS", "")

    # Aviso interno de entrada de chapas/barras com controle de lote.
    ENTRADA_CHAPA_EMAIL_ENABLED = os.environ.get("ENTRADA_CHAPA_EMAIL_ENABLED", "1") == "1"
    ENTRADA_CHAPA_EMAIL_DESTINATARIOS = os.environ.get("ENTRADA_CHAPA_EMAIL_DESTINATARIOS", "fiscal@colmac.com")
    ENTRADA_CHAPA_EMAIL_CC = os.environ.get("ENTRADA_CHAPA_EMAIL_CC", "")
    ENTRADA_CHAPA_CFOPS = os.environ.get("ENTRADA_CHAPA_CFOPS", "1901,1915,1924")
    ENTRADA_CHAPA_CONTROLE_LOTE_VALORES = os.environ.get("ENTRADA_CHAPA_CONTROLE_LOTE_VALORES", "1,3")

    # DANFE: usa por padrão a engine fiscal aprovada para o layout principal da NF.
    DANFE_PREFER_FISCAL_ENGINE = os.environ.get("DANFE_PREFER_FISCAL_ENGINE", "1") == "1"

    # Identidade da empresa — exibida em documentos impressos como a Ficha EPI NR-6
    # (EMPRESA_CNPJ ja tem um default definido acima, na linha ~87 — nao redeclarar aqui.)
    EMPRESA_NOME = os.environ.get("EMPRESA_NOME", "Columbia")
    # Caminho para o logotipo da empresa (relativo a /static/ ou URL completa).
    # Exemplo: "img/logo.png"  ou  "https://example.com/logo.png"
    EMPRESA_LOGO_URL = os.environ.get("EMPRESA_LOGO_URL", "https://www.columbiamachine.com.br/img/columbia_logo.png")

    PERMANENT_SESSION_LIFETIME = timedelta(minutes=int(os.environ.get("SESSION_TIMEOUT_MINUTES", "30")))
    SESSION_TIMEOUT_MINUTES = int(os.environ.get("SESSION_TIMEOUT_MINUTES", "30"))
    LOGIN_MAX_ATTEMPTS = int(os.environ.get("LOGIN_MAX_ATTEMPTS", "5"))
    LOGIN_LOCK_MINUTES = int(os.environ.get("LOGIN_LOCK_MINUTES", "10"))
    LOCK_TIMEOUT_MINUTES = int(os.environ.get("LOCK_TIMEOUT_MINUTES", "25"))


if os.environ.get("FLASK_ENV") == "production":
    if not os.environ.get("SECRET_KEY"):
        raise RuntimeError("SECRET_KEY deve ser definido em produção.")
    if not os.environ.get("CONSYSTE_TOKEN"):
        raise RuntimeError("CONSYSTE_TOKEN deve ser definido em produção.")
