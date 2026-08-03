"""Add BofA fields to boleto_conta_receber

Revision ID: 20260409_add_bofa_fields
Revises: 20260331_add_log_evento_fiscal_nota
Create Date: 2026-04-09 10:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20260409_add_bofa_fields"
down_revision = "20260331_add_log_evento_fiscal_nota"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)

    if not inspector.has_table("boleto_conta_receber"):
        return

    cols = {c["name"] for c in inspector.get_columns("boleto_conta_receber")}
    indexes = {idx["name"] for idx in inspector.get_indexes("boleto_conta_receber")}

    if "cpf_cnpj_pagador" not in cols:
        op.add_column("boleto_conta_receber", sa.Column("cpf_cnpj_pagador", sa.String(18), nullable=True))
    if "nome_pagador" not in cols:
        op.add_column("boleto_conta_receber", sa.Column("nome_pagador", sa.String(200), nullable=True))
    if "vencimento" not in cols:
        op.add_column("boleto_conta_receber", sa.Column("vencimento", sa.Date(), nullable=True))
    if "data_pagamento" not in cols:
        op.add_column("boleto_conta_receber", sa.Column("data_pagamento", sa.Date(), nullable=True))
    if "bofa_id" not in cols:
        op.add_column("boleto_conta_receber", sa.Column("bofa_id", sa.String(100), nullable=True))

    if "ix_boleto_cpf_cnpj_pagador" not in indexes:
        op.create_index("ix_boleto_cpf_cnpj_pagador", "boleto_conta_receber", ["cpf_cnpj_pagador"], unique=False)


def downgrade():
    bind = op.get_bind()
    inspector = inspect(bind)

    if not inspector.has_table("boleto_conta_receber"):
        return

    cols = {c["name"] for c in inspector.get_columns("boleto_conta_receber")}
    indexes = {idx["name"] for idx in inspector.get_indexes("boleto_conta_receber")}

    if "ix_boleto_cpf_cnpj_pagador" in indexes:
        op.drop_index("ix_boleto_cpf_cnpj_pagador", table_name="boleto_conta_receber")

    if "bofa_id" in cols:
        op.drop_column("boleto_conta_receber", "bofa_id")
    if "data_pagamento" in cols:
        op.drop_column("boleto_conta_receber", "data_pagamento")
    if "vencimento" in cols:
        op.drop_column("boleto_conta_receber", "vencimento")
    if "nome_pagador" in cols:
        op.drop_column("boleto_conta_receber", "nome_pagador")
    if "cpf_cnpj_pagador" in cols:
        op.drop_column("boleto_conta_receber", "cpf_cnpj_pagador")
