
from datetime import datetime
from .extensions import db

class ActiveSession(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(100), nullable=False, index=True)
    session_id = db.Column(db.String(128), nullable=False, unique=True, index=True)
    created_at = db.Column(db.DateTime, default=datetime.now, nullable=False)
    last_activity = db.Column(db.DateTime, default=datetime.now, nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    ip_address = db.Column(db.String(64), nullable=True)
    user_agent = db.Column(db.String(400), nullable=True)


class Usuario(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False, index=True)
    email = db.Column(db.String(160), unique=True, nullable=True, index=True)
    password = db.Column(db.String(255), nullable=True)
    role = db.Column(db.String(20), default="Logística")
    ativo = db.Column(db.Boolean, nullable=False, default=True, index=True)
    nome_exibicao = db.Column(db.String(120), nullable=True)
    telefone = db.Column(db.String(40), nullable=True)
    tema = db.Column(db.String(10), nullable=True)  # 'claro' | 'escuro'
    senha_atualizada_em = db.Column(db.DateTime, nullable=True)
    ultimo_login_em = db.Column(db.DateTime, nullable=True, index=True)
    convite_token_hash = db.Column(db.String(64), nullable=True, index=True)
    convite_expires_at = db.Column(db.DateTime, nullable=True, index=True)
    convite_enviado_em = db.Column(db.DateTime, nullable=True)
    convite_aceito_em = db.Column(db.DateTime, nullable=True)
    forcar_troca_senha = db.Column(db.Boolean, nullable=False, default=False)
    criado_em = db.Column(db.DateTime, default=datetime.now, nullable=False)
    criado_por = db.Column(db.String(100), nullable=True)
    atualizado_em = db.Column(db.DateTime, default=datetime.now, nullable=False)
    atualizado_por = db.Column(db.String(100), nullable=True)


class UsuarioGestaoAuditoria(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    ator_username = db.Column(db.String(100), nullable=False, index=True)
    alvo_username = db.Column(db.String(80), nullable=False, index=True)
    acao = db.Column(db.String(60), nullable=False, index=True)
    detalhes = db.Column(db.Text, nullable=True)
    ip_address = db.Column(db.String(64), nullable=True)
    criado_em = db.Column(db.DateTime, default=datetime.now, nullable=False, index=True)


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
    # Quantidade de chapas em UND informada pelo conferente para material
    # recebido em peso (KG). Auditavel e enviada no aviso de entrada de chapa.
    qtd_chapas_und = db.Column(db.Float, nullable=True)
    # Caminho relativo ao instance_path do anexo para documentos não fiscais
    # (faturas, contas, notas de débito etc.). Ex: "doc_entrada_anexos/42/42_FATURA.pdf"
    anexo_path = db.Column(db.String(300), nullable=True)


class ConferenciaRecebimento(db.Model):
    """Identificador sequencial e rastreável por NF na conferência de recebimento.

    Espelha o padrão da conferência de expedição (código interno por ordem):
    cada NF que passa pela fila recebe um código estável ``RC-000123`` usado
    na tela como "ID da conferência".
    """

    id = db.Column(db.Integer, primary_key=True)
    numero_nota = db.Column(db.String(20), unique=True, index=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.now, nullable=False)

    @property
    def codigo(self):
        return f"RC-{self.id:06d}"


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
    # Origem do registro: Manual (criado na tela) | Romaneio (nasceu da
    # conferencia de expedicao/romaneio, ja finalizado, canhoto = romaneio).
    origem = db.Column(db.String(20), nullable=False, default="Manual", index=True)
    consyste_document_id = db.Column(db.String(120), index=True)
    consyste_chave = db.Column(db.String(50), index=True)
    transportadora = db.Column(db.String(160))
    placa = db.Column(db.String(20))
    motorista = db.Column(db.String(160))
    sem_conferencia = db.Column(db.Boolean, nullable=False, default=False)  # Expedição avulsa sem conferência
    sem_conferencia_motivo = db.Column(db.String(60))  # Motivo da expedição sem conferência
    retirado_por = db.Column(db.String(160))  # Quem retirou (quando aplicável)
    retirada_justificativa = db.Column(db.String(500))  # Justificativa opcional da retirada
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
    # Foto destinada ao cliente (capturada no registro, para envio posterior)
    foto_cliente_file_name = db.Column(db.String(260))
    foto_cliente_file_path = db.Column(db.String(500))
    foto_cliente_uploaded_at = db.Column(db.DateTime)
    foto_cliente_uploaded_by = db.Column(db.String(100))
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


class ExpedicaoOrdemFat(db.Model):
    """Cabecalho de uma ordem de faturamento (cod_ordem_fat) para a
    Conferencia de Expedicao. Origem dos dados: API externa de faturamento."""

    __tablename__ = "expedicao_ordem_fat"

    id = db.Column(db.Integer, primary_key=True)
    # ID interno permanente e rastreavel (ex.: OF-000123), gerado uma unica
    # vez na criacao e nunca mais alterado - permite localizar a ordem no
    # Sync mesmo que ela suma da origem (API de faturamento).
    codigo_interno = db.Column(db.String(20), unique=True, index=True)
    cod_ordem_fat = db.Column(db.Integer, nullable=False, unique=True, index=True)
    cliente = db.Column(db.String(160))
    orcamento = db.Column(db.String(80), index=True)
    pedido = db.Column(db.String(80))
    liberado_faturar = db.Column(db.String(10), default="nao")
    origem_status = db.Column(db.String(40))  # status bruto vindo da API (em_aberto, faturado...)
    dt_solicitacao_fat = db.Column(db.DateTime)
    dt_previsao_entrega = db.Column(db.DateTime)

    # Pendente de conferência | Conferido | Faturado | Expedido
    status = db.Column(db.String(40), nullable=False, default="Pendente de conferência", index=True)

    # Tipo de operacao definido pelo conferente: nacional | internacional.
    # Quando internacional, o conferente informa medidas/volumes por item.
    operacao_tipo = db.Column(db.String(20), nullable=False, default="nacional")

    # Dados de volume/peso (cabecalho) preenchidos ao concluir a conferencia
    peso_liquido = db.Column(db.String(40))
    peso_bruto = db.Column(db.String(40))
    qtde_volumes = db.Column(db.String(40))
    especie_volumes = db.Column(db.String(80))
    marca_volumes = db.Column(db.String(120))

    # Conferencia
    conferente = db.Column(db.String(100))
    conferido_at = db.Column(db.DateTime)
    divergente = db.Column(db.Boolean, nullable=False, default=False)
    # Registro para consulta futura: a conferencia foi feita APOS o
    # faturamento (NF ja emitida quando o material foi conferido).
    conferido_pos_faturamento = db.Column(db.Boolean, nullable=False, default=False)

    # Faturamento (NF preenchida na origem)
    numero_nf = db.Column(db.String(80), index=True)
    faturado_at = db.Column(db.DateTime)

    # Expedicao (finalizacao no Registro de expedicao)
    expedido_at = db.Column(db.DateTime)
    expedido_by = db.Column(db.String(100))
    expedicao_registro_id = db.Column(db.Integer, index=True)

    # Exclusao (Admin): a ordem some do dashboard/fila normal, mas a linha e
    # todo o historico de conferencia continuam no banco para auditoria (ver
    # expedicao_auditoria_routes.py) - nunca e apagada de fato.
    excluido = db.Column(db.Boolean, nullable=False, default=False, index=True)
    excluido_at = db.Column(db.DateTime)
    excluido_by = db.Column(db.String(100))
    excluido_motivo = db.Column(db.String(300))

    created_at = db.Column(db.DateTime, default=datetime.now, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.now, nullable=False)

    itens = db.relationship(
        "ExpedicaoOrdemFatItem",
        backref="ordem",
        cascade="all, delete-orphan",
        order_by="ExpedicaoOrdemFatItem.linha",
    )

    # Volumes do embarque (operacao internacional) — lista adicionada
    # manualmente pelo conferente, com medidas e peso de cada volume.
    volumes = db.relationship(
        "ExpedicaoOrdemFatVolume",
        backref="ordem",
        cascade="all, delete-orphan",
        order_by="ExpedicaoOrdemFatVolume.linha",
    )


class ExpedicaoOrdemFatVolume(db.Model):
    """Volume do embarque de uma ordem de faturamento (operacao internacional).

    Cada linha e um volume fisico adicionado manualmente pelo conferente, com
    especie/descricao, quantidade e medidas (cm) + peso (kg)."""

    __tablename__ = "expedicao_ordem_fat_volume"

    id = db.Column(db.Integer, primary_key=True)
    ordem_id = db.Column(
        db.Integer,
        db.ForeignKey("expedicao_ordem_fat.id"),
        nullable=False,
        index=True,
    )
    linha = db.Column(db.Integer, nullable=False, default=0)
    especie = db.Column(db.String(120))
    quantidade = db.Column(db.Integer)
    altura_cm = db.Column(db.Float)
    comprimento_cm = db.Column(db.Float)
    largura_cm = db.Column(db.Float)
    peso_kg = db.Column(db.Float)


class ExpedicaoOrdemFatItem(db.Model):
    __tablename__ = "expedicao_ordem_fat_item"

    id = db.Column(db.Integer, primary_key=True)
    ordem_id = db.Column(
        db.Integer,
        db.ForeignKey("expedicao_ordem_fat.id"),
        nullable=False,
        index=True,
    )
    linha = db.Column(db.Integer, nullable=False, default=0)
    cod_interno = db.Column(db.String(80), index=True)
    item = db.Column(db.String(200))
    n_os = db.Column(db.String(80), index=True)
    # Quantidade esperada (resultado da conferencia) - NUNCA exibida ao conferente
    qtde_a_faturar = db.Column(db.Integer, nullable=False, default=0)
    qtde_conferida = db.Column(db.Integer)
    divergente = db.Column(db.Boolean, nullable=False, default=False)


class ExpedicaoOrdemST(db.Model):
    """Cabecalho de uma Ordem de Compra com material de Servico de Terceiro (ST)
    a faturar, para a Conferencia de Expedicao (aba ST). A cadeia de origem e:
    Ordem de Compra -> Servico de Terceiro -> OS -> material enviado para ST."""

    __tablename__ = "expedicao_ordem_st"

    id = db.Column(db.Integer, primary_key=True)
    # ID interno permanente e rastreavel (ex.: OC-000123), gerado uma unica
    # vez na criacao e nunca mais alterado - mesmo proposito do
    # ExpedicaoOrdemFat.codigo_interno, para a aba de Servico de Terceiro.
    codigo_interno = db.Column(db.String(20), unique=True, index=True)
    cod_ordem_compra = db.Column(db.String(80), nullable=False, unique=True, index=True)
    fornecedor = db.Column(db.String(160))
    n_os = db.Column(db.String(120))  # pode agregar varias OS separadas por virgula
    origem_status = db.Column(db.String(40))
    dt_solicitacao = db.Column(db.DateTime)
    dt_prevista_entrega = db.Column(db.DateTime)

    # Pendente de conferência | Conferido/Ag. Fat | Faturado | Expedido
    status = db.Column(db.String(40), nullable=False, default="Pendente de conferência", index=True)

    # Dados de volume/peso (cabecalho) preenchidos ao concluir a conferencia
    peso_liquido = db.Column(db.String(40))
    peso_bruto = db.Column(db.String(40))
    qtde_volumes = db.Column(db.String(40))
    especie_volumes = db.Column(db.String(80))

    # Conferencia
    conferente = db.Column(db.String(100))
    conferido_at = db.Column(db.DateTime)
    divergente = db.Column(db.Boolean, nullable=False, default=False)
    # Registro para consulta futura: conferencia feita APOS o faturamento.
    conferido_pos_faturamento = db.Column(db.Boolean, nullable=False, default=False)

    # Faturamento (NF de envio/retorno preenchida na origem)
    numero_nf = db.Column(db.String(80), index=True)
    faturado_at = db.Column(db.DateTime)

    # Expedicao
    expedido_at = db.Column(db.DateTime)
    expedido_by = db.Column(db.String(100))
    expedicao_registro_id = db.Column(db.Integer, index=True)

    # Exclusao (Admin): mesma logica do FAT - some do dashboard, mas fica
    # no banco para auditoria (ver expedicao_auditoria_routes.py).
    excluido = db.Column(db.Boolean, nullable=False, default=False, index=True)
    excluido_at = db.Column(db.DateTime)
    excluido_by = db.Column(db.String(100))
    excluido_motivo = db.Column(db.String(300))

    created_at = db.Column(db.DateTime, default=datetime.now, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.now, nullable=False)

    itens = db.relationship(
        "ExpedicaoOrdemSTItem",
        backref="ordem",
        cascade="all, delete-orphan",
        order_by="ExpedicaoOrdemSTItem.linha",
    )


class ExpedicaoOrdemSTItem(db.Model):
    __tablename__ = "expedicao_ordem_st_item"

    id = db.Column(db.Integer, primary_key=True)
    ordem_id = db.Column(
        db.Integer,
        db.ForeignKey("expedicao_ordem_st.id"),
        nullable=False,
        index=True,
    )
    linha = db.Column(db.Integer, nullable=False, default=0)
    cod_interno = db.Column(db.String(80), index=True)
    item = db.Column(db.String(200))
    n_os = db.Column(db.String(80), index=True)
    # Quantidade enviada/esperada para o ST - NUNCA exibida ao conferente
    qtde_a_faturar = db.Column(db.Integer, nullable=False, default=0)
    qtde_conferida = db.Column(db.Integer)
    divergente = db.Column(db.Boolean, nullable=False, default=False)


class ExpedicaoConferenciaLog(db.Model):
    """Trilha de auditoria das conferencias de expedicao (FAT e ST).

    Registra cada conferencia ou edicao posterior, guardando o que mudou
    (cabecalho e itens) para consultas futuras. Usado tanto quando a
    conferencia e feita no momento correto (fila pendente) quanto quando e
    feita/corrigida apos o faturamento."""

    __tablename__ = "expedicao_conferencia_log"

    id = db.Column(db.Integer, primary_key=True)
    # ID interno permanente e rastreavel (ex.: CNF-000123) para citar/buscar
    # este evento de conferencia especifico na auditoria (ver
    # expedicao_auditoria_routes.py).
    codigo_interno = db.Column(db.String(20), unique=True, index=True)
    origem = db.Column(db.String(10), nullable=False, index=True)  # "fat" | "st"
    ordem_id = db.Column(db.Integer, nullable=False, index=True)
    cod_ordem = db.Column(db.String(80), nullable=False, index=True)
    # "conferencia" (primeira) | "edicao" (alteracao posterior)
    acao = db.Column(db.String(20), nullable=False, default="conferencia")
    usuario = db.Column(db.String(100))
    status_anterior = db.Column(db.String(40))
    status_novo = db.Column(db.String(40))
    divergente = db.Column(db.Boolean, nullable=False, default=False)
    pos_faturamento = db.Column(db.Boolean, nullable=False, default=False)
    # JSON com o detalhamento das alteracoes (cabecalho + itens).
    detalhes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.now, nullable=False, index=True)


class SolicitacaoNF(db.Model):
    """Solicitacao de saida de material sem venda direta (garantia,
    bonificacao, teste ou atendimento tecnico), aberta por qualquer
    funcionario via formulario publico (sem login), separada pela
    logistica/fiscal/admin e faturada pelo fiscal."""

    __tablename__ = "solicitacao_nf"

    id = db.Column(db.Integer, primary_key=True)
    protocolo = db.Column(db.String(20), unique=True, index=True)

    solicitante_codigo = db.Column(db.String(40))
    solicitante_nome = db.Column(db.String(160), nullable=False)
    solicitante_setor = db.Column(db.String(120))

    # Garantia | Bonificação | Teste | Atendimento técnico
    tipo_operacao = db.Column(db.String(40), nullable=False)
    # Indica se havera retorno do material ao estoque + orcamento de venda
    # posterior (acompanhado manualmente fora deste modulo por enquanto).
    venda_posterior = db.Column(db.Boolean, nullable=False, default=False)

    cliente_codigo = db.Column(db.String(40))
    cliente_nome = db.Column(db.String(160), nullable=False)
    cliente_documento = db.Column(db.String(30))

    # Solicitado | Expedido sem nota fiscal | Notas fiscais emitidas |
    # Estoque em poder de terceiros | Estoque em poder da Assistência
    # técnica | Estoque retornado
    status = db.Column(db.String(60), nullable=False, default="Solicitado", index=True)

    separado_por = db.Column(db.String(100))
    separado_at = db.Column(db.DateTime)
    observacoes_separacao = db.Column(db.String(500))

    faturado_por = db.Column(db.String(100))
    faturado_at = db.Column(db.DateTime)
    numero_nf = db.Column(db.String(80))
    observacoes_faturamento = db.Column(db.String(500))

    # Retorno do material (Teste / Atendimento técnico) apos uso "emprestado"
    numero_nf_retorno = db.Column(db.String(80))
    retorno_por = db.Column(db.String(100))
    retorno_at = db.Column(db.DateTime)
    observacoes_retorno = db.Column(db.String(500))

    # Dados do parceiro puxados da NF na bridge (Remessa para Conserto):
    # para quem o material foi enviado (destinatario) e o endereco.
    nf_parceiro_nome = db.Column(db.String(200))
    nf_parceiro_endereco = db.Column(db.String(400))

    # Ordem de faturamento (cod_ordem_fat) do ERP vinculada a esta expedicao
    # sem NF. Quando a OF for faturada (numero_nf preenchido), a solicitacao
    # avanca automaticamente para o status final conforme o tipo.
    ordem_faturamento = db.Column(db.Integer, index=True)

    ip_solicitante = db.Column(db.String(64))
    created_at = db.Column(db.DateTime, default=datetime.now, nullable=False, index=True)
    updated_at = db.Column(db.DateTime, default=datetime.now, nullable=False)

    itens = db.relationship(
        "SolicitacaoNFItem",
        backref="solicitacao",
        cascade="all, delete-orphan",
        order_by="SolicitacaoNFItem.linha",
    )


class SolicitacaoNFItem(db.Model):
    __tablename__ = "solicitacao_nf_item"

    id = db.Column(db.Integer, primary_key=True)
    solicitacao_id = db.Column(
        db.Integer,
        db.ForeignKey("solicitacao_nf.id"),
        nullable=False,
        index=True,
    )
    linha = db.Column(db.Integer, nullable=False, default=0)
    material_codigo = db.Column(db.String(80), index=True)
    material_nome = db.Column(db.String(200))
    # Local de estoque (endereco) do material no ERP (tproduto.localizacao_estoque),
    # congelado no momento em que a solicitacao e criada.
    material_local = db.Column(db.String(160))
    quantidade = db.Column(db.Float, nullable=False, default=0)
    separado = db.Column(db.Boolean, nullable=False, default=False)


class SolicitacaoNFLog(db.Model):
    """Trilha de auditoria da solicitacao de NF (criacao, separacao,
    faturamento, cancelamento)."""

    __tablename__ = "solicitacao_nf_log"

    id = db.Column(db.Integer, primary_key=True)
    solicitacao_id = db.Column(db.Integer, nullable=False, index=True)
    acao = db.Column(db.String(30), nullable=False)  # criada|separada|faturada|cancelada
    usuario = db.Column(db.String(100))
    status_anterior = db.Column(db.String(20))
    status_novo = db.Column(db.String(20))
    detalhes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.now, nullable=False, index=True)


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
    orcamento = db.Column(db.String(80), index=True)
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
    # Viagem avulsa: veiculo sai sem solicitacoes/paradas, apenas com o funcionario responsavel
    avulsa = db.Column(db.Boolean, default=False, nullable=False, index=True)
    funcionario_responsavel = db.Column(db.String(160))


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
    unidade_logistica_id = db.Column(db.Integer, db.ForeignKey("wms_unidade_logistica.id"), index=True)
    status_estoque = db.Column(db.String(30), nullable=False, default="Disponivel", index=True)  # Disponivel|Bloqueado|Quarentena|Avaria|Qualidade
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


