"""Servico da Solicitacao de Producao de Pecas (Assistencia Tecnica).

Workflow:
    (EDI) --> Pendente --efetivar--> Em andamento --efetivar--> Em producao
          --efetivar--> Concluido
    estornar: volta uma etapa (engano na efetivacao).
    cancelar: encerra com motivo, de qualquer etapa antes do Concluido.

A solicitacao so' nasce pelo EDI (receber_edi); a tela nao cria nem edita,
so' movimenta o status.
"""
from __future__ import annotations

import base64
import binascii
import hmac
import json
import os
from datetime import date, datetime

from flask import current_app

from ..extensions import db
from ..models import (
    AssistenciaProducaoPeca as Solicitacao,
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


def receber_edi(dados: dict) -> tuple[Solicitacao, bool]:
    """Cria a solicitacao a partir do EDI. Devolve (solicitacao, criada) -
    criada=False quando o `id_externo` ja existia (reenvio do sistema de
    origem): devolve a existente sem duplicar."""
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
    _consultar_erp(solicitacao)
    solicitacao.historico.append(Historico(
        acao="recebido", status_de=None, status_para=Solicitacao.STATUS_PENDENTE, usuario=USUARIO_EDI,
    ))
    db.session.add(solicitacao)
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
