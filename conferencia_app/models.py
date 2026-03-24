from datetime import datetime

from .extensions import db


class Usuario(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False, index=True)
    password = db.Column(db.String(120), nullable=False)
    role = db.Column(db.String(20), default="Conferente")


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


class BoletoContaReceber(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    numero_nota = db.Column(db.String(20), nullable=False, unique=True, index=True)
    chave_acesso = db.Column(db.String(44), index=True)
    banco = db.Column(db.String(80), nullable=False, default="BOFA - Bank of America")
    valor = db.Column(db.Float, nullable=False, default=0.0)
    nosso_numero = db.Column(db.String(40), nullable=False, unique=True, index=True)
    linha_digitavel = db.Column(db.String(120), nullable=False)
    codigo_barras = db.Column(db.String(120), nullable=False)
    status = db.Column(db.String(20), nullable=False, default="Gerado", index=True)
    usuario_geracao = db.Column(db.String(100), nullable=False)
    data_geracao = db.Column(db.DateTime, default=datetime.now, nullable=False)


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


class DepositoWMS(db.Model):
    """Depósitos fixos para armazenagem: DEP 01, 02, 03, CLIENTE, TERCEIROS"""
    id = db.Column(db.Integer, primary_key=True)
    codigo = db.Column(db.String(30), unique=True, nullable=False, index=True)  # Ex: DEP_01, DEP_02, DEP_03, CLIENTE, TERCEIROS
    nome = db.Column(db.String(100), nullable=False)  # Ex: "DEP 01 - Almoxarifado"
    descricao = db.Column(db.String(300))
    ativo = db.Column(db.Boolean, nullable=False, default=True)
    data_criacao = db.Column(db.DateTime, default=datetime.now, nullable=False)
