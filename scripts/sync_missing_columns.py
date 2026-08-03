"""Diagnostico + hotfix de schema: compara TODOS os models da aplicacao com o
banco em uso (MySQL em producao ou SQLite local) e adiciona as colunas/tabelas
que estiverem faltando, de forma idempotente.

Motivacao: o banco de producao foi migrado manualmente e ficou defasado em
relacao aos models (ex.: coluna 'usuario.ativo' que faltava). Quando uma rota
faz SELECT/INSERT em uma tabela que perdeu uma coluna, o MySQL responde
"Unknown column ..." e a aplicacao devolve 500. Este script realinha o banco
sem depender das migrations legadas (que podem falhar por estado parcial).

As colunas faltantes sao adicionadas como NULL (ou com DEFAULT quando o model
define um valor escalar), para nunca quebrar em linhas ja existentes. Os valores
padrao definidos no Python continuam populando os novos registros.

Uso no servidor (PythonAnywhere):

    cd /home/felipefazevedo/conferencia_system
    source /home/felipefazevedo/.virtualenvs/conferencia-env/bin/activate
    export SKIP_APP_BOOTSTRAP=1
    python scripts/sync_missing_columns.py
    unset SKIP_APP_BOOTSTRAP

Depois: Reload no painel Web.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Evita que o bootstrap do app rode ao importar (ele consulta colunas que talvez
# ainda nao existam).
os.environ.setdefault("SKIP_APP_BOOTSTRAP", "1")

from sqlalchemy import inspect, text  # noqa: E402
from sqlalchemy.schema import CreateTable  # noqa: E402

from conferencia_app import create_app  # noqa: E402
from conferencia_app.extensions import db  # noqa: E402


def _default_literal(column) -> str | None:
    """Retorna um literal SQL para o DEFAULT da coluna, quando for um valor
    escalar simples (bool/int/float/str). Caso contrario, None."""
    default = column.default
    if default is None or not getattr(default, "is_scalar", False):
        return None
    valor = default.arg
    if isinstance(valor, bool):
        return "1" if valor else "0"
    if isinstance(valor, (int, float)):
        return str(valor)
    if isinstance(valor, str):
        return "'" + valor.replace("'", "''") + "'"
    return None


def _add_column_sql(dialect, table_name: str, column) -> str:
    tipo = column.type.compile(dialect=dialect)
    partes = [f'ALTER TABLE "{table_name}" ADD COLUMN "{column.name}" {tipo}']
    # Reescreve aspas para o MySQL usar crase; SQLite aceita aspas duplas.
    literal = _default_literal(column)
    # Adiciona sempre como NULL-avel para nao falhar em linhas ja existentes;
    # o DEFAULT (quando houver) cuida dos proximos INSERTs feitos direto no SQL.
    if literal is not None:
        partes.append(f"DEFAULT {literal}")
    sql = " ".join(partes)
    if dialect.name == "mysql":
        sql = sql.replace('"', "`")
    return sql


def main() -> int:
    app = create_app()
    with app.app_context():
        bind = db.engine
        dialect = bind.dialect
        inspector = inspect(bind)

        # Mostra ANTES de tudo em qual banco vamos mexer, com a senha mascarada.
        # Isso evita rodar sem querer no SQLite local achando que era o MySQL.
        try:
            url = bind.url
            alvo = f"{url.drivername} | host={url.host or '(local)'} | db={url.database}"
        except Exception:  # noqa: BLE001
            alvo = dialect.name
        print("=" * 60)
        print(f"BANCO ALVO: {alvo}")
        print("=" * 60)
        if dialect.name == "sqlite":
            print("")
            print("################################################################")
            print("# ATENCAO: este e o banco SQLITE (nao e o MySQL de producao!).  #")
            print("# Se voce queria corrigir a PRODUCAO, ABORTE (Ctrl+C) e rode    #")
            print("# depois de exportar a DATABASE_URL do MySQL. Veja instrucoes   #")
            print("# passadas pelo suporte. Continuando em 5s para uso local...    #")
            print("################################################################")
            print("")
            import time

            time.sleep(5)

        existentes_tabelas = set(inspector.get_table_names())

        tabelas_criadas: list[str] = []
        colunas_add: list[str] = []
        erros: list[str] = []

        for table in db.metadata.sorted_tables:
            if table.name not in existentes_tabelas:
                try:
                    table.create(bind=bind)
                    tabelas_criadas.append(table.name)
                    print(f"[+] Tabela criada: {table.name}")
                except Exception as exc:  # noqa: BLE001
                    erros.append(f"criar {table.name}: {exc}")
                    print(f"[ERRO] criar tabela {table.name}: {exc}")
                continue

            cols_db = {c["name"] for c in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in cols_db:
                    continue
                sql = _add_column_sql(dialect, table.name, column)
                try:
                    with bind.begin() as conn:
                        conn.execute(text(sql))
                    colunas_add.append(f"{table.name}.{column.name}")
                    print(f"[+] Coluna adicionada: {table.name}.{column.name}")
                except Exception as exc:  # noqa: BLE001
                    erros.append(f"{table.name}.{column.name}: {exc}")
                    print(f"[ERRO] {table.name}.{column.name}: {exc}")

        print("\n===== RESUMO =====")
        print(f"Banco: {dialect.name}")
        print(f"Tabelas criadas: {tabelas_criadas or 'nenhuma'}")
        print(f"Colunas adicionadas: {colunas_add or 'nenhuma'}")
        if erros:
            print(f"Erros: {erros}")
            return 1
        if not tabelas_criadas and not colunas_add:
            print("Nada a fazer: banco ja esta alinhado com os models.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
