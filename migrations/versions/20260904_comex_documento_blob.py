"""add dados/mimetype columns to comex_documento (guarda o arquivo direto
no banco, sem depender de Google Drive nem de disco do servidor)

Revision ID: 20260904_comex_documento_blob
Revises: 20260903_inventario_analise_causa
Create Date: 2026-09-04
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.mysql import LONGBLOB


revision = "20260904_comex_documento_blob"
down_revision = "20260903_inventario_analise_causa"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("comex_documento"):
        return
    colunas = {c["name"] for c in inspector.get_columns("comex_documento")}

    if "dados" not in colunas:
        op.add_column(
            "comex_documento",
            sa.Column("dados", sa.LargeBinary().with_variant(LONGBLOB, "mysql"), nullable=True),
        )
    if "mimetype" not in colunas:
        op.add_column(
            "comex_documento",
            sa.Column("mimetype", sa.String(length=120), nullable=True),
        )


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("comex_documento"):
        return
    colunas = {c["name"] for c in inspector.get_columns("comex_documento")}
    if "mimetype" in colunas:
        op.drop_column("comex_documento", "mimetype")
    if "dados" in colunas:
        op.drop_column("comex_documento", "dados")
