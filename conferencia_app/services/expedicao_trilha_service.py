"""Trilha de auditoria automatica do Registro de expedicao e do Romaneio.

Um listener de `after_flush` grava em ExpedicaoTrilhaLog todo insert, update
e delete das tabelas auditadas, com o valor anterior e o novo de cada campo,
quem estava logado e de qual rota veio. A linha de log entra na MESMA
transacao da mudanca: se a mudanca for desfeita (rollback), o log some junto;
se for confirmada, o log esta' la'.

Por que no banco e nao rota a rota: registros e romaneios sao gravados por
varios modulos, e um log manual depende de cada rota nova lembrar de chamar.
"""

import json
from datetime import date, datetime

from flask import current_app, has_request_context, request, session
from sqlalchemy import event, inspect as sa_inspect

from ..extensions import db
from ..models import (
    ExpedicaoConferenciaSimples,
    ExpedicaoConferenciaSimplesEstorno,
    ExpedicaoConferenciaSimplesFoto,
    ExpedicaoRomaneio,
    ExpedicaoRomaneioEstorno,
    ExpedicaoRomaneioExclusao,
    ExpedicaoRomaneioFotoCarregamento,
    ExpedicaoRomaneioNF,
    ExpedicaoTrilhaLog,
)
from ..tempo import agora_br


USUARIO_SISTEMA = "SISTEMA"

# model -> (entidade, atributo com o id da entidade pai)
_AUDITADOS = {
    ExpedicaoConferenciaSimples: ("registro", "id"),
    ExpedicaoConferenciaSimplesFoto: ("registro", "conferencia_id"),
    ExpedicaoConferenciaSimplesEstorno: ("registro", "conferencia_id"),
    ExpedicaoRomaneio: ("romaneio", "id"),
    ExpedicaoRomaneioNF: ("romaneio", "romaneio_id"),
    ExpedicaoRomaneioFotoCarregamento: ("romaneio", "romaneio_id"),
    ExpedicaoRomaneioExclusao: ("romaneio", "romaneio_id"),
    ExpedicaoRomaneioEstorno: ("romaneio", "romaneio_id"),
}

# Carimbos que mudam em toda gravacao: sozinhos nao sao uma alteracao.
_CAMPOS_IGNORADOS = {"updated_at", "atualizado_em"}
_TAMANHO_MAXIMO = 300


def _texto(valor):
    if valor is None:
        return None
    if isinstance(valor, (datetime, date)):
        return valor.isoformat()
    if isinstance(valor, (bytes, bytearray, memoryview)):
        return f"<binário, {len(valor)} bytes>"
    if isinstance(valor, (bool, int, float)):
        return valor
    texto = str(valor)
    if len(texto) > _TAMANHO_MAXIMO:
        return texto[:_TAMANHO_MAXIMO] + f"… (+{len(texto) - _TAMANHO_MAXIMO} caracteres)"
    return texto


_CHAVE_ANTES = "_expedicao_trilha_antes"


@event.listens_for(db.session, "before_flush")
def _fotografar_antes(sessao, flush_context, instances):
    """Guarda como a linha esta' NO BANCO antes de o flush altera'-la.

    O historico do SQLAlchemy so' conhece o valor anterior de atributo que ja'
    estava carregado; num objeto expirado (logo apos um commit) a alteracao
    sairia como "— -> novo valor", sem o que havia antes."""
    antes = {}
    try:
        for obj in list(sessao.dirty) + list(sessao.deleted):
            if type(obj) not in _AUDITADOS:
                continue
            identidade = sa_inspect(obj).identity
            if not identidade:
                continue
            tabela = obj.__table__
            linha = sessao.connection().execute(
                tabela.select().where(tabela.c.id == identidade[0])
            ).mappings().first()
            if linha is not None:
                antes[(tabela.name, identidade[0])] = dict(linha)
    except Exception:  # noqa: BLE001
        current_app.logger.exception("Falha ao ler o estado anterior para a trilha da expedicao")
    sessao.info[_CHAVE_ANTES] = antes


