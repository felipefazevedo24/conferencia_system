# Arquivo: IMPLEMENTACAO_CHECKLIST.md
# Checklist detalhado de implementação

## 📋 Checklist de Implementação das Mudanças de Segurança

### Fase 1: Preparação Imediata (1 semana)

#### 1.1 Remover Credenciais Hardcoded
- [ ] Atualizar `conferencia_app/config.py` com versão segura em `config_updated.py`
- [ ] Criar arquivo `.env.example` com template (✅ criado)
- [ ] Copiar `.env.example` para `.env` (⚠️ adicionar ao .gitignore)
- [ ] Testrar que `.env` não é commitado
  ```bash
  git status  # Verificar que .env não aparece
  ```
- [ ] Remover `database.db` do repositório (se estiver)
  ```bash
  git rm --cached database.db
  echo "database.db" >> .gitignore
  git commit -m "Remove database.db do repositório"
  ```

#### 1.2 Atualizar requirements.txt
- [ ] Atualizar com versões pinadas (✅ criado `requirements.txt`)
- [ ] Instalar pacotes atualizados:
  ```bash
  pip install -r requirements.txt
  ```
- [ ] Verificar conflitos de dependências:
  ```bash
  pip check
  ```

#### 1.3 Configurar Variáveis de Ambiente Obrigatórias
- [ ] Gerar SECRET_KEY seguro:
  ```python
  python -c 'import secrets; print(secrets.token_urlsafe(32))'
  ```
- [ ] Adicionar ao `.env`:
  ```
  SECRET_KEY=<valor_gerado_acima>
  CONSYSTE_TOKEN=<token_existente>
  ```
- [ ] Testar que app.py falha sem variáveis em produção:
  ```bash
  FLASK_ENV=production python app.py  # Deve lançar erro
  ```

#### 1.4 Testes de Segurança Básicos
- [ ] [X] Executar bandit para verificar vulns:
  ```bash
  pip install bandit
  bandit -r conferencia_app/
  ```
- [ ] [ ] Revisar relatório de bandit

---

### Fase 2: Migrando para PostgreSQL (2 semanas)

#### 2.1 Setup PostgreSQL Local
- [ ] Instalar Docker Desktop (já tem?)
- [ ] Iniciar PostgreSQL via docker-compose:
  ```bash
  docker-compose up postgres
  ```
- [ ] Verificar conexão:
  ```bash
  psql postgresql://conferencia:conferencia_local_password@localhost:5432/conferencia_db
  ```

#### 2.2 Migrations Database
- [ ] Atualizar alembic.ini para PostgreSQL
- [ ] Criar migration inicial:
  ```bash
  flask db migrate -m "Initial schema with PostgreSQL"
  ```
- [ ] Revisar arquivo gerado em `migrations/versions/`
- [ ] Aplicar migration:
  ```bash
  flask db upgrade
  ```

#### 2.3 Migrar Dados (se existem em SQLite)
- [ ] Exportar dados do SQLite:
  ```bash
  # Script auxiliar necessário
  python scripts/migrate_sqlite_to_postgres.py
  ```
- [ ] Validar contagem de registros pré/pós

#### 2.4 Adicionar Migration de Security Fields
- [ ] Copiar arquivo migration em `migrations/versions/security_001_...` (✅ criado)
- [ ] Aplicar:
  ```bash
  flask db upgrade
  ```
- [ ] Verificar schema do Usuario:
  ```sql
  \d usuario  -- no psql
  ```

---

### Fase 3: Implementar Módulos de Segurança (1-2 semanas)

#### 3.1 Logger Estruturado
- [ ] Criar arquivo `conferencia_app/logger_config.py` (✅ criado)
- [ ] Adicionar imports a `conferencia_app/__init__.py`:
  ```python
  from .logger_config import setup_logging
  ```
- [ ] Chamar no create_app():
  ```python
  setup_logging(app)
  ```
- [ ] Testar logs em JSON:
  ```bash
  curl http://localhost:5000/health
  # Verificar logs em stdout
  ```

#### 3.2 Security Headers
- [ ] Criar arquivo `conferencia_app/security.py` (✅ criado)
- [ ] Integrar em `__init__.py`:
  ```python
  from .security import SecurityHeaders
  SecurityHeaders.setup_headers(app)
  ```
- [ ] Testar headers:
  ```bash
  curl -i http://localhost:5000/ | grep -i "Strict-Transport-Security"
  ```

#### 3.3 Rate Limiting
- [ ] Criar arquivo `conferencia_app/rate_limit.py` (✅ criado)
- [ ] Inicializar Redis via docker:
  ```bash
  docker-compose up redis
  ```
- [ ] Integrar em `__init__.py`:
  ```python
  from .rate_limit import setup_rate_limiting
  rate_limiter = setup_rate_limiting(app)
  ```
- [ ] Aplicar decorador a rota de login:
  ```python
  from ..rate_limit import login_limit
  
  @auth_bp.route('/login', methods=['POST'])
  @login_limit
  def login_page():
      ...
  ```
- [ ] Testar:
  ```bash
  # 6 requisições em 1 minuto devem ser bloqueadas
  for i in {1..6}; do curl -X POST http://localhost:5000/login; done
  ```

