"""add logistica_consumo_chapa_nesting + logistica_consumo_chapa_peca
tables (modulo de Consumo de Chapa / import de relatorio de Nesting)

Revision ID: 20260908_consumo_chapa_nesting
Revises: 20260904_inventario_justificativa_imagem
Create Date: 2026-09-08
"""

from alembic import op
import sqlalchemy as sa


revision = "20260908_consumo_chapa_nesting"
down_revision = "20260904_inventario_justificativa_imagem"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table("logistica_consumo_chapa_nesting"):
        op.create_table(
            "logistica_consumo_chapa_nesting",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("numero_programa", sa.String(length=30), nullable=False),
            sa.Column("pagina_atual", sa.Integer(), nullable=True),
            sa.Column("pagina_total", sa.Integer(), nullable=True),
            sa.Column("programador", sa.String(length=100), nullable=True),
            sa.Column("maquina", sa.String(length=120), nullable=True),
            sa.Column("data_corte", sa.Date(), nullable=True),
            sa.Column("hora_corte", sa.String(length=10), nullable=True),
            sa.Column("tempo_corte", sa.String(length=20), nullable=True),
            sa.Column("material", sa.String(length=200), nullable=True),
            sa.Column("codigo_material", sa.String(length=60), nullable=True),
            sa.Column("espessura_mm", sa.Float(), nullable=True),
            sa.Column("nome_tarefa", sa.String(length=100), nullable=True),
            sa.Column("qtde_chapas", sa.Integer(), nullable=True),
            sa.Column("peso_sucata_kg", sa.Float(), nullable=True),
            sa.Column("peso_pecas_kg", sa.Float(), nullable=True),
            sa.Column("peso_retalho_kg", sa.Float(), nullable=True),
            sa.Column("peso_total_kg", sa.Float(), nullable=True),
            sa.Column("aproveitamento_pct", sa.Float(), nullable=True),
            sa.Column("retalho_pct", sa.Float(), nullable=True),
            sa.Column("sucata_pct", sa.Float(), nullable=True),
            sa.Column("status", sa.String(length=20), nullable=False, server_default="Nesting"),
            sa.Column("arquivo_origem", sa.String(length=260), nullable=True),
            sa.Column("criado_em", sa.DateTime(), nullable=False),
            sa.Column("criado_por", sa.String(length=100), nullable=True),
            sa.Column("concluido_em", sa.DateTime(), nullable=True),
            sa.Column("concluido_por", sa.String(length=100), nullable=True),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("numero_programa"),
        )
        op.create_index(
            "ix_logistica_consumo_chapa_nesting_numero_programa",
            "logistica_consumo_chapa_nesting", ["numero_programa"],
        )
        op.create_index(
            "ix_logistica_consumo_chapa_nesting_codigo_material",
            "logistica_consumo_chapa_nesting", ["codigo_material"],
        )
        op.create_index(
            "ix_logistica_consumo_chapa_nesting_status",
            "logistica_consumo_chapa_nesting", ["status"],
        )
        op.create_index(
            "ix_logistica_consumo_chapa_nesting_criado_em",
            "logistica_consumo_chapa_nesting", ["criado_em"],
        )

    if not inspector.has_table("logistica_consumo_chapa_peca"):
        op.create_table(
            "logistica_consumo_chapa_peca",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("nesting_id", sa.Integer(), nullable=False),
            sa.Column("peca_numero", sa.String(length=20), nullable=True),
            sa.Column("nome_peca", sa.String(length=200), nullable=True),
            sa.Column("qtd_requerida", sa.Float(), nullable=True),
            sa.Column("qtd_arranjada", sa.Float(), nullable=True),
            sa.Column("peso_liquido_kg", sa.Float(), nullable=True),
            sa.Column("prox_operacao", sa.String(length=60), nullable=True),
            sa.Column("cliente", sa.String(length=150), nullable=True),
            sa.Column("os_orcamento", sa.String(length=60), nullable=True),
            sa.Column("os_numero", sa.String(length=20), nullable=True),
            sa.ForeignKeyConstraint(["nesting_id"], ["logistica_consumo_chapa_nesting.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            "ix_logistica_consumo_chapa_peca_nesting_id",
            "logistica_consumo_chapa_peca", ["nesting_id"],
        )
        op.create_index(
            "ix_logistica_consumo_chapa_peca_os_numero",
            "logistica_consumo_chapa_peca", ["os_numero"],
        )


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table("logistica_consumo_chapa_peca"):
        op.drop_table("logistica_consumo_chapa_peca")
    if inspector.has_table("logistica_consumo_chapa_nesting"):
        op.drop_table("logistica_consumo_chapa_nesting")
