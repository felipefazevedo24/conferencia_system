"""Saúde do estoque (Logística > Inventário > Saúde do estoque).

Checagens de dado e processo do estoque no GRV, levantadas no diagnóstico de
05/10/2026. O Sync não corrige nada no ERP (acesso só leitura e alteração
por fora não deixa rastro): o painel lista o que a Logística/Controladoria
precisa corrigir no próprio GRV e guarda uma foto diária de cada checagem
para mostrar a lista diminuindo.

Cada checagem é uma consulta separada (a tela carrega em paralelo; uma lenta
não derruba as outras). O SQL mora aqui e a bridge importa este módulo
(executar_checagem), como já faz com o catálogo do Compras. Integração com o
ERP nunca derruba a tela: falhou, a checagem volta com "disponivel": False.

Convenções do GRV usadas (conferidas no Cardex): depósito 1 = principal;
tipo_movimento 0/1/2 = movimento físico (3 a 6 são reserva/solicitação);
custo unitário = tproduto.preco_custo (custo_medio está zerado no cadastro).
"""
from __future__ import annotations

import time
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from flask import current_app

from ..extensions import db

# A bridge importa este módulo só para executar_checagem. Ela roda numa VM
# atualizada arquivo a arquivo, sem conferencia_app/tempo.py (e sem tzdata
# garantido para o zoneinfo): agora_br é importado dentro das funções do Sync.

MAX_LINHAS = 500
CACHE_SEGUNDOS = 30 * 60

UNIDADES_FRACIONAVEIS = "('KG','MM','M','G','CM','MT','L','ML')"
UNIDADES_INTEIRAS = "('UN','UND','UNID','PC','PÇ','PCA','PEÇA','CJ','JG','PAR')"

CATEGORIAS = {
    "cadastro": "Cadastro",
    "inventario": "Inventário",
    "parado": "Estoque parado",
    "reposicao": "Reposição",
    "processo": "Processo",
    "erp": "ERP (suporte GRV)",
}

# Último movimento físico por produto/depósito (base de várias checagens).
_ULTIMO_MOV = """
ult AS (
    SELECT cod_produto, cod_deposito, MAX(dt_hora_movimentacao) AS ult
    FROM public.tproduto_cardex
    WHERE cod_empresa = %(empresa)s AND tipo_movimento IN (0, 1, 2) {filtro}
    GROUP BY 1, 2
)"""


def _ult(filtro: str = "") -> str:
    return _ULTIMO_MOV.replace("{filtro}", filtro)


