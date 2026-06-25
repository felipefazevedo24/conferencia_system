"""Queries SQL centralizadas para o ERP CPS (PostgreSQL).

Usam diretamente as tabelas reais do ERP (tos, tlis_mat, tcom_aux_os,
tnota_fiscal_item, tproduto, tstat_os).
"""

# -------------------------------------------------------------------
# Lista materiais de uma OS espec?fica (filtro pelo n_os vis?vel)
# -------------------------------------------------------------------
SQL_MATERIAIS_POR_OS = """
WITH oc_sc AS (
    SELECT cod_empresa,
           cod_os,
           cod_os_aux,
           cod_produto,
           STRING_AGG(DISTINCT NULLIF(cod_ord_compra::text, '0'), ', ' ORDER BY NULLIF(cod_ord_compra::text, '0')) AS n_oc,
           STRING_AGG(DISTINCT NULLIF(cod_solicitacao::text, '0'), ', ' ORDER BY NULLIF(cod_solicitacao::text, '0')) AS n_sc
    FROM public.tcom_aux_os
    WHERE COALESCE(cancelado, 0) = 0
    GROUP BY cod_empresa, cod_os, cod_os_aux, cod_produto
),
orc_link AS (
    SELECT DISTINCT ON (sg.cod_empresa, sg.cod_os)
           sg.cod_empresa, sg.cod_os, sg.cod_orcamento
    FROM public.torcamento_servico_gerados sg
    WHERE COALESCE(sg.cancelado, 0) = 0
    ORDER BY sg.cod_empresa, sg.cod_os, sg.cod_orcamento DESC
)
SELECT
    os.cod_empresa,
    os.codigo AS cod_os,
    os.n_os AS n_os,
    lm.cod_os_completo AS cod_os_completo,
    os.titulo AS titulo_os,
    os.u_classificacao AS classificacao,
    st.nome AS status_os,
    lm.cod_os_aux,
    lm.cod_produto,
    p.codigo_interno AS cod_interno,
    COALESCE(p.nome, lm.produto) AS material_descricao,
    lm.unidade,
    lm.qtde AS qtde_necessaria,
    COALESCE(lm.qtde_solicitada, 0) AS qtde_solicitada,
    COALESCE(lm.qtde_reservada, 0) AS qtde_reservada,
    COALESCE(lm.qtde_entregue, 0) AS qtde_entregue,
    COALESCE(lm.qtde_utilizada, 0) AS qtde_utilizada,
    lm.dt_necessidade,
    p.tipo_producao AS metodo_reposicao,
    oc.n_oc,
    oc.n_sc,
    CASE
        WHEN COALESCE(lm.qtde_entregue,0) >= COALESCE(lm.qtde,0) THEN 'ENTREGUE'
        WHEN oc.n_oc IS NOT NULL THEN 'OC EMITIDA'
        WHEN oc.n_sc IS NOT NULL THEN 'SOLICITACAO'
        WHEN COALESCE(lm.qtde,0) - COALESCE(lm.qtde_entregue,0) <= COALESCE(p.estoque_disponivel_uso,0) THEN 'COBERTO ESTOQUE'
        WHEN COALESCE(lm.qtde_reservada,0) >= COALESCE(lm.qtde,0) - COALESCE(lm.qtde_entregue,0) THEN 'RESERVADO'
        ELSE 'A COMPRAR'
    END AS status_material,
    COALESCE(orc.dt_aprovacao, os.dt_aprovacao, os.dt_entrada)::timestamp AS engenharia_inicio,
    (COALESCE(orc.dt_aprovacao, os.dt_aprovacao, os.dt_entrada) + ((os.dt_prevista - COALESCE(orc.dt_aprovacao, os.dt_aprovacao, os.dt_entrada)) * 0.25))::timestamp AS engenharia_prazo,
    (COALESCE(orc.dt_aprovacao, os.dt_aprovacao, os.dt_entrada) + ((os.dt_prevista - COALESCE(orc.dt_aprovacao, os.dt_aprovacao, os.dt_entrada)) * 0.25))::timestamp AS compras_inicio,
    (COALESCE(orc.dt_aprovacao, os.dt_aprovacao, os.dt_entrada) + ((os.dt_prevista - COALESCE(orc.dt_aprovacao, os.dt_aprovacao, os.dt_entrada)) * 0.50))::timestamp AS compras_prazo,
    (COALESCE(orc.dt_aprovacao, os.dt_aprovacao, os.dt_entrada) + ((os.dt_prevista - COALESCE(orc.dt_aprovacao, os.dt_aprovacao, os.dt_entrada)) * 0.50))::timestamp AS producao_inicio,
    os.dt_prevista AS producao_prazo
FROM public.tos os
INNER JOIN public.tlis_mat lm ON lm.cod_empresa = os.cod_empresa AND lm.cod_os = os.codigo
LEFT JOIN public.tproduto p ON p.cod_empresa = lm.cod_empresa AND p.codigo = lm.cod_produto
LEFT JOIN public.tstat_os st ON st.cod_empresa = os.cod_empresa AND st.codigo = os.cod_status
LEFT JOIN oc_sc oc ON oc.cod_empresa = lm.cod_empresa AND oc.cod_os = lm.cod_os AND oc.cod_os_aux = lm.cod_os_aux AND oc.cod_produto = lm.cod_produto
LEFT JOIN orc_link ol ON ol.cod_empresa = os.cod_empresa AND ol.cod_os = os.codigo
LEFT JOIN public.torcamento orc ON orc.cod_empresa = os.cod_empresa AND orc.codigo = COALESCE(os.cod_orcamento, ol.cod_orcamento)
WHERE os.cod_empresa = %(cod_empresa)s
  AND (%(n_os_list)s::text[] IS NULL OR os.n_os = ANY(%(n_os_list)s::text[]))
  AND (%(classificacao)s::text IS NULL OR os.u_classificacao = %(classificacao)s::text)
  AND (CASE %(data_campo)s
            WHEN 'dt_aprovacao' THEN os.dt_aprovacao
            WHEN 'dt_prevista'  THEN os.dt_prevista
            ELSE os.dt_entrada
       END)::date BETWEEN COALESCE(%(data_de)s::date, '1900-01-01'::date)
                      AND COALESCE(%(data_ate)s::date, '9999-12-31'::date)
ORDER BY os.n_os, lm.cod_os_aux, p.codigo_interno
LIMIT %(limite)s;
"""

