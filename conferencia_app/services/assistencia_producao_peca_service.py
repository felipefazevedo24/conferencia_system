"""Servico da Solicitacao de Producao de Pecas (Assistencia Tecnica).

Workflow:
    (EDI) --> Pendente --efetivar--> Em andamento --efetivar--> Em producao
          --efetivar--> Concluido
    estornar: volta uma etapa (engano na efetivacao).
    cancelar: encerra com motivo, de qualquer etapa antes do Concluido.

A solicitacao so' nasce pelo EDI (receber_edi); a tela nao cria nem edita,
so' movimenta o status e anexa documentos. O sistema de origem acompanha
o status (e baixa os documentos) pela consulta do EDI - ver as rotas.
"""
from __future__ import annotations

import base64
import binascii
import hmac
import io
import json
import os
from datetime import date, datetime

from flask import current_app

from ..extensions import db
from ..models import (
    AssistenciaProducaoPeca as Solicitacao,
    AssistenciaProducaoPecaAnexo as Anexo,
    AssistenciaProducaoPecaAnexoParte as AnexoParte,
    AssistenciaProducaoPecaHistorico as Historico,
)
from ..tempo import agora_br

FLUXO = (
    Solicitacao.STATUS_PENDENTE,
    Solicitacao.STATUS_EM_ANDAMENTO,
    Solicitacao.STATUS_EM_PRODUCAO,
    Solicitacao.STATUS_CONCLUIDO,
)
STATUS_FINAIS = (Solicitacao.STATUS_CONCLUIDO, Solicitacao.STATUS_CANCELADO)

USUARIO_EDI = "EDI"
MAX_IMAGEM_BYTES = 8 * 1024 * 1024

# O tipo sai dos bytes (assinatura do arquivo), nao do que o sistema de
# origem declarou - a imagem e' servida de volta no navegador.
_ASSINATURAS_IMAGEM = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
)


def _txt(valor) -> str:
    return str(valor if valor is not None else "").strip()


# ── Token do EDI ─────────────────────────────────────────────────────────
def token_edi_configurado() -> str:
    """Token que o sistema de origem manda no header. Fica fora do git, no
    mesmo esquema dos webhooks do Teams: variavel de ambiente ou
    instance/edi_config.json ({"assistencia_producao_pecas_token": "..."})."""
    token = _txt(os.environ.get("ASSISTENCIA_PRODUCAO_EDI_TOKEN"))
    if token:
        return token
    try:
        caminho = os.path.join(current_app.instance_path, "edi_config.json")
        if os.path.isfile(caminho):
            with open(caminho, encoding="utf-8") as fh:
                return _txt((json.load(fh) or {}).get("assistencia_producao_pecas_token"))
    except Exception:
        current_app.logger.warning("EDI producao de pecas: falha ao ler instance/edi_config.json", exc_info=True)
    return ""


def token_valido(informado: str) -> bool:
    esperado = token_edi_configurado()
    # Sem token configurado o endpoint fica fechado - e' publico e cria registro.
    if not esperado or not informado:
        return False
    return hmac.compare_digest(informado.encode("utf-8"), esperado.encode("utf-8"))


# ── Recebimento (EDI) ────────────────────────────────────────────────────
def _data_prazo(valor) -> date:
    texto = _txt(valor)
    for formato in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(texto[:10], formato).date()
        except ValueError:
            continue
    raise ValueError("Prazo inválido - use AAAA-MM-DD ou DD/MM/AAAA.")


def _imagem(dados: dict) -> tuple[bytes | None, str | None, str | None]:
    bruto = _txt(dados.get("imagem_base64"))
    if not bruto:
        return None, None, None
    # Aceita tambem data URL ("data:image/png;base64,....").
    if bruto.startswith("data:") and "," in bruto:
        bruto = bruto.split(",", 1)[1]
    try:
        conteudo = base64.b64decode(bruto, validate=False)
    except (binascii.Error, ValueError):
        raise ValueError("Imagem inválida - envie o arquivo em base64.")
    if not conteudo:
        raise ValueError("Imagem inválida - envie o arquivo em base64.")
    if len(conteudo) > MAX_IMAGEM_BYTES:
        raise ValueError("Imagem muito grande (máximo 8 MB).")
    content_type = next((ct for assinatura, ct in _ASSINATURAS_IMAGEM if conteudo.startswith(assinatura)), None)
    if content_type is None and conteudo[:4] == b"RIFF" and conteudo[8:12] == b"WEBP":
        content_type = "image/webp"
    if content_type is None:
        raise ValueError("Formato de imagem não suportado - use PNG, JPG ou WEBP.")
    nome = _txt(dados.get("imagem_nome"))[:260] or "imagem"
    return conteudo, nome, content_type


