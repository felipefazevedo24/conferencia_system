"""access management invites and audit

Revision ID: 20260803_access_mgmt_invites
Revises: 20260803_extend_planner_kanban
Create Date: 2026-08-03 18:50:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "20260803_access_mgmt_invites"
down_revision = "20260803_extend_planner_kanban"
branch_labels = None
depends_on = None


def _colunas_tabela(inspector, table_name: str) -> set[str]:
    try:
        return {c["name"] for c in inspector.get_columns(table_name)}
    except Exception:
        return set()


def _has_index(inspector, table_name: str, index_name: str) -> bool:
    try:
        return any(idx.get("name") == index_name for idx in inspector.get_indexes(table_name))
    except Exception:
        return False


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)

    if inspector.has_table("usuario"):
        cols = _colunas_tabela(inspector, "usuario")

        if "ativo" not in cols:
            op.add_column("usuario", sa.Column("ativo", sa.Boolean(), nullable=False, server_default=sa.true()))
        if "ultimo_login_em" not in cols:
            op.add_column("usuario", sa.Column("ultimo_login_em", sa.DateTime(), nullable=True))
        if "convite_token_hash" not in cols:
            op.add_column("usuario", sa.Column("convite_token_hash", sa.String(length=64), nullable=True))
        if "convite_expires_at" not in cols:
            op.add_column("usuario", sa.Column("convite_expires_at", sa.DateTime(), nullable=True))
        if "convite_enviado_em" not in cols:
            op.add_column("usuario", sa.Column("convite_enviado_em", sa.DateTime(), nullable=True))
        if "convite_aceito_em" not in cols:
            op.add_column("usuario", sa.Column("convite_aceito_em", sa.DateTime(), nullable=True))
        if "forcar_troca_senha" not in cols:
            op.add_column("usuario", sa.Column("forcar_troca_senha", sa.Boolean(), nullable=False, server_default=sa.false()))
        if "criado_em" not in cols:
            op.add_column("usuario", sa.Column("criado_em", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")))
        if "criado_por" not in cols:
            op.add_column("usuario", sa.Column("criado_por", sa.String(length=100), nullable=True))
        if "atualizado_em" not in cols:
            op.add_column("usuario", sa.Column("atualizado_em", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")))
        if "atualizado_por" not in cols:
            op.add_column("usuario", sa.Column("atualizado_por", sa.String(length=100), nullable=True))

        if not _has_index(inspector, "usuario", "ix_usuario_ativo"):
            op.create_index("ix_usuario_ativo", "usuario", ["ativo"], unique=False)
        if not _has_index(inspector, "usuario", "ix_usuario_ultimo_login_em"):
            op.create_index("ix_usuario_ultimo_login_em", "usuario", ["ultimo_login_em"], unique=False)
        if not _has_index(inspector, "usuario", "ix_usuario_convite_token_hash"):
            op.create_index("ix_usuario_convite_token_hash", "usuario", ["convite_token_hash"], unique=False)
        if not _has_index(inspector, "usuario", "ix_usuario_convite_expires_at"):
            op.create_index("ix_usuario_convite_expires_at", "usuario", ["convite_expires_at"], unique=False)

    if not inspector.has_table("usuario_gestao_auditoria"):
        op.create_table(
            "usuario_gestao_auditoria",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("ator_username", sa.String(length=100), nullable=False),
            sa.Column("alvo_username", sa.String(length=80), nullable=False),
            sa.Column("acao", sa.String(length=60), nullable=False),
            sa.Column("detalhes", sa.Text(), nullable=True),
            sa.Column("ip_address", sa.String(length=64), nullable=True),
            sa.Column("criado_em", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        )
        op.create_index("ix_usuario_gestao_auditoria_ator_username", "usuario_gestao_auditoria", ["ator_username"], unique=False)
        op.create_index("ix_usuario_gestao_auditoria_alvo_username", "usuario_gestao_auditoria", ["alvo_username"], unique=False)
        op.create_index("ix_usuario_gestao_auditoria_acao", "usuario_gestao_auditoria", ["acao"], unique=False)
        op.create_index("ix_usuario_gestao_auditoria_criado_em", "usuario_gestao_auditoria", ["criado_em"], unique=False)


def downgrade():
    bind = op.get_bind()
    inspector = inspect(bind)

    if inspector.has_table("usuario_gestao_auditoria"):
        if _has_index(inspector, "usuario_gestao_auditoria", "ix_usuario_gestao_auditoria_criado_em"):
            op.drop_index("ix_usuario_gestao_auditoria_criado_em", table_name="usuario_gestao_auditoria")
        if _has_index(inspector, "usuario_gestao_auditoria", "ix_usuario_gestao_auditoria_acao"):
            op.drop_index("ix_usuario_gestao_auditoria_acao", table_name="usuario_gestao_auditoria")
        if _has_index(inspector, "usuario_gestao_auditoria", "ix_usuario_gestao_auditoria_alvo_username"):
            op.drop_index("ix_usuario_gestao_auditoria_alvo_username", table_name="usuario_gestao_auditoria")
        if _has_index(inspector, "usuario_gestao_auditoria", "ix_usuario_gestao_auditoria_ator_username"):
            op.drop_index("ix_usuario_gestao_auditoria_ator_username", table_name="usuario_gestao_auditoria")
        op.drop_table("usuario_gestao_auditoria")

    if inspector.has_table("usuario"):
        cols = _colunas_tabela(inspector, "usuario")

        if _has_index(inspector, "usuario", "ix_usuario_convite_expires_at"):
            op.drop_index("ix_usuario_convite_expires_at", table_name="usuario")
        if _has_index(inspector, "usuario", "ix_usuario_convite_token_hash"):
            op.drop_index("ix_usuario_convite_token_hash", table_name="usuario")
        if _has_index(inspector, "usuario", "ix_usuario_ultimo_login_em"):
            op.drop_index("ix_usuario_ultimo_login_em", table_name="usuario")
        if _has_index(inspector, "usuario", "ix_usuario_ativo"):
            op.drop_index("ix_usuario_ativo", table_name="usuario")

        if "atualizado_por" in cols:
            op.drop_column("usuario", "atualizado_por")
        if "atualizado_em" in cols:
            op.drop_column("usuario", "atualizado_em")
        if "criado_por" in cols:
            op.drop_column("usuario", "criado_por")
        if "criado_em" in cols:
            op.drop_column("usuario", "criado_em")
        if "forcar_troca_senha" in cols:
            op.drop_column("usuario", "forcar_troca_senha")
        if "convite_aceito_em" in cols:
            op.drop_column("usuario", "convite_aceito_em")
        if "convite_enviado_em" in cols:
            op.drop_column("usuario", "convite_enviado_em")
        if "convite_expires_at" in cols:
            op.drop_column("usuario", "convite_expires_at")
        if "convite_token_hash" in cols:
            op.drop_column("usuario", "convite_token_hash")
        if "ultimo_login_em" in cols:
            op.drop_column("usuario", "ultimo_login_em")
        if "ativo" in cols:
            op.drop_column("usuario", "ativo")
