from datetime import datetime, timedelta

from flask import Blueprint, redirect, render_template, request, session, url_for
from sqlalchemy import func

from ..auth import get_effective_permissions, login_required, permission_required, permission_required_any
from ..extensions import db
from ..models import (
    ActiveSession,
    AgendamentoSolicitacao,
    CadastroWorkflowSolicitacao,
    ComexProcesso,
    ExpedicaoConferencia,
    ExpedicaoConferenciaSimples,
    ItemNota,
    PlannerCard,
    PlannerColumn,
    Usuario,
    Viagem,
)


page_bp = Blueprint("pages", __name__)


HOME_MODULES = [
    {
        "id": "portaria",
        "title": "Portaria",
        "subtitle": "Entrada inicial",
        "description": "Receba XMLs, organize a fila e dispare o fluxo fiscal logo na chegada.",
        "href": "/portaria",
        "icon": "fa-door-open",
        "permission": "PAGE_PORTARIA",
        "section": "Logística",
        "tone": "gold",
        "priority": 100,
        "keywords": ["xml", "portaria", "entrada", "recebimento"],
        "metric_key": "recebimento_pendente",
    },
    {
        "id": "documento_entrada",
        "title": "Documento de Entrada",
        "subtitle": "Compras · Fiscal",
        "description": "Importação, auditoria fiscal e lançamento ERP num único fluxo por KPI.",
        "href": "/upload",
        "icon": "fa-file-invoice",
        "permission": ["PAGE_UPLOAD", "PAGE_XML_AUDITOR", "PAGE_LANCAMENTO"],
        "section": "Compras",
        "tone": "orange",
        "priority": 98,
        "keywords": ["pre-nota", "documento", "entrada", "auditor", "xml", "lancamento", "grv", "compra", "importacao"],
        "metric_key": "recebimento_pendente",
    },
    {
        "id": "compras_cps",
        "title": "Compras CPS",
        "subtitle": "Demand planning",
        "description": "Acompanhe OS, GAP de compras, spend baseline e visibilidade de SC/OC.",
        "href": "/compras",
        "icon": "fa-cart-shopping",
        "permission": "PAGE_COMPRAS_CPS",
        "section": "Compras",
        "tone": "orange",
        "priority": 95,
        "keywords": ["compras", "gap", "os", "spend", "sc", "oc"],
        "metric_key": "recebimento_pendente",
    },
    {
        "id": "cadastro_workflow",
        "title": "Workflow de Cadastros",
        "subtitle": "Materiais, clientes e parceiros",
        "description": "Solicite, aprove e acompanhe cadastros ERP com SLA, historico e checklist por etapa.",
        "href": "/cadastros/",
        "icon": "fa-diagram-project",
        "permission": "PAGE_CADASTRO_WORKFLOW",
        "section": "Compras",
        "tone": "teal",
        "priority": 93,
        "keywords": ["cadastro", "workflow", "material", "cliente", "fornecedor", "transportadora"],
        "metric_key": "cadastro_workflow",
    },
    {
        "id": "planejamento_tarefas",
        "title": "Planejamento de Tarefas",
        "subtitle": "Administração",
        "description": "Organize tarefas em colunas, mova por drag-and-drop e acompanhe KPIs do fluxo em tempo real.",
        "href": "/planejamento",
        "icon": "fa-table-columns",
        "permission": "PAGE_PLANEJAMENTO_TAREFAS",
        "section": "Administração",
        "tone": "navy",
        "priority": 73,
        "keywords": ["kanban", "trello", "tarefas", "backlog", "planejamento"],
        "metric_key": "planner_abertas",
    },
    {
        "id": "conferencia",
        "title": "Conferência Cega",
        "subtitle": "Recebimento",
        "description": "Faça a contagem operacional das notas pendentes com rastreabilidade.",
        "href": "/conferencia",
        "icon": "fa-barcode",
        "permission": "PAGE_CONFERENCIA",
        "section": "Logística",
        "tone": "blue",
        "priority": 92,
        "keywords": ["conferencia", "cego", "recebimento", "nota"],
        "metric_key": "recebimento_pendente",
    },
    {
        "id": "notas_liberadas",
        "title": "NF-e Liberadas",
        "subtitle": "Histórico",
        "description": "Acompanhe tudo que já passou por conferência e lançamento.",
        "href": "/fiscal/liberadas",
        "icon": "fa-box-open",
        "tone": "blue",
        "tone": "blue",
        "permission": "PAGE_FISCAL_LIBERADAS",
        "section": "Logística",
        "tone": "blue",
        "priority": 84,
        "keywords": ["historico", "liberadas", "nfe", "fiscal"],
        "metric_key": "notas_lancadas",
    },
    {
        "id": "agendamento_solicitacao",
        "title": "Central de Viagens",
        "subtitle": "Logística • Transportes",
        "description": "Visualize e opere coletas e entregas geradas automaticamente pelo sistema.",
        "href": "/logistica/viagens",
        "icon": "fa-file-circle-plus",
        "permission": "PAGE_LOGISTICA_AGENDAMENTO",
        "section": "Logística",
        "tone": "slate",
        "priority": 88,
        "keywords": ["central", "viagens", "coleta", "entrega", "transporte"],
        "metric_key": "agendamento_ativo",
    },
    {
        "id": "agendamento_veiculos",
        "title": "Central de Viagens",
        "subtitle": "Logística • Transportes",
        "description": "Central gerencial única para coletas e entregas com rastreabilidade por documento de origem.",
        "href": "/logistica/viagens",
        "icon": "fa-truck-fast",
        "permission": "PAGE_LOGISTICA_AGENDAMENTO",
        "section": "Logística",
        "tone": "emerald",
        "priority": 87,
        "keywords": ["central", "viagens", "coleta", "entrega", "transporte"],
        "metric_key": "agendamento_ativo",
    },
    {
        "id": "logistica_mapa_frota",
        "title": "Mapa da Frota",
        "subtitle": "Visao geral",
        "description": "Veja os veiculos no mapa e acesse a viagem em andamento a partir do popup.",
        "href": "/logistica/mapa-frota",
        "icon": "fa-map-location-dot",
        "permission": "PAGE_LOGISTICA_VIAGEM",
        "section": "LogÃ­stica",
        "tone": "cyan",
        "priority": 87,
        "keywords": ["mapa", "frota", "veiculo", "viagem", "gps", "rastreamento"],
        "metric_key": "agendamento_ativo",
    },
    {
        "id": "logistica_viagem",
        "title": "Central de Viagens",
        "subtitle": "Rastreamento ponta-a-ponta",
        "description": "Tela gerencial de coletas e entregas automáticas, com controle operacional e rastreabilidade.",
        "href": "/logistica/viagens",
        "icon": "fa-route",
        "permission": "PAGE_LOGISTICA_VIAGEM",
        "section": "Logística",
        "tone": "emerald",
        "priority": 88,
        "keywords": ["viagem", "rota", "entrega", "coleta", "rastreamento", "gps", "timeline", "motorista"],
        "metric_key": "agendamento_ativo",
    },
    {
        "id": "logistica_inventario_inicial",
        "title": "Modulo de Inventario",
        "subtitle": "Novo, consulta e exportacao",
        "description": "Funcionalidade de inventario da Logistica com criacao de registros, consulta por filtros e exportacao em Excel.",
        "href": "/logistica/inventario",
        "icon": "fa-clipboard-list",
        "permission": "PAGE_LOGISTICA_INVENTARIO",
        "section": "Logística",
        "tone": "cyan",
        "priority": 84,
        "keywords": ["inventario", "tablet", "codigo", "barras", "local", "produto", "logistica"],
        "metric_key": "agendamento_ativo",
    },
    {
        "id": "comex_processos",
        "title": "Processos de Importação/Exportação",
        "subtitle": "OC, PO e acompanhamento",
        "description": "Gestão de processos de comércio exterior: da Ordem de Compra à PO, cotação de frete e acompanhamento até a entrega.",
        "href": "/comex",
        "icon": "fa-ship",
        "permission": "PAGE_COMEX",
        "section": "Comex",
        "tone": "navy",
        "priority": 80,
        "keywords": ["comex", "importacao", "exportacao", "oc", "po", "duimp", "desembaraco", "frete"],
        "metric_key": "comex_ativo",
    },
    {
        "id": "expedicao_conferencia",
        "title": "Registro de Expedição",
        "subtitle": "Saída",
        "description": "Registre a saída, vincule a NF e mantenha a expedição rastreável.",
        "href": "/expedicao/conferencia",
        "icon": "fa-clipboard-check",
        "permission": "PAGE_EXPEDICAO_CONFERENCIA",
        "section": "Logística",
        "tone": "slate",
        "priority": 86,
        "keywords": ["expedicao", "saida", "registro", "embarque"],
        "metric_key": "expedicao_aberta",
    },
    {
        "id": "romaneios",
        "title": "Romaneios",
        "subtitle": "Carga",
        "description": "Monte, revise e acompanhe a separação das cargas do dia.",
        "href": "/expedicao/romaneio",
        "icon": "fa-truck-ramp-box",
        "permission": "PAGE_EXPEDICAO_ROMANEIO",
        "section": "Logística",
        "tone": "slate",
        "priority": 74,
        "keywords": ["romaneio", "carga", "expedicao", "separacao"],
        "metric_key": "expedicao_aberta",
    },
    {
        "id": "classificacao_contabil",
        "title": "Classificacao Contabil",
        "subtitle": "Contabilidade",
        "description": "Revise classificacoes de entradas, ajuste contas e acompanhe pendencias do contador.",
        "href": "/financeiro/classificacao-contabil",
        "icon": "fa-scale-balanced",
        "permission": "PAGE_FINANCEIRO_CLASSIFICACAO_CONTABIL",
        "section": "Controladoria",
        "tone": "red",
        "priority": 84,
        "keywords": ["contabil", "classificacao", "contador", "conta", "entrada"],
        "metric_key": "boletos_gerados",
    },
    {
        "id": "relatorio_custos",
        "title": "Relatorio de Custos",
        "subtitle": "Contabilidade",
        "description": "Consolide custos de insumos produtivos, energia, qualidade e gases com base nos lancamentos reais.",
        "href": "/financeiro/relatorio-custos",
        "icon": "fa-chart-pie",
        "permission": "PAGE_FINANCEIRO_RELATORIO_CUSTOS",
        "section": "Controladoria",
        "tone": "red",
        "priority": 83,
        "keywords": ["custos", "usinagem", "solda", "energia", "qualidade", "oxigenio"],
        "metric_key": "boletos_gerados",
    },
    {
        "id": "emails_nfe",
        "title": "E-mails de NF-e",
        "subtitle": "Administração",
        "description": "Configure envios automaticos e acompanhe historico de e-mails de NF-e.",
        "href": "/faturamento/emails-nfe",
        "icon": "fa-envelope-open-text",
        "permission": "PAGE_ADMIN_EMAILS_NFE",
        "section": "Administração",
        "tone": "navy",
        "priority": 65,
        "keywords": ["email", "nfe", "danfe", "administracao"],
        "metric_key": "sessoes_ativas",
    },
    {
        "id": "dashboard_admin",
        "title": "Painel de Controle",
        "subtitle": "Administração",
        "description": "Tenha visão executiva da operação e gargalos da rotina fiscal.",
        "href": "/admin",
        "icon": "fa-chart-line",
        "permission": "PAGE_ADMIN_DASHBOARD",
        "section": "Administração",
        "tone": "navy",
        "priority": 78,
        "keywords": ["dashboard", "admin", "painel", "controle"],
        "metric_key": "sessoes_ativas",
    },
    {
        "id": "usuarios",
        "title": "Gestão de Acessos",
        "subtitle": "Administração",
        "description": "Administre usuários, perfis e permissões sem perder governança.",
        "href": "/admin/usuarios",
        "icon": "fa-users-cog",
        "permission": "PAGE_ADMIN_USUARIOS",
        "section": "Administração",
        "tone": "navy",
        "priority": 68,
        "keywords": ["usuario", "acesso", "perfil", "permissao"],
        "metric_key": "sessoes_ativas",
    },
    {
        "id": "atualizacoes",
        "title": "Avisos de Atualizações",
        "subtitle": "Administração",
        "description": "Publique novidades do sistema para aparecerem no próximo login dos usuários.",
        "href": "/admin/atualizacoes",
        "icon": "fa-bullhorn",
        "permission": "PAGE_ADMIN_ATUALIZACOES",
        "section": "Administração",
        "tone": "navy",
        "priority": 67,
        "keywords": ["aviso", "atualizacao", "comunicado", "admin"],
        "metric_key": "sessoes_ativas",
    },
    {
        "id": "atualizacoes_cadastrais",
        "title": "Atualizações Cadastrais",
        "subtitle": "Administração",
        "description": "Veja as atualizações de cadastro enviadas por clientes e fornecedores e exporte em Excel.",
        "href": "/admin/atualizacoes-cadastrais",
        "icon": "fa-address-card",
        "permission": "PAGE_ADMIN_ATUALIZACOES_CADASTRAIS",
        "section": "Administração",
        "tone": "navy",
        "priority": 63,
        "keywords": ["cadastro", "atualizacao", "cliente", "fornecedor", "cnpj", "excel"],
        "metric_key": "sessoes_ativas",
    },
]


