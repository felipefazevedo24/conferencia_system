"""Departamentos da empresa, cadastrados em Administração > Configuração.

Fonte única da lista usada na solicitação de veículo, nas viagens, no
relatório de viagens e no rateio do combustível.
"""
from __future__ import annotations

from ..extensions import db
from ..models import Departamento

# Lista que existia fixa no código; semeia a tabela no primeiro boot.
PADRAO = ("COMPRAS", "ASSISTÊNCIA TÉCNICA", "ENGENHARIA/PCP", "LOGÍSTICA", "FACILITIES")


def normalizar(nome) -> str:
    return " ".join(str(nome or "").split()).upper()


def semear_padrao() -> None:
    """Só com a tabela vazia: um departamento desativado não volta sozinho."""
    if Departamento.query.first():
        return
    for nome in PADRAO:
        db.session.add(Departamento(nome=nome, ativo=True, criado_por="SISTEMA"))
    db.session.commit()


def nomes_ativos() -> list[str]:
    return [d.nome for d in Departamento.query.filter_by(ativo=True).order_by(Departamento.nome).all()]


def nomes_todos() -> list[str]:
    """Inclui inativos: filtro de relatório precisa achar viagem antiga."""
    return [d.nome for d in Departamento.query.order_by(Departamento.ativo.desc(), Departamento.nome).all()]


def listar() -> list[dict]:
    return [
        {"id": d.id, "nome": d.nome, "ativo": bool(d.ativo)}
        for d in Departamento.query.order_by(Departamento.ativo.desc(), Departamento.nome).all()
    ]


def criar(nome, usuario: str) -> Departamento:
    nome = normalizar(nome)
    if not nome:
        raise ValueError("Informe o nome do departamento.")
    if len(nome) > 50:
        raise ValueError("Nome muito longo (máximo 50 caracteres).")
    existente = Departamento.query.filter_by(nome=nome).first()
    if existente:
        if existente.ativo:
            raise ValueError(f"{nome} já existe.")
        existente.ativo = True
        db.session.commit()
        return existente
    dep = Departamento(nome=nome, ativo=True, criado_por=usuario)
    db.session.add(dep)
    db.session.commit()
    return dep


def definir_ativo(dep_id: int, ativo: bool) -> Departamento | None:
    dep = db.session.get(Departamento, dep_id)
    if not dep:
        return None
    if not ativo and dep.ativo and len(nomes_ativos()) <= 1:
        raise ValueError("Precisa ficar pelo menos um departamento ativo.")
    dep.ativo = ativo
    db.session.commit()
    return dep
