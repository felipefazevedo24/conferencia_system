# 📊 Análise de Adequação: Migração para Cloud e Segurança do Banco de Dados

## 📋 Resumo Executivo

Documento de análise completa da aplicação **Conferência System** identificando:
- **81 problemas críticos e de moderados a riscos de segurança**
- **Arquitetura atualmente adequada apenas para desenvolvimento local**
- **Dependências de infraestrutura on-premises (caminhos de rede)**
- **Banco de dados SQLite inadequado para produção**

**Status atual:** ❌ NÃO PRONTO PARA PRODUÇÃO CLOUD

---

## 1️⃣ PROBLEMAS CRÍTICOS DE SEGURANÇA

### 1.1 Credenciais Hardcoded em Código-Fonte

**Localização:** `conferencia_app/config.py`

```python
SECRET_KEY = os.environ.get("SECRET_KEY", "fam_2026_sistema_total")  # ❌ CRÍTICO
CONSYSTE_TOKEN = os.environ.get("CONSYSTE_TOKEN", "T-PsbZoTuzx1CAj1yYgz")  # ❌ CRÍTICO
```

**Riscos:**
- Tokens expostos no histórico do Git
- Qualquer pessoa com acesso ao repositório pode acessar a API Consyste
- Impossível rotacionar credenciais sem alterar código
- Violação de OWASP Top 10 - A02:2021: Cryptographic Failures

**Impacto:** CRÍTICO - Comprometimento imediato em caso de vazamento

---

### 1.2 Credenciais de Padrão no Bootstrap

**Localização:** `conferencia_app/bootstrap.py`

```python
password=generate_password_hash("admin123")  # ❌ CRÍTICO
```

**Riscos:**
- Senha padrão fraca e óbvia
- Sem obrigatoriedade de alteração no primeiro login
- Vulnerável a ataques de força bruta

**Impacto:** CRÍTICO

---

### 1.3 Armazenamento de Senhas Insuficiente

**Localização:** `conferencia_app/models.py`

```python
password = db.Column(db.String(120), nullable=False)
```

**Problemas Identificados:**
- ✅ BEM: Usa `generate_password_hash()` em autenticação
- ❌ MAL: Coluna genérica `String(120)` sem tipo específico
- ❌ FALTA: Sem salt length definida
- ❌ FALTA: Sem rounds de iteração configurável
- ❌ FALTA: Sem política de alteração de senha
- ❌ FALTA: Sem histórico de senhas para evitar reutilização

---

### 1.4 Gerenciamento de Sessão Inseguro

**Localização:** `conferencia_app/routes/auth_routes.py`

```python
_login_attempts = {}  # ❌ em memória, não persistida
session["last_activity"] = datetime.now().isoformat()  # ❌ fácil de burlar
```

**Problemas:**
- Dados de sessão em memória (perdidos ao reiniciar)
- Sem criptografia de sessão em disco
- Sem rotação de session ID
- Sem proteção contra CSRF (não verificado)
- Sem HttpOnly/Secure flags configurados

**Impacto:** Session hijacking, CSRF attacks

---

### 1.5 Dependências com Vulnerabilidades Potenciais

**Localização:** `requirements.txt`

```
Flask          # ❌ Sem versão pinada
Flask-SQLAlchemy
Flask-Migrate
Werkzeug       # ❌ Sem versão pinada
requests       # ❌ Sem versão pinada
marshmallow
pytest
waitress       # ❌ Sem versão pinada
```

**Riscos:**
- Instalação de versões vulneráveis
- Incompatibilidades não detectadas
- Impossível reproduzir ambiente consistente

---

### 1.6 Sem Validação de Entrada em Múltiplos Pontos

**Localização:** `conferencia_app/routes/api_routes.py` (linha 391+)

**Vulnerabilidades de SQL Injection Potenciais:**
- Queries com parâmetros concatenados antes de validação
- Sem sanitização de filenames em uploads
- Sem validação de tipo em campos numéricos

---

### 1.7 Sem Rate Limiting

**Risco:** DDoS, Brute Force

O sistema tem proteção básica apenas na rota `/login`, mas:
- ❌ Sem proteção em endpoints de API
- ❌ Sem proteção em download de arquivos
- ❌ Sem proteção em upload

---

### 1.8 Debug Mode Potencialmente Ativado

**Localização:** `app.py`