class WMSUnidadeLogistica(db.Model):
    """Unidade logistica rastreavel: pallet, caixa, gaiola ou volume interno."""
    __tablename__ = "wms_unidade_logistica"

    id = db.Column(db.Integer, primary_key=True)
    codigo = db.Column(db.String(80), nullable=False, unique=True, index=True)
    tipo = db.Column(db.String(20), nullable=False, default="PALLET", index=True)  # PALLET|CAIXA|GAIOLA|VOLUME
    status = db.Column(db.String(20), nullable=False, default="Aberta", index=True)  # Aberta|Fechada|Movimentando|Concluida|Cancelada
    deposito_id = db.Column(db.Integer, db.ForeignKey("deposito_wms.id"), index=True)
    localizacao_id = db.Column(db.Integer, db.ForeignKey("localizacao_armazem.id"), index=True)
    observacao = db.Column(db.String(400))
    criado_por = db.Column(db.String(100))
    criado_em = db.Column(db.DateTime, default=datetime.now, nullable=False, index=True)
    fechado_em = db.Column(db.DateTime)


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


class WMSTarefaOperacional(db.Model):
    """Tarefa guiada para coletor WMS (enderecamento, movimentacao, inventario, separacao)."""
    __tablename__ = "wms_tarefa_operacional"

    id = db.Column(db.Integer, primary_key=True)
    tipo = db.Column(db.String(30), nullable=False, index=True)  # ENDERECAMENTO|MOVIMENTACAO|INVENTARIO|SEPARACAO
    status = db.Column(db.String(20), nullable=False, default="PENDENTE", index=True)  # PENDENTE|EM_EXECUCAO|CONCLUIDA|CANCELADA
    item_wms_id = db.Column(db.Integer, db.ForeignKey("item_wms.id"), index=True)
    localizacao_origem_id = db.Column(db.Integer, db.ForeignKey("localizacao_armazem.id"), index=True)
    localizacao_destino_id = db.Column(db.Integer, db.ForeignKey("localizacao_armazem.id"), index=True)
    qtd_planejada = db.Column(db.Float)
    qtd_executada = db.Column(db.Float)
    prioridade = db.Column(db.String(10), nullable=False, default="MEDIA", index=True)  # BAIXA|MEDIA|ALTA|CRITICA
    atribuido_para = db.Column(db.String(100), index=True)
    criado_por = db.Column(db.String(100))
    iniciado_por = db.Column(db.String(100))
    concluido_por = db.Column(db.String(100))
    observacao = db.Column(db.String(500))
    criado_em = db.Column(db.DateTime, default=datetime.now, nullable=False, index=True)
    iniciado_em = db.Column(db.DateTime)
    concluido_em = db.Column(db.DateTime)