# -------------------------------------------------------------------
# Indicadores de GAP de compras (necessidade x em OC x recebido)
# -------------------------------------------------------------------
SQL_GAP_COMPRAS = """
WITH necessidade AS (
    SELECT lm.cod_empresa,
           lm.cod_produto,
           SUM(lm.qtde) AS qtd_necessaria,
           SUM(COALESCE(lm.qtde_solicitada, 0)) AS qtd_solicitada,
           SUM(COALESCE(lm.qtde_entregue, 0)) AS qtd_recebida_lm,
           STRING_AGG(DISTINCT os.u_classificacao, ', ' ORDER BY os.u_classificacao) AS classificacoes,
           STRING_AGG(DISTINCT os.n_os, ', ' ORDER BY os.n_os) AS os_lista,
           STRING_AGG(DISTINCT lm.cod_os_completo, ', ' ORDER BY lm.cod_os_completo) AS cod_os_completo_lista,
           COUNT(DISTINCT os.codigo) AS qtd_os
    FROM public.tlis_mat lm
    INNER JOIN public.tos os ON os.cod_empresa = lm.cod_empresa AND os.codigo = lm.cod_os
    WHERE os.cod_empresa = %(cod_empresa)s
      AND COALESCE(os.cancelado, 0) = 0
      AND COALESCE(os.concluido, 0) = 0
      AND (%(classificacao)s::text IS NULL OR os.u_classificacao = %(classificacao)s::text)
      AND (%(n_os_list)s::text[] IS NULL OR os.n_os = ANY(%(n_os_list)s::text[]))
      AND (CASE %(data_campo)s
                WHEN 'dt_aprovacao' THEN os.dt_aprovacao
                WHEN 'dt_prevista'  THEN os.dt_prevista
                ELSE os.dt_entrada
           END)::date BETWEEN COALESCE(%(data_de)s::date, '1900-01-01'::date)
                          AND COALESCE(%(data_ate)s::date, '9999-12-31'::date)
    GROUP BY lm.cod_empresa, lm.cod_produto
),
em_oc AS (
    SELECT c.cod_empresa,
           c.cod_produto,
           SUM(COALESCE(c.qtde, 0) - COALESCE(c.qtde_devolucao, 0)) AS qtd_em_oc
    FROM public.tcom_aux_os c
    WHERE c.cod_empresa = %(cod_empresa)s
    GROUP BY c.cod_empresa, c.cod_produto
),
recebido_nf AS (
    SELECT nfi.cod_empresa,
           nfi.cod_produto,
           SUM(COALESCE(nfi.qtde, 0)) AS qtd_recebida_nf
    FROM public.tnota_fiscal_item nfi
    WHERE nfi.cod_empresa = %(cod_empresa)s
      AND nfi.cod_ordem_compra IS NOT NULL
    GROUP BY nfi.cod_empresa, nfi.cod_produto
)
SELECT
    n.cod_empresa,
    n.cod_produto,
    p.codigo_interno AS cod_interno,
    p.nome AS material_descricao,
    p.metodo_reposicao AS metodo_reposicao,
    n.classificacoes AS classificacao,
    n.os_lista AS n_os,
    n.cod_os_completo_lista AS cod_os_completo,
    n.qtd_os,
    n.qtd_necessaria,
    n.qtd_solicitada,
    COALESCE(o.qtd_em_oc, 0) AS qtd_em_oc,
    COALESCE(r.qtd_recebida_nf, n.qtd_recebida_lm, 0) AS qtd_recebida,
    n.qtd_necessaria - COALESCE(o.qtd_em_oc, 0) - COALESCE(r.qtd_recebida_nf, n.qtd_recebida_lm, 0) AS gap
FROM necessidade n
LEFT JOIN em_oc o ON o.cod_empresa = n.cod_empresa AND o.cod_produto = n.cod_produto
LEFT JOIN recebido_nf r ON r.cod_empresa = n.cod_empresa AND r.cod_produto = n.cod_produto
LEFT JOIN public.tproduto p ON p.cod_empresa = n.cod_empresa AND p.codigo = n.cod_produto
WHERE (%(metodo)s::int IS NULL OR p.metodo_reposicao = %(metodo)s::int)
  AND n.qtd_necessaria - COALESCE(o.qtd_em_oc, 0) - COALESCE(r.qtd_recebida_nf, n.qtd_recebida_lm, 0) > 0
ORDER BY gap DESC NULLS LAST
LIMIT 1000;
"""

SQL_HEALTHCHECK = "SELECT 1 AS ok;"

SQL_CLASSIFICACOES = """
SELECT u_classificacao AS classificacao, COUNT(*) AS qtd
FROM public.tos
WHERE cod_empresa = %(cod_empresa)s
  AND u_classificacao IS NOT NULL
GROUP BY u_classificacao
ORDER BY qtd DESC;
"""


