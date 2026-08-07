# Especificação — Módulo Comex (Importação/Exportação)

> **Status: registrado para referência futura — implementação ainda não iniciada.**
> Este documento arquiva a especificação completa recebida em 2026-08-06 para o
> futuro módulo de gestão de processos de importação/exportação do Sync.
> A intenção do autor é que esta especificação vire, mais adiante, uma Skill,
> um Subagent, ou outro mecanismo de automação do Claude Code — por isso está
> preservada aqui na íntegra antes de qualquer decisão de arquitetura.

## Contexto geral

Sistema de gestão de processos de importação/exportação, dividido em módulos
sequenciais (workflow):

```
OC → PO → Cotação → Instrução e Documentação → Coleta → Em Trânsito
   → Desembarque → Desembaraço → Transporte → NF/Câmbio
```

Cada processo tem um identificador único que atravessa todos os módulos
(ex.: **IM** — Importação Marítima, **IA** — [Importação Aérea, a confirmar]).

## Funcionalidades gerais (transversais a todo o sistema)

1. **Gestão de acesso**: criar grupo de usuários com acesso ao módulo de Comex
   (nota: já existe um cargo "Comex" criado em `conferencia_app/auth.py` com o
   mesmo nível de `BASE_ROLE_PERMISSIONS` de Logística — ver histórico de
   2026-08-03/05 nesta mesma sessão de trabalho; as páginas específicas deste
   módulo precisarão ser adicionadas a esse cargo quando o módulo existir).
2. **Função estornar**: estorna a ação totalmente, retornando ao módulo
   anterior.
   - Workflow normal: `OC → PO → Cotação → Instrução e Documentação → Coleta
     → Em Trânsito → Desembarque → Desembaraço → Transporte → NF/Câmbio`
   - Workflow de estorno (ordem inversa): `NF/Câmbio → Transporte →
     Desembaraço → Desembarque → Em Trânsito → Coleta → Instrução e
     Documentação → Cotação → PO → OC`
3. **Banco de dados**:
   - Otimizar o schema ao máximo — se possível, **uma única tabela completa**
     com todos os campos preenchidos ao longo do workflow (cada etapa popula
     colunas dessa mesma tabela, evitando "colcha de retalhos" de várias
     tabelas).
   - Incluir desde já ~100 colunas extras não utilizadas ainda, reservadas
     para variáveis futuras.
   - A função de Cotação pode eventualmente precisar de um banco/tabela
     separado (por causa do formulário público para fornecedores), mas com
     conexão clara e fácil ao schema principal (chave = Ref FF do processo).

### Referência de campos (planilha de controle atual, ver imagem anexada)

Colunas identificadas na planilha de controle usada hoje pela operação
(fonte primária de campos para a tabela única do item 3 acima):

```
REFERENCIA PO-OC | Referencia freight forward | Referencia despachante
FORNECEDOR/CLIENTE | Freight forward | HISTORICO | Status | Tipo Operação
CONHECIMENTO DE EMBARQUE | AGENTE DE CARGA | INVOICE | VALOR IMPORTAÇÃO (DÓLAR)
VENCIMENTO FORNECEDOR | PAIS | PRODUTO | QUANTIDADE | MODAL | ETD | ETA
PRAZO DE TRANSITO | REF DESPACHANTE | NUMERARIO R$ | data de pagamento
DI-DUIMP | DATA REGISTRO | CANAL | DATA DESEMBARAÇO CI | NOTA FISCAL
DATA EMISSÃO NFE | DATA DE ENTREGA | LOCAL DE ENTREGA | FECHAMENTO PROCESSO
NOME DO CLIENTE | STATUS2
```

Exemplo de linha real (referência 10195): fornecedor Mouser Electronics,
status "Entregue", tipo operação "Impo Courrier", NCM 96.90 e 8538, invoice
493782122494, valor US$ 397,24, país Estados Unidos, produto "Conector e
aliviador peças para máquina de concreto", 1 caixa, modal aéreo, ETD
11/12/2025, ETA 15/12/2025, prazo trânsito 4 dias, transportador FedEx,
numerário S/D, DI-DUIMP 25/019624239-7, data desembaraço 13/12/2025, canal
verde, nota fiscal 10.713 emitida 12/01/2026, entregue 13/12/2025 em
Hortolândia, fechamento de processo 12/01/2026, cliente "CMB Hortolândia".

## Fase 01 — Importação

### Módulo 1: OC

**Objetivo**: integrar as OCs do sistema GRV via Bridge.

- Requisito: importar/receber dados de uma Ordem de Compra via bridge já
  configurada (mesmo padrão de bridge já usado em outras integrações do
  sistema, ex. `scripts/erp_lancamento_api_bridge.py`).
