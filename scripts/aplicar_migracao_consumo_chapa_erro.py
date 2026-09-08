"""Aplica a migration idempotente que adiciona as colunas do ramo lateral
"Erro"/divergencia em logistica_consumo_chapa_nesting
(migrations/versions/20260908_consumo_chapa_erro.py) direto no banco
configurado (DATABASE_URL ou DB_PATH), sem depender do alembic_version
estar "stampado" - so adiciona a coluna se ela ainda nao existir, nunca
apaga ou altera dado.

Uso: DATABASE_URL='...' python scripts/aplicar_migracao_consumo_chapa_erro.py
(SEMPRE prefixe com DATABASE_URL='...' - copiado do arquivo WSGI - senao
roda contra um banco local vazio em vez do MySQL de producao.)
"""
import importlib.util
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from conferencia_app import create_app
from conferencia_app.extensions import db
from alembic.migration import MigrationContext
from alembic.operations import Operations

MIGRATION_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "migrations", "versions", "20260908_consumo_chapa_erro.py",
)

app = create_app()
with app.app_context():
    spec = importlib.util.spec_from_file_location("consumo_chapa_erro_migration", MIGRATION_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    conn = db.engine.connect()
    ctx = MigrationContext.configure(conn)
    mod.op = Operations(ctx)
    trans = conn.begin()
    try:
        mod.upgrade()
        trans.commit()
        print("Migration Consumo de Chapa (ramo Erro/divergencia) aplicada com sucesso (idempotente - nada quebra se rodar de novo).")
    except Exception as exc:
        trans.rollback()
        print(f"Falha ao aplicar migration: {exc}")
        raise
    finally:
        conn.close()