# -------------------------------------------------------------------
# Painel OS (PCP) — colunas conforme tela do ERP + planilha apiclumbia.xlsx.
# Campos sem fonte confirmada no schema CPS (possui_engenharia,
# engf1_finalizada, tp_dt_finalizacao, det_finalizado,
# status_disponibilidade) retornam NULL.
# Fases (engenharia/compras/producao) são derivadas por proporção do
# período entre dt_entrada e dt_prevista (25% / 50% / 100%).
# -------------------------------------------------------------------
SQL_OS_PAINEL = """
WITH tp AS (
    -- tos_aux agregado: tipo de molde, contagens de lista_materiais_ok
    -- (usado para 'Com lista' / 'sem lista') e processo_produtivo_ok
    -- (usado para distinguir 'sem processos' / 'não' em producao).
    SELECT cod_empresa, cod_os,
           STRING_AGG(DISTINCT sistema_molde, ', ' ORDER BY sistema_molde) AS tp_molde,
           MAX(cod_os_completo)                                            AS cod_os_completo,
           COUNT(*)                                                       AS n_aux,
           COUNT(*) FILTER (WHERE COALESCE(lista_materiais_ok,0)=1)       AS n_lm_ok,
           COUNT(*) FILTER (WHERE COALESCE(processo_produtivo_ok,0)=1)    AS n_pp_ok
    FROM   public.tos_aux
    GROUP  BY cod_empresa, cod_os
),
pp AS (
    -- Processos produtivos cadastrados (tpro_pro): existencia e
    -- finalizacao de todos. Usado para producao_finalizada='sim'.
    SELECT cod_empresa, cod_os,
           COUNT(*)                                           AS n_pp,
           BOOL_AND(COALESCE(finalizado,0)=1)                 AS pp_all_fin
    FROM   public.tpro_pro
    GROUP  BY cod_empresa, cod_os
),
oc AS (
    SELECT cod_empresa, cod_os,
           COUNT(*) FILTER (WHERE COALESCE(cancelado,0)=0)             AS qtd_oc_ativas,
           COUNT(*) FILTER (WHERE COALESCE(cod_ord_compra,0)<>0
                            AND COALESCE(cancelado,0)=0)               AS qtd_oc_emitida
    FROM   public.tcom_aux_os
    GROUP  BY cod_empresa, cod_os
),
disp AS (
    -- status_disponibilidade: checa se o estoque disponivel de cada item
    -- da lista de materiais cobre a necessidade liquida (qtde - entregue -
    -- compra ja contratada). Quando TODO item esta coberto, o ERP entende
    -- que nao precisa gerar OC para essa OS.
    -- Aderencia observada: 73 pct das OS DISPONIVEL nao tem OC ativa.
    SELECT lm.cod_empresa, lm.cod_os,
           COUNT(*)                                              AS n_itens_lm,
           COUNT(*) FILTER (
               WHERE COALESCE(lm.qtde,0) - COALESCE(lm.qtde_entregue,0)
                                         - COALESCE(lm.qtde_compra,0)
                     > COALESCE(p.estoque_disponivel_uso,0)
           )                                                     AS n_itens_descobertos
    FROM   public.tlis_mat lm
    LEFT   JOIN public.tproduto p
           ON p.cod_empresa = lm.cod_empresa
          AND p.codigo_interno = lm.cod_interno
    GROUP  BY lm.cod_empresa, lm.cod_os
),
-- Liga OS ao orçamento que a gerou (cod_orcamento aponta para torcamento.codigo).
-- Quando 1 OS é gerada por mais de 1 orçamento (raro), pega o mais recente aprovado.
orc_link AS (
    SELECT DISTINCT ON (sg.cod_empresa, sg.cod_os)
           sg.cod_empresa, sg.cod_os, sg.cod_orcamento
    FROM   public.torcamento_servico_gerados sg
    WHERE  COALESCE(sg.cancelado, 0) = 0
    ORDER  BY sg.cod_empresa, sg.cod_os, sg.cod_orcamento DESC
)
SELECT
    os.cod_empresa,
    os.codigo                                            AS cod_os,
    os.dt_entrada,
    os.n_os,
    tp.cod_os_completo                                   AS cod_os_completo,
    os.titulo,
    COALESCE(os.n_orcamento, orc.n_orcamento)            AS n_orcamento,
    COALESCE(NULLIF(os.versao_orcamento, ''), orc.versao) AS versao,
    os.cliente,
    COALESCE(os.dt_aprovacao, orc.dt_aprovacao)          AS dt_aprovacao,
    orc.dt_previsao_entrega                              AS dt_previsao_entrega,
    os.dt_prevista,
    NULL::timestamp                                      AS prazo_entrega,
    os.dt_inicio_mais_cedo                               AS dt_inicio,
    EXTRACT(WEEK FROM os.dt_entrada)::int                AS semana_os,
    os.classificacao,
    os.u_classificacao,
    os.u_classificacao_ii,
    os.u_complexibilidade_do_pro,
    os.u_complexibilidade_produc,
    NULL::text                                           AS possui_engenharia,
    NULL::text                                           AS engf1_finalizada,
    NULL::timestamp                                      AS tp_dt_finalizacao,
    tp.tp_molde,
    NULL::text                                           AS det_finalizado,
    -- 'Com lista' quando algum tos_aux tem lista_materiais_ok=1
    -- (mesma semantica observada na planilha apiclumbia, ~87 pct de aderencia).
    CASE WHEN COALESCE(tp.n_lm_ok, 0) > 0 THEN 'Com lista'
         ELSE 'sem lista'
    END                                                  AS status_lis_material,
    -- Mantida a regra atual (EMITIDA/SEM OC) ate que a logica de
    -- 'Sem necessidade de compra' / 'Todos com OC' do gerador externo
    -- seja decifrada. Investigacao em aberto.
    CASE WHEN oc.qtd_oc_emitida > 0 THEN 'EMITIDA'
         WHEN oc.qtd_oc_ativas  > 0 THEN 'EM ABERTO'
         ELSE 'SEM OC'
    END                                                  AS status_ordem_compra,
    os.status_servico                                    AS status_processamento,
    -- status_disponibilidade: 'DISPONIVEL' quando o estoque cobre todos os
    -- itens da lista; 'INDISPONIVEL' caso falte algum; NULL quando nao ha
    -- lista de materiais cadastrada (logo, sem como avaliar).
    CASE WHEN COALESCE(disp.n_itens_lm,0) = 0           THEN NULL
         WHEN COALESCE(disp.n_itens_descobertos,0) = 0  THEN 'DISPONIVEL'
         ELSE 'INDISPONIVEL'
    END                                                  AS status_disponibilidade,
    -- perc_real: tkpi_os.pcp_serv_percentual ja vem em base 100;
    -- convertemos para fracao (0..1) como na planilha apiclumbia.
    ROUND((kpi.pcp_serv_percentual / 100.0)::numeric, 4) AS perc_real,
    -- producao_finalizada em 3 niveis (mesma nomenclatura do apiclumbia):
    --   'sim'           : OS concluida OU todos tpro_pro finalizados.
    --   'sem processos' : sem nenhum apontamento real (pcp_serv_percentual nulo ou 0).
    --   'n\u00e3o'           : caso contrario (em andamento, ja apontou algo).
    -- Acerto vs xls = 85 pct (sim=100 pct, sem processos=~80 pct).
    CASE WHEN COALESCE(os.concluido,0) = 1
           OR (COALESCE(pp.n_pp,0) > 0 AND pp.pp_all_fin)            THEN 'sim'
         WHEN COALESCE(kpi.pcp_serv_percentual, 0) = 0               THEN 'sem processos'
         ELSE 'não'
    END                                                  AS producao_finalizada,
    kpi.pcp_os_dt_primeiro_apont                         AS primeiro_apontamento,
    kpi.pcp_os_dt_ultimo_apont                           AS ultimo_apontamento,
    CASE WHEN os.dt_prevista IS NOT NULL AND os.dt_entrada IS NOT NULL
         THEN ROUND(EXTRACT(EPOCH FROM (os.dt_prevista - os.dt_entrada))::numeric / 604800.0, 2)
    END                                                  AS prazo_semanas,
    -- origem: identificador do orçamento que originou a OS.
    CASE WHEN COALESCE(os.n_orcamento, orc.n_orcamento) IS NOT NULL
         THEN 'ORÇ.: ' || COALESCE(os.n_orcamento, orc.n_orcamento)::text
    END                                                  AS origem,
    -- Fases derivadas: 25%% engenharia / 25%% compras / 50%% producao do
    -- intervalo entre a aprovacao do orcamento (ou criacao da OS, quando
    -- nao ha orcamento) e a previsao de entrega (os.dt_prevista).
    COALESCE(orc.dt_aprovacao, os.dt_aprovacao, os.dt_entrada)::timestamp AS engenharia_inicio,
    (COALESCE(orc.dt_aprovacao, os.dt_aprovacao, os.dt_entrada)
        + ((os.dt_prevista - COALESCE(orc.dt_aprovacao, os.dt_aprovacao, os.dt_entrada)) * 0.25))::timestamp AS engenharia_prazo,
    (COALESCE(orc.dt_aprovacao, os.dt_aprovacao, os.dt_entrada)
        + ((os.dt_prevista - COALESCE(orc.dt_aprovacao, os.dt_aprovacao, os.dt_entrada)) * 0.25))::timestamp AS compras_inicio,
    (COALESCE(orc.dt_aprovacao, os.dt_aprovacao, os.dt_entrada)
        + ((os.dt_prevista - COALESCE(orc.dt_aprovacao, os.dt_aprovacao, os.dt_entrada)) * 0.50))::timestamp AS compras_prazo,
    (COALESCE(orc.dt_aprovacao, os.dt_aprovacao, os.dt_entrada)
        + ((os.dt_prevista - COALESCE(orc.dt_aprovacao, os.dt_aprovacao, os.dt_entrada)) * 0.50))::timestamp AS producao_inicio,
    os.dt_prevista                                       AS producao_prazo
FROM       public.tos os
LEFT JOIN  orc_link ol ON ol.cod_empresa = os.cod_empresa AND ol.cod_os = os.codigo
LEFT JOIN  public.torcamento orc
        ON orc.cod_empresa = os.cod_empresa
       AND orc.codigo      = COALESCE(os.cod_orcamento, ol.cod_orcamento)
LEFT JOIN  tp ON tp.cod_empresa = os.cod_empresa AND tp.cod_os = os.codigo
LEFT JOIN  pp ON pp.cod_empresa = os.cod_empresa AND pp.cod_os = os.codigo
LEFT JOIN  oc ON oc.cod_empresa = os.cod_empresa AND oc.cod_os = os.codigo
LEFT JOIN  disp ON disp.cod_empresa = os.cod_empresa AND disp.cod_os = os.codigo
LEFT JOIN  public.tkpi_os kpi
        ON kpi.cod_empresa = os.cod_empresa AND kpi.cod_os = os.codigo
WHERE  os.cod_empresa = %(cod_empresa)s
  AND  COALESCE(os.cancelado, 0) = 0
  AND  (%(somente_abertas)s::int = 0 OR COALESCE(os.concluido, 0) = 0)
  AND  (%(classificacao)s::text IS NULL OR os.u_classificacao = %(classificacao)s::text)
  AND  (%(n_os_list)s::text[] IS NULL OR os.n_os = ANY(%(n_os_list)s::text[]))
  AND  (CASE %(data_campo)s
            WHEN 'dt_aprovacao' THEN COALESCE(os.dt_aprovacao, orc.dt_aprovacao)
            WHEN 'dt_prevista'  THEN os.dt_prevista
            ELSE os.dt_entrada
        END)::date BETWEEN COALESCE(%(data_de)s::date,  '1900-01-01'::date)
                       AND COALESCE(%(data_ate)s::date, '9999-12-31'::date)
ORDER BY os.dt_entrada DESC NULLS LAST, os.n_os
LIMIT %(limite)s;
"""


