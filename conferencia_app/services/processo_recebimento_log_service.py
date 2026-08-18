import json
from datetime import datetime

from ..extensions import db
from ..models import ProcessoRecebimentoEvento


def registrar_evento(
    *,
    numero_nota,
    categoria,
    acao,
    descricao,
    usuario,
    cnpj_emitente=None,
    fornecedor=None,
    dados=None,
    ip_address=None,
    user_agent=None,
):
    evento = ProcessoRecebimentoEvento(
        numero_nota=str(numero_nota).strip(),
        cnpj_emitente=str(cnpj_emitente or "").strip() or None,
        fornecedor=str(fornecedor or "").strip() or None,
        categoria=str(categoria).strip(),
        acao=str(acao).strip(),
        descricao=str(descricao).strip(),
        usuario=str(usuario or "desconhecido").strip(),
        dados_json=json.dumps(dados or {}, ensure_ascii=False, default=str),
        ip_address=str(ip_address or "").split(",", 1)[0].strip()[:64] or None,
        user_agent=str(user_agent or "").strip()[:400] or None,
        created_at=datetime.now(),
    )
    db.session.add(evento)
    return evento


def listar_eventos(numero_nota, *, cnpj_emitente=None, fornecedor=None):
    query = ProcessoRecebimentoEvento.query.filter_by(numero_nota=str(numero_nota).strip())
    cnpj = str(cnpj_emitente or "").strip()
    nome_fornecedor = str(fornecedor or "").strip()
    if cnpj:
        query = query.filter_by(cnpj_emitente=cnpj)
    elif nome_fornecedor:
        query = query.filter_by(fornecedor=nome_fornecedor)
    return query.order_by(
        ProcessoRecebimentoEvento.created_at.asc(),
        ProcessoRecebimentoEvento.id.asc(),
    ).all()