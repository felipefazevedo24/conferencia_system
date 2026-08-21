"""add soft delete fields to agendamento_solicitacao

Revision ID: 20260821_soft_delete_agendamento
Revises: 20260818_recebimento_evento
Create Date: 2026-08-21
"""

from alembic import op
import sqlalchemy as sa


revision = "20260821_soft_delete_agendamento"
down_revision = "20260818_recebimento_evento"
branch_labels = None
depends_on = None


def _has_column(inspector, table_name: str, column_name: str) -> bool:
    try:
        cols = inspector.get_columns(table_name)
    except Exception:
        return False
    return any(str(c.get("name")) == column_name for c in cols)


def _has_index(inspector, table_name: str, index_name: str) -> bool:
    try:
        indexes = inspector.get_indexes(table_name)
    except Exception:
        return False
    return any(str(i.get("name")) == index_name for i in indexes)


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    table = "agendamento_solicitacao"
    if not inspector.has_table(table):
        return

    if not _has_column(inspector, table, "excluida"):
        op.add_column(table, sa.Column("excluida", sa.Boolean(), nullable=False, server_default=sa.false()))
    if not _has_column(inspector, table, "excluida_em"):
        op.add_column(table, sa.Column("excluida_em", sa.DateTime(), nullable=True))
    if not _has_column(inspector, table, "excluida_por"):
        op.add_column(table, sa.Column("excluida_por", sa.String(length=100), nullable=True))
    if not _has_column(inspector, table, "motivo_exclusao"):
        op.add_column(table, sa.Column("motivo_exclusao", sa.String(length=500), nullable=True))

    op.execute("UPDATE agendamento_solicitacao SET excluida = 0 WHERE excluida IS NULL")

    inspector = sa.inspect(bind)
    if not _has_index(inspector, table, "ix_agendamento_solicitacao_excluida"):
        op.create_index("ix_agendamento_solicitacao_excluida", table, ["excluida"])
    if not _has_index(inspector, table, "ix_agendamento_solicitacao_excluida_em"):
        op.create_index("ix_agendamento_solicitacao_excluida_em", table, ["excluida_em"])
    if not _has_index(inspector, table, "ix_agendamento_solicitacao_excluida_por"):
        op.create_index("ix_agendamento_solicitacao_excluida_por", table, ["excluida_por"])


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    table = "agendamento_solicitacao"
    if not inspector.has_table(table):
        return

    for idx in (
        "ix_agendamento_solicitacao_excluida_por",
        "ix_agendamento_solicitacao_excluida_em",
        "ix_agendamento_solicitacao_excluida",
    ):
        if _has_index(inspector, table, idx):
            op.drop_index(idx, table_name=table)

    inspector = sa.inspect(bind)
    if _has_column(inspector, table, "motivo_exclusao"):
        op.drop_column(table, "motivo_exclusao")
    if _has_column(inspector, table, "excluida_por"):
        op.drop_column(table, "excluida_por")
    if _has_column(inspector, table, "excluida_em"):
        op.drop_column(table, "excluida_em")
    if _has_column(inspector, table, "excluida"):
        op.drop_column(table, "excluida")