# -------------------------------------------------------------------
# Historico de ordens de compra (abertas e encerradas)
# - Header: 1 linha por cod_ordem_compra
# - Itens: detalhe por OS/produto da OC
# -------------------------------------------------------------------
SQL_HIST_OC_HEADER = """
WITH compras_ref AS (
    SELECT DISTINCT ON (c.cod_empresa, c.cod_ordem_compra)
           c.cod_empresa,
           c.cod_ordem_compra,
           c.codigo                                AS cod_compra,
           c.dt_lancamento,
           c.dt_recebimento,
           c.status                                AS cod_status_oc,
           c.fornecedor,
           c.total_produtos,
           c.total,
           c.comprador
    FROM   public.tcompras c
    WHERE  c.cod_empresa = %(cod_empresa)s
      AND  COALESCE(c.cod_ordem_compra, 0) <> 0
    ORDER  BY c.cod_empresa, c.cod_ordem_compra,
              COALESCE(c.dt_lancamento, c.dt_recebimento) DESC NULLS LAST,
              c.codigo DESC
),
base AS (
    SELECT
        ao.cod_empresa,
        ao.cod_ord_compra                          AS cod_ordem_compra,
        os.u_classificacao                         AS classificacao,
        os.n_os                                    AS n_os,
        ao.cod_produto,
        COALESCE(ao.qtde, 0)                       AS qtde,
        COALESCE(ao.qtde_devolucao, 0)             AS qtde_devolucao,
        cr.cod_compra,
        cr.dt_lancamento,
        cr.dt_recebimento,
        COALESCE(
            cr.dt_lancamento::date,
            (CASE %(data_campo)s
                WHEN 'dt_aprovacao' THEN os.dt_aprovacao
                WHEN 'dt_prevista'  THEN os.dt_prevista
                ELSE os.dt_entrada
            END)::date
        )                                           AS data_referencia,
        cr.cod_status_oc,
        cr.fornecedor,
        cr.total_produtos,
        cr.total,
        cr.comprador,
        st.nome                                    AS status_oc_nome,
        CASE
            WHEN cr.dt_recebimento IS NOT NULL THEN 'ENCERRADA'
            WHEN COALESCE(st.nome, '') ~* '(ENCERR|CONCL|FECH|FINAL|RECEB)' THEN 'ENCERRADA'
            ELSE 'ABERTA'
        END                                        AS situacao_oc
    FROM   public.tcom_aux_os ao
    LEFT   JOIN public.tos os
           ON os.cod_empresa = ao.cod_empresa
          AND os.codigo      = ao.cod_os
    LEFT   JOIN compras_ref cr
           ON cr.cod_empresa      = ao.cod_empresa
          AND cr.cod_ordem_compra = ao.cod_ord_compra
    LEFT   JOIN public.tstat_oc st
           ON st.cod_empresa = cr.cod_empresa
          AND st.codigo      = cr.cod_status_oc
    WHERE  ao.cod_empresa = %(cod_empresa)s
      AND  COALESCE(ao.cancelado, 0) = 0
      AND  COALESCE(ao.cod_ord_compra, 0) <> 0
      AND  (%(classificacao)s::text IS NULL OR os.u_classificacao = %(classificacao)s::text)
      AND  (%(n_os_list)s::text[] IS NULL OR os.n_os = ANY(%(n_os_list)s::text[]))
      AND  (
            (%(data_de)s::date IS NULL AND %(data_ate)s::date IS NULL)
            OR COALESCE(
                cr.dt_lancamento::date,
                (CASE %(data_campo)s
                    WHEN 'dt_aprovacao' THEN os.dt_aprovacao
                    WHEN 'dt_prevista'  THEN os.dt_prevista
                    ELSE os.dt_entrada
                END)::date
            ) BETWEEN COALESCE(%(data_de)s::date,  '1900-01-01'::date)
                AND COALESCE(%(data_ate)s::date, '9999-12-31'::date)
      )
)
SELECT
    cod_empresa,
    cod_ordem_compra,
    MAX(cod_compra)                                 AS cod_compra,
    MAX(dt_lancamento)                              AS dt_lancamento,
    MAX(dt_recebimento)                             AS dt_recebimento,
    MIN(data_referencia)                            AS data_referencia,
    MAX(cod_status_oc)                              AS cod_status_oc,
    MAX(status_oc_nome)                             AS status_oc_nome,
    MAX(situacao_oc)                                AS situacao_oc,
    MAX(fornecedor)                                 AS fornecedor,
    MAX(comprador)                                  AS comprador,
    MAX(total_produtos)                             AS total_produtos,
    MAX(total)                                      AS total,
    COUNT(*)                                        AS qtd_linhas,
    COUNT(DISTINCT cod_produto)                     AS qtd_produtos,
    COUNT(DISTINCT n_os)                            AS qtd_os,
    SUM(qtde)                                       AS qtd_bruta,
    SUM(qtde - qtde_devolucao)                      AS qtd_liquida
FROM base
WHERE (
    %(situacao)s::text IS NULL
    OR %(situacao)s::text = 'todas'
    OR (%(situacao)s::text = 'abertas'   AND situacao_oc = 'ABERTA')
    OR (%(situacao)s::text = 'encerradas' AND situacao_oc = 'ENCERRADA')
)
GROUP BY cod_empresa, cod_ordem_compra
ORDER BY COALESCE(MAX(dt_lancamento), MAX(dt_recebimento)) DESC NULLS LAST, cod_ordem_compra DESC
LIMIT %(limite)s;
"""


