"""add grv snapshot columns to logistica_inventario_inicial

Revision ID: 20260824_inventario_grv_snapshot
Revises: 20260821_soft_delete_agendamento
Create Date: 2026-08-24
"""

from alembic import op
import sqlalchemy as sa


revision = "20260824_inventario_grv_snapshot"
down_revision = "20260821_soft_delete_agendamento"
branch_labels = None
depends_on = None


def _has_column(inspector, table_name: str, column_name: str) -> bool:
    try:
        cols = inspector.get_columns(table_name)
    except Exception:
        return False
    return any(str(c.get("name")) == column_name for c in cols)


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    table = "logistica_inventario_inicial"
    if not inspector.has_table(table):
        return

    if not _has_column(inspector, table, "qtde_grv_no_momento"):
        op.add_column(table, sa.Column("qtde_grv_no_momento", sa.Float(), nullable=True))
    if not _has_column(inspector, table, "grv_consultado_em"):
        op.add_column(table, sa.Column("grv_consultado_em", sa.DateTime(), nullable=True))


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    table = "logistica_inventario_inicial"
    if not inspector.has_table(table):
        return

    if _has_column(inspector, table, "grv_consultado_em"):
        op.drop_column(table, "grv_consultado_em")
    if _has_column(inspector, table, "qtde_grv_no_momento"):
        op.drop_column(table, "qtde_grv_no_momento")
