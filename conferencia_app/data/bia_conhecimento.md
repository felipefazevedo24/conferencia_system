# Base de conhecimento da Bia — Sistema Columbia Machine Brasil

> Este é o conhecimento base que a Bia usa para responder sobre o sistema.
> Ele é mantido pelos desenvolvedores (Felipe Franco Azevedo e Filipe Allan Oliveira).
> Conhecimento aprendido no dia a dia é adicionado no arquivo
> `instance/bia_conhecimento_extra.md` (não versionado), e a Bia junta os dois.

## Sobre a empresa
- A Columbia Machine Brasil faz parte da Columbia Machine, fabricante de
  equipamentos e máquinas para a produção de blocos, pavers e artefatos de
  concreto (vibro-prensas, moldes, sistemas de paletização e manuseio).
- Este sistema é o ERP/WMS interno usado pela operação (logística, expedição,
  compras, financeiro e cadastros).

## Glossário (termos do sistema)
- **OF (Ordem de Faturamento)**: ordem ligada à saída/faturamento de material.
- **OC / ST (Ordem de Compra / Suprimento e Transferência)**: ordem de entrada
  ou movimentação de material de fornecedor.
- **Romaneio**: documento que agrupa NFs/volumes de uma expedição por
  destinatário; pode estar em rascunho, pronto ou expedido.
- **NF-e**: Nota Fiscal eletrônica. Cada NF tem número, destinatário e itens.
- **CC-e (Carta de Correção eletrônica)**: correção de dados da NF-e (por
  exemplo, quando a modalidade de frete sai divergente do romaneio).
- **Canhoto / comprovante**: comprovante de entrega assinado; anexado ao
  registro de expedição depois que o material é entregue.
- **Conferência cega**: conferência em que o conferente confere as quantidades
  sem ver o esperado, para evitar viés e erros.
- **Modalidade de frete**: CIF (remetente paga/organiza) ou FOB (destinatário).
- **Divergência**: diferença encontrada na conferência (quantidade, item,
  modalidade de frete etc.) que precisa de tratamento antes de seguir.

## Módulos principais
- **Conferência de Expedição (Conferência Cega)**: coração da operação de saída.
  Mostra as ordens/NFs a conferir, com KPIs por status (pendente, conferido,
  faturado, faturado sem conferência, romaneio, expedido).
- **Compras**: gestão de ordens de compra, incluindo automações de CIF.
- **Agendamento de Veículos / Solicitações de Transporte**: hub de solicitações
  logísticas (coleta e entrega), com origem manual ou automática (Auto CIF).
- **Romaneios de Expedição**: montagem, finalização e expedição de romaneios,
  com validação de modalidade de frete por NF.
- **Financeiro (Contas a Receber / Boletos)**: acompanhamento de títulos.
- **Cadastros e Workflow de Cadastro**: manutenção de dados e fluxos de
  atualização cadastral.

## Fluxo da Conferência de Expedição (visão geral)
1. A ordem chega para conferência (status **pendente**).
2. O conferente faz a **conferência cega** das quantidades.
3. Se houver diferença, vira **divergência** e precisa ser tratada.
4. Sem divergência, a ordem fica **conferida**.
5. O material é **faturado** (gera NF-e) e depois **expedido**.
6. Na expedição, monta-se o **romaneio** por destinatário.
7. Após a entrega, anexa-se o **canhoto** (comprovante assinado).

## Regras e prioridades importantes
- **Faturado sem conferência** é o caso mais crítico: a NF já saiu, mas a
  conferência não foi registrada — isso trava e gera risco. É prioridade máxima.
- **Pendente e atrasada**: conferência com previsão de entrega já vencida —
  atenção urgente.
- **CC-e pendente**: quando a modalidade de frete da NF diverge do romaneio,
  fica pendente uma carta de correção que precisa ser aprovada.
- **Sem canhoto**: material expedido sem comprovante de entrega anexado.
- **Conferido parado**: conferido há muito tempo sem seguir para o próximo passo.

## Como a Bia ajuda
- Mostra o panorama das pendências priorizadas por urgência.
- Responde sobre o status de uma ordem ou NF específica (pelo número).
- Lembra o operador do que está atrasado, sem canhoto, com CC-e etc.
- Orienta o próximo passo de cada pendência.