SQL_HIST_OC_ITENS = """
WITH compras_ref AS (
    SELECT DISTINCT ON (c.cod_empresa, c.cod_ordem_compra)
           c.cod_empresa,
           c.cod_ordem_compra,
           c.codigo                                AS cod_compra,
           c.dt_lancamento,
           c.dt_recebimento,
           c.status                                AS cod_status_oc,
           c.fornecedor,
           c.comprador
    FROM   public.tcompras c
    WHERE  c.cod_empresa = %(cod_empresa)s
      AND  COALESCE(c.cod_ordem_compra, 0) <> 0
    ORDER  BY c.cod_empresa, c.cod_ordem_compra,
              COALESCE(c.dt_lancamento, c.dt_recebimento) DESC NULLS LAST,
              c.codigo DESC
)
SELECT
    ao.cod_empresa,
    ao.cod_ord_compra                              AS cod_ordem_compra,
    cr.cod_compra,
    cr.dt_lancamento,
    cr.dt_recebimento,
    COALESCE(
        cr.dt_lancamento::date,
        (CASE %(data_campo)s
            WHEN 'dt_aprovacao' THEN os.dt_aprovacao
            WHEN 'dt_prevista'  THEN os.dt_prevista
            ELSE os.dt_entrada
        END)::date
    )                                              AS data_referencia,
    cr.cod_status_oc,
    st.nome                                        AS status_oc_nome,
    CASE
        WHEN cr.dt_recebimento IS NOT NULL THEN 'ENCERRADA'
        WHEN COALESCE(st.nome, '') ~* '(ENCERR|CONCL|FECH|FINAL|RECEB)' THEN 'ENCERRADA'
        ELSE 'ABERTA'
    END                                            AS situacao_oc,
    os.n_os,
    ao.cod_os_completo,
    os.u_classificacao                             AS classificacao,
    ao.cod_os,
    ao.cod_os_aux,
    ao.cod_produto,
    p.codigo_interno                               AS cod_interno,
    COALESCE(p.nome, lm.produto)                   AS material_descricao,
    COALESCE(ao.qtde, 0)                           AS qtde,
    COALESCE(ao.qtde_devolucao, 0)                 AS qtde_devolucao,
    COALESCE(ao.qtde, 0) - COALESCE(ao.qtde_devolucao, 0) AS qtde_liquida,
    cr.fornecedor,
    cr.comprador
FROM   public.tcom_aux_os ao
LEFT   JOIN public.tos os
       ON os.cod_empresa = ao.cod_empresa
      AND os.codigo      = ao.cod_os
LEFT   JOIN public.tlis_mat lm
       ON lm.cod_empresa = ao.cod_empresa
      AND lm.cod_os      = ao.cod_os
      AND lm.cod_os_aux  = ao.cod_os_aux
      AND lm.cod_produto = ao.cod_produto
LEFT   JOIN public.tproduto p
       ON p.cod_empresa  = ao.cod_empresa
      AND p.codigo       = ao.cod_produto
LEFT   JOIN compras_ref cr
       ON cr.cod_empresa      = ao.cod_empresa
      AND cr.cod_ordem_compra = ao.cod_ord_compra
LEFT   JOIN public.tstat_oc st
       ON st.cod_empresa = cr.cod_empresa
      AND st.codigo      = cr.cod_status_oc
WHERE  ao.cod_empresa = %(cod_empresa)s
  AND  COALESCE(ao.cancelado, 0) = 0
  AND  COALESCE(ao.cod_ord_compra, 0) <> 0
  AND  (%(classificacao)s::text IS NULL OR os.u_classificacao = %(classificacao)s::text)
  AND  (%(n_os_list)s::text[] IS NULL OR os.n_os = ANY(%(n_os_list)s::text[]))
    AND  (
                (%(data_de)s::date IS NULL AND %(data_ate)s::date IS NULL)
            OR COALESCE(
                cr.dt_lancamento::date,
                (CASE %(data_campo)s
                    WHEN 'dt_aprovacao' THEN os.dt_aprovacao
                    WHEN 'dt_prevista'  THEN os.dt_prevista
                    ELSE os.dt_entrada
                END)::date
            ) BETWEEN COALESCE(%(data_de)s::date,  '1900-01-01'::date)
                AND COALESCE(%(data_ate)s::date, '9999-12-31'::date)
    )
  AND (
    %(situacao)s::text IS NULL
    OR %(situacao)s::text = 'todas'
    OR (%(situacao)s::text = 'abertas'   AND (CASE
        WHEN cr.dt_recebimento IS NOT NULL THEN 'ENCERRADA'
        WHEN COALESCE(st.nome, '') ~* '(ENCERR|CONCL|FECH|FINAL|RECEB)' THEN 'ENCERRADA'
        ELSE 'ABERTA'
    END) = 'ABERTA')
    OR (%(situacao)s::text = 'encerradas' AND (CASE
        WHEN cr.dt_recebimento IS NOT NULL THEN 'ENCERRADA'
        WHEN COALESCE(st.nome, '') ~* '(ENCERR|CONCL|FECH|FINAL|RECEB)' THEN 'ENCERRADA'
        ELSE 'ABERTA'
    END) = 'ENCERRADA')
  )
ORDER BY COALESCE(cr.dt_lancamento, cr.dt_recebimento) DESC NULLS LAST,
         ao.cod_ord_compra DESC,
         os.n_os,
         p.codigo_interno
LIMIT %(limite)s;
"""


