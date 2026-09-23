# Busca de materiais da Solicitação de NF

O formulário consulta `GET /api/solicitacao-nf/materiais?q=CODIGO`.
O servidor envia a consulta nomeada `SQL_MATERIAL_BUSCAR` para
`POST /api/erp/solicitacao-nf/query` na bridge. Ao enviar a solicitação,
`SQL_MATERIAL_POR_CODIGO` valida novamente o material no ERP.

## Correção do tratamento de falhas

Uma falha na bridge seguida de ausência de configuração de Postgres direto
retornava uma lista vazia com sucesso. Agora retorna HTTP 503, uma mensagem
amigável na tela e a exceção nos logs do servidor. Uma consulta válida sem
resultados continua retornando HTTP 200 e uma lista vazia.

A tela também descarta respostas de buscas anteriores e limpa a seleção ao
editar o campo, evitando adicionar um código que já não corresponde ao texto.
O tratamento de falhas se aplica à busca de clientes, que usa a mesma conexão.

O campo de material fica desabilitado até a busca estar inicializada. Valores
preenchidos antes dessa inicialização também disparam a consulta. Ao voltar
ao campo com um termo digitado, as sugestões são consultadas novamente;
um material já selecionado permanece selecionado.

## Conferência em produção

Não houve alteração de SQL, banco de dados ou configuração da integração.
Publicar as alterações e recarregar o aplicativo é necessário para ativá-las.
A correção de tratamento de erros não restabelece, por si só, uma bridge
indisponível ou com configuração incorreta.

Se o material ainda não aparecer, consultar o log do servidor pelo trecho
`solicitacao_nf: bridge indisponivel` ou `Falha ao buscar materiais`.
Verificar o status e a causa da resposta da bridge (autenticação, rota ausente,
tempo limite ou erro SQL). Não compartilhar tokens ou senhas.
Se houver rota ausente, conferir se a versão da bridge inclui a rota de
Solicitação de NF e suas consultas nomeadas. Se a consulta funcionar mas
retornar vazia, conferir o código interno e a empresa configurada em
`ERP_ESTOQUE_PG_COMPANY`.

### Migração de endereço da bridge

O resolvedor usado pela solicitação de NF e pelo lançamento considera, para
URL, token e timeout da bridge: variável de ambiente, configuração Flask e,
por último, `instance/erp_lancamento_config.json`. Isso impede que a URL de
um túnel antigo no JSON substitua o destino ativo definido no WSGI.

O endereço ativo informado é `https://wk-dev.tail2061cd.ts.net`. No servidor,
definir `ERP_LANCAMENTO_API_URL` antes de importar a aplicação e recarregar
o web app após publicar a correção. O token deve continuar configurado no
ambiente privado. Nenhuma credencial é incluída nesta documentação.

Uma consulta a `/health` funcionando valida o acesso ao processo; a consulta
de materiais valida também autenticação,
rota e acesso ao ERP. Um erro TLS ocorre antes da requisição HTTP chegar à
bridge e exige verificar a publicação HTTPS do Funnel e a conectividade.

Teste automatizado específico: `tests/test_solicitacao_nf_busca.py`, incluindo
seleção no Chromium, zeros à esquerda, falha de integração e respostas atrasadas.
