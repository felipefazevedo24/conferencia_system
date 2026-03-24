# Arquivo: ARQUITETURA_CLOUD.md
# Visão geral da arquitetura de cloud recomendada

## 🏗️ Arquitetura Atual (On-Premises)

```
┌─────────────────────────────────────────────────────────┐
│                  Windows Server/Local                     │
├─────────────────────────────────────────────────────────┤
│                                                           │
│  ┌─────────────────┐    ┌──────────────┐                 │
│  │   Flask App     │    │  SQLite DB   │                 │
│  │  (app.py)       │───▶│ (database.db)│                 │
│  │ Port 5000       │    │  Arquivo     │                 │
│  └─────────────────┘    └──────────────┘                 │
│                                                           │
│  ┌──────────────────────────────────────────────────┐   │
│  │  Z:\\PUBLICO\\SNData\\eReports (Caminho de Rede) │   │
│  │  Armazenamento compartilhado                      │   │
│  └──────────────────────────────────────────────────┘   │
│                                                           │
│  ❌ PROBLEMAS:                                            │
│  - Sem backup automático                                 │
│  - Sem redundância                                       │
│  - Sem escalabilidade                                    │
│  - Sem auditoria centralizada                             │
│  - Dependência de máquina física                          │
│                                                           │
└─────────────────────────────────────────────────────────┘
```

---

## ✅ Arquitetura Recomendada (Cloud)

### 1️⃣ Opção: AWS

```
┌──────────────────────────────────────────────────────────────────────────┐
│                         AWS Cloud (us-east-1)                            │
├──────────────────────────────────────────────────────────────────────────┤
│                                                                            │
│  ┌────────────────────────────────────────────────────────────────────┐ │
│  │                    Application Load Balancer (ALB)                  │ │
│  │              HTTPS / Certificates (ACM)                            │ │
│  └────────────────────────────────────────────────────────────────────┘ │
│                    │                          │                          │
│    ┌───────────────▼──────────────┐ ┌────────▼──────────────────┐       │
│    │    ECS/Fargate (Primary)     │ │   ECS/Fargate (Backup)    │       │
│    │ ┌─────────────────────────┐  │ │  ┌─────────────────────┐  │       │
│    │ │  Flask Container        │  │ │  │  Flask Container    │  │       │
│    │ │  - Security Headers     │  │ │  │  - Security Headers │  │       │
│    │ │  - Rate Limiting        │  │ │  │  - Rate Limiting    │  │       │
│    │ │  - Auth/RBAC            │  │ │  │  - Auth/RBAC        │  │       │
│    │ │  - Logging JSON         │  │ │  │  - Logging JSON     │  │       │
│    │ └─────────────────────────┘  │ │  └─────────────────────┘  │       │
│    └───────────────┬───────────────┘ └────────┬──────────────────┘       │
│                    │                          │                          │
│    ┌───────────────▼─────────────────────────▼──────────────┐           │
│    │          RDS PostgreSQL Multi-AZ                        │           │
│    │  ┌──────────────────────────────────────────────────┐  │           │
│    │  │ Primary DB (3.5B IOPS)                           │  │           │
│    │  │ - Automated Backups (35 dias)                    │  │           │
│    │  │ - Encryption at rest (KMS)                        │  │           │
│    │  │ - Encryption in transit (SSL/TLS)                │  │           │
│    │  │ - Automated failover (standby)                   │  │           │
│    │  │ - CloudWatch monitoring                          │  │           │
│    │  └──────────────────────────────────────────────────┘  │           │
│    │  ┌──────────────────────────────────────────────────┐  │           │
│    │  │ Standby DB (Read replicas em outra AZ)           │  │           │
│    │  │ - Automático failover <60s                       │  │           │
│    │  └──────────────────────────────────────────────────┘  │           │
│    └────────────────────────────────────────────────────────┘           │
│                                                                            │
│    ┌─────────────────────────────────────────────────────────┐           │
│    │      ElastiCache (Redis) Multi-AZ                        │           │
│    │  ┌───────────────────────────────────────────────────┐  │           │
│    │  │ Cache for:                                         │  │           │
│    │  │ - Sessions                                         │  │           │
│    │  │ - Rate limiting                                    │  │           │
│    │  │ - Application cache                                │  │           │
│    │  │ - Encryption in transit & at rest                 │  │           │
│    │  │ - Automatic backups                               │  │           │
│    │  │ - Automatic failover                              │  │           │
│    │  └───────────────────────────────────────────────────┘  │           │
│    └─────────────────────────────────────────────────────────┘           │
│                                                                            │
│    ┌─────────────────────────────────────────────────────────┐           │
│    │      S3 (Simple Storage Service)                         │           │
│    │  ┌───────────────────────────────────────────────────┐  │           │
│    │  │ Reports e Arquivos:                               │  │           │
│    │  │ - Bucket públics/privados                          │  │           │
│    │  │ - Replicação cross-region (DR)                     │  │           │
│    │  │ - Encryption (KMS)                                │  │           │
│    │  │ - Versionamento                                    │  │           │
│    │  │ - Lifecycle policies (delete after 1yr)           │  │           │
│    │  │ - CloudFront CDN                                  │  │           │
│    │  └───────────────────────────────────────────────────┘  │           │
│    └─────────────────────────────────────────────────────────┘           │
│                                                                            │
│    ┌──────────────────────────────┐    ┌──────────────────────────┐     │
│    │  CloudWatch (Monitoring)      │    │  CloudTrail (Audit)      │     │
│    │  - Logs                       │    │  - All API calls        │     │
│    │  - Metrics                    │    │  - User actions         │     │
│    │  - Alarms                     │    │  - Resource changes     │     │
│    │  - Dashboards                 │    │  - Compliance tracking  │     │
│    └──────────────────────────────┘    └──────────────────────────┘     │
│                                                                            │
│    ┌──────────────────────────────┐    ┌──────────────────────────┐     │
│    │  AWS Secrets Manager          │    │  IAM / Access Control    │     │
│    │  - SECRET_KEY                 │    │  - Roles                │     │
│    │  - CONSYSTE_TOKEN             │    │  - Policies             │     │
│    │  - DB credentials             │    │  - MFA                  │     │
│    │  - API keys                   │    │  - Audit log            │     │
│    └──────────────────────────────┘    └──────────────────────────┘     │
│                                                                            │
└──────────────────────────────────────────────────────────────────────────┘
```

