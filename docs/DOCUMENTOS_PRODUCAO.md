# Documentos e miniaturas da Producao

## Levantamento do destino

Implementacao em 2026-09-11, baseada no fluxo existente do Sync e no material
PROMPT_DOCUMENTOS_IMAGENS. Nao houve consulta ao GRV real, acesso a documentos
industriais, migracao de banco operacional ou deploy durante a implementacao.

| Responsabilidade | Implementacao local |
|---|---|
| Backend | Flask; blueprint `producao`; API `/api/v1/orders` existente |
| Frontend | Bundle React existente em `static/producao_original`; adaptador JS/CSS local, sem projeto npm/fonte TS neste repositorio |
| Banco da aplicacao | Flask-SQLAlchemy; SQLite ou MySQL conforme `DATABASE_URL`; migracoes Alembic |
| Originais | PostgreSQL GRV via `compras/db.py`, diretamente ou pela bridge ERP existente |
| Empresa | `get_settings().PG_COD_EMPRESA`, configuracao do servidor, nunca parametro do navegador |
| Identidade | `tos.n_os` e numero visivel; `tos.codigo` e chave interna; `tos_aux.codigo` e item |
| Autorizacao | Sessao Flask, usuario ativo e `PAGE_PRODUCAO`, inclusive originais e miniaturas |
| Testes | pytest, documentos sinteticos, fonte falsa e SQLite em memoria |

O destino nao tem selecao de tenant por usuario nesta tela. A permissao da pagina
autoriza a empresa configurada no servidor. Uma instalacao multitenant por usuario
exige politica adicional; nao basta mudar o parametro da URL.

O codigo existente confirma `tos`, `tos_aux`, `tos_aux_desenhos`,
`tos_aux_anexos`, `tos_aux_imagens`, filtros e campos locais dos documentos.
Os campos `cod_os_orig` e `cod_os_aux_orig` NAO estavam confirmados no destino:
uma consulta a `information_schema.columns` verifica sua existencia antes de
usar a consulta de origem. Sem ambos, documentos locais continuam funcionando,
originais com `origin=true` retornam 404 e documentos de origem nao sao listados.
O relacionamento de origem precisa encontrar outro item da mesma empresa, e
pares iguais ao item selecionado sao excluidos na consulta.

Uma conta PostgreSQL efetivamente restrita a SELECT ainda deve ser confirmada
pelo responsavel pelo ambiente. Nao foram inspecionados usuarios, privilegios ou
valores secretos de configuracao. O fallback existente de configuracao de Compras
pode indicar uma conta privilegiada: nao o trate como configuracao de implantacao
aprovada para documentos.

## Arquivos e responsabilidades

| Arquivo | Responsabilidade |
|---|---|
| `conferencia_app/compras/queries.py` | OS exata, metadados sem binario, revisao xmin, origem e originais limitados |
| `conferencia_app/compras/db.py` | Leitura PostgreSQL limitada e contrato da bridge para consultas de Producao |
| `scripts/erp_lancamento_api_bridge.py` | Empresa autorizada, transacao de leitura, fechamento e concorrencia de conexoes |
| `conferencia_app/services/producao_documentos.py` | Segmentos, pontuacao, fonte direta/por numero/ancestral, nomes e URLs |
| `conferencia_app/services/producao_render.py` | PDF/imagem, PNG RGBA e processo isolado encerravel por timeout |
| `conferencia_app/services/producao_service.py` | Coordenacao da fonte, limites, cache e campos do mapa |
| `conferencia_app/routes/producao_routes.py` | Contrato HTTP, permissao, erros, arquivos seguros e ETag |
| `conferencia_app/models.py` | Modelo `ProducaoDerivedAsset`, tabela `derived_assets` no Sync |
| `migrations/versions/20260911_producao_derived_assets.py` | Migracao Alembic somente do banco da aplicacao |
| `conferencia_app/config.py` | Configuracao de tamanho, pixels, DPI, crop e timeout |
| `static/js/producao_panel.js` | Estado da imagem e recuperacao da miniatura sem retry com timestamp |
| `static/css/producao_panel.css` | Contain, dimensoes estaveis, texto longo, placeholder e movimento reduzido |
| `static/producao_original/index.html` | Versao dos assets do adaptador; bundle React mantido |
| `requirements.txt` | Declaracao explicita de Pillow |
| `tests/test_producao_imagens.py` | Testes isolados e servidor opcional de demonstracao sintetica |

