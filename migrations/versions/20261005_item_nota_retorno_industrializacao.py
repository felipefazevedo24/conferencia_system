"""add retorno_industrializacao to item_nota

NF de retorno de industrialização (só CFOP 5902/5903): o auditor marca a
caixa, o Sync confere que a nota é mesmo só desses CFOPs e, com o pedido
informado como em qualquer NF, manda direto para lançamento sem conferência
física - e sem registrar ninguém como conferente. Registros antigos ficam False.

Revision ID: 20261005_item_nota_retind
Revises: 20260930_homolog_rev04
Create Date: 2026-10-05
"""

from alembic import op
import sqlalchemy as sa


revision = "20261005_item_nota_retind"
down_revision = "20260930_homolog_rev04"
branch_labels = None
depends_on = None

_TABELA = "item_nota"
_COLUNA = "retorno_industrializacao"


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
