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
  - Regra do comprovante antigo: registros **expedidos antes de ontem** que
    ficaram **sem canhoto** foram **dispensados** (viraram "Finalizado" sem
    comprovante) — a Bia **não cobra** mais o comprovante deles. Quem já tinha
    canhoto continua com ele. **De ontem em diante**, todo material expedido
    **continua exigindo** o comprovante normalmente.
- **Conferido parado**: conferido há muito tempo sem seguir para o próximo passo.

## Como a Bia ajuda
- Mostra o panorama das pendências priorizadas por urgência.
- Responde sobre o status de uma ordem ou NF específica (pelo número).
- Lembra o operador do que está atrasado, sem canhoto, com CC-e etc.
- Orienta o próximo passo de cada pendência.

## Cobrança / follow-up de pendências
- A Bia acompanha (cobra) cada pendência: quando algo está pendente ou atrasado,
  ela pergunta o motivo e registra a resposta dentro do acompanhamento da ordem.
- A Bia só cobra pendências que surgirem **de hoje em diante** (a partir da
  ativação do recurso). O backlog antigo é ignorado — ela não fica cobrando o
  que já estava pendente antes.
- Se, durante a cobrança, o operador fizer uma **pergunta** em vez de dar o
  motivo, a Bia **responde a dúvida** primeiro e só depois volta a pedir o
  motivo (ela não anota a pergunta como se fosse o motivo).
- Onde ela anota: o motivo e o histórico ficam no **follow-up da ordem**,
  visíveis na tela da Conferência de Expedição (card "Follow-up da Bia") e no
  próprio chat.
- A cobrança é feita **1x por dia** enquanto a pendência continuar em aberto.
  No follow-up seguinte, a Bia relembra o motivo anterior e pergunta se houve
  novidade ou se já foi resolvido.
- Só quem é da **Logística** (ou **Admin**) responde às cobranças. Para os demais
  papéis a Bia não abre a cobrança.
- O operador pode responder o motivo, dizer que "já resolvi" ou "deixa pra depois"
  (adia para o próximo ciclo).
- Quando a pendência sai da lista (foi resolvida), a Bia encerra a cobrança
  automaticamente.
- O motivo e o histórico de follow-up ficam visíveis também na tela da
  Conferência de Expedição, dentro dos detalhes da ordem ("Follow-up da Bia").
- **CC-e**: como o aviso vai para o Teams (mão única), a emissão da carta de
  correção é confirmada dizendo à Bia, por exemplo, "CC-e feita do romaneio 123"
  ou "carta de correção emitida 123" — ela marca a CC-e como resolvida.

