"""Cadastro de fornecedores do Comex (compartilhado entre PO e Cotacao).

Modulo 2 (PO): qualquer fornecedor ativo pode ser escolhido pra pre-preencher
o(s) e-mail(s) de envio da PO, em vez de digitar toda vez.
Modulo 3 (Cotacao): a selecao multipla pro envio em lote de link de cotacao
filtra so os fornecedores do tipo "Freight Forwarder".
"""
from __future__ import annotations

import re
from datetime import datetime

from ..extensions import db
from ..models import ComexFornecedor

TIPOS_FORNECEDOR = ("Freight Forwarder", "Transportador", "Prod/Dist")


def _normalizar_cnpj(valor) -> str | None:
    digitos = re.sub(r"\D", "", str(valor or ""))
    return digitos or None


def _normalizar_emails(valor) -> str | None:
    """Aceita string com um ou mais e-mails separados por ";" (ou ",", que o
    usuario as vezes digita por engano) e devolve normalizado com ";"."""
    bruto = str(valor or "").strip()
    if not bruto:
        return None
    partes = [p.strip() for p in re.split(r"[;,]", bruto) if p.strip()]
    invalidos = [p for p in partes if "@" not in p]
    if invalidos:
        raise ValueError(f"E-mail inválido: {invalidos[0]}")
    return "; ".join(partes) or None


def _validar_dados(dados: dict) -> dict:
    razao_social = str(dados.get("razao_social") or "").strip()
    if not razao_social:
        raise ValueError("Informe a razão social.")
    tipo_fornecedor = str(dados.get("tipo_fornecedor") or "").strip()
    if tipo_fornecedor not in TIPOS_FORNECEDOR:
        raise ValueError("Tipo de fornecedor inválido (use Freight Forwarder, Transportador ou Prod/Dist).")
    return {
        "razao_social": razao_social[:200],
        "nome_fantasia": (str(dados.get("nome_fantasia") or "").strip()[:200] or None),
        "cnpj": _normalizar_cnpj(dados.get("cnpj")),
        "email": _normalizar_emails(dados.get("email")),
        "telefone": (str(dados.get("telefone") or "").strip()[:40] or None),
        "tipo_fornecedor": tipo_fornecedor,
    }


def listar_fornecedores(tipo: str | None = None, ativo: bool | None = True, busca: str = "") -> list[ComexFornecedor]:
    query = ComexFornecedor.query
    if tipo:
        query = query.filter(ComexFornecedor.tipo_fornecedor == tipo)
    if ativo is not None:
        query = query.filter(ComexFornecedor.ativo == ativo)
    termo = str(busca or "").strip()
    if termo:
        like = f"%{termo}%"
        query = query.filter(
            db.or_(
                ComexFornecedor.razao_social.ilike(like),
                ComexFornecedor.nome_fantasia.ilike(like),
                ComexFornecedor.cnpj.ilike(like),
            )
        )
    return query.order_by(ComexFornecedor.razao_social.asc()).all()


def obter_fornecedor(fornecedor_id: int) -> ComexFornecedor | None:
    return ComexFornecedor.query.get(fornecedor_id)


def criar_fornecedor(dados: dict, usuario: str) -> ComexFornecedor:
    campos = _validar_dados(dados)
    fornecedor = ComexFornecedor(
        **campos,
        ativo=True,
        criado_por=usuario,
        atualizado_por=usuario,
    )
    db.session.add(fornecedor)
    db.session.commit()
    return fornecedor


def atualizar_fornecedor(fornecedor: ComexFornecedor, dados: dict, usuario: str) -> ComexFornecedor:
    campos = _validar_dados(dados)
    for chave, valor in campos.items():
        setattr(fornecedor, chave, valor)
    fornecedor.atualizado_em = datetime.now()
    fornecedor.atualizado_por = usuario
    db.session.commit()
    return fornecedor


def alternar_ativo(fornecedor: ComexFornecedor, ativo: bool, usuario: str) -> ComexFornecedor:
    fornecedor.ativo = bool(ativo)
    fornecedor.atualizado_em = datetime.now()
    fornecedor.atualizado_por = usuario
    db.session.commit()
    return fornecedor
