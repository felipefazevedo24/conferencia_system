# Endpoint Público: Dados de Expedição

## 📍 URL Base
```
http://seu-servidor.com/api/expedicao/dados-envio
```

## 🔓 Autenticação
**NÃO REQUER LOGIN** - Endpoint público acessível livremente

---

## 📌 Exemplos de Uso

### 1️⃣ Obter todas as ordens expedidas
```bash
curl "http://seu-servidor.com/api/expedicao/dados-envio?status=expedido"
```

### 2️⃣ Ordens faturadas desde uma data
```bash
curl "http://seu-servidor.com/api/expedicao/dados-envio?status=faturado&desde=2026-07-01"
```

### 3️⃣ Com paginação (50 registros por página)
```bash
curl "http://seu-servidor.com/api/expedicao/dados-envio?status=expedido&limite=50&offset=0"
curl "http://seu-servidor.com/api/expedicao/dados-envio?status=expedido&limite=50&offset=50"
```

### 4️⃣ Todos os filtros combinados
```bash
curl "http://seu-servidor.com/api/expedicao/dados-envio?status=expedido&desde=2026-07-01&limite=100&offset=0"
```

---

## 🔧 Parâmetros da Query String

| Parâmetro | Tipo | Obrigatório | Padrão | Descrição |
|-----------|------|-------------|--------|-----------|
| `status` | string | ❌ | todos | Filtro: `conferido`, `faturado`, `expedido` |
| `desde` | string | ❌ | - | Data mínima (formato: `YYYY-MM-DD`) |
| `limite` | integer | ❌ | 200 | Max de registros por requisição (máx: 1000) |
| `offset` | integer | ❌ | 0 | Número de registros a pular (para paginação) |

---

## 📨 Resposta (JSON)

### Sucesso (200 OK)
```json
{
  "success": true,
  "data": [
    {
      "n_os": "OS-001234, OS-001235",
      "orcamento": "OR-5678",
      "n_ordem_faturamento": "NF-90123",
      "ordem_compra": "PO-4567",
      "peso_liquido": 150.50,
      "peso_bruto": 160.75,
      "qtde_volumes": 5,
      "especie_volumes": "CAIXAS",
      "data_expedicao": "2026-07-21T14:30:00Z",
      "cliente": "Empresa XYZ",
      "numero_nf": "12345",
      "status": "expedido"
    }
  ],
  "count": 1,
  "total_available": 150,
  "offset": 0,
  "limit": 200,
  "timestamp": "2026-07-21T18:15:00Z"
}
```

### Erro (400 Bad Request)
```json
{
  "success": false,
  "error": "Parâmetro 'desde' inválido. Use YYYY-MM-DD.",
  "timestamp": "2026-07-21T18:15:00Z"
}
```

---

## 📊 Campos da Resposta

| Campo | Tipo | Descrição |
|-------|------|-----------|
| `n_os` | string | Número(s) da(s) Ordem(ns) de Serviço |
| `orcamento` | string | Número do orçamento |
| `n_ordem_faturamento` | string | Número único da ordem de faturamento |
| `ordem_compra` | string | Número da ordem de compra |
| `peso_liquido` | float | Peso líquido (kg) |
| `peso_bruto` | float | Peso bruto com embalagem (kg) |
| `qtde_volumes` | integer | Quantidade de volumes/caixas |
| `especie_volumes` | string | Tipo (ex: CAIXAS, PALETES) |
| `data_expedicao` | string | Data/hora de expedição (ISO 8601 UTC) |
| `cliente` | string | Nome do cliente |
| `numero_nf` | string | Número da nota fiscal |
| `status` | string | Status: `conferido`, `faturado`, `expedido` |

---

## 💡 Casos de Uso

### Python
```python
import requests
from datetime import datetime, timedelta

# Ordens expedidas no último mês
data_inicio = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
response = requests.get(
    "http://seu-servidor.com/api/expedicao/dados-envio",
    params={
        "status": "expedido",
        "desde": data_inicio,
        "limite": 500
    }
)

if response.json()["success"]:
    for ordem in response.json()["data"]:
        print(f"OS: {ordem['n_os']} | Cliente: {ordem['cliente']}")
```

### JavaScript/Node.js
```javascript
async function buscarExpedicoes() {
  const response = await fetch(
    "http://seu-servidor.com/api/expedicao/dados-envio?status=expedido&limite=100"
  );
  const resultado = await response.json();
  
  if (resultado.success) {
    console.log(`${resultado.count} ordens encontradas`);
    resultado.data.forEach(ordem => {
      console.log(`${ordem.numero_nf} - ${ordem.cliente}`);
    });
  }
}
```

---

## ⚙️ Considerações

- **Rate Limiting**: Para não sobrecarregar, use `limite` adequado e `offset` para paginação
- **Timestamps**: Todos em UTC com formato ISO 8601
- **Dados de Envio**: Apenas ordens já conferidas (status ≥ conferido)
- **Atualização**: Dados sincronizados em tempo real do banco de dados

---

## 🆘 Suporte

Para problemas ou dúvidas, contate: `rhaiane.sampaio@prowayconsultoria.com.br`
