
from datetime import datetime
from .extensions import db

class ActiveSession(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(100), nullable=False, index=True)
    session_id = db.Column(db.String(128), nullable=False, unique=True, index=True)
    created_at = db.Column(db.DateTime, default=datetime.now, nullable=False)
    last_activity = db.Column(db.DateTime, default=datetime.now, nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)


class Usuario(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False, index=True)
    email = db.Column(db.String(160), unique=True, nullable=True, index=True)
    password = db.Column(db.String(255), nullable=True)
    role = db.Column(db.String(20), default="Logística")


class AvisoAtualizacao(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    titulo = db.Column(db.String(160), nullable=False)
    conteudo = db.Column(db.Text, nullable=False)
    ativo = db.Column(db.Boolean, nullable=False, default=True, index=True)
    exibir_ate = db.Column(db.DateTime, nullable=True, index=True)
    criado_por = db.Column(db.String(100))
    criado_em = db.Column(db.DateTime, default=datetime.now, nullable=False)
    atualizado_por = db.Column(db.String(100))
    atualizado_em = db.Column(db.DateTime, default=datetime.now, nullable=False)


class PermissaoAcesso(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    scope_type = db.Column(db.String(10), nullable=False, index=True)  # ROLE|USER
    scope_id = db.Column(db.String(80), nullable=False, index=True)  # role name or username
    permission_key = db.Column(db.String(80), nullable=False, index=True)
    allow = db.Column(db.Boolean, nullable=False, default=True)
    updated_by = db.Column(db.String(100))
    updated_at = db.Column(db.DateTime, default=datetime.now, nullable=False)

    __table_args__ = (
        db.UniqueConstraint("scope_type", "scope_id", "permission_key", name="_perm_scope_key_uc"),
    )


class CadastroWorkflowSolicitacao(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    numero = db.Column(db.String(12), nullable=False, unique=True, index=True)
    tipo = db.Column(db.String(30), nullable=False, index=True)
    status = db.Column(db.String(40), nullable=False, default="Rascunho", index=True)
    etapa_atual = db.Column(db.String(30), nullable=False, default="Solicitante", index=True)
    solicitante = db.Column(db.String(100), nullable=False, index=True)
    responsavel_atual = db.Column(db.String(100), nullable=True, index=True)
    departamento_atual = db.Column(db.String(30), nullable=False, default="Solicitante", index=True)
    dados_json = db.Column(db.Text, nullable=False, default="{}")
    anexos = db.Column(db.Text)
    alerta_duplicidade = db.Column(db.Text)
    data_abertura = db.Column(db.DateTime, default=datetime.now, nullable=False, index=True)
    data_ultima_movimentacao = db.Column(db.DateTime, default=datetime.now, nullable=False, index=True)
    etapa_iniciada_em = db.Column(db.DateTime, default=datetime.now, nullable=False, index=True)
    concluido_em = db.Column(db.DateTime, nullable=True)
    cancelado_em = db.Column(db.DateTime, nullable=True)

    historicos = db.relationship(
        "CadastroWorkflowHistorico",
        backref="solicitacao",
        cascade="all, delete-orphan",
        order_by="CadastroWorkflowHistorico.data_hora.desc()",
    )
    checklists = db.relationship(
        "CadastroWorkflowChecklist",
        backref="solicitacao",
        cascade="all, delete-orphan",
    )
    notificacoes = db.relationship(
        "CadastroWorkflowNotificacao",
        backref="solicitacao",
        cascade="all, delete-orphan",
    )


class CadastroWorkflowHistorico(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    solicitacao_id = db.Column(db.Integer, db.ForeignKey("cadastro_workflow_solicitacao.id"), nullable=False, index=True)
    data_hora = db.Column(db.DateTime, default=datetime.now, nullable=False, index=True)
    usuario = db.Column(db.String(100), nullable=False)
    departamento = db.Column(db.String(30), nullable=False)
    acao = db.Column(db.String(80), nullable=False, index=True)
    comentario = db.Column(db.String(1000))


class CadastroWorkflowChecklist(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    solicitacao_id = db.Column(db.Integer, db.ForeignKey("cadastro_workflow_solicitacao.id"), nullable=False, index=True)
    departamento = db.Column(db.String(30), nullable=False, index=True)
    item = db.Column(db.String(120), nullable=False)
    valor = db.Column(db.String(20), nullable=False, default="Nao se aplica")
    atualizado_por = db.Column(db.String(100))
    atualizado_em = db.Column(db.DateTime, default=datetime.now, nullable=False)

    __table_args__ = (
        db.UniqueConstraint("solicitacao_id", "departamento", "item", name="ux_cadastro_checklist_item"),
    )


class CadastroWorkflowNotificacao(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    solicitacao_id = db.Column(db.Integer, db.ForeignKey("cadastro_workflow_solicitacao.id"), nullable=False, index=True)
    usuario = db.Column(db.String(100), nullable=False, index=True)
    mensagem = db.Column(db.String(240), nullable=False)
    lida = db.Column(db.Boolean, nullable=False, default=False, index=True)
    criada_em = db.Column(db.DateTime, default=datetime.now, nullable=False, index=True)


class CadastroWorkflowSLAConfig(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    departamento = db.Column(db.String(30), nullable=False, unique=True, index=True)
    horas = db.Column(db.Integer, nullable=False, default=48)
    atualizado_por = db.Column(db.String(100))
    atualizado_em = db.Column(db.DateTime, default=datetime.now, nullable=False)


class ItemNota(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    tipo_documento = db.Column(db.String(10), nullable=False, default="NFE", index=True)
    documento_externo_id = db.Column(db.String(120), index=True)
    codigo_verificacao = db.Column(db.String(40), index=True)
    numero_nota = db.Column(db.String(20), index=True)
    chave_acesso = db.Column(db.String(44))
    cfop = db.Column(db.String(4), index=True)
    fornecedor = db.Column(db.String(100))
    codigo = db.Column(db.String(50))
    codigo_grv = db.Column(db.String(80), index=True)
    cfop_descricao_grv = db.Column(db.String(180))
    descricao = db.Column(db.String(200))
    qtd_real = db.Column(db.Float)
    status = db.Column(db.String(20), default="Pendente", index=True)
    usuario_importacao = db.Column(db.String(100))
    data_importacao = db.Column(db.DateTime, default=datetime.now)
    usuario_conferencia = db.Column(db.String(100))
    inicio_conferencia = db.Column(db.DateTime)
    fim_conferencia = db.Column(db.DateTime)
    usuario_lancamento = db.Column(db.String(100))
    data_lancamento = db.Column(db.DateTime)
    numero_lancamento = db.Column(db.String(80))
    valor_total = db.Column(db.String(50))
    valor_imposto = db.Column(db.String(50))
    unidade_comercial = db.Column(db.String(20))
    cnpj_emitente = db.Column(db.String(14), index=True)
    cnpj_destinatario = db.Column(db.String(14), index=True)
    ncm = db.Column(db.String(8), index=True)
    cst_icms = db.Column(db.String(3), index=True)
    cst_pis = db.Column(db.String(2), index=True)
    cst_cofins = db.Column(db.String(2), index=True)
    valor_produto = db.Column(db.Float)
    valor_nf = db.Column(db.Float)
    icms_base_calculo = db.Column(db.Float)
    icms_aliquota = db.Column(db.Float)
    icms_valor = db.Column(db.Float)
    pis_base_calculo = db.Column(db.Float)
    pis_aliquota = db.Column(db.Float)
    pis_valor_credito = db.Column(db.Float)
    cofins_base_calculo = db.Column(db.Float)
    cofins_aliquota = db.Column(db.Float)
    cofins_valor_credito = db.Column(db.Float)
    tributos_origem = db.Column(db.String(20), index=True)
    tributos_grv_atualizado_em = db.Column(db.DateTime)
    pagamento_xml = db.Column(db.Boolean, nullable=False, default=False, index=True)
    tipo_pagamento_xml = db.Column(db.String(100))
    valor_pagamento_xml = db.Column(db.Float)
    vencimento_pagamento_xml = db.Column(db.DateTime)
    pedido_compra = db.Column(db.String(50), index=True)
    linha_po_vinculada = db.Column(db.Integer, nullable=True, comment="Índice 0-based da linha do PO vinculada manualmente")
    material_cliente = db.Column(db.Boolean, nullable=False, default=False, index=True)
    remessa = db.Column(db.Boolean, nullable=False, default=False, index=True)
    sem_conferencia_logistica = db.Column(db.Boolean, nullable=False, default=False, index=True)
    auditor_status = db.Column(db.String(30), default="NaoAuditado", index=True)
    auditor_decisao = db.Column(db.String(20), default="PendenteDecisao", index=True)
    auditor_diagnostico = db.Column(db.String(4000))
    auditor_inconsistencias = db.Column(db.String(1000))
    auditor_justificativa = db.Column(db.String(500))
    auditor_observacao = db.Column(db.String(500))
    auditor_usuario = db.Column(db.String(100))
    auditor_data = db.Column(db.DateTime)
    # Data de emissao da NF (dhEmi do XML). Usada para integracao com ERP
    # (matching n_nf + dt_nf na tabela tcompras do Postgres).
    data_emissao = db.Column(db.DateTime, nullable=True, index=True)


class ClassificacaoContabilPadrao(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    fornecedor_norm = db.Column(db.String(180), nullable=False, default="", index=True)
    cfop = db.Column(db.String(10), nullable=False, default="", index=True)
    codigo_norm = db.Column(db.String(80), nullable=False, default="", index=True)
    descricao_norm = db.Column(db.String(260), nullable=False, default="", index=True)
    conta = db.Column(db.String(30), nullable=False, index=True)
    nome_conta = db.Column(db.String(180), nullable=False)
    comentario = db.Column(db.String(500))
    ocorrencias = db.Column(db.Integer, nullable=False, default=0)
    origem = db.Column(db.String(120))
    atualizado_em = db.Column(db.DateTime, default=datetime.now, nullable=False, index=True)

    __table_args__ = (
        db.UniqueConstraint(
            "fornecedor_norm",
            "cfop",
            "codigo_norm",
            "descricao_norm",
            "conta",
            name="ux_classificacao_padrao_chave_conta",
        ),
    )


class PlanoContaDominio(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    codigo_conta = db.Column(db.String(30), nullable=False, unique=True, index=True)
    classificacao_conta = db.Column(db.String(60), index=True)
    nome_conta = db.Column(db.String(180), nullable=False)
    tipo_conta = db.Column(db.String(20))
    origem = db.Column(db.String(160))
    atualizado_em = db.Column(db.DateTime, default=datetime.now, nullable=False, index=True)


class ClassificacaoContabilItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    item_nota_id = db.Column(db.Integer, db.ForeignKey("item_nota.id"), nullable=False, unique=True, index=True)
    numero_nota = db.Column(db.String(20), nullable=False, index=True)
    fornecedor = db.Column(db.String(180))
    codigo_item = db.Column(db.String(80), index=True)
    descricao_item = db.Column(db.String(260))
    cfop = db.Column(db.String(10), index=True)
    conta = db.Column(db.String(30), index=True)
    nome_conta = db.Column(db.String(180))
    comentario = db.Column(db.String(500))
    confianca = db.Column(db.Integer, nullable=False, default=0, index=True)
    metodo = db.Column(db.String(40), nullable=False, default="Pendente", index=True)
    status = db.Column(db.String(20), nullable=False, default="Pendente", index=True)
    motivo_pendencia = db.Column(db.String(80), index=True)
    tipo_regra = db.Column(db.String(30), index=True)
    regra_id = db.Column(db.Integer, db.ForeignKey("classificacao_contabil_padrao.id"), nullable=True)
    revisado_por = db.Column(db.String(100))
    revisado_em = db.Column(db.DateTime)
    aprovado_por = db.Column(db.String(100))
    aprovado_em = db.Column(db.DateTime)
    criado_em = db.Column(db.DateTime, default=datetime.now, nullable=False, index=True)
    atualizado_em = db.Column(db.DateTime, default=datetime.now, nullable=False, index=True)

    item_nota = db.relationship("ItemNota", backref=db.backref("classificacao_contabil", uselist=False))


class ClassificacaoContabilCompetencia(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    competencia = db.Column(db.String(7), nullable=False, unique=True, index=True)
    status = db.Column(db.String(20), nullable=False, default="Aberta", index=True)
    aprovado_por = db.Column(db.String(100))
    aprovado_em = db.Column(db.DateTime)
    fechado_por = db.Column(db.String(100))
    fechado_em = db.Column(db.DateTime)
    reaberto_por = db.Column(db.String(100))
    reaberto_em = db.Column(db.DateTime)
    motivo_reabertura = db.Column(db.String(500))
    atualizado_em = db.Column(db.DateTime, default=datetime.now, nullable=False)


class LogClassificacaoContabil(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    classificacao_id = db.Column(db.Integer, db.ForeignKey("classificacao_contabil_item.id"), nullable=True, index=True)
    numero_nota = db.Column(db.String(20), index=True)
    competencia = db.Column(db.String(7), index=True)
    evento = db.Column(db.String(40), nullable=False, index=True)
    valor_anterior = db.Column(db.Text)
    valor_novo = db.Column(db.Text)
    motivo = db.Column(db.String(500))
    usuario = db.Column(db.String(100))
    data = db.Column(db.DateTime, default=datetime.now, nullable=False, index=True)


class LogDivergencia(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    numero_nota = db.Column(db.String(20), index=True)
    item_descricao = db.Column(db.String(200))
    qtd_esperada = db.Column(db.Float)
    qtd_contada = db.Column(db.Float)
    usuario_erro = db.Column(db.String(100))
    data_erro = db.Column(db.DateTime, default=datetime.now)
    motivo_tipo = db.Column(db.String(80))
    destino_fisico = db.Column(db.String(80))
    evidencia_path = db.Column(db.String(300))
    tentativa_numero = db.Column(db.Integer, default=1)


class LogTentativaConferencia(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    numero_nota = db.Column(db.String(20), index=True, nullable=False)
    item_id = db.Column(db.Integer, nullable=False)
    tentativa_numero = db.Column(db.Integer, nullable=False)
    qtd_esperada = db.Column(db.Float, nullable=False)
    qtd_digitada = db.Column(db.Float)
    qtd_convertida = db.Column(db.Float)
    unidade_informada = db.Column(db.String(20))
    fator_conversao = db.Column(db.Float, default=1.0)
    status_item = db.Column(db.String(20), nullable=False)
    motivo = db.Column(db.String(500))
    usuario = db.Column(db.String(100), nullable=False)
    data = db.Column(db.DateTime, default=datetime.now, nullable=False)


class ChecklistRecebimento(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    numero_nota = db.Column(db.String(20), index=True, nullable=False, unique=True)
    usuario = db.Column(db.String(100), nullable=False)
    lacre_ok = db.Column(db.Boolean, nullable=False, default=False)
    volumes_ok = db.Column(db.Boolean, nullable=False, default=False)
    avaria_visual = db.Column(db.Boolean, nullable=False, default=False)
    etiqueta_ok = db.Column(db.Boolean, nullable=False, default=False)
    observacao = db.Column(db.String(500))
    data = db.Column(db.DateTime, default=datetime.now, nullable=False)


class EtiquetaRecebimento(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    numero_nota = db.Column(db.String(20), index=True, nullable=False, unique=True)
    usuario_impressao = db.Column(db.String(100), nullable=False)
    data_impressao = db.Column(db.DateTime, default=datetime.now, nullable=False)
    quantidade_impressao = db.Column(db.Integer, nullable=False, default=1)


class LogReversaoConferencia(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    numero_nota = db.Column(db.String(20), index=True, nullable=False)
    usuario_reversao = db.Column(db.String(100), nullable=False)
    motivo = db.Column(db.String(500), nullable=False)
    data_reversao = db.Column(db.DateTime, default=datetime.now, nullable=False)


class LogEstornoLancamento(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    numero_nota = db.Column(db.String(20), index=True, nullable=False)
    usuario_estorno = db.Column(db.String(100), nullable=False)
    motivo = db.Column(db.String(500), nullable=False)
    data_estorno = db.Column(db.DateTime, default=datetime.now, nullable=False)


class LogManifestacaoDestinatario(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    numero_nota = db.Column(db.String(20), index=True, nullable=False)
    chave_acesso = db.Column(db.String(44))
    manifestacao = db.Column(db.String(40), nullable=False, default="confirmada")
    status = db.Column(db.String(20), nullable=False, default="Sucesso")
    detalhe = db.Column(db.String(500))
    usuario = db.Column(db.String(100), nullable=False)
    data = db.Column(db.DateTime, default=datetime.now, nullable=False)


class LogEventoFiscalNota(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    numero_nota = db.Column(db.String(20), index=True, nullable=False)
    evento = db.Column(db.String(60), nullable=False, index=True)
    etapa = db.Column(db.String(30), index=True)
    status = db.Column(db.String(20), index=True)
    detalhe = db.Column(db.String(1000))
    payload_json = db.Column(db.Text)
    usuario = db.Column(db.String(100), nullable=False)
    data = db.Column(db.DateTime, default=datetime.now, nullable=False, index=True)


class BoletoContaReceber(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    numero_nota = db.Column(db.String(20), nullable=False, unique=True, index=True)
    chave_acesso = db.Column(db.String(44), index=True)
    banco = db.Column(db.String(80), nullable=False, default="Banco do Brasil")
    valor = db.Column(db.Float, nullable=False, default=0.0)
    nosso_numero = db.Column(db.String(40), nullable=False, unique=True, index=True)
    linha_digitavel = db.Column(db.String(120), nullable=False)
    codigo_barras = db.Column(db.String(120), nullable=False)
    status = db.Column(db.String(20), nullable=False, default="Gerado", index=True)
    usuario_geracao = db.Column(db.String(100), nullable=False)
    data_geracao = db.Column(db.DateTime, default=datetime.now, nullable=False)
    cpf_cnpj_pagador = db.Column(db.String(18), index=True)
    nome_pagador = db.Column(db.String(200))
    vencimento = db.Column(db.Date)
    data_pagamento = db.Column(db.Date)
    bofa_id = db.Column(db.String(100))


class LogExclusaoNota(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    numero_nota = db.Column(db.String(20), index=True, nullable=False)
    fornecedor = db.Column(db.String(100))
    usuario_exclusao = db.Column(db.String(100), nullable=False)
    motivo = db.Column(db.String(500), nullable=False)
    data_exclusao = db.Column(db.DateTime, default=datetime.now, nullable=False)


class SolicitacaoDevolucaoRecebimento(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    numero_nota = db.Column(db.String(20), index=True, nullable=False)
    fornecedor = db.Column(db.String(100))
    chave_acesso = db.Column(db.String(44))
    usuario_solicitante = db.Column(db.String(100), nullable=False)
    motivo = db.Column(db.String(500), nullable=False)
    status = db.Column(db.String(20), nullable=False, default="Pendente", index=True)
    observacao_admin = db.Column(db.String(500))
    usuario_aprovador = db.Column(db.String(100))
    data_solicitacao = db.Column(db.DateTime, default=datetime.now, nullable=False)
    data_decisao = db.Column(db.DateTime)
    ativa = db.Column(db.Boolean, nullable=False, default=True)


class ConferenciaLock(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    numero_nota = db.Column(db.String(20), unique=True, nullable=False, index=True)
    usuario = db.Column(db.String(100), nullable=False)
    lock_until = db.Column(db.DateTime, nullable=False)
    heartbeat_at = db.Column(db.DateTime, nullable=False, default=datetime.now)


class LogAcessoAdministrativo(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    usuario = db.Column(db.String(100), nullable=False)
    rota = db.Column(db.String(200), nullable=False)
    metodo = db.Column(db.String(10), nullable=False)
    data = db.Column(db.DateTime, default=datetime.now, nullable=False)


class ExpedicaoConferencia(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    report_file_name = db.Column(db.String(260), nullable=False, unique=True, index=True)
    report_file_path = db.Column(db.String(500), nullable=False)
    status = db.Column(db.String(20), default="Aberta", nullable=False, index=True)
    created_by = db.Column(db.String(100), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.now, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.now, nullable=False)
    closed_by = db.Column(db.String(100))
    closed_at = db.Column(db.DateTime)


class ExpedicaoConferenciaDecisao(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    conferencia_id = db.Column(db.Integer, db.ForeignKey("expedicao_conferencia.id"), nullable=False, index=True)
    tipo = db.Column(db.String(20), nullable=False)  # Recontar|Pendencia
    motivo = db.Column(db.String(500), nullable=False)
    usuario = db.Column(db.String(100), nullable=False)
    data = db.Column(db.DateTime, default=datetime.now, nullable=False)
    ativa = db.Column(db.Boolean, nullable=False, default=True)


class ExpedicaoConferenciaItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    conferencia_id = db.Column(db.Integer, db.ForeignKey("expedicao_conferencia.id"), nullable=False, index=True)
    item_index = db.Column(db.Integer, nullable=False)
    codigo = db.Column(db.String(120), nullable=False, index=True)
    nome_peca = db.Column(db.String(200), nullable=False)
    dimensao = db.Column(db.String(120))
    os_numero = db.Column(db.String(120), index=True)
    cliente = db.Column(db.String(120), index=True)
    imagem = db.Column(db.String(200))
    qtd_html = db.Column(db.Integer, nullable=False, default=0)
    qtd_conferida = db.Column(db.Integer, nullable=False, default=0)
    qtd_faturada = db.Column(db.Integer, nullable=False, default=0)
    divergente = db.Column(db.Boolean, nullable=False, default=False)


class ExpedicaoFaturamento(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    conferencia_id = db.Column(db.Integer, db.ForeignKey("expedicao_conferencia.id"), nullable=False, index=True)
    numero_nf = db.Column(db.String(40), nullable=False, index=True)
    tipo = db.Column(db.String(10), nullable=False)  # Parcial|Total
    transporte_tipo = db.Column(db.String(20), nullable=False, default="Proprio")  # Proprio|Transportadora
    transportadora = db.Column(db.String(120))
    placa = db.Column(db.String(20))
    motorista = db.Column(db.String(120))
    peso_bruto = db.Column(db.Float)
    observacao = db.Column(db.String(300))
    usuario = db.Column(db.String(100), nullable=False)
    data = db.Column(db.DateTime, default=datetime.now, nullable=False)
    ativo = db.Column(db.Boolean, nullable=False, default=True)


class ExpedicaoFaturamentoItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    faturamento_id = db.Column(db.Integer, db.ForeignKey("expedicao_faturamento.id"), nullable=False, index=True)
    conferencia_item_id = db.Column(db.Integer, db.ForeignKey("expedicao_conferencia_item.id"), nullable=False, index=True)
    qtd_enviada = db.Column(db.Integer, nullable=False, default=0)
    foto_path = db.Column(db.String(400))
    ativo = db.Column(db.Boolean, nullable=False, default=True)


class ExpedicaoEstorno(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    conferencia_id = db.Column(db.Integer, db.ForeignKey("expedicao_conferencia.id"), nullable=False, index=True)
    faturamento_id = db.Column(db.Integer, db.ForeignKey("expedicao_faturamento.id"), index=True)
    conferencia_item_id = db.Column(db.Integer, db.ForeignKey("expedicao_conferencia_item.id"), index=True)
    tipo = db.Column(db.String(10), nullable=False)  # Parcial|Total
    motivo = db.Column(db.String(500), nullable=False)
    usuario = db.Column(db.String(100), nullable=False)
    data = db.Column(db.DateTime, default=datetime.now, nullable=False)


class ExpedicaoConferenciaSimples(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    orcamento = db.Column(db.String(80), nullable=False, index=True)
    tipo_referencia = db.Column(db.String(20), nullable=False, default="Orcamento")
    numero_os = db.Column(db.String(80), index=True)
    ordem_compra = db.Column(db.String(80), index=True)
    conferente = db.Column(db.String(100), nullable=False, index=True)
    data_conferencia = db.Column(db.DateTime, default=datetime.now, nullable=False, index=True)
    numero_nf = db.Column(db.String(160), index=True)
    nome_cliente = db.Column(db.String(160))
    cliente_origem = db.Column(db.String(20), nullable=False, default="Manual")
    nf_origem = db.Column(db.String(20), nullable=False, default="Manual")  # Manual | Consyste (quem preencheu a NF)
    consyste_document_id = db.Column(db.String(120), index=True)
    consyste_chave = db.Column(db.String(50), index=True)
    transportadora = db.Column(db.String(160))
    placa = db.Column(db.String(20))
    motorista = db.Column(db.String(160))
    status = db.Column(db.String(30), nullable=False, default="Pendente de expedição", index=True)
    created_at = db.Column(db.DateTime, default=datetime.now, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.now, nullable=False)
    expedido_at = db.Column(db.DateTime)
    expedido_by = db.Column(db.String(100))
    # Canhoto - foto obrigatória para finalizar
    canhoto_file_name = db.Column(db.String(260))
    canhoto_file_path = db.Column(db.String(500))
    canhoto_uploaded_at = db.Column(db.DateTime)
    canhoto_uploaded_by = db.Column(db.String(100))
    finalizado_at = db.Column(db.DateTime)
    finalizado_by = db.Column(db.String(100))


class ExpedicaoConferenciaSimplesFoto(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    conferencia_id = db.Column(
        db.Integer,
        db.ForeignKey("expedicao_conferencia_simples.id"),
        nullable=False,
        index=True,
    )
    file_name = db.Column(db.String(260), nullable=False)
    file_path = db.Column(db.String(500), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.now, nullable=False)


class ExpedicaoConferenciaSimplesEstorno(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    conferencia_id = db.Column(
        db.Integer,
        db.ForeignKey("expedicao_conferencia_simples.id"),
        nullable=False,
        index=True,
    )
    solicitante = db.Column(db.String(100), nullable=False)
    motivo = db.Column(db.String(500), nullable=False)
    status = db.Column(db.String(30), nullable=False, default="Pendente")  # Pendente | Aprovado | Rejeitado
    admin_usuario = db.Column(db.String(100))
    admin_observacao = db.Column(db.String(500))
    resolvido_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.now, nullable=False)


class AgendamentoVeiculo(db.Model):
    __tablename__ = "agendamento_veiculo"

    id = db.Column(db.Integer, primary_key=True)
    codigo = db.Column(db.String(20), nullable=False, unique=True, index=True)
    nome_exibicao = db.Column(db.String(60), nullable=False)
    placa = db.Column(db.String(12))
    cor_kanban = db.Column(db.String(20))
    janela_conflito_min = db.Column(db.Integer, nullable=False, default=30)
    duracao_padrao_min = db.Column(db.Integer, nullable=False, default=120)
    ordem_exibicao = db.Column(db.Integer, nullable=False, default=0)
    ativo = db.Column(db.Boolean, nullable=False, default=True, index=True)
    created_at = db.Column(db.DateTime, default=datetime.now, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.now, nullable=False)


class AgendamentoMotorista(db.Model):
    __tablename__ = "agendamento_motorista"

    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(160), nullable=False, index=True)
    telefone = db.Column(db.String(40))
    cnh = db.Column(db.String(40), index=True)
    observacoes = db.Column(db.String(500))
    ativo = db.Column(db.Boolean, nullable=False, default=True, index=True)
    usuario_username = db.Column(db.String(80), index=True)
    created_at = db.Column(db.DateTime, default=datetime.now, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.now, nullable=False)


class AgendamentoFornecedor(db.Model):
    __tablename__ = "agendamento_fornecedor"

    id = db.Column(db.Integer, primary_key=True)
    codigo = db.Column(db.String(50), index=True)
    nome = db.Column(db.String(160), nullable=False, index=True)
    razao_social = db.Column(db.String(220))
    cnpj_cpf = db.Column(db.String(20), index=True)
    tipo_pessoa = db.Column(db.String(30))
    contato = db.Column(db.String(120))
    telefone = db.Column(db.String(40))
    telefone_secundario = db.Column(db.String(40))
    email = db.Column(db.String(160))
    logradouro = db.Column(db.String(180))
    numero = db.Column(db.String(30))
    complemento = db.Column(db.String(80))
    bairro = db.Column(db.String(80))
    cidade = db.Column(db.String(80), index=True)
    uf = db.Column(db.String(2), index=True)
    cep = db.Column(db.String(10))
    observacoes = db.Column(db.String(500))
    janela_atendimento = db.Column(db.String(120))
    transportadora = db.Column(db.String(160))
    codigo_integracao = db.Column(db.String(80), index=True)
    ativo = db.Column(db.Boolean, nullable=False, default=True, index=True)
    fonte_arquivo = db.Column(db.String(260))
    importado_em = db.Column(db.DateTime, default=datetime.now, nullable=False, index=True)


class AgendamentoCliente(db.Model):
    __tablename__ = "agendamento_cliente"

    id = db.Column(db.Integer, primary_key=True)
    codigo = db.Column(db.String(50), index=True)
    nome = db.Column(db.String(160), nullable=False, index=True)
    razao_social = db.Column(db.String(220))
    cnpj_cpf = db.Column(db.String(20), index=True)
    tipo_pessoa = db.Column(db.String(30))
    contato = db.Column(db.String(120))
    telefone = db.Column(db.String(40))
    telefone_secundario = db.Column(db.String(40))
    email = db.Column(db.String(160))
    logradouro = db.Column(db.String(180))
    numero = db.Column(db.String(30))
    complemento = db.Column(db.String(80))
    bairro = db.Column(db.String(80))
    cidade = db.Column(db.String(80), index=True)
    uf = db.Column(db.String(2), index=True)
    cep = db.Column(db.String(10))
    observacoes = db.Column(db.String(500))
    municipio_entrega = db.Column(db.String(120))
    codigo_integracao = db.Column(db.String(80), index=True)
    ativo = db.Column(db.Boolean, nullable=False, default=True, index=True)
    fonte_arquivo = db.Column(db.String(260))
    importado_em = db.Column(db.DateTime, default=datetime.now, nullable=False, index=True)


class AgendamentoSolicitacao(db.Model):
    __tablename__ = "agendamento_solicitacao"

    id = db.Column(db.Integer, primary_key=True)
    codigo = db.Column(db.String(30), unique=True, index=True)
    tipo = db.Column(db.String(10), nullable=False, index=True)  # COLETA | ENTREGA
    status = db.Column(db.String(20), nullable=False, default="Pendente", index=True)
    prioridade = db.Column(db.String(20), nullable=False, default="Media", index=True)
    prazo_limite = db.Column(db.DateTime, index=True)
    solicitante = db.Column(db.String(100), nullable=False, index=True)
    criado_em = db.Column(db.DateTime, default=datetime.now, nullable=False, index=True)
    atualizado_em = db.Column(db.DateTime, default=datetime.now, nullable=False)
    documento_tipo = db.Column(db.String(10), nullable=False, index=True)  # OC | NF
    documento_numero = db.Column(db.String(60), nullable=False, index=True)
    numero_oc = db.Column(db.String(60), index=True)
    numero_nf = db.Column(db.String(60), index=True)
    origem_documento = db.Column(db.String(20), nullable=False, default="Manual")
    parceiro_tipo = db.Column(db.String(20), nullable=False)  # Fornecedor | Cliente
    parceiro_codigo = db.Column(db.String(50), index=True)
    parceiro_nome = db.Column(db.String(160), nullable=False, index=True)
    parceiro_razao_social = db.Column(db.String(220))
    parceiro_documento = db.Column(db.String(20), index=True)
    contato = db.Column(db.String(120))
    telefone = db.Column(db.String(40))
    email = db.Column(db.String(160))
    logradouro = db.Column(db.String(180), nullable=False)
    numero = db.Column(db.String(30))
    complemento = db.Column(db.String(80))
    bairro = db.Column(db.String(80))
    cidade = db.Column(db.String(80), nullable=False, index=True)
    uf = db.Column(db.String(2), nullable=False, index=True)
    cep = db.Column(db.String(10))
    observacoes_endereco = db.Column(db.String(500))
    observacoes_solicitante = db.Column(db.String(500))
    observacoes_logistica = db.Column(db.String(500))
    veiculo_id = db.Column(db.Integer, db.ForeignKey("agendamento_veiculo.id"), index=True)
    motorista_id = db.Column(db.Integer, db.ForeignKey("agendamento_motorista.id"), index=True)
    motorista_nome = db.Column(db.String(160), index=True)
    data_hora_saida_prevista = db.Column(db.DateTime, index=True)
    data_hora_retorno_prevista = db.Column(db.DateTime)
    data_hora_saida_real = db.Column(db.DateTime)
    data_hora_retorno_real = db.Column(db.DateTime)
    origem_latitude = db.Column(db.Float)
    origem_longitude = db.Column(db.Float)
    destino_latitude = db.Column(db.Float)
    destino_longitude = db.Column(db.Float)
    km_estimado = db.Column(db.Float)
    km_estimado_retorno = db.Column(db.Float)
    alocado_por = db.Column(db.String(100))
    alocado_em = db.Column(db.DateTime)
    concluido_por = db.Column(db.String(100))
    concluido_em = db.Column(db.DateTime)
    cancelado_por = db.Column(db.String(100))
    cancelado_em = db.Column(db.DateTime)
    motivo_cancelamento = db.Column(db.String(500))
    data_desejada = db.Column(db.DateTime, index=True)
    cancelamento_pendente = db.Column(db.Boolean, nullable=False, default=False)
    cancelamento_solicitado_por = db.Column(db.String(100))
    cancelamento_motivo_pendente = db.Column(db.String(500))
    departamento_solicitante = db.Column(db.String(50), index=True)  # COMPRAS, ASSISTÊNCIA TÉCNICA, ENGENHARIA/PCP, LOGÍSTICA, FACILITIES
    tempo_estimado_min = db.Column(db.Integer)
    qtd_itens = db.Column(db.Integer, nullable=False, default=0)
    qtd_volumes = db.Column(db.Float, nullable=False, default=0.0)
    resumo_itens = db.Column(db.String(100), nullable=False, default="0 itens / 0 volumes")
    payload_origem = db.Column(db.Text)


class AgendamentoSolicitacaoItem(db.Model):
    __tablename__ = "agendamento_solicitacao_item"

    id = db.Column(db.Integer, primary_key=True)
    solicitacao_id = db.Column(
        db.Integer,
        db.ForeignKey("agendamento_solicitacao.id"),
        nullable=False,
        index=True,
    )
    sequencia = db.Column(db.Integer, nullable=False, default=1)
    codigo_item = db.Column(db.String(60), index=True)
    descricao = db.Column(db.String(255), nullable=False)
    quantidade = db.Column(db.Float, nullable=False, default=0.0)
    unidade = db.Column(db.String(20))
    volumes = db.Column(db.Float, nullable=False, default=0.0)
    observacoes = db.Column(db.String(500))


class AgendamentoSolicitacaoHistorico(db.Model):
    __tablename__ = "agendamento_solicitacao_historico"

    id = db.Column(db.Integer, primary_key=True)
    solicitacao_id = db.Column(
        db.Integer,
        db.ForeignKey("agendamento_solicitacao.id"),
        nullable=False,
        index=True,
    )
    evento = db.Column(db.String(40), nullable=False, index=True)
    status_anterior = db.Column(db.String(20))
    status_novo = db.Column(db.String(20))
    usuario = db.Column(db.String(100), nullable=False, index=True)
    detalhe = db.Column(db.String(500))
    payload_json = db.Column(db.Text)
    criado_em = db.Column(db.DateTime, default=datetime.now, nullable=False, index=True)


# ============================================================================
# VIAGENS - rastreamento consolidado por viagem (paradas, GPS, eventos)
# ============================================================================

class Viagem(db.Model):
    __tablename__ = "viagem"

    id = db.Column(db.Integer, primary_key=True)
    codigo = db.Column(db.String(30), unique=True, index=True)  # VG-2026-0001
    veiculo_id = db.Column(db.Integer, db.ForeignKey("agendamento_veiculo.id"), nullable=False, index=True)
    motorista_id = db.Column(db.Integer, db.ForeignKey("agendamento_motorista.id"), index=True)
    motorista_nome = db.Column(db.String(160))
    tipo = db.Column(db.String(20), nullable=False, default="MISTA", index=True)  # COLETA|ENTREGA|MISTA|ALEATORIA
    status = db.Column(db.String(20), nullable=False, default="Planejada", index=True)  # Planejada|EmAndamento|Concluida|Cancelada
    titulo = db.Column(db.String(200))
    observacao = db.Column(db.String(600))
    # Previsão
    saida_prevista = db.Column(db.DateTime, index=True)
    retorno_previsto = db.Column(db.DateTime)
    km_previsto = db.Column(db.Float)
    # Execução
    saida_real = db.Column(db.DateTime, index=True)
    retorno_real = db.Column(db.DateTime, index=True)
    km_inicial = db.Column(db.Integer)
    km_final = db.Column(db.Integer)
    # Origem/Destino resumidos
    origem_label = db.Column(db.String(200))
    origem_lat = db.Column(db.Float)
    origem_lng = db.Column(db.Float)
    destino_label = db.Column(db.String(200))
    destino_lat = db.Column(db.Float)
    destino_lng = db.Column(db.Float)
    # Totais (calculados ao concluir)
    km_percorrido = db.Column(db.Float, default=0)
    total_litros = db.Column(db.Float, default=0)
    total_gasto = db.Column(db.Float, default=0)
    tempo_total_min = db.Column(db.Integer, default=0)
    # Auditoria
    criado_por = db.Column(db.String(100))
    criado_em = db.Column(db.DateTime, default=datetime.now, nullable=False, index=True)
    atualizado_em = db.Column(db.DateTime, default=datetime.now, nullable=False)
    iniciado_por = db.Column(db.String(100))
    concluido_por = db.Column(db.String(100))
    cancelado_por = db.Column(db.String(100))
    motivo_cancelamento = db.Column(db.String(500))
    # Liberacao para motorista (gate do gestor)
    liberada = db.Column(db.Boolean, default=False, nullable=False, index=True)
    liberada_em = db.Column(db.DateTime)
    liberada_por = db.Column(db.String(100))
    destino_unico = db.Column(db.Boolean, default=False, nullable=False)


class ViagemParada(db.Model):
    """Paradas da viagem - cada parada pode ser livre ou atrelada a uma solicitacao."""
    __tablename__ = "viagem_parada"

    id = db.Column(db.Integer, primary_key=True)
    viagem_id = db.Column(db.Integer, db.ForeignKey("viagem.id"), nullable=False, index=True)
    sequencia = db.Column(db.Integer, nullable=False, default=1)
    solicitacao_id = db.Column(db.Integer, db.ForeignKey("agendamento_solicitacao.id"), index=True)
    tipo = db.Column(db.String(20), nullable=False, default="ENTREGA")  # COLETA|ENTREGA|PARADA|ABASTECIMENTO|REFEICAO
    parceiro_nome = db.Column(db.String(200))
    endereco = db.Column(db.String(400))
    cidade = db.Column(db.String(100))
    uf = db.Column(db.String(2))
    latitude = db.Column(db.Float)
    longitude = db.Column(db.Float)
    previsao_chegada = db.Column(db.DateTime)
    chegada_real = db.Column(db.DateTime)
    saida_real = db.Column(db.DateTime)
    status = db.Column(db.String(20), nullable=False, default="Pendente", index=True)  # Pendente|EmAndamento|Concluida|Nao_realizada|Cancelada
    resultado = db.Column(db.String(30))  # Entregue|Coletado|Recusado|AusenciaRecebedor|Outros
    observacao = db.Column(db.String(500))
    assinatura_path = db.Column(db.String(400))  # foto/assinatura digital
    foto_paths = db.Column(db.Text)  # json lista
    criado_em = db.Column(db.DateTime, default=datetime.now, nullable=False)


class ViagemPosicao(db.Model):
    """Breadcrumb GPS - ponto de posicionamento registrado durante a viagem."""
    __tablename__ = "viagem_posicao"

    id = db.Column(db.Integer, primary_key=True)
    viagem_id = db.Column(db.Integer, db.ForeignKey("viagem.id"), nullable=False, index=True)
    latitude = db.Column(db.Float, nullable=False)
    longitude = db.Column(db.Float, nullable=False)
    velocidade_kmh = db.Column(db.Float)
    rumo = db.Column(db.Float)  # graus (0-360)
    precisao_m = db.Column(db.Float)
    bateria_pct = db.Column(db.Integer)
    registrado_em = db.Column(db.DateTime, default=datetime.now, nullable=False, index=True)
    origem = db.Column(db.String(20), default="manual")  # manual|gps|locartrack|motorista_app


class ViagemEvento(db.Model):
    """Timeline de eventos da viagem (inicio, chegada, abastecimento, ocorrencia, foto)."""
    __tablename__ = "viagem_evento"

    id = db.Column(db.Integer, primary_key=True)
    viagem_id = db.Column(db.Integer, db.ForeignKey("viagem.id"), nullable=False, index=True)
    parada_id = db.Column(db.Integer, db.ForeignKey("viagem_parada.id"), index=True)
    tipo = db.Column(db.String(30), nullable=False, index=True)  # INICIO|FIM|CHEGADA|SAIDA_PARADA|ABASTECIMENTO|OCORRENCIA|FOTO|PARADA_EXTRA|OBSERVACAO|CHECKLIST
    titulo = db.Column(db.String(200), nullable=False)
    descricao = db.Column(db.String(800))
    latitude = db.Column(db.Float)
    longitude = db.Column(db.Float)
    km = db.Column(db.Integer)
    foto_path = db.Column(db.String(400))
    severidade = db.Column(db.String(20), default="info")  # info|warning|danger|success
    registrado_por = db.Column(db.String(100))
    registrado_em = db.Column(db.DateTime, default=datetime.now, nullable=False, index=True)


# ============================================================================
# GESTAO DE FROTA - documentos, manutencao, abastecimento, multas, checklist
# ============================================================================

class FrotaDocumento(db.Model):
    """Documentos do veiculo (CRLV, seguro, licenciamento) ou motorista (CNH)."""
    __tablename__ = "frota_documento"

    id = db.Column(db.Integer, primary_key=True)
    # Exatamente um dos dois deve estar preenchido
    veiculo_id = db.Column(db.Integer, db.ForeignKey("agendamento_veiculo.id"), index=True)
    motorista_id = db.Column(db.Integer, db.ForeignKey("agendamento_motorista.id"), index=True)
    tipo = db.Column(db.String(40), nullable=False, index=True)  # CRLV|SEGURO|IPVA|LICENCIAMENTO|CNH|OUTRO
    numero = db.Column(db.String(80))
    emitido_em = db.Column(db.Date)
    vencimento = db.Column(db.Date, index=True)
    observacao = db.Column(db.String(500))
    anexo_path = db.Column(db.String(400))
    criado_por = db.Column(db.String(100))
    criado_em = db.Column(db.DateTime, default=datetime.now, nullable=False)
    atualizado_em = db.Column(db.DateTime, default=datetime.now, nullable=False)


class FrotaManutencao(db.Model):
    """Registro de manutencao (preventiva/corretiva) com alerta de proxima."""
    __tablename__ = "frota_manutencao"

    id = db.Column(db.Integer, primary_key=True)
    veiculo_id = db.Column(db.Integer, db.ForeignKey("agendamento_veiculo.id"), nullable=False, index=True)
    tipo = db.Column(db.String(40), nullable=False, index=True)  # PREVENTIVA|CORRETIVA|REVISAO|TROCA_OLEO|PNEUS|FREIOS|OUTRO
    data = db.Column(db.Date, nullable=False, index=True)
    km_atual = db.Column(db.Integer)
    custo = db.Column(db.Float, default=0)
    fornecedor = db.Column(db.String(160))
    nota_fiscal = db.Column(db.String(80))
    descricao = db.Column(db.String(500), nullable=False)
    # Alerta de proxima manutencao
    proxima_data = db.Column(db.Date, index=True)
    proxima_km = db.Column(db.Integer)
    status = db.Column(db.String(20), nullable=False, default="Realizada", index=True)  # Realizada|Agendada|Cancelada
    anexo_path = db.Column(db.String(400))
    criado_por = db.Column(db.String(100))
    criado_em = db.Column(db.DateTime, default=datetime.now, nullable=False)


class FrotaAbastecimento(db.Model):
    """Registro de abastecimento para calcular consumo medio (km/l) e custo/km."""
    __tablename__ = "frota_abastecimento"

    id = db.Column(db.Integer, primary_key=True)
    veiculo_id = db.Column(db.Integer, db.ForeignKey("agendamento_veiculo.id"), nullable=False, index=True)
    motorista_id = db.Column(db.Integer, db.ForeignKey("agendamento_motorista.id"), index=True)
    viagem_id = db.Column(db.Integer, db.ForeignKey("viagem.id"), index=True)
    data = db.Column(db.DateTime, nullable=False, index=True)
    km_atual = db.Column(db.Integer, nullable=False)
    litros = db.Column(db.Float, nullable=False)
    valor_litro = db.Column(db.Float, nullable=False, default=0)
    valor_total = db.Column(db.Float, nullable=False, default=0)
    combustivel = db.Column(db.String(20), default="Diesel")  # Diesel|Gasolina|Etanol|Flex
    posto = db.Column(db.String(160))
    tanque_cheio = db.Column(db.Boolean, nullable=False, default=True)
    observacao = db.Column(db.String(400))
    criado_por = db.Column(db.String(100))
    criado_em = db.Column(db.DateTime, default=datetime.now, nullable=False)


class FrotaMulta(db.Model):
    """Multa/infracao de transito."""
    __tablename__ = "frota_multa"

    id = db.Column(db.Integer, primary_key=True)
    veiculo_id = db.Column(db.Integer, db.ForeignKey("agendamento_veiculo.id"), nullable=False, index=True)
    motorista_id = db.Column(db.Integer, db.ForeignKey("agendamento_motorista.id"), index=True)
    auto_infracao = db.Column(db.String(80), index=True)
    data_infracao = db.Column(db.DateTime, nullable=False, index=True)
    local = db.Column(db.String(300))
    descricao = db.Column(db.String(500), nullable=False)
    valor = db.Column(db.Float, nullable=False, default=0)
    pontos = db.Column(db.Integer, nullable=False, default=0)
    gravidade = db.Column(db.String(20))  # Leve|Media|Grave|Gravissima
    vencimento = db.Column(db.Date, index=True)
    status = db.Column(db.String(20), nullable=False, default="Pendente", index=True)  # Pendente|Paga|Recorrida|Cancelada
    anexo_path = db.Column(db.String(400))
    observacao = db.Column(db.String(400))
    criado_por = db.Column(db.String(100))
    criado_em = db.Column(db.DateTime, default=datetime.now, nullable=False)


class FrotaChecklistDiario(db.Model):
    """Checklist de saida (pneus, farol, freio, documentos, etc.)."""
    __tablename__ = "frota_checklist_diario"

    id = db.Column(db.Integer, primary_key=True)
    veiculo_id = db.Column(db.Integer, db.ForeignKey("agendamento_veiculo.id"), nullable=False, index=True)
    motorista_id = db.Column(db.Integer, db.ForeignKey("agendamento_motorista.id"), index=True)
    viagem_id = db.Column(db.Integer, db.ForeignKey("viagem.id"), index=True)
    data = db.Column(db.DateTime, nullable=False, default=datetime.now, index=True)
    km_atual = db.Column(db.Integer)
    itens_json = db.Column(db.Text, nullable=False)  # [{"item":"Pneus","status":"OK"|"ATENCAO"|"NAO_OK","obs":""}, ...]
    status_geral = db.Column(db.String(20), nullable=False, default="OK", index=True)  # OK|ATENCAO|BLOQUEADO
    observacao = db.Column(db.String(500))
    foto_paths = db.Column(db.Text)  # lista json de paths
    criado_por = db.Column(db.String(100))
    criado_em = db.Column(db.DateTime, default=datetime.now, nullable=False)


# ============================================================================
# MODELOS WMS - WAREHOUSE MANAGEMENT SYSTEM
# ============================================================================

class LocalizacaoArmazem(db.Model):
    """Localização física no armazém (Rua-Prédio-Nível-Apartamento)"""
    id = db.Column(db.Integer, primary_key=True)
    codigo = db.Column(db.String(80), unique=True, nullable=False, index=True)  # Ex: R1-PD1-N2-AP03
    deposito_id = db.Column(db.Integer, db.ForeignKey("deposito_wms.id"), index=True)
    rua = db.Column(db.String(30), index=True)
    predio = db.Column(db.String(30), index=True)
    nivel = db.Column(db.String(30), index=True)
    apartamento = db.Column(db.String(30), index=True)
    corredor = db.Column(db.String(10), nullable=False)  # Ex: C1
    prateleira = db.Column(db.String(10), nullable=False)  # Ex: P1
    posicao = db.Column(db.String(10), nullable=False)  # Ex: 1
    capacidade_maxima = db.Column(db.Float, nullable=False, default=100.0)  # kg ou unidades
    capacidade_atual = db.Column(db.Float, nullable=False, default=0.0)
    ativo = db.Column(db.Boolean, nullable=False, default=True)
    data_criacao = db.Column(db.DateTime, default=datetime.now, nullable=False)


class ItemWMS(db.Model):
    """Rastreamento de itens no armazém (liga item de nota com localização)"""
    id = db.Column(db.Integer, primary_key=True)
    numero_nota = db.Column(db.String(20), index=True, nullable=False)
    chave_acesso = db.Column(db.String(44))
    fornecedor = db.Column(db.String(100))
    codigo_item = db.Column(db.String(50), nullable=False, index=True)
    descricao = db.Column(db.String(200))
    qtd_recebida = db.Column(db.Float, nullable=False)
    qtd_atual = db.Column(db.Float, nullable=False)
    unidade = db.Column(db.String(20))
    lote = db.Column(db.String(50))
    data_validade = db.Column(db.Date)
    codigo_grv = db.Column(db.String(80), index=True)
    ordem_servico = db.Column(db.String(80), index=True)
    ordem_compra = db.Column(db.String(80), index=True)
    localizacao_id = db.Column(db.Integer, db.ForeignKey("localizacao_armazem.id"), index=True)
    usuario_armazenamento = db.Column(db.String(100))
    data_armazenamento = db.Column(db.DateTime)
    status = db.Column(db.String(20), nullable=False, default="Armazenado", index=True)  # Armazenado|Separado|Enviado
    deposito_id = db.Column(db.Integer, db.ForeignKey("deposito_wms.id"), index=True)  # DEP 01, 02, 03, CLIENTE, TERCEIROS
    origem_estoque_inicial = db.Column(db.Boolean, nullable=False, default=False, index=True)
    ativo = db.Column(db.Boolean, nullable=False, default=True)
    data_criacao = db.Column(db.DateTime, default=datetime.now, nullable=False)


class MovimentacaoWMS(db.Model):
    """Log de movimentações de itens no armazém (rastreabilidade completa)"""
    id = db.Column(db.Integer, primary_key=True)
    item_wms_id = db.Column(db.Integer, db.ForeignKey("item_wms.id"), nullable=False, index=True)
    numero_nota = db.Column(db.String(20), index=True, nullable=False)
    tipo_movimentacao = db.Column(db.String(30), nullable=False)  # Armazenamento|Reposicionamento|Separacao|Devolucao
    localizacao_origem_id = db.Column(db.Integer, db.ForeignKey("localizacao_armazem.id"))
    localizacao_destino_id = db.Column(db.Integer, db.ForeignKey("localizacao_armazem.id"))
    qtd_movimentada = db.Column(db.Float, nullable=False)
    motivo = db.Column(db.String(300))
    usuario = db.Column(db.String(100), nullable=False)
    data_movimentacao = db.Column(db.DateTime, default=datetime.now, nullable=False)


class EstoqueWMS(db.Model):
    """Consolidação de estoque por localização e SKU (para relatórios rápidos)"""
    id = db.Column(db.Integer, primary_key=True)
    codigo_item = db.Column(db.String(50), nullable=False, index=True)
    localizacao_id = db.Column(db.Integer, db.ForeignKey("localizacao_armazem.id"), nullable=False, index=True)
    qtd_total = db.Column(db.Float, nullable=False, default=0.0)
    qtd_separada = db.Column(db.Float, nullable=False, default=0.0)  # Reservada para separação/despacho
    qtd_bloqueada = db.Column(db.Float, nullable=False, default=0.0)  # Quarentena, avaria, qualidade
    data_atualizacao = db.Column(db.DateTime, default=datetime.now, nullable=False, onupdate=datetime.now)
    __table_args__ = (db.UniqueConstraint("codigo_item", "localizacao_id", name="_sku_localizacao_uc"),)


class WMSIntegracaoEvento(db.Model):
    """Fila de integração WMS para eventos vindos do ERP/fiscal."""
    id = db.Column(db.Integer, primary_key=True)
    idempotency_key = db.Column(db.String(120), unique=True, nullable=False, index=True)
    tipo_evento = db.Column(db.String(40), nullable=False, index=True)  # NotaLancada|Reconciliacao
    referencia = db.Column(db.String(80), nullable=False, index=True)  # numero_nota ou chave externa
    origem = db.Column(db.String(30), nullable=False, default="ERP")
    payload_json = db.Column(db.Text)
    status = db.Column(db.String(20), nullable=False, default="Pendente", index=True)  # Pendente|Processando|Sucesso|Falha|DeadLetter
    tentativas = db.Column(db.Integer, nullable=False, default=0)
    proxima_tentativa_em = db.Column(db.DateTime)
    ultima_erro = db.Column(db.String(500))
    processado_em = db.Column(db.DateTime)
    criado_em = db.Column(db.DateTime, default=datetime.now, nullable=False)


class WMSInventreeVinculo(db.Model):
    """Mapa entre entidades locais do WMS e registros remotos do InvenTree."""
    id = db.Column(db.Integer, primary_key=True)
    entidade_tipo = db.Column(db.String(40), nullable=False, index=True)  # sku|item_wms|localizacao|deposito
    entidade_chave = db.Column(db.String(120), nullable=False, index=True)
    inventree_tipo = db.Column(db.String(40), nullable=False, index=True)  # part|stock_item|location
    inventree_id = db.Column(db.Integer, nullable=False, index=True)
    inventree_codigo = db.Column(db.String(120), index=True)
    inventree_path = db.Column(db.String(300))
    metadata_json = db.Column(db.Text)
    sincronizado_em = db.Column(db.DateTime, default=datetime.now, nullable=False, onupdate=datetime.now)
    criado_em = db.Column(db.DateTime, default=datetime.now, nullable=False)
    __table_args__ = (db.UniqueConstraint("entidade_tipo", "entidade_chave", name="_wms_inventree_vinculo_uc"),)


class WMSSkuMestre(db.Model):
    """Cadastro mestre para governança de SKU entre ERP e WMS."""
    id = db.Column(db.Integer, primary_key=True)
    codigo_item = db.Column(db.String(50), nullable=False, unique=True, index=True)
    codigo_erp = db.Column(db.String(50), index=True)
    unidade = db.Column(db.String(20), default="UN")
    fator_conversao = db.Column(db.Float, nullable=False, default=1.0)
    curva_abc = db.Column(db.String(1), default="C")  # A|B|C
    politica_validade = db.Column(db.String(10), default="FIFO")  # FIFO|FEFO
    estoque_minimo = db.Column(db.Float, default=0.0)
    estoque_maximo = db.Column(db.Float, default=0.0)
    endereco_preferencial = db.Column(db.String(80))
    ativo = db.Column(db.Boolean, nullable=False, default=True)
    atualizado_em = db.Column(db.DateTime, default=datetime.now, nullable=False, onupdate=datetime.now)


class WMSParametroOperacional(db.Model):
    """Parâmetros operacionais para políticas logísticas do WMS."""
    id = db.Column(db.Integer, primary_key=True)
    chave = db.Column(db.String(80), unique=True, nullable=False, index=True)
    valor = db.Column(db.String(200), nullable=False)
    descricao = db.Column(db.String(300))
    atualizado_por = db.Column(db.String(100))
    atualizado_em = db.Column(db.DateTime, default=datetime.now, nullable=False, onupdate=datetime.now)


class WMSReconciliacaoDivergencia(db.Model):
    """Divergências entre fonte ERP (fiscal) e WMS por NF/SKU."""
    id = db.Column(db.Integer, primary_key=True)
    numero_nota = db.Column(db.String(20), nullable=False, index=True)
    codigo_item = db.Column(db.String(50), nullable=False, index=True)
    qtd_erp = db.Column(db.Float, nullable=False, default=0.0)
    qtd_wms = db.Column(db.Float, nullable=False, default=0.0)
    diferenca = db.Column(db.Float, nullable=False, default=0.0)
    status = db.Column(db.String(20), nullable=False, default="Aberta", index=True)  # Aberta|Tratando|Resolvida
    origem = db.Column(db.String(30), nullable=False, default="Recon")
    observacao = db.Column(db.String(400))
    criado_em = db.Column(db.DateTime, default=datetime.now, nullable=False)
    resolvido_em = db.Column(db.DateTime)


class WMSAlertaOperacional(db.Model):
    """Alertas operacionais para gestão diária do armazém."""
    id = db.Column(db.Integer, primary_key=True)
    tipo = db.Column(db.String(40), nullable=False, index=True)  # PendenciaAntiga|Ruptura|Capacidade
    severidade = db.Column(db.String(10), nullable=False, default="MEDIA")  # BAIXA|MEDIA|ALTA
    referencia = db.Column(db.String(100), index=True)
    descricao = db.Column(db.String(400), nullable=False)
    status = db.Column(db.String(20), nullable=False, default="Aberto", index=True)  # Aberto|Resolvido
    criado_em = db.Column(db.DateTime, default=datetime.now, nullable=False)
    resolvido_em = db.Column(db.DateTime)



class ConsertoEstoque(db.Model):
    __tablename__ = 'conserto_estoque'
    id = db.Column(db.Integer, primary_key=True)
    tipo_controle = db.Column(db.String(50), nullable=False, default="Meu em poder de terceiros", index=True)
    tipo_operacao = db.Column(db.String(30), nullable=False, default="Conserto", index=True)
    cfop_remessa = db.Column(db.String(4), nullable=True, index=True)
    numero_nf_remessa = db.Column(db.String(20), index=True)
    chave_nf_remessa = db.Column(db.String(44), nullable=False, index=True)
    data_emissao = db.Column(db.DateTime, nullable=False)
    fornecedor_cnpj = db.Column(db.String(14), nullable=False, index=True)
    fornecedor_nome = db.Column(db.String(100), nullable=False)
    produto_codigo = db.Column(db.String(50), nullable=False, index=True)
    produto_descricao = db.Column(db.String(200), nullable=False)
    quantidade_enviada = db.Column(db.Float, nullable=False)
    quantidade_saldo = db.Column(db.Float, nullable=False)
    status = db.Column(db.String(30), nullable=False, default="Em conserto", index=True)
    data_criacao = db.Column(db.DateTime, default=datetime.now, nullable=False)
    usuario_criacao = db.Column(db.String(100), nullable=False)

    baixas = db.relationship('ConsertoBaixa', backref='estoque', lazy=True)

class ConsertoBaixa(db.Model):
    __tablename__ = 'conserto_baixa'
    id = db.Column(db.Integer, primary_key=True)
    conserto_estoque_id = db.Column(db.Integer, db.ForeignKey('conserto_estoque.id'), nullable=False)
    cfop_retorno = db.Column(db.String(4), nullable=True, index=True)
    numero_nf_retorno = db.Column(db.String(20), index=True)
    chave_nf_retorno = db.Column(db.String(44), nullable=True, index=True)
    data_nf_retorno = db.Column(db.DateTime, nullable=True)
    quantidade_baixada = db.Column(db.Float, nullable=False)
    tipo_vinculo = db.Column(db.String(20), nullable=False)  # automatico/manual
    status_baixa = db.Column(db.String(30), nullable=False, default="Pendente de confirmação", index=True)
    usuario_confirmacao = db.Column(db.String(100), nullable=True)
    data_confirmacao = db.Column(db.DateTime, nullable=True)
    observacoes = db.Column(db.String(500), nullable=True)

class ConsertoAuditoria(db.Model):
    __tablename__ = 'conserto_auditoria'
    id = db.Column(db.Integer, primary_key=True)
    acao = db.Column(db.String(50), nullable=False)
    referencia_id = db.Column(db.Integer, nullable=False)
    referencia_tipo = db.Column(db.String(30), nullable=False)  # estoque/baixa
    usuario = db.Column(db.String(100), nullable=False)
    data_hora = db.Column(db.DateTime, default=datetime.now, nullable=False)
    detalhes = db.Column(db.String(1000), nullable=True)

class DepositoWMS(db.Model):
    """Depósitos fixos para armazenagem: DEP 01, 02, 03, CLIENTE, TERCEIROS"""
    id = db.Column(db.Integer, primary_key=True)
    codigo = db.Column(db.String(30), unique=True, nullable=False, index=True)  # Ex: DEP_01, DEP_02, DEP_03, CLIENTE, TERCEIROS
    nome = db.Column(db.String(100), nullable=False)  # Ex: "DEP 01 - Almoxarifado"
    descricao = db.Column(db.String(300))
    ativo = db.Column(db.Boolean, nullable=False, default=True)
    data_criacao = db.Column(db.DateTime, default=datetime.now, nullable=False)


# ============================================================================
# MODELOS FACILITIES - GESTÃO DE OBRAS, EPI, LIMPEZA
# ============================================================================

class FacilitiesColaborador(db.Model):
    """Colaboradores internos (para solicitações - não precisa login no sistema)"""
    __tablename__ = "facilities_colaborador"
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(100), nullable=False, index=True)
    cargo = db.Column(db.String(80))
    setor = db.Column(db.String(80), index=True)
    telefone = db.Column(db.String(20))
    email = db.Column(db.String(160), index=True)
    nivel_acesso = db.Column(db.String(20), nullable=False, default="solicitante", index=True)  # solicitante|gestor
    ativo = db.Column(db.Boolean, nullable=False, default=True)
    origem = db.Column(db.String(20), nullable=False, default="Local", index=True)
    grv_cod_empresa = db.Column(db.Integer, index=True)
    grv_codigo = db.Column(db.Integer, index=True)
    grv_identificacao = db.Column(db.String(30), index=True)
    grv_apelido = db.Column(db.String(100), index=True)
    criado_em = db.Column(db.DateTime, default=datetime.now, nullable=False)


class FacilitiesProjeto(db.Model):
    """Projetos/Obras para acompanhamento"""
    __tablename__ = "facilities_projeto"
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(150), nullable=False, index=True)
    cliente_nome = db.Column(db.String(150))
    cliente_telefone = db.Column(db.String(20))
    cliente_endereco = db.Column(db.String(300))
    observacoes = db.Column(db.Text)
    status = db.Column(db.String(30), nullable=False, default="Em andamento", index=True)  # Em andamento|Pausado|Concluído
    criado_em = db.Column(db.DateTime, default=datetime.now, nullable=False)

    tarefas = db.relationship("FacilitiesTarefa", backref="projeto", lazy=True)


class FacilitiesTarefa(db.Model):
    """Tarefas de um projeto/obra"""
    __tablename__ = "facilities_tarefa"
    id = db.Column(db.Integer, primary_key=True)
    projeto_id = db.Column(db.Integer, db.ForeignKey("facilities_projeto.id"), nullable=False, index=True)
    titulo = db.Column(db.String(150), nullable=False)
    local = db.Column(db.String(100))  # sala, ambiente
    descricao = db.Column(db.Text)
    status = db.Column(db.String(30), nullable=False, default="nao_planejado", index=True)  # nao_planejado|planejado|em_andamento|pausado|concluido
    observacao = db.Column(db.Text)
    impedimento = db.Column(db.Text)
    foto_path = db.Column(db.String(500))
    data_inicio_prevista = db.Column(db.Date)
    data_fim_prevista = db.Column(db.Date)
    atualizado_em = db.Column(db.DateTime)
    criado_em = db.Column(db.DateTime, default=datetime.now, nullable=False)


class FacilitiesLimpeza(db.Model):
    """Cronograma de limpeza"""
    __tablename__ = "facilities_limpeza"
    id = db.Column(db.Integer, primary_key=True)
    colaborador_id = db.Column(db.Integer, db.ForeignKey("facilities_colaborador.id"), index=True)
    titulo = db.Column(db.String(150), nullable=False)
    local = db.Column(db.String(150))
    data_agendada = db.Column(db.Date, nullable=False, index=True)
    hora_inicio = db.Column(db.String(5))  # HH:MM
    hora_fim = db.Column(db.String(5))
    observacoes = db.Column(db.Text)
    concluido = db.Column(db.Boolean, nullable=False, default=False)
    concluido_em = db.Column(db.DateTime)
    concluido_por = db.Column(db.String(100))
    evidencia_foto_path = db.Column(db.String(500))
    criado_em = db.Column(db.DateTime, default=datetime.now, nullable=False)

    colaborador = db.relationship("FacilitiesColaborador", backref="limpezas")


class FacilitiesEpiMaterial(db.Model):
    """Catálogo de EPIs e Uniformes disponíveis"""
    __tablename__ = "facilities_epi_material"
    id = db.Column(db.Integer, primary_key=True)
    codigo_interno = db.Column(db.String(30), nullable=False, index=True)
    nome = db.Column(db.String(150), nullable=False)
    tipo = db.Column(db.String(20), nullable=False, default="epi", index=True)  # epi|uniforme
    numero_ca = db.Column(db.String(20))  # Certificado de Aprovação (NR-6)
    qtd_estoque = db.Column(db.Integer, nullable=False, default=0)
    qtd_minima = db.Column(db.Integer, nullable=False, default=0)
    ativo = db.Column(db.Boolean, nullable=False, default=True)
    criado_em = db.Column(db.DateTime, default=datetime.now, nullable=False)


class FacilitiesEpiSolicitacao(db.Model):
    """Solicitações de EPI/Uniforme"""
    __tablename__ = "facilities_epi_solicitacao"
    id = db.Column(db.Integer, primary_key=True)
    colaborador_id = db.Column(db.Integer, db.ForeignKey("facilities_colaborador.id"), nullable=False, index=True)
    solicitante_id = db.Column(db.Integer, db.ForeignKey("facilities_colaborador.id"), index=True)  # quem abriu a solicitação
    solicitante_nome = db.Column(db.String(120))  # sempre preenchido (fallback ao username quando sem FacilitiesColaborador)
    liberador_id = db.Column(db.Integer, db.ForeignKey("facilities_colaborador.id"), index=True)  # gestor que aprovou/negou
    tipo = db.Column(db.String(20), nullable=False, default="epi", index=True)  # epi|uniforme
    codigo_item = db.Column(db.String(30), nullable=False, index=True)
    nome_item = db.Column(db.String(150), nullable=False)
    tamanho = db.Column(db.String(20))
    quantidade = db.Column(db.Integer, nullable=False, default=1)
    motivo = db.Column(db.Text)
    status = db.Column(db.String(20), nullable=False, default="solicitado", index=True)  # solicitado|liberado|retirado|negado|cancelado
    motivo_recusa = db.Column(db.Text)
    solicitado_em = db.Column(db.DateTime, default=datetime.now, nullable=False, index=True)
    liberado_em = db.Column(db.DateTime)
    liberado_por_username = db.Column(db.String(100))  # auditoria: quem clicou aprovar/negar
    retirado_em = db.Column(db.DateTime)
    retirado_por = db.Column(db.String(100))  # username de quem entregou
    numero_ca_entregue = db.Column(db.String(20))
    assinatura_path = db.Column(db.String(500))  # PNG da assinatura digital
    cancelado_em = db.Column(db.DateTime)
    cancelado_por = db.Column(db.String(100))
    motivo_cancelamento = db.Column(db.Text)
    proxima_troca_em = db.Column(db.Date, index=True)  # calculado na retirada (vencimento do EPI)
    lembrete_retirada_enviado_em = db.Column(db.DateTime)
    estoque_grv_antes = db.Column(db.Float)
    estoque_grv_depois = db.Column(db.Float)
    estoque_grv_baixado = db.Column(db.Boolean)
    estoque_grv_verificado_em = db.Column(db.DateTime)
    estoque_grv_mensagem = db.Column(db.String(300))
    criado_em = db.Column(db.DateTime, default=datetime.now, nullable=False)

    colaborador = db.relationship("FacilitiesColaborador", foreign_keys=[colaborador_id], backref="solicitacoes_epi")
    solicitante = db.relationship("FacilitiesColaborador", foreign_keys=[solicitante_id])
    liberador = db.relationship("FacilitiesColaborador", foreign_keys=[liberador_id])


class FacilitiesEpiCicloTroca(db.Model):
    """Ciclo de troca (validade) padrao por tipo/codigo de EPI em meses.
    Ex: 'BOTINA' = 6 meses, codigo_interno='25-01-00001' = 12 meses."""
    __tablename__ = "facilities_epi_ciclo_troca"
    id = db.Column(db.Integer, primary_key=True)
    codigo_interno = db.Column(db.String(30), index=True)
    palavra_chave = db.Column(db.String(100), index=True)  # match no nome_item (case-insensitive)
    meses_validade = db.Column(db.Integer, nullable=False, default=6)
    descricao = db.Column(db.String(200))
    ativo = db.Column(db.Boolean, nullable=False, default=True)
    criado_em = db.Column(db.DateTime, default=datetime.now, nullable=False)


class FacilitiesAuditLog(db.Model):
    """Log de auditoria para acoes administrativas no modulo Facilities."""
    __tablename__ = "facilities_audit_log"
    id = db.Column(db.Integer, primary_key=True)
    ts = db.Column(db.DateTime, default=datetime.now, nullable=False, index=True)
    usuario = db.Column(db.String(100), index=True)
    entidade = db.Column(db.String(40), nullable=False, index=True)  # epi_solicitacao|limpeza|projeto|tarefa|material|colaborador
    entidade_id = db.Column(db.Integer, index=True)
    acao = db.Column(db.String(40), nullable=False, index=True)  # criar|aprovar|negar|retirar|cancelar|concluir|editar|excluir
    detalhes = db.Column(db.Text)
    ip = db.Column(db.String(45))


class FacilitiesLimpezaTemplate(db.Model):
    """Template de limpeza recorrente (ex: 'Banheiro - diario'). Usado para gerar agendamentos em lote."""
    __tablename__ = "facilities_limpeza_template"
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(150), nullable=False)
    local = db.Column(db.String(150))
    recorrencia = db.Column(db.String(20), nullable=False, default="diaria", index=True)  # diaria|semanal|quinzenal|mensal
    dias_semana = db.Column(db.String(20))  # "1,2,3,4,5" (0=domingo)
    hora_inicio = db.Column(db.String(5))
    hora_fim = db.Column(db.String(5))
    colaborador_id = db.Column(db.Integer, db.ForeignKey("facilities_colaborador.id"), index=True)
    checklist_json = db.Column(db.Text)  # JSON array de strings
    qr_code = db.Column(db.String(40), unique=True, index=True)  # token QR do ambiente
    ativo = db.Column(db.Boolean, nullable=False, default=True)
    criado_em = db.Column(db.DateTime, default=datetime.now, nullable=False)

    colaborador = db.relationship("FacilitiesColaborador")


class FacilitiesProjetoTarefa(db.Model):
    """Tarefas em estilo kanban para projetos Facilities."""
    __tablename__ = "facilities_projeto_tarefa"
    id = db.Column(db.Integer, primary_key=True)
    projeto_id = db.Column(db.Integer, db.ForeignKey("facilities_projeto.id"), nullable=False, index=True)
    titulo = db.Column(db.String(200), nullable=False)
    descricao = db.Column(db.Text)
    status = db.Column(db.String(30), nullable=False, default="nao_planejado", index=True)
    ordem = db.Column(db.Integer, nullable=False, default=0)
    responsavel_id = db.Column(db.Integer, db.ForeignKey("facilities_colaborador.id"), index=True)
    data_inicio = db.Column(db.Date)
    data_fim = db.Column(db.Date)
    impedimento = db.Column(db.Text)
    impedimento_em = db.Column(db.DateTime)
    foto_path = db.Column(db.String(500))
    criado_em = db.Column(db.DateTime, default=datetime.now, nullable=False)
    atualizado_em = db.Column(db.DateTime)
    concluido_em = db.Column(db.DateTime)

    projeto = db.relationship("FacilitiesProjeto", backref="tarefas_kanban")
    responsavel = db.relationship("FacilitiesColaborador")


class FacilitiesEstoqueItem(db.Model):
    """Controle de estoque de EPIs e uniformes no almoxarifado."""
    __tablename__ = "facilities_estoque_item"
    id = db.Column(db.Integer, primary_key=True)
    material_id = db.Column(db.Integer, db.ForeignKey("facilities_epi_material.id"), nullable=False, index=True)
    numero_ca = db.Column(db.String(20))           # Certificado de Aprovação (NR-6)
    lote = db.Column(db.String(50))
    data_validade = db.Column(db.Date)
    localizacao = db.Column(db.String(100))        # Ex: Prateleira A3
    quantidade = db.Column(db.Integer, nullable=False, default=0)
    qtd_minima = db.Column(db.Integer, nullable=False, default=5)
    criado_em = db.Column(db.DateTime, default=datetime.now, nullable=False)
    atualizado_em = db.Column(db.DateTime)

    material = db.relationship("FacilitiesEpiMaterial")


class FacilitiesChamado(db.Model):
    """Chamado de Facilities: manutenção, limpeza, reposição, etc."""
    __tablename__ = "facilities_chamado"
    id = db.Column(db.Integer, primary_key=True)
    titulo = db.Column(db.String(200), nullable=False)
    descricao = db.Column(db.Text)
    categoria = db.Column(db.String(30), nullable=False, default="outros", index=True)  # manutencao|limpeza|reposicao|outros
    prioridade = db.Column(db.String(15), nullable=False, default="media", index=True)  # baixa|media|alta|urgente
    status = db.Column(db.String(20), nullable=False, default="aberto", index=True)     # aberto|em_analise|aprovado|em_execucao|concluido|cancelado
    local = db.Column(db.String(150))
    aberto_por = db.Column(db.String(100))         # nome ou usuario
    responsavel = db.Column(db.String(100))        # responsável pela execução
    observacao = db.Column(db.Text)                # última observação de atualização
    aberto_em = db.Column(db.DateTime, default=datetime.now, nullable=False, index=True)
    atualizado_em = db.Column(db.DateTime)
    concluido_em = db.Column(db.DateTime)


class EmailNFEnviado(db.Model):
    """Log/idempotencia de envio de NF-e (XML + DANFE) por e-mail ao cliente/destinatario."""
    __tablename__ = "email_nf_enviado"

    id = db.Column(db.Integer, primary_key=True)
    numero_nf = db.Column(db.String(60), nullable=False, index=True)
    chave_acesso = db.Column(db.String(44), index=True)
    destinatario_email = db.Column(db.String(200), nullable=False, index=True)
    destinatario_nome = db.Column(db.String(200))
    destinatario_cnpj = db.Column(db.String(20), index=True)
    cc_emails = db.Column(db.String(500))
    assunto = db.Column(db.String(300))
    fonte_email = db.Column(db.String(20))  # Manual | Cadastro | XML
    origem = db.Column(db.String(20), nullable=False, default="Manual", index=True)  # Manual | Auto | Sync
    status = db.Column(db.String(20), nullable=False, default="Pendente", index=True)  # Pendente | Enviado | Falha
    tentativas = db.Column(db.Integer, nullable=False, default=0)
    erro_mensagem = db.Column(db.String(800))
    anexou_xml = db.Column(db.Boolean, nullable=False, default=False)
    anexou_pdf = db.Column(db.Boolean, nullable=False, default=False)
    conferencia_id = db.Column(db.Integer, index=True)
    faturamento_id = db.Column(db.Integer, index=True)
    disparado_por = db.Column(db.String(100))
    criado_em = db.Column(db.DateTime, default=datetime.now, nullable=False, index=True)
    enviado_em = db.Column(db.DateTime)


class EmailEntradaChapa(db.Model):
    """Log/idempotencia do aviso de NF de entrada de chapa/barra com lote."""
    __tablename__ = "email_entrada_chapa"

    id = db.Column(db.Integer, primary_key=True)
    numero_nota = db.Column(db.String(60), nullable=False, index=True)
    chave_acesso = db.Column(db.String(44), index=True)
    numero_ar = db.Column(db.String(80), nullable=False, index=True)
    parceiro_nome = db.Column(db.String(220))
    cfops = db.Column(db.String(120))
    destinatarios = db.Column(db.String(800), nullable=False)
    assunto = db.Column(db.String(300))
    status = db.Column(db.String(20), nullable=False, default="Pendente", index=True)
    tentativas = db.Column(db.Integer, nullable=False, default=0)
    erro_mensagem = db.Column(db.String(800))
    disparado_por = db.Column(db.String(100))
    origem = db.Column(db.String(20), nullable=False, default="Sistema", index=True)
    criado_em = db.Column(db.DateTime, default=datetime.now, nullable=False, index=True)
    enviado_em = db.Column(db.DateTime)

    __table_args__ = (
        db.UniqueConstraint("numero_nota", "numero_ar", name="ux_email_entrada_chapa_nota_ar"),
    )