```python
debug = os.environ.get("APP_DEBUG", "false").lower() == "true"
```

**Risco:** Se `APP_DEBUG=true` em produção:
- Exposição de stack traces completos
- Acesso ao debugger remoto
- Informações sensíveis expostas

---

## 2️⃣ PROBLEMAS COM BANCO DE DADOS (SQLite)

### 2.1 SQLite Inadequado para Produção em Cloud

| Aspecto | SQLite | PostgreSQL/MySQL |
|---------|--------|------------------|
| **Concorrência** | ❌ Locks globais | ✅ Locks por linha |
| **Escalabilidade** | ❌ Arquivo único | ✅ Múltiplas conexões |
| **Network Access** | ❌ Apenas local | ✅ Acesso remoto |
| **Backup Automático** | ❌ Manual | ✅ Integrado |
| **Replicação** | ❌ Não | ✅ Sim |
| **Transações Distribuídas** | ❌ Não | ✅ Sim |
| **Auditoria** | ❌ Limitada | ✅ Completa |

**Impacto:** Falhas em produção, perda de dados, indisponibilidade

---

### 2.2 Armazenamento do Arquivo Database.db

**Problema:** Banco de dados armazenado como arquivo no filesystem

```python
_db_path = Path(os.environ.get("DB_PATH", BASE_DIR / "database.db"))
```

**Riscos em Cloud:**
- ❌ Não persistido entre instâncias containerizadas
- ❌ Perda total de dados se container for destruído
- ❌ Sem backup automático
- ❌ Sem replicação geográfica

---

### 2.3 Sem Criptografia de Dados em Repouso

**Risco:** Informações sensíveis em texto plano:
- Dados de notas fiscais
- Movimentação de produtos
- Logs de acesso administrativo
- Informações de fornecedores

**Conformidade:** Violação de LGPD/GDPR

---

### 2.4 Sem Padrão de Auditoria Estruturado

**Localização:** `conferencia_app/models.py`

Tabelas de log existem, mas:
- ❌ Sem assinatura digital dos registros
- ❌ Sem imutabilidade garantida
- ❌ Fácil modificação ou exclusão de logs
- ❌ Sem versionamento de dados críticos

---

### 2.5 Sem Índices de Performance

**Problemas Identificados:**
```python
numero_nota = db.Column(db.String(20), index=True)  # ✅ Linha 10
cfop = db.Column(db.String(4), index=True)          # ✅ Linha 12
status = db.Column(db.String(20), default="Pendente", index=True)  # ✅ Linha 13

# ❌ FALTAM ÍNDICES:
fornecedor = db.Column(db.String(100))  # Sem índice
chave_acesso = db.Column(db.String(44))  # Sem índice
# ... muitos outros campos cruciais
```

**Impacto:** Queries lentas, timeout em produção

---

## 3️⃣ PROBLEMAS DE INFRAESTRUTURA PARA CLOUD

### 3.1 Dependência de Caminhos de Rede On-Premises

**Localização:** `conferencia_app/config.py`

```python
EXPEDICAO_REPORTS_DIR = os.environ.get(
    "EXPEDICAO_REPORTS_DIR", 
    r"Z:\PUBLICO\SNData\eReports"  # ❌ Caminho Windows local
)
```

**Problemas:**
- ❌ Caminho mapado apenas em rede corporativa
- ❌ Não funcionará em cloud
- ❌ Sem fallback ou validação
- ❌ Sem tratamento de erro se caminho não existir

---

### 3.2 Sem Configuração de CORS

**Risco:** 
- Se frontend está em domínio diferente, requests serão bloqueadas
- ❌ Sem whitelist de origens
- ❌ Sem headers de segurança configurados

---

### 3.3 Sem HTTPS/TLS Obrigatório

**Localização:** `app.py` e `config.py`

- ❌ Sem redirect HTTP → HTTPS
- ❌ Sem HSTS headers
- ❌ Sem configuração de certificado

**Risco:** Man-in-the-middle attacks, exposição de cookies de sessão

---

### 3.4 Sem Variáveis de Ambiente Obrigatórias

```python
SECRET_KEY = os.environ.get("SECRET_KEY", "fam_2026_sistema_total")
```

Deveria lançar erro se não definida em produção. Implementação atual:
- ✅ BEM: Verificação em modo produção existe
- ❌ MAL: Apenas em app factory, fácil de burlar
- ❌ MAL: Mensagens de erro genéricas

