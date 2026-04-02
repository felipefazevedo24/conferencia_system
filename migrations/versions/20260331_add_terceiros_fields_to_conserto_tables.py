"""Add terceiros tracking fields to conserto tables

Revision ID: 20260331_add_terceiros_fields
Revises: add_remessa_field_20260317
Create Date: 2026-03-31
"""

from alembic import op
import sqlalchemy as sa


revision = "20260331_add_terceiros_fields"
down_revision = "add_remessa_field_20260317"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    estoque_cols = {row[1] for row in bind.execute(sa.text("PRAGMA table_info('conserto_estoque')")).fetchall()}
    baixa_cols = {row[1] for row in bind.execute(sa.text("PRAGMA table_info('conserto_baixa')")).fetchall()}

    if "tipo_controle" not in estoque_cols:
        op.add_column(
            "conserto_estoque",
            sa.Column("tipo_controle", sa.String(length=50), nullable=False, server_default="Meu em poder de terceiros"),
        )
        op.alter_column("conserto_estoque", "tipo_controle", server_default=None)
    if "tipo_operacao" not in estoque_cols:
        op.add_column(
            "conserto_estoque",
            sa.Column("tipo_operacao", sa.String(length=30), nullable=False, server_default="Conserto"),
        )
        op.alter_column("conserto_estoque", "tipo_operacao", server_default=None)
    if "cfop_remessa" not in estoque_cols:
        op.add_column("conserto_estoque", sa.Column("cfop_remessa", sa.String(length=4), nullable=True))

    if "cfop_retorno" not in baixa_cols:
        op.add_column("conserto_baixa", sa.Column("cfop_retorno", sa.String(length=4), nullable=True))


def downgrade():
    bind = op.get_bind()
    estoque_cols = {row[1] for row in bind.execute(sa.text("PRAGMA table_info('conserto_estoque')")).fetchall()}
    baixa_cols = {row[1] for row in bind.execute(sa.text("PRAGMA table_info('conserto_baixa')")).fetchall()}

    if "cfop_retorno" in baixa_cols:
        op.drop_column("conserto_baixa", "cfop_retorno")
    if "cfop_remessa" in estoque_cols:
        op.drop_column("conserto_estoque", "cfop_remessa")
    if "tipo_operacao" in estoque_cols:
        op.drop_column("conserto_estoque", "tipo_operacao")
    if "tipo_controle" in estoque_cols:
        op.drop_column("conserto_estoque", "tipo_controle")
