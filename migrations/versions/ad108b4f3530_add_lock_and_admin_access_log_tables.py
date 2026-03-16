"""add lock and admin access log tables

Revision ID: ad108b4f3530
Revises: 4b8c32202995
Create Date: 2026-03-09 16:25:06.928107

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision = 'ad108b4f3530'
down_revision = '4b8c32202995'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)

    if not inspector.has_table("conferencia_lock"):
        op.create_table(
            "conferencia_lock",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("numero_nota", sa.String(length=20), nullable=False),
            sa.Column("usuario", sa.String(length=100), nullable=False),
            sa.Column("lock_until", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("numero_nota"),
        )
    idx_lock = {idx["name"] for idx in inspector.get_indexes("conferencia_lock")}
    if "ix_conferencia_lock_numero_nota" not in idx_lock:
        op.create_index("ix_conferencia_lock_numero_nota", "conferencia_lock", ["numero_nota"], unique=False)

    if not inspector.has_table("log_acesso_administrativo"):
        op.create_table(
            "log_acesso_administrativo",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("usuario", sa.String(length=100), nullable=False),
            sa.Column("rota", sa.String(length=200), nullable=False),
            sa.Column("metodo", sa.String(length=10), nullable=False),
            sa.Column("data", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )


def downgrade():
    bind = op.get_bind()
    inspector = inspect(bind)

    if inspector.has_table("log_acesso_administrativo"):
        op.drop_table("log_acesso_administrativo")

    if inspector.has_table("conferencia_lock"):
        idx_lock = {idx["name"] for idx in inspector.get_indexes("conferencia_lock")}
        if "ix_conferencia_lock_numero_nota" in idx_lock:
            op.drop_index("ix_conferencia_lock_numero_nota", table_name="conferencia_lock")
        op.drop_table("conferencia_lock")
