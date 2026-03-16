from datetime import datetime

from .extensions import db


class Usuario(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False, index=True)
    password = db.Column(db.String(120), nullable=False)
    role = db.Column(db.String(20), default="Conferente")


class ItemNota(db.Model):
    id = db.Column(db.Integer, primary_key=True)
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
