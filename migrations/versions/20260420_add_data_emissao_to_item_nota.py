"""Add data_emissao column to item_nota (e merge dos heads anteriores)

Revision ID: 20260420_add_data_emissao
Revises: 20260409_add_bofa_fields, 3b6761b3b9e3
Create Date: 2026-04-20 09:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = "20260420_add_data_emissao"
down_revision = ("20260409_add_bofa_fields", "3b6761b3b9e3")
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("item_nota", schema=None) as batch_op:
        batch_op.add_column(sa.Column("data_emissao", sa.DateTime(), nullable=True))
        batch_op.create_index("ix_item_nota_data_emissao", ["data_emissao"])


def downgrade():
    with op.batch_alter_table("item_nota", schema=None) as batch_op:
        batch_op.drop_index("ix_item_nota_data_emissao")
        batch_op.drop_column("data_emissao")
