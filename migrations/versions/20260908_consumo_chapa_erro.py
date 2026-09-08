"""add motivo_erro/erro_marcado_em/erro_marcado_por/erro_resolvido_em/
erro_resolvido_por columns to logistica_consumo_chapa_nesting (ramo
lateral "Erro"/divergencia do workflow, trava o Concluir)

Revision ID: 20260908_consumo_chapa_erro
Revises: 20260908_consumo_chapa_peca_baixa
Create Date: 2026-09-08
"""

from alembic import op
import sqlalchemy as sa


revision = "20260908_consumo_chapa_erro"
down_revision = "20260908_consumo_chapa_peca_baixa"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("logistica_consumo_chapa_nesting"):
        return
    colunas = {c["name"] for c in inspector.get_columns("logistica_consumo_chapa_nesting")}

    if "motivo_erro" not in colunas:
        op.add_column("logistica_consumo_chapa_nesting", sa.Column("motivo_erro", sa.Text(), nullable=True))
    if "erro_marcado_em" not in colunas:
        op.add_column("logistica_consumo_chapa_nesting", sa.Column("erro_marcado_em", sa.DateTime(), nullable=True))
    if "erro_marcado_por" not in colunas:
        op.add_column("logistica_consumo_chapa_nesting", sa.Column("erro_marcado_por", sa.String(length=100), nullable=True))
    if "erro_resolvido_em" not in colunas:
        op.add_column("logistica_consumo_chapa_nesting", sa.Column("erro_resolvido_em", sa.DateTime(), nullable=True))
    if "erro_resolvido_por" not in colunas:
        op.add_column("logistica_consumo_chapa_nesting", sa.Column("erro_resolvido_por", sa.String(length=100), nullable=True))


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("logistica_consumo_chapa_nesting"):
        return
    colunas = {c["name"] for c in inspector.get_columns("logistica_consumo_chapa_nesting")}
    for coluna in ("erro_resolvido_por", "erro_resolvido_em", "erro_marcado_por", "erro_marcado_em", "motivo_erro"):
        if coluna in colunas:
            op.drop_column("logistica_consumo_chapa_nesting", coluna)