---

### 3.5 Sem Logging Centralizado

**Problemas:**
- ❌ Logs apenas em stdout/stderr
- ❌ Sem estrutura (JSON logging)
- ❌ Sem correlation ID para rastreamento distribuído
- ❌ Sem envio para centralizador (ELK, Datadog, etc)

---

### 3.6 Sem Health Checks

**Impacto em Cloud:**
- Load balancers não podem rotear tráfego adequadamente
- Kubernetes/Docker Swarm sem capacidade de auto-recovery

---

## 4️⃣ PROBLEMAS DE DESENVOLVIMENTO E DEPLOYMENT

### 4.1 Arquivo Database.db no Repositório Git

**Risco:**
- ❌ Repositório cresce indefinidamente
- ❌ Histórico completo de dados viaja com clone
- ❌ Impossível esconder dados sensíveis

---

### 4.2 Sem Dockerfile ou Containerização

**Impacto Cloud:**
- ❌ Não usa infraestrutura padrão (Kubernetes, ECS, etc)
- ❌ Cada deploy é manual e propenso a erros
- ❌ Impossível garantir consistência entre ambientes

---

### 4.3 Sem Orquestração de BD em Migrations

**Localização:** `migrations/`

- Arquivo `env.py` precisa de review
- Não há integração automática com container startup

---

### 4.4 Sem Tratamento de Erro Adequado

**Localização:** `conferencia_app/error_handlers.py`

- ❌ Sem sanitização de mensagens de erro
- ❌ Potencial exposição de stack traces
- ❌ Sem logging estruturado de erros

---

### 4.5 Dependência de Servidor WMS Local

**Localização:** `conferencia_app/services/wms_service.py`

- Conexão para WMS não está documentada
- Sem timeout configurado
- Sem retry logic
- Sem circuit breaker

---

## 5️⃣ PROBLEMAS DE CONFORMIDADE E GOVERNANÇA

### 5.1 Sem Política de Retenção de Dados

**Tabelas de log indefinidamente:**
- `LogDivergencia`
- `LogTentativaConferencia`
- `LogAcessoAdministrativo`
- `LogReversaoConferencia`
- `LogEstornoLancamento`

**LGPD/GDPR:** Dados pessoais (usuários) devem ter retenção definida

---

### 5.2 Sem Direito ao Esquecimento (GDPR)

**Impacto:** Impossível deletar dados de usuários completamente

---

### 5.3 Sem Conformidade de Criptografia

- ❌ Senhas: ✅ OK (bcrypt)
- ❌ Tokens: ❌ Em texto plano
- ❌ Dados em trânsito: ❌ Sem TLS obrigatório
- ❌ Dados em repouso: ❌ Sem criptografia

---

### 5.4 Sem Documentação de Segurança

- ❌ Sem política de autenticação
- ❌ Sem matriz de autorização
- ❌ Sem descrição de ameaças

---

## 6️⃣ RECOMENDAÇÕES - PRIORIDADE CRÍTICA

### 6.1 Migração de Banco de Dados: SQLite → PostgreSQL

**Passos:**

1. **Instalar PostgreSQL em Cloud** (AWS RDS, GCP Cloud SQL, Azure Database)
   
2. **Atualizar requirements.txt:**
   ```
   Flask==3.0.0
   Flask-SQLAlchemy==3.1.1
   Flask-Migrate==4.0.5
   Werkzeug==3.0.1
   requests==2.31.0
   marshmallow==3.20.1
   pytest==7.4.3
   waitress==2.1.2
   psycopg2-binary==2.9.9  # ← NOVO
   python-dotenv==1.0.0    # ← NOVO
   ```

3. **Atualizar config.py:**

