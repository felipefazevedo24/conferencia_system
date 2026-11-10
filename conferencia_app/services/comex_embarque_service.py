"""Comex > embarque com mais de uma PO.

Na cotacao, o operador inclui POs de outros processos do mesmo fornecedor no
embarque do processo principal: uma cotacao de frete, uma instrucao, um
BL/AWB. Regras (usuario, 10/11/2026):
  - mesmo fornecedor; a PO incluida precisa estar Finalizada;
  - so' antes da Instrucao (principal e incluido em PO ou Cotacao);
  - o incluido some da lista e segue o principal; remover devolve ele ao
    proprio status (que nao e' alterado enquanto incluido);
  - toda inclusao/remocao vira comentario nos dois processos;
  - cada link de cotacao grava a composicao de POs do momento
    (ComexCotacaoComposicao); incluir/remover com cotacao ja' gerada exige
    confirmacao do operador.
"""
from __future__ import annotations

import json

from ..extensions import db
from ..models import ComexComentario, ComexCotacao, ComexCotacaoComposicao, ComexEmbarquePo, ComexProcesso
from ..tempo import agora_br
from . import comex_service as svc

STATUS_EDITAVEIS = ("PO", "Cotacao")


class ConfirmacaoNecessaria(ValueError):
    """Ja' existem cotacoes geradas com a composicao atual - a rota devolve
    409 e a tela pergunta antes de repetir com `confirmar`."""


def processos_do_embarque(principal: ComexProcesso) -> list[ComexProcesso]:
    """O principal e as POs incluidas, nessa ordem."""
    return [principal, *[v.processo for v in principal.pos_embarque]]


def subtotal_embarque(principal: ComexProcesso) -> float | None:
    total = sum(svc.subtotal_itens_po(p) or 0 for p in processos_do_embarque(principal))
    return round(total, 2) if total else None


def resumo_pos(principal: ComexProcesso) -> list[dict]:
    return [
        {"processo_id": p.id, "id_op": p.id_op, "po_numero": p.po_numero,
         "cod_ordem_compra": p.cod_ordem_compra, "valor_usd": svc.subtotal_itens_po(p)}
        for p in processos_do_embarque(principal)
    ]


def gravar_composicao(cotacao: ComexCotacao, principal: ComexProcesso, pos: list[dict] | None = None) -> None:
    pos = resumo_pos(principal) if pos is None else pos
    db.session.add(ComexCotacaoComposicao(cotacao=cotacao, pos=json.dumps(pos, ensure_ascii=False)))


def composicao_da_cotacao(cotacao: ComexCotacao) -> list[dict] | None:
    """None = cotacao anterior a este registro."""
    if not cotacao.composicao:
        return None
    try:
        return json.loads(cotacao.composicao.pos)
    except (TypeError, ValueError):
        return None


def candidatas(principal: ComexProcesso) -> list[ComexProcesso]:
    """Processos do mesmo fornecedor que podem entrar no embarque agora (os
    ja' incluidos vem a parte, em `pos_embarque`)."""
    pendentes = []
    for p in ComexProcesso.query.filter(ComexProcesso.id != principal.id,
                                        ComexProcesso.status_modulo.in_(STATUS_EDITAVEIS)).all():
        if _motivo_bloqueio(principal, p, checar_principal=False) is None:
            pendentes.append(p)
    return pendentes


def _motivo_bloqueio(principal: ComexProcesso, outro: ComexProcesso, checar_principal: bool = True) -> str | None:
    if checar_principal:
        motivo = _motivo_principal(principal)
        if motivo:
            return motivo
    if outro.id == principal.id:
        return "Escolha outro processo."
    if not svc._mesmo_fornecedor([principal, outro]):
        return f"{outro.id_op} é de outro fornecedor."
    if svc.exportacao_da_expedicao(outro):
        return f"{outro.id_op} é exportação."
    if not outro.po_numero or outro.po_status != "Finalizada":
        return f"Gere e finalize a PO de {outro.id_op} antes de incluí-la."
    if outro.status_modulo not in STATUS_EDITAVEIS:
        return f"{outro.id_op} já passou da Cotação (está em {svc.LABELS_MODULO.get(outro.status_modulo, outro.status_modulo)})."
    if outro.po_processo_principal_id:
        return f"{outro.id_op} está combinado na PO de outro processo."
    if outro.embarque_principal:
        return f"{outro.id_op} já está no embarque de {outro.embarque_principal.principal.id_op}."
    if outro.pos_embarque:
        return f"{outro.id_op} já é o principal de outro embarque - inclua as POs nele."
    if ComexCotacao.query.filter_by(processo_id=outro.id).first():
        return f"{outro.id_op} já tem cotação própria - use-o como principal ou inclua as POs nele."
    return None