def _mudancas(obj, operacao: str, antes: dict | None) -> list:
    estado = sa_inspect(obj)
    antes = antes or {}
    mudancas = []
    for coluna in estado.mapper.column_attrs:
        campo = coluna.key
        nome_coluna = coluna.columns[0].name
        historico = estado.attrs[campo].history
        if operacao == "alterado":
            if campo in _CAMPOS_IGNORADOS or not historico.has_changes():
                continue
            de = historico.deleted[0] if historico.deleted else antes.get(nome_coluna)
            para = historico.added[0] if historico.added else None
            if de == para:
                continue
        elif operacao == "criado":
            de, para = None, getattr(obj, campo)
            if para is None:
                continue
        else:  # excluido: guarda o que a linha tinha, senao a exclusao apaga a prova
            if nome_coluna in antes:
                de = antes[nome_coluna]
            else:
                valores = historico.deleted or historico.unchanged
                de = valores[0] if valores else None
            para = None
            if de is None:
                continue
        mudancas.append({"campo": campo, "de": _texto(de), "para": _texto(para)})
    return mudancas


def _contexto() -> tuple[str, str | None]:
    if not has_request_context():
        return USUARIO_SISTEMA, None
    rota = f"{request.method} {request.endpoint or request.path}"
    return session.get("username") or USUARIO_SISTEMA, rota[:160]


def _linhas(sessao) -> list:
    usuario, rota = _contexto()
    agora = agora_br()
    linhas = []
    fotos_antes = sessao.info.pop(_CHAVE_ANTES, {})
    for operacao, objetos in (("criado", sessao.new), ("alterado", sessao.dirty), ("excluido", sessao.deleted)):
        for obj in objetos:
            regra = _AUDITADOS.get(type(obj))
            if not regra:
                continue
            entidade, attr_pai = regra
            identidade = sa_inspect(obj).identity
            linha_id = identidade[0] if identidade else obj.id
            antes = fotos_antes.get((obj.__table__.name, linha_id))
            if operacao == "excluido":
                # A linha ja' saiu do banco: ler atributo expirado do objeto
                # tentaria recarrega'-la. Vale a foto tirada antes do flush.
                atual = antes or {}
                entidade_id, numero_nf = atual.get(attr_pai), atual.get("numero_nf")
            else:
                entidade_id, numero_nf = getattr(obj, attr_pai, None), getattr(obj, "numero_nf", None)
            mudancas = _mudancas(obj, operacao, antes)
            if entidade_id is None or not mudancas:
                continue
            linhas.append({
                "entidade": entidade,
                "entidade_id": entidade_id,
                "tabela": obj.__table__.name,
                "registro_id": linha_id,
                "operacao": operacao,
                "numero_nf": (str(numero_nf or "")[:160] or None),
                "usuario": usuario,
                "rota": rota,
                "detalhes": json.dumps(mudancas, ensure_ascii=False),
                "created_at": agora,
            })
    return linhas


@event.listens_for(db.session, "after_flush")
def _gravar_trilha(sessao, flush_context):
    try:
        linhas = _linhas(sessao)
    except Exception:  # noqa: BLE001
        # Montar o log nunca pode impedir a expedicao de ser gravada.
        current_app.logger.exception("Falha ao montar a trilha de auditoria da expedicao")
        return
    if linhas:
        # Insert direto na conexao da propria transacao: nao passa pelo
        # unit-of-work, entao nao dispara outro flush nem este listener.
        sessao.connection().execute(ExpedicaoTrilhaLog.__table__.insert(), linhas)