- **Importante (esclarecido em 2026-08-06):** a importação é **sob
  demanda**, não uma sincronização automática/em massa. O operador
  pesquisa a OC no ERP (por número/fornecedor) e decide explicitamente
  importar aquela OC específica para virar um processo Comex — dentro das
  OCs do ERP existem compras locais que não são de importação/exportação e
  não devem entrar automaticamente no módulo.

### Módulo 2: PO

**Objetivo**: gerar PO a partir da Ordem de Compra.

1. Formulário de PO com base na OC, campos pré-preenchidos a partir da OC
   importada mas totalmente editáveis pelo operador. Operador pode selecionar
   **mais de uma OC**, desde que do mesmo fornecedor.
2. Antes de avançar, operador deve selecionar o **pagador do frete**
   (Columbia ou Cliente/Fornecedor) — este campo condiciona o Módulo de
   Cotação (ver abaixo).
3. Ações:
   - **Salvar**: salva sem enviar.
   - **Editar**: permite edição total.
   - **Apagar**: apaga a PO criada.
4. Gerar PDF com opção de download.
5. Enviar e-mail para o fornecedor (se o fornecedor não tiver e-mail
   cadastrado, abrir campo para inclusão + envio). Suporta múltiplos
   destinatários separados por `;`. Copiar sempre `laroli@colmac.com` e
   `filoli@colmac.com` (conferir grafia exata do segundo e-mail com o
   solicitante — no texto original aparece "Filoli@colmac.com").
6. Função de liberar/finalizar **sem** enviar e-mail (operador envia
   manualmente depois).

### Módulo 2 (bis, nomeado como "Módulo 02" no original): Cotação

**Pré-condição**: PO já criada e e-mail do Módulo 2 (PO) já enviado.

**Objetivo**: cotação de frete/transporte.

1. Regra condicional: se `pagador do frete == "Columbia"`, inicia
   automaticamente o fluxo de cotação; caso contrário, pula o módulo ou
   marca como "não aplicável".
2. Formulário de cotação: fornecedor de frete, modal, origem, destino, prazo
   estimado, valor do frete, seguro, taxas adicionais, custo total.
3. Gerar link único e temporário por fornecedor, para preenchimento externo
   sem login completo (formulário público vinculado à Ref FF). Nesse
   formulário, solicitar também o e-mail das pessoas que devem receber a
   instrução de embarque futuramente.
4. Comparar automaticamente as cotações recebidas e sugerir o fornecedor de
   **melhor custo total** (não necessariamente o menor frete — considerar o
   total com taxas).
5. Operador pode escolher fornecedor diferente do sugerido, mas o sistema
   **obriga** justificativa (campo de texto) antes de avançar.
6. Registrar **todas** as cotações recebidas (não só a escolhida), para
   histórico/auditoria.

### Módulo 3: Instrução e Documentação

**Pré-condição**: fornecedor de frete definido (Módulo Cotação).

**Objetivo**: instrução de embarque e acompanhamento documental.

1. Gerar automaticamente e-mail de "instrução de embarque" com: cópia da
   cotação vencedora + fornecedor de frete escolhido, dados da PO e contato
   do despachante — pronto para revisão/envio pelo operador (permitir
   incluir anexos/itens extras).
2. Ações:
   - **Salvar**: salva sem enviar.
   - **Editar**: edição total.
   - **Anexar documento**.
   - **Apagar**.
3. Componente reutilizável **"Follow up de documentação"**:
   - Botão de ação "Documentação OK" (toggle/checkbox de conclusão).
   - Campo para anexar o documento recebido.
   - Campo de comentário livre associado.
4. Histórico de comentários **append-only** (múltiplas entradas com data e
   autor, nunca sobrescreve).

**Entregável**: template de e-mail, formulário de campos obrigatórios, e o
componente reutilizável de Follow-up (será reaproveitado nos módulos
seguintes).

### Módulo 4: Coleta

**Objetivo**: acompanhamento da coleta da mercadoria.

1. Reutiliza o componente de Follow-up: botão "Coleta OK" + comentário
   livre, vinculado à Ref FF.
2. Múltiplos comentários ao longo do tempo (log cronológico).

Modelo de dados sugerido: `FollowUp { processo_id, modulo='coleta',
status_ok, comentario, autor, data }`.

**Entregável**: tela/card do módulo exibindo histórico de follow-ups e botão
de ação, visualmente consistente com o Kanban geral do fluxo.

### Módulo 5: Em Trânsito

**Objetivo**: acompanhamento durante o transporte internacional.

1. Reutiliza Follow-up: botão "Em Trânsito OK" (ou "Coleta OK", conforme
   nomenclatura padrão a definir) + comentário livre.
