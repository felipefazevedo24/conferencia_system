"""add comex module tables (comex_processo + satelites)

Revision ID: 20260806_comex_tables
Revises: 20260803_romaneio_foto_carreg
Create Date: 2026-08-06 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision = "20260806_comex_tables"
down_revision = "20260803_romaneio_foto_carreg"
branch_labels = None
depends_on = None


def _create_index_if_missing(inspector, index_name, table_name, columns, unique=False):
    existing = {idx["name"] for idx in inspector.get_indexes(table_name)}
    if index_name not in existing:
        op.create_index(index_name, table_name, columns, unique=unique)


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)

    if not inspector.has_table("comex_processo"):
        op.create_table(
            "comex_processo",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("id_op", sa.String(length=30), nullable=False),
            sa.Column("ref_ff", sa.String(length=80), nullable=True),
            sa.Column("tipo_operacao", sa.String(length=2), nullable=False),
            sa.Column("status_modulo", sa.String(length=40), nullable=False),
            sa.Column("status_slug", sa.String(length=40), nullable=False),
            sa.Column("criado_em", sa.DateTime(), nullable=False),
            sa.Column("criado_por", sa.String(length=100), nullable=False),
            sa.Column("atualizado_em", sa.DateTime(), nullable=False),
            sa.Column("atualizado_por", sa.String(length=100), nullable=True),
            # Modulo 1: OC
            sa.Column("cod_empresa", sa.Integer(), nullable=True),
            sa.Column("cod_ordem_compra", sa.Integer(), nullable=True),
            sa.Column("cod_compra", sa.String(length=40), nullable=True),
            sa.Column("numero_os", sa.String(length=200), nullable=True),
            sa.Column("fornecedor", sa.String(length=200), nullable=True),
            sa.Column("comprador", sa.String(length=100), nullable=True),
            sa.Column("dt_lancamento_oc", sa.DateTime(), nullable=True),
            sa.Column("dt_recebimento_oc", sa.DateTime(), nullable=True),
            sa.Column("total_produtos_oc", sa.Float(), nullable=True),
            sa.Column("total_oc", sa.Float(), nullable=True),
            sa.Column("qtd_linhas_oc", sa.Integer(), nullable=True),
            sa.Column("qtd_produtos_oc", sa.Integer(), nullable=True),
            sa.Column("situacao_oc", sa.String(length=20), nullable=True),
            sa.Column("oc_origem_payload", sa.Text(), nullable=True),
            # Modulo 2: PO
            sa.Column("po_numero", sa.String(length=40), nullable=True),
            sa.Column("po_ocs_vinculadas", sa.Text(), nullable=True),
            sa.Column("pagador_frete", sa.String(length=20), nullable=True),
            sa.Column("po_status", sa.String(length=20), nullable=True),
            sa.Column("po_pdf_file_name", sa.String(length=260), nullable=True),
            sa.Column("po_pdf_file_path", sa.String(length=500), nullable=True),
            sa.Column("po_enviada_em", sa.DateTime(), nullable=True),
            sa.Column("po_enviada_por", sa.String(length=100), nullable=True),
            sa.Column("po_destinatarios_email", sa.String(length=500), nullable=True),
            sa.Column("po_finalizada_sem_envio", sa.Boolean(), nullable=True),
            # Modulo 3: Cotacao (resumo)
            sa.Column("frete_aplicavel", sa.Boolean(), nullable=True),
            sa.Column("cotacao_vencedora_id", sa.Integer(), nullable=True),
            sa.Column("cotacao_justificativa", sa.Text(), nullable=True),
            # Modulos 4-7
            sa.Column("coleta_data", sa.DateTime(), nullable=True),
            sa.Column("em_transito_eta", sa.Date(), nullable=True),
            sa.Column("desembarque_data", sa.DateTime(), nullable=True),
            sa.Column("numero_duimp", sa.String(length=60), nullable=True),
            sa.Column("data_duimp", sa.Date(), nullable=True),
            # Modulo 8: Transporte/Entrega
            sa.Column("entrega_recebida", sa.Boolean(), nullable=True),
            sa.Column("entrega_recebida_em", sa.DateTime(), nullable=True),
            sa.Column("entrega_comentario", sa.Text(), nullable=True),
            sa.Column("entrega_divergencias", sa.Text(), nullable=True),
            # Modulo 9: NF/Cambio
            sa.Column("nf_numero", sa.String(length=40), nullable=True),
            sa.Column("nf_data_emissao", sa.Date(), nullable=True),
            sa.Column("cambio_valor_final", sa.Float(), nullable=True),
            sa.Column("documento_consolidado_file_name", sa.String(length=260), nullable=True),
            sa.Column("documento_consolidado_file_path", sa.String(length=500), nullable=True),
            sa.Column("processo_concluido_em", sa.DateTime(), nullable=True),
            # Colunas de reserva para variaveis futuras
            sa.Column("extra_texto_01", sa.String(length=255), nullable=True),
            sa.Column("extra_texto_02", sa.String(length=255), nullable=True),
            sa.Column("extra_texto_03", sa.String(length=255), nullable=True),
            sa.Column("extra_texto_04", sa.String(length=255), nullable=True),
            sa.Column("extra_texto_05", sa.String(length=255), nullable=True),
            sa.Column("extra_texto_06", sa.String(length=255), nullable=True),
            sa.Column("extra_texto_07", sa.String(length=255), nullable=True),
            sa.Column("extra_texto_08", sa.String(length=255), nullable=True),
            sa.Column("extra_texto_09", sa.String(length=255), nullable=True),
            sa.Column("extra_texto_10", sa.String(length=255), nullable=True),
            sa.Column("extra_texto_11", sa.String(length=255), nullable=True),
            sa.Column("extra_texto_12", sa.String(length=255), nullable=True),
            sa.Column("extra_texto_13", sa.String(length=255), nullable=True),
            sa.Column("extra_texto_14", sa.String(length=255), nullable=True),
            sa.Column("extra_texto_15", sa.String(length=255), nullable=True),
            sa.Column("extra_texto_16", sa.String(length=255), nullable=True),
            sa.Column("extra_texto_17", sa.String(length=255), nullable=True),
            sa.Column("extra_texto_18", sa.String(length=255), nullable=True),
            sa.Column("extra_texto_19", sa.String(length=255), nullable=True),
            sa.Column("extra_texto_20", sa.String(length=255), nullable=True),
            sa.Column("extra_texto_21", sa.String(length=255), nullable=True),
            sa.Column("extra_texto_22", sa.String(length=255), nullable=True),
            sa.Column("extra_texto_23", sa.String(length=255), nullable=True),
            sa.Column("extra_texto_24", sa.String(length=255), nullable=True),
            sa.Column("extra_texto_25", sa.String(length=255), nullable=True),
            sa.Column("extra_texto_26", sa.String(length=255), nullable=True),
            sa.Column("extra_texto_27", sa.String(length=255), nullable=True),
            sa.Column("extra_texto_28", sa.String(length=255), nullable=True),
            sa.Column("extra_texto_29", sa.String(length=255), nullable=True),
            sa.Column("extra_texto_30", sa.String(length=255), nullable=True),
            sa.Column("extra_texto_31", sa.String(length=255), nullable=True),
            sa.Column("extra_texto_32", sa.String(length=255), nullable=True),
            sa.Column("extra_texto_33", sa.String(length=255), nullable=True),
            sa.Column("extra_texto_34", sa.String(length=255), nullable=True),
            sa.Column("extra_texto_35", sa.String(length=255), nullable=True),
            sa.Column("extra_texto_36", sa.String(length=255), nullable=True),
            sa.Column("extra_texto_37", sa.String(length=255), nullable=True),
            sa.Column("extra_texto_38", sa.String(length=255), nullable=True),
            sa.Column("extra_texto_39", sa.String(length=255), nullable=True),
            sa.Column("extra_texto_40", sa.String(length=255), nullable=True),
            sa.Column("extra_texto_41", sa.String(length=255), nullable=True),
            sa.Column("extra_texto_42", sa.String(length=255), nullable=True),
            sa.Column("extra_texto_43", sa.String(length=255), nullable=True),
            sa.Column("extra_texto_44", sa.String(length=255), nullable=True),
            sa.Column("extra_texto_45", sa.String(length=255), nullable=True),
            sa.Column("extra_texto_46", sa.String(length=255), nullable=True),
            sa.Column("extra_texto_47", sa.String(length=255), nullable=True),
            sa.Column("extra_texto_48", sa.String(length=255), nullable=True),
            sa.Column("extra_texto_49", sa.String(length=255), nullable=True),
            sa.Column("extra_texto_50", sa.String(length=255), nullable=True),
            sa.Column("extra_numero_01", sa.Float(), nullable=True),
            sa.Column("extra_numero_02", sa.Float(), nullable=True),
            sa.Column("extra_numero_03", sa.Float(), nullable=True),
            sa.Column("extra_numero_04", sa.Float(), nullable=True),
            sa.Column("extra_numero_05", sa.Float(), nullable=True),
            sa.Column("extra_numero_06", sa.Float(), nullable=True),
            sa.Column("extra_numero_07", sa.Float(), nullable=True),
            sa.Column("extra_numero_08", sa.Float(), nullable=True),
            sa.Column("extra_numero_09", sa.Float(), nullable=True),
            sa.Column("extra_numero_10", sa.Float(), nullable=True),
            sa.Column("extra_numero_11", sa.Float(), nullable=True),
            sa.Column("extra_numero_12", sa.Float(), nullable=True),
            sa.Column("extra_numero_13", sa.Float(), nullable=True),
            sa.Column("extra_numero_14", sa.Float(), nullable=True),
            sa.Column("extra_numero_15", sa.Float(), nullable=True),
            sa.Column("extra_numero_16", sa.Float(), nullable=True),
            sa.Column("extra_numero_17", sa.Float(), nullable=True),
            sa.Column("extra_numero_18", sa.Float(), nullable=True),
            sa.Column("extra_numero_19", sa.Float(), nullable=True),
            sa.Column("extra_numero_20", sa.Float(), nullable=True),
            sa.Column("extra_data_01", sa.DateTime(), nullable=True),
            sa.Column("extra_data_02", sa.DateTime(), nullable=True),
            sa.Column("extra_data_03", sa.DateTime(), nullable=True),
            sa.Column("extra_data_04", sa.DateTime(), nullable=True),
            sa.Column("extra_data_05", sa.DateTime(), nullable=True),
            sa.Column("extra_data_06", sa.DateTime(), nullable=True),
            sa.Column("extra_data_07", sa.DateTime(), nullable=True),
            sa.Column("extra_data_08", sa.DateTime(), nullable=True),
            sa.Column("extra_data_09", sa.DateTime(), nullable=True),
            sa.Column("extra_data_10", sa.DateTime(), nullable=True),
            sa.Column("extra_data_11", sa.DateTime(), nullable=True),
            sa.Column("extra_data_12", sa.DateTime(), nullable=True),
            sa.Column("extra_data_13", sa.DateTime(), nullable=True),
            sa.Column("extra_data_14", sa.DateTime(), nullable=True),
            sa.Column("extra_data_15", sa.DateTime(), nullable=True),
            sa.Column("extra_data_16", sa.DateTime(), nullable=True),
            sa.Column("extra_data_17", sa.DateTime(), nullable=True),
            sa.Column("extra_data_18", sa.DateTime(), nullable=True),
            sa.Column("extra_data_19", sa.DateTime(), nullable=True),
            sa.Column("extra_data_20", sa.DateTime(), nullable=True),
            sa.Column("extra_flag_01", sa.Boolean(), nullable=True),
            sa.Column("extra_flag_02", sa.Boolean(), nullable=True),
            sa.Column("extra_flag_03", sa.Boolean(), nullable=True),
            sa.Column("extra_flag_04", sa.Boolean(), nullable=True),
            sa.Column("extra_flag_05", sa.Boolean(), nullable=True),
            sa.Column("extra_flag_06", sa.Boolean(), nullable=True),
            sa.Column("extra_flag_07", sa.Boolean(), nullable=True),
            sa.Column("extra_flag_08", sa.Boolean(), nullable=True),
            sa.Column("extra_flag_09", sa.Boolean(), nullable=True),
            sa.Column("extra_flag_10", sa.Boolean(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )

    if not inspector.has_table("comex_po_item"):
        op.create_table(
            "comex_po_item",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("processo_id", sa.Integer(), nullable=False),
            sa.Column("order_index", sa.Integer(), nullable=False),
            sa.Column("codigo", sa.String(length=60), nullable=True),
            sa.Column("ncm", sa.String(length=20), nullable=True),
            sa.Column("pn", sa.String(length=80), nullable=True),
            sa.Column("descricao", sa.String(length=500), nullable=True),
            sa.Column("quantidade", sa.Float(), nullable=True),
            sa.Column("valor_unitario", sa.Float(), nullable=True),
            sa.Column("valor_total", sa.Float(), nullable=True),
            sa.Column("criado_em", sa.DateTime(), nullable=False),
            sa.Column("atualizado_em", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["processo_id"], ["comex_processo.id"]),
            sa.PrimaryKeyConstraint("id"),
        )

    if not inspector.has_table("comex_cotacao"):
        op.create_table(
            "comex_cotacao",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("processo_id", sa.Integer(), nullable=False),
            sa.Column("fornecedor_frete", sa.String(length=200), nullable=False),
            sa.Column("modal", sa.String(length=40), nullable=True),
            sa.Column("origem", sa.String(length=120), nullable=True),
            sa.Column("destino", sa.String(length=120), nullable=True),
            sa.Column("prazo_estimado", sa.String(length=60), nullable=True),
            sa.Column("valor_frete", sa.Float(), nullable=True),
            sa.Column("seguro", sa.Float(), nullable=True),
            sa.Column("taxas_adicionais", sa.Float(), nullable=True),
            sa.Column("custo_total", sa.Float(), nullable=True),
            sa.Column("is_sugerida_pelo_sistema", sa.Boolean(), nullable=True),
            sa.Column("is_escolhida", sa.Boolean(), nullable=True),
            sa.Column("token_publico_hash", sa.String(length=128), nullable=True),
            sa.Column("token_publico_expira_em", sa.DateTime(), nullable=True),
            sa.Column("email_instrucao_embarque", sa.String(length=255), nullable=True),
            sa.Column("recebida_em", sa.DateTime(), nullable=False),
            sa.Column("criado_por", sa.String(length=100), nullable=True),
            sa.ForeignKeyConstraint(["processo_id"], ["comex_processo.id"]),
            sa.PrimaryKeyConstraint("id"),
        )

    if not inspector.has_table("comex_follow_up"):
        op.create_table(
            "comex_follow_up",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("processo_id", sa.Integer(), nullable=False),
            sa.Column("modulo", sa.String(length=30), nullable=False),
            sa.Column("status_ok", sa.Boolean(), nullable=False),
            sa.Column("criado_em", sa.DateTime(), nullable=False),
            sa.Column("atualizado_em", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["processo_id"], ["comex_processo.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("processo_id", "modulo", name="uq_comex_follow_up_processo_modulo"),
        )

    if not inspector.has_table("comex_follow_up_log"):
        op.create_table(
            "comex_follow_up_log",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("follow_up_id", sa.Integer(), nullable=False),
            sa.Column("texto", sa.Text(), nullable=True),
            sa.Column("documento_file_name", sa.String(length=260), nullable=True),
            sa.Column("documento_file_path", sa.String(length=500), nullable=True),
            sa.Column("autor", sa.String(length=100), nullable=True),
            sa.Column("criado_em", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["follow_up_id"], ["comex_follow_up.id"]),
            sa.PrimaryKeyConstraint("id"),
        )

    if not inspector.has_table("comex_lembrete"):
        op.create_table(
            "comex_lembrete",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("processo_id", sa.Integer(), nullable=False),
            sa.Column("tipo", sa.String(length=40), nullable=False),
            sa.Column("destinatario", sa.String(length=255), nullable=True),
            sa.Column("enviado_em", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["processo_id"], ["comex_processo.id"]),
            sa.PrimaryKeyConstraint("id"),
        )

    if not inspector.has_table("comex_entrega_foto"):
        op.create_table(
            "comex_entrega_foto",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("processo_id", sa.Integer(), nullable=False),
            sa.Column("file_name", sa.String(length=260), nullable=False),
            sa.Column("file_path", sa.String(length=500), nullable=False),
            sa.Column("uploaded_at", sa.DateTime(), nullable=False),
            sa.Column("uploaded_by", sa.String(length=100), nullable=True),
            sa.ForeignKeyConstraint(["processo_id"], ["comex_processo.id"]),
            sa.PrimaryKeyConstraint("id"),
        )

    # Indices (idempotentes - recria o inspector apos as tabelas existirem).
    inspector = inspect(bind)
    _create_index_if_missing(inspector, "ix_comex_processo_id_op", "comex_processo", ["id_op"], unique=True)
    _create_index_if_missing(inspector, "ix_comex_processo_ref_ff", "comex_processo", ["ref_ff"])
    _create_index_if_missing(inspector, "ix_comex_processo_status_modulo", "comex_processo", ["status_modulo"])
    _create_index_if_missing(inspector, "ix_comex_processo_status_slug", "comex_processo", ["status_slug"])
    _create_index_if_missing(inspector, "ix_comex_processo_criado_em", "comex_processo", ["criado_em"])
    _create_index_if_missing(inspector, "ix_comex_processo_cod_empresa", "comex_processo", ["cod_empresa"])
    _create_index_if_missing(inspector, "ix_comex_processo_cod_ordem_compra", "comex_processo", ["cod_ordem_compra"])
    _create_index_if_missing(inspector, "ix_comex_processo_fornecedor", "comex_processo", ["fornecedor"])
    _create_index_if_missing(inspector, "ix_comex_processo_po_numero", "comex_processo", ["po_numero"])
    _create_index_if_missing(inspector, "ix_comex_po_item_processo_id", "comex_po_item", ["processo_id"])
    _create_index_if_missing(inspector, "ix_comex_cotacao_processo_id", "comex_cotacao", ["processo_id"])
    _create_index_if_missing(inspector, "ix_comex_follow_up_processo_id", "comex_follow_up", ["processo_id"])
    _create_index_if_missing(inspector, "ix_comex_follow_up_modulo", "comex_follow_up", ["modulo"])
    _create_index_if_missing(inspector, "ix_comex_follow_up_log_follow_up_id", "comex_follow_up_log", ["follow_up_id"])
    _create_index_if_missing(inspector, "ix_comex_follow_up_log_criado_em", "comex_follow_up_log", ["criado_em"])
    _create_index_if_missing(inspector, "ix_comex_lembrete_processo_id", "comex_lembrete", ["processo_id"])
    _create_index_if_missing(inspector, "ix_comex_entrega_foto_processo_id", "comex_entrega_foto", ["processo_id"])


def downgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    for table in (
        "comex_entrega_foto",
        "comex_lembrete",
        "comex_follow_up_log",
        "comex_follow_up",
        "comex_cotacao",
        "comex_po_item",
        "comex_processo",
    ):
        if inspector.has_table(table):
            op.drop_table(table)