# -------------------------------------------------------------------
# Visibility (compras)
# - Header: solicitação, ordem de compra e status do fluxo
# - Detalhada: itens ainda "A COMPRAR"
#
# Tabelas-base avaliadas e usadas por aderência ao objetivo:
# - tcom_aux_os: vínculo SC/OC por OS/produto (fonte principal de SC/OC)
# - tos: contexto da OS (n_os/classificação e filtro de período)
# - tcompras + tstat_oc: status operacional da OC
# - tlis_mat + tproduto: item de material e estoque para detectar "A COMPRAR"
# -------------------------------------------------------------------
SQL_VISIBILITY_HEADER = """
WITH compras_ref AS (
    SELECT DISTINCT ON (c.cod_empresa, c.cod_ordem_compra)
           c.cod_empresa,
           c.cod_ordem_compra,
           c.codigo                                AS cod_compra,
           c.dt_lancamento,
           c.dt_recebimento,
           c.status                                AS cod_status_oc,
           c.fornecedor
    FROM   public.tcompras c
    WHERE  c.cod_empresa = %(cod_empresa)s
      AND  COALESCE(c.cod_ordem_compra, 0) <> 0
    ORDER  BY c.cod_empresa, c.cod_ordem_compra,
              COALESCE(c.dt_lancamento, c.dt_recebimento) DESC NULLS LAST,
              c.codigo DESC
), sc_aux AS (
    SELECT
        ao.cod_empresa,
        NULLIF(ao.cod_solicitacao, 0)               AS cod_solicitacao,
        ao.cod_produto,
        NULLIF(ao.cod_ord_compra, 0)                AS cod_ordem_compra,
        ao.cod_os
    FROM   public.tcom_aux_os ao
    WHERE  ao.cod_empresa = %(cod_empresa)s
      AND  COALESCE(ao.cancelado, 0) = 0
      AND  COALESCE(ao.cod_solicitacao, 0) <> 0
        UNION
        SELECT
                sm.cod_empresa,
                sm.cod_solicitacao,
                sm.cod_produto,
                NULL::int                                   AS cod_ordem_compra,
                NULL::int                                   AS cod_os
        FROM   public.tsol_max sm
        WHERE  sm.cod_empresa = %(cod_empresa)s
            AND  COALESCE(sm.cancelado, 0) = 0
            AND  NOT EXISTS (
                        SELECT 1
                        FROM   public.tcom_aux_os ao2
                        WHERE  ao2.cod_empresa = sm.cod_empresa
                            AND  COALESCE(ao2.cancelado, 0) = 0
                            AND  NULLIF(ao2.cod_solicitacao, 0) = sm.cod_solicitacao
                            AND  ao2.cod_produto = sm.cod_produto
            )
), sc_base AS (
    SELECT
        sa.cod_empresa,
        sa.cod_solicitacao,
        sa.cod_produto,
        sa.cod_ordem_compra,
        sa.cod_os,
        NULLIF(TRIM(sm.cod_interno), '')            AS cod_interno,
        sm.dt_solicitacao::date                     AS dt_solicitacao,
        sm.dt_necessidade::date                     AS dt_necessidade,
        UPPER(COALESCE(NULLIF(TRIM(sm.situacao), ''), 'NAO INFORMADA')) AS situacao_sc,
        CASE
            WHEN UPPER(COALESCE(NULLIF(TRIM(sm.situacao), ''), '')) IN ('PENDENTE', 'ABERTA', 'EM ABERTO', 'ABERTO') THEN 1
            ELSE 0
        END                                         AS situacao_sc_aberta
    FROM   sc_aux sa
    LEFT   JOIN public.tsol_max sm
           ON sm.cod_empresa     = sa.cod_empresa
          AND sm.cod_solicitacao = sa.cod_solicitacao
          AND sm.cod_produto     = sa.cod_produto
          AND COALESCE(sm.cancelado, 0) = 0
), base AS (
    SELECT
        sc.cod_empresa,
        sc.cod_solicitacao,
        sc.cod_ordem_compra,
        sc.cod_interno,
        sc.dt_solicitacao,
        sc.dt_necessidade,
        os.n_os,
        os.u_classificacao                         AS classificacao,
        sc.cod_produto,
        sc.situacao_sc,
        sc.situacao_sc_aberta,
        cr.cod_compra,
        cr.dt_lancamento,
        cr.dt_recebimento,
        cr.cod_status_oc,
        st.nome                                    AS status_oc_nome,
        cr.fornecedor,
        CASE
            WHEN sc.situacao_sc_aberta = 1 THEN 'SOLICITACAO ABERTA'
            WHEN sc.cod_ordem_compra IS NOT NULL THEN
                CASE
                    WHEN cr.dt_recebimento IS NOT NULL THEN 'ENCERRADA'
                    WHEN COALESCE(st.nome, '') ~* '(ENCERR|CONCL|FECH|FINAL|RECEB)' THEN 'ENCERRADA'
                    ELSE 'OC EMITIDA'
                END
            ELSE 'SOLICITACAO ENCERRADA'
        END                                         AS status_fluxo,
        COALESCE(
            sc.dt_solicitacao,
            (CASE %(data_campo)s
                WHEN 'dt_aprovacao' THEN os.dt_aprovacao
                WHEN 'dt_prevista'  THEN os.dt_prevista
                ELSE os.dt_entrada
            END)::date
        )                                           AS data_referencia
    FROM   sc_base sc
    LEFT   JOIN public.tos os
             ON os.cod_empresa = sc.cod_empresa
            AND os.codigo      = sc.cod_os
    LEFT   JOIN compras_ref cr
             ON cr.cod_empresa      = sc.cod_empresa
            AND cr.cod_ordem_compra = sc.cod_ordem_compra
    LEFT   JOIN public.tstat_oc st
           ON st.cod_empresa = cr.cod_empresa
          AND st.codigo      = cr.cod_status_oc
    WHERE  (%(classificacao)s::text IS NULL OR os.u_classificacao = %(classificacao)s::text)
      AND  (%(n_os_list)s::text[] IS NULL OR os.n_os = ANY(%(n_os_list)s::text[]))
)
SELECT
    cod_empresa,
    cod_solicitacao,
    cod_ordem_compra,
    MIN(dt_solicitacao)                             AS dt_solicitacao,
    MIN(dt_necessidade)                             AS dt_necessidade,
    CASE
        WHEN cod_solicitacao IS NOT NULL AND MAX(situacao_sc_aberta) = 1
            THEN (CURRENT_DATE - MIN(COALESCE(dt_solicitacao, data_referencia)))::int
        ELSE NULL
    END                                             AS dias_em_aberto,
    MAX(cod_compra)                                 AS cod_compra,
    MAX(dt_lancamento)                              AS dt_lancamento,
    MAX(dt_recebimento)                             AS dt_recebimento,
    MAX(cod_status_oc)                              AS cod_status_oc,
    MAX(status_oc_nome)                             AS status_oc_nome,
    MAX(situacao_sc)                                AS situacao_solicitacao,
    MAX(situacao_sc_aberta)                         AS solicitacao_aberta,
    MAX(status_fluxo)                               AS status_fluxo,
    MAX(fornecedor)                                 AS fornecedor,
    STRING_AGG(DISTINCT n_os, ', ' ORDER BY n_os)  AS n_os_lista,
    STRING_AGG(DISTINCT classificacao, ', ' ORDER BY classificacao) AS classificacao_lista,
    STRING_AGG(DISTINCT cod_interno::text, ', ' ORDER BY cod_interno::text) AS cod_interno_lista,
    COUNT(*)                                        AS qtd_linhas,
    COUNT(DISTINCT cod_produto)                     AS qtd_produtos,
    COUNT(*) FILTER (WHERE situacao_sc = 'PENDENTE') AS qtd_itens_pendentes
FROM base
WHERE (
    %(somente_sc_sem_oc)s::int = 0
    OR (cod_solicitacao IS NOT NULL AND cod_ordem_compra IS NULL)
)
  AND COALESCE(data_referencia, dt_solicitacao)
      BETWEEN COALESCE(%(data_de)s::date,  '1900-01-01'::date)
          AND COALESCE(%(data_ate)s::date, '9999-12-31'::date)
GROUP BY cod_empresa, cod_solicitacao, cod_ordem_compra
ORDER BY COALESCE(MAX(dt_lancamento), MAX(dt_recebimento)) DESC NULLS LAST,
         cod_ordem_compra DESC NULLS LAST,
         cod_solicitacao DESC NULLS LAST
LIMIT %(limite)s;
"""


