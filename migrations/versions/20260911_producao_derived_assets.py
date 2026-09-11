"""Cache de miniaturas exclusivamente no banco da aplicacao."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.mysql import LONGBLOB

revision = "20260911_producao_assets"
down_revision = "20260908_consumo_chapa_confirmacao"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "derived_assets",
        sa.Column("cache_key", sa.String(64), primary_key=True),
        sa.Column("company_code", sa.Integer(), nullable=False),
        sa.Column("order_number", sa.String(80), nullable=False),
        sa.Column("item_aux_code", sa.Integer(), nullable=False),
        sa.Column("source_filename", sa.String(240), nullable=False),
        sa.Column("source_sha256", sa.String(64), nullable=False),
        sa.Column("media_type", sa.String(40), nullable=False),
        sa.Column("content", sa.LargeBinary().with_variant(LONGBLOB(), "mysql"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_derived_assets_company_code", "derived_assets", ["company_code"])


def downgrade():
    op.drop_index("ix_derived_assets_company_code", table_name="derived_assets")
    op.drop_table("derived_assets")