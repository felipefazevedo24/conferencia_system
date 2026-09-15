"""Indicadores pessoais, sempre limitados à conta autenticada e à data da ação."""
from calendar import monthrange
from datetime import date, datetime, time, timedelta

from sqlalchemy import func

from ..extensions import db
from ..models import (
    AgendamentoMotorista, AgendamentoSolicitacao, CadastroWorkflowSolicitacao,
    ExpedicaoConferencia, ExpedicaoConferenciaSimples, ExpedicaoRomaneio,
    ExpedicaoRomaneioNF, ExpedicaoOrdemFat, ExpedicaoOrdemST,
    ItemNota, LogisticaInventarioInicial, Viagem,
    WMSInventarioCiclico,
)


def periodo_perfil(args, hoje=None):
    hoje = hoje or date.today()
    periodo = args.get("periodo", "30")
    if periodo in ("30", "90"):
        inicio, fim = hoje - timedelta(days=int(periodo) - 1), hoje
    elif periodo == "personalizado":
        try:
            inicio = date.fromisoformat(args.get("inicio", ""))
            fim = date.fromisoformat(args.get("fim", ""))
        except (ValueError, TypeError):
            raise ValueError("Informe as datas inicial e final válidas.") from None
        if inicio > fim:
            raise ValueError("A data inicial deve ser anterior ou igual à data final.")
        if fim > hoje:
            raise ValueError("A data final não pode estar no futuro.")
        mes = inicio.month - 1 + 6
        ano, mes = inicio.year + mes // 12, mes % 12 + 1
        limite = date(ano, mes, min(inicio.day, monthrange(ano, mes)[1]))
        if fim >= limite:
            raise ValueError("Selecione no máximo 6 meses, incluindo as datas inicial e final.")
    else:
        raise ValueError("Selecione um período válido.")
    return {"periodo": periodo, "inicio": inicio, "fim": fim, "hoje": hoje}


def indicadores_perfil(username, periodo):
    inicio = datetime.combine(periodo["inicio"], time.min)
    fim = datetime.combine(periodo["fim"] + timedelta(days=1), time.min)

    def filtros(autor, data):
        return (autor == username, data >= inicio, data < fim)

    def contar(modelo, autor, data, *extras):
        return modelo.query.filter(*filtros(autor, data), *extras).count()

    # A chave fiscal identifica a NF; documentos legados usam tipo, emitente e número.
    identidade = (
        func.coalesce(func.nullif(ItemNota.chave_acesso, ""), ""),
        ItemNota.tipo_documento, func.coalesce(ItemNota.cnpj_emitente, ""),
        ItemNota.numero_nota,
    )

    def notas(autor, data, *extras):
        return db.session.query(*identidade).filter(
            *filtros(autor, data), ItemNota.numero_nota.isnot(None), *extras,
        ).distinct().count()

    e = ExpedicaoConferenciaSimples
    r = ExpedicaoRomaneio
    # UNION remove as NFs espelhadas automaticamente do romaneio no registro simples.
    avulsas = db.session.query(e.numero_nf).filter(
        *filtros(e.expedido_by, e.expedido_at), e.numero_nf.isnot(None),
        e.numero_nf != "",
    )
    romaneios = db.session.query(ExpedicaoRomaneioNF.numero_nf).join(r).filter(
        *filtros(r.expedido_por, r.expedido_em), r.status == "Expedido",
    )
    motorista_ids = db.session.query(AgendamentoMotorista.id).filter(
        AgendamentoMotorista.usuario_username == username,
    )
    viagens = Viagem.query.filter(
        Viagem.motorista_id.in_(motorista_ids), Viagem.status == "Concluida",
        Viagem.retorno_real >= inicio, Viagem.retorno_real < fim,
    )

    def modulo(id_, titulo, icone, descricao, metricas):
        return {"id": id_, "titulo": titulo, "icone": icone, "descricao": descricao,
                "metricas": [{"rotulo": label, "valor": value} for label, value in metricas],
                "ativo": any(value for _, value in metricas)}

    return [
        modulo("recebimento", "Recebimento", "fa-box-open", "Conferências vinculadas à sua conta.", [
            ("Notas fiscais conferidas por você", notas(ItemNota.usuario_conferencia, ItemNota.fim_conferencia, ItemNota.tipo_documento == "NFE")),
            ("Itens conferidos", contar(ItemNota, ItemNota.usuario_conferencia, ItemNota.fim_conferencia)),
        ]),
        modulo("expedicao", "Expedição", "fa-truck-ramp-box", "Conferências encerradas e saídas realizadas por você.", [
            ("Notas fiscais expedidas por você", avulsas.union(romaneios).count()),
            ("Ordens FAT conferidas", contar(ExpedicaoOrdemFat, ExpedicaoOrdemFat.conferente, ExpedicaoOrdemFat.conferido_at, ExpedicaoOrdemFat.excluido.is_(False))),
            ("Ordens ST conferidas", contar(ExpedicaoOrdemST, ExpedicaoOrdemST.conferente, ExpedicaoOrdemST.conferido_at, ExpedicaoOrdemST.excluido.is_(False))),
            ("Conferências manuais", contar(e, e.conferente, e.data_conferencia, e.origem == "Manual", e.sem_conferencia.is_(False))),
            ("Conferências encerradas", contar(ExpedicaoConferencia, ExpedicaoConferencia.closed_by, ExpedicaoConferencia.closed_at, ExpedicaoConferencia.status == "Fechada")),
            ("Romaneios expedidos", contar(r, r.expedido_por, r.expedido_em, r.status == "Expedido")),
        ]),
        modulo("viagens", "Viagens e agendamentos", "fa-route", "Viagens realizadas consideram seu vínculo como motorista.", [
            ("Viagens concluídas como motorista", viagens.count()),
            ("Viagens criadas", contar(Viagem, Viagem.criado_por, Viagem.criado_em)),
            ("Agendamentos solicitados", contar(AgendamentoSolicitacao, AgendamentoSolicitacao.solicitante, AgendamentoSolicitacao.criado_em)),
        ]),
        modulo("inventarios", "Inventários", "fa-boxes-stacked", "Cada contagem representa um registro de produto e local.", [
            ("Contagens de estoque feitas por você", contar(LogisticaInventarioInicial, LogisticaInventarioInicial.criado_por, LogisticaInventarioInicial.criado_em)),
            ("Contagens cíclicas", contar(WMSInventarioCiclico, WMSInventarioCiclico.contado_por, WMSInventarioCiclico.contado_em)),
            ("Contagens aprovadas", contar(WMSInventarioCiclico, WMSInventarioCiclico.aprovado_por, WMSInventarioCiclico.aprovado_em, WMSInventarioCiclico.status == "Aprovado")),
        ]),
        modulo("documentos", "Documentos de entrada", "fa-file-invoice", "Documentos únicos, independentemente da quantidade de itens.", [
            ("Documentos importados por você", notas(ItemNota.usuario_importacao, ItemNota.data_importacao)),
            ("Documentos auditados", notas(ItemNota.auditor_usuario, ItemNota.auditor_data)),
            ("Documentos lançados", notas(ItemNota.usuario_lancamento, ItemNota.data_lancamento)),
        ]),
        modulo("cadastros", "Cadastros", "fa-diagram-project", "Solicitações de cadastro abertas por você.", [
            ("Solicitações de cadastro abertas por você", contar(CadastroWorkflowSolicitacao, CadastroWorkflowSolicitacao.solicitante, CadastroWorkflowSolicitacao.data_abertura)),
        ]),
    ]