def _consultar_erp(solicitacao: Solicitacao) -> None:
    """Complementa com o ERP. Nunca impede o recebimento: se o ERP cair, a
    solicitacao entra do mesmo jeito e a tela avisa que nao foi verificada."""
    from .solicitacao_nf_service import _buscar_cliente_por_codigo, _buscar_material_por_codigo

    try:
        material = _buscar_material_por_codigo(solicitacao.codigo)
        cliente = _buscar_cliente_por_codigo(solicitacao.cliente_codigo) if solicitacao.cliente_codigo else None
    except Exception:
        current_app.logger.warning(
            "EDI producao de pecas: ERP indisponivel ao verificar o codigo %s", solicitacao.codigo, exc_info=True
        )
        return
    solicitacao.descricao_erp = _txt((material or {}).get("nome"))[:300] or None
    solicitacao.cliente_nome_erp = _txt((cliente or {}).get("nome"))[:200] or None
    solicitacao.erp_verificado_em = agora_br()


_OBRIGATORIOS = (
    ("cliente", "Cliente"),
    ("maquina", "Máquina"),
    ("codigo", "Código"),
    ("descricao", "Descrição"),
    ("motivo", "Motivo"),
    ("prazo", "Prazo"),
    ("solicitante", "Solicitante"),
)


def receber_edi(dados: dict, arquivos=None) -> tuple[Solicitacao, bool]:
    """Cria a solicitacao a partir do EDI. Devolve (solicitacao, criada) -
    criada=False quando o `id_externo` ja existia (reenvio do sistema de
    origem): devolve a existente sem duplicar (e sem mexer nos anexos - pra
    acrescentar documento depois existe a rota de anexos).

    `arquivos`: [(nome, stream)] do multipart; o JSON tambem aceita `anexos`
    em base64. Anexo invalido recusa a solicitacao inteira."""
    if not isinstance(dados, dict):
        raise ValueError("Envie os campos em JSON.")

    id_externo = _txt(dados.get("id_externo"))[:100] or None
    if id_externo:
        existente = Solicitacao.query.filter_by(id_externo=id_externo).first()
        if existente:
            return existente, False

    faltando = [rotulo for campo, rotulo in _OBRIGATORIOS if not _txt(dados.get(campo))]
    if faltando:
        raise ValueError("Campos obrigatórios não informados: " + ", ".join(faltando) + ".")

    prazo = _data_prazo(dados.get("prazo"))
    imagem, imagem_nome, imagem_content_type = _imagem(dados)

    solicitacao = Solicitacao(
        id_externo=id_externo,
        cliente=_txt(dados.get("cliente"))[:200],
        cliente_codigo=_txt(dados.get("cliente_codigo"))[:40] or None,
        maquina=_txt(dados.get("maquina"))[:200],
        codigo=_txt(dados.get("codigo"))[:60],
        descricao=_txt(dados.get("descricao"))[:300],
        motivo=_txt(dados.get("motivo"))[:300],
        comentario=_txt(dados.get("comentario")) or None,
        prazo=prazo,
        solicitante=_txt(dados.get("solicitante"))[:120],
        imagem=imagem,
        imagem_nome=imagem_nome,
        imagem_content_type=imagem_content_type,
        status=Solicitacao.STATUS_PENDENTE,
        recebido_em=agora_br(),
    )
    arquivos = list(arquivos or []) + anexos_base64(dados)
    _consultar_erp(solicitacao)
    solicitacao.historico.append(Historico(
        acao="recebido", status_de=None, status_para=Solicitacao.STATUS_PENDENTE, usuario=USUARIO_EDI,
    ))
    db.session.add(solicitacao)
    try:
        _gravar_anexos(solicitacao, arquivos, Anexo.ORIGEM_EDI, USUARIO_EDI)
    except ValueError:
        db.session.rollback()
        raise
    db.session.commit()
    return solicitacao, True


