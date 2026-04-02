"""Add fiscal event log table for documento de entrada

Revision ID: 20260331_add_log_evento_fiscal_nota
Revises: 20260331_add_terceiros_fields
Create Date: 2026-03-31
"""

from alembic import op
import sqlalchemy as sa


revision = "20260331_add_log_evento_fiscal_nota"
down_revision = "20260331_add_terceiros_fields"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    tabelas = set(sa.inspect(bind).get_table_names())
    if "log_evento_fiscal_nota" in tabelas:
        return

    op.create_table(
        "log_evento_fiscal_nota",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("numero_nota", sa.String(length=20), nullable=False),
        sa.Column("evento", sa.String(length=60), nullable=False),
        sa.Column("etapa", sa.String(length=30), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=True),
        sa.Column("detalhe", sa.String(length=1000), nullable=True),
        sa.Column("payload_json", sa.Text(), nullable=True),
        sa.Column("usuario", sa.String(length=100), nullable=False),
        sa.Column("data", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_log_evento_fiscal_nota_numero_nota", "log_evento_fiscal_nota", ["numero_nota"])
    op.create_index("ix_log_evento_fiscal_nota_evento", "log_evento_fiscal_nota", ["evento"])
    op.create_index("ix_log_evento_fiscal_nota_etapa", "log_evento_fiscal_nota", ["etapa"])
    op.create_index("ix_log_evento_fiscal_nota_status", "log_evento_fiscal_nota", ["status"])
    op.create_index("ix_log_evento_fiscal_nota_data", "log_evento_fiscal_nota", ["data"])


def downgrade():
    bind = op.get_bind()
    tabelas = set(sa.inspect(bind).get_table_names())
    if "log_evento_fiscal_nota" not in tabelas:
        return

    op.drop_index("ix_log_evento_fiscal_nota_data", table_name="log_evento_fiscal_nota")
    op.drop_index("ix_log_evento_fiscal_nota_status", table_name="log_evento_fiscal_nota")
    op.drop_index("ix_log_evento_fiscal_nota_etapa", table_name="log_evento_fiscal_nota")
    op.drop_index("ix_log_evento_fiscal_nota_evento", table_name="log_evento_fiscal_nota")
    op.drop_index("ix_log_evento_fiscal_nota_numero_nota", table_name="log_evento_fiscal_nota")
    op.drop_table("log_evento_fiscal_nota")