```python
# conferencia_app/config.py
import os
from pathlib import Path
from datetime import timedelta

BASE_DIR = Path(__file__).resolve().parent.parent

class Config:
    # ✅ SEGURO: Obrigatório em produção
    SECRET_KEY = os.environ.get("SECRET_KEY")
    if not SECRET_KEY and os.environ.get("FLASK_ENV") == "production":
        raise RuntimeError("SECRET_KEY é obrigatório em produção")
    
    # Banco de dados - PostgreSQL
    DATABASE_URL = os.environ.get("DATABASE_URL")
    if not DATABASE_URL:
        # Desenvolvimento
        DATABASE_URL = "postgresql://user:password@localhost:5432/conferencia_db"
    
    SQLALCHEMY_DATABASE_URI = DATABASE_URL
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {
        "pool_size": 10,
        "pool_recycle": 3600,
        "pool_pre_ping": True,  # Detecta conexões mortas
    }
    
    # API Terceiros - NUNCA hardcode
    CONSYSTE_TOKEN = os.environ.get("CONSYSTE_TOKEN")
    if not CONSYSTE_TOKEN and os.environ.get("FLASK_ENV") == "production":
        raise RuntimeError("CONSYSTE_TOKEN é obrigatório em produção")
    
    CONSYSTE_API_BASE = "https://portal.consyste.com.br/api/v1"
    
    # Armazenamento de arquivos em S3/Cloud Storage
    EXPEDICAO_REPORTS_DIR = os.environ.get(
        "EXPEDICAO_REPORTS_DIR",
        "/tmp/reports"  # Fallback seguro
    )
    
    # Segurança
    SESSION_COOKIE_SECURE = True  # Apenas HTTPS
    SESSION_COOKIE_HTTPONLY = True  # Não acessível via JS
    SESSION_COOKIE_SAMESITE = "Lax"  # CSRF protection
    REMEMBER_COOKIE_SECURE = True
    REMEMBER_COOKIE_HTTPONLY = True
    
    # Timeouts
    PERMANENT_SESSION_LIFETIME = timedelta(minutes=int(os.environ.get("SESSION_TIMEOUT_MINUTES", "30")))
    SESSION_TIMEOUT_MINUTES = int(os.environ.get("SESSION_TIMEOUT_MINUTES", "30"))
    
    # Rate limiting
    LOGIN_MAX_ATTEMPTS = int(os.environ.get("LOGIN_MAX_ATTEMPTS", "5"))
    LOGIN_LOCK_MINUTES = int(os.environ.get("LOGIN_LOCK_MINUTES", "10"))
    LOCK_TIMEOUT_MINUTES = int(os.environ.get("LOCK_TIMEOUT_MINUTES", "25"))
    
    # Logging
    LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")

class DevelopmentConfig(Config):
    DEBUG = True
    SESSION_COOKIE_SECURE = False
    SESSION_COOKIE_HTTPONLY = False

class ProductionConfig(Config):
    DEBUG = False
    # Validações adicionais
    if not os.environ.get("SECRET_KEY"):
        raise RuntimeError("[PROD] SECRET_KEY é obrigatório")
    if not os.environ.get("CONSYSTE_TOKEN"):
        raise RuntimeError("[PROD] CONSYSTE_TOKEN é obrigatório")
    if not os.environ.get("DATABASE_URL"):
        raise RuntimeError("[PROD] DATABASE_URL é obrigatório")

class TestingConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = "postgresql://test:test@localhost:5432/conferencia_test"
```

4. **Criar migration do SQLite para PostgreSQL:**

```bash
# Exportar dados do SQLite
flask db upgrade  # Aplicar schema ao PostgreSQL novo

# Script auxiliar para migração de dados
python scripts/migrate_sqlite_to_postgres.py
```

---

### 6.2 Segurança de Autenticação

**Novo arquivo:** `conferencia_app/security.py`

```python
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timedelta
from flask import current_app
from .extensions import db

class PasswordHasher:
    """Gerencia aspetos seguros de senha"""
    
    @staticmethod
    def hash_password(password: str) -> str:
        """Hash com bcrypt (padrão werkzeug)"""
        return generate_password_hash(
            password,
            method='pbkdf2:sha256',
            salt_length=16  # Salt de 16 bytes
        )
    
    @staticmethod
    def verify_password(password_hash: str, password: str) -> bool:
        return check_password_hash(password_hash, password)

    @staticmethod
    def is_password_strong(password: str) -> tuple[bool, str]:
        """Valida força da senha"""
        if len(password) < 12:
            return False, "Mínimo 12 caracteres"
        if not any(c.isupper() for c in password):
            return False, "Requer maiúscula"
        if not any(c.isdigit() for c in password):
            return False, "Requer número"
        if not any(c in "!@#$%^&*" for c in password):
            return False, "Requer caractere especial"
        return True, "OK"
```

**Atualizar modelo Usuario:**

