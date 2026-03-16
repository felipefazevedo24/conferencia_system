"""add reversal log table

Revision ID: 1ebb54290034
Revises: 95019c662378
Create Date: 2026-03-09 15:54:15.140352

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision = '1ebb54290034'
down_revision = '95019c662378'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)

    if not inspector.has_table("log_reversao_conferencia"):
        op.create_table(
            "log_reversao_conferencia",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("numero_nota", sa.String(length=20), nullable=False),
            sa.Column("usuario_reversao", sa.String(length=100), nullable=False),
            sa.Column("motivo", sa.String(length=500), nullable=False),
            sa.Column("data_reversao", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )

    existing_indexes = {idx["name"] for idx in inspector.get_indexes("log_reversao_conferencia")}
    if "ix_log_reversao_conferencia_numero_nota" not in existing_indexes:
        op.create_index(
            "ix_log_reversao_conferencia_numero_nota",
            "log_reversao_conferencia",
            ["numero_nota"],
            unique=False,
        )


def downgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    if inspector.has_table("log_reversao_conferencia"):
        existing_indexes = {idx["name"] for idx in inspector.get_indexes("log_reversao_conferencia")}
        if "ix_log_reversao_conferencia_numero_nota" in existing_indexes:
            op.drop_index("ix_log_reversao_conferencia_numero_nota", table_name="log_reversao_conferencia")
        op.drop_table("log_reversao_conferencia")
