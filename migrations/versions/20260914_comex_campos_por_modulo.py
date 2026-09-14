"""add numerario/DI/data_fechamento columns to comex_processo

Campos novos do Comex, cada um liberado a partir de um modulo do
workflow (ver comex_service.CAMPOS_OPERACIONAIS):
  - Desembarque: numerario_numero, numerario_valor, numerario_data_pagamento
  - Desembaraco: di_numero, di_data
  - Concluido:   data_fechamento

Nao mexe em nf_recebimento: o campo saiu do formulario, mas a coluna fica
no banco preservando o historico ja preenchido.

Revision ID: 20260914_comex_campos_por_modulo
Revises: 20260908_consumo_chapa_confirmacao
Create Date: 2026-09-14
"""

from alembic import op
import sqlalchemy as sa


revision = "20260914_comex_campos_por_modulo"
down_revision = "20260908_consumo_chapa_confirmacao"
branch_labels = None
depends_on = None

_COLUNAS = (
    ("numerario_numero", sa.String(length=40)),
    ("numerario_valor", sa.Float()),
    ("numerario_data_pagamento", sa.Date()),
    ("di_numero", sa.String(length=40)),
    ("di_data", sa.Date()),
    ("data_fechamento", sa.Date()),
)


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("comex_processo"):
        return
    existentes = {c["name"] for c in inspector.get_columns("comex_processo")}
    for nome, tipo in _COLUNAS:
        if nome not in existentes:
            op.add_column("comex_processo", sa.Column(nome, tipo, nullable=True))


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("comex_processo"):
        return
    existentes = {c["name"] for c in inspector.get_columns("comex_processo")}
    for nome, _ in reversed(_COLUNAS):
        if nome in existentes:
            op.drop_column("comex_processo", nome)
