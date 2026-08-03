"""Add planner kanban tables

Revision ID: 20260803_add_planner_kanban
Revises: 20260420_add_data_emissao
Create Date: 2026-08-03 11:30:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "20260803_add_planner_kanban"
down_revision = "20260420_add_data_emissao"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)

    if not inspector.has_table("planner_board"):
        op.create_table(
            "planner_board",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("nome", sa.String(length=120), nullable=False),
            sa.Column("criado_por", sa.String(length=100), nullable=False),
            sa.Column("criado_em", sa.DateTime(), nullable=False),
            sa.Column("atualizado_em", sa.DateTime(), nullable=False),
        )
        op.create_index("ix_planner_board_nome", "planner_board", ["nome"], unique=True)
        op.create_index("ix_planner_board_criado_em", "planner_board", ["criado_em"], unique=False)
        op.create_index("ix_planner_board_atualizado_em", "planner_board", ["atualizado_em"], unique=False)

    if not inspector.has_table("planner_column"):
        op.create_table(
            "planner_column",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("board_id", sa.Integer(), sa.ForeignKey("planner_board.id"), nullable=False),
            sa.Column("titulo", sa.String(length=80), nullable=False),
            sa.Column("color", sa.String(length=20), nullable=False, server_default="#0f62c9"),
            sa.Column("is_done", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("order_index", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("criado_por", sa.String(length=100), nullable=False),
            sa.Column("criado_em", sa.DateTime(), nullable=False),
            sa.Column("atualizado_em", sa.DateTime(), nullable=False),
        )
        op.create_index("ix_planner_column_board_id", "planner_column", ["board_id"], unique=False)
        op.create_index("ix_planner_column_order_index", "planner_column", ["order_index"], unique=False)
        op.create_index("ix_planner_column_is_done", "planner_column", ["is_done"], unique=False)

    if not inspector.has_table("planner_card"):
        op.create_table(
            "planner_card",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("column_id", sa.Integer(), sa.ForeignKey("planner_column.id"), nullable=False),
            sa.Column("titulo", sa.String(length=180), nullable=False),
            sa.Column("descricao", sa.Text(), nullable=True),
            sa.Column("prioridade", sa.String(length=20), nullable=False, server_default="Media"),
            sa.Column("responsavel", sa.String(length=100), nullable=True),
            sa.Column("prazo", sa.Date(), nullable=True),
            sa.Column("order_index", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("criado_por", sa.String(length=100), nullable=False),
            sa.Column("atualizado_por", sa.String(length=100), nullable=True),
            sa.Column("criado_em", sa.DateTime(), nullable=False),
            sa.Column("atualizado_em", sa.DateTime(), nullable=False),
            sa.Column("concluido_em", sa.DateTime(), nullable=True),
        )
        op.create_index("ix_planner_card_column_id", "planner_card", ["column_id"], unique=False)
        op.create_index("ix_planner_card_prioridade", "planner_card", ["prioridade"], unique=False)
        op.create_index("ix_planner_card_responsavel", "planner_card", ["responsavel"], unique=False)
        op.create_index("ix_planner_card_prazo", "planner_card", ["prazo"], unique=False)
        op.create_index("ix_planner_card_order_index", "planner_card", ["order_index"], unique=False)


def downgrade():
    bind = op.get_bind()
    inspector = inspect(bind)

    if inspector.has_table("planner_card"):
        op.drop_index("ix_planner_card_order_index", table_name="planner_card")
        op.drop_index("ix_planner_card_prazo", table_name="planner_card")
        op.drop_index("ix_planner_card_responsavel", table_name="planner_card")
        op.drop_index("ix_planner_card_prioridade", table_name="planner_card")
        op.drop_index("ix_planner_card_column_id", table_name="planner_card")
        op.drop_table("planner_card")

    if inspector.has_table("planner_column"):
        op.drop_index("ix_planner_column_is_done", table_name="planner_column")
        op.drop_index("ix_planner_column_order_index", table_name="planner_column")
        op.drop_index("ix_planner_column_board_id", table_name="planner_column")
        op.drop_table("planner_column")

    if inspector.has_table("planner_board"):
        op.drop_index("ix_planner_board_atualizado_em", table_name="planner_board")
        op.drop_index("ix_planner_board_criado_em", table_name="planner_board")
        op.drop_index("ix_planner_board_nome", table_name="planner_board")
        op.drop_table("planner_board")