# ── Movimentacao de status ───────────────────────────────────────────────
def proximo_status(status: str) -> str | None:
    if status not in FLUXO:
        return None
    idx = FLUXO.index(status)
    return FLUXO[idx + 1] if idx + 1 < len(FLUXO) else None


def status_anterior(status: str) -> str | None:
    if status not in FLUXO:
        return None
    idx = FLUXO.index(status)
    return FLUXO[idx - 1] if idx > 0 else None


def _mover(solicitacao: Solicitacao, para: str, acao: str, usuario: str, motivo: str | None = None) -> Solicitacao:
    agora = agora_br()
    solicitacao.historico.append(Historico(
        acao=acao, status_de=solicitacao.status, status_para=para,
        motivo=(motivo or None), usuario=usuario, criado_em=agora,
    ))
    solicitacao.status = para
    solicitacao.atualizado_em = agora
    solicitacao.atualizado_por = usuario
    db.session.commit()
    return solicitacao


def efetivar(solicitacao: Solicitacao, usuario: str, status_esperado: str | None = None) -> Solicitacao:
    """Avanca uma etapa. `status_esperado` e' o status que a tela mostrava:
    se outra pessoa ja efetivou, nao pula duas etapas com um clique so'."""
    if status_esperado and status_esperado != solicitacao.status:
        raise ValueError(
            f"A solicitação mudou para \"{solicitacao.status}\" enquanto você olhava - atualize a tela."
        )
    proximo = proximo_status(solicitacao.status)
    if proximo is None:
        raise ValueError(f"Uma solicitação \"{solicitacao.status}\" não tem próxima etapa.")
    return _mover(solicitacao, proximo, "efetivado", usuario)


def estornar(solicitacao: Solicitacao, usuario: str, motivo: str | None = None) -> Solicitacao:
    anterior = status_anterior(solicitacao.status)
    if anterior is None:
        raise ValueError(f"Uma solicitação \"{solicitacao.status}\" não pode ser estornada.")
    return _mover(solicitacao, anterior, "estornado", usuario, _txt(motivo)[:500])


def cancelar(solicitacao: Solicitacao, usuario: str, motivo: str) -> Solicitacao:
    motivo = _txt(motivo)[:500]
    if not motivo:
        raise ValueError("Informe o motivo do cancelamento.")
    if solicitacao.status in STATUS_FINAIS:
        raise ValueError(f"Uma solicitação \"{solicitacao.status}\" não pode ser cancelada.")
    solicitacao.motivo_cancelamento = motivo
    return _mover(solicitacao, Solicitacao.STATUS_CANCELADO, "cancelado", usuario, motivo)


# ── Consulta ─────────────────────────────────────────────────────────────
def obter(solicitacao_id: int) -> Solicitacao | None:
    return db.session.get(Solicitacao, solicitacao_id)


def listar() -> list[Solicitacao]:
    return Solicitacao.query.order_by(Solicitacao.recebido_em.desc(), Solicitacao.id.desc()).all()


def protocolo(solicitacao: Solicitacao) -> str:
    return f"PP-{solicitacao.id:05d}"


def situacao_erp(solicitacao: Solicitacao) -> str:
    """ok / nao_encontrado / nao_verificado - so' pra tela avisar."""
    if not solicitacao.erp_verificado_em:
        return "nao_verificado"
    return "ok" if solicitacao.descricao_erp else "nao_encontrado"


def atrasada(solicitacao: Solicitacao, hoje: date | None = None) -> bool:
    if solicitacao.status in STATUS_FINAIS or not solicitacao.prazo:
        return False
    return solicitacao.prazo < (hoje or agora_br().date())


def obter_por_referencia(id_externo=None, protocolo_txt=None) -> Solicitacao | None:
    """O sistema de origem acha a solicitacao pelo proprio id_externo ou pelo
    protocolo que recebeu na criacao (PP-00012)."""
    id_externo = _txt(id_externo)
    if id_externo:
        return Solicitacao.query.filter_by(id_externo=id_externo[:100]).first()
    numero = _txt(protocolo_txt).upper().removeprefix("PP-")
    return db.session.get(Solicitacao, int(numero)) if numero.isdigit() else None