CHECAGENS: list[dict[str, Any]] = [
    {
        "chave": "unidade_custo",
        "categoria": "cadastro",
        "titulo": "Unidade incoerente com o custo",
        "descricao": "Item em unidade fracionável (KG, MM, M...) com custo acima de R$ 1.000 por unidade.",
        "acao": "Corrigir a unidade do cadastro (ex.: produto de OS em PC, não KG; barra em BR, não MM) e o custo. Esses itens já geraram ajuste de inventário de R$ 27 milhões fictícios.",
        "colunas": [("codigo_interno", "Código", "texto"), ("nome", "Descrição", "texto"), ("unidade", "Un.", "texto"),
                    ("custo_unitario", "Custo unit.", "moeda"), ("saldo", "Saldo dep. 1", "qtd"), ("valor", "Valor", "moeda")],
        "valor": "valor",
        "sql": f"""
SELECT p.codigo_interno, p.nome, p.unidade, p.preco_custo AS custo_unitario,
       COALESCE(d.qtde_total, 0) AS saldo, COALESCE(d.qtde_total, 0) * p.preco_custo AS valor
FROM public.tproduto p
LEFT JOIN public.tproduto_deposito d ON d.cod_empresa = p.cod_empresa AND d.cod_produto = p.codigo AND d.cod_deposito = 1
WHERE p.cod_empresa = %(empresa)s AND COALESCE(p.inativo, 0) = 0
  AND UPPER(TRIM(p.unidade)) IN {UNIDADES_FRACIONAVEIS} AND p.preco_custo > 1000
ORDER BY p.preco_custo DESC""",
    },
    {
        "chave": "fracao_unidade_inteira",
        "categoria": "cadastro",
        "titulo": "Quantidade quebrada em unidade inteira",
        "descricao": "Item contado em UN/PC/CJ que movimentou quantidade fracionada nos últimos 12 meses.",
        "acao": "Ver se a unidade do cadastro está errada (o item é vendido/consumido por peso ou metro) ou se o lançamento foi digitado errado.",
        "colunas": [("codigo_interno", "Código", "texto"), ("nome", "Descrição", "texto"), ("unidade", "Un.", "texto"),
                    ("movimentos", "Movimentos", "int"), ("exemplo_qtde", "Exemplo", "qtd"), ("ultimo", "Último", "data")],
        "valor": None,
        "sql": f"""
SELECT p.codigo_interno, p.nome, p.unidade, COUNT(*) AS movimentos,
       (ARRAY_AGG(c.qtde_movimentada ORDER BY c.dt_hora_movimentacao DESC))[1] AS exemplo_qtde,
       MAX(c.dt_hora_movimentacao)::date AS ultimo
FROM public.tproduto_cardex c
JOIN public.tproduto p ON p.cod_empresa = c.cod_empresa AND p.codigo = c.cod_produto
WHERE c.cod_empresa = %(empresa)s AND c.tipo_movimento IN (0, 1, 2)
  AND c.dt_hora_movimentacao >= NOW() - INTERVAL '12 months'
  AND UPPER(TRIM(p.unidade)) IN {UNIDADES_INTEIRAS}
  AND ABS(c.qtde_movimentada - ROUND(c.qtde_movimentada::numeric)) > 0.0001
GROUP BY 1, 2, 3
ORDER BY movimentos DESC""",
    },
    {
        "chave": "custo_zero",
        "categoria": "cadastro",
        "titulo": "Saldo com custo zerado",
        "descricao": "Item com saldo no depósito 1 e custo zero no cadastro: some da valorização do estoque.",
        "acao": "Lançar o custo correto no GRV (última compra ou custo de produção).",
        "colunas": [("codigo_interno", "Código", "texto"), ("nome", "Descrição", "texto"), ("unidade", "Un.", "texto"),
                    ("saldo", "Saldo dep. 1", "qtd")],
        "valor": None,
        "sql": """
SELECT p.codigo_interno, p.nome, p.unidade, d.qtde_total AS saldo
FROM public.tproduto_deposito d
JOIN public.tproduto p ON p.cod_empresa = d.cod_empresa AND p.codigo = d.cod_produto
WHERE d.cod_empresa = %(empresa)s AND d.cod_deposito = 1 AND d.qtde_total > 0 AND COALESCE(p.preco_custo, 0) <= 0
ORDER BY d.qtde_total DESC""",
    },
    {
        "chave": "cadastro_teste",
        "categoria": "cadastro",
        "titulo": "Cadastros de teste ativos",
        "descricao": "Produto com \"TESTE\" no nome, ativo na base de produção.",
        "acao": "Zerar o saldo e inativar o cadastro no GRV.",
        "colunas": [("codigo_interno", "Código", "texto"), ("nome", "Descrição", "texto"), ("saldo", "Saldo dep. 1", "qtd"),
                    ("ultimo_movimento", "Último mov.", "data"), ("valor", "Valor", "moeda")],
        "valor": "valor",
        "sql": f"""
WITH {_ult("AND cod_deposito = 1")}
SELECT p.codigo_interno, p.nome, COALESCE(d.qtde_total, 0) AS saldo, u.ult::date AS ultimo_movimento,
       COALESCE(d.qtde_total, 0) * COALESCE(p.preco_custo, 0) AS valor
FROM public.tproduto p
LEFT JOIN public.tproduto_deposito d ON d.cod_empresa = p.cod_empresa AND d.cod_produto = p.codigo AND d.cod_deposito = 1
LEFT JOIN ult u ON u.cod_produto = p.codigo
WHERE p.cod_empresa = %(empresa)s AND COALESCE(p.inativo, 0) = 0 AND p.nome ILIKE '%%teste%%'
ORDER BY u.ult DESC NULLS LAST""",
    },
    {
        "chave": "duplicados",
        "categoria": "cadastro",
        "titulo": "Cadastros duplicados",
        "descricao": "Mesma descrição em 2 ou mais cadastros ativos, com saldo em pelo menos um deles.",
        "acao": "Escolher o cadastro que fica, transferir o saldo e inativar os outros.",
        "colunas": [("nome", "Descrição", "texto"), ("cadastros", "Cadastros", "int"), ("com_saldo", "Com saldo", "int"),
                    ("codigos", "Código = saldo", "texto")],
        "valor": None,
        "sql": """
WITH k AS (
    SELECT UPPER(REGEXP_REPLACE(TRIM(p.nome), '\\s+', ' ', 'g')) AS nome, p.codigo_interno, COALESCE(d.qtde_total, 0) AS saldo
    FROM public.tproduto p
    LEFT JOIN public.tproduto_deposito d ON d.cod_empresa = p.cod_empresa AND d.cod_produto = p.codigo AND d.cod_deposito = 1
    WHERE p.cod_empresa = %(empresa)s AND COALESCE(p.inativo, 0) = 0 AND COALESCE(TRIM(p.nome), '') <> ''
)
SELECT nome, COUNT(*) AS cadastros, COUNT(*) FILTER (WHERE saldo > 0) AS com_saldo,
       STRING_AGG(codigo_interno || ' = ' || ROUND(saldo::numeric, 2)::text, '; ' ORDER BY saldo DESC) AS codigos
FROM k GROUP BY nome
HAVING COUNT(*) > 1 AND COUNT(*) FILTER (WHERE saldo > 0) > 0
ORDER BY com_saldo DESC, cadastros DESC""",
    },
    {
        "chave": "ajuste_alto_valor",
        "categoria": "inventario",
        "titulo": "Ajuste de inventário de alto valor",
        "descricao": "Ajuste de inventário acima de R$ 50 mil nos últimos 12 meses (máquina entrando/saindo por inventário, unidade errada).",
        "acao": "Máquina e produto acabado devem passar por OS/produção/faturamento, não por inventário. Ajuste de valor absurdo indica cadastro errado.",
        "colunas": [("data", "Data", "data"), ("usuario", "Usuário", "texto"), ("codigo_interno", "Código", "texto"),
                    ("nome", "Descrição", "texto"), ("unidade", "Un.", "texto"), ("tipo", "Tipo", "texto"),
                    ("qtde", "Qtd.", "qtd"), ("custo_unitario", "Custo unit.", "moeda"), ("valor", "Valor", "moeda")],
        "valor": "valor",
        "sql": """
SELECT c.dt_hora_movimentacao::date AS data, c.usuario, p.codigo_interno, p.nome, p.unidade,
       CASE WHEN c.tipo_movimento = 0 THEN 'Entrada' ELSE 'Saída' END AS tipo,
       c.qtde_movimentada AS qtde, c.vl_custo AS custo_unitario, ABS(c.qtde_movimentada) * c.vl_custo AS valor
FROM public.tproduto_cardex c
JOIN public.tproduto p ON p.cod_empresa = c.cod_empresa AND p.codigo = c.cod_produto
WHERE c.cod_empresa = %(empresa)s AND UPPER(c.tabela_link) = 'TINVENT_DEP' AND c.tipo_movimento IN (0, 1)
  AND c.dt_hora_movimentacao >= NOW() - INTERVAL '12 months'
  AND ABS(c.qtde_movimentada) * c.vl_custo > 50000
ORDER BY valor DESC""",
    },
    {
        "chave": "ajuste_recorrente",
        "categoria": "inventario",
        "titulo": "Ajuste de inventário recorrente",
        "descricao": "Item ajustado em 3 ou mais meses diferentes nos últimos 24 meses (depósito 1).",
        "acao": "Investigar a causa (baixa sem saída, unidade errada, consumo por peso) e incluir na contagem cíclica.",
        "colunas": [("codigo_interno", "Código", "texto"), ("nome", "Descrição", "texto"), ("unidade", "Un.", "texto"),
                    ("meses_com_ajuste", "Meses", "int"), ("ajustes", "Ajustes", "int"),
                    ("qtde_liquida", "Qtd. líquida", "qtd"), ("ultimo_ajuste", "Último", "data"), ("valor", "Valor líquido", "moeda")],
        "valor": "valor",
        "sql": """
SELECT p.codigo_interno, p.nome, p.unidade,
       COUNT(DISTINCT DATE_TRUNC('month', c.dt_hora_movimentacao)) AS meses_com_ajuste, COUNT(*) AS ajustes,
       SUM(CASE WHEN c.tipo_movimento = 0 THEN c.qtde_movimentada ELSE -ABS(c.qtde_movimentada) END) AS qtde_liquida,
       MAX(c.dt_hora_movimentacao)::date AS ultimo_ajuste,
       SUM(CASE WHEN c.tipo_movimento = 0 THEN c.qtde_movimentada ELSE -ABS(c.qtde_movimentada) END * COALESCE(c.vl_custo, 0)) AS valor
FROM public.tproduto_cardex c
JOIN public.tproduto p ON p.cod_empresa = c.cod_empresa AND p.codigo = c.cod_produto
WHERE c.cod_empresa = %(empresa)s AND c.cod_deposito = 1 AND UPPER(c.tabela_link) = 'TINVENT_DEP'
  AND c.tipo_movimento IN (0, 1) AND c.dt_hora_movimentacao >= NOW() - INTERVAL '24 months'
GROUP BY 1, 2, 3
HAVING COUNT(DISTINCT DATE_TRUNC('month', c.dt_hora_movimentacao)) >= 3
ORDER BY meses_com_ajuste DESC, ABS(SUM(CASE WHEN c.tipo_movimento = 0 THEN c.qtde_movimentada ELSE -ABS(c.qtde_movimentada) END * COALESCE(c.vl_custo, 0))) DESC""",
    },
    {
        "chave": "terceiros_parados",
        "categoria": "parado",
        "titulo": "Material em terceiros parado",
        "descricao": "Saldo em \"Meu em poder de terceiros\" sem movimento há mais de 180 dias.",
        "acao": "Cobrar o retorno do fornecedor/cliente. Remessa para industrialização tem prazo de retorno para manter a suspensão do ICMS: confirmar com o Fiscal.",
        "colunas": [("parceiro", "Com quem está", "texto"), ("codigo_interno", "Código", "texto"), ("nome", "Descrição", "texto"),
                    ("saldo", "Saldo", "qtd"), ("ultimo_movimento", "Último mov.", "data"), ("dias", "Dias", "int"), ("valor", "Valor", "moeda")],
        "valor": "valor",
        "sql": f"""
WITH dep AS (
    -- nome_pn = fornecedor/cliente do depósito ("MEU EM PODER DE TERCEIROS - FOR.: 1051" -> BRASIMET ...).
    SELECT codigo, COALESCE(NULLIF(TRIM(nome_pn), ''), nome) || COALESCE(' (' || cod_pn || ')', '') AS parceiro
    FROM public.tdeposito WHERE cod_empresa = %(empresa)s AND nome ILIKE 'MEU EM PODER%%'
),
{_ult("AND cod_deposito IN (SELECT codigo FROM public.tdeposito WHERE cod_empresa = %(empresa)s AND nome ILIKE 'MEU EM PODER%%')")}
SELECT dep.parceiro, p.codigo_interno, p.nome, d.qtde_total AS saldo, u.ult::date AS ultimo_movimento,
       (CURRENT_DATE - u.ult::date) AS dias, d.qtde_total * COALESCE(p.preco_custo, 0) AS valor
FROM public.tproduto_deposito d
JOIN dep ON dep.codigo = d.cod_deposito
JOIN public.tproduto p ON p.cod_empresa = d.cod_empresa AND p.codigo = d.cod_produto
LEFT JOIN ult u ON u.cod_produto = d.cod_produto AND u.cod_deposito = d.cod_deposito
WHERE d.cod_empresa = %(empresa)s AND d.qtde_total > 0 AND (u.ult IS NULL OR u.ult < NOW() - INTERVAL '180 days')
ORDER BY valor DESC""",
    },
    {
        "chave": "producao_parada",
        "categoria": "parado",
        "titulo": "Parado em \"Em produção\"",
        "descricao": "Saldo no depósito 2 (Em produção) sem movimento há mais de 90 dias.",
        "acao": "Ver se a OS foi concluída sem baixar o material ou se o material voltou fisicamente ao estoque sem transferência.",
        "colunas": [("codigo_interno", "Código", "texto"), ("nome", "Descrição", "texto"), ("unidade", "Un.", "texto"),
                    ("saldo", "Saldo", "qtd"), ("ultimo_movimento", "Último mov.", "data"), ("dias", "Dias", "int"), ("valor", "Valor", "moeda")],
        "valor": "valor",
        "sql": f"""
WITH {_ult("AND cod_deposito = 2")}
SELECT p.codigo_interno, p.nome, p.unidade, d.qtde_total AS saldo, u.ult::date AS ultimo_movimento,
       (CURRENT_DATE - u.ult::date) AS dias, d.qtde_total * COALESCE(p.preco_custo, 0) AS valor
FROM public.tproduto_deposito d
JOIN public.tproduto p ON p.cod_empresa = d.cod_empresa AND p.codigo = d.cod_produto
LEFT JOIN ult u ON u.cod_produto = d.cod_produto
WHERE d.cod_empresa = %(empresa)s AND d.cod_deposito = 2 AND d.qtde_total > 0
  AND (u.ult IS NULL OR u.ult < NOW() - INTERVAL '90 days')
ORDER BY valor DESC""",
    },
    {
        "chave": "parados_dep1",
        "categoria": "parado",
        "titulo": "Sem movimento há mais de 1 ano",
        "descricao": "Saldo no depósito 1 sem movimento físico há mais de 12 meses.",
        "acao": "Avaliar obsolescência: usar, vender, sucatear ou provisionar.",
        "colunas": [("codigo_interno", "Código", "texto"), ("nome", "Descrição", "texto"), ("unidade", "Un.", "texto"),
                    ("saldo", "Saldo", "qtd"), ("ultimo_movimento", "Último mov.", "data"), ("dias", "Dias", "int"), ("valor", "Valor", "moeda")],
        "valor": "valor",
        "sql": f"""
WITH {_ult("AND cod_deposito = 1")}
SELECT p.codigo_interno, p.nome, p.unidade, d.qtde_total AS saldo, u.ult::date AS ultimo_movimento,
       (CURRENT_DATE - u.ult::date) AS dias, d.qtde_total * COALESCE(p.preco_custo, 0) AS valor
FROM public.tproduto_deposito d
JOIN public.tproduto p ON p.cod_empresa = d.cod_empresa AND p.codigo = d.cod_produto
LEFT JOIN ult u ON u.cod_produto = d.cod_produto
WHERE d.cod_empresa = %(empresa)s AND d.cod_deposito = 1 AND d.qtde_total > 0
  AND (u.ult IS NULL OR u.ult < NOW() - INTERVAL '12 months')
ORDER BY valor DESC""",
    },
    {
        "chave": "reserva_presa",
        "categoria": "parado",
        "titulo": "Reserva presa",
        "descricao": "Reserva no depósito 1 em item sem movimento há 180 dias, ou reservado maior que o saldo.",
        "acao": "Liberar a reserva da OS/solicitação que não vai mais consumir: ela esconde saldo disponível.",
        "colunas": [("codigo_interno", "Código", "texto"), ("nome", "Descrição", "texto"), ("saldo", "Saldo", "qtd"),
                    ("reservado", "Reservado", "qtd"), ("ultimo_movimento", "Último mov.", "data"), ("motivo", "Motivo", "texto"),
                    ("valor", "Valor reservado", "moeda")],
        "valor": "valor",
        "sql": f"""
WITH {_ult("AND cod_deposito = 1")}
SELECT p.codigo_interno, p.nome, d.qtde_total AS saldo, d.qtde_reservada AS reservado, u.ult::date AS ultimo_movimento,
       CASE WHEN d.qtde_reservada > d.qtde_total THEN 'Reservado maior que o saldo' ELSE 'Sem movimento há 180+ dias' END AS motivo,
       d.qtde_reservada * COALESCE(p.preco_custo, 0) AS valor
FROM public.tproduto_deposito d
JOIN public.tproduto p ON p.cod_empresa = d.cod_empresa AND p.codigo = d.cod_produto
LEFT JOIN ult u ON u.cod_produto = d.cod_produto
WHERE d.cod_empresa = %(empresa)s AND d.cod_deposito = 1 AND d.qtde_reservada > 0
  AND (d.qtde_reservada > d.qtde_total OR u.ult IS NULL OR u.ult < NOW() - INTERVAL '180 days')
ORDER BY valor DESC""",
    },
    {
        "chave": "minimo_consumo",
        "categoria": "reposicao",
        "titulo": "Estoque mínimo × consumo",
        "descricao": "Mínimo sem consumo, mínimo acima de 6 meses de consumo, ou item consumido todo mês sem mínimo (depósito 1, 12 meses).",
        "acao": "Lançar no GRV o mínimo sugerido (coluna \"Mínimo sugerido\"). Sugestão = consumo no prazo de entrega + estoque de segurança de 95%. O lead time do cadastro está zerado em todos os produtos; o prazo usado é o real, da data da OC até a entrada no estoque (mediana das OCs dos últimos 24 meses; 30 dias quando o item não tem OC com entrada).",
        "colunas": [("situacao", "Situação", "texto"), ("codigo_interno", "Código", "texto"), ("nome", "Descrição", "texto"),
                    ("unidade", "Un.", "texto"), ("minimo", "Mínimo atual", "qtd"), ("minimo_sugerido", "Mínimo sugerido", "qtd"),
                    ("consumo_medio_mensal", "Consumo/mês", "qtd"), ("prazo_entrega", "Prazo entrega", "texto"),
                    ("meses_com_consumo", "Meses c/ consumo", "int"), ("disponivel", "Disponível", "qtd"), ("valor", "Valor do mínimo", "moeda")],
        "valor": "valor",
        # Mínimo sugerido = ponto de pedido:
        #   consumo médio diário x prazo de entrega  (o que sai enquanto a compra chega)
        # + 1,65 x desvio do consumo mensal x raiz(prazo/30)  (segurança: 95% de não faltar)
        # Consumo: 12 meses fechados, mês sem saída conta como zero (senão o desvio
        # de item intermitente sai subestimado). Prazo: mediana real OC -> 1a entrada
        # no depósito 1 (05/10/2026: 187 de 197 itens frequentes têm; mediana 7 dias).
        # Unidade inteira arredonda para cima.
        "sql": f"""
WITH meses AS (
    SELECT GENERATE_SERIES(DATE_TRUNC('month', NOW()) - INTERVAL '12 months',
                           DATE_TRUNC('month', NOW()) - INTERVAL '1 month', INTERVAL '1 month') AS mes
), cons_mes AS (
    SELECT cod_produto, DATE_TRUNC('month', dt_hora_movimentacao) AS mes, SUM(ABS(qtde_movimentada)) AS q
    FROM public.tproduto_cardex
    WHERE cod_empresa = %(empresa)s AND cod_deposito = 1 AND tipo_movimento = 1
      AND UPPER(tabela_link) IN ('TSAIDA_E', 'TOS', 'TSOL_MAT', 'TNOTA_FISCAL')
      AND dt_hora_movimentacao >= DATE_TRUNC('month', NOW()) - INTERVAL '12 months'
      AND dt_hora_movimentacao < DATE_TRUNC('month', NOW())
    GROUP BY 1, 2
), cons AS (
    SELECT pr.cod_produto, AVG(COALESCE(c.q, 0)) AS media, COALESCE(STDDEV_SAMP(COALESCE(c.q, 0)), 0) AS desvio,
           COUNT(c.q) AS meses
    FROM (SELECT DISTINCT cod_produto FROM cons_mes) pr
    CROSS JOIN meses m
    LEFT JOIN cons_mes c ON c.cod_produto = pr.cod_produto AND c.mes = m.mes
    GROUP BY 1
), lt AS (
    SELECT a.cod_produto, PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY ent.dt - o.data::date) AS dias, COUNT(*) AS n_oc
    FROM public.tord_aux a
    JOIN public.tord_com o ON o.cod_empresa = a.cod_empresa AND o.codigo = a.cod_ord_compra
    JOIN LATERAL (
        SELECT MIN(k.dt_hora_movimentacao)::date AS dt
        FROM public.tcom_ordem_compra co
        JOIN public.tproduto_cardex k
          ON k.cod_empresa = co.cod_empresa AND UPPER(k.tabela_link) = 'TCOMPRAS'
         AND k.chave_primaria_link = co.cod_empresa::text || ';' || co.cod_compra::text
         AND k.cod_produto = a.cod_produto AND k.cod_deposito = 1 AND k.tipo_movimento = 0 AND k.qtde_movimentada > 0
        WHERE co.cod_empresa = o.cod_empresa AND co.cod_ordem_compra = o.codigo AND k.dt_hora_movimentacao >= o.data
    ) ent ON ent.dt IS NOT NULL
    WHERE a.cod_empresa = %(empresa)s AND COALESCE(o.cancelado, 0) = 0 AND o.data >= NOW() - INTERVAL '24 months'
      AND a.cod_produto IN (SELECT cod_produto FROM cons)
    GROUP BY 1
), base AS (
    SELECT p.codigo_interno, p.nome, p.unidade, COALESCE(d.estoque_minimo, 0) AS minimo,
           COALESCE(c.media, 0) AS consumo_medio_mensal, COALESCE(c.desvio, 0) AS desvio, COALESCE(c.meses, 0) AS meses_com_consumo,
           COALESCE(d.qtde_disponivel, 0) AS disponivel, COALESCE(p.preco_custo, 0) AS custo,
           lt.dias AS lt_real, lt.n_oc, COALESCE(lt.dias, 30) AS lt_dias,
           UPPER(TRIM(p.unidade)) IN {UNIDADES_INTEIRAS} AS inteira
    FROM public.tproduto p
    LEFT JOIN public.tproduto_deposito d ON d.cod_empresa = p.cod_empresa AND d.cod_produto = p.codigo AND d.cod_deposito = 1
    LEFT JOIN cons c ON c.cod_produto = p.codigo
    LEFT JOIN lt ON lt.cod_produto = p.codigo
    WHERE p.cod_empresa = %(empresa)s AND COALESCE(p.inativo, 0) = 0
), sug AS (
    SELECT *, consumo_medio_mensal / 30.0 * lt_dias + 1.65 * desvio * SQRT(lt_dias / 30.0) AS bruto FROM base
), cls AS (
    SELECT *, CASE
        WHEN minimo > 0 AND meses_com_consumo = 0 THEN 'Mínimo sem consumo em 12 meses'
        WHEN minimo > 0 AND consumo_medio_mensal > 0 AND minimo > consumo_medio_mensal * 6 THEN 'Mínimo acima de 6 meses de consumo'
        WHEN minimo <= 0 AND meses_com_consumo >= 6 THEN 'Consumido todo mês, sem mínimo'
    END AS situacao,
    CASE WHEN inteira THEN CEIL(bruto) ELSE CEIL(bruto * 100) / 100.0 END AS minimo_sugerido
    FROM sug
)
SELECT situacao, codigo_interno, nome, unidade, minimo, minimo_sugerido, consumo_medio_mensal,
       CASE WHEN lt_real IS NULL THEN '30 dias (padrão, sem OC)'
            ELSE ROUND(lt_real::numeric)::text || ' dias (' || n_oc || ' OC)' END AS prazo_entrega,
       meses_com_consumo, disponivel,
       CASE WHEN minimo > 0 THEN minimo * custo END AS valor
FROM cls WHERE situacao IS NOT NULL
ORDER BY situacao, CASE WHEN minimo > 0 THEN minimo * custo ELSE 0 END DESC, meses_com_consumo DESC""",
    },
    {
        "chave": "saidas_sem_os",
        "categoria": "processo",
        "titulo": "Saídas avulsas sem OS",
        "descricao": "Saídas avulsas concluídas nos últimos 90 dias sem OS vinculada, por quem retirou: o custo não vai para nenhuma OS.",
        "acao": "Exigir a OS na saída (ou centro de custo) e orientar quem mais retira sem OS.",
        "colunas": [("retirou", "Quem retirou", "texto"), ("saidas", "Saídas", "int"), ("sem_os", "Sem OS", "int"),
                    ("pct_sem_os", "% sem OS", "pct"), ("ultima", "Última", "data")],
        "valor": None,
        "contagem": "sem_os",
        "sql": """
SELECT COALESCE(NULLIF(TRIM(funcionario_retirou), ''), '(não informado)') AS retirou, COUNT(*) AS saidas,
       COUNT(*) FILTER (WHERE COALESCE(saida_com_os, 0) <> 1 AND COALESCE(cod_os, 0) = 0 AND COALESCE(TRIM(n_os), '') = '') AS sem_os,
       ROUND(100.0 * COUNT(*) FILTER (WHERE COALESCE(saida_com_os, 0) <> 1 AND COALESCE(cod_os, 0) = 0 AND COALESCE(TRIM(n_os), '') = '') / COUNT(*), 1) AS pct_sem_os,
       MAX(data)::date AS ultima
FROM public.tsaida_e
WHERE cod_empresa = %(empresa)s AND data >= NOW() - INTERVAL '90 days' AND tipo_movimento_estoque ILIKE 'CONCLUS%%'
GROUP BY 1
HAVING COUNT(*) FILTER (WHERE COALESCE(saida_com_os, 0) <> 1 AND COALESCE(cod_os, 0) = 0 AND COALESCE(TRIM(n_os), '') = '') > 0
ORDER BY sem_os DESC""",
    },
    {
        "chave": "saldo_cadastro_desatualizado",
        "categoria": "erp",
        "titulo": "Saldo do cadastro desatualizado",
        "descricao": "tproduto.estoque_disponivel_uso diferente do disponível real do depósito 1. O Sync já usa o saldo do depósito; telas/relatórios do GRV que leem o cadastro mostram valor errado.",
        "acao": "Abrir chamado no suporte do GRV para recalcular os saldos do cadastro (rotina de recálculo de estoque).",
        "colunas": [("codigo_interno", "Código", "texto"), ("nome", "Descrição", "texto"), ("no_cadastro", "No cadastro", "qtd"),
                    ("real_deposito", "Real (dep. 1)", "qtd"), ("diferenca", "Diferença", "qtd")],
        "valor": None,
        "sql": """
SELECT p.codigo_interno, p.nome, p.estoque_disponivel_uso AS no_cadastro, COALESCE(d.qtde_disponivel, 0) AS real_deposito,
       COALESCE(p.estoque_disponivel_uso, 0) - COALESCE(d.qtde_disponivel, 0) AS diferenca
FROM public.tproduto p
LEFT JOIN public.tproduto_deposito d ON d.cod_empresa = p.cod_empresa AND d.cod_produto = p.codigo AND d.cod_deposito = 1
WHERE p.cod_empresa = %(empresa)s AND COALESCE(p.inativo, 0) = 0
  AND ABS(COALESCE(p.estoque_disponivel_uso, 0) - COALESCE(d.qtde_disponivel, 0)) > 0.001
ORDER BY ABS(COALESCE(p.estoque_disponivel_uso, 0) - COALESCE(d.qtde_disponivel, 0)) * COALESCE(p.preco_custo, 0) DESC""",
    },
]