SECTION_META = {
    "Compras": {
        "description": "Validação fiscal, auditoria e lançamento de documentos.",
        "icon": "fa-file-invoice-dollar",
    },
    "Logística": {
        "description": "Recebimento, expedição, estoque físico, endereçamento e governança do armazém.",
        "icon": "fa-warehouse",
        "tone": "orange",
    },
    "Comex": {
        "description": "Importação e exportação: OC, PO, cotação de frete, desembaraço e transporte.",
        "icon": "fa-ship",
        "tone": "navy",
    },
    "Controladoria": {
        "description": "Classificação contábil e relatório de custos.",
        "icon": "fa-coins",
        "tone": "violet",
    },
    "Administração": {
        "description": "Governança, permissões, dashboards e auditorias.",
        "icon": "fa-user-shield",
        "tone": "slate",
    },
}


def _fmt_metric(value: int | float, singular: str, plural: str | None = None) -> str:
    plural = plural or singular
    label = singular if abs(value) == 1 else plural
    return f"{int(value) if float(value).is_integer() else value} {label}"


def _build_home_metrics() -> dict:
    today = datetime.now().date()
    metrics = {
        "materiais_expedidos": 0,
        "materiais_recebidos": 0,
        "recebimento_pendente": 0,
        "notas_concluidas": 0,
        "notas_lancadas": 0,
        "auditoria_pendente": 0,
        "planner_abertas": 0,
        "agendamento_ativo": 0,
        "expedicao_aberta": 0,
        "sessoes_ativas": 0,
        "importadas_hoje": 0,
        "lancadas_hoje": 0,
        "comex_ativo": 0,
        "cadastro_pendente": 0,
        "viagens_em_andamento": 0,
    }
    try:
        metrics["recebimento_pendente"] = (
            db.session.query(func.count(func.distinct(ItemNota.numero_nota)))
            .filter(ItemNota.status == "Pendente")
            .scalar()
            or 0
        )
        metrics["notas_concluidas"] = (
            db.session.query(func.count(func.distinct(ItemNota.numero_nota)))
            .filter(ItemNota.status == "Concluído")
            .scalar()
            or 0
        )
        metrics["notas_lancadas"] = (
            db.session.query(func.count(func.distinct(ItemNota.numero_nota)))
            .filter(ItemNota.status == "Lançado")
            .scalar()
            or 0
        )
        metrics["materiais_recebidos"] = int(metrics["notas_lancadas"])
        metrics["materiais_expedidos"] = (
            db.session.query(func.count(func.distinct(ExpedicaoConferenciaSimples.numero_nf)))
            .filter(
                ExpedicaoConferenciaSimples.status.in_(["Expedido", "Finalizado"]),
                ExpedicaoConferenciaSimples.numero_nf.isnot(None),
                ExpedicaoConferenciaSimples.numero_nf != "",
            )
            .scalar()
            or 0
        )
        metrics["auditoria_pendente"] = (
            db.session.query(func.count(func.distinct(ItemNota.numero_nota)))
            .filter(ItemNota.auditor_decisao == "PendenteDecisao")
            .scalar()
            or 0
        )
        metrics["planner_abertas"] = (
            db.session.query(func.count(PlannerCard.id))
            .join(PlannerColumn, PlannerColumn.id == PlannerCard.column_id)
            .filter(PlannerColumn.is_done.is_(False))
            .scalar()
            or 0
        )
        metrics["agendamento_ativo"] = (
            AgendamentoSolicitacao.query
            .filter(AgendamentoSolicitacao.status.notin_(["Concluida", "Cancelada"]))
            .count()
        )
        metrics["viagens_em_andamento"] = (
            Viagem.query
            .filter(Viagem.status == "EmAndamento")
            .count()
        )
        metrics["expedicao_aberta"] = (
            ExpedicaoConferencia.query
            .filter(ExpedicaoConferencia.status.in_(["Aberta", "PendenteDecisao"]))
            .count()
        ) + (
            ExpedicaoConferenciaSimples.query
            .filter(ExpedicaoConferenciaSimples.status == "Pendente de expedição")
            .count()
        )
        metrics["sessoes_ativas"] = ActiveSession.query.filter_by(is_active=True).count()
        metrics["comex_ativo"] = (
            ComexProcesso.query.filter(ComexProcesso.processo_concluido_em.is_(None)).count()
        )
        metrics["importadas_hoje"] = (
            db.session.query(func.count(func.distinct(ItemNota.numero_nota)))
            .filter(func.date(ItemNota.data_importacao) == today)
            .scalar()
            or 0
        )
        metrics["lancadas_hoje"] = (
            db.session.query(func.count(func.distinct(ItemNota.numero_nota)))
            .filter(ItemNota.status == "Lançado", func.date(ItemNota.data_lancamento) == today)
            .scalar()
            or 0
        )
        metrics["cadastro_pendente"] = (
            CadastroWorkflowSolicitacao.query
            .filter(CadastroWorkflowSolicitacao.status.notin_(["Finalizada", "Cancelada"]))
            .count()
        )
    except Exception:
        db.session.rollback()

    return metrics


