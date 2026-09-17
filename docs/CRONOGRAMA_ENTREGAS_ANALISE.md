# Cronograma de Entregas — análise e prévia

## Etapa atual

Prévia visual navegável em `previas/cronograma-entregas.html`. Dados fictícios identificados por `DEMO-`; não consulta APIs nem bancos. Não é a implementação final. A prévia permite avaliar tema, mês/ano, pesquisa, setor demonstrativo, seleção de orçamento/OS/peça e estados vazios antes de alterar as telas existentes.

## Arquitetura identificada

- Flask, templates Jinja e navegação compartilhada em `templates/base.html`.
- Produção protegida por `PAGE_PRODUCAO`, rotas em `conferencia_app/routes/producao_routes.py`.
- `/producao` renderiza `producao_shell.html`, que incorpora `/producao-original/`.
- Painel React compilado em `static/producao_original/assets`, com mapa hierárquico, sequência e consultas sob demanda. Fontes TSX e manifesto de build não foram encontrados na busca inicial dos arquivos do projeto.
- Ajustes de apresentação separados em `static/css/producao_panel.css` e `static/js/producao_panel.js`.
- `producao_service.py` usa consultas registradas em `compras/queries.py`, `fetch_all`/`fetch_one` e caches existentes.
- A conexão ERP usa PostgreSQL/psycopg2 ou bridge. Consultas registradas com prefixo `SQL_PRODUCAO_` recebem transação somente leitura; a bridge deve confirmar `read_only=true`.

## Evidências e pendências de dados

| Informação | Evidência no código | Pendência |
|---|---|---|
| Previsão de entrega do orçamento | `torcamento.dt_previsao_entrega`, já usada em `compras/queries.py` | Validar dados reais e tipo no ambiente de destino |
| Número/versão do orçamento | `torcamento.n_orcamento`, `torcamento.versao` | Preservar identidade por empresa e código, não apenas número |
| Vínculo direto orçamento–OS | `tos.cod_orcamento` e `torcamento_servico_gerados`, com cancelados desconsiderados nas consultas atuais | Validar cardinalidade e eventual coexistência de vínculos |
| Vínculo usado pelo painel de Produção | `SQL_PRODUCAO_ORCAMENTO_OS` / `SQL_PRODUCAO_DEPENDENCIAS_OS`: `tsol_max_os` e `tos_solicitacao_necessidade` | Conciliar com vínculo direto; não confundir OS dependente com entrega principal |
| Cliente | `tos.cliente` nas consultas atuais | Fonte do cliente para orçamento sem OS ainda não confirmada |
| Estrutura e quantidades | `SQL_PRODUCAO_ESTRUTURA_OS`, `tos_aux` | Reutilizar hierarquia do serviço atual |
| Sequência, máquina e status | `SQL_PRODUCAO_OPERACOES_OS`, `tpro_pro` e `tctrl_ph` | Reutilizar contratos e regras atuais |
| Operação em execução | `/api/v1/orders/<os>/items/<id>/operations/live` | Considerar todos os apontamentos ativos; não escolher arbitrariamente uma etapa quando houver paralelismo |
| Setor | Nenhuma origem inequívoca localizada nesta análise inicial | Validar tabela/campo e semântica do filtro antes de integração |
| Imagens | Rotas existentes de thumbnail e serviço de previews | Reutilizar e tratar indisponibilidade |
| Entrega realizada / risco de prazo | Conclusão de produção não comprova entrega ao cliente | Não classificar entrega como atrasada/concluída sem regra e fonte validadas |

Uma tentativa de inspeção de schema usou conexão PostgreSQL com `default_transaction_read_only=on`, `readonly=True`, timeout e apenas SELECT de `information_schema.columns`. A conexão não ficou disponível. A configuração consultada por um contexto Flask mínimo não apresentou bridge configurada; isso não comprova indisponibilidade da configuração do ambiente implantado. Nenhum bootstrap da aplicação ou rotina de escrita foi executado.

## Plano de integração

1. Avaliar visualmente a prévia com o usuário, conforme solicitado.
2. Validar origens pendentes e localizar fontes do painel React ou uma extensão suportada do painel existente.
3. Criar rota de página e endpoint GET dentro do blueprint atual, com `PAGE_PRODUCAO`, e adicionar a entrada no menu Produção.
4. Consultar a partir do orçamento com `data >= início do mês AND data < início do mês seguinte`, preservando orçamentos sem OS e agrupando múltiplas OS sem duplicar contagens. Parâmetros SQL, sem interpolar entradas do usuário.
5. Reutilizar mapa, sequência, apontamentos e imagens atuais. Consultar orçamento primeiro, estrutura após seleção e detalhe da peça sob demanda. Invalidar seleções ao mudar período e descartar respostas antigas.
6. Manter somente leitura inclusive no painel incorporado: a tela atual possui recursos de escrita de observações/sequência que precisam ficar fora do fluxo do cronograma. Reutilizar o painel inteiro sem essa adaptação não atende ao requisito.
7. Verificar catálogo da bridge antes da disponibilização; consultas novas precisam estar presentes no ambiente que executa o catálogo.
8. Testar limites de mês/ano, relacionamentos, ordenação, apontamentos paralelos, estados vazios/erro/carregamento, permissões, responsividade e regressões da Produção.

## Limites da prévia

Mapa com formas esquemáticas; não são imagens reais de peças. A prévia não replica a lógica produtiva real nem implementa consulta mensal, carregamento remoto, erros de API ou permissões. Os filtros operam somente nos exemplos de setembro/outubro de 2026. Todas as operações apresentadas são demonstrativas. A integração permanece pendente das verificações acima.
