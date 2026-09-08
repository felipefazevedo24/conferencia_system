"""add confirmado_em/confirmado_por columns to logistica_consumo_chapa_nesting
(status "Nesting Liberado" - logistica confirma recebimento da lista de
separacao antes de liberar a tratativa por peca)

Revision ID: 20260908_consumo_chapa_confirmacao
Revises: 20260908_consumo_chapa_erro
Create Date: 2026-09-08
"""

from alembic import op
import sqlalchemy as sa


revision = "20260908_consumo_chapa_confirmacao"
down_revision = "20260908_consumo_chapa_erro"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("logistica_consumo_chapa_nesting"):
        return
    colunas = {c["name"] for c in inspector.get_columns("logistica_consumo_chapa_nesting")}

    if "confirmado_em" not in colunas:
        op.add_column("logistica_consumo_chapa_nesting", sa.Column("confirmado_em", sa.DateTime(), nullable=True))
    if "confirmado_por" not in colunas:
        op.add_column("logistica_consumo_chapa_nesting", sa.Column("confirmado_por", sa.String(length=100), nullable=True))


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("logistica_consumo_chapa_nesting"):
        return
    colunas = {c["name"] for c in inspector.get_columns("logistica_consumo_chapa_nesting")}
    for coluna in ("confirmado_por", "confirmado_em"):
        if coluna in colunas:
            op.drop_column("logistica_consumo_chapa_nesting", coluna)
