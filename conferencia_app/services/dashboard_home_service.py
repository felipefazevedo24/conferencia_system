"""Tela inicial: o que precisa de acao agora e o movimento de hoje.

Cada fila mostra quantos itens estao pendentes e quantos estao atrasados
(parados alem do limite da fila, ou com prazo vencido). Total historico
("desde sempre") fica de fora de proposito: nao pede acao de ninguem.

Cada fila/indicador so aparece para quem tem permissao na area dela.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

from sqlalchemy import and_, func

from ..auth import get_effective_permissions
from ..extensions import db
from ..models import (
    AgendamentoSolicitacao,
    CadastroWorkflowSolicitacao,
    ComexProcesso,
    ExpedicaoConferencia,
    ExpedicaoConferenciaSimples,
    ExpedicaoOrdemFat,
    ExpedicaoOrdemST,
    ExpedicaoRomaneio,
    ItemNota,
    PlannerBoardPessoal,
    PlannerCard,
    PlannerColumn,
    Viagem,
)
from ..tempo import agora_br

_logger = logging.getLogger(__name__)

_PERM_RECEBIMENTO = ("PAGE_CONFERENCIA", "PAGE_PORTARIA")
_PERM_DOC_ENTRADA = ("PAGE_UPLOAD", "PAGE_LANCAMENTO")
_PERM_EXPEDICAO = ("PAGE_EXPEDICAO_CONFERENCIA", "PAGE_EXPEDICAO_CONF_CEGA", "PAGE_EXPEDICAO_ROMANEIO")
_PERM_LOGISTICA = ("PAGE_LOGISTICA_AGENDAMENTO", "PAGE_LOGISTICA_SOLICITACAO", "PAGE_LOGISTICA_VIAGEM")


def _pode(perms: dict, chaves) -> bool:
    return any(perms.get(k, False) for k in chaves)


def _notas(filtro, coluna_data, limite: datetime) -> tuple[int, int]:
    """(total, atrasadas) de notas distintas; a nota conta pela data mais
    antiga dos itens dela."""
    sub = (
        db.session.query(ItemNota.numero_nota.label("numero"), func.min(coluna_data).label("desde"))
        .filter(filtro)
        .group_by(ItemNota.numero_nota)
        .subquery()
    )
    total = db.session.query(func.count()).select_from(sub).scalar() or 0
    atrasadas = db.session.query(func.count()).select_from(sub).filter(sub.c.desde < limite).scalar() or 0
    return int(total), int(atrasadas)


def _contar(query, coluna_atraso, corte) -> tuple[int, int]:
    total = query.count()
    atrasados = query.filter(coluna_atraso.isnot(None), coluna_atraso < corte).count()
    return int(total), int(atrasados)


def _fila(id_, titulo, icone, href, total, atrasados, texto_atraso) -> dict:
    if atrasados:
        estado, status = "atrasado", texto_atraso
    elif total:
        estado, status = "pendente", "nenhum atrasado"
    else:
        estado, status = "ok", "tudo em dia"
    return {
        "id": id_,
        "titulo": titulo,
        "icone": icone,
        "href": href,
        "total": total,
        "atrasados": atrasados,
        "estado": estado,
        "status": status,
    }


def montar_filas() -> list[dict]:
    """Filas que pedem acao, da mais grave para a mais tranquila."""
    perms = get_effective_permissions()
    agora = agora_br()
    hoje = agora.date()
    dias = lambda n: agora - timedelta(days=n)  # noqa: E731
    filas: list[dict] = []

    def tentar(montar):
        # Uma fila com erro nao derruba as outras nem a tela.
        try:
            filas.append(montar())
        except Exception:
            db.session.rollback()
            _logger.exception("dashboard_fila_falhou")

    if _pode(perms, ("PAGE_EXPEDICAO_CONF_CEGA",)):
        def ordens():
            t1, a1 = _contar(
                ExpedicaoOrdemFat.query.filter(ExpedicaoOrdemFat.excluido.is_(False),
                                               ExpedicaoOrdemFat.status == "Pendente de conferência"),
                ExpedicaoOrdemFat.dt_solicitacao_fat, dias(2),
            )
            t2, a2 = _contar(
                ExpedicaoOrdemST.query.filter(ExpedicaoOrdemST.excluido.is_(False),
                                              ExpedicaoOrdemST.status == "Pendente de conferência"),
                ExpedicaoOrdemST.dt_solicitacao, dias(2),
            )
            return _fila("ordens", "Ordens a conferir (FAT/ST)", "fa-clipboard-list", "/expedicao/conferencia-cega",
                         t1 + t2, a1 + a2, f"{a1 + a2} há mais de 2 dias")
        tentar(ordens)

    if _pode(perms, ("PAGE_EXPEDICAO_ROMANEIO",)):
        def faturado_sem_romaneio():
            # Conferido e faturado: falta montar o romaneio e carregar.
            t1, a1 = _contar(
                ExpedicaoOrdemFat.query.filter(ExpedicaoOrdemFat.excluido.is_(False), ExpedicaoOrdemFat.status == "Faturado"),
                ExpedicaoOrdemFat.faturado_at, dias(2),
            )
            t2, a2 = _contar(
                ExpedicaoOrdemST.query.filter(ExpedicaoOrdemST.excluido.is_(False), ExpedicaoOrdemST.status == "Faturado"),
                ExpedicaoOrdemST.faturado_at, dias(2),
            )
            return _fila("faturado", "Faturado aguardando romaneio", "fa-file-invoice", "/expedicao/romaneio",
                         t1 + t2, a1 + a2, f"{a1 + a2} há mais de 2 dias")
        tentar(faturado_sem_romaneio)

        def romaneios():
            total, atrasados = _contar(
                ExpedicaoRomaneio.query.filter(ExpedicaoRomaneio.status.in_(["Rascunho", "Pronto"])),
                ExpedicaoRomaneio.criado_em, dias(2),
            )
            return _fila("romaneios", "Romaneios a expedir", "fa-receipt", "/expedicao/romaneio",
                         total, atrasados, f"{atrasados} há mais de 2 dias")
        tentar(romaneios)

    if _pode(perms, ("PAGE_EXPEDICAO_CONFERENCIA",)):
        def canhotos():
            # Registro fica "Expedido" ate o comprovante de entrega chegar.
            total, atrasados = _contar(
                ExpedicaoConferenciaSimples.query.filter(
                    ExpedicaoConferenciaSimples.status == "Expedido",
                    func.coalesce(ExpedicaoConferenciaSimples.canhoto_file_name, "") == "",
                ),
                ExpedicaoConferenciaSimples.expedido_at, dias(7),
            )
            return _fila("canhotos", "Canhotos pendentes", "fa-file-signature", "/expedicao/conferencia",
                         total, atrasados, f"{atrasados} há mais de 7 dias")
        tentar(canhotos)

    if _pode(perms, _PERM_EXPEDICAO):
        def expedicao():
            t1, a1 = _contar(
                ExpedicaoConferencia.query.filter(ExpedicaoConferencia.status.in_(["Aberta", "PendenteDecisao"])),
                ExpedicaoConferencia.created_at, dias(2),
            )
            t2, a2 = _contar(
                ExpedicaoConferenciaSimples.query.filter(ExpedicaoConferenciaSimples.status == "Pendente de expedição"),
                ExpedicaoConferenciaSimples.created_at, dias(2),
            )
            return _fila("expedicao", "Registros a expedir", "fa-truck-ramp-box", "/expedicao/conferencia",
                         t1 + t2, a1 + a2, f"{a1 + a2} há mais de 2 dias")
        tentar(expedicao)

    if _pode(perms, _PERM_RECEBIMENTO):
        def recebimento():
            total, atrasadas = _notas(ItemNota.status == "Pendente", ItemNota.data_importacao, dias(2))
            return _fila("recebimento", "Recebimento a conferir", "fa-barcode", "/conferencia",
                         total, atrasadas, f"{atrasadas} há mais de 2 dias")
        tentar(recebimento)

    if _pode(perms, _PERM_DOC_ENTRADA):
        def documento():
            total, atrasadas = _notas(ItemNota.status == "Concluído", ItemNota.fim_conferencia, dias(2))
            return _fila("documento", "Documento de entrada a lançar", "fa-file-import", "/upload",
                         total, atrasadas, f"{atrasadas} há mais de 2 dias")
        tentar(documento)

    if _pode(perms, ("PAGE_XML_AUDITOR",)):
        def auditoria():
            # auditor_decisao nasce "PendenteDecisao" em toda nota e a maioria
            # nunca passa pelo auditor; pendente de verdade e a nota parada em
            # AguardandoLiberacao (mesma regra do painel de recebimento).
            pendente = and_(
                ItemNota.status == "AguardandoLiberacao",
                func.coalesce(ItemNota.auditor_decisao, "").notin_(["Aprovado", "AprovadoAutomatico"]),
            )
            total, atrasadas = _notas(pendente, ItemNota.data_importacao, dias(7))
            return _fila("auditoria", "Auditoria XML", "fa-file-shield", "/upload",
                         total, atrasadas, f"{atrasadas} há mais de 7 dias")
        tentar(auditoria)

    if _pode(perms, ("PAGE_CADASTRO_WORKFLOW",)):
        def cadastros():
            total, atrasados = _contar(
                CadastroWorkflowSolicitacao.query.filter(
                    CadastroWorkflowSolicitacao.status.notin_(["Finalizada", "Cancelada"])),
                CadastroWorkflowSolicitacao.data_abertura, dias(3),
            )
            return _fila("cadastros", "Cadastros a validar", "fa-diagram-project", "/cadastros/",
                         total, atrasados, f"{atrasados} há mais de 3 dias")
        tentar(cadastros)

    if _pode(perms, _PERM_LOGISTICA):
        abertas = AgendamentoSolicitacao.query.filter(AgendamentoSolicitacao.status.notin_(["Concluida", "Cancelada"]))
        for tipo, titulo, icone in (("COLETA", "Coletas abertas", "fa-dolly"), ("ENTREGA", "Entregas abertas", "fa-truck")):
            def solicitacoes(tipo=tipo, titulo=titulo, icone=icone):
                total, atrasadas = _contar(
                    abertas.filter(AgendamentoSolicitacao.tipo == tipo),
                    AgendamentoSolicitacao.prazo_limite, agora,
                )
                return _fila(tipo.lower(), titulo, icone, "/logistica/viagens",
                             total, atrasadas, f"{atrasadas} com prazo vencido")
            tentar(solicitacoes)

        def viagens():
            total, atrasadas = _contar(
                Viagem.query.filter(Viagem.status == "Planejada"), Viagem.saida_prevista, agora,
            )
            return _fila("viagens", "Viagens programadas", "fa-route", "/logistica/viagens",
                         total, atrasadas, f"{atrasadas} com saída atrasada")
        tentar(viagens)

    if _pode(perms, ("PAGE_PLANEJAMENTO_TAREFAS",)):
        def tarefas():
            # So o board da equipe: os de "Minhas tarefas" sao privados.
            abertas = (
                PlannerCard.query
                .join(PlannerColumn, PlannerColumn.id == PlannerCard.column_id)
                .filter(PlannerColumn.is_done.is_(False))
                .filter(PlannerColumn.board_id.notin_(db.session.query(PlannerBoardPessoal.board_id)))
            )
            total, vencidas = _contar(abertas, PlannerCard.prazo, hoje)
            return _fila("tarefas", "Tarefas da equipe", "fa-list-check", "/planejamento",
                         total, vencidas, f"{vencidas} com prazo vencido")
        tentar(tarefas)

    ordem = {"atrasado": 0, "pendente": 1, "ok": 2}
    filas.sort(key=lambda f: (ordem[f["estado"]], -f["total"]))
    return filas


def _por_dia(query_base, coluna, dia: date) -> int:
    return int(query_base.filter(func.date(coluna) == dia).scalar() or 0)


def montar_hoje() -> list[dict]:
    """Movimento de hoje comparado com ontem, mais o que esta em andamento."""
    perms = get_effective_permissions()
    hoje = agora_br().date()
    ontem = hoje - timedelta(days=1)
    itens: list[dict] = []

    def tentar(montar):
        try:
            itens.append(montar())
        except Exception:
            db.session.rollback()
            _logger.exception("dashboard_hoje_falhou")

    notas = db.session.query(func.count(func.distinct(ItemNota.numero_nota)))

    if _pode(perms, _PERM_RECEBIMENTO + _PERM_DOC_ENTRADA + ("PAGE_FISCAL_LIBERADAS",)):
        tentar(lambda: {
            "titulo": "NFs recebidas", "icone": "fa-inbox",
            "valor": _por_dia(notas, ItemNota.data_importacao, hoje),
            "ontem": _por_dia(notas, ItemNota.data_importacao, ontem),
        })

    if _pode(perms, _PERM_DOC_ENTRADA):
        lancadas = notas.filter(ItemNota.status == "Lançado")
        tentar(lambda: {
            "titulo": "NFs lançadas", "icone": "fa-file-circle-check",
            "valor": _por_dia(lancadas, ItemNota.data_lancamento, hoje),
            "ontem": _por_dia(lancadas, ItemNota.data_lancamento, ontem),
        })

    if _pode(perms, _PERM_EXPEDICAO):
        expedidas = db.session.query(func.count(func.distinct(ExpedicaoConferenciaSimples.numero_nf)))
        tentar(lambda: {
            "titulo": "NFs expedidas", "icone": "fa-truck-ramp-box",
            "valor": _por_dia(expedidas, ExpedicaoConferenciaSimples.expedido_at, hoje),
            "ontem": _por_dia(expedidas, ExpedicaoConferenciaSimples.expedido_at, ontem),
        })

    if _pode(perms, ("PAGE_EXPEDICAO_ROMANEIO",)):
        romaneios = db.session.query(func.count(ExpedicaoRomaneio.id))
        tentar(lambda: {
            "titulo": "Romaneios expedidos", "icone": "fa-receipt",
            "valor": _por_dia(romaneios, ExpedicaoRomaneio.expedido_em, hoje),
            "ontem": _por_dia(romaneios, ExpedicaoRomaneio.expedido_em, ontem),
        })

    if _pode(perms, _PERM_LOGISTICA):
        for tipo, titulo, icone in (("COLETA", "Coletas feitas", "fa-dolly"), ("ENTREGA", "Entregas feitas", "fa-truck")):
            feitas = db.session.query(func.count(AgendamentoSolicitacao.id)).filter(
                AgendamentoSolicitacao.tipo == tipo, AgendamentoSolicitacao.status == "Concluida")
            tentar(lambda feitas=feitas, titulo=titulo, icone=icone: {
                "titulo": titulo, "icone": icone,
                "valor": _por_dia(feitas, AgendamentoSolicitacao.concluido_em, hoje),
                "ontem": _por_dia(feitas, AgendamentoSolicitacao.concluido_em, ontem),
            })
        tentar(lambda: {
            "titulo": "Viagens hoje", "icone": "fa-calendar-day",
            "valor": _por_dia(db.session.query(func.count(Viagem.id)), Viagem.saida_prevista, hoje),
            "ontem": None, "href": "/logistica/viagens", "rotulo": "programadas",
        })
        tentar(lambda: {
            "titulo": "Viagens em rota", "icone": "fa-truck-fast",
            "valor": Viagem.query.filter(Viagem.status == "EmAndamento").count(),
            "ontem": None, "href": "/logistica/viagens?viagens=EM_ANDAMENTO",
        })

    if _pode(perms, ("PAGE_COMEX",)):
        tentar(lambda: {
            "titulo": "Comex em andamento", "icone": "fa-ship",
            "valor": ComexProcesso.query.filter(ComexProcesso.processo_concluido_em.is_(None),
                                                ComexProcesso.status_modulo != "Cancelado").count(),
            "ontem": None, "href": "/comex",
        })

    return itens
