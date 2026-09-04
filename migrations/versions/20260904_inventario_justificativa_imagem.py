"""add justificativa_imagem/justificativa_imagem_mimetype/justificativa_imagem_nome
columns to logistica_inventario_ajuste (foto de apoio da justificativa,
guardada direto no banco - mesmo padrao do comex_documento.dados)

Revision ID: 20260904_inventario_justificativa_imagem
Revises: 20260904_inventario_relatorio_ajuste
Create Date: 2026-09-04
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.mysql import LONGBLOB


revision = "20260904_inventario_justificativa_imagem"
down_revision = "20260904_inventario_relatorio_ajuste"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("logistica_inventario_ajuste"):
        return
    colunas = {c["name"] for c in inspector.get_columns("logistica_inventario_ajuste")}

    if "justificativa_imagem" not in colunas:
        op.add_column(
            "logistica_inventario_ajuste",
            sa.Column("justificativa_imagem", sa.LargeBinary().with_variant(LONGBLOB, "mysql"), nullable=True),
        )
    if "justificativa_imagem_mimetype" not in colunas:
        op.add_column(
            "logistica_inventario_ajuste",
            sa.Column("justificativa_imagem_mimetype", sa.String(length=120), nullable=True),
        )
    if "justificativa_imagem_nome" not in colunas:
        op.add_column(
            "logistica_inventario_ajuste",
            sa.Column("justificativa_imagem_nome", sa.String(length=260), nullable=True),
        )


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("logistica_inventario_ajuste"):
        return
    colunas = {c["name"] for c in inspector.get_columns("logistica_inventario_ajuste")}
    if "justificativa_imagem_nome" in colunas:
        op.drop_column("logistica_inventario_ajuste", "justificativa_imagem_nome")
    if "justificativa_imagem_mimetype" in colunas:
        op.drop_column("logistica_inventario_ajuste", "justificativa_imagem_mimetype")
    if "justificativa_imagem" in colunas:
        op.drop_column("logistica_inventario_ajuste", "justificativa_imagem")
