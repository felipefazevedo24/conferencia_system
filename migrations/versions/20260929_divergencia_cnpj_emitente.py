"""add cnpj_emitente to divergencia_pedido_aprovacao

Numero de NF nao e unico entre fornecedores: a tela de aprovacao de
divergencia filtrava os itens so pelo numero e misturava NFs homonimas.
Registros antigos ficam com NULL (a tela cai no filtro por fornecedor).

A tabela divergencia_vinculo_ajuste e nova e sai no db.create_all().

Revision ID: 20260929_diverg_cnpj
Revises: 20260923_rpa_agent_queue
Create Date: 2026-09-29
"""

from alembic import op
import sqlalchemy as sa


revision = "20260929_diverg_cnpj"
down_revision = "20260923_rpa_agent_queue"
branch_labels = None
depends_on = None

_TABELA = "divergencia_pedido_aprovacao"
_COLUNA = "cnpj_emitente"


def upgrade():
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table(_TABELA):
        return
    if _COLUNA not in {c["name"] for c in inspector.get_columns(_TABELA)}:
        op.add_column(_TABELA, sa.Column(_COLUNA, sa.String(length=14), nullable=True))


def downgrade():
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table(_TABELA):
        return
    if _COLUNA in {c["name"] for c in inspector.get_columns(_TABELA)}:
        op.drop_column(_TABELA, _COLUNA)
