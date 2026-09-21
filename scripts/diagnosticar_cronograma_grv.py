"""Diagnostico somente leitura do vinculo orcamento -> servicos no GRV.

Execute no ambiente que possui acesso direto ao PostgreSQL do ERP:
    python -m scripts.diagnosticar_cronograma_grv 7344 10616
"""

import json
import sys

import psycopg2
from psycopg2 import sql

from conferencia_app import create_app
from conferencia_app.compras.db import get_connection


TABLES = ("torcamento", "torcamento_servico_gerados", "tos", "tpro_pro")


def _columns(cur, table):
    cur.execute(
        """SELECT column_name, data_type FROM information_schema.columns
           WHERE table_schema = 'public' AND table_name = %s
           ORDER BY ordinal_position""",
        (table,),
    )
    return {row["column_name"]: row["data_type"] for row in cur.fetchall()}


def _value_for_type(value, data_type):
    if data_type in {"smallint", "integer", "bigint", "numeric"}:
        return int(value)
    return value


def _select(cur, table, columns, where, value):
    statement = sql.SQL("SELECT {} FROM public.{} WHERE {} = %s LIMIT 200").format(
        sql.SQL(", ").join(sql.Identifier(name) for name in columns),
        sql.Identifier(table), sql.Identifier(where),
    )
    cur.execute(statement, (value,))
    return [dict(row) for row in cur.fetchall()]


def main(numero_orcamento, numero_os):
    app = create_app()
    with app.app_context(), get_connection(readonly=True) as conn, conn.cursor() as cur:
        schema = {table: _columns(cur, table) for table in TABLES}
        required = {
            "torcamento": {"codigo", "n_orcamento"},
            "torcamento_servico_gerados": {"cod_orcamento", "cod_os"},
            "tos": {"codigo", "n_os"},
        }
        for table, names in required.items():
            missing = names - schema[table].keys()
            if missing:
                raise RuntimeError(f"Schema inesperado em {table}: {sorted(missing)}")

        budget_cols = [name for name in ("cod_empresa", "codigo", "n_orcamento", "dt_previsao_entrega", "dt_prevista_entrega") if name in schema["torcamento"]]
        budgets = _select(cur, "torcamento", budget_cols, "n_orcamento", _value_for_type(numero_orcamento, schema["torcamento"]["n_orcamento"]))
        os_cols = [name for name, kind in schema["tos"].items() if name in {"cod_empresa", "codigo", "n_os", "cod_orcamento", "n_orcamento", "cliente", "u_classificacao", "classificacao", "u_classificacao_ii", "status_servico"} or kind in {"date", "timestamp without time zone", "timestamp with time zone"}]
        service = _select(cur, "tos", os_cols, "n_os", _value_for_type(numero_os, schema["tos"]["n_os"]))
        link_cols = [name for name in ("cod_empresa", "codigo", "cod_orcamento", "cod_os", "n_os", "cancelado") if name in schema["torcamento_servico_gerados"]]
        links = []
        related = []
        for budget in budgets:
            links.extend(_select(cur, "torcamento_servico_gerados", link_cols, "cod_orcamento", budget["codigo"]))
        for link in links:
            related.extend(_select(cur, "tos", os_cols, "codigo", link["cod_os"]))
        process_cols = [name for name in schema["tpro_pro"] if name in {"cod_os", "codigo", "seq", "tiposervico", "cod_tp_servico", "maquina", "finalizado", "processo_travado"} or "setor" in name]
        processes = []
        if "cod_os" in schema["tpro_pro"]:
            for order in service:
                processes.extend(_select(cur, "tpro_pro", process_cols, "cod_os", order["codigo"]))
        result = {
            "schema": {table: schema[table] for table in TABLES},
            "orcamentos": budgets,
            "servico_pesquisado": service,
            "solicitacoes_geradas": links,
            "servicos_vinculados": related,
            "operacoes_servico_pesquisado": processes,
        }
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    try:
        main(sys.argv[1] if len(sys.argv) > 1 else "7344", sys.argv[2] if len(sys.argv) > 2 else "10616")
    except psycopg2.OperationalError:
        sys.exit("Conexao somente leitura com o PostgreSQL do GRV indisponivel neste ambiente.")
