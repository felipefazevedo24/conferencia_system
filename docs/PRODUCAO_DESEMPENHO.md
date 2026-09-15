# Desempenho da Producao

As melhorias ficam no modulo de Producao do servidor web e em seu adaptador de interface. Nao alteram outros modulos, o catalogo SQL, o banco ou a configuracao do ERP Bridge.

## Consultas e pesquisa

- Pedidos simultaneos da mesma estrutura compartilham a consulta em andamento.
- A OS retornada pela pesquisa e reaproveitada na abertura, evitando repetir a busca ampla no GRV.
- As miniaturas compartilham a estrutura de itens e o catalogo de documentos da OS. Estes catalogos contem metadados, nao os PDFs. Cada item continua filtrado pelo seu codigo, segmento e revisao.
- Busca e metadados usam cache de 10 segundos, por processo e aplicativo, limitado a 128 entradas. A estrutura mantem 16 entradas. Nao foram ampliados os intervalos de atualizacao dos status.
- Itens, operacoes e ocorrencias da estrutura continuam sendo consultados em paralelo, com tres trabalhadores. Consultas com falha nao ficam no cache de dados.
- O bundle existente mantem cancelamento de pesquisas antigas, debounce de 250 ms e cache de pesquisa de 30 segundos no navegador.

## Imagens

- O cache da imagem pronta e consultado antes de baixar o arquivo, usando origem, codigo, revisao do documento, revisao do desenho e aplicativo como identidade.
- Miniatura e detalhe compartilham download e renderizacao. Uma revisao nova gera outra imagem; documentos sem revisao continuam sendo conferidos pelo hash do conteudo.
- Downloads nao ocupam os dois trabalhadores de renderizacao. O detalhe selecionado tem uma fila de consulta separada das miniaturas.
- A analise de transparencia usa histograma e operacoes nativas do Pillow. As mascaras sao lidas como bytes contiguos em vez de consultar cada pixel individualmente.
- As derivadas permanecem limitadas a 64 entradas. Falhas de imagem ficam limitadas e expiram em 10 segundos, permitindo nova tentativa apos uma indisponibilidade temporaria.
- A tela usa decodificacao assincrona e prioriza o detalhe. A consulta de processamento pendente fica limitada a 500 ms entre tentativas no detalhe e 1 segundo nas miniaturas visiveis, em vez dos 2 segundos anteriores.
- Tentativas e prazo de carregamento sao pausados fora da area visivel ou com a pagina oculta. Atualizacoes de uma imagem nao percorrem novamente toda a arvore do DOM.

## Validacao local

Em 14/09/2026, a mesma imagem sintetica de 900 x 1000 pixels levou 1,92 s antes e 0,99 s depois, aproximadamente 49% menos tempo nesta medicao. Os PNGs de miniatura e detalhe foram identicos por SHA-256. Isto mede CPU local, nao tempo de rede ou de consultas reais.

Executar a partir de `conferencia_system`, usando o Python 3.12 do ambiente do projeto:

```powershell
..\.venv312\Scripts\python.exe -m pytest tests/test_producao_imagens.py -q
..\.venv312\Scripts\python.exe -m pytest tests/test_app.py -k producao -q
node tmp_producao_layout/check.cjs --performance
```

O verificador visual usa Chrome local e dados simulados. Confere imagem pendente/pronta, prioridade, pausa fora da tela, troca rapida de pecas, cache de pesquisa e capturas em 1920 e 390 pixels. Nao acessa o ERP.

## Publicacao

Aplicar a Parte 1 do procedimento de atualizacao: publicar o codigo e fazer Reload do processo web. Nao ha migration, nova dependencia ou atualizacao de bridge. O adaptador JavaScript possui uma nova versao na pagina de Producao.

Na aplicacao publicada, medir no Network do navegador busca, estrutura e imagens com cache frio, repetindo dentro e depois de 10 segundos. Conferir uma alteracao de revisao de desenho e duas OS diferentes. Os caches sao locais ao processo, de modo que cada trabalhador web precisa aquece-los separadamente.

A primeira consulta ainda depende do GRV, da bridge e da rede. Os testes locais nao garantem resposta instantanea nem substituem essa medicao no ambiente publicado.
