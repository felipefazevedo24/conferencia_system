"""add expedicao_romaneio_foto_carregamento table

Revision ID: 20260803_romaneio_foto_carreg
Revises: 20260803_access_mgmt_invites
Create Date: 2026-08-03 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision = "20260803_romaneio_foto_carreg"
down_revision = "20260803_access_mgmt_invites"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)

    if not inspector.has_table("expedicao_romaneio_foto_carregamento"):
        op.create_table(
            "expedicao_romaneio_foto_carregamento",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("romaneio_id", sa.Integer(), nullable=False),
            sa.Column("file_name", sa.String(length=260), nullable=False),
            sa.Column("file_path", sa.String(length=500), nullable=False),
            sa.Column("uploaded_at", sa.DateTime(), nullable=False),
            sa.Column("uploaded_by", sa.String(length=100), nullable=True),
            sa.ForeignKeyConstraint(["romaneio_id"], ["expedicao_romaneio.id"]),
            sa.PrimaryKeyConstraint("id"),
        )

    existing_indexes = {
        idx["name"] for idx in inspector.get_indexes("expedicao_romaneio_foto_carregamento")
    } if inspector.has_table("expedicao_romaneio_foto_carregamento") else set()
    if "ix_expedicao_romaneio_foto_carregamento_romaneio_id" not in existing_indexes:
        op.create_index(
            "ix_expedicao_romaneio_foto_carregamento_romaneio_id",
            "expedicao_romaneio_foto_carregamento",
            ["romaneio_id"],
            unique=False,
        )


def downgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    if inspector.has_table("expedicao_romaneio_foto_carregamento"):
        existing_indexes = {
            idx["name"] for idx in inspector.get_indexes("expedicao_romaneio_foto_carregamento")
        }
        if "ix_expedicao_romaneio_foto_carregamento_romaneio_id" in existing_indexes:
            op.drop_index(
                "ix_expedicao_romaneio_foto_carregamento_romaneio_id",
                table_name="expedicao_romaneio_foto_carregamento",
            )
        op.drop_table("expedicao_romaneio_foto_carregamento")
