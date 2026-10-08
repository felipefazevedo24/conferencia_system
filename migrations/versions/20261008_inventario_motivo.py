"""add motivo to inventario (contagem e ajuste)

Quem conta passa a informar o motivo do inventário (transferência de
depósito, correção de saldo, inventário rotativo, outros). O ajuste guarda
um snapshot do motivo da contagem que o abriu. Registros antigos ficam NULL.

Revision ID: 20261008_inventario_motivo
Revises: 20261008_usuario_role_60
Create Date: 2026-10-08
"""

from alembic import op
import sqlalchemy as sa


revision = "20261008_inventario_motivo"
down_revision = "20261008_usuario_role_60"
branch_labels = None
depends_on = None

_COLUNAS = (
    ("logistica_inventario_inicial", "motivo"),
    ("logistica_inventario_ajuste", "motivo_inventario"),
)


def upgrade():
    inspector = sa.inspect(op.get_bind())
    for tabela, coluna in _COLUNAS:
        if not inspector.has_table(tabela):
            continue
        if coluna not in {c["name"] for c in inspector.get_columns(tabela)}:
            op.add_column(tabela, sa.Column(coluna, sa.String(60), nullable=True))


def downgrade():
    inspector = sa.inspect(op.get_bind())
    for tabela, coluna in _COLUNAS:
        if not inspector.has_table(tabela):
            continue
        if coluna in {c["name"] for c in inspector.get_columns(tabela)}:
            op.drop_column(tabela, coluna)
