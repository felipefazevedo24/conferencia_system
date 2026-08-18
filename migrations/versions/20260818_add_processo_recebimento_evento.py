"""add processo recebimento event audit trail

Revision ID: 20260818_recebimento_evento
Revises: 20260806_comex_tables
Create Date: 2026-08-18
"""

from alembic import op
import sqlalchemy as sa


revision = "20260818_recebimento_evento"
down_revision = "20260806_comex_tables"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table("processo_recebimento_evento"):
        return

    op.create_table(
        "processo_recebimento_evento",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("numero_nota", sa.String(length=20), nullable=False),
        sa.Column("cnpj_emitente", sa.String(length=20), nullable=True),
        sa.Column("fornecedor", sa.String(length=200), nullable=True),
        sa.Column("categoria", sa.String(length=40), nullable=False),
        sa.Column("acao", sa.String(length=80), nullable=False),
        sa.Column("descricao", sa.String(length=500), nullable=False),
        sa.Column("usuario", sa.String(length=100), nullable=False),
        sa.Column("dados_json", sa.Text(), nullable=True),
        sa.Column("ip_address", sa.String(length=64), nullable=True),
        sa.Column("user_agent", sa.String(length=400), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    for coluna in (
        "numero_nota",
        "cnpj_emitente",
        "fornecedor",
        "categoria",
        "acao",
        "usuario",
        "created_at",
    ):
        op.create_index(
            f"ix_processo_recebimento_evento_{coluna}",
            "processo_recebimento_evento",
            [coluna],
        )


def downgrade():
    bind = op.get_bind()
    if not sa.inspect(bind).has_table("processo_recebimento_evento"):
        return
    op.drop_table("processo_recebimento_evento")