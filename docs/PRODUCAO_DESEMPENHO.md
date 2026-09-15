# Desempenho da Producao

As melhorias ficam no modulo de Producao do servidor web, em seu adaptador de interface e no endpoint de previas da bridge. Nao alteram regras de outros modulos nem o esquema do banco. Desde 15/09/2026, a aceleracao da transferencia requer atualizar tambem os arquivos de Producao usados pela bridge.

## Consultas e pesquisa

- Pedidos simultaneos da mesma estrutura compartilham a consulta em andamento.
- A OS retornada pela pesquisa e reaproveitada na abertura, evitando repetir a busca ampla no GRV.
- As miniaturas compartilham a estrutura de itens e o catalogo de documentos da OS. Estes catalogos contem metadados, nao os PDFs. Cada item continua filtrado pelo seu codigo, segmento e revisao.
- Busca e metadados usam cache de 10 segundos, por processo e aplicativo, limitado a 128 entradas. A estrutura mantem 16 entradas. Nao foram ampliados os intervalos de atualizacao dos status.
- Itens, operacoes e ocorrencias da estrutura continuam sendo consultados em paralelo, com tres trabalhadores. Consultas com falha nao ficam no cache de dados.
- O bundle existente mantem cancelamento de pesquisas antigas, debounce de 250 ms e cache de pesquisa de 30 segundos no navegador.

## Imagens

- Quando a bridge oferece `POST /api/erp/producao/preview`, o PDF e processado junto ao ERP. Apenas `thumbnail.png` e `detail.png` atravessam a rede em um pacote binario, sem o PDF e sem Base64.
- A conexao usa automaticamente a URL e o token ja configurados para Compras/ERP. Cada trabalhador reutiliza sua sessao HTTP, sem alterar o transporte das outras integracoes.
- O endpoint exige autenticacao, restringe a empresa a 1 e executa consultas somente leitura. A revisao e conferida antes e depois da leitura do arquivo, e as conexoes PostgreSQL sao fechadas antes de renderizar.
- A bridge reaproveita as duas imagens pela identidade e revisao do documento. O servidor web tambem as armazena em memoria e em `instance/producao_previews`, fora dos arquivos publicos.
- O cache em disco compartilha as previas prontas entre trabalhadores e sobrevive ao Reload. Armazena somente as duas imagens, com limite de 256 pacotes e 128 MiB, expirando apos sete dias sem acesso. As gravacoes sao atomicas e a limpeza ocorre nas gravacoes seguintes. Falha de disco ou pacote corrompido nao impede o carregamento pelo caminho normal.
- A chave do disco inclui origem da bridge, hash da credencial, empresa, OS/item de origem, documento, revisao e versao do renderizador. A revisao ainda e obtida pelos metadados com cache de 10 segundos; os sete dias de retencao nao substituem essa verificacao.
- Bridge antiga, renderizador incompativel ou bibliotecas ausentes acionam o caminho local automaticamente. A disponibilidade e testada novamente em cinco minutos, ou imediatamente apos Reload do web app. Falhas de autenticacao ou de confirmacao de leitura segura nao sao ignoradas.
- O cache da imagem pronta e consultado antes de baixar o arquivo, usando origem, codigo, revisao do documento, revisao do desenho e aplicativo como identidade.
- Miniatura e detalhe compartilham download e renderizacao. Uma revisao nova gera outra imagem; documentos sem revisao continuam sendo conferidos pelo hash do conteudo.
- Downloads nao ocupam os dois trabalhadores de renderizacao. Sao admitidas quatro requisicoes de miniatura e duas de detalhe por processo web. Quando nao ha vaga, a resposta indica processamento pendente sem enfileirar o item; a proxima tentativa visivel pode ocupar a vaga liberada. Assim, uma OS grande nao acumula trabalhos de pecas que ja sairam da tela. Trabalho ja iniciado nao e interrompido.
- Em PDFs com imagem incorporada, somente paginas com rotulo isometrico precisam de analise geometrica antes de aproveitar a imagem. A geometria completa continua sendo analisada se nao houver imagem incorporada nem vista rotulada utilizavel. A prioridade de selecao do desenho foi preservada.
- A analise de transparencia usa histograma e operacoes nativas do Pillow. As mascaras sao lidas como bytes contiguos em vez de consultar cada pixel individualmente.
- As derivadas permanecem limitadas a 64 entradas. Falhas de imagem ficam limitadas e expiram em 10 segundos, permitindo nova tentativa apos uma indisponibilidade temporaria.
- A tela usa decodificacao assincrona e prioriza o detalhe. A consulta de processamento pendente fica limitada a 500 ms entre tentativas no detalhe e 1 segundo nas miniaturas visiveis, em vez dos 2 segundos anteriores.
- Tentativas e prazo de carregamento sao pausados fora da area visivel ou com a pagina oculta. Atualizacoes de uma imagem nao percorrem novamente toda a arvore do DOM.