POR_CHAVE = {c["chave"]: c for c in CHECAGENS}


def metadados() -> list[dict[str, Any]]:
    """O que a tela precisa para montar os cartões (sem o SQL)."""
    return [
        {**{k: v for k, v in c.items() if k != "sql"}, "categoria_label": CATEGORIAS[c["categoria"]]}
        for c in CHECAGENS
    ]


# --------------------------------------------------------------------------
# Execução (roda na bridge, que tem acesso ao Postgres)
# --------------------------------------------------------------------------

def _json(valor):
    if isinstance(valor, Decimal):
        return float(valor)
    if isinstance(valor, (datetime, date)):
        return valor.isoformat()
    return valor


def executar_checagem(conn, chave: str, empresa: int = 1) -> dict[str, Any]:
    """Roda a checagem numa conexão só leitura e devolve as primeiras
    MAX_LINHAS linhas com o total e o valor calculados sobre todas."""
    checagem = POR_CHAVE.get(chave)
    if not checagem:
        raise ValueError("Checagem desconhecida.")
    with conn.cursor() as cur:
        # Varre o Cardex inteiro em algumas checagens: mais que os 15 s padrão da bridge.
        cur.execute("SET statement_timeout = 90000")
        cur.execute(checagem["sql"], {"empresa": int(empresa)})
        cols = [d[0] for d in cur.description]
        linhas = [dict(zip(cols, (_json(v) for v in row))) for row in cur.fetchall()]
    campo_valor, campo_contagem = checagem.get("valor"), checagem.get("contagem")
    quantidade = sum(int(l.get(campo_contagem) or 0) for l in linhas) if campo_contagem else len(linhas)
    valor = round(sum(abs(float(l.get(campo_valor) or 0)) for l in linhas), 2) if campo_valor else None
    return {"chave": chave, "quantidade": quantidade, "valor": valor, "linhas_total": len(linhas), "linhas": linhas[:MAX_LINHAS]}