class WMSInventarioCiclico(db.Model):
    """Contagem ciclica operacional por SKU/local."""
    __tablename__ = "wms_inventario_ciclico"

    id = db.Column(db.Integer, primary_key=True)
    codigo = db.Column(db.String(80), nullable=False, unique=True, index=True)
    status = db.Column(db.String(20), nullable=False, default="Aberto", index=True)  # Aberto|Contado|Aprovado|Rejeitado|Cancelado
    localizacao_id = db.Column(db.Integer, db.ForeignKey("localizacao_armazem.id"), nullable=False, index=True)
    codigo_item = db.Column(db.String(50), nullable=False, index=True)
    qtd_sistema = db.Column(db.Float, nullable=False, default=0.0)
    qtd_contada = db.Column(db.Float)
    diferenca = db.Column(db.Float)
    tarefa_id = db.Column(db.Integer, db.ForeignKey("wms_tarefa_operacional.id"), index=True)
    criado_por = db.Column(db.String(100))
    contado_por = db.Column(db.String(100))
    aprovado_por = db.Column(db.String(100))
    motivo = db.Column(db.String(400))
    criado_em = db.Column(db.DateTime, default=datetime.now, nullable=False, index=True)
    contado_em = db.Column(db.DateTime)
    aprovado_em = db.Column(db.DateTime)


class LogisticaInventarioInicial(db.Model):
    """Inventario inicial simplificado da operacao de Logistica."""

    __tablename__ = "logistica_inventario_inicial"

    id = db.Column(db.Integer, primary_key=True)
    local_codigo = db.Column(db.String(120), nullable=False, index=True)
    codigo_produto = db.Column(db.String(120), nullable=False, index=True)
    unidade_medida = db.Column(db.String(20), nullable=False, default="UN")
    quantidade = db.Column(db.Float, nullable=False, default=0)
    lote = db.Column(db.String(120))
    observacao = db.Column(db.String(800))
    criado_por = db.Column(db.String(100), nullable=False, index=True)
    criado_em = db.Column(db.DateTime, default=datetime.now, nullable=False, index=True)
    atualizado_em = db.Column(db.DateTime, default=datetime.now, nullable=False)


class WMSPedidoSeparacao(db.Model):
    """Pedido/tarefa simples de separacao para expedir ou abastecer processo."""
    __tablename__ = "wms_pedido_separacao"

    id = db.Column(db.Integer, primary_key=True)
    referencia = db.Column(db.String(80), nullable=False, index=True)
    status = db.Column(db.String(20), nullable=False, default="Aberto", index=True)  # Aberto|Separando|Separado|Cancelado
    codigo_item = db.Column(db.String(50), nullable=False, index=True)
    qtd_solicitada = db.Column(db.Float, nullable=False, default=0.0)
    qtd_separada = db.Column(db.Float, nullable=False, default=0.0)
    localizacao_id = db.Column(db.Integer, db.ForeignKey("localizacao_armazem.id"), index=True)
    tarefa_id = db.Column(db.Integer, db.ForeignKey("wms_tarefa_operacional.id"), index=True)
    criado_por = db.Column(db.String(100))
    separado_por = db.Column(db.String(100))
    observacao = db.Column(db.String(400))
    criado_em = db.Column(db.DateTime, default=datetime.now, nullable=False, index=True)
    separado_em = db.Column(db.DateTime)



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


class CadastroAtualizacaoPublica(db.Model):
    """Atualizacao cadastral enviada pelo proprio cliente/fornecedor via pagina
    publica (sem login). Fica em fila de revisao interna."""
    __tablename__ = "cadastro_atualizacao_publica"

    id = db.Column(db.Integer, primary_key=True)
    tipo = db.Column(db.String(20), nullable=False, index=True)  # cliente | fornecedor
    documento = db.Column(db.String(20), nullable=False, index=True)  # CNPJ
    razao_social = db.Column(db.String(220))
    nome_fantasia = db.Column(db.String(220))
    inscricao_estadual = db.Column(db.String(40))
    regime_tributario = db.Column(db.String(40), nullable=False)
    contribuinte_icms = db.Column(db.String(60))
    possui_beneficios_fiscais = db.Column(db.Boolean, nullable=False, default=False)
    beneficios_fiscais_descricao = db.Column(db.String(500))
    endereco = db.Column(db.String(300))
    cep = db.Column(db.String(12))
    municipio = db.Column(db.String(120))
    uf = db.Column(db.String(2))
    telefone = db.Column(db.String(40))
    email = db.Column(db.String(160))
    email_confirmado = db.Column(db.Boolean, nullable=False, default=False)
    contato = db.Column(db.String(120))
    observacoes = db.Column(db.String(500))
    situacao_cadastral = db.Column(db.String(60))
    fonte_cnpj = db.Column(db.String(40))
    dados_json = db.Column(db.Text)
    status = db.Column(db.String(40), nullable=False, default="Pendente de revisão", index=True)
    origem_ip = db.Column(db.String(60))
    created_at = db.Column(db.DateTime, default=datetime.now, nullable=False, index=True)


