"""Add BofA fields to boleto_conta_receber

Revision ID: 20260409_add_bofa_fields
Revises: 20260331_add_log_evento_fiscal_nota
Create Date: 2026-04-09 10:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision = "20260409_add_bofa_fields"
down_revision = "20260331_add_log_evento_fiscal_nota"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("boleto_conta_receber", schema=None) as batch_op:
        batch_op.add_column(sa.Column("cpf_cnpj_pagador", sa.String(18), nullable=True))
        batch_op.add_column(sa.Column("nome_pagador", sa.String(200), nullable=True))
        batch_op.add_column(sa.Column("vencimento", sa.Date(), nullable=True))
        batch_op.add_column(sa.Column("data_pagamento", sa.Date(), nullable=True))
        batch_op.add_column(sa.Column("bofa_id", sa.String(100), nullable=True))
        batch_op.create_index("ix_boleto_cpf_cnpj_pagador", ["cpf_cnpj_pagador"])


def downgrade():
    with op.batch_alter_table("boleto_conta_receber", schema=None) as batch_op:
        batch_op.drop_index("ix_boleto_cpf_cnpj_pagador")
        batch_op.drop_column("bofa_id")
        batch_op.drop_column("data_pagamento")
        batch_op.drop_column("vencimento")
        batch_op.drop_column("nome_pagador")
        batch_op.drop_column("cpf_cnpj_pagador")
