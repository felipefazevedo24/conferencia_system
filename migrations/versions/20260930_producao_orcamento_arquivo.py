"""Arquivamento reversivel de orcamentos no cronograma de Producao.

Revision ID: 20260930_prod_orc_arquivo
Revises: 20260929_diverg_cnpj
Create Date: 2026-09-30
"""

from alembic import op
import sqlalchemy as sa


revision = "20260930_prod_orc_arquivo"
down_revision = "20260929_diverg_cnpj"
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table("producao_orcamento_arquivo"):
        return
    op.create_table(
        "producao_orcamento_arquivo",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("usuario", sa.String(100), nullable=False),
        sa.Column("numero_orcamento", sa.String(80), nullable=False),
        sa.Column("versao", sa.String(30), nullable=False, server_default=""),
        sa.Column("ativo", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("arquivado_em", sa.DateTime(), nullable=False),
        sa.Column("restaurado_em", sa.DateTime()),
        sa.Column("atualizado_em", sa.DateTime(), nullable=False),
        sa.UniqueConstraint(
            "usuario", "numero_orcamento", "versao",
            name="uq_producao_orcamento_arquivo_usuario_orcamento",
        ),
    )
    op.create_index("ix_producao_orcamento_arquivo_usuario", "producao_orcamento_arquivo", ["usuario"])
    op.create_index("ix_producao_orcamento_arquivo_numero", "producao_orcamento_arquivo", ["numero_orcamento"])
    op.create_index("ix_producao_orcamento_arquivo_ativo", "producao_orcamento_arquivo", ["ativo"])


def downgrade():
    op.drop_table("producao_orcamento_arquivo")