SQL_VISIBILITY_DETALHADA = """
WITH oc_sc AS (
    SELECT cod_empresa,
           cod_os,
           cod_os_aux,
           cod_produto,
           STRING_AGG(DISTINCT NULLIF(cod_ord_compra::text, '0'), ', '
                      ORDER BY NULLIF(cod_ord_compra::text, '0')) AS n_oc,
           STRING_AGG(DISTINCT NULLIF(cod_solicitacao::text, '0'), ', '
                      ORDER BY NULLIF(cod_solicitacao::text, '0')) AS n_sc
    FROM   public.tcom_aux_os
    WHERE  COALESCE(cancelado, 0) = 0
    GROUP  BY cod_empresa, cod_os, cod_os_aux, cod_produto
), base AS (
    SELECT
        os.cod_empresa,
        os.n_os,
        os.u_classificacao                           AS classificacao,
        lm.cod_os_completo,
        lm.cod_os_aux,
        lm.cod_produto,
        p.codigo_interno                             AS cod_interno,
        COALESCE(p.nome, lm.produto)                 AS material_descricao,
        lm.unidade,
        COALESCE(lm.qtde, 0)                         AS qtde_necessaria,
        COALESCE(lm.qtde_entregue, 0)                AS qtde_entregue,
        (COALESCE(lm.qtde, 0) - COALESCE(lm.qtde_entregue, 0)) AS qtde_pendente,
        COALESCE(p.estoque_disponivel_uso, 0)        AS estoque_disponivel_uso,
        oc.n_sc,
        oc.n_oc,
        CASE
            WHEN COALESCE(lm.qtde_entregue,0) >= COALESCE(lm.qtde,0)
                 THEN 'ENTREGUE'
            WHEN oc.n_oc IS NOT NULL
                 THEN 'OC EMITIDA'
            WHEN oc.n_sc IS NOT NULL
                 THEN 'SOLICITACAO'
            WHEN COALESCE(lm.qtde,0) - COALESCE(lm.qtde_entregue,0)
                 <= COALESCE(p.estoque_disponivel_uso,0)
                 THEN 'COBERTO ESTOQUE'
            WHEN COALESCE(lm.qtde_reservada,0)
                 >= COALESCE(lm.qtde,0) - COALESCE(lm.qtde_entregue,0)
                 THEN 'RESERVADO'
            ELSE 'A COMPRAR'
        END                                          AS status_material,
        (CASE %(data_campo)s
            WHEN 'dt_aprovacao' THEN os.dt_aprovacao
            WHEN 'dt_prevista'  THEN os.dt_prevista
            ELSE os.dt_entrada
        END)::date                                   AS data_referencia
    FROM       public.tlis_mat lm
    INNER JOIN public.tos os
            ON os.cod_empresa = lm.cod_empresa
           AND os.codigo      = lm.cod_os
    LEFT  JOIN public.tproduto p
            ON p.cod_empresa  = lm.cod_empresa
           AND p.codigo       = lm.cod_produto
    LEFT  JOIN oc_sc           oc
            ON oc.cod_empresa = lm.cod_empresa
           AND oc.cod_os      = lm.cod_os
           AND oc.cod_os_aux  = lm.cod_os_aux
           AND oc.cod_produto = lm.cod_produto
    WHERE  os.cod_empresa = %(cod_empresa)s
      AND  COALESCE(os.cancelado, 0) = 0
      AND  COALESCE(os.concluido, 0) = 0
      AND  (%(classificacao)s::text IS NULL OR os.u_classificacao = %(classificacao)s::text)
      AND  (%(n_os_list)s::text[] IS NULL OR os.n_os = ANY(%(n_os_list)s::text[]))
      AND  (CASE %(data_campo)s
                WHEN 'dt_aprovacao' THEN os.dt_aprovacao
                WHEN 'dt_prevista'  THEN os.dt_prevista
                ELSE os.dt_entrada
            END)::date BETWEEN COALESCE(%(data_de)s::date,  '1900-01-01'::date)
                           AND COALESCE(%(data_ate)s::date, '9999-12-31'::date)
)
SELECT *
FROM base
WHERE status_material = 'A COMPRAR'
ORDER BY qtde_pendente DESC, n_os, cod_os_completo, cod_interno
LIMIT %(limite)s;
"""