def _motivo_principal(principal: ComexProcesso) -> str | None:
    if svc.exportacao_da_expedicao(principal):
        return "Exportação não tem PO para incluir."
    if principal.embarque_principal:
        return f"Este processo está no embarque de {principal.embarque_principal.principal.id_op} - altere por lá."
    if principal.status_modulo not in STATUS_EDITAVEIS:
        return "As POs do embarque só podem ser alteradas antes da Instrução de Embarque."
    return None


def _exigir_confirmacao(principal: ComexProcesso, confirmar: bool, acao: str) -> None:
    cotacoes = ComexCotacao.query.filter_by(processo_id=principal.id).all()
    if cotacoes and not confirmar:
        recebidas = sum(1 for c in cotacoes if c.status == "Recebida")
        raise ConfirmacaoNecessaria(
            f"Este embarque já tem {len(cotacoes)} cotação(ões) gerada(s) ({recebidas} recebida(s)) com as POs atuais. "
            f"Os forwarders cotaram sem {acao} - considere pedir nova cotação. Confirma?"
        )


def _comentar(processo: ComexProcesso, texto: str, usuario: str) -> None:
    db.session.add(ComexComentario(processo_id=processo.id, texto=texto, criado_por=usuario))


def _rotulo(p: ComexProcesso) -> str:
    return f"{p.po_numero} ({p.id_op})" if p.po_numero else p.id_op


def incluir_po(principal: ComexProcesso, outro: ComexProcesso, usuario: str, confirmar: bool = False) -> ComexProcesso:
    motivo = _motivo_bloqueio(principal, outro)
    if motivo:
        raise ValueError(motivo)
    _exigir_confirmacao(principal, confirmar, f"a {_rotulo(outro)}")
    db.session.add(ComexEmbarquePo(principal=principal, processo=outro, incluido_em=agora_br(), incluido_por=usuario))
    _comentar(principal, f"PO {_rotulo(outro)} incluída no embarque.", usuario)
    _comentar(outro, f"PO incluída no embarque de {principal.id_op} - segue o processo principal.", usuario)
    principal.atualizado_em, principal.atualizado_por = agora_br(), usuario
    db.session.commit()
    return principal


def remover_po(principal: ComexProcesso, outro: ComexProcesso, usuario: str, confirmar: bool = False) -> ComexProcesso:
    motivo = _motivo_principal(principal)
    if motivo:
        raise ValueError(motivo)
    vinculo = ComexEmbarquePo.query.filter_by(principal_id=principal.id, processo_id=outro.id).first()
    if not vinculo:
        raise ValueError(f"{outro.id_op} não está neste embarque.")
    _exigir_confirmacao(principal, confirmar, f"a saída da {_rotulo(outro)}")
    db.session.delete(vinculo)
    _comentar(principal, f"PO {_rotulo(outro)} removida do embarque.", usuario)
    _comentar(outro, f"PO removida do embarque de {principal.id_op} - volta a ser processo independente.", usuario)
    principal.atualizado_em, principal.atualizado_por = agora_br(), usuario
    db.session.commit()
    return principal


def liberar_todas(principal: ComexProcesso, usuario: str) -> list[str]:
    """Principal cancelado: as POs incluidas voltam a ser independentes
    (sem commit - quem chama comita)."""
    liberadas = []
    for vinculo in list(principal.pos_embarque):
        _comentar(vinculo.processo, f"PO liberada: o embarque {principal.id_op} foi cancelado.", usuario)
        liberadas.append(_rotulo(vinculo.processo))
        db.session.delete(vinculo)
    return liberadas
