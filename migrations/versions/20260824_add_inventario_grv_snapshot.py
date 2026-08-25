"""add grv snapshot columns to logistica_inventario_inicial (+ custo medio)

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
    if inspector.has_table(table):
        if not _has_column(inspector, table, "qtde_grv_no_momento"):
            op.add_column(table, sa.Column("qtde_grv_no_momento", sa.Float(), nullable=True))
        if not _has_column(inspector, table, "grv_consultado_em"):
            op.add_column(table, sa.Column("grv_consultado_em", sa.DateTime(), nullable=True))
        if not _has_column(inspector, table, "custo_medio_no_momento"):
            op.add_column(table, sa.Column("custo_medio_no_momento", sa.Float(), nullable=True))

    # Snapshot do custo medio (tproduto_deposito.custo_medio) no momento em
    # que a divergencia foi detectada - mesma logica de "nunca recalcular
    # depois" que ja vale pra qtde_contada/qtde_estoque_no_momento/diferenca.
    table_ajuste = "logistica_inventario_ajuste"
    if inspector.has_table(table_ajuste):
        if not _has_column(inspector, table_ajuste, "custo_medio"):
            op.add_column(table_ajuste, sa.Column("custo_medio", sa.Float(), nullable=True))


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    table = "logistica_inventario_inicial"
    if inspector.has_table(table):
        if _has_column(inspector, table, "custo_medio_no_momento"):
            op.drop_column(table, "custo_medio_no_momento")
        if _has_column(inspector, table, "grv_consultado_em"):
            op.drop_column(table, "grv_consultado_em")
        if _has_column(inspector, table, "qtde_grv_no_momento"):
            op.drop_column(table, "qtde_grv_no_momento")

    table_ajuste = "logistica_inventario_ajuste"
    if inspector.has_table(table_ajuste):
        if _has_column(inspector, table_ajuste, "custo_medio"):
            op.drop_column(table_ajuste, "custo_medio")
