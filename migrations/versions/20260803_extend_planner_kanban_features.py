"""Extend planner kanban with labels, comments and checklist

Revision ID: 20260803_extend_planner_kanban
Revises: 20260803_add_planner_kanban
Create Date: 2026-08-03 13:10:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "20260803_extend_planner_kanban"
down_revision = "20260803_add_planner_kanban"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)

    if not inspector.has_table("planner_label"):
        op.create_table(
            "planner_label",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("board_id", sa.Integer(), sa.ForeignKey("planner_board.id"), nullable=False),
            sa.Column("nome", sa.String(length=60), nullable=False),
            sa.Column("color", sa.String(length=20), nullable=False, server_default="#0f62c9"),
            sa.Column("order_index", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("criado_por", sa.String(length=100), nullable=False),
            sa.Column("criado_em", sa.DateTime(), nullable=False),
        )
        op.create_index("ix_planner_label_board_id", "planner_label", ["board_id"], unique=False)
        op.create_index("ix_planner_label_order_index", "planner_label", ["order_index"], unique=False)

    if not inspector.has_table("planner_card_label"):
        op.create_table(
            "planner_card_label",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("card_id", sa.Integer(), sa.ForeignKey("planner_card.id"), nullable=False),
            sa.Column("label_id", sa.Integer(), sa.ForeignKey("planner_label.id"), nullable=False),
            sa.Column("criado_em", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("card_id", "label_id", name="ux_planner_card_label"),
        )
        op.create_index("ix_planner_card_label_card_id", "planner_card_label", ["card_id"], unique=False)
        op.create_index("ix_planner_card_label_label_id", "planner_card_label", ["label_id"], unique=False)

    if not inspector.has_table("planner_card_comment"):
        op.create_table(
            "planner_card_comment",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("card_id", sa.Integer(), sa.ForeignKey("planner_card.id"), nullable=False),
            sa.Column("texto", sa.Text(), nullable=False),
            sa.Column("criado_por", sa.String(length=100), nullable=False),
            sa.Column("criado_em", sa.DateTime(), nullable=False),
        )
        op.create_index("ix_planner_card_comment_card_id", "planner_card_comment", ["card_id"], unique=False)
        op.create_index("ix_planner_card_comment_criado_em", "planner_card_comment", ["criado_em"], unique=False)

    if not inspector.has_table("planner_checklist_item"):
        op.create_table(
            "planner_checklist_item",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("card_id", sa.Integer(), sa.ForeignKey("planner_card.id"), nullable=False),
            sa.Column("texto", sa.String(length=240), nullable=False),
            sa.Column("is_done", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("order_index", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("criado_por", sa.String(length=100), nullable=False),
            sa.Column("criado_em", sa.DateTime(), nullable=False),
            sa.Column("atualizado_em", sa.DateTime(), nullable=False),
        )
        op.create_index("ix_planner_checklist_item_card_id", "planner_checklist_item", ["card_id"], unique=False)
        op.create_index("ix_planner_checklist_item_is_done", "planner_checklist_item", ["is_done"], unique=False)
        op.create_index("ix_planner_checklist_item_order_index", "planner_checklist_item", ["order_index"], unique=False)


def downgrade():
    bind = op.get_bind()
    inspector = inspect(bind)

    if inspector.has_table("planner_checklist_item"):
        op.drop_index("ix_planner_checklist_item_order_index", table_name="planner_checklist_item")
        op.drop_index("ix_planner_checklist_item_is_done", table_name="planner_checklist_item")
        op.drop_index("ix_planner_checklist_item_card_id", table_name="planner_checklist_item")
        op.drop_table("planner_checklist_item")

    if inspector.has_table("planner_card_comment"):
        op.drop_index("ix_planner_card_comment_criado_em", table_name="planner_card_comment")
        op.drop_index("ix_planner_card_comment_card_id", table_name="planner_card_comment")
        op.drop_table("planner_card_comment")

    if inspector.has_table("planner_card_label"):
        op.drop_index("ix_planner_card_label_label_id", table_name="planner_card_label")
        op.drop_index("ix_planner_card_label_card_id", table_name="planner_card_label")
        op.drop_table("planner_card_label")

    if inspector.has_table("planner_label"):
        op.drop_index("ix_planner_label_order_index", table_name="planner_label")
        op.drop_index("ix_planner_label_board_id", table_name="planner_label")
        op.drop_table("planner_label")
