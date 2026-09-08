"""Aplica a migration idempotente das tabelas do modulo de Consumo de
Chapa / Nesting (migrations/versions/20260908_consumo_chapa_nesting.py)
direto no banco configurado (DATABASE_URL ou DB_PATH), sem depender do
alembic_version estar "stampado" - so cria as tabelas se ainda nao
existirem, nunca apaga ou altera dado.

Tabela nova via classe de model nova -> na pratica db.create_all() ja
cria sozinho no proximo boot do app (Parte 1 basta). Esse script fica
disponivel por completude/consistencia com os outros modulos.

Uso: DATABASE_URL='...' python scripts/aplicar_migracao_consumo_chapa_nesting.py
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
    "migrations", "versions", "20260908_consumo_chapa_nesting.py",
)

app = create_app()
with app.app_context():
    spec = importlib.util.spec_from_file_location("consumo_chapa_nesting_migration", MIGRATION_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    conn = db.engine.connect()
    ctx = MigrationContext.configure(conn)
    mod.op = Operations(ctx)
    trans = conn.begin()
    try:
        mod.upgrade()
        trans.commit()
        print("Migration Consumo de Chapa / Nesting aplicada com sucesso (idempotente - nada quebra se rodar de novo).")
    except Exception as exc:
        trans.rollback()
        print(f"Falha ao aplicar migration: {exc}")
        raise
    finally:
        conn.close()
