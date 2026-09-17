"""add finance_documento_grv to logistica_inventario_ajuste

Numero do documento do GRV informado pelo Finance ao confirmar que o
ajuste foi lancado no ERP.

Revision ID: 20260917_inv_finance_grv
Revises: 20260916_inventario_recontagem
Create Date: 2026-09-17
"""

from alembic import op
import sqlalchemy as sa


revision = "20260917_inv_finance_grv"
down_revision = "20260916_inventario_recontagem"
branch_labels = None
depends_on = None

_TABELA = "logistica_inventario_ajuste"
_COLUNA = "finance_documento_grv"


def upgrade():
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table(_TABELA):
        return
    if _COLUNA not in {c["name"] for c in inspector.get_columns(_TABELA)}:
        op.add_column(_TABELA, sa.Column(_COLUNA, sa.String(length=60), nullable=True))


def downgrade():
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table(_TABELA):
        return
    if _COLUNA in {c["name"] for c in inspector.get_columns(_TABELA)}:
        op.drop_column(_TABELA, _COLUNA)
