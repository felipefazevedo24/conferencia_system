# Produção: orçamento, dependências e peças

## Objetivo e referência visual

Integrar ao campo Produção do Sync o comportamento apresentado nas imagens:
pesquisar um orçamento, localizar suas OS e navegar entre dependências de OS
e estrutura interna de peças. Os números abaixo são exemplos de aceite, nunca
condições fixas em código de produção.

- Busca `7175`: OS 7175 por coincidência de número; OS 9958 e 9959 com a
  identificação `Orçamento 7175 · OS ...`.
- Dependências / Todas as OS: orçamento 7175, duas OS vinculadas. Nas imagens,
  9958 está CONCLUÍDO e 9959 APROVADO; nenhuma ligação entre elas é mostrada.
- Dependências / Cadeia da OS: apenas a OS selecionada e as OS conectadas por
  vínculos reais. Uma OS isolada mostra a mensagem de ausência de dependências.
- Estrutura da OS 9958: peça 9958/005 (PB2, quantidade 1) com filhos
  9958/002 (PB, 1), 9958/003 (CANECO, 10) e 9958/006 (USINAGEM FACÃO, 5).
  Outros itens raízes, como 9958/001 e 9958/004, continuam independentes conforme
  `os_pai`; não devem ser ligados ao PB2 pela numeração.
- Ao selecionar uma peça, árvore, mapa e detalhes devem representar a mesma
  peça, com pai, caminho e predecessoras provenientes do GRV.

## Origem da implementação

O projeto vizinho `Estrutura/apps/api/app/adapters/grv/repository.py` já contém
as consultas de rastreio correspondentes às imagens. Elas foram adaptadas ao
catálogo `SQL_PRODUCAO_*` do Sync. O bundle visual integrado ao Sync já suporta
busca por orçamento, as duas abas e os filtros; faltava preencher o contrato
da API, que respondia orçamento e dependências com valores fixos vazios.

| Informação | Fonte no GRV / integração |
| --- | --- |
| Número da OS e status geral | `tos.n_os`, `tos.status_servico` |
| Orçamento que originou a OS | `tsol_max_os.numero_orcamento` e `tos_solicitacao_necessidade.cod_os`, unidos por empresa e `guid_pai = guid_solicitacao` |
| OS dependente → OS necessária | `tsol_max_os.cod_os` → `tos_solicitacao_necessidade.cod_os`, pelo mesmo vínculo de solicitação |
| Estrutura de peças | `tos_aux.os_pai`, dentro da mesma empresa e OS |
| Predecessoras das peças | `tos_aux.predecessora1` e `predecessora2`, quando referenciam itens presentes na OS |
| Status calculado da peça | Operações produtivas, evidências de execução e filhos da estrutura |

## Regras aplicadas

1. GRV é consultado somente para leitura, empresa 1, via infraestrutura existente
   do Sync/bridge. Nenhuma alteração no ERP ou banco de produção é executada.
2. A busca aceita orçamento, OS, peça, desenho e descrição. Uma coincidência
   exata com o número da OS mantém prioridade; a legenda identifica as OS que
   foram encontradas pelo orçamento ou peça.
3. Abrir uma OS usa uma consulta exclusiva por número exato. Um orçamento ou
   uma descrição coincidente não pode substituir a OS solicitada.
4. Pertencer ao mesmo orçamento não cria uma dependência. As linhas do mapa
   representam exclusivamente solicitações reais entre OS no GRV.
5. O rastreio percorre solicitações encadeadas, elimina nós/linhas duplicados e
   usa recursão com `UNION` para terminar mesmo com ciclos, sem cortar em oito
   níveis. Ao abrir uma OS dependente, o orçamento também pode ser localizado
   percorrendo suas origens. OS iniciadas por E não são mostradas no mapa.
6. O contrato visual aceita um orçamento por vez. Se o rastreio encontrar mais
   de um, seleciona o maior número, seguindo a convenção do projeto original.
   Uma futura seleção explícita de orçamento exige ampliar esse contrato.
