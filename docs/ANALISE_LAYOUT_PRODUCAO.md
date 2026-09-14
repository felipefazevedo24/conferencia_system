# Análise do layout de Produção

Data: 11/09/2026.

O diagnóstico abaixo registra a análise estática inicial da página `/producao`, anterior às alterações. Após a autorização, os ajustes foram implementados e revisados no navegador; os resultados estão na seção "Implementação e revisão" ao final deste documento.

## Tela analisada

A rota utiliza `templates/producao_shell.html`, que incorpora a aplicação de `static/producao_original/` em um iframe. Na versão analisada inicialmente, o template injetava estilos após o carregamento. O arquivo `templates/producao.html` não é o template servido por essa rota; alterações somente nele não modificariam a tela atual.

A composição existente é adequada ao acompanhamento de montagem: estrutura da OS à esquerda, mapa no centro, detalhes à direita e sequência de operações abaixo do mapa. Existem recursos de recolher painéis, filtrar a estrutura por status, destacar o item selecionado e consultar apontamentos periodicamente. Vale preservar esses recursos.

## Achados e melhorias

### 1. Busca e indicadores ocultos — prioridade crítica

O estilo injetado aplica `.app-header { display: none !important }`. No aplicativo incorporado, esse cabeçalho contém a pesquisa global por orçamento, OS, peça, desenho e descrição, o retorno à OS anterior e os indicadores de progresso, pendências e etapa atual.

O cabeçalho externo apresenta somente título e descrição. A pesquisa da árvore continua disponível, mas filtra os itens da estrutura atual; não substitui a busca global. O aplicativo também adota a OS `7807` como padrão quando a URL não informa outra ordem.

**Proposta:** preservar a busca e os indicadores em uma barra compacta e ocultar somente os elementos decorativos ou os títulos repetidos. Mostrar explicitamente a OS selecionada. Em uma evolução do fluxo de entrada, apresentar seleção de OS ou a última ordem consultada, com identificação clara, em vez de uma ordem fixa.

### 2. Largura mínima e adaptação a telas menores — prioridade alta

Até 1380 px de largura interna, a grade mantém 320 px para estrutura, 350 px para detalhes, pelo menos 480 px para o centro e dois divisores de 20 px. Com os quatro espaçamentos de 10 px e os 20 px de preenchimento injetados, a composição exige aproximadamente 1250 px, antes de eventuais bordas. O menu e as margens externas reduzem a área disponível ao iframe.

Embora a integração remova a largura mínima do contêiner principal, não elimina essas restrições das colunas. O uso de `overflow: hidden` no documento incorporado e na área de trabalho cria risco de conteúdo cortado. As regras para telas menores ajustam dimensões, mas não reorganizam os três painéis.

**Proposta:** usar colunas flexíveis no desktop e aproveitar os controles de recolhimento já existentes. Quando faltar espaço, abrir os detalhes sob demanda. Em tablets e celulares, alternar entre Estrutura, Mapa, Operações e Detalhes por abas. Definir os pontos de adaptação pela largura efetivamente disponível ao painel.

### 3. Altura e excesso de rolagens — prioridade alta

O iframe tem altura mínima de 700 px, reduzida para 580 px abaixo de 900 px de largura. A página externa possui sua própria rolagem vertical; árvore, detalhes e sequência também possuem áreas roláveis. Em notebooks com pouca altura disponível, essa combinação tende a aumentar a necessidade de deslocar a página para alcançar informações.

**Proposta:** dimensionar o painel pelo espaço restante após o cabeçalho e reduzir áreas fixas. Manter o contexto da OS visível durante a consulta. No desktop, delimitar claramente as rolagens dos painéis; em dispositivos pequenos, favorecer um fluxo vertical simples dentro da visualização selecionada.

### 4. Imagem ocupa espaço fixo nos detalhes — prioridade média

A integração força a prévia a `height: 320px` e `min-height: 320px`, substituindo a altura adaptativa original. Esse espaço precede informações técnicas e ações e continua reservado quando a prévia não contribui para a consulta.

**Proposta:** tornar a prévia recolhível, reduzir sua altura conforme a área disponível e oferecer ampliação ao selecionar a imagem. Dar destaque inicial ao código, à descrição, ao status e ao motivo de bloqueio do item.

### 5. Leitura das operações — prioridade média

Os cartões de operações têm largura mínima de 248 px, com conectores de 48 px. Em uma área central de 480 px, dois cartões completos já não cabem. Informações como máquina, operador e horário usam fontes de 9 px; outros textos operacionais usam 10–11 px. Há truncamento de textos, inclusive em nomes de operações.

**Proposta:** aumentar os textos operacionais para uma faixa inicial de 12–14 px, validada no equipamento de uso. Permitir quebra de nomes relevantes e oferecer uma lista vertical compacta como alternativa à sequência horizontal. Evidenciar a operação atual e os bloqueios, preservando acesso às demais etapas.

### 6. Consistência visual e status — prioridade média

