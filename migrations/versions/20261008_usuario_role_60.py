"""widen usuario.role to 60 chars

A Gestão de Acessos passou a criar cargos (CargoAcesso) e nomes como
"Gerente de Manufatura" (21 caracteres) não cabem nos 20 da coluna. Só
aumenta o tamanho: não mexe em dado, nulidade nem default.

No SQLite não faz nada - lá o tamanho do VARCHAR não é aplicado.

Revision ID: 20261008_usuario_role_60
Revises: 20261005_item_nota_retind
Create Date: 2026-10-08
"""

from alembic import op
import sqlalchemy as sa


revision = "20261008_usuario_role_60"
down_revision = "20261005_item_nota_retind"
branch_labels = None
depends_on = None

_TABELA = "usuario"
_COLUNA = "role"
_TAMANHO = 60


def upgrade():
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        return
    inspector = sa.inspect(bind)
    if not inspector.has_table(_TABELA):
        return
    coluna = next((c for c in inspector.get_columns(_TABELA) if c["name"] == _COLUNA), None)
    if coluna is None:
        return
    atual = getattr(coluna["type"], "length", None)
    if atual is None or atual >= _TAMANHO:
        return
    default = coluna.get("default")
    op.alter_column(
        _TABELA,
        _COLUNA,
        existing_type=sa.String(atual),
        type_=sa.String(_TAMANHO),
        existing_nullable=coluna.get("nullable", True),
        existing_server_default=sa.text(default) if default else None,
    )


def downgrade():
    # Encolher a coluna cortaria o cargo de quem ja' usa nome maior que 20.
    pass
