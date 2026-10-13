"""Validacao financeira da homologacao de fornecedor (F 066 rev. 04).

    Rascunho --enviar--> Validacao financeira --aprovar--> Em aprovacao
                                 \\--reprovar (com motivo)--> Rascunho

O Financeiro registra o parecer, campos de analise e anexos. Reprovado, a
homologacao volta pra Compras com o motivo; cada reenvio abre uma rodada
nova e as anteriores ficam de historico. Homologacoes do formulario antigo
(F-COM-001-01) nao passam por aqui.
"""
from __future__ import annotations

from ..auth import has_permission, is_admin_role
from ..extensions import db
from ..models import (
    ComprasHomologacaoFinanceiro as Validacao,
    ComprasHomologacaoFinanceiroAnexo as Anexo,
    ComprasHomologacaoFornecedor as Homologacao,
    Usuario,
)
from ..tempo import agora_br
from . import compras_homologacao_service as svc

PERMISSION = "PAGE_COMPRAS_HOMOLOGACAO_FINANCEIRO"
SIM_NAO = ("Sim", "Não")
MAX_ANEXO_BYTES = svc.MAX_EVIDENCIA_BYTES


def _txt(valor) -> str:
    return str(valor if valor is not None else "").strip()


def validacao_atual(homologacao: Homologacao) -> Validacao | None:
    return homologacao.validacoes_financeiras[-1] if homologacao.validacoes_financeiras else None


def abrir_rodada(homologacao: Homologacao, usuario: str) -> Validacao:
    """Chamado quando Compras envia. Sem commit - quem chama decide."""
    validacao = Validacao(status=Validacao.STATUS_PENDENTE, solicitado_por=usuario, solicitado_em=agora_br())
    homologacao.validacoes_financeiras.append(validacao)
    homologacao.status = Homologacao.STATUS_VALIDACAO_FINANCEIRA
    return validacao


def cancelar_rodada(homologacao: Homologacao) -> None:
    """Compras recolheu o envio antes do parecer."""
    validacao = validacao_atual(homologacao)
    if validacao and validacao.status == Validacao.STATUS_PENDENTE:
        validacao.status = Validacao.STATUS_CANCELADO


def _pendente(homologacao: Homologacao) -> Validacao:
    validacao = validacao_atual(homologacao)
    if (homologacao.status != Homologacao.STATUS_VALIDACAO_FINANCEIRA
            or not validacao or validacao.status != Validacao.STATUS_PENDENTE):
        raise ValueError("Esta homologação não está aguardando a validação financeira.")
    return validacao


def _aplicar_campos(validacao: Validacao, dados: dict) -> None:
    if "restricao_serasa" in dados:
        valor = _txt(dados.get("restricao_serasa"))
        if valor and valor not in SIM_NAO:
            raise ValueError("Restrição no Serasa: responda Sim ou Não.")
        validacao.restricao_serasa = valor or None
    if "score" in dados:
        validacao.score = _txt(dados.get("score"))[:40] or None
    if "limite_credito" in dados:
        bruto = _txt(dados.get("limite_credito")).replace("R$", "").replace(" ", "")
        if not bruto:
            validacao.limite_credito = None
        else:
            # Aceita 1.234,56 e 1234.56.
            if "," in bruto:
                bruto = bruto.replace(".", "").replace(",", ".")
            try:
                valor = float(bruto)
            except ValueError:
                raise ValueError("Limite de crédito inválido.")
            if valor < 0:
                raise ValueError("Limite de crédito inválido.")
            validacao.limite_credito = valor
    if "condicao_pagamento" in dados:
        validacao.condicao_pagamento = _txt(dados.get("condicao_pagamento"))[:160] or None
    if "observacao" in dados:
        validacao.observacao = _txt(dados.get("observacao"))[:4000] or None


def salvar(homologacao: Homologacao, dados: dict) -> Validacao:
    """Rascunho do parecer - o Financeiro pode salvar e voltar depois."""
    validacao = _pendente(homologacao)
    _aplicar_campos(validacao, dados or {})
    db.session.commit()
    return validacao


def aprovar(homologacao: Homologacao, usuario: str, dados: dict | None = None) -> Homologacao:
    validacao = _pendente(homologacao)
    _aplicar_campos(validacao, dados or {})
    agora = agora_br()
    validacao.status = Validacao.STATUS_APROVADO
    validacao.decidido_em = agora
    validacao.decidido_por = usuario
    homologacao.status = Homologacao.STATUS_EM_APROVACAO
    db.session.commit()
    return homologacao


def reprovar(homologacao: Homologacao, usuario: str, dados: dict | None = None) -> Homologacao:
    validacao = _pendente(homologacao)
    _aplicar_campos(validacao, dados or {})
    if not _txt(validacao.observacao):
        raise ValueError("Informe o motivo da reprovação financeira na observação.")
    agora = agora_br()
    validacao.status = Validacao.STATUS_REPROVADO
    validacao.decidido_em = agora
    validacao.decidido_por = usuario
    # Volta pra Compras, com o motivo a' vista na homologacao.
    homologacao.status = Homologacao.STATUS_RASCUNHO
    homologacao.enviado_em = None
    homologacao.enviado_por = None
    homologacao.justificativa_decisao = f"Reprovado pelo Financeiro ({usuario}): {validacao.observacao}"[:2000]
    db.session.commit()
    return homologacao


# ── Anexos ──────────────────────────────────────────────────────────────
def anexar(homologacao: Homologacao, arquivo, usuario: str) -> Anexo:
    validacao = _pendente(homologacao)
    if not arquivo or not getattr(arquivo, "filename", ""):
        raise ValueError("Nenhum arquivo recebido.")
    nome = _txt(arquivo.filename)
    extensao = ("." + nome.rsplit(".", 1)[-1].lower()) if "." in nome else ""
    content_type = svc.TIPOS_EVIDENCIA.get(extensao)
    if not content_type:
        raise ValueError("Formato não suportado - envie PDF, JPG ou PNG.")
    conteudo = arquivo.read(MAX_ANEXO_BYTES + 1)
    if not conteudo:
        raise ValueError("Arquivo vazio.")
    if len(conteudo) > MAX_ANEXO_BYTES:
        raise ValueError("Arquivo muito grande (máximo 10 MB).")
    anexo = Anexo(nome_arquivo=nome[:260], content_type=content_type, tamanho_bytes=len(conteudo),
                  dados=conteudo, enviado_por=usuario)
    validacao.anexos.append(anexo)
    db.session.commit()
    return anexo


def obter_anexo(anexo_id: int) -> Anexo | None:
    return db.session.get(Anexo, anexo_id)


def remover_anexo(anexo: Anexo) -> Homologacao:
    homologacao = anexo.validacao.homologacao
    validacao = _pendente(homologacao)
    if anexo.validacao_id != validacao.id:
        raise ValueError("Só dá pra remover anexo da validação em andamento.")
    validacao.anexos.remove(anexo)
    db.session.commit()
    return homologacao


# ── Avisos ──────────────────────────────────────────────────────────────
def emails_do_financeiro() -> list[str]:
    """Usuarios ativos com a permissao da validacao financeira e e-mail
    cadastrado. Admin tem todas as permissoes por definicao, entao fica de
    fora - senao todo admin receberia aviso de uma fila que nao e' dele."""
    emails = []
    for usuario in Usuario.query.filter_by(ativo=True).all():
        if not usuario.email or is_admin_role(usuario.role):
            continue
        if has_permission(PERMISSION, username=usuario.username, role=usuario.role):
            emails.append(usuario.email.strip())
    return sorted(set(emails))
