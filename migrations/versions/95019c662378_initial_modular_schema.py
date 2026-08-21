"""initial modular schema

Revision ID: 95019c662378
Revises: 
Create Date: 2026-03-09 15:47:10.763941

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '95019c662378'
down_revision = None
branch_labels = None
depends_on = None


def _has_table(inspector, table_name: str) -> bool:
    try:
        return inspector.has_table(table_name)
    except Exception:
        return False


def _has_index(inspector, table_name: str, index_name: str) -> bool:
    try:
        indexes = inspector.get_indexes(table_name)
    except Exception:
        return False
    return any(str(idx.get("name") or "") == index_name for idx in indexes)


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if _has_table(inspector, 'item_nota'):
        if not _has_index(inspector, 'item_nota', 'ix_item_nota_numero_nota'):
            op.create_index('ix_item_nota_numero_nota', 'item_nota', ['numero_nota'], unique=False)
        if not _has_index(inspector, 'item_nota', 'ix_item_nota_status'):
            op.create_index('ix_item_nota_status', 'item_nota', ['status'], unique=False)

    if _has_table(inspector, 'log_divergencia'):
        if not _has_index(inspector, 'log_divergencia', 'ix_log_divergencia_numero_nota'):
            op.create_index('ix_log_divergencia_numero_nota', 'log_divergencia', ['numero_nota'], unique=False)

    if _has_table(inspector, 'usuario'):
        if not _has_index(inspector, 'usuario', 'ix_usuario_username'):
            op.create_index('ix_usuario_username', 'usuario', ['username'], unique=True)


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if _has_table(inspector, 'usuario') and _has_index(inspector, 'usuario', 'ix_usuario_username'):
        op.drop_index('ix_usuario_username', table_name='usuario')

    inspector = sa.inspect(bind)
    if _has_table(inspector, 'log_divergencia') and _has_index(inspector, 'log_divergencia', 'ix_log_divergencia_numero_nota'):
        op.drop_index('ix_log_divergencia_numero_nota', table_name='log_divergencia')

    inspector = sa.inspect(bind)
    if _has_table(inspector, 'item_nota') and _has_index(inspector, 'item_nota', 'ix_item_nota_status'):
        op.drop_index('ix_item_nota_status', table_name='item_nota')
    inspector = sa.inspect(bind)
    if _has_table(inspector, 'item_nota') and _has_index(inspector, 'item_nota', 'ix_item_nota_numero_nota'):
        op.drop_index('ix_item_nota_numero_nota', table_name='item_nota')