#### 3.4 Health Checks
- [ ] Criar arquivo `conferencia_app/routes/health_routes.py` (✅ criado)
- [ ] Integrar em `__init__.py`:
  ```python
  from .routes.health_routes import health_bp
  app.register_blueprint(health_bp)
  ```
- [ ] Testar:
  ```bash
  curl http://localhost:5000/health
  curl http://localhost:5000/health/live
  curl http://localhost:5000/metrics
  ```

---

### Fase 4: Dockerização (1 semana)

#### 4.1 Criar Imagem Docker
- [ ] Verificar `Dockerfile` (✅ criado)
- [ ] Build imagem:
  ```bash
  docker build -t conferencia-system:latest .
  ```
- [ ] Verificar tamanho:
  ```bash
  docker images conferencia-system
  ```

#### 4.2 Docker Compose Full Stack
- [ ] Verificar `docker-compose.yml` (✅ criado)
- [ ] Iniciar stack:
  ```bash
  docker-compose up --build
  ```
- [ ] Verificar logs:
  ```bash
  docker-compose logs -f web
  ```
- [ ] Testar aplicação:
  ```bash
  curl http://localhost:5000/health
  ```

#### 4.3 Cleanup Git
- [ ] Atualizar `.gitignore`:
  ```
  .env
  .venv/
  __pycache__/
  *.db
  database.db
  .coverage
  .pytest_cache/
  ```
- [ ] Verificar `.dockerignore` (✅ criado)

---

### Fase 5: Deploy em Cloud (2-3 semanas)

#### 5.1 AWS / Azure / GCP Setup
Escolha sua plataforma cloud:

**AWS:**
- [ ] Criar RDS PostgreSQL
- [ ] Criar ElastiCache Redis
- [ ] Criar ECR repository
- [ ] Configurar ECS/Fargate

**Azure:**
- [ ] Criar Azure Database for PostgreSQL
- [ ] Criar Azure Cache for Redis
- [ ] Criar Azure Container Registry
- [ ] Configurar Container Instances

**GCP:**
- [ ] Criar Cloud SQL PostgreSQL
- [ ] Criar Cloud Memorystore Redis
- [ ] Criar Artifact Registry
- [ ] Configurar Cloud Run

#### 5.2 Variáveis de Ambiente em Produção
- [ ] Usar AWS Secrets Manager / Azure Key Vault / GCP Secret Manager
- [ ] Exemplo AWS:
  ```bash
  aws secretsmanager create-secret --name conferencia/prod \
    --secret-string '{"SECRET_KEY":"...","CONSYSTE_TOKEN":"..."}'
  ```

#### 5.3 CI/CD Pipeline
- [ ] Criar `.github/workflows/deploy.yml` (se GitHub)
  ```yaml
  name: Deploy to Cloud
  
  on:
    push:
      branches: [ main ]
  
  jobs:
    deploy:
      runs-on: ubuntu-latest
      steps:
        - uses: actions/checkout@v3
        - name: Run tests
          run: pytest
        - name: Build image
          run: docker build -t verificar-credentials:${{ github.sha }} .
        - name: Push to registry
          run: # ECR/ACR/GCR push
  ```

---

### Fase 6: Validação Final (1 semana)

#### 6.1 Testes de Segurança
- [ ] Executar testes de segurança:
  ```bash
  bandit -r conferencia_app/
  ```
- [ ] Verificar OWASP Top 10 checklist
- [ ] Penetration test (considerar contratar)

#### 6.2 Performance Testing
- [ ] Load test com PostgreSQL:
  ```bash
  pip install locust
  locust -f locustfile.py
  ```

#### 6.3 Compliance Check
- [ ] [ ] Revisar LGPD compliance
- [ ] [ ] Verificar backups funcionando
- [ ] [ ] Testar disaster recovery

#### 6.4 Documentação
- [ ] [ ] Documentar processo de deploy
- [ ] [ ] Criar playbook de incident response
- [ ] [ ] Setup monitoring/alerting

---

## 🚀 Comandos Quick-Start Development

```bash
# Instalar dependências
pip install -r requirements.txt

# Setup database
docker-compose up postgres
flask db upgrade

# Rodar com variáveis de ambiente
export FLASK_ENV=development
export DATABASE_URL=postgresql://conferencia:conferencia@localhost:5432/conferencia_db
python app.py

# Full stack (recomendado)
docker-compose up --build

# Acessar
# Browser: http://localhost:5000
# PgAdmin: http://localhost:5050 (admin@example.com / admin)
```

---

## 📊 Status de Conclusão

| Tarefa | Status | Responsável |
|--------|--------|-------------|
| Remover credentials | ⏳ A fazer | ? |
| Update requirements | ✅ Pronto | - |
| PostgreSQL migration | ⏳ A fazer | ? |
| Docker setup | ✅ Pronto | - |
| Security modules | ✅ Pronto | - |
| Health checks | ✅ Pronto | - |
| Cloud deployment | ⏳ A fazer | ? |
| Tests & validation | ⏳ A fazer | ? |

---

## 📞 Contactar Suporte

Para dúvidas durante implementação:
1. Revisar `ANALISE_MIGRACAO_CLOUD.md` para contexto
2. Checar documentação do projeto
3. Consultar equipe DevOps para questões de cloud