def alteradas_desde(desde) -> list[Solicitacao]:
    """Pro sistema de origem replicar so' o que mudou: recebidas ou
    movimentadas (status ou anexo) a partir de `desde` (horario de Brasilia)."""
    try:
        momento = datetime.fromisoformat(_txt(desde).replace("Z", "")).replace(tzinfo=None)
    except ValueError:
        raise ValueError("Parâmetro desde inválido - use AAAA-MM-DD ou AAAA-MM-DDTHH:MM:SS.")
    return (
        Solicitacao.query
        .filter(db.or_(Solicitacao.recebido_em >= momento, Solicitacao.atualizado_em >= momento))
        .order_by(Solicitacao.id)
        .all()
    )


# ── Anexos ───────────────────────────────────────────────────────────────
MAX_ANEXOS_BYTES = 100 * 1024 * 1024  # somando os anexos da solicitacao
PARTE_BYTES = 2 * 1024 * 1024
# Pedido de 13/11/2026: PDF, imagens, CAD e Office/ZIP.
EXTENSOES_ANEXO = {
    "pdf": "application/pdf",
    "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "webp": "image/webp",
    "dwg": "application/acad", "dxf": "application/dxf",
    "step": "application/step", "stp": "application/step", "igs": "application/iges", "iges": "application/iges",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "xls": "application/vnd.ms-excel",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "doc": "application/msword",
    "zip": "application/zip",
}
# PDF e imagem abrem no navegador: o conteudo tem que ser mesmo o que a
# extensao diz (senao um HTML renomeado pra .pdf rodaria no dominio do Sync).
# O resto so' baixa como arquivo.
_ASSINATURA_ANEXO = {
    "pdf": b"%PDF",
    "png": b"\x89PNG\r\n\x1a\n",
    "jpg": b"\xff\xd8\xff",
    "jpeg": b"\xff\xd8\xff",
    "webp": b"RIFF",
}
ABRE_NO_NAVEGADOR = frozenset(_ASSINATURA_ANEXO)


def _mb(n: int) -> str:
    return f"{n / (1024 * 1024):.1f}".replace(".", ",") + " MB"


def extensao(nome: str) -> str:
    return nome.rsplit(".", 1)[-1].lower() if "." in nome else ""


def usado_bytes(solicitacao: Solicitacao) -> int:
    return sum(a.tamanho or 0 for a in solicitacao.anexos)


def anexos_base64(dados: dict) -> list:
    """`anexos: [{"nome": "...", "conteudo_base64": "..."}]` no JSON do EDI."""
    lista = dados.get("anexos") if isinstance(dados, dict) else None
    if not lista:
        return []
    if not isinstance(lista, list):
        raise ValueError("anexos deve ser uma lista de {nome, conteudo_base64}.")
    arquivos = []
    for i, item in enumerate(lista, 1):
        item = item if isinstance(item, dict) else {}
        nome = _txt(item.get("nome"))
        bruto = _txt(item.get("conteudo_base64"))
        if bruto.startswith("data:") and "," in bruto:
            bruto = bruto.split(",", 1)[1]
        try:
            conteudo = base64.b64decode(bruto, validate=False) if bruto else b""
        except (binascii.Error, ValueError):
            raise ValueError(f"Anexo {i} ({nome or 'sem nome'}): conteúdo base64 inválido.")
        arquivos.append((nome, io.BytesIO(conteudo)))
    return arquivos