class QualidadeCertificado(db.Model):
    """Análise de qualidade disparada após a conferência de recebimento quando o
    remetente da NF é um fornecedor de tratamento térmico (Brasimet, Metal
    Paulista ou Friese). O analista de qualidade anexa a foto do certificado e
    preenche os dados do laudo técnico (linhas Grid e Sapatas)."""
    __tablename__ = "qualidade_certificado"

    id = db.Column(db.Integer, primary_key=True)
    numero_nota = db.Column(db.String(20), nullable=False, index=True)
    chave_acesso = db.Column(db.String(44), index=True)
    fornecedor = db.Column(db.String(100))

    # Campos preenchidos pelo analista de qualidade
    numero_orcamento = db.Column(db.String(120))  # Orçamento vinculado ao modelo
    numero_certificado = db.Column(db.String(120))  # Nº Laudo do Material (célula N25)
    os = db.Column(db.String(120))  # Campo legado compartilhado; mantido por compatibilidade

    # Linha GRID (registro dos resultados do corpo de prova)
    grid_os = db.Column(db.String(120))       # OS / Lote-CP da linha Grid
    grid_numero_certificado = db.Column(db.String(120))
    grid_dureza = db.Column(db.String(120))   # F35
    grid_chd = db.Column(db.String(120))      # I35
    grid_resultado = db.Column(db.String(20))  # L35 - Conforme | Não Conforme

    # Linha SAPATAS
    sapatas_os = db.Column(db.String(120))       # OS / Lote-CP da linha Sapatas
    sapatas_numero_certificado = db.Column(db.String(120))
    sapatas_dureza = db.Column(db.String(120))   # F36
    sapatas_chd = db.Column(db.String(120))      # I36
    sapatas_resultado = db.Column(db.String(20))  # L36 - Conforme | Não Conforme

    foto_path = db.Column(db.String(255))

    status = db.Column(db.String(30), nullable=False, default="Pendente de análise", index=True)
    # Status: "Pendente de análise" | "Laudo emitido" | "Laudo aprovado"
    analista = db.Column(db.String(100))
    criado_em = db.Column(db.DateTime, default=datetime.now, nullable=False, index=True)
    analisado_em = db.Column(db.DateTime)   # data de emissão do laudo (execução)
    aprovado_em = db.Column(db.DateTime)    # data de aprovação pelo supervisor/gerente
    aprovado_por = db.Column(db.String(100))

    __table_args__ = (
        db.UniqueConstraint("numero_nota", name="ux_qualidade_certificado_nota"),
    )


class ExpedicaoRomaneio(db.Model):
    """Romaneio de Expedição - documento que agrupa múltiplas NFes que serão expedidas juntas.
    Permite definir tipo de frete (FOB/CIF) e gerenciar informações consolidadas."""

    __tablename__ = "expedicao_romaneio"

    id = db.Column(db.Integer, primary_key=True)
    numero_romaneio = db.Column(db.String(40), nullable=False, unique=True, index=True)
    data_romaneio = db.Column(db.Date, nullable=False, default=datetime.now, index=True)
    orcamento = db.Column(db.String(80), index=True)
    cliente = db.Column(db.String(160))
    
    # Tipo de frete: FOB (Frete por conta do cliente) ou CIF (Frete por conta do fornecedor)
    tipo_frete = db.Column(db.String(10), nullable=False, default="FOB", index=True)

    # Dados do transportador (preenchidos no formulario do romaneio) — copiados
    # para o Registro de Expedicao quando o romaneio e expedido.
    transportadora = db.Column(db.String(160))
    placa = db.Column(db.String(20))
    motorista = db.Column(db.String(160))
    motorista_documento = db.Column(db.String(40))

    # Transportadora do frete FOB: a logistica digita apenas o CNPJ e o Sync
    # puxa os dados pelo cartao CNPJ (BrasilAPI), gravando um snapshot aqui.
    transportadora_documento = db.Column(db.String(40))
    transportadora_dados_json = db.Column(db.Text)

    # Dados consolidados das NFes
    peso_bruto_total = db.Column(db.Float, nullable=False, default=0)
    qtde_volumes_total = db.Column(db.Integer, nullable=False, default=0)
    
    # Campos livres para observações (conforme modelo do romaneio)
    observacao_1 = db.Column(db.String(500))
    observacao_2 = db.Column(db.String(500))
    observacao_3 = db.Column(db.String(500))
    
    # Assinatura do transportador (foto/imagem)
    assinatura_transportador_file_name = db.Column(db.String(260))
    assinatura_transportador_file_path = db.Column(db.String(500))
    assinatura_uploadado_em = db.Column(db.DateTime)
    assinatura_uploadado_por = db.Column(db.String(100))

    # Assinatura do conferente/responsavel (foto/imagem)
    assinatura_conferente_file_name = db.Column(db.String(260))
    assinatura_conferente_file_path = db.Column(db.String(500))
    assinatura_conferente_uploadado_em = db.Column(db.DateTime)
    assinatura_conferente_uploadado_por = db.Column(db.String(100))

    # Foto do carregamento (tirada durante o carregamento do caminhao, com o
    # romaneio ja Pronto)
    foto_carregamento_file_name = db.Column(db.String(260))
    foto_carregamento_file_path = db.Column(db.String(500))
    foto_carregamento_uploadado_em = db.Column(db.DateTime)
    foto_carregamento_uploadado_por = db.Column(db.String(100))

    # Status: Rascunho (em construção), Pronto (finalizado), Expedido (já expedido)
    status = db.Column(db.String(30), nullable=False, default="Rascunho", index=True)

    # Carta de correção (CC-e) da modalidade de frete: marcado quando o
    # operador finaliza o romaneio mesmo com NF cuja modalidade declarada
    # diverge do tipo_frete do romaneio. Fica pendente até o Faturamento
    # emitir a CC-e.
    cce_modalidade_pendente = db.Column(db.Boolean, nullable=False, default=False)
    cce_modalidade_aprovado_por = db.Column(db.String(100))
    cce_modalidade_aprovado_em = db.Column(db.DateTime)
    cce_modalidade_detalhe = db.Column(db.String(1000))

    # Auditoria
    criado_por = db.Column(db.String(100), nullable=False)
    criado_em = db.Column(db.DateTime, default=datetime.now, nullable=False, index=True)
    atualizado_por = db.Column(db.String(100))
    atualizado_em = db.Column(db.DateTime, default=datetime.now, nullable=False)
    expedido_por = db.Column(db.String(100))
    expedido_em = db.Column(db.DateTime)
    
    # Relacionamento com as NFes do romaneio
    nfs = db.relationship(
        "ExpedicaoRomaneioNF",
        backref="romaneio",
        cascade="all, delete-orphan",
        order_by="ExpedicaoRomaneioNF.numero_nf",
    )


class ExpedicaoRomaneioNF(db.Model):
    """Linhas do Romaneio - NFes que fazem parte de um romaneio de expedição."""

    __tablename__ = "expedicao_romaneio_nf"

    id = db.Column(db.Integer, primary_key=True)
    romaneio_id = db.Column(
        db.Integer,
        db.ForeignKey("expedicao_romaneio.id"),
        nullable=False,
        index=True,
    )
    
    # Informações da NF
    numero_nf = db.Column(db.String(160), nullable=False, index=True)
    orcamento = db.Column(db.String(80), index=True)
    # Ordem de compra (fluxo ST/Serviço de Terceiro). Orçamento e OC são
    # mutuamente exclusivos por NF: FAT preenche orcamento, ST preenche
    # ordem_compra. Um romaneio pode misturar NFs dos dois tipos.
    ordem_compra = db.Column(db.String(80), index=True)
    cliente = db.Column(db.String(160))
    
    # Dados da expedição
    peso_bruto = db.Column(db.Float, nullable=False, default=0)
    qtde_volumes = db.Column(db.Integer, nullable=False, default=0)
    especie_volumes = db.Column(db.String(80))
    
    # Relação de OSs desta NF (comma-separated ou JSON)
    numeros_os = db.Column(db.String(500))

    # Modalidade de frete declarada na própria NF-e (código modFrete do XML:
    # 0/3 = remetente (CIF), 1/4 = destinatário (FOB), 2 = terceiros,
    # 9 = sem frete). Preenchido na inclusão da NF e usado para detectar
    # divergência com o tipo_frete do romaneio na finalização.
    modfrete_nf = db.Column(db.String(4))

    # Auditoria
    adicionado_em = db.Column(db.DateTime, default=datetime.now, nullable=False)
    adicionado_por = db.Column(db.String(100), nullable=False)
    
    __table_args__ = (
        db.UniqueConstraint(
            "romaneio_id",
            "numero_nf",
            name="ux_romaneio_nf_unique",
        ),
    )


class ExpedicaoRomaneioExclusao(db.Model):
    """Solicitacao de exclusao de um romaneio em Rascunho feita por quem nao
    e Admin — precisa de aprovacao. Mesmo padrao de
    ExpedicaoConferenciaSimplesEstorno."""

    __tablename__ = "expedicao_romaneio_exclusao"

    id = db.Column(db.Integer, primary_key=True)
    romaneio_id = db.Column(
        db.Integer,
        db.ForeignKey("expedicao_romaneio.id"),
        nullable=False,
        index=True,
    )
    solicitante = db.Column(db.String(100), nullable=False)
    motivo = db.Column(db.String(500), nullable=False)
    # Pendente | Aprovado | Rejeitado
    status = db.Column(db.String(20), nullable=False, default="Pendente", index=True)
    admin_usuario = db.Column(db.String(100))
    admin_observacao = db.Column(db.String(500))
    resolvido_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.now, nullable=False, index=True)


class ExpedicaoRomaneioEstorno(db.Model):
    """Solicitacao de estorno de um romaneio ja finalizado (Pronto/Expedido)
    para voltar a Rascunho e permitir edicao — pedida pela Bia por quem nao e
    Admin. Precisa de aprovacao de um Admin. Mesmo padrao de
    ExpedicaoRomaneioExclusao."""

    __tablename__ = "expedicao_romaneio_estorno"

    id = db.Column(db.Integer, primary_key=True)
    romaneio_id = db.Column(
        db.Integer,
        db.ForeignKey("expedicao_romaneio.id"),
        nullable=False,
        index=True,
    )
    solicitante = db.Column(db.String(100), nullable=False)
    motivo = db.Column(db.String(500), nullable=False)
    # Estado do romaneio no momento do pedido (Pronto | Expedido) — informativo.
    status_romaneio = db.Column(db.String(20))
    # Pendente | Aprovado | Rejeitado
    status = db.Column(db.String(20), nullable=False, default="Pendente", index=True)
    admin_usuario = db.Column(db.String(100))
    admin_observacao = db.Column(db.String(500))
    resolvido_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.now, nullable=False, index=True)


