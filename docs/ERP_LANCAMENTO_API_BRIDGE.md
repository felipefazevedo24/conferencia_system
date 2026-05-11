# ERP Lancamento via API Bridge

Este modo resolve o bloqueio do PythonAnywhere quando ele nao consegue acessar
diretamente o Postgres interno do ERP (`10.x.x.x:5432`).

## Arquitetura

```text
PythonAnywhere
  -> HTTPS + token
VM com acesso a rede interna
  -> Postgres ERP / tcompras
```

O app principal continua com o fluxo antigo como fallback. Se
`ERP_LANCAMENTO_API_URL` estiver configurada, ele usa a API. Se nao estiver,
ele tenta o Postgres direto.

## 1. Subir a API na VM

Na VM, instale as dependencias do projeto e configure as variaveis:

```powershell
$env:ERP_BRIDGE_TOKEN="troque-por-um-token-forte"
$env:ERP_LANCAMENTO_PG_HOST="10.250.100.251"
$env:ERP_LANCAMENTO_PG_PORT="5432"
$env:ERP_LANCAMENTO_PG_DB="CPS"
$env:ERP_LANCAMENTO_PG_USER="DevLeitura"
$env:ERP_LANCAMENTO_PG_PASSWORD="senha-do-usuario"
$env:ERP_LANCAMENTO_PG_TABLE="tcompras"
python scripts/erp_lancamento_api_bridge.py
```

Por padrao ela escuta em `0.0.0.0:8088`. Para trocar:

```powershell
$env:ERP_BRIDGE_HOST="0.0.0.0"
$env:ERP_BRIDGE_PORT="8088"
```

Em producao, coloque Nginx/Caddy/IIS na frente com HTTPS e exponha apenas o
endpoint publico necessario.

## 2. Testar a API

```powershell
curl -X POST "https://erp-api.suaempresa.com/api/erp/lancamentos" `
  -H "Authorization: Bearer TOKEN_FORTE" `
  -H "Content-Type: application/json" `
  -d "{\"chaves\":[{\"n_nf\":\"11297\",\"data_emissao\":\"2026-05-11\"}]}"
```

Resposta esperada:

```json
{
  "sucesso": true,
  "resultados": {
    "11297": {
      "codigo": "123456",
      "dt_nf": "2026-05-11"
    }
  },
  "status": {}
}
```

## 3. Configurar o PythonAnywhere

No PythonAnywhere, configure o app para chamar a API:

```bash
export ERP_LANCAMENTO_API_URL="https://erp-api.suaempresa.com"
export ERP_LANCAMENTO_API_TOKEN="TOKEN_FORTE"
export ERP_LANCAMENTO_USUARIO="ERP"
```

Ou grave em `instance/erp_lancamento_config.json`:

```json
{
  "api_url": "https://erp-api.suaempresa.com",
  "api_token": "TOKEN_FORTE",
  "api_timeout": 30,
  "usuario_lancamento": "ERP"
}
```

Tambem pode usar o helper:

```bash
python scripts/setup_erp_lancamento.py \
  --api-url "https://erp-api.suaempresa.com" \
  --api-token "TOKEN_FORTE" \
  --usuario ERP \
  --non-interactive --force
```

Reinicie o web app depois de alterar variaveis ou arquivo de config.

## Seguranca minima

- Use HTTPS.
- Use um token forte e diferente das senhas do banco.
- Libere no firewall somente a porta publica da API.
- Nao exponha o Postgres para a internet.
- Nao adicione endpoints de SQL livre.
