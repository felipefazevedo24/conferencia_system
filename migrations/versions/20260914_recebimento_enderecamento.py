"""Fila e auditoria do endereçamento após recebimento."""
from alembic import op
import sqlalchemy as sa

revision = "20260914_receb_enderecamento"
down_revision = "20260908_consumo_chapa_confirmacao"
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("recebimento_enderecamento"):
        op.create_table("recebimento_enderecamento",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("item_nota_id", sa.Integer(), sa.ForeignKey("item_nota.id"), nullable=False, unique=True),
            sa.Column("sku", sa.String(80), nullable=False),
            sa.Column("quantidade", sa.Float(), nullable=False),
            sa.Column("versao_leitura", sa.Integer(), nullable=False),
            sa.Column("unidade", sa.String(20)),
            sa.Column("status", sa.String(40), nullable=False),
            sa.Column("criado_em", sa.DateTime(), nullable=False),
            sa.Column("criado_por", sa.String(100), nullable=False),
            sa.Column("concluido_em", sa.DateTime()),
            sa.Column("alocacoes", sa.JSON()),
            sa.Column("motivo", sa.String(30)),
            sa.Column("justificativa", sa.String(500)),
            sa.Column("confirmado_por", sa.String(100)),
            sa.Column("enderecos_antes", sa.JSON()),
            sa.Column("enderecos_enviados", sa.Text()),
            sa.Column("erro", sa.Text()),
            sa.Column("executando_em", sa.DateTime()),
        )
        op.create_index("ix_recebimento_enderecamento_sku", "recebimento_enderecamento", ["sku"])
        op.create_index("ix_recebimento_enderecamento_status", "recebimento_enderecamento", ["status"])
    if not inspector.has_table("recebimento_enderecamento_evento"):
        op.create_table("recebimento_enderecamento_evento",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("tarefa_id", sa.Integer(), sa.ForeignKey("recebimento_enderecamento.id"), nullable=False),
            sa.Column("tipo", sa.String(40), nullable=False),
            sa.Column("usuario", sa.String(100), nullable=False),
            sa.Column("criado_em", sa.DateTime(), nullable=False),
            sa.Column("detalhes", sa.JSON()),
        )
        op.create_index("ix_recebimento_enderecamento_evento_tarefa_id", "recebimento_enderecamento_evento", ["tarefa_id"])
    if not inspector.has_table("recebimento_enderecamento_trava"):
        op.create_table("recebimento_enderecamento_trava",
            sa.Column("sku", sa.String(80), primary_key=True),
            sa.Column("token", sa.String(36)),
            sa.Column("expira_em", sa.DateTime()),
        )


def downgrade():
    op.drop_table("recebimento_enderecamento_evento")
    op.drop_table("recebimento_enderecamento_trava")
    op.drop_table("recebimento_enderecamento")
