"""Cria somente as tabelas do endereçamento no banco explicitamente configurado.

Use o mesmo DATABASE_URL do servidor. --check apenas verifica as tabelas.
Não inicia Flask/schedulers nem modifica o histórico de versões de outras migrations.
"""
import argparse
import importlib.util
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    if not (os.environ.get('DATABASE_URL') or os.environ.get('DB_PATH')):
        parser.error('Defina DATABASE_URL (servidor) ou DB_PATH (SQLite) explicitamente. Use o mesmo banco do aplicativo.')
    from sqlalchemy import create_engine, inspect
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from conferencia_app.config import Config

    engine = create_engine(Config.SQLALCHEMY_DATABASE_URI, pool_pre_ping=True)
    expected = ('recebimento_enderecamento', 'recebimento_enderecamento_evento',
                'recebimento_enderecamento_trava', 'endereco_saldo', 'endereco_movimento')
    try:
        if not args.check:
            with engine.begin() as conn, Operations.context(MigrationContext.configure(conn)):
                for filename in ('20260914_recebimento_enderecamento.py', '20260917_enderecamento_movimentos.py'):
                    spec = importlib.util.spec_from_file_location('migration_enderecamento', ROOT/'migrations'/'versions'/filename)
                    migration = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(migration)
                    migration.upgrade()
        inspector = inspect(engine)
        missing = [name for name in expected if not inspector.has_table(name)]
        if missing:
            print('Tabelas ausentes: ' + ', '.join(missing))
            return 1
        print('Endereçamento: as cinco tabelas estão disponíveis. Nenhum saldo foi importado ou alterado.')
        return 0
    finally:
        engine.dispose()


if __name__ == '__main__':
    raise SystemExit(main())
