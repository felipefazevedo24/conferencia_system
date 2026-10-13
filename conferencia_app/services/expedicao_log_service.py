"""Registro de trilha de auditoria das conferencias de expedicao (FAT e ST).

Centraliza a criacao de entradas em ExpedicaoConferenciaLog para que as duas
abas (Ordens de faturamento e Servico de terceiro) gravem o log de forma
identica: o que mudou no cabecalho e nos itens, quem fez e quando.
"""

import json
from datetime import datetime

from flask import has_request_context, session

from ..auth import is_admin_session
from ..extensions import db
from ..models import ExpedicaoConferenciaLog
from ..tempo import agora_br


# Campos de cabecalho auditados (rotulo amigavel -> atributo).
_CAMPOS_CABECALHO = [
    ("operacao_tipo", "Tipo de operação"),
    ("peso_liquido", "Peso líquido"),
    ("peso_bruto", "Peso bruto"),
    ("qtde_volumes", "Qtde volumes"),
    ("especie_volumes", "Espécie volumes"),
]


def _norm(valor) -> str:
    return str(valor if valor is not None else "").strip()


def montar_diff_cabecalho(antes: dict, depois: dict) -> list:
    """Retorna a lista de alteracoes de cabecalho [{campo,label,de,para}]."""
    mudancas = []
    for attr, label in _CAMPOS_CABECALHO:
        de = _norm(antes.get(attr))
        para = _norm(depois.get(attr))
        if de != para:
            mudancas.append({"campo": attr, "label": label, "de": de, "para": para})
    return mudancas


def registrar_log(
    *,
    origem: str,
    ordem_id: int,
    cod_ordem,
    acao: str,
    usuario: str,
    status_anterior: str,
    status_novo: str,
    divergente: bool,
    pos_faturamento: bool,
    diff_cabecalho: list,
    diff_itens: list,
) -> ExpedicaoConferenciaLog:
    """Cria (sem commit) a entrada de log da conferencia/edicao."""
    detalhes = {
        "cabecalho": diff_cabecalho,
        "itens": diff_itens,
    }
    log = ExpedicaoConferenciaLog(
        origem=origem,
        ordem_id=ordem_id,
        cod_ordem=str(cod_ordem),
        acao=acao,
        usuario=usuario,
        status_anterior=status_anterior,
        status_novo=status_novo,
        divergente=bool(divergente),
        pos_faturamento=bool(pos_faturamento),
        detalhes=json.dumps(detalhes, ensure_ascii=False),
        created_at=agora_br(),
    )
    db.session.add(log)
    db.session.flush()
    log.codigo_interno = f"CNF-{log.id:06d}"
    return log


USUARIO_SISTEMA = "SISTEMA"

# Acoes cujo log revela a quantidade PREVISTA. O historico da ordem aparece
# na tela do conferente, entao estas so' sao listadas para Admin.
ACOES_SO_ADMIN = {"previsto_alterado"}


def usuario_da_sessao() -> str:
    """Quem esta' logado. Fora de requisicao (scheduler da sincronizacao) nao
    existe sessao: o evento fica em nome do sistema."""
    if has_request_context():
        return session.get("username") or USUARIO_SISTEMA
    return USUARIO_SISTEMA


def registrar_evento(
    *,
    origem: str,
    ordem,
    cod_ordem,
    acao: str,
    usuario: str,
    status_anterior: str,
    diff_cabecalho: list | None = None,
    diff_itens: list | None = None,
    divergente: bool | None = None,
) -> ExpedicaoConferenciaLog:
    """Log de um evento que NAO e' a conferencia em si (progresso salvo, fotos,
    faturamento, romaneio, expedicao): tudo que grava na ordem precisa deixar
    rastro de quem fez e quando, mesmo sem mudar status."""
    return registrar_log(
        origem=origem,
        ordem_id=ordem.id,
        cod_ordem=cod_ordem,
        acao=acao,
        usuario=usuario,
        status_anterior=status_anterior,
        status_novo=ordem.status,
        divergente=bool(ordem.divergente) if divergente is None else divergente,
        pos_faturamento=bool(ordem.conferido_pos_faturamento),
        diff_cabecalho=diff_cabecalho or [],
        diff_itens=diff_itens or [],
    )


def mudanca(campo: str, label: str, de, para) -> dict:
    """Entrada de cabecalho para campos fora de _CAMPOS_CABECALHO (NF, fotos...)."""
    return {"campo": campo, "label": label, "de": _norm(de), "para": _norm(para)}


def listar_logs(origem: str, ordem_id: int) -> list:
    """Retorna o historico (mais recente primeiro) serializavel de uma ordem."""
    logs = (
        ExpedicaoConferenciaLog.query
        .filter_by(origem=origem, ordem_id=ordem_id)
        .order_by(ExpedicaoConferenciaLog.created_at.desc(), ExpedicaoConferenciaLog.id.desc())
        .all()
    )
    ve_tudo = has_request_context() and is_admin_session()
    resultado = []
    for lg in logs:
        if lg.acao in ACOES_SO_ADMIN and not ve_tudo:
            continue
        try:
            detalhes = json.loads(lg.detalhes) if lg.detalhes else {}
        except (ValueError, TypeError):
            detalhes = {}
        resultado.append({
            "id": lg.id,
            "codigo_interno": lg.codigo_interno,
            "acao": lg.acao,
            "usuario": lg.usuario,
            "status_anterior": lg.status_anterior,
            "status_novo": lg.status_novo,
            "divergente": bool(lg.divergente),
            "pos_faturamento": bool(lg.pos_faturamento),
            "cabecalho": detalhes.get("cabecalho", []),
            "itens": detalhes.get("itens", []),
            "created_at": lg.created_at.isoformat() if lg.created_at else None,
        })
    return resultado