class BiaMensagem(db.Model):
    """Aviso/recado enviado por um Admin (via chat da Bia) para um usuario
    especifico, um cargo inteiro ou todos (broadcast). A Bia entrega in-app:
    toast + registro no painel. A leitura por usuario fica em BiaMensagemLeitura
    (uma mensagem de cargo/broadcast atinge varias pessoas)."""

    __tablename__ = "bia_mensagem"

    id = db.Column(db.Integer, primary_key=True)
    remetente = db.Column(db.String(100), nullable=False, index=True)
    remetente_nome = db.Column(db.String(160))
    # usuario | cargo | broadcast
    destino_tipo = db.Column(db.String(20), nullable=False, index=True)
    # username (usuario) | role (cargo) | "" (broadcast)
    destino_valor = db.Column(db.String(120), nullable=False, default="", index=True)
    texto = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.now, nullable=False, index=True)


class BiaMensagemLeitura(db.Model):
    """Marca que um usuario ja recebeu/leu uma BiaMensagem (para nao reentregar).
    Uma linha por (mensagem, usuario)."""

    __tablename__ = "bia_mensagem_leitura"

    id = db.Column(db.Integer, primary_key=True)
    mensagem_id = db.Column(
        db.Integer,
        db.ForeignKey("bia_mensagem.id"),
        nullable=False,
        index=True,
    )
    username = db.Column(db.String(100), nullable=False, index=True)
    lida_em = db.Column(db.DateTime, default=datetime.now, nullable=False)

    __table_args__ = (
        db.UniqueConstraint("mensagem_id", "username", name="ux_bia_mensagem_leitura"),
    )


class ExpedicaoRomaneioFotoCarregamento(db.Model):
    """Fotos do carregamento do romaneio (multiplas, tiradas durante o
    carregamento do caminhao). Complementa as colunas legadas
    foto_carregamento_* de ExpedicaoRomaneio, que guardavam so uma foto."""

    __tablename__ = "expedicao_romaneio_foto_carregamento"

    id = db.Column(db.Integer, primary_key=True)
    romaneio_id = db.Column(
        db.Integer,
        db.ForeignKey("expedicao_romaneio.id"),
        nullable=False,
        index=True,
    )
    file_name = db.Column(db.String(260), nullable=False)
    file_path = db.Column(db.String(500), nullable=False)
    uploaded_at = db.Column(db.DateTime, default=datetime.now, nullable=False)
    uploaded_by = db.Column(db.String(100))


class ExpedicaoCobranca(db.Model):
    """Follow-up (cobrança) da Bia sobre uma pendência de expedição. Uma linha
    por pendência acompanhada, identificada por (ref_tipo, ref_id). A Bia
    pergunta o motivo do atraso, registra a resposta e refaz o follow-up a cada
    24h enquanto a pendência continuar em aberto."""

    __tablename__ = "expedicao_cobranca"

    id = db.Column(db.Integer, primary_key=True)
    # Identidade da pendência (bate com os itens do insights do assistente).
    ref_tipo = db.Column(db.String(20), nullable=False, index=True)   # fat/st/romaneio/registro
    ref_id = db.Column(db.String(60), nullable=False, index=True)     # cod_ordem/numero/id

    # Snapshot da pendência (atualizado a cada sincronização).
    categoria = db.Column(db.String(40), nullable=False, default="")  # chave do card
    titulo = db.Column(db.String(160), default="")
    referencia = db.Column(db.String(200), default="")                # cliente/fornecedor
    numero_nf = db.Column(db.String(40), default="")
    severidade = db.Column(db.String(10), default="media")

    # aberta (nunca respondida) | respondida | resolvida (saiu da lista) |
    # ignorada (backlog anterior à ativação — acompanhada, mas nunca cobrada)
    status = db.Column(db.String(20), nullable=False, default="aberta", index=True)
    motivo = db.Column(db.String(1000), default="")                   # último motivo informado

    primeira_cobranca_em = db.Column(db.DateTime)
    ultima_cobranca_em = db.Column(db.DateTime)
    proxima_cobranca_em = db.Column(db.DateTime, index=True)          # None = perguntar já
    respondida_por = db.Column(db.String(100))
    respondida_em = db.Column(db.DateTime)

    criada_em = db.Column(db.DateTime, default=datetime.now, nullable=False)
    atualizada_em = db.Column(db.DateTime, default=datetime.now, nullable=False)
    resolvida_em = db.Column(db.DateTime)

    __table_args__ = (
        db.UniqueConstraint("ref_tipo", "ref_id", name="uq_expedicao_cobranca_ref"),
    )

    logs = db.relationship(
        "ExpedicaoCobrancaLog",
        backref="cobranca",
        cascade="all, delete-orphan",
        order_by="ExpedicaoCobrancaLog.criado_em",
    )


class ExpedicaoCobrancaLog(db.Model):
    """Histórico de interações de uma cobrança (perguntas da Bia e respostas)."""

    __tablename__ = "expedicao_cobranca_log"

    id = db.Column(db.Integer, primary_key=True)
    cobranca_id = db.Column(
        db.Integer,
        db.ForeignKey("expedicao_cobranca.id"),
        nullable=False,
        index=True,
    )
    # cobranca (Bia perguntou) | resposta (usuário respondeu) | sistema
    tipo = db.Column(db.String(20), nullable=False, default="resposta")
    texto = db.Column(db.String(1000), nullable=False, default="")
    autor = db.Column(db.String(100), default="")
    criado_em = db.Column(db.DateTime, default=datetime.now, nullable=False, index=True)


class PlannerBoard(db.Model):
    __tablename__ = "planner_board"

    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(120), nullable=False, unique=True, index=True)
    criado_por = db.Column(db.String(100), nullable=False)
    criado_em = db.Column(db.DateTime, default=datetime.now, nullable=False, index=True)
    atualizado_em = db.Column(db.DateTime, default=datetime.now, nullable=False, index=True)

    colunas = db.relationship(
        "PlannerColumn",
        backref="board",
        cascade="all, delete-orphan",
        order_by="PlannerColumn.order_index.asc(), PlannerColumn.id.asc()",
    )
    labels = db.relationship(
        "PlannerLabel",
        backref="board",
        cascade="all, delete-orphan",
        order_by="PlannerLabel.order_index.asc(), PlannerLabel.id.asc()",
    )


class PlannerColumn(db.Model):
    __tablename__ = "planner_column"

    id = db.Column(db.Integer, primary_key=True)
    board_id = db.Column(db.Integer, db.ForeignKey("planner_board.id"), nullable=False, index=True)
    titulo = db.Column(db.String(80), nullable=False)
    color = db.Column(db.String(20), nullable=False, default="#0f62c9")
    is_done = db.Column(db.Boolean, nullable=False, default=False, index=True)
    order_index = db.Column(db.Integer, nullable=False, default=0, index=True)
    criado_por = db.Column(db.String(100), nullable=False)
    criado_em = db.Column(db.DateTime, default=datetime.now, nullable=False, index=True)
    atualizado_em = db.Column(db.DateTime, default=datetime.now, nullable=False, index=True)

    cards = db.relationship(
        "PlannerCard",
        backref="column",
        cascade="all, delete-orphan",
        order_by="PlannerCard.order_index.asc(), PlannerCard.id.asc()",
    )


class PlannerCard(db.Model):
    __tablename__ = "planner_card"

    id = db.Column(db.Integer, primary_key=True)
    column_id = db.Column(db.Integer, db.ForeignKey("planner_column.id"), nullable=False, index=True)
    titulo = db.Column(db.String(180), nullable=False)
    descricao = db.Column(db.Text)
    prioridade = db.Column(db.String(20), nullable=False, default="Media", index=True)
    responsavel = db.Column(db.String(100), index=True)
    prazo = db.Column(db.Date, index=True)
    order_index = db.Column(db.Integer, nullable=False, default=0, index=True)
    criado_por = db.Column(db.String(100), nullable=False)
    atualizado_por = db.Column(db.String(100))
    criado_em = db.Column(db.DateTime, default=datetime.now, nullable=False, index=True)
    atualizado_em = db.Column(db.DateTime, default=datetime.now, nullable=False, index=True)
    concluido_em = db.Column(db.DateTime, index=True)

    labels = db.relationship(
        "PlannerCardLabel",
        backref="card",
        cascade="all, delete-orphan",
        order_by="PlannerCardLabel.id.asc()",
    )
    comentarios = db.relationship(
        "PlannerCardComment",
        backref="card",
        cascade="all, delete-orphan",
        order_by="PlannerCardComment.criado_em.asc(), PlannerCardComment.id.asc()",
    )
    checklist_itens = db.relationship(
        "PlannerChecklistItem",
        backref="card",
        cascade="all, delete-orphan",
        order_by="PlannerChecklistItem.order_index.asc(), PlannerChecklistItem.id.asc()",
    )


class PlannerLabel(db.Model):
    __tablename__ = "planner_label"

    id = db.Column(db.Integer, primary_key=True)
    board_id = db.Column(db.Integer, db.ForeignKey("planner_board.id"), nullable=False, index=True)
    nome = db.Column(db.String(60), nullable=False)
    color = db.Column(db.String(20), nullable=False, default="#0f62c9")
    order_index = db.Column(db.Integer, nullable=False, default=0, index=True)
    criado_por = db.Column(db.String(100), nullable=False)
    criado_em = db.Column(db.DateTime, default=datetime.now, nullable=False, index=True)


