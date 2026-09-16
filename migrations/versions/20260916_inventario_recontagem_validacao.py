"""add recontagem columns to logistica_inventario_ajuste

A validacao do gestor acontece dias depois da contagem. Recontar no dia
da validacao confirma que a apuracao original estava certa (se o saldo
sistemico e o fisico andaram juntos, a diferenca se mantem). Estas
colunas guardam esse segundo levantamento, sem mexer no snapshot
original.

Revision ID: 20260916_inventario_recontagem
Revises: 20260914_comex_campos_por_modulo
Create Date: 2026-09-16
"""

from alembic import op
import sqlalchemy as sa


revision = "20260916_inventario_recontagem"
down_revision = "20260914_comex_campos_por_modulo"
branch_labels = None
depends_on = None

_TABELA = "logistica_inventario_ajuste"
_COLUNAS = (
    ("recontagem_qtde", sa.Float()),
    ("recontagem_estoque", sa.Float()),
    ("recontagem_diferenca", sa.Float()),
    ("recontagem_em", sa.DateTime()),
    ("recontagem_por", sa.String(length=100)),
    ("recontagem_divergente", sa.Boolean()),
    ("recontagem_justificativa", sa.String(length=500)),
)


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table(_TABELA):
        return
    existentes = {c["name"] for c in inspector.get_columns(_TABELA)}
    for nome, tipo in _COLUNAS:
        if nome not in existentes:
            op.add_column(_TABELA, sa.Column(nome, tipo, nullable=True))

    # Ajuste antigo nunca foi recontado - deixa o flag como False (e nao
    # NULL) pra consulta/filtro nao precisar tratar os dois casos.
    if "recontagem_divergente" not in existentes:
        op.execute(sa.text(
            f"UPDATE {_TABELA} SET recontagem_divergente = 0 WHERE recontagem_divergente IS NULL"
        ))


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table(_TABELA):
        return
    existentes = {c["name"] for c in inspector.get_columns(_TABELA)}
    for nome, _ in reversed(_COLUNAS):
        if nome in existentes:
            op.drop_column(_TABELA, nome)