# --------------------------------------------------------------------------
# Sync: busca pela bridge, cache e foto diária
# --------------------------------------------------------------------------

_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}


def _consultar_bridge(chave: str) -> dict[str, Any]:
    import requests

    from .erp_estoque_service import _bridge_config, _headers

    cfg = _bridge_config()
    if not cfg["api_url"]:
        raise RuntimeError("ERP_LANCAMENTO_API_URL não configurada.")
    resp = requests.post(f"{cfg['api_url']}/api/erp/estoque/saude", headers=_headers(cfg),
                         json={"chave": chave, "empresa": 1}, timeout=max(float(cfg["timeout"] or 0), 90.0))
    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, dict) or not data.get("sucesso"):
        raise RuntimeError(str((data or {}).get("erro") or "Resposta inválida da bridge."))
    return data


def _gravar_foto(chave: str, quantidade: int, valor: float | None) -> None:
    from ..models import LogisticaEstoqueSaudeFoto
    from ..tempo import agora_br

    try:
        hoje = agora_br().date()
        foto = LogisticaEstoqueSaudeFoto.query.filter_by(dia=hoje, chave=chave).first()
        if not foto:
            foto = LogisticaEstoqueSaudeFoto(dia=hoje, chave=chave)
            db.session.add(foto)
        foto.quantidade, foto.valor, foto.atualizado_em = int(quantidade or 0), valor, agora_br()
        db.session.commit()
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Saúde do estoque: falha ao gravar a foto de %s", chave)