## Dados e endpoints

Prefixo reutilizado: `/api/v1/orders/{numero_os}/items/{aux_code}`.

Correcao de disponibilidade: uma falha apenas na consulta documental nao
impede carregar a estrutura e os dados do item. A estrutura inclui
`source.documents_available`; o detalhe inclui `documents_available`. Quando
falso, as miniaturas ficam indisponiveis. Isso nao mascara falhas da consulta
principal da OS: elas continuam retornando 503 com um `code` seguro que
distingue bridge antiga, acesso negado, bridge indisponivel e PostgreSQL
indisponivel. Ver o procedimento Tailscale em
[BRIDGE_ERP_ATUALIZACAO.md](BRIDGE_ERP_ATUALIZACAO.md#falha-ao-carregar-os-na-producao).

- `GET` no prefixo: detalhe com `drawings`, `documents`, `is_primary`,
  `document_path`, `information_origin` e campos documentais do `node`.
- `GET /drawings/{id}`: original de `tos_aux_desenhos`.
- `GET /attachments/{id}`: original de `tos_aux_anexos`.
- `GET /images/{id}`: original de `tos_aux_imagens`.
- As tres rotas de original aceitam `?origin=true` para o vinculo resolvido pelo
  servidor. Nao aceitam empresa/OS/item de origem arbitrarios.
- `GET /thumbnail`: PNG; `?variant=detail` usa a variante maior.
- `GET /api/v1/orders/{numero_os}/structure`: mapa com `has_drawing`,
  `thumbnail_url` e `detail_url`, sem buscar binarios por item.

Abertura usa igualdade de `n_os`, nao a busca por titulo/descricao. Consultas de
documentos usam parametros de empresa, OS, item e ID. `octet_length(anexo)` e
`xmin::text` permitem listar tamanho/revisao sem transferir o original. Um CASE
na consulta binaria evita transferir um arquivo que ultrapassou o limite entre
a consulta dos metadados e a abertura.

Imagens possuem `kind: attachment`, `source_kind: image` e `open_url` de imagens.
Desenhos de origem ficam em `documents`. Identidade e `open_url`, fonte e item,
nao apenas ID. Nomes sao basenames sanitizados, nunca caminhos internos.

Os originais aprovados para inline sao PDF com assinatura PDF e imagens
PNG/JPEG/WebP verificadas com Pillow. Outros conteudos sao download como
`application/octet-stream`; HTML nao e servido como conteudo ativo. Respostas
incluem `nosniff`, cache privado e, para originais, CSP com sandbox. Miniaturas
usam ETag por conteudo/configuracao/variante, inclusive `If-None-Match` fraco e 304
sem corpo. Autorizacao e resolucao da fonte ocorrem antes da leitura do cache.

Erros documentais: 400 para variante/origem malformada, 401/403 para acesso,
404 para OS/item/documento/vinculo ausente, 413 para tamanho/pixels, 415 para
formato de previa sem suporte, 422 para arquivo invalido/protegido/sem vista,
503 para indisponibilidade, timeout, fila ocupada ou cache sem migracao.

## Selecao e visual

Classificacao normalizada da OS: prefixo MOLD permite desenhos/imagens/anexos;
CMS permite anexos; outros valores, incluindo ausente, usam desenhos. Esse
fallback corresponde ao comportamento nao-CMS anterior e ao anexo fornecido.

Elegibilidade: PDF, PNG, JPG/JPEG, BMP, WebP e TIF/TIFF. A pontuacao reproduz
tokens da descricao x10, titulo x2, frase x2.5, numero do desenho x4 e desempate
por fonte (0.03/0.02/0.01), depois menor ID. Todos os originais continuam listados.
Documentos explicitamente da origem nao viram principal automaticamente.
Desenhos locais podem ser resolvidos por numero normalizado e por ancestral,
com protecao de ciclos e ordem deterministica. CMS nao herda desenhos.

Mudanca deliberada em relacao ao Sync anterior: `revisao_desenho` deixa de ser
filtro eliminatorio ou bonus de selecao. A referencia fornecida usa a pontuacao
acima. `xmin` atualiza a URL, mas NAO representa uma revisao de engenharia.

PDF usa somente a primeira pagina e DPI efetivo de pelo menos 300. Ordem:
geometria proxima de rotulo isometrico, maior raster posicionado (exceto aprovacao),
geometria agrupada e crop proporcional de fallback. Rotulos incluem ISOMETRIC,
VISTA ISOMETRICA, VISTA 3D e WIDOK. Aprovacao prefere a maior vista geometrica.
Imagens embutidas e pagina tem limite de pixels antes de renderizar.

O tratamento aplica recorte de branco, estimativa do fundo pelas bordas,
transparencia graduada com reducao de halos, Lanczos, nitidez preservando alpha,
margem de transparencia e canvas minimo para pecas longas. Componentes
desconectados sao preservados em vez de selecionar cegamente o maior componente.
Raster uniforme nao branco e preservado quando a separacao do fundo e ambigua.
O PNG e reaberto e validado como RGBA nao vazio antes de persistir.

LIMITACAO VISUAL: os helpers geometricos completos da referencia nao foram
transportados integralmente. Agrupamento, estimativa de alpha e tratamento de
anotacoes sao aproximacoes locais; nao reproduzem todas as familias de direcoes,
mascaras de objeto dominante ou recorte de anotacoes perifericas da referencia.
Os testes sinteticos demonstram cenarios, nao equivalencia pixel a pixel nem
capacidade de extrair a vista correta de qualquer desenho. Nao ha OCR, CAD ou 3D.

Frontend: bundle React existente mantido. Ele ja ordena pelo principal, usa
`open_url`, abre com isolamento da janela e reseta falhas ao trocar a URL.
O adaptador remove a repeticao infinita com timestamp, restaura imagens do mapa
quando a fonte muda e preserva o estado vazio. Dimensoes continuam compativeis
com o layout Sync, incluindo altura de 220 px no detalhe mobile, nao uma copia
da altura da referencia. Nenhum polling novo foi adicionado.

## Separacao, cache e limites

Para consultas `SQL_PRODUCAO_*`, conexao direta e bridge usam
`default_transaction_read_only=on`, `SET TRANSACTION READ ONLY` e
`statement_timeout=15000`. Ha no maximo quatro conexoes de leitura simultaneas
por processo em cada lado; conexoes sao fechadas, sem criar um pool novo.
Timeout de conexao vem das configuracoes existentes. Outras consultas do Sync
mantem suas opcoes anteriores. Nenhum INSERT/UPDATE/DELETE/DDL foi adicionado
ao adaptador de documentos externo.

A bridge confirma `read_only: true`; versao antiga/falha gera 503, sem fallback
silencioso para outro banco. Isso verifica o contrato da bridge, nao substitui
a verificacao administrativa dos privilegios da conta no PostgreSQL real.

Cache usa `Session(db.engine)` separado, exclusivamente no banco Flask-SQLAlchemy
da aplicacao. A chave SHA-256 inclui empresa, hash dos bytes originais, versao do
renderizador, DPI, crop, limites, formato, modo aprovacao e variante. Essa formula
melhora isolamento/invalidation em relacao a referencia. Nunca se usa somente
tamanho como prova de igualdade. Alteracao de algoritmo exige mudar
`RENDER_VERSION` em `producao_documentos.py`.

O original ainda e lido para calcular SHA-256 em cache hit. Uma consulta de cache
nao equivale a ausencia de consultas ao GRV. Ha LRU em memoria para as duas
variantes e persistencia da variante solicitada. Locks locais sao limitados a
64 faixas por processo; dois workers geram previews e a fila admite quatro jobs
ao todo. Renderizacao roda em subprocesso Python local com timeout e encerramento,
sem arquivos temporarios de originais e sem servicos de conversao externos.
Conflito de chave unica entre workers faz rollback e reutiliza o asset vencedor.

Nao existe limite de memoria do SO/job object nesta implementacao. Bytes,
dimensoes, timeout e concorrencia limitam a carga, mas a memoria de decodificadores
nativos deve ser dimensionada/monitorada no host. Retencao/expurgo do cache e
quota de disco sao tarefas operacionais ainda nao implementadas.

## Configuracao e implantacao

| Configuracao | Finalidade/padrao sem segredos |
|---|---|
| `COMPRAS_PG_*` / `ERP_LANCAMENTO_PG_*` | Conexao externa existente; usar conta dedicada com privilegio somente SELECT |
| `COMPRAS_PG_COD_EMPRESA` | Empresa autorizada no Sync; fallback existente `ERP_ESTOQUE_PG_COMPANY`, depois 1 |
| `COMPRAS_PG_CONNECT_TIMEOUT` | Timeout da conexao direta, padrao 8 s |
| `COMPRAS_API_URL`, `COMPRAS_USE_API_BRIDGE` | Bridge existente quando habilitada |
| `COMPRAS_API_TOKEN` | Autenticacao servidor-servidor, nunca no navegador |
| `COMPRAS_API_TIMEOUT` | Timeout HTTP da bridge, padrao 60 s |
| `ERP_BRIDGE_PRODUCAO_EMPRESA` | Empresa permitida na bridge, padrao 1; deve corresponder ao Sync |
| `ERP_BRIDGE_CONNECT_TIMEOUT` | Timeout PostgreSQL da bridge, padrao 15 s |
| `DATABASE_URL` | Banco da aplicacao, separado do GRV; destino das migracoes/cache |
| `PRODUCAO_DOCUMENT_MAX_BYTES` | Original, padrao 20 MiB |
| `PRODUCAO_THUMBNAIL_DPI` | Configurado 180, efetivo PDF >=300; validado entre 72 e 600 |
| `PRODUCAO_THUMBNAIL_CROP` | `left,top,right,bottom`, padrao `0,0,1,1`; quatro valores ordenados entre 0 e 1 |
| `PRODUCAO_MAX_PIXELS` | Padrao 24000000; limite configuravel ate 40000000 |
| `PRODUCAO_RENDER_TIMEOUT` | Padrao 20 s, maximo configuravel 120 s |

1. Confirmar conta de leitura, schema e empresa em ambiente autorizado de teste.
2. Atualizar dependencias com o requirements existente. Validado localmente com
   Python 3.12.10, PyMuPDF 1.28.2 e Pillow 12.3.0; nao ha lockfile Python no projeto.
3. Revisar licenciamento de PyMuPDF (AGPL-3.0 ou licenca comercial Artifex), que
   ja era dependencia do Sync. Pillow 12.3.0 declara MIT-CMU. A implementacao nao
   constitui aprovacao juridica/comercial dessas licencas.
4. Atualizar o catalogo de queries e o codigo da bridge junto com o Sync.
5. Apontar Alembic para o banco DA APLICACAO e aplicar a revisao
   `20260911_producao_assets`, descendente de `20260908_consumo_chapa_confirmacao`,
   pelo procedimento de migracoes existente. Nenhuma migracao foi aplicada em
   ambiente operacional por esta tarefa.
6. Confirmar permissao PAGE_PRODUCAO, capacidade de subprocessos no host, memoria,
   concorrencia por numero de workers e funcionamento de documentos sinteticos.

## Verificacao reproduzivel

Na raiz Sync:

```powershell
.\.venv312\Scripts\python.exe -m pytest conferencia_system/tests/test_producao_imagens.py -q
```

Resultado da implementacao inicial: 39 testes passaram em 21.13 s, incluindo disputa
concorrente por um asset entre duas sessoes. Cobrem permissao nos binarios, OS exata,
empresa, origem, IDs sobrepostos, selecao, fonte por numero/ancestral/ciclo,
primeira pagina, rotulo, raster/aprovacao, RGBA, peca longa, arquivo invalido,
PDF protegido, pixels, timeout, cache persistente, conflito, ETag/304,
conteudo diferente com mesmo tamanho, versao/DPI e migracao SQLite isolada.

Correcao posterior da falha de carregamento: 50 testes passaram em 22.66 s na
mesma tarefa focada. Incluem estrutura/detalhe com documentos indisponiveis,
catalogo antigo, bridge sem confirmacao de leitura, acesso negado, falha de rede
ou banco e preservacao de 404 para item inexistente. O teste novo reproduziu
503 indevido antes da correcao. A configuracao local nao possui bridge/host
PostgreSQL remoto; o funcionamento da OS 7807 no servidor publicado nao foi
confirmado nesta sessao. Nao houve restart remoto, deploy ou migracao.

A tarefa existente da suite geral (`python -m pytest tests -q`, dentro de
`conferencia_system`) foi executada e encerrou, mas a ferramenta nao preservou
o resumo final. A saida parcial mostrou falhas e o cache pytest listou dez testes
de `tests/test_app.py`: migracao Alembic, repeticao de sessao, autenticacao de
stats, manifestacao, acesso/ordenacao de notas liberadas, permissao de painel de
recebimento, exclusao de expedicao e codigo de material do auditor. Nao foi
declarada aprovacao da suite geral nem uma contagem total sem saida conclusiva.
Esses modulos nao foram corrigidos nesta tarefa. A suite documental acima tem
resultado completo capturado pela tarefa `Teste Producao Python 312`.

Pylance: sem erros reportados nos arquivos documentais verificados, sintaxe do
worker valida e dois chamadores de `obter_arquivo` compativeis. Nao existe
configuracao Ruff/mypy/npm/typecheck/build do painel neste destino; nao foram
declarados esses comandos como executados. O bundle fornecido nao foi recompilado.

Playwright no navegador integrado, apenas dados sinteticos: desktop 1440x1000
e mobile 390x844; imagens carregadas, `contain`, ausencia de overflow horizontal,
PNG com pixels visiveis e transparentes, movimento reduzido, detalhe vazio com
altura estavel/botao desabilitado e recuperacao apos erro e troca de item.
Na verificacao final, trocar de aba manteve a URL da previa. A interceptacao
da chamada `window.open` do botao principal confirmou o mesmo `open_url` da API
e as opcoes `noopener,noreferrer`; a abertura do binario foi validada nos testes
HTTP, sem depender da automacao de janelas do navegador integrado.
Parte da automacao precisou ignorar a espera de estabilidade dos cliques do
navegador integrado; os controles foram conferidos como visiveis antes disso.
Nao houve screenshots de documentos industriais.

Para demonstracao isolada (nao e servidor de producao):

```powershell
.\.venv312\Scripts\python.exe conferencia_system/tests/test_producao_imagens.py --serve
```

O comando imprime uma URL localhost com porta livre. Usa somente SyntheticSource,
sessao de teste e SQLite em memoria. O bundle continua exibindo o rotulo GRV;
isso NAO significa conexao real. Nao expor esse servidor de testes na rede.

Pendente: homologacao real do schema/privilegios/bridge, licenciamento e capacidade
do host; equivalencia visual em desenhos reais; coordenacao global de carga,
quota/retencao do cache e testes frontend de build a partir de fonte TS ausente.