class PlannerCardLabel(db.Model):
    __tablename__ = "planner_card_label"

    id = db.Column(db.Integer, primary_key=True)
    card_id = db.Column(db.Integer, db.ForeignKey("planner_card.id"), nullable=False, index=True)
    label_id = db.Column(db.Integer, db.ForeignKey("planner_label.id"), nullable=False, index=True)
    criado_em = db.Column(db.DateTime, default=datetime.now, nullable=False, index=True)

    label = db.relationship("PlannerLabel")

    __table_args__ = (
        db.UniqueConstraint("card_id", "label_id", name="ux_planner_card_label"),
    )


class PlannerCardComment(db.Model):
    __tablename__ = "planner_card_comment"

    id = db.Column(db.Integer, primary_key=True)
    card_id = db.Column(db.Integer, db.ForeignKey("planner_card.id"), nullable=False, index=True)
    texto = db.Column(db.Text, nullable=False)
    criado_por = db.Column(db.String(100), nullable=False)
    criado_em = db.Column(db.DateTime, default=datetime.now, nullable=False, index=True)


class PlannerChecklistItem(db.Model):
    __tablename__ = "planner_checklist_item"

    id = db.Column(db.Integer, primary_key=True)
    card_id = db.Column(db.Integer, db.ForeignKey("planner_card.id"), nullable=False, index=True)
    texto = db.Column(db.String(240), nullable=False)
    is_done = db.Column(db.Boolean, nullable=False, default=False, index=True)
    order_index = db.Column(db.Integer, nullable=False, default=0, index=True)
    criado_por = db.Column(db.String(100), nullable=False)
    criado_em = db.Column(db.DateTime, default=datetime.now, nullable=False, index=True)
    atualizado_em = db.Column(db.DateTime, default=datetime.now, nullable=False, index=True)


# ═══════════════════════════════════════════════════════════════════════════
# COMEX — Gestao de processos de importacao/exportacao
# ═══════════════════════════════════════════════════════════════════════════
#
# Workflow: OC -> PO -> Cotacao -> Instrucao e Documentacao -> Coleta ->
# Em Transito -> Desembarque -> Desembaraco -> Transporte -> NF/Cambio.
#
# ComexProcesso e uma tabela larga unica que atravessa todo o workflow: cada
# etapa so preenche suas proprias colunas na mesma linha (identificada pela
# Ref FF), evitando "colcha de retalhos" de varias tabelas por modulo. Ver
# COMEX_ESPECIFICACAO.md na raiz do repo para a especificacao completa.
#
# Tabelas satelite existem so onde a relacao e genuinamente 1:N: cotacoes
# recebidas (todas, nao so a vencedora - auditoria), follow-up (historico de
# comentarios reutilizado por varios modulos, mesmo molde de
# ExpedicaoCobranca/ExpedicaoCobrancaLog), lembretes automaticos e fotos de
# divergencia na entrega (mesmo molde de ExpedicaoRomaneioFotoCarregamento).


class ComexProcesso(db.Model):
    """Processo de importacao/exportacao - tabela central que atravessa todo
    o workflow (OC -> PO -> Cotacao -> ... -> NF/Cambio). O ID OP (`id_op`)
    e o identificador unico gerado pelo sistema, usado em todos os modulos.
    `ref_ff` e um campo separado, preenchido manualmente mais adiante (a
    partir do Modulo 3/Cotacao) com a referencia que o proprio freight
    forward atribui ao processo - nao e gerado pelo sistema. Nesta primeira
    leva apenas os modulos OC e PO sao operados pela UI; os campos dos
    demais modulos ja existem no schema para as proximas levas."""

    __tablename__ = "comex_processo"

    id = db.Column(db.Integer, primary_key=True)

    # ── Identificacao ────────────────────────────────────────────────────
    id_op = db.Column(db.String(30), nullable=False, unique=True, index=True)
    # Referencia freight forward - atribuida pelo freight forward (Modulo 3
    # em diante), nao gerada pelo sistema. Fica em branco ate la.
    ref_ff = db.Column(db.String(80), index=True)
    tipo_operacao = db.Column(db.String(2), nullable=False, default="IM")  # IM | IA
    status_modulo = db.Column(db.String(40), nullable=False, default="OC", index=True)
    status_slug = db.Column(db.String(40), nullable=False, default="oc", index=True)
    criado_em = db.Column(db.DateTime, default=datetime.now, nullable=False, index=True)
    criado_por = db.Column(db.String(100), nullable=False)
    atualizado_em = db.Column(db.DateTime, default=datetime.now, nullable=False)
    atualizado_por = db.Column(db.String(100))

    # ── Modulo 1: OC (espelha SQL_HIST_OC_HEADER do bridge GRV/Compras) ───
    cod_empresa = db.Column(db.Integer, index=True)
    cod_ordem_compra = db.Column(db.Integer, index=True)
    cod_compra = db.Column(db.String(40))
    numero_os = db.Column(db.String(200))
    fornecedor = db.Column(db.String(200), index=True)
    comprador = db.Column(db.String(100))
    dt_lancamento_oc = db.Column(db.DateTime)
    dt_recebimento_oc = db.Column(db.DateTime)
    total_produtos_oc = db.Column(db.Float)
    total_oc = db.Column(db.Float)
    qtd_linhas_oc = db.Column(db.Integer)
    qtd_produtos_oc = db.Column(db.Integer)
    situacao_oc = db.Column(db.String(20))
    oc_origem_payload = db.Column(db.Text)  # JSON bruto da consulta ao ERP, para auditoria

    # ── Modulo 2: PO ────────────────────────────────────────────────────
    po_numero = db.Column(db.String(40), index=True)
    po_ocs_vinculadas = db.Column(db.Text)  # JSON: lista de cod_ordem_compra (mesmo fornecedor)
    pagador_frete = db.Column(db.String(20))  # Columbia | Cliente-Fornecedor
    po_status = db.Column(db.String(20), default="Rascunho")  # Rascunho | Finalizada
    po_pdf_file_name = db.Column(db.String(260))
    po_pdf_file_path = db.Column(db.String(500))
    po_enviada_em = db.Column(db.DateTime)
    po_enviada_por = db.Column(db.String(100))
    po_destinatarios_email = db.Column(db.String(500))  # separados por ";"
    po_finalizada_sem_envio = db.Column(db.Boolean, default=False)

    # ── Dados gerais de operacao (visiveis a partir da PO, editaveis pelo ──
    # operador ao longo de todo o processo - nao sao exclusivos de um so
    # modulo, por isso ficam fora dos blocos "Modulo N"). `direcao_operacao`
    # e `modal_transporte` juntos determinam o prefixo do ID OP (ver
    # comex_service._derivar_tipo_operacao): IMPO+Maritimo=IM,
    # IMPO+Aereo=IA, EXPO+Maritimo=EM, EXPO+Aereo=EA.
    direcao_operacao = db.Column(db.String(4))  # IMPO | EXPO
    modal_transporte = db.Column(db.String(20))  # Aereo | Maritimo
    po_data = db.Column(db.Date)
    ref_despachante = db.Column(db.String(40))
    bl_awb = db.Column(db.String(40))
    invoice_numero = db.Column(db.String(40))
    etd = db.Column(db.Date)
    previsao_entrega = db.Column(db.Date)
    entrega_real = db.Column(db.String(40))
    nf_impo = db.Column(db.String(40))
    nf_recebimento = db.Column(db.String(40))

    # ── Modulo 3: Cotacao (resumo; detalhe/historico em ComexCotacao) ──────
    frete_aplicavel = db.Column(db.Boolean)  # deriva de pagador_frete == "Columbia"
    cotacao_vencedora_id = db.Column(db.Integer, db.ForeignKey("comex_cotacao.id"))
    cotacao_justificativa = db.Column(db.Text)
    # Taxa de cambio de referencia (ex.: PTAX do dia), digitada uma vez pelo
    # operador e usada pra converter o custo total de TODAS as cotacoes
    # desse processo pra um total consolidado em BRL - garante que a
    # comparacao entre fornecedores use a mesma taxa, nao a que cada um
    # informou por conta propria.
    taxa_cambio_referencia = db.Column(db.Float)

    # ── Modulo 4: Instrucao de Embarque (versao minima) ────────────────────
    # So depois que a instrucao e enviada (cotacao ja escolhida) que os
    # campos "Dados gerais de operacao" acima (ref_despachante, bl_awb,
    # invoice_numero, etd, previsao_entrega, entrega_real, nf_impo,
    # nf_recebimento; ETA reaproveita em_transito_eta) ficam liberados para
    # edicao - antes disso ainda nao sao conhecidos.
    instrucao_enviada_em = db.Column(db.DateTime)
    instrucao_enviada_por = db.Column(db.String(100))

    # ── Modulos 4-7: datas/flags rapidos (detalhe no Follow-up) ───────────
    coleta_data = db.Column(db.DateTime)
    em_transito_eta = db.Column(db.Date)
    desembarque_data = db.Column(db.DateTime)
    numero_duimp = db.Column(db.String(60))
    data_duimp = db.Column(db.Date)

    # ── Modulo 8: Transporte/Entrega ────────────────────────────────────
    entrega_recebida = db.Column(db.Boolean, default=False)
    entrega_recebida_em = db.Column(db.DateTime)
    entrega_comentario = db.Column(db.Text)
    entrega_divergencias = db.Column(db.Text)

    # ── Modulo 9: NF/Cambio ─────────────────────────────────────────────
    nf_numero = db.Column(db.String(40))
    nf_data_emissao = db.Column(db.Date)
    cambio_valor_final = db.Column(db.Float)
    documento_consolidado_file_name = db.Column(db.String(260))
    documento_consolidado_file_path = db.Column(db.String(500))
    processo_concluido_em = db.Column(db.DateTime)

    # ── Colunas de reserva para variaveis futuras (tipadas, nao um blob) ──
    extra_texto_01 = db.Column(db.String(255))
    extra_texto_02 = db.Column(db.String(255))
    extra_texto_03 = db.Column(db.String(255))
    extra_texto_04 = db.Column(db.String(255))
    extra_texto_05 = db.Column(db.String(255))
    extra_texto_06 = db.Column(db.String(255))
    extra_texto_07 = db.Column(db.String(255))
    extra_texto_08 = db.Column(db.String(255))
    extra_texto_09 = db.Column(db.String(255))
    extra_texto_10 = db.Column(db.String(255))
    extra_texto_11 = db.Column(db.String(255))
    extra_texto_12 = db.Column(db.String(255))
    extra_texto_13 = db.Column(db.String(255))
    extra_texto_14 = db.Column(db.String(255))
    extra_texto_15 = db.Column(db.String(255))
    extra_texto_16 = db.Column(db.String(255))
    extra_texto_17 = db.Column(db.String(255))
    extra_texto_18 = db.Column(db.String(255))
    extra_texto_19 = db.Column(db.String(255))
    extra_texto_20 = db.Column(db.String(255))
    extra_texto_21 = db.Column(db.String(255))
    extra_texto_22 = db.Column(db.String(255))
    extra_texto_23 = db.Column(db.String(255))
    extra_texto_24 = db.Column(db.String(255))
    extra_texto_25 = db.Column(db.String(255))
    extra_texto_26 = db.Column(db.String(255))
    extra_texto_27 = db.Column(db.String(255))
    extra_texto_28 = db.Column(db.String(255))
    extra_texto_29 = db.Column(db.String(255))
    extra_texto_30 = db.Column(db.String(255))
    extra_texto_31 = db.Column(db.String(255))
    extra_texto_32 = db.Column(db.String(255))
    extra_texto_33 = db.Column(db.String(255))
    extra_texto_34 = db.Column(db.String(255))
    extra_texto_35 = db.Column(db.String(255))
    extra_texto_36 = db.Column(db.String(255))
    extra_texto_37 = db.Column(db.String(255))
    extra_texto_38 = db.Column(db.String(255))
    extra_texto_39 = db.Column(db.String(255))
    extra_texto_40 = db.Column(db.String(255))
    extra_texto_41 = db.Column(db.String(255))
    extra_texto_42 = db.Column(db.String(255))
    extra_texto_43 = db.Column(db.String(255))
    extra_texto_44 = db.Column(db.String(255))
    extra_texto_45 = db.Column(db.String(255))
    extra_texto_46 = db.Column(db.String(255))
    extra_texto_47 = db.Column(db.String(255))
    extra_texto_48 = db.Column(db.String(255))
    extra_texto_49 = db.Column(db.String(255))
    extra_texto_50 = db.Column(db.String(255))
    extra_numero_01 = db.Column(db.Float)
    extra_numero_02 = db.Column(db.Float)
    extra_numero_03 = db.Column(db.Float)
    extra_numero_04 = db.Column(db.Float)
    extra_numero_05 = db.Column(db.Float)
    extra_numero_06 = db.Column(db.Float)
    extra_numero_07 = db.Column(db.Float)
    extra_numero_08 = db.Column(db.Float)
    extra_numero_09 = db.Column(db.Float)
    extra_numero_10 = db.Column(db.Float)
    extra_numero_11 = db.Column(db.Float)
    extra_numero_12 = db.Column(db.Float)
    extra_numero_13 = db.Column(db.Float)
    extra_numero_14 = db.Column(db.Float)
    extra_numero_15 = db.Column(db.Float)
    extra_numero_16 = db.Column(db.Float)
    extra_numero_17 = db.Column(db.Float)
    extra_numero_18 = db.Column(db.Float)
    extra_numero_19 = db.Column(db.Float)
    extra_numero_20 = db.Column(db.Float)
    extra_data_01 = db.Column(db.DateTime)
    extra_data_02 = db.Column(db.DateTime)
    extra_data_03 = db.Column(db.DateTime)
    extra_data_04 = db.Column(db.DateTime)
    extra_data_05 = db.Column(db.DateTime)
    extra_data_06 = db.Column(db.DateTime)
    extra_data_07 = db.Column(db.DateTime)
    extra_data_08 = db.Column(db.DateTime)
    extra_data_09 = db.Column(db.DateTime)
    extra_data_10 = db.Column(db.DateTime)
    extra_data_11 = db.Column(db.DateTime)
    extra_data_12 = db.Column(db.DateTime)
    extra_data_13 = db.Column(db.DateTime)
    extra_data_14 = db.Column(db.DateTime)
    extra_data_15 = db.Column(db.DateTime)
    extra_data_16 = db.Column(db.DateTime)
    extra_data_17 = db.Column(db.DateTime)
    extra_data_18 = db.Column(db.DateTime)
    extra_data_19 = db.Column(db.DateTime)
    extra_data_20 = db.Column(db.DateTime)
    extra_flag_01 = db.Column(db.Boolean)
    extra_flag_02 = db.Column(db.Boolean)
    extra_flag_03 = db.Column(db.Boolean)
    extra_flag_04 = db.Column(db.Boolean)
    extra_flag_05 = db.Column(db.Boolean)
    extra_flag_06 = db.Column(db.Boolean)
    extra_flag_07 = db.Column(db.Boolean)
    extra_flag_08 = db.Column(db.Boolean)
    extra_flag_09 = db.Column(db.Boolean)
    extra_flag_10 = db.Column(db.Boolean)

    po_itens = db.relationship(
        "ComexPoItem",
        backref="processo",
        cascade="all, delete-orphan",
        order_by="ComexPoItem.order_index",
    )
    cotacoes = db.relationship(
        "ComexCotacao",
        backref="processo",
        cascade="all, delete-orphan",
        order_by="ComexCotacao.link_gerado_em",
        foreign_keys="ComexCotacao.processo_id",
    )
    follow_ups = db.relationship(
        "ComexFollowUp",
        backref="processo",
        cascade="all, delete-orphan",
    )
    fotos_entrega = db.relationship(
        "ComexEntregaFoto",
        backref="processo",
        cascade="all, delete-orphan",
        order_by="ComexEntregaFoto.id",
    )
    documentos = db.relationship(
        "ComexDocumento",
        backref="processo",
        cascade="all, delete-orphan",
        order_by="ComexDocumento.id.desc()",
    )