# -------------------------------------------------------------------
# Spend baseline (entrada por CNPJ)
# - Base principal: tcompras
# - Consolidação mensal e anual por CNPJ
# - Segmentação por tipo (SERVICO/PRODUTO)
# - Campos sem origem em tcompras retornam nulos padronizados
# -------------------------------------------------------------------
SQL_SPEND_BASELINE = """
WITH base AS (
    SELECT
        tc.cod_empresa,
        COALESCE(tc.dt_recebimento::date, tc.dt_nf::date, tc.dt_emissao::date) AS data_entrada,
        CASE
            WHEN COALESCE(tc.chv_nfe, '') ~ '^[0-9]{44}$'
                THEN SUBSTRING(tc.chv_nfe FROM 7 FOR 14)
            ELSE 'SEM_CNPJ'
        END AS cnpj,
        NULL::text AS cnpj_destinatario,
        NULLIF(TRIM(tc.fornecedor), '') AS fornecedor,
        CASE
            WHEN COALESCE(tc.vl_servicos, 0) <> 0 THEN 'SERVICO'
            ELSE 'PRODUTO'
        END AS tipo_item,
        NULL::text AS classificacao_item,
        NULLIF(TRIM(tc.cfop), '') AS cfop_entrada,
        NULLIF(TRIM(tc.cfop), '') AS cfop_nf,
        NULL::text AS natureza_operacao,
        tc.modelo_nf AS modelo,
        tc.serie_nf AS serie,
        tc.sub_serie,
        NULLIF(regexp_replace(COALESCE(tc.n_nf, ''), '[^0-9]', '', 'g'), '')::int AS numero_nf,
        CASE
            WHEN COALESCE(tc.vl_servicos, 0) <> 0 THEN COALESCE(tc.vl_servicos, 0)
            ELSE COALESCE(NULLIF(tc.vl_total_nf, 0), NULLIF(tc.total, 0), 0)
        END::numeric(18,2) AS valor_item
    FROM public.tcompras tc
    WHERE tc.cod_empresa = %(cod_empresa)s
            AND tc.cod_tp_mov = 20
        AND (CASE
            WHEN COALESCE(tc.vl_servicos, 0) <> 0 THEN COALESCE(tc.vl_servicos, 0)
            ELSE COALESCE(NULLIF(tc.vl_total_nf, 0), NULLIF(tc.total, 0), 0)
            END) <> 0
        AND EXISTS (
            SELECT 1
            FROM regexp_matches(COALESCE(tc.cfop, ''), '([0-9]{4})', 'g') AS m(cfop_arr)
            WHERE m.cfop_arr[1] IS NOT NULL
        )
        AND NOT EXISTS (
            SELECT 1
            FROM regexp_matches(COALESCE(tc.cfop, ''), '([0-9]{4})', 'g') AS m(cfop_arr)
            WHERE m.cfop_arr[1] IS NOT NULL
              AND m.cfop_arr[1] <> ALL(%(cfop_ap_list)s::text[])
        )
      AND COALESCE(tc.dt_recebimento::date, tc.dt_nf::date, tc.dt_emissao::date)
          BETWEEN COALESCE(%(data_de)s::date, '1900-01-01'::date)
              AND COALESCE(%(data_ate)s::date, '9999-12-31'::date)
      AND (%(tipo_item)s::text IS NULL OR
           (CASE WHEN COALESCE(tc.vl_servicos, 0) <> 0 THEN 'SERVICO' ELSE 'PRODUTO' END) = %(tipo_item)s::text)
), agg AS (
    SELECT
        cod_empresa,
        cnpj,
        COALESCE(fornecedor, '(Sem nome)') AS fornecedor,
        tipo_item,
        COALESCE(classificacao_item, '(Sem classificação)') AS classificacao_item,
        COALESCE(cfop_entrada, '(Sem CFOP entrada)') AS cfop_entrada,
        COALESCE(cfop_nf, '(Sem CFOP NF)') AS cfop_nf,
        COALESCE(natureza_operacao, '(Sem natureza)') AS natureza_operacao,
        EXTRACT(YEAR FROM data_entrada)::int AS ano,
        EXTRACT(MONTH FROM data_entrada)::int AS mes,
        TO_CHAR(data_entrada, 'YYYY-MM') AS periodo_mes,
        SUM(valor_item)::numeric(18,2) AS valor_mensal,
        COUNT(*)::int AS qtd_itens,
        COUNT(DISTINCT (modelo::text || '-' || serie::text || '-' || sub_serie::text || '-' || numero_nf::text))::int AS qtd_nf
    FROM base
    GROUP BY cod_empresa, cnpj, COALESCE(fornecedor, '(Sem nome)'), tipo_item,
             COALESCE(classificacao_item, '(Sem classificação)'),
             COALESCE(cfop_entrada, '(Sem CFOP entrada)'),
             COALESCE(cfop_nf, '(Sem CFOP NF)'),
             COALESCE(natureza_operacao, '(Sem natureza)'),
             EXTRACT(YEAR FROM data_entrada), EXTRACT(MONTH FROM data_entrada), TO_CHAR(data_entrada, 'YYYY-MM')
)
SELECT
    cod_empresa,
    cnpj,
    fornecedor,
    tipo_item,
    classificacao_item,
    cfop_entrada AS cfop,
    cfop_entrada,
    cfop_nf,
    natureza_operacao,
    ano,
    mes,
    periodo_mes,
    valor_mensal,
    SUM(valor_mensal) OVER (PARTITION BY cnpj, tipo_item, ano)::numeric(18,2) AS valor_anual,
    qtd_itens,
    qtd_nf
FROM agg
ORDER BY ano DESC, mes DESC, cnpj, tipo_item, classificacao_item
"""


SQL_SPEND_BASELINE_COMPOSICAO = """
SELECT
    tc.cod_empresa,
    COALESCE(tc.dt_recebimento::date, tc.dt_nf::date, tc.dt_emissao::date) AS data_entrada,
    NULLIF(TRIM(tc.n_nf), '') AS numero_nf,
    NULLIF(TRIM(tc.modelo_nf), '') AS modelo_nf,
    NULLIF(TRIM(tc.serie_nf), '') AS serie_nf,
    NULLIF(TRIM(tc.sub_serie), '') AS sub_serie,
    CASE
        WHEN COALESCE(tc.chv_nfe, '') ~ '^[0-9]{44}$'
            THEN SUBSTRING(tc.chv_nfe FROM 7 FOR 14)
        ELSE 'SEM_CNPJ'
    END AS cnpj,
    COALESCE(NULLIF(TRIM(tc.fornecedor), ''), '(Sem fornecedor)') AS fornecedor,
    COALESCE(NULLIF(TRIM(tc.cfop), ''), '(Sem CFOP)') AS cfop,
    CASE
        WHEN COALESCE(tc.vl_servicos, 0) <> 0 THEN COALESCE(tc.vl_servicos, 0)
        ELSE COALESCE(NULLIF(tc.vl_total_nf, 0), NULLIF(tc.total, 0), 0)
    END::numeric(18,2) AS valor_nf,
    NULLIF(TRIM(tc.chv_nfe), '') AS chave_nfe,
    tc.cod_fornecedor,
    tc.cod_tp_mov,
    NULLIF(TRIM(tc.tipo_movimento), '') AS tipo_movimento,
    CASE
        WHEN COALESCE(tc.vl_servicos, 0) <> 0 THEN 'SERVICO'
        ELSE 'PRODUTO'
    END AS tipo_item
FROM public.tcompras tc
WHERE tc.cod_empresa = %(cod_empresa)s
  AND tc.cod_tp_mov = 20
    AND (CASE
                WHEN COALESCE(tc.vl_servicos, 0) <> 0 THEN COALESCE(tc.vl_servicos, 0)
                ELSE COALESCE(NULLIF(tc.vl_total_nf, 0), NULLIF(tc.total, 0), 0)
            END) <> 0
    AND EXISTS (
                SELECT 1
                FROM regexp_matches(COALESCE(tc.cfop, ''), '([0-9]{4})', 'g') AS m(cfop_arr)
                WHERE m.cfop_arr[1] IS NOT NULL
    )
    AND NOT EXISTS (
                SELECT 1
                FROM regexp_matches(COALESCE(tc.cfop, ''), '([0-9]{4})', 'g') AS m(cfop_arr)
                WHERE m.cfop_arr[1] IS NOT NULL
                    AND m.cfop_arr[1] <> ALL(%(cfop_ap_list)s::text[])
    )
  AND COALESCE(tc.dt_recebimento::date, tc.dt_nf::date, tc.dt_emissao::date)
      BETWEEN COALESCE(%(data_de)s::date, '1900-01-01'::date)
          AND COALESCE(%(data_ate)s::date, '9999-12-31'::date)
  AND (%(tipo_item)s::text IS NULL OR
       (CASE WHEN COALESCE(tc.vl_servicos, 0) <> 0 THEN 'SERVICO' ELSE 'PRODUTO' END) = %(tipo_item)s::text)
  AND (%(cnpj)s::text IS NULL OR
       (CASE
            WHEN COALESCE(tc.chv_nfe, '') ~ '^[0-9]{44}$'
                THEN SUBSTRING(tc.chv_nfe FROM 7 FOR 14)
            ELSE 'SEM_CNPJ'
        END) = %(cnpj)s::text)
    AND (%(fornecedor)s::text IS NULL OR UPPER(COALESCE(tc.fornecedor, '')) LIKE '%%' || UPPER(%(fornecedor)s::text) || '%%')
ORDER BY data_entrada DESC NULLS LAST, numero_nf DESC NULLS LAST
LIMIT %(limite)s;
"""
