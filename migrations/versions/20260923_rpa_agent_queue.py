"""Fila persistente para o agente Windows do RPA GRV."""

from alembic import op
import sqlalchemy as sa


revision = "20260923_rpa_agent_queue"
down_revision = "20260918_assistencia_tecnica"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "rpa_executor",
        sa.Column("id", sa.String(100), primary_key=True),
        sa.Column("ambiente", sa.String(40), nullable=False),
        sa.Column("hostname", sa.String(160), nullable=False),
        sa.Column("usuario_windows", sa.String(160), nullable=False),
        sa.Column("versao", sa.String(80)),
        sa.Column("desktop_interativo", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("grv_disponivel", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("janela_titulo", sa.String(300)),
        sa.Column("janela_hwnd", sa.String(40)),
        sa.Column("erro", sa.Text()),
        sa.Column("ultima_comunicacao", sa.DateTime(), nullable=False),
        sa.Column("atualizado_em", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_rpa_executor_ambiente", "rpa_executor", ["ambiente"])
    op.create_index("ix_rpa_executor_ultima_comunicacao", "rpa_executor", ["ultima_comunicacao"])

    op.create_table(
        "rpa_execucao",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("ambiente", sa.String(40), nullable=False),
        sa.Column("usuario_solicitante", sa.String(100), nullable=False),
        sa.Column("descricao", sa.String(80), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="PENDING"),
        sa.Column("executor_id", sa.String(100), sa.ForeignKey("rpa_executor.id")),
        sa.Column("tentativas", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("erro", sa.Text()),
        sa.Column("resultado_json", sa.Text()),
        sa.Column("criada_em", sa.DateTime(), nullable=False),
        sa.Column("reivindicada_em", sa.DateTime()),
        sa.Column("iniciada_em", sa.DateTime()),
        sa.Column("finalizada_em", sa.DateTime()),
        sa.Column("atualizada_em", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_rpa_execucao_ambiente", "rpa_execucao", ["ambiente"])
    op.create_index("ix_rpa_execucao_usuario_solicitante", "rpa_execucao", ["usuario_solicitante"])
    op.create_index("ix_rpa_execucao_fingerprint", "rpa_execucao", ["fingerprint"])
    op.create_index("ix_rpa_execucao_status", "rpa_execucao", ["status"])
    op.create_index("ix_rpa_execucao_executor_id", "rpa_execucao", ["executor_id"])
    op.create_index("ix_rpa_execucao_criada_em", "rpa_execucao", ["criada_em"])
    op.create_index("ix_rpa_execucao_fila", "rpa_execucao", ["ambiente", "status", "criada_em"])


def downgrade():
    op.drop_table("rpa_execucao")
    op.drop_table("rpa_executor")