def _build_available_modules(metrics: dict) -> list[dict]:
    perms = get_effective_permissions()
    modules = []

    for item in HOME_MODULES:
        permission = item["permission"]
        if isinstance(permission, (list, tuple)):
            liberado = any(perms.get(key, False) for key in permission)
        else:
            liberado = perms.get(permission, False)
        if not liberado:
            continue

        metric_value = metrics.get(item.get("metric_key") or "", 0)
        module = dict(item)
        module["search_blob"] = " ".join(
            [
                item["title"],
                item["subtitle"],
                item["section"],
                item["description"],
                " ".join(item.get("keywords", [])),
            ]
        ).lower()
        module["metric_value"] = metric_value
        module["metric_label"] = _metric_label_for_module(module["id"], metric_value)
        module["href_short"] = item["href"].replace("/", " / ").strip()
        modules.append(module)

    return sorted(modules, key=lambda row: (-row["priority"], row["title"]))


def _metric_label_for_module(module_id: str, value: int | float) -> str:
    mapping = {
        "portaria": _fmt_metric(value, "NF pendente", "NFs pendentes"),
        "documento_entrada": _fmt_metric(value, "NF na fila", "NFs na fila"),
        "planejamento_tarefas": _fmt_metric(value, "tarefa aberta", "tarefas abertas"),
        "conferencia": _fmt_metric(value, "nota aguardando", "notas aguardando"),
        "notas_liberadas": _fmt_metric(value, "NF lançada", "NFs lançadas"),
        "expedicao_conferencia": _fmt_metric(value, "operação aberta", "operações abertas"),
        "romaneios": _fmt_metric(value, "operação aberta", "operações abertas"),
        "emails_nfe": _fmt_metric(value, "sessão ativa", "sessões ativas"),
        "dashboard_admin": _fmt_metric(value, "sessão ativa", "sessões ativas"),
        "usuarios": _fmt_metric(value, "sessão ativa", "sessões ativas"),
        "historico": _fmt_metric(value, "sessão ativa", "sessões ativas"),
        "auditoria_acessos": _fmt_metric(value, "sessão ativa", "sessões ativas"),
    }
    mapping["agendamento_veiculos"] = _fmt_metric(value, "roteiro ativo", "roteiros ativos")
    return mapping.get(module_id, _fmt_metric(value, "item", "itens"))


