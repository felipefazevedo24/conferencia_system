# POP - Fluxo Integrado de Recebimento

## Objetivo
Padronizar o processo de Pre-Nota, Auditoria XML, Conferencia e Documento de Entrada para reduzir retrabalho, acelerar liberacao e aumentar rastreabilidade.

## Escopo
- Recebimento de NF de entrada via XML.
- Triagem e auditoria fiscal-operacional.
- Conferencia fisica (cego).
- Lancamento do documento de entrada.

## Papéis e Responsabilidades
- Recebimento:
  - Importar XML e garantir dados minimos de pre-nota.
  - Tratar pendencias de cadastro (fornecedor, codigo, unidade).
- Auditor XML (Fiscal/Compras):
  - Analisar somente excecoes.
  - Aprovar, reprovar ou liberar com ressalva.
- Conferente:
  - Executar conferencia fisica e registrar divergencias com motivo.
- Fiscal:
  - Validar etapa final e efetuar lancamento.
- Lider/Coordenacao:
  - Monitorar SLA e fila priorizada de pendencias.

## Fluxo Operacional
1. Pre-Nota
- Entrada: XML importado.
- Status alvo: AguardandoLiberacao.
- Regras:
  - NF, fornecedor, itens e quantidades obrigatorios.
  - Identificar se possui pedido de compra vinculado.
  - Classificar marcacoes operacionais (remessa, material de cliente, sem conferencia logistica).

2. Auditoria XML
- Entrada: notas em AguardandoLiberacao.
- Saidas:
  - Aprovada: segue para conferencia.
  - Reprovada: volta para tratativa.
  - Com ressalva: segue com anotacao.
- Regra de ouro:
  - Auditor atua por excecao, nao por volume total.

3. Conferencia
- Entrada: notas em Pendente.
- Saidas:
  - Concluida (sem divergencia): segue para fiscal.
  - Concluida (com divergencia): abre tratativa e segue com registro.
- Controles:
  - Conferencia cega.
  - Motivo de divergencia obrigatorio.

4. Documento de Entrada
- Entrada: notas em Concluido.
- Saida: Lancado.
- Bloqueios:
  - Pendencia critica de auditoria sem decisao.
  - Falha de integracao obrigatoria.

## Regras de Prioridade (Fila)
Ordenar por maior impacto:
1. Nota com divergencia aberta.
2. Nota com auditoria pendente.
3. Nota sem pedido de compra (quando aplicavel).
4. Tempo em fila (SLA estourado).

## SLA Operacional Sugerido
- Pre-Nota ate Auditoria: 4h.
- Auditoria ate Liberacao de Conferencia: 8h.
- Conferencia ate Documento de Entrada: 12h.
- Tratativa de Excecao Critica: 24h.

## KPI Minimos
- Notas por etapa: Pre-Nota, Auditoria pendente, Conferencia, Documento de Entrada, Finalizado.
- Taxa sem intervencao: percentual de notas lancadas sem divergencia.
- Fila de excecao: volume e idade media.
- Top fornecedores criticos: por volume de divergencias.

## Rotina de Gestao Diaria (15 min)
1. Abrir painel de processo do recebimento.
2. Atacar top 10 da fila de excecao por score.
3. Redistribuir backlog por papel (recebimento, auditor, fiscal).
4. Registrar impedimentos do dia e plano de acao.

## Endpoint de Apoio
- Novo endpoint operacional:
  - GET /api/processo/recebimento_painel
  - Parametros:
    - dias (padrao 30)
    - limite_fila (padrao 30)
  - Retorna:
    - etapas
    - kpis
    - fila_excecao priorizada

## Checklist de Implantacao
- Treinar usuarios por papel.
- Definir dono do SLA por etapa.
- Rodar daily de 15 min por 2 semanas.
- Revisar regras e ajustar score de prioridade.