class ComexPoItem(db.Model):
    """Item de linha da PO (Modulo 2) - codigo, NCM/HS code, part number,
    descricao, quantidade e valores, no formato do modelo de PO usado hoje
    (Purchase Order em PDF). Preenchido manualmente pelo operador enquanto
    o ERP nao expoe preco unitario/NCM por item via bridge; a ideia e que
    no futuro esses campos venham pre-preenchidos da OC."""

    __tablename__ = "comex_po_item"

    id = db.Column(db.Integer, primary_key=True)
    processo_id = db.Column(db.Integer, db.ForeignKey("comex_processo.id"), nullable=False, index=True)

    order_index = db.Column(db.Integer, nullable=False, default=0)
    codigo = db.Column(db.String(60))            # CODE (codigo interno do produto)
    ncm = db.Column(db.String(20))                # NCM / HS CODE
    pn = db.Column(db.String(80))                 # PN (part number)
    descricao = db.Column(db.String(500))          # DESCRIPTION
    quantidade = db.Column(db.Float)
    valor_unitario = db.Column(db.Float)           # UNIT US$
    valor_total = db.Column(db.Float)               # Line Total USD

    criado_em = db.Column(db.DateTime, default=datetime.now, nullable=False)
    atualizado_em = db.Column(db.DateTime, default=datetime.now, nullable=False)


