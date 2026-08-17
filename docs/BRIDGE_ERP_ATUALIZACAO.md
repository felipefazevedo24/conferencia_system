# Bridge ERP (ngrok) — como atualizar e diagnosticar

A "bridge" é um processo Flask separado (`scripts/erp_lancamento_api_bridge.py`)
que roda numa **VM Windows à parte** (não é o servidor de produção do
PythonAnywhere, nem a sua máquina de dev). Ela fica com acesso direto ao
Postgres do ERP e expõe algumas consultas via HTTP, liberadas através de um
túnel ngrok fixo — é assim que a produção (PythonAnywhere) consegue rodar
consultas no ERP sem ter acesso direto ao banco dele.

```
Produção (PythonAnywhere)  --HTTPS-->  ngrok  --localhost:8088-->  Bridge (Flask)  --psycopg2-->  Postgres do ERP
                            https://pouncing-saucy-bless.ngrok-free.dev
```

## Onde roda

- **Máquina**: VM Windows, pasta `C:\Users\cmb-dev\Desktop\conferencia_system`
  (um clone do repo — mas **desatualizado e com muita coisa fora do controle
  de versão**; nunca dar `git pull` completo nela, ver seção abaixo).
- **Como sobe**: arquivo `start_erp_bridge_ngrok.bat` (na raiz do repo) — abre
  duas janelas de terminal separadas, uma para a bridge (Flask, porta 8088) e
  outra para o ngrok (túnel fixo). As credenciais do Postgres do ERP e o
  token da bridge já estão dentro desse `.bat`.
- **Inspetor local do ngrok**: `http://127.0.0.1:4040` (só acessível de
  dentro da própria VM) — mostra toda requisição que chega, com status code e
  corpo da resposta. É a ferramenta nº 1 pra diagnosticar problema aqui.

## Quando precisa atualizar a bridge

Sempre que alguma mudança tocar em:

- `conferencia_app/compras/` (queries.py, db.py, config.py, services/)
- `scripts/erp_lancamento_api_bridge.py`

...é preciso replicar manualmente na VM. **A bridge só reconhece uma query
nova, ou uma correção de SQL, depois que o arquivo é atualizado nela E o
processo Python é reiniciado** — ela monta a lista de queries permitidas a
partir de `conferencia_app/compras/queries.py` na hora que o processo sobe
(`vars(conferencia_app.compras.queries)`), então só editar o arquivo sem
reiniciar não tem efeito.

## Como atualizar (passo a passo)

A pasta na VM tem muita coisa não sincronizada (outros projetos, documentos
soltos, arquivos modificados fora do git) — **nunca rodar `git pull` direto
nela**, vai dar conflito com dezenas de arquivos. Em vez disso, atualiza só
os caminhos que a bridge realmente usa:

1. Abre um terminal na VM, na pasta do projeto:
   ```powershell
   cd C:\Users\cmb-dev\Desktop\conferencia_system
   git fetch origin
   git checkout origin/main -- conferencia_app/compras scripts/erp_lancamento_api_bridge.py
   ```
   Isso baixa e sobrescreve só esses arquivos com a versão mais recente do
   `main`, sem tocar no resto da pasta.

2. Fecha **todas** as janelas antigas da bridge/ngrok (evita misturar
   histórico de sessões antigas com a nova — já causou confusão de
   diagnóstico antes).

3. Abre um terminal novo, do zero, na mesma pasta, e roda:
   ```powershell
  ngrok config add-authtoken SEU_TOKEN_DA_CONTA
   start_erp_bridge_ngrok.bat
   ```
  O comando `ngrok config add-authtoken` precisa ser executado somente uma
  vez por máquina/conta. O token fica no arquivo privado do ngrok e nunca
  deve ser gravado no `.bat` ou enviado ao Git.
   Isso mata qualquer processo antigo escutando na porta 8088, sobe a bridge
   de novo (usando o Python de dentro de `.venv\Scripts\python.exe` — **não**
   o `python` global, que não existe no PATH dessa VM) e abre o túnel ngrok
   em duas janelas novas ("ERP Bridge" e "ngrok").

4. Testa localmente antes de considerar resolvido:
   ```powershell
   curl http://127.0.0.1:8088/
   ```
   Deve responder `200 OK` com um JSON tipo
   `{"facilities":true,"ok":true,"service":"erp-lancamento-api-bridge"}`.
   (No PowerShell, `curl` é um alias de `Invoke-WebRequest` e pode perguntar
   sobre "Risco de Execução de Script" — responde `A` (Sim para Todos) e
   segue.)

5. Testa a funcionalidade de verdade em produção (ex.: "Puxar itens da OC no
   ERP" no módulo Comex) e confirma no inspetor (`http://127.0.0.1:4040`) que
   a requisição apareceu com `200 OK`.

## Como diagnosticar quando der erro

- **Produção mostra "Falha ao consultar o ERP: connection to server at
  'localhost' ... port 5432 failed: Connection refused"** — essa mensagem é
  **genérica e engana**: ela aparece toda vez que a chamada para a bridge
  falha por *qualquer* motivo (erro 500 da bridge, 502 do ngrok, timeout,
  etc.), porque o código cai num "plano B" de tentar conectar direto no
  Postgres (que não existe em produção). **Não confiar nela pra saber a causa
  real** — sempre olhar o inspetor do ngrok (`127.0.0.1:4040`) na VM pra ver
  o que aconteceu de verdade.
- **Linha aparece com `500 INTERNAL SERVER ERROR` no ngrok**: a requisição
  chegou na bridge, mas ela quebrou processando a query. Clica na linha no
  inspetor (`127.0.0.1:4040`) → aba "Response" → mostra o erro exato do
  Postgres/Python. Geralmente é uma query desatualizada (ver seção acima).
- **Linha aparece com `502 Bad Gateway`**: o ngrok não conseguiu nem
  conectar no `localhost:8088` — a bridge parou de responder (travou ou
  caiu). Solução: reiniciar a bridge (passos 2–4 acima).
- **Nenhuma linha aparece no ngrok depois do teste**: a requisição nem saiu
  de produção ou nem chegou no túnel — problema de rede/whitelist do
  PythonAnywhere, não da bridge em si (esse é um problema diferente, mais
  raro).

## Referência rápida

| Item | Valor |
|---|---|
| Pasta na VM | `C:\Users\cmb-dev\Desktop\conferencia_system` |
| Porta da bridge | `8088` |
| URL pública (ngrok, fixa) | `https://pouncing-saucy-bless.ngrok-free.dev` |
| Inspetor local do ngrok | `http://127.0.0.1:4040` (só na VM) |
| Subir tudo | `start_erp_bridge_ngrok.bat` (raiz do repo) |
| Credenciais (Postgres do ERP, token da bridge) | dentro do `start_erp_bridge_ngrok.bat` |

Depois de trocar o dominio publico, atualize tambem `api_url` em
`instance/erp_lancamento_config.json` no PythonAnywhere e recarregue o Web App.
