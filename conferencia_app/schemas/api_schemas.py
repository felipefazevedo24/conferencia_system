from marshmallow import Schema, fields, validate


class LoginSchema(Schema):
    username = fields.Str(required=True, validate=validate.Length(min=1, max=80))
    password = fields.Str(load_default="", validate=validate.Length(max=120))


class RegisterSchema(Schema):
    username = fields.Str(required=True, validate=validate.Length(min=3, max=80))
    email = fields.Email(required=True, validate=validate.Length(min=5, max=160))
    role = fields.Str(required=True, validate=validate.OneOf(["Admin", "Fiscal", "Logística", "Logistica", "Comex", "Portaria", "Financeiro", "Controladoria", "Compras", "Motorista", "Solicitante", "Qualidade"]))
    ativo = fields.Bool(required=False, load_default=True)


class ConsysteDownloadSchema(Schema):
    modelo = fields.Str(required=False, load_default="nfe", validate=validate.OneOf(["nfe", "nfse"]))
    chave = fields.Str(required=False, load_default="", validate=validate.Length(max=80))
    documento_id = fields.Str(required=False, load_default="", validate=validate.Length(max=120))


class ConsysteEmissaoSolicitarSchema(Schema):
    cnpj = fields.Str(required=True, validate=validate.Length(min=14, max=18))
    txt_payload = fields.Str(required=True, validate=validate.Length(min=20))
    ambiente = fields.Int(required=False, load_default=2, validate=validate.OneOf([1, 2]))


class ConsysteEmissaoConsultarSchema(Schema):
    emissao_id = fields.Str(required=True, validate=validate.Length(min=8, max=80))
    ambiente = fields.Int(required=False, load_default=2, validate=validate.OneOf([1, 2]))


class ValidarSchema(Schema):
    nota = fields.Raw(required=True)
    contagens = fields.Dict(required=True)
    motivos_itens = fields.Dict(required=False, load_default={})
    motivos_tipos = fields.Dict(required=False, load_default={})
    motivos_observacoes = fields.Dict(required=False, load_default={})
    destinos_itens = fields.Dict(required=False, load_default={})
    evidencias_itens = fields.Dict(required=False, load_default={})
    conversoes_itens = fields.Dict(required=False, load_default={})
    chapas_itens = fields.Dict(required=False, load_default={})
    checklist = fields.Dict(required=False, load_default={})
    forcar_pendencia = fields.Bool(required=False, load_default=False)


class DevolverMaterialSchema(Schema):
    nota = fields.Raw(required=True)
    motivo = fields.Str(required=True, validate=validate.Length(min=1, max=500))


class AprovarSolicitacaoDevolucaoSchema(Schema):
    solicitacao_id = fields.Int(required=True, validate=validate.Range(min=1))
    observacao_admin = fields.Str(required=False, load_default="", validate=validate.Length(max=500))


class ResetNotaSchema(Schema):
    nota = fields.Raw(required=True)
    motivo = fields.Str(required=True, validate=validate.Length(min=3, max=500))


class CodigoMaterialItemSchema(Schema):
    item_id = fields.Int(required=True, validate=validate.Range(min=1))
    codigo_material = fields.Str(required=True, validate=validate.Length(min=1, max=50))


class ConfirmarLancamentoSchema(Schema):
    nota = fields.Raw(required=True)
    codigo = fields.Str(required=True, validate=validate.Length(min=1, max=80))
    codigo_material = fields.Str(required=False, load_default="", validate=validate.Length(max=50))
    codigos_materiais = fields.List(fields.Nested(CodigoMaterialItemSchema), required=False, load_default=[])
    manifestar_destinatario = fields.Bool(required=False, load_default=True)
    idempotency_key = fields.Str(required=False, load_default="", validate=validate.Length(max=120))
    cnpj_emitente = fields.Str(required=False, load_default="", validate=validate.Length(max=20))
    fornecedor = fields.Str(required=False, load_default="", validate=validate.Length(max=160))


class ManifestarDestinatarioSchema(Schema):
    nota = fields.Raw(required=True)
    idempotency_key = fields.Str(required=False, load_default="", validate=validate.Length(max=120))
    cnpj_emitente = fields.Str(required=False, load_default="", validate=validate.Length(max=20))
    fornecedor = fields.Str(required=False, load_default="", validate=validate.Length(max=160))


class EstornoLancamentoSchema(Schema):
    nota = fields.Raw(required=True)
    motivo = fields.Str(required=True, validate=validate.Length(min=3, max=500))
    motivo_padrao = fields.Str(required=False, load_default="", validate=validate.Length(max=120))
    complemento = fields.Str(required=False, load_default="", validate=validate.Length(max=300))
    cnpj_emitente = fields.Str(required=False, load_default="", validate=validate.Length(max=20))
    fornecedor = fields.Str(required=False, load_default="", validate=validate.Length(max=160))


class NotaSchema(Schema):
    nota = fields.Raw(required=True)
    cnpj_emitente = fields.Str(required=False, load_default="", validate=validate.Length(max=20))
    fornecedor = fields.Str(required=False, load_default="", validate=validate.Length(max=160))


class ExcluirNotaPendenteSchema(Schema):
    nota = fields.Raw(required=True)
    confirmacao_nota = fields.Raw(required=True)
    motivo = fields.Str(required=True, validate=validate.Length(min=5, max=500))
    cnpj_emitente = fields.Str(required=False, load_default="", validate=validate.Length(max=20))
    fornecedor = fields.Str(required=False, load_default="", validate=validate.Length(max=160))
