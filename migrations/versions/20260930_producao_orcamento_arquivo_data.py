"""Distingue blocos arquivados pela data de entrega.

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


TABLE = "producao_orcamento_arquivo"
LEGACY_UNIQUES = {
    "uq_producao_orcamento_arquivo_usuario_orcamento",
    "uq_producao_orcamento_arquivo_usuario_orcamento_data",
}
NEW_UNIQUE = "uq_producao_orcamento_arquivo_usuario_registro_data"


def _unique_names(inspector) -> set[str]:
    return {
        item["name"] for item in inspector.get_unique_constraints(TABLE)
        if item.get("name")
    }


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table(TABLE):
        return

    columns = {item["name"] for item in inspector.get_columns(TABLE)}
    legacy_schema = "data_entrega" not in columns or "orcamento_id" not in columns
    if "data_entrega" not in columns:
        with op.batch_alter_table(TABLE) as batch_op:
            batch_op.add_column(sa.Column(
                "data_entrega", sa.String(10), nullable=False, server_default="",
            ))
    if "orcamento_id" not in columns:
        with op.batch_alter_table(TABLE) as batch_op:
            batch_op.add_column(sa.Column(
                "orcamento_id", sa.String(80), nullable=False, server_default="",
            ))
    if legacy_schema:
        # O registro interno e a data do card antigo nao podem ser inferidos com
        # seguranca. A preferencia fica no historico, sem afetar varios cards.
        op.execute(sa.text(
            "UPDATE producao_orcamento_arquivo SET ativo = 0"
        ))

    inspector = sa.inspect(bind)
    unique_names = _unique_names(inspector)
    legacy_uniques = LEGACY_UNIQUES & unique_names
    if legacy_uniques or NEW_UNIQUE not in unique_names:
        with op.batch_alter_table(TABLE) as batch_op:
            for constraint_name in sorted(legacy_uniques):
                batch_op.drop_constraint(constraint_name, type_="unique")
            if NEW_UNIQUE not in unique_names:
                batch_op.create_unique_constraint(
                    NEW_UNIQUE,
                    ["usuario", "orcamento_id", "data_entrega"],
                )


def downgrade():
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table(TABLE):
        return
    unique_names = _unique_names(inspector)
    with op.batch_alter_table(TABLE) as batch_op:
        if NEW_UNIQUE in unique_names:
            batch_op.drop_constraint(NEW_UNIQUE, type_="unique")
        old_unique = "uq_producao_orcamento_arquivo_usuario_orcamento"
        if old_unique not in unique_names:
            batch_op.create_unique_constraint(
                old_unique, ["usuario", "numero_orcamento", "versao"],
            )
        columns = {item["name"] for item in inspector.get_columns(TABLE)}
        if "data_entrega" in columns:
            batch_op.drop_column("data_entrega")
        if "orcamento_id" in columns:
            batch_op.drop_column("orcamento_id")
