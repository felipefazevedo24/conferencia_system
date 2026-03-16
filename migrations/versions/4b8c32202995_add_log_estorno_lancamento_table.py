"""add log estorno lancamento table

Revision ID: 4b8c32202995
Revises: 1ebb54290034
Create Date: 2026-03-09 16:08:29.134186

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision = '4b8c32202995'
down_revision = '1ebb54290034'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)

    if not inspector.has_table("log_estorno_lancamento"):
        op.create_table(
            "log_estorno_lancamento",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("numero_nota", sa.String(length=20), nullable=False),
            sa.Column("usuario_estorno", sa.String(length=100), nullable=False),
            sa.Column("motivo", sa.String(length=500), nullable=False),
            sa.Column("data_estorno", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )

    existing_indexes = {idx["name"] for idx in inspector.get_indexes("log_estorno_lancamento")}
    if "ix_log_estorno_lancamento_numero_nota" not in existing_indexes:
        op.create_index(
            "ix_log_estorno_lancamento_numero_nota",
            "log_estorno_lancamento",
            ["numero_nota"],
            unique=False,
        )


def downgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    if inspector.has_table("log_estorno_lancamento"):
        existing_indexes = {idx["name"] for idx in inspector.get_indexes("log_estorno_lancamento")}
        if "ix_log_estorno_lancamento_numero_nota" in existing_indexes:
            op.drop_index("ix_log_estorno_lancamento_numero_nota", table_name="log_estorno_lancamento")
        op.drop_table("log_estorno_lancamento")