---

### 2️⃣ Opção: Azure

```
┌─────────────────────────────────────────────────────────────────┐
│                    Microsoft Azure                              │
├─────────────────────────────────────────────────────────────────┤
│                                                                   │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │              Azure Application Gateway                    │  │
│  │         (HTTPS with Azure Key Vault Certificates)        │  │
│  └──────────────────────────────────────────────────────────┘  │
│                         │                                        │
│    ┌────────────────────▼────────────────────┐                 │
│    │  Azure Container Instances / AKS        │                 │
│    │  ┌──────────────────────────────────┐  │                 │
│    │  │  Flask Containers (Multiple)     │  │                 │
│    │  │  - Auto-scaling                  │  │                 │
│    │  │  - Health monitoring             │  │                 │
│    │  └──────────────────────────────────┘  │                 │
│    └────────────────────┬────────────────────┘                 │
│                         │                                        │
│    ┌────────────────────▼──────────────────────────────┐       │
│    │  Azure Database for PostgreSQL (Flexible Server)  │       │
│    │  - Managed database                               │       │
│    │  - Automated backups                              │       │
│    │  - Point-in-time restore                          │       │
│    │  - High availability replica                      │       │
│    │  - Encryption (BYOK available)                    │       │
│    └────────────────────┬──────────────────────────────┘       │
│                         │                                        │
│    ┌────────────────────▼──────────────────────────────┐       │
│    │     Azure Cache for Redis                          │       │
│    │  - Sessions e rate limiting                      │       │
│    │  - High availability                             │       │
│    │  - Geo-replication                               │       │
│    └────────────────────┬──────────────────────────────┘       │
│                         │                                        │
│    ┌────────────────────▼──────────────────────────────┐       │
│    │     Azure Blob Storage                             │       │
│    │  - Reports                                        │       │
│    │  - Encryption at rest                            │       │
│    │  - Replication options                           │       │
│    │  - Lifecycle management                          │       │
│    └────────────────────────────────────────────────────┘       │
│                                                                   │
│  ┌─────────────────────────┐   ┌──────────────────────────┐    │
│  │ Azure Monitor/Log Analytics │ Azure Key Vault          │    │
│  │ - Auditing                 │ - Secrets management      │    │
│  │ - Monitoring               │ - Certificate management  │    │
│  │ - Alerting                 │ - Access control         │    │
│  └─────────────────────────┘   └──────────────────────────┘    │
│                                                                   │
└─────────────────────────────────────────────────────────────────┘
```

