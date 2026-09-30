"""add mercado_livre to item_nota

NF de compra feita no Mercado Livre: o pedido continua obrigatório, mas a
divergência de quantidade/valor XML x pedido não bloqueia a liberação nem
dispara aprovação de Compras. Registros antigos ficam False.

Revision ID: 20260930_item_nota_ml
Revises: 20260930_prod_orc_data
Create Date: 2026-09-30
"""

from alembic import op
import sqlalchemy as sa


revision = "20260930_item_nota_ml"
down_revision = "20260930_prod_orc_data"
branch_labels = None
depends_on = None

_TABELA = "item_nota"
_COLUNA = "mercado_livre"


def upgrade():
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table(_TABELA):
        return
    if _COLUNA not in {c["name"] for c in inspector.get_columns(_TABELA)}:
        op.add_column(_TABELA, sa.Column(_COLUNA, sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade():
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table(_TABELA):
        return
    if _COLUNA in {c["name"] for c in inspector.get_columns(_TABELA)}:
        op.drop_column(_TABELA, _COLUNA)