def _group_modules(modules: list[dict]) -> list[dict]:
    grouped = []
    for section, meta in SECTION_META.items():
        section_modules = [module for module in modules if module["section"] == section]
        if not section_modules:
            continue
        grouped.append(
            {
                "name": section,
                "description": meta["description"],
                "icon": meta["icon"],
                "tone": meta.get("tone", "blue"),
                "modules": section_modules,
            }
        )
    return grouped


def _build_home_highlights(metrics: dict) -> list[dict]:
    return [
        {
            "label": "Notas aguardando recebimento",
            "value": int(metrics["recebimento_pendente"]),
            "caption": "Fila de entrada ainda não conferida.",
            "tone": "gold",
        },
        {
            "label": "Notas prontas para documento",
            "value": int(metrics["notas_concluidas"]),
            "caption": "Conferidas e aguardando lançamento.",
            "tone": "orange",
        },
    ]


def _build_priority_actions(modules: list[dict], metrics: dict) -> list[dict]:
    actions = []
    today = datetime.now().strftime("%d/%m/%Y")

    def add_action(module_id: str, title: str, text: str):
        module = next((item for item in modules if item["id"] == module_id), None)
        if module:
            actions.append(
                {
                    "module_id": module["id"],
                    "title": title,
                    "text": text,
                    "href": module["href"],
                    "icon": module["icon"],
                }
            )

    if metrics["notas_concluidas"] > 0:
        add_action("documento_entrada", "Fechar documento de entrada", f"{int(metrics['notas_concluidas'])} nota(s) prontas para lançamento.")
    if metrics["auditoria_pendente"] > 0:
        add_action("auditor_xml", "Tratar auditoria fiscal", f"{int(metrics['auditoria_pendente'])} nota(s) aguardando decisão fiscal.")

    if not actions:
        add_action("notas_liberadas", "Acompanhar histórico", f"Operação está estável em {today}. Use o painel para análise.")

    return actions[:4]


