"""add logistica_inventario_relatorio_ajuste table (FORM-08.52 - Ajuste
para Faturamento) + logistica_inventario_ajuste.relatorio_id

Revision ID: 20260904_inventario_relatorio_ajuste
Revises: 20260904_comex_documento_blob
Create Date: 2026-09-04
"""

from alembic import op
import sqlalchemy as sa


revision = "20260904_inventario_relatorio_ajuste"
down_revision = "20260904_comex_documento_blob"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table("logistica_inventario_relatorio_ajuste"):
        op.create_table(
            "logistica_inventario_relatorio_ajuste",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("mes_referencia", sa.Integer(), nullable=False),
            sa.Column("ano_referencia", sa.Integer(), nullable=False),
            sa.Column("sequencial", sa.Integer(), nullable=False),
            sa.Column("numero_documento", sa.String(length=20), nullable=False),
            sa.Column("tipo_ajuste", sa.String(length=60), nullable=False),
            sa.Column("tipo_ajuste_detalhe", sa.String(length=300), nullable=True),
            sa.Column("motivo_ajuste", sa.String(length=60), nullable=False),
            sa.Column("motivo_ajuste_detalhe", sa.Text(), nullable=False),
            sa.Column("deposito_tipo", sa.String(length=80), nullable=False),
            sa.Column("deposito_local", sa.String(length=200), nullable=True),
            sa.Column("responsavel", sa.String(length=100), nullable=True),
            sa.Column("solicitante", sa.String(length=100), nullable=True),
            sa.Column("depto", sa.String(length=100), nullable=True),
            sa.Column("observacoes_ajuste", sa.Text(), nullable=True),
            sa.Column("observacoes_itens", sa.Text(), nullable=True),
            sa.Column("criado_em", sa.DateTime(), nullable=False),
            sa.Column("criado_por", sa.String(length=100), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("numero_documento"),
        )
        op.create_index(
            "ix_logistica_inventario_relatorio_ajuste_numero_documento",
            "logistica_inventario_relatorio_ajuste", ["numero_documento"],
        )
        op.create_index(
            "ix_logistica_inventario_relatorio_ajuste_criado_em",
            "logistica_inventario_relatorio_ajuste", ["criado_em"],
        )

    if inspector.has_table("logistica_inventario_ajuste"):
        colunas = {c["name"] for c in inspector.get_columns("logistica_inventario_ajuste")}
        if "relatorio_id" not in colunas:
            op.add_column(
                "logistica_inventario_ajuste",
                sa.Column("relatorio_id", sa.Integer(), nullable=True),
            )
            op.create_index(
                "ix_logistica_inventario_ajuste_relatorio_id",
                "logistica_inventario_ajuste", ["relatorio_id"],
            )


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if inspector.has_table("logistica_inventario_ajuste"):
        colunas = {c["name"] for c in inspector.get_columns("logistica_inventario_ajuste")}
        if "relatorio_id" in colunas:
            op.drop_column("logistica_inventario_ajuste", "relatorio_id")

    if inspector.has_table("logistica_inventario_relatorio_ajuste"):
        op.drop_table("logistica_inventario_relatorio_ajuste")