def tendencia(chave: str) -> dict[str, Any] | None:
    """Comparação com a foto mais antiga dos últimos 30 dias (fora hoje)."""
    from ..models import LogisticaEstoqueSaudeFoto
    from ..tempo import agora_br

    hoje = agora_br().date()
    foto = (LogisticaEstoqueSaudeFoto.query
            .filter(LogisticaEstoqueSaudeFoto.chave == chave, LogisticaEstoqueSaudeFoto.dia < hoje,
                    LogisticaEstoqueSaudeFoto.dia >= date.fromordinal(hoje.toordinal() - 30))
            .order_by(LogisticaEstoqueSaudeFoto.dia).first())
    if not foto:
        return None
    return {"dia": foto.dia.isoformat(), "quantidade": foto.quantidade, "valor": foto.valor}


def buscar_checagem(chave: str, forcar: bool = False) -> dict[str, Any] | None:
    """Resultado de uma checagem para a tela. None = chave desconhecida.
    Nunca levanta: falha na bridge volta com disponivel=False."""
    from ..tempo import agora_br

    if chave not in POR_CHAVE:
        return None
    agora = time.time()
    em_cache = _CACHE.get(chave)
    if em_cache and not forcar and agora - em_cache[0] < CACHE_SEGUNDOS:
        dados = em_cache[1]
    else:
        try:
            dados = _consultar_bridge(chave)
        except Exception as exc:
            current_app.logger.warning("Saúde do estoque: checagem %s indisponível: %s", chave, exc)
            if em_cache:
                return {**em_cache[1], "disponivel": True, "desatualizado": True, "tendencia": tendencia(chave)}
            return {"chave": chave, "disponivel": False, "erro": "Não foi possível consultar o GRV agora."}
        dados = {k: dados.get(k) for k in ("chave", "quantidade", "valor", "linhas_total", "linhas")}
        dados["consultado_em"] = agora_br().isoformat(timespec="minutes")
        _CACHE[chave] = (agora, dados)
        _gravar_foto(chave, dados["quantidade"], dados["valor"])
    return {**dados, "disponivel": True, "tendencia": tendencia(chave)}