def _build_user_context(metrics: dict) -> dict:
    username = session.get("username", "Usuário")
    role = session.get("role", "Operação")
    role_display = "Controladoria" if role == "Financeiro" else role
    ultima_sessao = (
        ActiveSession.query
        .filter(ActiveSession.username == username)
        .order_by(ActiveSession.last_activity.desc())
        .first()
    )
    return {
        "username": username,
        "role": role_display,
        "active_sessions": int(metrics["sessoes_ativas"]),
        "importadas_hoje": int(metrics["importadas_hoje"]),
        "lancadas_hoje": int(metrics["lancadas_hoje"]),
        "last_seen": ultima_sessao.last_activity.strftime("%d/%m/%Y %H:%M") if ultima_sessao and ultima_sessao.last_activity else "Agora",
    }


# ---------------------------------------------------------------------------
# Dashboard inicial (home): indicadores e gráficos variáveis por permissão.
# Cada card/gráfico só aparece se o usuário tem acesso à área correspondente.
# WMS fica de fora de propósito.
# ---------------------------------------------------------------------------
_PERM_RECEBIMENTO = ("PAGE_CONFERENCIA", "PAGE_PORTARIA", "PAGE_FISCAL_LIBERADAS")
_PERM_COMPRAS = ("PAGE_UPLOAD", "PAGE_XML_AUDITOR", "PAGE_LANCAMENTO")
_PERM_EXPEDICAO = ("PAGE_EXPEDICAO_CONFERENCIA", "PAGE_EXPEDICAO_CONF_CEGA", "PAGE_EXPEDICAO_ROMANEIO")
_PERM_LOGISTICA = ("PAGE_LOGISTICA_AGENDAMENTO", "PAGE_LOGISTICA_SOLICITACAO", "PAGE_LOGISTICA_VIAGEM")
_PERM_CONTROLADORIA = (
    "PAGE_FINANCEIRO_CLASSIFICACAO_CONTABIL",
    "PAGE_FINANCEIRO_RELATORIO_CUSTOS",
)


def _serie_entradas_por_dia(dias: int = 14) -> dict:
    """Série diária de notas que entraram (por data de importação)."""
    today = datetime.now().date()
    inicio = today - timedelta(days=dias - 1)
    contagem: dict[str, int] = {}
    try:
        rows = (
            db.session.query(
                func.date(ItemNota.data_importacao),
                func.count(func.distinct(ItemNota.numero_nota)),
            )
            .filter(func.date(ItemNota.data_importacao) >= inicio)
            .group_by(func.date(ItemNota.data_importacao))
            .all()
        )
        for dia, qtd in rows:
            contagem[str(dia)] = int(qtd or 0)
    except Exception:
        db.session.rollback()

    labels, valores = [], []
    for i in range(dias):
        d = inicio + timedelta(days=i)
        labels.append(d.strftime("%d/%m"))
        valores.append(contagem.get(str(d), 0))
    return {"labels": labels, "valores": valores}