O sistema externo permite tema claro e escuro, enquanto a aplicação incorporada declara esquema claro e recebe fundos brancos e cores fixas. Os estados já têm legenda e rótulos em diferentes áreas; na árvore, a representação compacta enfatiza pontos coloridos.

**Proposta:** sincronizar a paleta da Produção com o tema do sistema. Manter significado uniforme para as cores em árvore, mapa e operações; complementar estados críticos com texto ou ícone visível. Validar contraste e leitura no navegador, sem pressupor que as cores atuais atendam a todos os contextos.

## Organização sugerida

1. Cabeçalho compacto: Produção e identificação da OS selecionada.
2. Barra de consulta: pesquisa de OS e retorno à ordem anterior.
3. Resumo operacional: progresso, operações concluídas/total, pendências e etapa atual.
4. Área principal: estrutura recolhível, mapa com maior espaço e detalhes sob demanda.
5. Operações do item selecionado: sequência com operação atual destacada e alternativa em lista.

Priorizar primeiro a recuperação da busca e dos indicadores, depois corrigir a adaptação das colunas e alturas. Ajustar prévia, tipografia e tema na sequência.

## Cuidados de implementação e validação

- Centralizar os estilos de integração em um arquivo próprio, evitando acumular regras contraditórias e substituições com `!important`. Para mudanças de componentes, recuperar o projeto-fonte e seu processo de build em vez de editar diretamente o JavaScript compilado.
- Conferir a tela em 1920×1080, 1366×768, 1024×768 e 390×844, com menu lateral aberto e fechado quando aplicável.
- Verificar que pesquisa, resultados, indicadores e troca de OS permaneçam acessíveis em todos os tamanhos previstos.
- Exercitar uma OS com estrutura extensa, descrições longas, diversas operações, bloqueios e itens sem imagem.
- Conferir seleção sincronizada entre árvore, mapa, detalhes e operações; recolhimento dos painéis; navegação por teclado; temas claro e escuro; estados de carregamento, erro e ausência de dados.
- Confirmar que nenhum painel essencial fique cortado e que os textos críticos possam ser lidos sem depender exclusivamente de passagem do mouse.

## Evidências locais

- `conferencia_app/routes/producao_routes.py`: escolha do template e disponibilização da aplicação incorporada.
- `templates/producao_shell.html`: cabeçalho externo, dimensões do iframe e estilos injetados.
- `static/producao_original/assets/index-DmXmPAVC.css`: grade, dimensões dos painéis, fontes, cores e regras de adaptação.
- `static/producao_original/assets/index-BI65Am7T.js`: conteúdo do cabeçalho, buscas, painéis recolhíveis, seleção inicial e sequência de operações.
- `static/css/ui_erp.css` e `templates/base.html`: contêiner externo, rolagem e alternância de tema.

## Implementação e revisão

Implementação concluída e revisada em 11/09/2026, restrita à interface de Produção:

- `templates/producao_shell.html` e `static/css/producao_shell.css`: contêiner dimensionado pelo espaço disponível, cabeçalho compacto e remoção da injeção de estilos que ocultava a busca.
- `static/producao_original/index.html`: carregamento dos arquivos próprios de apresentação de Produção após o bundle original.
- `static/css/producao_panel.css`: busca e indicadores visíveis, colunas flexíveis, navegação compacta até 1100 px de largura interna, tipografia das operações, prévia menor e cores para os temas claro e escuro.
- `static/js/producao_panel.js`: navegação entre visualizações, lista de operações, recolhimento/ampliação da imagem e sincronização do tema. Os complementos são reaplicados quando os componentes do bundle são remontados.

Os estilos compartilhados, o backend, as permissões, as APIs, a OS padrão e as regras produtivas foram preservados. O JavaScript compilado e seu mapa de fontes também não foram alterados.

### Validação executada

Teste local no Chrome sem janela, usando o bundle React real, os estilos do shell e respostas simuladas das APIs, sem consultas ao ERP. O servidor de teste reproduziu o contêiner externo e o reset básico de dimensionamento; não executou autenticação ou renderização Jinja pelo Flask.

- 23 combinações de resolução, estado do menu lateral e visualização ativa: 1920×1080, 1366×768, 1024×768 e 390×844.
- Painéis ativos e iframe dentro dos limites disponíveis, sem rolagem horizontal da página nem excesso de altura externa nos cenários testados.
- Busca global, seleção de outra OS e retorno à anterior.
- Seleção de item e atualização dos detalhes/operações; acesso aos materiais.
- Recolhimento, ampliação e fechamento da imagem com mouse e tecla Escape. Ampliação desabilitada e visualmente atenuada quando não há imagem.
- Lista de operações e os três controles originais de recolhimento dos painéis no desktop.
- Sincronização do tema escuro, inspeção de capturas de tela e verificação do fundo da busca após a transição de tema.
- Estados de erro e estrutura vazia mantendo a busca acessível.
- Nenhuma exceção JavaScript não tratada durante a execução. Verificações de sintaxe JavaScript e `git diff --check` concluídas.

A revisão valida a apresentação e as interações com dados simulados. Autenticação, respostas reais do GRV e implantação no servidor não foram exercitadas.
