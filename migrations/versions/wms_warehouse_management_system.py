"""Add WMS (Warehouse Management System) tables

Revision ID: wms_warehouse_management
Revises: ad108b4f3530
Create Date: 2026-03-16 10:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'wms_warehouse_management'
down_revision = 'ad108b4f3530'
branch_labels = None
depends_on = None


def upgrade():
    # Create localizacao_armazem table
    op.create_table(
        'localizacao_armazem',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('codigo', sa.String(50), nullable=False),
        sa.Column('corredor', sa.String(10), nullable=False),
        sa.Column('prateleira', sa.String(10), nullable=False),
        sa.Column('posicao', sa.String(10), nullable=False),
        sa.Column('capacidade_maxima', sa.Float(), nullable=False),
        sa.Column('capacidade_atual', sa.Float(), nullable=False),
        sa.Column('ativo', sa.Boolean(), nullable=False),
        sa.Column('data_criacao', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('codigo', name='uq_localizacao_codigo')
    )
    op.create_index('ix_localizacao_armazem_codigo', 'localizacao_armazem', ['codigo'])

    # Create item_wms table
    op.create_table(
        'item_wms',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('numero_nota', sa.String(20), nullable=False),
        sa.Column('chave_acesso', sa.String(44)),
        sa.Column('fornecedor', sa.String(100)),
        sa.Column('codigo_item', sa.String(50), nullable=False),
        sa.Column('descricao', sa.String(200)),
        sa.Column('qtd_recebida', sa.Float(), nullable=False),
        sa.Column('qtd_atual', sa.Float(), nullable=False),
        sa.Column('unidade', sa.String(20)),
        sa.Column('lote', sa.String(50)),
        sa.Column('data_validade', sa.Date()),
        sa.Column('localizacao_id', sa.Integer()),
        sa.Column('usuario_armazenamento', sa.String(100)),
        sa.Column('data_armazenamento', sa.DateTime()),
        sa.Column('status', sa.String(20), nullable=False),
        sa.Column('ativo', sa.Boolean(), nullable=False),
        sa.Column('data_criacao', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['localizacao_id'], ['localizacao_armazem.id']),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_item_wms_numero_nota', 'item_wms', ['numero_nota'])
    op.create_index('ix_item_wms_codigo_item', 'item_wms', ['codigo_item'])
    op.create_index('ix_item_wms_localizacao_id', 'item_wms', ['localizacao_id'])
    op.create_index('ix_item_wms_status', 'item_wms', ['status'])

    # Create movimentacao_wms table
    op.create_table(
        'movimentacao_wms',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('item_wms_id', sa.Integer(), nullable=False),
        sa.Column('numero_nota', sa.String(20), nullable=False),
        sa.Column('tipo_movimentacao', sa.String(30), nullable=False),
        sa.Column('localizacao_origem_id', sa.Integer()),
        sa.Column('localizacao_destino_id', sa.Integer()),
        sa.Column('qtd_movimentada', sa.Float(), nullable=False),
        sa.Column('motivo', sa.String(300)),
        sa.Column('usuario', sa.String(100), nullable=False),
        sa.Column('data_movimentacao', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['item_wms_id'], ['item_wms.id']),
        sa.ForeignKeyConstraint(['localizacao_origem_id'], ['localizacao_armazem.id']),
        sa.ForeignKeyConstraint(['localizacao_destino_id'], ['localizacao_armazem.id']),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_movimentacao_wms_item_wms_id', 'movimentacao_wms', ['item_wms_id'])
    op.create_index('ix_movimentacao_wms_numero_nota', 'movimentacao_wms', ['numero_nota'])

    # Create estoque_wms table
    op.create_table(
        'estoque_wms',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('codigo_item', sa.String(50), nullable=False),
        sa.Column('localizacao_id', sa.Integer(), nullable=False),
        sa.Column('qtd_total', sa.Float(), nullable=False),
        sa.Column('qtd_separada', sa.Float(), nullable=False),
        sa.Column('data_atualizacao', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['localizacao_id'], ['localizacao_armazem.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('codigo_item', 'localizacao_id', name='_sku_localizacao_uc')
    )
    op.create_index('ix_estoque_wms_codigo_item', 'estoque_wms', ['codigo_item'])
    op.create_index('ix_estoque_wms_localizacao_id', 'estoque_wms', ['localizacao_id'])


def downgrade():
    op.drop_index('ix_estoque_wms_localizacao_id', table_name='estoque_wms')
    op.drop_index('ix_estoque_wms_codigo_item', table_name='estoque_wms')
    op.drop_table('estoque_wms')
    
    op.drop_index('ix_movimentacao_wms_numero_nota', table_name='movimentacao_wms')
    op.drop_index('ix_movimentacao_wms_item_wms_id', table_name='movimentacao_wms')
    op.drop_table('movimentacao_wms')
    
    op.drop_index('ix_item_wms_status', table_name='item_wms')
    op.drop_index('ix_item_wms_localizacao_id', table_name='item_wms')
    op.drop_index('ix_item_wms_codigo_item', table_name='item_wms')
    op.drop_index('ix_item_wms_numero_nota', table_name='item_wms')
    op.drop_table('item_wms')
    
    op.drop_index('ix_localizacao_armazem_codigo', table_name='localizacao_armazem')
    op.drop_table('localizacao_armazem')