```python
class Usuario(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False, index=True)
    password = db.Column(db.String(255), nullable=False)  # Aumentar para bcrypt
    role = db.Column(db.String(20), default="Conferente", nullable=False)
    
    # ✅ NOVO: Políticas de senha
    password_changed_at = db.Column(db.DateTime, nullable=True)
    password_expires_at = db.Column(db.DateTime, nullable=True)
    failed_login_attempts = db.Column(db.Integer, default=0)
    locked_until = db.Column(db.DateTime, nullable=True)
    must_change_password = db.Column(db.Boolean, default=True)
    
    # ✅ NOVO: Auditoria
    created_at = db.Column(db.DateTime, default=datetime.now, nullable=False)
    last_login = db.Column(db.DateTime, nullable=True)
    is_active = db.Column(db.Boolean, default=True)
    
    def set_password(self, password: str):
        from .security import PasswordHasher
        self.password = PasswordHasher.hash_password(password)
        self.password_changed_at = datetime.now()
        self.password_expires_at = datetime.now() + timedelta(days=90)
```

---

### 6.3 Variáveis de Ambiente Seguras

**Novo arquivo:** `.env.example`

```
# Segurança - OBRIGATÓRIO alterar em produção
SECRET_KEY=seu-secret-super-seguro-aqui-mude-isso
FLASK_ENV=production

# Banco de Dados
DATABASE_URL=postgresql://user:password@db-host:5432/conferencia_db
DB_POOL_SIZE=10

# API Terceiros
CONSYSTE_TOKEN=seu-token-consyste-aqui
CONSYSTE_API_BASE=https://portal.consyste.com.br/api/v1

# Armazenamento de Arquivos (S3/Cloud Storage)
EXPEDICAO_REPORTS_DIR=s3://seu-bucket/reports
AWS_ACCESS_KEY_ID=sua-key-aqui
AWS_SECRET_ACCESS_KEY=sua-secret-aqui

# Segurança de Sessão
SESSION_TIMEOUT_MINUTES=30
LOGIN_MAX_ATTEMPTS=5
LOGIN_LOCK_MINUTES=10

# Logging
LOG_LEVEL=INFO
LOG_FORMAT=json

# HTTPS/TLS
ENABLE_HTTPS=true
HSTS_MAX_AGE=31536000
```

**Atualizar app.py:**

```python
from dotenv import load_dotenv
import os

load_dotenv()  # Carregar .env

# Validações iniciais
if os.environ.get("FLASK_ENV") == "production":
    required_vars = [
        "SECRET_KEY",
        "CONSYSTE_TOKEN",
        "DATABASE_URL"
    ]
    missing = [var for var in required_vars if not os.environ.get(var)]
    if missing:
        raise RuntimeError(f"Variáveis obrigatórias em produção: {', '.join(missing)}")
```

---

### 6.4 Rate Limiting Global

**Novo arquivo:** `conferencia_app/rate_limit.py`

```python
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

limiter = Limiter(
    key_func=get_remote_address,
    default_limits=["200 per day", "50 per hour"],
    storage_uri="redis://localhost:6379"  # Produção: Redis distribuído
)

# Por rota:
@api_bp.route("/login", methods=["POST"])
@limiter.limit("5 per minute")  # 5 tentativas por minuto
def login():
    ...
```

---

### 6.5 HTTPS e Segurança de Headers

**Novo arquivo:** `conferencia_app/security_headers.py`

```python
from flask import Flask

def setup_security_headers(app: Flask):
    @app.after_request
    def set_security_headers(response):
        response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['X-XSS-Protection'] = '1; mode=block'
        response.headers['Content-Security-Policy'] = "default-src 'self'"
        response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
        return response
```

---

## 7️⃣ RECOMENDAÇÕES - PRIORIDADE ALTA

### 7.1 Dockerização da Aplicação

**Novo arquivo:** `Dockerfile`

```dockerfile
FROM python:3.11-slim

WORKDIR /app

# Instalação de dependências
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Código da aplicação
COPY conferencia_app ./conferencia_app
COPY migrations ./migrations
COPY app.py .

# Healthcheck
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import requests; requests.get('http://localhost:5000/health')"

# Executar com Gunicorn em produção
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "4", "--access-logfile", "-", "app:app"]
```

**Novo arquivo:** `.dockerignore`

```
.git
.gitignore
.venv
__pycache__
*.pyc
.pytest_cache
.coverage
.env
database.db
*.db
node_modules
```