## Validacao local

Em 14/09/2026, a mesma imagem sintetica de 900 x 1000 pixels levou 1,92 s antes e 0,99 s depois, aproximadamente 49% menos tempo nesta medicao. Os PNGs de miniatura e detalhe foram identicos por SHA-256. Isto mede CPU local, nao tempo de rede ou de consultas reais.

Em 15/09/2026, um teste HTTP completo, autenticado, gerou um PDF local de 1.539.576 bytes e recebeu 100.707 bytes de previas, aproximadamente 93% menos dados que o PDF binario. O caminho antigo ainda acrescentava Base64 ao PDF. A primeira resposta levou 0,733 s; o detalhe subsequente reutilizou o cache, sem outra transferencia. O banco foi simulado, mas o servidor HTTP, o cliente e a renderizacao foram reais. Esses valores nao medem o Tailscale, o GRV nem a VM publicada.

Na verificacao adicional de 15/09/2026, um PDF sintetico com 500 registros vetoriais e imagem incorporada passou de mediana de 1,142 s para 0,685 s em tres execucoes, 40% menos tempo. Os dois PNGs permaneceram identicos. No teste HTTP, outra instancia web recuperou a previa em 0,00114 s pelo disco, sem nova transferencia. Um processo Python independente tambem confirmou o reaproveitamento sem HTTP. Estes tempos nao representam uma medicao da OS 7807/042 no GRV.

Os testes de fila simulam 50 itens concorrentes e verificam a admissao limitada, a liberacao de vagas para a selecao atual e a entrega imediata de trabalhos ja concluidos.

Executar a partir de `conferencia_system`, usando o Python 3.12 do ambiente do projeto:

```powershell
..\.venv312\Scripts\python.exe -m pytest tests/test_producao_imagens.py -q
..\.venv312\Scripts\python.exe -m pytest tests/test_producao_imagens.py -k bridge_http_completo -q -s
..\.venv312\Scripts\python.exe -m pytest tests/test_app.py -k producao -q
node tmp_producao_layout/check.cjs --performance
```

O verificador visual usa Chrome local e dados simulados. Confere imagem pendente/pronta, prioridade, pausa fora da tela, troca rapida de pecas, cache de pesquisa e capturas em 1920 e 390 pixels. Nao acessa o ERP.

## Publicacao

Aplicar a Parte 1 do procedimento de atualizacao no PythonAnywhere e a atualizacao seletiva da bridge descrita em [BRIDGE_ERP_ATUALIZACAO.md](BRIDGE_ERP_ATUALIZACAO.md#previas-de-producao-processadas-na-bridge). Nao ha migration ou alteracao de credenciais. PyMuPDF e Pillow, ja usados no sistema web, precisam estar instalados tambem no ambiente virtual da VM.

Atualizar somente o servidor web mantem a compatibilidade, mas nao ativa o processamento junto ao ERP enquanto a VM nao receber o novo endpoint. Nao rodar `git pull` completo na VM e nao sobrescrever arquivos locais sem backup.

Quando o endpoint ja estiver ativo, esta rodada adicional exige publicar `conferencia_app/services/producao_service.py` e `conferencia_app/services/producao_bridge.py` no servidor web e fazer Reload. A mesma versao do servico deve estar na VM para o caminho rapido de PDF. Nao e necessario reinstalar PyMuPDF/Pillow nem atualizar o bundle JavaScript nesta rodada. O processo web precisa de permissao de escrita na pasta `instance`, que ja e usada pela aplicacao; o cache e criado automaticamente.

O cliente e a bridge registram `producao_bridge_preview`/`producao_preview_bridge` em nivel INFO, com tempo e quantidade de bytes, sem token ou conteudo do documento. A resposta interna da bridge inclui `Server-Timing`, `X-Original-Bytes` e `X-Production-Preview-Version` para diagnostico.

Na aplicacao publicada, medir no Network do navegador busca, estrutura e imagens com cache frio, repetindo dentro e depois de 10 segundos. Conferir uma alteracao de revisao de desenho e duas OS diferentes. O cache de metadados e os trabalhos em andamento continuam locais ao processo; as previas prontas da bridge podem ser reutilizadas por todos os trabalhadores que compartilham a mesma pasta `instance`.

A primeira consulta ainda depende do GRV, da bridge e da rede. Os testes locais nao garantem resposta instantanea nem substituem essa medicao no ambiente publicado.
