"""Add remessa field to item_nota

Revision ID: add_remessa_field_20260317
Revises: add_linha_po_vinculada_20260317
Create Date: 2026-03-17

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "add_remessa_field_20260317"
down_revision = "add_linha_po_vinculada_20260317"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    existing_cols = {row[1] for row in bind.execute(sa.text("PRAGMA table_info('item_nota')")).fetchall()}
    if "remessa" not in existing_cols:
        op.add_column(
            "item_nota",
            sa.Column("remessa", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        )
        op.alter_column("item_nota", "remessa", server_default=None)


def downgrade():
    bind = op.get_bind()
    existing_cols = {row[1] for row in bind.execute(sa.text("PRAGMA table_info('item_nota')")).fetchall()}
    if "remessa" in existing_cols:
        op.drop_column("item_nota", "remessa")