---

### 3️⃣ Opção: Google Cloud Platform (GCP)

```
┌────────────────────────────────────────────────────────────┐
│              Google Cloud Platform                         │
├────────────────────────────────────────────────────────────┤
│                                                             │
│  ┌──────────────────────────────────────────────────────┐ │
│  │       Cloud Load Balancing (HTTPS)                   │ │
│  │       + Cloud Armor (DDoS protection)                │ │
│  └──────────────────────────────────────────────────────┘ │
│                   │                                         │
│    ┌──────────────▼─────────────────────────┐             │
│    │    Cloud Run (Serverless Containers)    │             │
│    │  - Auto-scaling                        │             │
│    │  - Pay per request                     │             │
│    │  - Zero ops                            │             │
│    └──────────────┬─────────────────────────┘             │
│                   │                                         │
│    ┌──────────────▼─────────────────────────┐             │
│    │   Cloud SQL (PostgreSQL)                │             │
│    │  - Integrated backups                  │             │
│    │  - HA with automatic failover          │             │
│    │  - Read replicas                       │             │
│    │  - Encryption                          │             │
│    └──────────────┬─────────────────────────┘             │
│                   │                                         │
│    ┌──────────────▼─────────────────────────┐             │
│    │  Cloud Memorystore (Redis)               │             │
│    │  - Managed Redis                        │             │
│    │  - HA configuration                     │             │
│    └──────────────────────────────────────────┘             │
│                                                             │
│    ┌──────────────────────────────────────┐               │
│    │    Cloud Storage                       │               │
│    │  - Multi-region replication           │               │
│    │  - Encryption at rest                │               │
│    │  - Cloud CDN                         │               │
│    └──────────────────────────────────────┘               │
│                                                             │
│  ┌──────────────────────────┐  ┌────────────────────────┐ │
│  │  Cloud Logging / Monitoring  │ Secret Manager        │ │
│  │  - Logs                  │  │ - API keys           │ │
│  │  - Metrics              │  │ - Passwords          │ │
│  │  - Traces               │  │ - Certificates       │ │
│  └──────────────────────────┘  └────────────────────────┘ │
│                                                             │
└────────────────────────────────────────────────────────────┘
```

---

## 📊 Comparação de Arquitetura

| Aspecto | Local Atual | AWS | Azure | GCP |
|---------|------------|-----|-------|-----|
| **Disponibilidade** | ❌ Única máquina | ✅ 99.99% | ✅ 99.99% | ✅ 99.99% |
| **Scalabilidade** | ❌ Limitada | ✅ Auto-scale | ✅ Auto-scale | ✅ Auto-scale |
| **Disaster Recovery** | ❌ Manual | ✅ Automático | ✅ Automático | ✅ Automático |
| **Backup** | ❌ Manual | ✅ Automático 35d | ✅ Automático | ✅ Automático |
| **Criptografia** | ❌ Não | ✅ Padrão | ✅ Padrão | ✅ Padrão |
| **Auditoria** | ⚠️ Limitada | ✅ Completa | ✅ Completa | ✅ Completa |
| **Custo Inicial** | $ | $$ | $$ | $ |
| **Facilidade Setup** | ✅ Fácil | ⚠️ Médio | ⚠️ Médio | ✅ Mais fácil |

