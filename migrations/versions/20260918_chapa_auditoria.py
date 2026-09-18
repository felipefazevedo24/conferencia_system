"""Auditoria permanente de unidades, cálculos e exclusões de chapas."""
from alembic import op
import sqlalchemy as sa

revision = '20260918_chapa_auditoria'
down_revision = '20260918_chapa_exclusao'
branch_labels = None
depends_on = None


def upgrade():
    if not sa.inspect(op.get_bind()).has_table('chapa_auditoria'):
        op.create_table('chapa_auditoria',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('item_nota_id', sa.Integer(), nullable=False),
            sa.Column('numero_nota', sa.String(20)),
            sa.Column('codigo', sa.String(120)),
            sa.Column('descricao', sa.String(200)),
            sa.Column('ar', sa.String(100)),
            sa.Column('acao', sa.String(60), nullable=False),
            sa.Column('usuario', sa.String(100), nullable=False),
            sa.Column('criado_em', sa.DateTime(), nullable=False),
            sa.Column('antes', sa.JSON()), sa.Column('depois', sa.JSON()))
        for campo in ('item_nota_id', 'numero_nota', 'codigo', 'criado_em'):
            op.create_index('ix_chapa_auditoria_'+campo, 'chapa_auditoria', [campo])


def downgrade():
    op.drop_table('chapa_auditoria')
