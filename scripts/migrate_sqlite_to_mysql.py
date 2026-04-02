import argparse
from typing import Iterable

from sqlalchemy import MetaData, create_engine, select, text
from sqlalchemy.engine import Engine


SKIPPED_PREFIXES = ("sqlite_", "_alembic_tmp_")


def normalize_database_url(raw_url: str) -> str:
    url = str(raw_url or "").strip()
    if url.startswith("mysql://"):
        return url.replace("mysql://", "mysql+pymysql://", 1)
    return url


def build_target_metadata(source_metadata: MetaData) -> MetaData:
    target_metadata = MetaData()
    for table in get_migratable_tables(source_metadata):
        table.to_metadata(target_metadata)
    return target_metadata


def iter_batches(result, batch_size: int) -> Iterable[list[dict]]:
    while True:
        rows = result.fetchmany(batch_size)
        if not rows:
            return
        yield [dict(row._mapping) for row in rows]


def get_migratable_tables(metadata: MetaData):
    return [table for table in metadata.sorted_tables if not table.name.startswith(SKIPPED_PREFIXES)]


def set_mysql_foreign_key_checks(connection, enabled: bool) -> None:
    if connection.dialect.name == "mysql":
        value = 1 if enabled else 0
        connection.execute(text(f"SET FOREIGN_KEY_CHECKS={value}"))


def clear_target_tables(connection, target_metadata: MetaData) -> None:
    set_mysql_foreign_key_checks(connection, False)
    try:
        for table in reversed(target_metadata.sorted_tables):
            connection.execute(table.delete())
        connection.commit()
    finally:
        set_mysql_foreign_key_checks(connection, True)
        connection.commit()


def copy_all_data(source_engine: Engine, target_engine: Engine, batch_size: int, replace: bool) -> None:
    source_metadata = MetaData()
    source_metadata.reflect(bind=source_engine)

    if not source_metadata.tables:
        raise RuntimeError("Nenhuma tabela foi encontrada no SQLite de origem.")

    target_metadata = build_target_metadata(source_metadata)
    target_metadata.create_all(bind=target_engine)

    with source_engine.connect() as source_conn, target_engine.connect() as target_conn:
        if replace:
            clear_target_tables(target_conn, target_metadata)

        set_mysql_foreign_key_checks(target_conn, False)
        try:
            for source_table in get_migratable_tables(source_metadata):
                target_table = target_metadata.tables[source_table.name]
                result = source_conn.execute(select(source_table))
                total = 0
                for batch in iter_batches(result, batch_size):
                    target_conn.execute(target_table.insert(), batch)
                    target_conn.commit()
                    total += len(batch)
                print(f"[OK] {source_table.name}: {total} registros copiados")
        finally:
            set_mysql_foreign_key_checks(target_conn, True)
            target_conn.commit()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Migra um banco SQLite para MySQL usando SQLAlchemy.")
    parser.add_argument("--source", required=True, help="URL SQLAlchemy do banco SQLite de origem.")
    parser.add_argument("--target", required=True, help="URL SQLAlchemy do banco MySQL de destino.")
    parser.add_argument(
        "--batch-size",
        type=int,
        default=500,
        help="Quantidade de linhas por lote durante a copia. Padrao: 500.",
    )
    parser.add_argument(
        "--replace",
        action="store_true",
        help="Limpa as tabelas do banco de destino antes de copiar os dados.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    source_url = normalize_database_url(args.source)
    target_url = normalize_database_url(args.target)

    source_engine = create_engine(source_url)
    target_engine = create_engine(target_url)

    try:
        copy_all_data(source_engine, target_engine, batch_size=args.batch_size, replace=args.replace)
        print("[OK] Migracao concluida.")
    finally:
        source_engine.dispose()
        target_engine.dispose()


if __name__ == "__main__":
    main()