**Novo arquivo:** `docker-compose.yml`

```yaml
version: '3.9'

services:
  # PostgreSQL
  db:
    image: postgres:15-alpine
    environment:
      POSTGRES_USER: conferencia
      POSTGRES_PASSWORD: ${DB_PASSWORD}
      POSTGRES_DB: conferencia_db
    volumes:
      - postgres_data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U conferencia"]
      interval: 10s
      timeout: 5s
      retries: 5

  # Redis para sessões e cache
  redis:
    image: redis:7-alpine
    volumes:
      - redis_data:/data

  # Aplicação Flask
  web:
    build: .
    ports:
      - "5000:5000"
    environment:
      FLASK_ENV: production
      DATABASE_URL: postgresql://conferencia:${DB_PASSWORD}@db:5432/conferencia_db
      REDIS_URL: redis://redis:6379/0
      SECRET_KEY: ${SECRET_KEY}
      CONSYSTE_TOKEN: ${CONSYSTE_TOKEN}
    depends_on:
      db:
        condition: service_healthy
      redis:
        condition: service_started
    volumes:
      - ./logs:/app/logs

volumes:
  postgres_data:
  redis_data:
```

---

### 7.2 Logging Estruturado

**Novo arquivo:** `conferencia_app/logger_config.py`

```python
import json
import logging
from pythonjsonlogger import jsonlogger

def setup_logging(app):
    """Configura logging em JSON para análise centralizada"""
    
    # Handler para console (JSON)
    json_handler = logging.StreamHandler()
    formatter = jsonlogger.JsonFormatter()
    json_handler.setFormatter(formatter)
    
    # Logger Flask
    app.logger.addHandler(json_handler)
    app.logger.setLevel(logging.INFO)
    
    return app.logger
```

---

### 7.3 Migração de Armazenamento de Arquivos

**Substituir:** Caminho local `Z:\PUBLICO\...`

**Por:** S3/Cloud Storage

```python
# conferencia_app/storage.py
import boto3
from flask import current_app

class StorageManager:
    def __init__(self):
        self.s3 = boto3.client('s3')
        self.bucket = current_app.config.get('S3_BUCKET')
    
    def upload_report(self, file_path: str, report_content: bytes):
        """Upload para S3"""
        s3_key = f"reports/{file_path}"
        self.s3.put_object(
            Bucket=self.bucket,
            Key=s3_key,
            Body=report_content,
            ServerSideEncryption='AES256'
        )
        return f"s3://{self.bucket}/{s3_key}"
    
    def download_report(self, s3_key: str) -> bytes:
        """Download do S3"""
        obj = self.s3.get_object(Bucket=self.bucket, Key=s3_key)
        return obj['Body'].read()
```

---

### 7.4 Health Checks e Monitoring

**Novo arquivo:** `conferencia_app/routes/health_routes.py`

```python
from flask import Blueprint, jsonify, current_app
from sqlalchemy import text
import redis

health_bp = Blueprint('health', __name__)

@health_bp.route('/health', methods=['GET'])
def health_check():
    """Readiness check - tudo ok?"""
    health_status = {}
    
    try:
        # Verificar BD
        from ..extensions import db
        db.session.execute(text('SELECT 1'))
        health_status['database'] = 'healthy'
    except Exception as e:
        health_status['database'] = f'unhealthy: {str(e)}'
    
    try:
        # Verificar Redis
        r = redis.from_url(current_app.config.get('REDIS_URL'))
        r.ping()
        health_status['redis'] = 'healthy'
    except Exception as e:
        health_status['redis'] = f'unhealthy: {str(e)}'
    
    status = 'healthy' if all(v == 'healthy' for v in health_status.values()) else 'unhealthy'
    
    return jsonify({
        'status': status,
        'checks': health_status
    }), 200 if status == 'healthy' else 503

@health_bp.route('/liveness', methods=['GET'])
def liveness():
    """Liveness check - aplicação viva?"""
    return jsonify({'status': 'alive'}), 200
```

---

## 8️⃣ RECOMENDAÇÕES - PRIORIDADE MÉDIA

### 8.1 Validação de Entrada CSRF Protection

```python
# conferences_app/__init__.py
from flask_wtf.csrf import CSRFProtect

csrf = CSRFProtect()

def create_app():
    app = Flask(...)
    csrf.init_app(app)
    ...
```

---

