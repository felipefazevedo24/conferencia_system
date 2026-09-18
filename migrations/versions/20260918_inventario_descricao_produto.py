"""add descricao_produto to logistica_inventario_ajuste

Descricao do produto (tproduto.nome) no momento da deteccao, pra sair na
tabela de itens do FORM-08.52 sem depender do ERP estar no ar.

Revision ID: 20260918_inv_descricao
Revises: 20260917_inv_finance_grv
Create Date: 2026-09-18
"""

from alembic import op
import sqlalchemy as sa


revision = "20260918_inv_descricao"
down_revision = "20260917_inv_finance_grv"
branch_labels = None
depends_on = None

_TABELA = "logistica_inventario_ajuste"
_COLUNA = "descricao_produto"


def upgrade():
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table(_TABELA):
        return
    if _COLUNA not in {c["name"] for c in inspector.get_columns(_TABELA)}:
        op.add_column(_TABELA, sa.Column(_COLUNA, sa.String(length=200), nullable=True))


def downgrade():
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table(_TABELA):
        return
    if _COLUNA in {c["name"] for c in inspector.get_columns(_TABELA)}:
        op.drop_column(_TABELA, _COLUNA)
