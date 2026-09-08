"""add observacao/baixado/baixado_em/baixado_por columns to
logistica_consumo_chapa_peca (observacao e confirmacao de baixa POR PECA,
independente da conclusao do Nesting inteiro)

Revision ID: 20260908_consumo_chapa_peca_baixa
Revises: 20260908_consumo_chapa_nesting
Create Date: 2026-09-08
"""

from alembic import op
import sqlalchemy as sa


revision = "20260908_consumo_chapa_peca_baixa"
down_revision = "20260908_consumo_chapa_nesting"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("logistica_consumo_chapa_peca"):
        return
    colunas = {c["name"] for c in inspector.get_columns("logistica_consumo_chapa_peca")}

    if "observacao" not in colunas:
        op.add_column("logistica_consumo_chapa_peca", sa.Column("observacao", sa.Text(), nullable=True))
    if "baixado" not in colunas:
        op.add_column(
            "logistica_consumo_chapa_peca",
            sa.Column("baixado", sa.Boolean(), nullable=False, server_default=sa.false()),
        )
        op.create_index(
            "ix_logistica_consumo_chapa_peca_baixado",
            "logistica_consumo_chapa_peca", ["baixado"],
        )
    if "baixado_em" not in colunas:
        op.add_column("logistica_consumo_chapa_peca", sa.Column("baixado_em", sa.DateTime(), nullable=True))
    if "baixado_por" not in colunas:
        op.add_column("logistica_consumo_chapa_peca", sa.Column("baixado_por", sa.String(length=100), nullable=True))


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("logistica_consumo_chapa_peca"):
        return
    colunas = {c["name"] for c in inspector.get_columns("logistica_consumo_chapa_peca")}
    if "baixado_por" in colunas:
        op.drop_column("logistica_consumo_chapa_peca", "baixado_por")
    if "baixado_em" in colunas:
        op.drop_column("logistica_consumo_chapa_peca", "baixado_em")
    if "baixado" in colunas:
        op.drop_column("logistica_consumo_chapa_peca", "baixado")
    if "observacao" in colunas:
        op.drop_column("logistica_consumo_chapa_peca", "observacao")