class ComexCotacao(db.Model):
    """Uma cotacao de frete para um processo (Modulo 3), no formato do
    "modelo de cotação.xlsx" usado hoje pela empresa - dois formularios
    possiveis (`tipo_frete`): FCL (container fechado) ou LCL_AEREO (volumes
    soltos/aereo, mesma estrutura para os dois). Todas as cotacoes recebidas
    ficam registradas aqui, nao so a vencedora - historico completo para
    auditoria. `is_escolhida` marca a selecionada pelo operador (pode ou nao
    ser a `is_sugerida_pelo_sistema`, a de menor custo total)."""

    __tablename__ = "comex_cotacao"

    id = db.Column(db.Integer, primary_key=True)
    processo_id = db.Column(db.Integer, db.ForeignKey("comex_processo.id"), nullable=False, index=True)

    tipo_frete = db.Column(db.String(12), nullable=False)  # FCL | LCL_AEREO
    status = db.Column(db.String(20), nullable=False, default="Pendente")  # Pendente | Recebida

    fornecedor_frete = db.Column(db.String(200))  # preenchido pelo prestador no formulario publico
    origem = db.Column(db.String(120))
    destino = db.Column(db.String(120))
    incoterm = db.Column(db.String(20))  # so LCL_AEREO

    # FCL - equipamento (container)
    qtd_40hc = db.Column(db.Integer)
    qtd_20dry = db.Column(db.Integer)

    # Carga perigosa - ambos os formularios ("NA" quando nao aplicavel)
    imo_classe = db.Column(db.String(20))
    un_numero = db.Column(db.String(20))

    # Valor da mercadoria (USD) - essencial pro prestador de frete calcular o
    # seguro (Ensurance); sugerido automaticamente a partir do subtotal dos
    # itens da PO, mas o operador pode ajustar.
    valor_mercadoria_usd = db.Column(db.Float)

    # LCL_AEREO - logistica (volumes em ComexCotacaoVolume)
    transit_time = db.Column(db.String(60))
    rota = db.Column(db.String(200))
    validade = db.Column(db.Date)
    ptax = db.Column(db.Float)

    # Custos por etapa, em USD e BRL - mesmas 6 linhas nos dois formularios
    pick_up_usd = db.Column(db.Float)
    pick_up_brl = db.Column(db.Float)
    origem_charges_usd = db.Column(db.Float)
    origem_charges_brl = db.Column(db.Float)
    frete_internacional_usd = db.Column(db.Float)
    frete_internacional_brl = db.Column(db.Float)
    seguro_usd = db.Column(db.Float)
    seguro_brl = db.Column(db.Float)
    destination_charges_usd = db.Column(db.Float)
    destination_charges_brl = db.Column(db.Float)
    docs_release_usd = db.Column(db.Float)
    docs_release_brl = db.Column(db.Float)
    delivery_usd = db.Column(db.Float)
    delivery_brl = db.Column(db.Float)
    custo_total_usd = db.Column(db.Float)  # calculado na submissao
    custo_total_brl = db.Column(db.Float)  # calculado na submissao

    # Termos de consentimento (ver COMEX_ESPECIFICACAO.md / modelo de
    # cotação.xlsx linhas 37-49) - aceite obrigatorio para submeter.
    termos_aceitos = db.Column(db.Boolean, default=False)
    termos_aceitos_em = db.Column(db.DateTime)

    is_sugerida_pelo_sistema = db.Column(db.Boolean, default=False)
    is_escolhida = db.Column(db.Boolean, default=False)

    # Texto livre do prestador com as proximas saidas/embarques disponiveis
    # (aceita colar tabela do Excel/planilha - so texto corrido, sem parsing).
    proximas_saidas = db.Column(db.Text)
    # Saida especifica que o operador confirmou com o prestador ao escolher
    # esta cotacao como vencedora - obrigatoria nesse momento (ver
    # comex_service.escolher_cotacao) e enviada no e-mail de selecao.
    saida_escolhida = db.Column(db.Text)

    # Link publico e temporario para o fornecedor preencher a cotacao sem
    # login completo. O token em si nunca e persistido em claro, so o hash
    # (mesmo padrao do convite de usuario em Usuario.convite_token_hash).
    token_publico_hash = db.Column(db.String(64), index=True)
    token_publico_expira_em = db.Column(db.DateTime)
    email_instrucao_embarque = db.Column(db.String(255))  # e-mail do contato do fornecedor de frete

    link_gerado_em = db.Column(db.DateTime, default=datetime.now, nullable=False)
    criado_por = db.Column(db.String(100))
    recebida_em = db.Column(db.DateTime)

    volumes = db.relationship(
        "ComexCotacaoVolume",
        backref="cotacao",
        cascade="all, delete-orphan",
        order_by="ComexCotacaoVolume.numero",
    )


class ComexCotacaoVolume(db.Model):
    """Dimensoes/peso de um volume da cotacao LCL/Aereo (ate 5 no modelo
    original, mas sem limite fixo aqui)."""

    __tablename__ = "comex_cotacao_volume"

    id = db.Column(db.Integer, primary_key=True)
    cotacao_id = db.Column(db.Integer, db.ForeignKey("comex_cotacao.id"), nullable=False, index=True)
    numero = db.Column(db.Integer, nullable=False, default=1)
    comprimento = db.Column(db.Float)
    largura = db.Column(db.Float)
    altura = db.Column(db.Float)
    peso = db.Column(db.Float)


class ComexFollowUp(db.Model):
    """Follow-up de acompanhamento reutilizado pelos modulos 3
    (Instrucao/Documentacao), 4 (Coleta), 5 (Em Transito), 6 (Desembarque) e
    7 (Desembaraco) - mesmo molde de ExpedicaoCobranca/ExpedicaoCobrancaLog:
    uma linha de status por (processo, modulo) + historico append-only de
    comentarios em ComexFollowUpLog."""

    __tablename__ = "comex_follow_up"

    id = db.Column(db.Integer, primary_key=True)
    processo_id = db.Column(db.Integer, db.ForeignKey("comex_processo.id"), nullable=False, index=True)
    # instrucao | coleta | em_transito | desembarque | desembaraco
    modulo = db.Column(db.String(30), nullable=False, index=True)
    status_ok = db.Column(db.Boolean, nullable=False, default=False)

    criado_em = db.Column(db.DateTime, default=datetime.now, nullable=False)
    atualizado_em = db.Column(db.DateTime, default=datetime.now, nullable=False)

    __table_args__ = (
        db.UniqueConstraint("processo_id", "modulo", name="uq_comex_follow_up_processo_modulo"),
    )

    logs = db.relationship(
        "ComexFollowUpLog",
        backref="follow_up",
        cascade="all, delete-orphan",
        order_by="ComexFollowUpLog.criado_em",
    )


class ComexFollowUpLog(db.Model):
    """Historico append-only de comentarios/anexos de um ComexFollowUp -
    nunca sobrescreve, cada acao do operador vira uma linha nova."""

    __tablename__ = "comex_follow_up_log"

    id = db.Column(db.Integer, primary_key=True)
    follow_up_id = db.Column(db.Integer, db.ForeignKey("comex_follow_up.id"), nullable=False, index=True)
    texto = db.Column(db.Text, default="")
    documento_file_name = db.Column(db.String(260))
    documento_file_path = db.Column(db.String(500))
    autor = db.Column(db.String(100), default="")
    criado_em = db.Column(db.DateTime, default=datetime.now, nullable=False, index=True)


class ComexLembrete(db.Model):
    """Lembrete automatico disparado ao concluir uma etapa (ex.: Desembarque
    concluido dispara lembrete de inicio de desembaraco; Desembaraco
    concluido dispara lembrete de entrega)."""

    __tablename__ = "comex_lembrete"

    id = db.Column(db.Integer, primary_key=True)
    processo_id = db.Column(db.Integer, db.ForeignKey("comex_processo.id"), nullable=False, index=True)
    tipo = db.Column(db.String(40), nullable=False)  # inicio_desembaraco | entrega
    destinatario = db.Column(db.String(255))
    enviado_em = db.Column(db.DateTime, default=datetime.now, nullable=False)


class ComexEntregaFoto(db.Model):
    """Fotos de divergencia registradas no Modulo 8 (Transporte/Entrega) -
    mesmo molde de ExpedicaoRomaneioFotoCarregamento (multiplas fotos por
    processo)."""

    __tablename__ = "comex_entrega_foto"

    id = db.Column(db.Integer, primary_key=True)
    processo_id = db.Column(db.Integer, db.ForeignKey("comex_processo.id"), nullable=False, index=True)
    file_name = db.Column(db.String(260), nullable=False)
    file_path = db.Column(db.String(500), nullable=False)
    uploaded_at = db.Column(db.DateTime, default=datetime.now, nullable=False)
    uploaded_by = db.Column(db.String(100))


class ComexFornecedor(db.Model):
    """Cadastro de fornecedores do Comex - compartilhado entre modulos.
    Modulo 2 (PO): qualquer fornecedor ativo pode ser selecionado pra
    pre-preencher o(s) e-mail(s) de envio. Modulo 3 (Cotacao): a selecao
    multipla pro envio em lote filtra so os do tipo "Freight Forwarder"."""

    __tablename__ = "comex_fornecedor"

    id = db.Column(db.Integer, primary_key=True)
    razao_social = db.Column(db.String(200), nullable=False, index=True)
    nome_fantasia = db.Column(db.String(200), index=True)
    cnpj = db.Column(db.String(20), index=True)
    # Um ou mais enderecos separados por ";" - mesmo padrao ja usado em
    # ComexProcesso.po_destinatarios_email.
    email = db.Column(db.String(300))
    telefone = db.Column(db.String(40))
    # Freight Forwarder | Transportador | Prod/Dist (ver TIPOS_FORNECEDOR
    # em comex_fornecedor_service.py)
    tipo_fornecedor = db.Column(db.String(30), nullable=False, index=True)
    ativo = db.Column(db.Boolean, nullable=False, default=True, index=True)
    criado_em = db.Column(db.DateTime, default=datetime.now, nullable=False)
    criado_por = db.Column(db.String(100))
    atualizado_em = db.Column(db.DateTime, default=datetime.now, nullable=False)
    atualizado_por = db.Column(db.String(100))


class ComexDocumento(db.Model):
    """Documento anexado ao processo em QUALQUER modulo do workflow (nota
    fiscal, BL/AWB, invoice, packing list, comprovante etc.) - requisito
    geral do Comex: todo modulo precisa ter uma funcao de anexar documento.
    Mesmo padrao de storage das fotos de expedicao (Google Drive ou disco
    local, conforme EXPEDICAO_FOTOS_STORAGE - ver expedicao_photo_storage.py)."""

    __tablename__ = "comex_documento"

    id = db.Column(db.Integer, primary_key=True)
    processo_id = db.Column(db.Integer, db.ForeignKey("comex_processo.id"), nullable=False, index=True)
    modulo = db.Column(db.String(20), nullable=False, index=True)  # OC | PO | Cotacao | ... (ver MODULOS_SEQUENCIA)
    titulo = db.Column(db.String(200))  # descricao livre opcional (ex.: "Invoice assinada")
    file_name = db.Column(db.String(260), nullable=False)
    file_path = db.Column(db.String(500), nullable=False)
    uploaded_at = db.Column(db.DateTime, default=datetime.now, nullable=False)
    uploaded_by = db.Column(db.String(100))
