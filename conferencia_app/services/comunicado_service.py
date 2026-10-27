"""Mural de avisos da Columbia na tela inicial (model Comunicado).

Todo mundo logado vê; publicar, editar e remover pede MANAGE_COMUNICADOS
(checado na rota). Erros de preenchimento sobem como ValueError com a
mensagem para o usuário.
"""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import or_

from ..extensions import db
from ..models import Comunicado
from ..tempo import agora_br

TITULO_MAX = 160
TEXTO_MAX = 4000
# Aviso publicado há menos que isso ganha a etiqueta "Novo".
DIAS_NOVO = 3

_MESES = ("jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez")


def _serializar(c: Comunicado, hoje: date) -> dict:
    publicado = c.criado_em.date()
    return {
        "id": c.id,
        "titulo": c.titulo,
        "texto": c.texto,
        "publicado_em": f"{publicado.day:02d} {_MESES[publicado.month - 1]}",
        "novo": (hoje - publicado).days < DIAS_NOVO,
        "expira_em": c.expira_em.isoformat() if c.expira_em else "",
        "expira_em_fmt": c.expira_em.strftime("%d/%m") if c.expira_em else "",
    }


def listar_mural() -> list[dict]:
    """Avisos no ar, do mais novo para o mais antigo."""
    hoje = agora_br().date()
    avisos = (
        Comunicado.query
        .filter(Comunicado.ativo.is_(True))
        .filter(or_(Comunicado.expira_em.is_(None), Comunicado.expira_em >= hoje))
        .order_by(Comunicado.criado_em.desc(), Comunicado.id.desc())
        .limit(30)
        .all()
    )
    return [_serializar(c, hoje) for c in avisos]


def _validar(titulo, texto, expira_em) -> tuple[str, str, date | None]:
    titulo = str(titulo or "").strip()
    texto = str(texto or "").strip()
    if not titulo:
        raise ValueError("Informe o título do aviso.")
    if len(titulo) > TITULO_MAX:
        raise ValueError(f"O título passa de {TITULO_MAX} caracteres.")
    if not texto:
        raise ValueError("Escreva o texto do aviso.")
    if len(texto) > TEXTO_MAX:
        raise ValueError(f"O texto passa de {TEXTO_MAX} caracteres.")

    data = None
    if expira_em:
        try:
            data = datetime.strptime(str(expira_em), "%Y-%m-%d").date()
        except ValueError:
            raise ValueError("Data de \"mostrar até\" inválida.") from None
        if data < agora_br().date():
            raise ValueError("A data de \"mostrar até\" já passou.")
    return titulo, texto, data


def publicar(usuario: str, titulo, texto, expira_em=None) -> dict:
    titulo, texto, data = _validar(titulo, texto, expira_em)
    agora = agora_br()
    c = Comunicado(
        titulo=titulo, texto=texto, expira_em=data,
        criado_por=usuario, criado_em=agora, atualizado_por=usuario, atualizado_em=agora,
    )
    db.session.add(c)
    db.session.commit()
    return _serializar(c, agora.date())


def editar(comunicado_id: int, usuario: str, titulo, texto, expira_em=None) -> dict | None:
    c = db.session.get(Comunicado, comunicado_id)
    if c is None or not c.ativo:
        return None
    c.titulo, c.texto, c.expira_em = _validar(titulo, texto, expira_em)
    c.atualizado_por = usuario
    c.atualizado_em = agora_br()
    db.session.commit()
    return _serializar(c, agora_br().date())


def remover(comunicado_id: int, usuario: str) -> bool:
    c = db.session.get(Comunicado, comunicado_id)
    if c is None or not c.ativo:
        return False
    c.ativo = False
    c.atualizado_por = usuario
    c.atualizado_em = agora_br()
    db.session.commit()
    return True
