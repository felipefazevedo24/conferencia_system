"""Aplica as colunas do módulo de Assistência Técnica no banco configurado.

Use o mesmo DATABASE_URL do servidor. --check apenas verifica as colunas.
Não inicia Flask/schedulers nem modifica o histórico de versões de outras migrations.

Além de criar as colunas, a migração copia para cada item o tipo de operação,
a NF e o retorno que antes ficavam no cabeçalho — sem isso as solicitações
que já existem apareceriam vazias nas telas novas.
"""
import argparse
import importlib.util
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

COLUNAS_ITEM = ('material_unidade', 'tipo_operacao', 'sera_vendido', 'necessita_retorno', 'status',
                'numero_nf', 'data_emissao_nf', 'data_prevista_retorno',
                'data_efetiva_retorno', 'quantidade_retornada', 'numero_nf_retorno')


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
    try:
        if not args.check:
            with engine.begin() as conn, Operations.context(MigrationContext.configure(conn)):
                spec = importlib.util.spec_from_file_location(
                    'migration_assistencia_tecnica',
                    ROOT / 'migrations' / 'versions' / '20260918_assistencia_tecnica.py')
                migration = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(migration)
                migration.upgrade()
        inspector = inspect(engine)
        if not inspector.has_table('solicitacao_nf_item'):
            print('Tabela solicitacao_nf_item ausente: rode as migrations da Solicitação de NF antes.')
            return 1
        item = {c['name'] for c in inspector.get_columns('solicitacao_nf_item')}
        faltando = [nome for nome in COLUNAS_ITEM if nome not in item]
        if 'item_id' not in {c['name'] for c in inspector.get_columns('solicitacao_nf_log')}:
            faltando.append('solicitacao_nf_log.item_id')
        if faltando:
            print('Colunas ausentes: ' + ', '.join(faltando))
            return 1
        with engine.connect() as conn:
            sem_tipo = conn.exec_driver_sql(
                'select count(*) from solicitacao_nf_item where tipo_operacao is null').scalar()
        if sem_tipo:
            print(f'{sem_tipo} item(ns) sem tipo de operação: o preenchimento do histórico não terminou.')
            return 1
        print('Assistência Técnica: colunas disponíveis e histórico preenchido. Nenhum dado foi apagado.')
        return 0
    finally:
        engine.dispose()


if __name__ == '__main__':
    raise SystemExit(main())
