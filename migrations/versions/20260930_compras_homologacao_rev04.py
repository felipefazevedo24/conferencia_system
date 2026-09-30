"""Homologacao de fornecedor: formulario F 066 rev. 04

Colunas novas em compras_homologacao_fornecedor: versao do formulario
(NULL = F-COM-001-01, o das homologacoes antigas), ISO 9001 (certificado +
validade) e o bloco "uso exclusivo da Columbia" (amostra / visita tecnica).
Registros antigos ficam com NULL - continuam no formulario antigo.

A tabela compras_homologacao_responsavel e nova e sai no db.create_all().

Revision ID: 20260930_homolog_rev04
Revises: 20260930_prod_orc_data
Create Date: 2026-09-30
"""

from alembic import op
import sqlalchemy as sa


revision = "20260930_homolog_rev04"
down_revision = "20260930_prod_orc_data"
branch_labels = None
depends_on = None

_TABELA = "compras_homologacao_fornecedor"
_COLUNAS = (
    ("formulario_versao", sa.String(length=20)),
    ("iso9001_certificado", sa.Boolean()),
    ("iso9001_validade", sa.Date()),
    ("amostra_necessaria", sa.String(length=3)),
    ("amostra_obs", sa.String(length=500)),
    ("visita_necessaria", sa.String(length=3)),
    ("visita_obs", sa.String(length=500)),
)


def upgrade():
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table(_TABELA):
        return
    existentes = {c["name"] for c in inspector.get_columns(_TABELA)}
    for nome, tipo in _COLUNAS:
        if nome not in existentes:
            op.add_column(_TABELA, sa.Column(nome, tipo, nullable=True))


def downgrade():
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table(_TABELA):
        return
    existentes = {c["name"] for c in inspector.get_columns(_TABELA)}
    for nome, _ in reversed(_COLUNAS):
        if nome in existentes:
            op.drop_column(_TABELA, nome)