@event.listens_for(db.session, "do_orm_execute")
def _gravar_trilha_exclusao_em_massa(estado):
    """`Model.query.filter(...).delete()` apaga direto no banco, sem passar
    pelo flush: sem isto, essas linhas sumiriam sem entrar na trilha."""
    if not estado.is_delete:
        return
    mapper = estado.bind_mapper
    regra = _AUDITADOS.get(mapper.class_) if mapper is not None else None
    if not regra:
        return
    try:
        entidade, attr_pai = regra
        tabela = mapper.local_table
        conexao = estado.session.connection()
        apagadas = conexao.execute(
            tabela.select().where(*estado.statement._where_criteria), estado.parameters or {}
        ).mappings().all()
        usuario, rota = _contexto()
        agora = agora_br()
        linhas = [
            {
                "entidade": entidade,
                "entidade_id": linha[attr_pai],
                "tabela": tabela.name,
                "registro_id": linha["id"],
                "operacao": "excluido",
                "numero_nf": (str(linha.get("numero_nf") or "")[:160] or None),
                "usuario": usuario,
                "rota": rota,
                "detalhes": json.dumps(
                    [{"campo": c, "de": _texto(v), "para": None} for c, v in linha.items() if v is not None],
                    ensure_ascii=False,
                ),
                "created_at": agora,
            }
            for linha in apagadas
            if linha[attr_pai] is not None
        ]
        if linhas:
            conexao.execute(ExpedicaoTrilhaLog.__table__.insert(), linhas)
    except Exception:  # noqa: BLE001
        current_app.logger.exception("Falha ao registrar exclusao em massa na trilha da expedicao")


def listar(q: str = "", entidade: str = "", limite: int = 200) -> list:
    """Eventos mais recentes primeiro. `q` casa com NF, numero do romaneio ou
    o id do registro/romaneio."""
    consulta = ExpedicaoTrilhaLog.query
    if entidade in ("registro", "romaneio"):
        consulta = consulta.filter(ExpedicaoTrilhaLog.entidade == entidade)
    q = (q or "").strip()
    if q:
        filtros = [ExpedicaoTrilhaLog.numero_nf.ilike(f"%{q}%")]
        ids_romaneio = [
            r.id for r in ExpedicaoRomaneio.query.filter(ExpedicaoRomaneio.numero_romaneio.ilike(f"%{q}%")).limit(50)
        ]
        ids_registro = [
            r.id for r in ExpedicaoConferenciaSimples.query.filter(
                ExpedicaoConferenciaSimples.numero_nf.ilike(f"%{q}%")
            ).limit(50)
        ]
        ids_romaneio += [
            nf.romaneio_id for nf in ExpedicaoRomaneioNF.query.filter(ExpedicaoRomaneioNF.numero_nf.ilike(f"%{q}%")).limit(50)
        ]
        if q.isdigit():
            filtros.append(ExpedicaoTrilhaLog.entidade_id == int(q))
        if ids_romaneio:
            filtros.append(db.and_(ExpedicaoTrilhaLog.entidade == "romaneio", ExpedicaoTrilhaLog.entidade_id.in_(ids_romaneio)))
        if ids_registro:
            filtros.append(db.and_(ExpedicaoTrilhaLog.entidade == "registro", ExpedicaoTrilhaLog.entidade_id.in_(ids_registro)))
        consulta = consulta.filter(db.or_(*filtros))
    eventos = consulta.order_by(ExpedicaoTrilhaLog.id.desc()).limit(limite).all()

    numeros = {
        r.id: r.numero_romaneio
        for r in ExpedicaoRomaneio.query.filter(
            ExpedicaoRomaneio.id.in_({e.entidade_id for e in eventos if e.entidade == "romaneio"} or {0})
        )
    }
    resultado = []
    for e in eventos:
        try:
            mudancas = json.loads(e.detalhes) if e.detalhes else []
        except (ValueError, TypeError):
            mudancas = []
        resultado.append({
            "id": e.id,
            "entidade": e.entidade,
            "entidade_id": e.entidade_id,
            "referencia": numeros.get(e.entidade_id) if e.entidade == "romaneio" else f"Registro #{e.entidade_id}",
            "tabela": e.tabela,
            "registro_id": e.registro_id,
            "operacao": e.operacao,
            "numero_nf": e.numero_nf,
            "usuario": e.usuario,
            "rota": e.rota,
            "mudancas": mudancas,
            "created_at": e.created_at.isoformat() if e.created_at else None,
        })
    return resultado
