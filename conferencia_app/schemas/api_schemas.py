from marshmallow import Schema, fields, validate


class LoginSchema(Schema):
    username = fields.Str(required=True, validate=validate.Length(min=1, max=80))
    password = fields.Str(required=True, validate=validate.Length(min=1, max=120))


class RegisterSchema(Schema):
    username = fields.Str(required=True, validate=validate.Length(min=3, max=80))
    password = fields.Str(required=True, validate=validate.Length(min=4, max=120))
    role = fields.Str(required=True, validate=validate.OneOf(["Admin", "Fiscal", "Conferente", "Portaria"]))


class ConsysteDownloadSchema(Schema):
    chave = fields.Str(required=True, validate=validate.Length(min=44, max=60))


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


class ConfirmarLancamentoSchema(Schema):
    nota = fields.Raw(required=True)
    codigo = fields.Str(required=True, validate=validate.Length(min=1, max=80))
    codigo_material = fields.Str(required=False, load_default="", validate=validate.Length(max=50))
    manifestar_destinatario = fields.Bool(required=False, load_default=True)


class ManifestarDestinatarioSchema(Schema):
    nota = fields.Raw(required=True)


class EstornoLancamentoSchema(Schema):
    nota = fields.Raw(required=True)
    motivo = fields.Str(required=True, validate=validate.Length(min=3, max=500))


class NotaSchema(Schema):
    nota = fields.Raw(required=True)


class ExcluirNotaPendenteSchema(Schema):
    nota = fields.Raw(required=True)
    confirmacao_nota = fields.Raw(required=True)
    motivo = fields.Str(required=True, validate=validate.Length(min=5, max=500))