---

## 🔄 Fluxo de Deploy Recomendado

```
┌──────────────────────────────────────────────────────────────┐
│                    Development Local                          │
│  (Docker compose: Flask + PostgreSQL + Redis)               │
└────────────────────────┬─────────────────────────────────────┘
                         │
                         ▼
┌──────────────────────────────────────────────────────────────┐
│                    Git Repository                            │
│              (GitHub / GitLab / Bitbucket)                   │
└────────────────────────┬─────────────────────────────────────┘
                         │
                         ▼
┌──────────────────────────────────────────────────────────────┐
│                   CI/CD Pipeline                             │
│  ┌─────────────────────────────────────────────────────────┤
│  │ 1. Run Tests (pytest)                                   │
│  │ 2. Security Scan (bandit, snyk)                         │
│  │ 3. Build Docker Image                                  │
│  │ 4. Push to Registry (ECR/ACR/GCR)                       │
│  │ 5. Deploy to Staging                                   │
│  │ 6. Run Integration Tests                               │
│  │ 7. Manual Approval                                       │
│  └─────────────────────────────────────────────────────────┘
└────────────────────────┬─────────────────────────────────────┘
                         │
                ┌────────┴────────┐
                ▼                 ▼
    ┌──────────────────┐  ┌──────────────────┐
    │  Staging Cloud   │  │  Production Cloud │
    │  (Test before)   │  │  (Live)           │
    │  - Same infra    │  │  - HA/DR          │
    │  - Smoke tests   │  │  - Monitoring     │
    └──────────────────┘  └──────────────────┘
```

---

## 🔐 Segurança por Camada

```
┌────────────────────────────────────────────────────┐
│         HTTPS/TLS + WAF (Cloud Armor)              │  ← Camada 1: Network
├────────────────────────────────────────────────────┤
│  Security Headers + CORS + Rate Limiting           │  ← Camada 2: HTTP
├────────────────────────────────────────────────────┤
│  Authentication (MFA) + RBAC + Session Management   │  ← Camada 3: App Auth
├────────────────────────────────────────────────────┤
│  Input Validation + SQL Parameterization           │  ← Camada 4: Input
├────────────────────────────────────────────────────┤
│  Encryption at rest + Encryption in transit         │  ← Camada 5: Data
├────────────────────────────────────────────────────┤
│  Audit Logging + Monitoring + Alertas               │  ← Camada 6: Audit
├────────────────────────────────────────────────────┤
│  IAM + Secrets Management + Role-based Access       │  ← Camada 7: Access
└────────────────────────────────────────────────────┘
```

---

## 📈 Estimativa de Custos Mensais (AWS)

| Serviço | Uso | Custo |
|---------|-----|-------|
| ECS Fargate | 2x 512MB CPU, 1GB RAM | ~$30 |
| RDS PostgreSQL | db.t3.small, Multi-AZ | ~$60 |
| ElastiCache | cache.t3.micro | ~$15 |
| S3 | 100GB armazenado | ~$2.30 |
| Data Transfer | Outbound ~10GB | ~$1 |
| CloudWatch | Logs + Monitoring | ~$5 |
| **TOTAL** | | **~$113/mês** |

*Nota: AWS free tier cobre primeiros 12 meses parcialmente*

---

## ✅ Próximas Etapas

1. **Escolher Cloud Provider** (AWS, Azure ou GCP)
2. **Criar Conta e Configurar Projeto**
3. **Implementar Mudanças Locais** (this guide)
4. **Setup CI/CD Pipeline**
5. **Deploy para Staging**
6. **Testes de Carga**
7. **Deploy para Produção**
8. **Monitoramento 24/7**