def _gravar_anexos(solicitacao: Solicitacao, arquivos, origem: str, usuario: str) -> list[Anexo]:
    """Grava em partes, lendo o arquivo aos pedacos (nunca 100 MB inteiros
    na sessao). Nao faz commit: quem chama confirma ou desfaz."""
    gravados = []
    if not arquivos:
        return gravados
    usado_antes = usado_bytes(solicitacao)
    usado = usado_antes
    for nome, stream in arquivos:
        nome = os.path.basename(_txt(nome).replace("\\", "/"))[:260]
        ext = extensao(nome)
        if not nome or ext not in EXTENSOES_ANEXO:
            raise ValueError(
                f'Arquivo "{nome or "sem nome"}" não aceito - envie PDF, imagem (PNG/JPG/WEBP), '
                "CAD (DWG/DXF/STEP/IGES), Excel, Word ou ZIP."
            )
        anexo = Anexo(nome=nome, content_type=EXTENSOES_ANEXO[ext], tamanho=0, origem=origem,
                      enviado_por=usuario, enviado_em=agora_br())
        solicitacao.anexos.append(anexo)
        db.session.flush()
        ordem = 0
        while True:
            pedaco = stream.read(PARTE_BYTES)
            if not pedaco:
                break
            if ordem == 0 and ext in _ASSINATURA_ANEXO and not pedaco.startswith(_ASSINATURA_ANEXO[ext]):
                raise ValueError(f'"{nome}" não é um arquivo .{ext} válido.')
            usado += len(pedaco)
            if usado > MAX_ANEXOS_BYTES:
                raise ValueError(
                    f"Limite de {_mb(MAX_ANEXOS_BYTES)} de anexos por solicitação ultrapassado "
                    f"(a solicitação já tinha {_mb(usado_antes)})."
                )
            parte = AnexoParte(anexo_id=anexo.id, ordem=ordem, dados=pedaco)
            db.session.add(parte)
            db.session.flush()
            db.session.expunge(parte)  # nao acumula os bytes na sessao
            anexo.tamanho += len(pedaco)
            ordem += 1
        if not anexo.tamanho:
            raise ValueError(f'"{nome}" está vazio.')
        gravados.append(anexo)
    solicitacao.historico.append(Historico(
        acao="anexado", status_de=solicitacao.status, status_para=solicitacao.status,
        motivo=", ".join(a.nome for a in gravados)[:500], usuario=usuario, criado_em=agora_br(),
    ))
    return gravados


def anexar(solicitacao: Solicitacao, arquivos, origem: str, usuario: str) -> list[Anexo]:
    """Acrescenta documentos a uma solicitacao que ja existe (EDI ou tela).
    Marca atualizado_em pra consulta `desde` do sistema de origem enxergar."""
    if not arquivos:
        raise ValueError("Nenhum arquivo enviado.")
    try:
        gravados = _gravar_anexos(solicitacao, arquivos, origem, usuario)
    except ValueError:
        db.session.rollback()
        raise
    solicitacao.atualizado_em = agora_br()
    solicitacao.atualizado_por = usuario
    db.session.commit()
    return gravados


def remover_anexo(solicitacao: Solicitacao, anexo: Anexo, usuario: str) -> None:
    """A equipe so' remove o que ela mesma anexou: o que veio do sistema de
    origem e' documento dele."""
    if anexo.origem != Anexo.ORIGEM_SYNC:
        raise ValueError("Anexo enviado pelo sistema de origem não pode ser removido pelo Sync.")
    AnexoParte.query.filter_by(anexo_id=anexo.id).delete(synchronize_session=False)
    agora = agora_br()
    solicitacao.historico.append(Historico(
        acao="anexo removido", status_de=solicitacao.status, status_para=solicitacao.status,
        motivo=anexo.nome[:500], usuario=usuario, criado_em=agora,
    ))
    solicitacao.anexos.remove(anexo)
    solicitacao.atualizado_em = agora
    solicitacao.atualizado_por = usuario
    db.session.commit()


def obter_anexo(solicitacao_id: int | None, anexo_id: int) -> Anexo | None:
    anexo = db.session.get(Anexo, anexo_id)
    if anexo is None or (solicitacao_id is not None and anexo.solicitacao_id != solicitacao_id):
        return None
    return anexo


def conteudo_anexo(anexo_id: int):
    """Gera o arquivo parte a parte (uma consulta por parte) pra nao montar
    100 MB na memoria no download."""
    ids = [i for (i,) in db.session.query(AnexoParte.id).filter_by(anexo_id=anexo_id).order_by(AnexoParte.ordem)]
    for parte_id in ids:
        yield db.session.query(AnexoParte.dados).filter_by(id=parte_id).scalar()
