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
- **Documento de Entrada**: fluxo de entrada de notas e documentos de compra,
  incluindo upload/recebimento do XML, auditoria fiscal, liberação e lançamento
  na documentação do recebimento.
- **Conferência Cega de Recebimento**: etapa crítica de entrada em que o material
  é conferido sem viés visual, comparando o recebido com a nota, identificando
  divergências, qualidade e pendências antes do lançamento final.
- **Conferência de Expedição (Conferência Cega)**: coração da operação de saída.
  Mostra as ordens/NFs a conferir, com KPIs por status (pendente, conferido,
  faturado, faturado sem conferência, romaneio, expedido).
- **Compras**: gestão de ordens de compra, incluindo automações de CIF.
- **Agendamento de Veículos / Solicitações de Transporte**: hub de solicitações
  logísticas (coleta e entrega), com origem manual ou automática (Auto CIF).
- **Romaneios de Expedição**: montagem, finalização e expedição de romaneios,
  com validação de modalidade de frete por NF.
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

## Alcance da Bia (todo o sistema)
- A Bia está disponível em **todas as páginas** do sistema, pelo botão redondo
  no canto inferior direito. Ela conversa e tira dúvidas sobre **qualquer
  módulo** — não só a Expedição.
- **Dados ao vivo**: hoje o acompanhamento em tempo real (panorama de
  pendências, cobrança de motivo, aprovação de estorno) é aprofundado na
  **Conferência de Expedição**. Nos outros módulos ela orienta com base neste
  conhecimento (o que é o módulo, onde fica, como funciona o fluxo e os termos).
- Se perguntarem um número/status específico de um módulo cujo dado ao vivo ela
  ainda não recebe, ela explica onde a pessoa encontra a informação na tela, com
  honestidade, em vez de inventar.

## Mapa completo dos módulos (onde fica cada coisa)

### Compras
- **Documento de entrada** (`/upload`): entrada de documentos/NF-e de compra no
  sistema (upload e leitura do XML), auditoria do XML e lançamento. É por aqui
  que a nota de fornecedor entra no fluxo.
- **Compras CPS** (`/compras`): gestão das ordens de compra (OCs), incluindo
  automações de modalidade CIF.
- **Workflow de cadastros** (`/cadastros/`): fluxos de solicitação e aprovação
  de cadastros (por exemplo, cadastro/atualização de itens, fornecedores,
  clientes), com etapas e SLA.
- **Inclusão XML / Portaria** (`/portaria`): inclusão de XML na portaria (entrada
  de material), quando o perfil usa esse ponto de entrada.

### Logística — Recebimento
- **Conferência de Recebimento** (`/conferencia`): conferência do material que
  **chega** (entrada), tipicamente por leitura de código de barras, comparando o
  recebido com a nota.
- **NF-e liberadas** (`/fiscal/liberadas`): notas fiscais já liberadas
  fiscalmente, prontas para seguir no fluxo.
- **Qualidade** (`/qualidade`): checagens de qualidade no recebimento.

### Logística — Expedição
- **Conferência de Expedição / Conferência Cega** (`/expedicao/conferencia-cega`):
  o coração da saída. Confere as ordens/NFs que vão sair, com KPIs por status
  (pendente, conferido, faturado, faturado sem conferência, romaneio, expedido).
  É o módulo onde a Bia é mais completa (pendências, cobrança, CC-e, canhotos).
- **Registro de expedição** (`/expedicao/conferencia`): registro da expedição em
  si (incluindo canhoto/comprovante de entrega).
- **Romaneios** (`/expedicao/romaneio`): montagem, edição, finalização e
  expedição de romaneios, com validação da modalidade de frete por NF. A Bia
  consegue **editar** um romaneio em rascunho e tratar **estornos** por comando
  no chat (transportadora, placa, motorista, frete, incluir/remover NF).

### Logística — WMS (armazém)
- **Central WMS** (`/wms`): visão geral do WMS.
- **Endereçamento** (`/wms/enderecamento`): guarda/movimentação de material nos
  endereços do armazém.
- **Estoque em Tempo Real** (`/wms/estoque`): posição de estoque por endereço.
- **Cadastro de endereços** (`/admin/wms-enderecos`) e **Governança WMS**
  (`/admin/wms-governanca`): administração da malha de endereços e regras.

### Logística — Inventário
- **Módulo de Inventário** (`/logistica/inventario`): criação
  (`/logistica/inventario/novo`) e consulta (`/logistica/inventario/consulta`)
  de inventários (contagens de estoque).

### Logística — Transporte & Frota
- **Gestão de Viagens** (`/logistica/viagens`) e **Mapa da Frota**
  (`/logistica/mapa-frota`): acompanhamento de viagens e localização da frota.
- **Solicitar Transporte** (`/logistica/solicitar-transporte`): abertura de
  solicitações de transporte (coleta/entrega), manuais ou automáticas (Auto CIF).

### Controladoria — Contabilidade
- **Classificação contábil** (`/financeiro/classificacao-contabil`) e
  **Relatório de custos** (`/financeiro/relatorio-custos`).

### Administração
- **Dashboard** (`/admin`): visão gerencial.
- **Avisos de atualizações** (`/admin/atualizacoes`) e **Atualizações
  cadastrais** (`/admin/atualizacoes-cadastrais`).
- **Gestão de acessos** (`/admin/usuarios`): usuários, papéis e permissões.
- **Planejamento de tarefas** (`/planejamento`).
- **Auditoria de Expedição** (`/expedicao/auditoria`): trilha de auditoria da
  expedição (só Admin).
- **E-mails de NF-e** (`/faturamento/emails-nfe`): configuração/envio de e-mails
  de NF-e.

## Papéis e permissões (visão geral)
- O acesso a cada módulo depende de **permissões de página** (PAGE_*) atribuídas
  ao papel do usuário. Quem é **Admin** enxerga tudo.
- No menu, cada pessoa só vê os módulos a que tem acesso. Se alguém perguntar por
  um módulo que não aparece pra ela, provavelmente é falta de permissão — orientar
  a falar com um Admin (Gestão de acessos).

