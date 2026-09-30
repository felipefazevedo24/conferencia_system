"""Arquivamento por bloco exato do cronograma.

Revision ID: 20260930_prod_orc_data
Revises: 20260930_prod_orc_arquivo
Create Date: 2026-09-30
"""

from alembic import op
import sqlalchemy as sa


revision = "20260930_prod_orc_data"
down_revision = "20260930_prod_orc_arquivo"
branch_labels = None
depends_on = None


TABLE = "producao_orcamento_arquivo_bloco"


def upgrade():
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table(TABLE):
        return
    op.create_table(
        TABLE,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("usuario", sa.String(100), nullable=False),
        sa.Column("orcamento_id", sa.String(80), nullable=False),
        sa.Column("numero_orcamento", sa.String(80), nullable=False),
        sa.Column("versao", sa.String(30), nullable=False, server_default=""),
        sa.Column("data_entrega", sa.String(10), nullable=False, server_default=""),
        sa.Column("ativo", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("arquivado_em", sa.DateTime(), nullable=False),
        sa.Column("restaurado_em", sa.DateTime()),
        sa.Column("atualizado_em", sa.DateTime(), nullable=False),
        sa.UniqueConstraint(
            "usuario", "orcamento_id", "data_entrega",
            name="uq_producao_orcamento_arquivo_bloco_registro_data",
        ),
    )
    op.create_index(
        "ix_producao_orcamento_arquivo_bloco_usuario", TABLE, ["usuario"],
    )
    op.create_index(
        "ix_producao_orcamento_arquivo_bloco_orcamento_id", TABLE, ["orcamento_id"],
    )
    op.create_index(
        "ix_producao_orcamento_arquivo_bloco_numero", TABLE, ["numero_orcamento"],
    )
    op.create_index(
        "ix_producao_orcamento_arquivo_bloco_ativo", TABLE, ["ativo"],
    )


def downgrade():
    if sa.inspect(op.get_bind()).has_table(TABLE):
        op.drop_table(TABLE)
