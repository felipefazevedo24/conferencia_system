"""Add linha_po_vinculada field to vincular pedido com xml

Revision ID: add_linha_po_vinculada_20260317
Revises: wms_warehouse_management
Create Date: 2026-03-17

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'add_linha_po_vinculada_20260317'
down_revision = 'wms_warehouse_management'
branch_labels = None
depends_on = None


def upgrade():
    # Adicionar coluna para vincular linha do pedido ao item da NF
    op.add_column('item_nota', sa.Column('linha_po_vinculada', sa.Integer, nullable=True, comment='Índice 0-based da linha do PO vinculada manualmente pelo auditor'))


def downgrade():
    op.drop_column('item_nota', 'linha_po_vinculada')
