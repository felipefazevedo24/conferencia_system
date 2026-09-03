"""add logistica_inventario_analise_causa table (Analise de Causa Raiz)

Revision ID: 20260903_inventario_analise_causa
Revises: 20260824_inventario_grv_snapshot
Create Date: 2026-09-03
"""

from alembic import op
import sqlalchemy as sa


revision = "20260903_inventario_analise_causa"
down_revision = "20260824_inventario_grv_snapshot"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table("logistica_inventario_analise_causa"):
        return

    op.create_table(
        "logistica_inventario_analise_causa",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("ajuste_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="Pendente"),
        sa.Column("motivo_causa_raiz", sa.Text(), nullable=True),
        sa.Column("solicitado_por", sa.String(length=100), nullable=False),
        sa.Column("solicitado_em", sa.DateTime(), nullable=False),
        sa.Column("analisado_por", sa.String(length=100), nullable=True),
        sa.Column("analisado_em", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["ajuste_id"], ["logistica_inventario_ajuste.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("ajuste_id"),
    )
    op.create_index(
        "ix_logistica_inventario_analise_causa_ajuste_id",
        "logistica_inventario_analise_causa", ["ajuste_id"],
    )
    op.create_index(
        "ix_logistica_inventario_analise_causa_status",
        "logistica_inventario_analise_causa", ["status"],
    )
    op.create_index(
        "ix_logistica_inventario_analise_causa_solicitado_em",
        "logistica_inventario_analise_causa", ["solicitado_em"],
    )


def downgrade():
    bind = op.get_bind()
    if not sa.inspect(bind).has_table("logistica_inventario_analise_causa"):
        return
    op.drop_table("logistica_inventario_analise_causa")