7. Sem orçamento confirmado: resposta válida com orçamento ausente. Falha de
   consulta: HTTP 503, nunca uma resposta falsa de ausência de vínculo. OS
   inexistente: HTTP 404.
8. Status geral da OS e status das peças são independentes. Uma OS concluída no
   GRV pode conter peças prontas para montagem pelo cálculo das operações.
   Concluir fabricação sem operação de montagem resulta em “Pronto para montagem”.
9. Filhos prontos não liberam montagem se a fabricação do próprio item está
   pendente. Corte iniciado não significa montagem iniciada. Operação concluída
   não permanece em execução apenas por possuir data de início.
10. Relações entre OS e predecessoras são exibidas para consulta. Esta entrega
    não adiciona comandos de liberação no ERP nem cria uma regra nova de bloqueio
    entre OS; a sequência das posições das peças não determina montagem.

## Arquivos e publicação

- `conferencia_app/compras/queries.py`: busca por orçamento, resolução exata da
  OS, rastreio de orçamento e mapa de dependências.
- `conferencia_app/services/producao_service.py`: tradução dos resultados para
  o painel, deduplicação das dependências, predecessoras e cálculo das peças.
- `conferencia_app/routes/producao_routes.py`: integração do contrato original,
  tratamento de ausência/falha e detalhes de hierarquia.

Após revisão e publicação desses arquivos no repositório:

1. Fazer backup dos arquivos locais da VM. Atualizar seletivamente
   `conferencia_app/compras/queries.py` e reiniciar a bridge pelo launcher
   Tailscale existente. A bridge registra o catálogo SQL ao iniciar.
2. Publicar os três arquivos no servidor web do Sync/PythonAnywhere e fazer
   Reload. Atualizar só a VM não atualiza a API web.
3. Reabrir Produção e executar o aceite abaixo. Não há migration, mudança de
   credenciais, nova dependência Python ou rebuild do frontend nesta entrega.

Esta preparação foi validada localmente. Publicação e consultas reais ao GRV
da VM ainda são necessárias para homologação. Não executar `git pull` completo
na pasta divergente da VM; seguir `BRIDGE_ERP_ATUALIZACAO.md`.

## Aceite e validação

| Cenário | Resultado esperado |
| --- | --- |
| Pesquisar 7175 | OS 7175 e OS do orçamento com legenda específica, conforme dados atuais do GRV |
| Selecionar 9958, Todas as OS | 9958 e 9959, seus status do GRV e somente as ligações efetivamente registradas |
| Cadeia da OS isolada | Apenas a selecionada e mensagem de ausência de dependências |
| Orçamento com dependências reais | Nós necessários e ligações direcionadas, inclusive cadeias com várias OS |
| Selecionar OS dependente | Estrutura correspondente; orçamento rastreado pelas origens |
| Selecionar 9958/005 | Filhos 002, 003 e 006 com quantidades do GRV |
| Selecionar uma peça filha | Pai e caminho completos; predecessoras reais, se presentes |
| Indisponibilidade da bridge | Mensagem de falha; não afirmar que não existe orçamento |
| OS inexistente | HTTP 404; não abrir outra OS por orçamento/descrição |

Testes locais, a partir da raiz Sync:

```powershell
.\.venv312\Scripts\python.exe -m pytest conferencia_system/tests/test_producao_contexto.py conferencia_system/tests/test_producao_imagens.py -q
node conferencia_system/tests/production_tree_selection_browser.cjs --budget-context
node conferencia_system/tests/production_tree_selection_browser.cjs
```

Os testes Python verificam serviço e rotas com leituras simuladas; os testes
Chrome verificam o bundle real com respostas simuladas. Não comprovam o conteúdo
atual do ERP nem substituem a execução das consultas SQL na VM. As capturas do
cenário visual são geradas em `tmp_producao_contexto/` (fora do versionamento).
