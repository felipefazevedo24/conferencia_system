"""Saldo físico por endereço e movimentos auditáveis do Sync."""
from alembic import op
import sqlalchemy as sa

revision = '20260917_endereco_movimentos'
down_revision = '20260914_receb_enderecamento'
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table('endereco_saldo'):
        op.create_table('endereco_saldo',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('sku', sa.String(80), nullable=False),
            sa.Column('endereco', sa.String(80), nullable=False),
            sa.Column('unidade', sa.String(20), nullable=False),
            sa.Column('quantidade', sa.Numeric(18, 6), nullable=False),
            sa.Column('conferido', sa.Boolean(), nullable=False),
            sa.Column('atualizado_em', sa.DateTime(), nullable=False),
            sa.UniqueConstraint('sku', 'endereco', name='uq_endereco_saldo'))
        op.create_index('ix_endereco_saldo_sku', 'endereco_saldo', ['sku'])
        op.create_index('ix_endereco_saldo_endereco', 'endereco_saldo', ['endereco'])
    if not inspector.has_table('endereco_movimento'):
        op.create_table('endereco_movimento',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('chave', sa.String(100), nullable=False, unique=True),
            sa.Column('sku', sa.String(80), nullable=False),
            sa.Column('unidade', sa.String(20), nullable=False),
            sa.Column('tipo', sa.String(30), nullable=False),
            sa.Column('origem', sa.String(80)),
            sa.Column('destino', sa.String(80)),
            sa.Column('quantidade', sa.Numeric(18, 6), nullable=False),
            sa.Column('usuario', sa.String(100), nullable=False),
            sa.Column('motivo', sa.String(500)),
            sa.Column('detalhes', sa.JSON()),
            sa.Column('criado_em', sa.DateTime(), nullable=False),
            sa.Column('sincronizado_em', sa.DateTime()),
            sa.Column('erro', sa.String(500)))
        op.create_index('ix_endereco_movimento_sku', 'endereco_movimento', ['sku'])
        op.create_index('ix_endereco_movimento_criado_em', 'endereco_movimento', ['criado_em'])


def downgrade():
    op.drop_table('endereco_movimento')
    op.drop_table('endereco_saldo')