2. Campo opcional de **ETA** (data estimada de chegada), atualizável
   manualmente conforme informação do transportador.
3. Alerta visual simples se a ETA estiver próxima ou vencida sem atualização
   de status.

Modelo de dados sugerido: `FollowUp { ..., modulo='em_transito' }` + campo
`eta (date, nullable)` na entidade Transporte/Processo.

**Entregável**: tela/card com histórico de follow-ups, campo de ETA editável
e indicador visual de atraso.

### Módulo 6: Desembarque

**Objetivo**: chegada da mercadoria no país de destino.

1. Reutiliza Follow-up: botão "Checada OK" + comentário livre.
2. Envio automático de lembrete (e-mail/notificação interna) para iniciar o
   desembaraço aduaneiro, disparado ao marcar desembarque como concluído (ou
   após X dias configuráveis).
3. Registrar data/hora do desembarque efetivo.

Modelo de dados sugerido: `FollowUp { ..., modulo='desembarque' }` +
`Lembrete { processo_id, tipo='inicio_desembaraco', enviado_em,
destinatario }`.

**Entregável**: tela/card do módulo, lógica de disparo do lembrete
(atraso/gatilho configurável) e template da notificação.

### Módulo 7: Desembaraço

**Objetivo**: desembaraço aduaneiro.

1. Campos obrigatórios: número da **DUIMP** (Declaração Única de
   Importação) e data da DUIMP.
2. Reutiliza Follow-up: botão "Checada OK" + comentário livre.
3. Envio automático de lembrete para iniciar a etapa de entrega, disparado
   quando o desembaraço for concluído.

Modelo de dados sugerido: `Desembaraco { processo_id, numero_duimp,
data_duimp }` + `FollowUp` + `Lembrete { tipo='entrega' }`.

**Entregável**: formulário com campos de DUIMP, componente de follow-up e
lógica de lembrete de entrega.

### Módulo 8: Transporte

**Objetivo**: transporte nacional/entrega final após o desembaraço.

1. Botão "Mercadoria Recebida" para marcar entrega concluída.
2. Comentário livre associado à entrega.
3. Upload de foto(s) para registrar divergências (múltiplas imagens).
4. Campo estruturado de divergências (texto livre, destacado visualmente
   quando preenchido — ex. badge "Divergência registrada").

Modelo de dados sugerido: `Entrega { processo_id, recebida (bool),
recebida_em, comentario, divergencias (texto, nullable), fotos[] (urls) }`.

**Entregável**: formulário de confirmação de recebimento com upload de
imagens, campo de divergências e indicador visual quando houver
divergência.

### Módulo 9: NF / Câmbio (módulo final)

**Objetivo**: geração de documento para NF/Câmbio.

1. Consolidar dados do processo completo (Ref FF, PO, cotação/frete
   escolhido, valores, DUIMP, datas de cada etapa) em documento consolidado
   para emissão de NF e/ou fechamento de câmbio. *Modelo de cálculo a ser
   enviado posteriormente.*
2. Botão "Gerar documento" → produz PDF ou planilha pronto para envio à
   contabilidade/instituição financeira.
3. Marcar processo como **"Concluído"** após gerar o documento, encerrando o
   fluxo.

Modelo de dados sugerido: reaproveita todos os dados já registrados nos
módulos anteriores via `processo_id`/Ref FF — camada de consolidação e
exportação, sem novos campos de entrada além de ajustes finais de valor
para câmbio.

**Entregável**: lógica de consolidação dos dados de todos os módulos,
geração do documento (PDF/planilha) e atualização do status final do
processo para "Concluído".

---

## Notas para quando a implementação começar

- Este documento é a fonte da verdade da especificação original — ao
  transformar isso em Skill/Subagent/outro mecanismo, preservar este arquivo
  como referência e linkar a partir da definição escolhida.
- Pontos em aberto a esclarecer com o solicitante antes de iniciar:
  - Definição completa dos prefixos de identificador (`IM` = Importação
    Marítima confirmado; `IA` citado mas não completado no texto original).
  - Se a "tabela única" deve ser literal (uma tabela SQL) ou se módulos como
    Cotação (que precisa de link público para fornecedores externos) devem
    ficar em tabela(s) satélite com FK para a tabela principal.
  - E-mail exato dos destinatários fixos em cópia no Módulo PO (grafia
    `filoli@colmac.com` vs `Filoli@colmac.com`).
  - Regra de disparo dos lembretes automáticos (imediato vs. "após X dias") —
    quem configura o X e onde.
  - Nomenclatura final dos botões de Follow-up por módulo ("Coleta OK" vs
    "Em Trânsito OK" vs "Checada OK" — o texto original alterna).