def _serie_fluxo_notas_por_dia(dias: int = 14) -> dict:
    """Série diária com entrada de NFs e lançamentos concluídos no ERP."""
    today = datetime.now().date()
    inicio = today - timedelta(days=dias - 1)
    importadas: dict[str, int] = {}
    lancadas: dict[str, int] = {}

    try:
        rows_import = (
            db.session.query(
                func.date(ItemNota.data_importacao),
                func.count(func.distinct(ItemNota.numero_nota)),
            )
            .filter(func.date(ItemNota.data_importacao) >= inicio)
            .group_by(func.date(ItemNota.data_importacao))
            .all()
        )
        for dia, qtd in rows_import:
            importadas[str(dia)] = int(qtd or 0)

        rows_lanc = (
            db.session.query(
                func.date(ItemNota.data_lancamento),
                func.count(func.distinct(ItemNota.numero_nota)),
            )
            .filter(ItemNota.status == "Lançado", func.date(ItemNota.data_lancamento) >= inicio)
            .group_by(func.date(ItemNota.data_lancamento))
            .all()
        )
        for dia, qtd in rows_lanc:
            lancadas[str(dia)] = int(qtd or 0)
    except Exception:
        db.session.rollback()

    labels = []
    serie_importadas = []
    serie_lancadas = []
    for i in range(dias):
        d = inicio + timedelta(days=i)
        chave = str(d)
        labels.append(d.strftime("%d/%m"))
        serie_importadas.append(importadas.get(chave, 0))
        serie_lancadas.append(lancadas.get(chave, 0))

    return {
        "labels": labels,
        "importadas": serie_importadas,
        "lancadas": serie_lancadas,
    }


def _dist_expedicao_status() -> dict:
    """Distribuição de expedições por status."""
    dados: dict[str, int] = {}
    try:
        rows = (
            db.session.query(ExpedicaoConferencia.status, func.count(ExpedicaoConferencia.id))
            .group_by(ExpedicaoConferencia.status)
            .all()
        )
        for status, qtd in rows:
            dados[str(status or "—")] = int(qtd or 0)
    except Exception:
        db.session.rollback()
    return dados


def _build_dashboard(metrics: dict) -> list[dict]:
    perms = get_effective_permissions()

    def ok(keys) -> bool:
        return any(perms.get(k, False) for k in keys)

    raw_sections = [
        {
            "name": "Operação agora",
            "icon": "fa-bolt",
            "cards": [
                {
                    "perms": _PERM_EXPEDICAO,
                    "label": "Materiais expedidos",
                    "value": metrics["materiais_expedidos"],
                    "caption": "Notas fiscais com status finalizado no Registro de Expedição.",
                    "icon": "fa-box-open",
                    "href": "/expedicao/conferencia",
                    "tone": "emerald",
                },
                {
                    "perms": ("PAGE_UPLOAD", "PAGE_XML_AUDITOR", "PAGE_LANCAMENTO", "PAGE_FISCAL_LIBERADAS"),
                    "label": "Materiais recebidos",
                    "value": metrics["materiais_recebidos"],
                    "caption": "Notas fiscais com status lançado no Documento de Entrada.",
                    "icon": "fa-file-circle-check",
                    "href": "/upload",
                    "tone": "blue",
                },
                {
                    "perms": _PERM_EXPEDICAO,
                    "label": "Materiais com pendência de expedição",
                    "value": metrics["expedicao_aberta"],
                    "caption": "Pendentes na conferência de expedição.",
                    "icon": "fa-boxes-packing",
                    "href": "/expedicao/conferencia-cega",
                    "tone": "amber",
                },
                {
                    "perms": ("PAGE_CONFERENCIA", "PAGE_PORTARIA"),
                    "label": "Materiais com pendência de recebimento",
                    "value": metrics["recebimento_pendente"],
                    "caption": "Pendentes na conferência de recebimento.",
                    "icon": "fa-inbox",
                    "href": "/conferencia",
                    "tone": "violet",
                },
                {
                    "perms": _PERM_LOGISTICA,
                    "label": "Viagens sendo realizadas",
                    "value": metrics["viagens_em_andamento"],
                    "caption": "Viagens com status EmAndamento.",
                    "icon": "fa-truck-fast",
                    "href": "/logistica/viagens",
                    "tone": "teal",
                },
                {
                    "perms": ("PAGE_CADASTRO_WORKFLOW",),
                    "label": "Cadastros aguardando validação",
                    "value": metrics["cadastro_pendente"],
                    "caption": "Cadastros com status diferente de concluído.",
                    "icon": "fa-diagram-project",
                    "href": "/cadastros/",
                    "tone": "sky",
                },
            ],
        }
    ]

    sections = []
    for sec in raw_sections:
        cards = [c for c in sec["cards"] if ok(c["perms"])]
        if cards:
            sections.append({"name": sec["name"], "icon": sec["icon"], "cards": cards})
    return sections


