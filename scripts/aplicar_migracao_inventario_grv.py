"""Aplica a migration idempotente do snapshot de GRV no Inventario
(migrations/versions/20260824_add_inventario_grv_snapshot.py) direto no
banco configurado (DATABASE_URL ou DB_PATH), sem depender do
alembic_version estar "stampado" - so adiciona coluna que ainda nao
existe, nunca apaga ou altera dado.

Uso: python scripts/aplicar_migracao_inventario_grv.py
(local: usa o database.db padrao; producao/PythonAnywhere: roda com as
mesmas variaveis de ambiente do app - DATABASE_URL ou DB_PATH - ja
definidas na sessao/console.)
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
    "migrations", "versions", "20260824_add_inventario_grv_snapshot.py",
)

app = create_app()
with app.app_context():
    spec = importlib.util.spec_from_file_location("inventario_grv_migration", MIGRATION_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    conn = db.engine.connect()
    ctx = MigrationContext.configure(conn)
    mod.op = Operations(ctx)
    trans = conn.begin()
    try:
        mod.upgrade()
        trans.commit()
        print("Migration Inventario (snapshot GRV) aplicada com sucesso (idempotente - nada quebra se rodar de novo).")
    except Exception as exc:
        trans.rollback()
        print(f"Falha ao aplicar migration: {exc}")
        raise
    finally:
        conn.close()
