"""Aplica a migration idempotente que aumenta usuario.role de 20 para 60 caracteres (cargos criados na Gestao de Acessos) - ver
migrations/versions/20261008_usuario_role_60.py - direto no banco
configurado (DATABASE_URL ou DB_PATH), sem depender do alembic_version
estar "stampado" - so aumenta a coluna se ela ainda for menor que 60, nunca
apaga ou altera dado.

Uso: DATABASE_URL='...' python scripts/aplicar_migracao_usuario_cargo.py
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
    "migrations", "versions", "20261008_usuario_role_60.py",
)

app = create_app()
with app.app_context():
    spec = importlib.util.spec_from_file_location("usuario_role_60_migration", MIGRATION_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    conn = db.engine.connect()
    ctx = MigrationContext.configure(conn)
    mod.op = Operations(ctx)
    trans = conn.begin()
    try:
        mod.upgrade()
        trans.commit()
        print("Migration Cargo do usuario (usuario.role com 60 caracteres) aplicada com sucesso (idempotente - nada quebra se rodar de novo).")
    except Exception as exc:
        trans.rollback()
        print(f"Falha ao aplicar migration: {exc}")
        raise
    finally:
        conn.close()