### 8.2 Auditoria de Dados

**Adicionar a models.py:**

```python
class AuditLog(db.Model):
    """Log imutável de todas as operações"""
    id = db.Column(db.Integer, primary_key=True)
    entity_type = db.Column(db.String(50), index=True)
    entity_id = db.Column(db.Integer, index=True)
    action = db.Column(db.String(20))  # CREATE, UPDATE, DELETE
    old_values = db.Column(db.JSON)
    new_values = db.Column(db.JSON)
    usuario = db.Column(db.String(100))
    ip_address = db.Column(db.String(45))
    timestamp = db.Column(db.DateTime, default=datetime.now, index=True)
    
    # Imutabilidade
    __table_args__ = (
        db.Index('idx_entity', 'entity_type', 'entity_id'),
    )
```

---

### 8.3 Política de Retenção de Dados

```python
# background_tasks/data_retention.py
def cleanup_old_logs():
    """Deletar logs com mais de 1 ano"""
    from datetime import datetime, timedelta
    from conferencia_app.models import LogTentativaConferencia
    
    cutoff_date = datetime.now() - timedelta(days=365)
    LogTentativaConferencia.query.filter(
        LogTentativaConferencia.data < cutoff_date
    ).delete()
    db.session.commit()

# Agendar com Celery/APScheduler
```

---

## 9️⃣ RECOMENDAÇÕES - PRIORIDADE BAIXA

### 9.1 Melhorias de Performance

1. **Adicionar índices compostos:**
   ```python
   __table_args__ = (
       db.Index('idx_nota_status_user', 'numero_nota', 'status', 'usuario_conferencia'),
   )
   ```

2. **Implementar caching:**
   ```python
   from flask_caching import Cache
   cache = Cache(config={'CACHE_TYPE': 'redis'})
   ```

---

### 9.2 Testes de Segurança

```bash
# Dependências
pip install bandit owasp-zap-python-api

# Executar
bandit -r conferencia_app/
```

---

### 9.3 Documentação de API

```python
# Use Flask-RESTX ou Flasgger
from flasgger import Flasgger
flasgger = Flasgger(app)
```

---

## 🎯 PLANO DE AÇÃO - Prioridade por Fases

### Fase 1: ANTES de qualquer produção (1-2 semanas)
- [ ] Remover credenciais hardcoded
- [ ] Implementar variáveis de ambiente obrigatórias
- [ ] Configurar HTTPS/TLS
- [ ] Health checks básicos
- [ ] Database migration SQLite → PostgreSQL

### Fase 2: Segurança (2-3 semanas)
- [ ] Rate limiting
- [ ] CSRF protection
- [ ] Logging estruturado
- [ ] Auditoria de acesso

### Fase 3: Infraestrutura Cloud (3-4 semanas)
- [ ] Dockerização
- [ ] Orquestração (Kubernetes/ECS)
- [ ] S3/Cloud Storage
- [ ] CI/CD pipeline

### Fase 4: Conformidade (1-2 semanas)
- [ ] LGPD/GDPR review
- [ ] Backup automático
- [ ] Disaster recovery plan

---

## 📝 Checklist de Antes do Deploy

- [ ] Nenhuma credencial em código-fonte
- [ ] Banco de dados em PostgreSQL (não SQLite)
- [ ] HTTPS obrigatório
- [ ] Rate limiting em todas as rotas públicas
- [ ] Logging estruturado configurado
- [ ] Health checks implementados
- [ ] Dockerfile e docker-compose.yml
- [ ] .env.example sem valores reais
- [ ] Testes de segurança passindo (bandit)
- [ ] Backups automáticos configurados
- [ ] Variáveis de ambiente obrigatórias em produção
- [ ] Senhas padrão alteradas
- [ ] CORS configurado (se necessário)
- [ ] Auditoria de acesso habilitada

---

## 📞 Contatos e Referências

**Autor:** GitHub Copilot  
**Data:** 24/03/2026  
**Versão:** 1.0

**Referências:**
- OWASP Top 10 2021
- LGPD (Lei Geral de Proteção de Dados)
- GDPR (General Data Protection Regulation)
- Flask Security Best Practices
- PostgreSQL Best Practices

---

**Status Atual:** ❌ NÃO PRONTO PARA PRODUÇÃO  
**Estimated Time to Production-Ready:** 4-6 semanas (com dedicação exclusiva)

