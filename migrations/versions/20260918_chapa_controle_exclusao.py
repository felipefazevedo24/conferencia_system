"""Exclusão administrativa de itens do controle de chapas."""
from alembic import op
import sqlalchemy as sa

revision = '20260918_chapa_exclusao'
down_revision = '20260918_inv_descricao'
branch_labels = None
depends_on = None


def upgrade():
    if not sa.inspect(op.get_bind()).has_table('chapa_controle_exclusao'):
        op.create_table('chapa_controle_exclusao',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('item_nota_id', sa.Integer(), sa.ForeignKey('item_nota.id', ondelete='CASCADE'), nullable=False, unique=True),
            sa.Column('usuario', sa.String(100), nullable=False),
            sa.Column('criado_em', sa.DateTime(), nullable=False))


def downgrade():
    op.drop_table('chapa_controle_exclusao')