def _build_dashboard_charts(metrics: dict) -> dict:
    perms = get_effective_permissions()

    def ok(keys) -> bool:
        return any(perms.get(k, False) for k in keys)

    charts: dict = {}
    if ok(_PERM_RECEBIMENTO + _PERM_COMPRAS):
        charts["fluxo_notas"] = _serie_fluxo_notas_por_dia()
        charts["recebido_vs_expedido"] = {
            "Materiais recebidos": int(metrics["materiais_recebidos"]),
            "Materiais expedidos": int(metrics["materiais_expedidos"]),
        }
    if ok(_PERM_RECEBIMENTO + _PERM_EXPEDICAO + ("PAGE_CADASTRO_WORKFLOW",)):
        charts["pendencias_chave"] = {
            "Pendência recebimento": int(metrics["recebimento_pendente"]),
            "Pendência expedição": int(metrics["expedicao_aberta"]),
            "Cadastros aguardando validação": int(metrics["cadastro_pendente"]),
        }
    return charts


@page_bp.route("/")
@login_required
def home():
    metrics = _build_home_metrics()
    sections = _build_dashboard(metrics)
    total_indicadores = sum(len(sec["cards"]) for sec in sections)
    return render_template(
        "dashboard_home.html",
        user=session.get("username", "Usuário"),
        user_context=_build_user_context(metrics),
        dashboard_sections=sections,
        dashboard_charts=_build_dashboard_charts(metrics),
        total_indicadores=total_indicadores,
        home_metrics=metrics,
    )


@page_bp.route("/perfil")
@login_required
def perfil():
    username = session.get("username", "")
    usuario = Usuario.query.filter_by(username=username).first()
    role = session.get("role", "Operação")
    role_display = "Controladoria" if role == "Financeiro" else role

    current_session_id = session.get("session_id")
    sessoes_raw = (
        ActiveSession.query
        .filter_by(username=username, is_active=True)
        .order_by(ActiveSession.last_activity.desc())
        .all()
    )
    sessoes = [
        {
            "is_current": s.session_id == current_session_id,
            "device": _friendly_device(getattr(s, "user_agent", None)),
            "ip": getattr(s, "ip_address", None) or "—",
            "created_at": s.created_at.strftime("%d/%m/%Y %H:%M") if s.created_at else "—",
            "last_activity": s.last_activity.strftime("%d/%m/%Y %H:%M") if s.last_activity else "—",
        }
        for s in sessoes_raw
    ]

    return render_template(
        "perfil.html",
        usuario=usuario,
        role_display=role_display,
        sessoes=sessoes,
        outras_sessoes=sum(1 for s in sessoes if not s["is_current"]),
    )


def _friendly_device(user_agent: str | None) -> str:
    ua = (user_agent or "").lower()
    if not ua:
        return "Dispositivo desconhecido"
    if "android" in ua:
        so = "Android"
    elif "iphone" in ua or "ipad" in ua or "ios" in ua:
        so = "iOS"
    elif "windows" in ua:
        so = "Windows"
    elif "mac os" in ua or "macintosh" in ua:
        so = "macOS"
    elif "linux" in ua:
        so = "Linux"
    else:
        so = "Outro sistema"

    if "edg" in ua:
        nav = "Edge"
    elif "chrome" in ua and "chromium" not in ua:
        nav = "Chrome"
    elif "firefox" in ua:
        nav = "Firefox"
    elif "safari" in ua:
        nav = "Safari"
    else:
        nav = "Navegador"
    return f"{nav} · {so}"


@page_bp.route("/conferencia")
@permission_required("PAGE_CONFERENCIA")
def conferencia_page():
    return render_template("conferente.html", user=session["username"])


@page_bp.route("/portaria")
@permission_required("PAGE_PORTARIA")
def portaria_page():
    return render_template("portaria.html", user=session["username"])


@page_bp.route("/admin")
@permission_required("PAGE_ADMIN_DASHBOARD")
def dashboard():
    return render_template("dashboard.html", user=session["username"])


@page_bp.route("/upload")
@permission_required_any("PAGE_UPLOAD", "PAGE_XML_AUDITOR", "PAGE_LANCAMENTO")
def upload_page():
    is_admin = session.get("role") == "Admin"
    return render_template("documento_entrada.html", user=session["username"], is_admin=is_admin)


@page_bp.route("/compras/auditor-xml")
@permission_required("PAGE_XML_AUDITOR")
def compras_auditor_xml_page():
    nota = request.args.get("nota", "")
    destino = url_for("pages.upload_page", stage="auditoria", nota=nota) if nota else url_for("pages.upload_page", stage="auditoria")
    return redirect(destino)


@page_bp.route("/lancamento")
@permission_required("PAGE_LANCAMENTO")
def lancamento_page():
    return redirect(url_for("pages.upload_page", stage="lancamento"))


@page_bp.route("/fiscal/liberadas")
@permission_required("PAGE_FISCAL_LIBERADAS")
def fiscal_liberadas_page():
    return render_template("notas_liberadas.html", user=session.get("username", "Fiscal"))


@page_bp.route("/financeiro/classificacao-contabil")
@permission_required("PAGE_FINANCEIRO_CLASSIFICACAO_CONTABIL")
def financeiro_classificacao_contabil_page():
    return render_template("classificacao_contabil.html", user=session["username"])


@page_bp.route("/financeiro/relatorio-custos")
@permission_required("PAGE_FINANCEIRO_RELATORIO_CUSTOS")
def financeiro_relatorio_custos_page():
    return render_template("relatorio_custos.html", user=session["username"])


@page_bp.route("/expedicao/conferencia")
@permission_required("PAGE_EXPEDICAO_CONFERENCIA")
def expedicao_conferencia_page():
    return render_template(
        "expedicao_conferencia_simples.html",
        user=session["username"],
        user_role=session.get("role", ""),
        is_admin=session.get("role") == "Admin",
    )


@page_bp.route("/logistica/solicitar-transporte")
@permission_required("PAGE_LOGISTICA_SOLICITACAO")
def solicitar_transporte_page():
    return render_template(
        "agendamento_solicitacao.html",
        user=session["username"],
        user_role=session.get("role", ""),
        is_admin=session.get("role") == "Admin",
    )


@page_bp.route("/logistica/agendamento-veiculos")
@permission_required("PAGE_LOGISTICA_AGENDAMENTO")
def agendamento_veiculos_page():
    return redirect("/logistica/viagens", code=302)


@page_bp.route("/logistica/operacao")
def logistica_operacao_page():
    from conferencia_app.auth import has_permission
    has_agend = has_permission("PAGE_LOGISTICA_AGENDAMENTO")
    has_viagem = has_permission("PAGE_LOGISTICA_VIAGEM")
    has_frota = has_permission("PAGE_LOGISTICA_FROTA")
    if not (has_agend or has_viagem or has_frota):
        from flask import abort
        abort(403)
    # Rotas foi absorvida por viagens; redireciona quem não tem frota
    if not has_frota:
        return redirect("/logistica/viagens" + ("?tab=solicitacoes" if has_agend else ""), code=302)
    return render_template(
        "logistica_operacao.html",
        user=session["username"],
        user_role=session.get("role", ""),
        is_admin=session.get("role") == "Admin",
    )


@page_bp.route("/logistica/painel-motorista")
def painel_motorista_page():
    # Rota legada. O painel antigo (login + sessão) foi substituído por link público
    # permanente HMAC compartilhado via WhatsApp/QR pelo gestor.
    return redirect("/logistica/operacao#frota", code=302)


@page_bp.route("/logistica/rastreamento")
@permission_required("PAGE_LOGISTICA_RASTREAMENTO")
def rastreamento_page():
    return render_template(
        "rastreamento.html",
        user=session["username"],
        user_role=session.get("role", ""),
        is_admin=session.get("role") == "Admin",
    )


@page_bp.route("/logistica/mapa-frota")
@permission_required("PAGE_LOGISTICA_VIAGEM")
def mapa_frota_page():
    return render_template(
        "mapa_frota.html",
        user=session["username"],
        user_role=session.get("role", ""),
        is_admin=session.get("role") == "Admin",
    )


@page_bp.route("/logistica/frota")
@permission_required("PAGE_LOGISTICA_FROTA")
def frota_page():
    return render_template(
        "frota.html",
        user=session["username"],
        user_role=session.get("role", ""),
        is_admin=session.get("role") == "Admin",
    )


@page_bp.route("/logistica/viagens")
@login_required
def viagens_page():
    from conferencia_app.auth import has_permission
    if not (has_permission("PAGE_LOGISTICA_VIAGEM") or has_permission("PAGE_LOGISTICA_AGENDAMENTO")):
        return render_template("acesso_negado.html", user=session.get("username")), 403
    return render_template(
        "viagens_central.html",
        user=session["username"],
        user_role=session.get("role", ""),
        is_admin=session.get("role") == "Admin",
    )


@page_bp.route("/logistica/recebimento-calendario")
@login_required
def recebimento_calendario_page():
    from conferencia_app.auth import has_permission

    if not (has_permission("PAGE_LOGISTICA_AGENDAMENTO") or has_permission("PAGE_LOGISTICA_SOLICITACAO")):
        return render_template("acesso_negado.html", user=session.get("username")), 403
    return render_template(
        "recebimento_calendario.html",
        user=session["username"],
        user_role=session.get("role", ""),
        is_admin=session.get("role") == "Admin",
    )




@page_bp.route("/admin/usuarios")
@permission_required("PAGE_ADMIN_USUARIOS")
def usuarios_page():
    return render_template("usuarios.html", user=session["username"])


@page_bp.route("/admin/atualizacoes")
@permission_required("PAGE_ADMIN_ATUALIZACOES")
def atualizacoes_admin_page():
    return render_template("admin_atualizacoes.html", user=session["username"])


@page_bp.route("/admin/atualizacoes-cadastrais")
@permission_required("PAGE_ADMIN_ATUALIZACOES_CADASTRAIS")
def atualizacoes_cadastrais_admin_page():
    return render_template("admin_atualizacoes_cadastrais.html", user=session["username"])
