from datetime import datetime

from flask import Blueprint, redirect, render_template, request, session, url_for
from sqlalchemy import func

from ..auth import get_effective_permissions, login_required, permission_required, permission_required_any
from ..extensions import db
from ..models import (
    ActiveSession,
    AgendamentoSolicitacao,
    BoletoContaReceber,
    ExpedicaoConferencia,
    ExpedicaoConferenciaSimples,
    ItemNota,
    PlannerCard,
    PlannerColumn,
    Usuario,
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
        "title": "Solicitar Coleta ou Entrega",
        "subtitle": "Abertura rápida",
        "description": "Abra uma demanda em poucos passos e acompanhe o andamento sem entrar no planner da logística.",
        "href": "/logistica/solicitar-transporte",
        "icon": "fa-file-circle-plus",
        "permission": "PAGE_LOGISTICA_SOLICITACAO",
        "section": "Logística",
        "tone": "slate",
        "priority": 88,
        "keywords": ["solicitacao", "coleta", "entrega", "pedido", "transporte"],
        "metric_key": "agendamento_ativo",
    },
    {
        "id": "agendamento_veiculos",
        "title": "Gestão de Viagens",
        "subtitle": "Solicitações & planner logístico",
        "description": "Gerencie solicitações de transporte, aloque motorista e veículo, acompanhe agenda e cadastros.",
        "href": "/logistica/viagens?tab=solicitacoes",
        "icon": "fa-truck-fast",
        "permission": "PAGE_LOGISTICA_AGENDAMENTO",
        "section": "Logística",
        "tone": "emerald",
        "priority": 87,
        "keywords": ["agendamento", "kanban", "veiculo", "coleta", "entrega", "solicitacao"],
        "metric_key": "agendamento_ativo",
    },
    {
        "id": "logistica_rastreamento",
        "title": "Rastreamento de Veículos",
        "subtitle": "Mapa ao vivo",
        "description": "Acompanhe em tempo real onde estão os veículos da frota, direto do GPS do motorista em viagem.",
        "href": "/logistica/rastreamento",
        "icon": "fa-location-crosshairs",
        "permission": "PAGE_LOGISTICA_RASTREAMENTO",
        "section": "Logística",
        "tone": "cyan",
        "priority": 86,
        "keywords": ["rastreamento", "gps", "mapa", "veiculo", "frota", "tempo real"],
        "metric_key": "agendamento_ativo",
    },
    {
        "id": "logistica_frota",
        "title": "Gestão de Frota",
        "subtitle": "Documentos, manutenção e consumo",
        "description": "Controle documentos, manutenções, abastecimentos, multas e checklist diário dos veículos.",
        "href": "/logistica/frota",
        "icon": "fa-truck-field",
        "permission": "PAGE_LOGISTICA_FROTA",
        "section": "Logística",
        "tone": "amber",
        "priority": 85,
        "keywords": ["frota", "manutenção", "abastecimento", "multa", "cnh", "crlv", "checklist"],
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
        "title": "Gestão de Viagens",
        "subtitle": "Rastreamento ponta-a-ponta",
        "description": "Planeje, acompanhe em tempo real e consolide cada viagem: paradas, GPS, ocorrências, abastecimento e relatório final.",
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
        "id": "faturamento",
        "title": "Faturamento",
        "subtitle": "Contas a receber",
        "description": "Emita documentos fiscais e acompanhe o fluxo financeiro de saída.",
        "href": "/financeiro/faturamento",
        "icon": "fa-file-invoice-dollar",
        "tone": "navy",
        "tone": "navy",
        "permission": "PAGE_FINANCEIRO_FATURAMENTO",
        "section": "Controladoria",
        "tone": "red",
        "priority": 82,
        "keywords": ["faturamento", "nota", "controladoria", "emissao"],
        "metric_key": "boletos_gerados",
    },
    {
        "id": "contas_receber",
        "title": "Contas a Receber",
        "subtitle": "Contas a receber",
        "description": "Acompanhe títulos, boletos e a saúde do recebimento financeiro.",
        "href": "/financeiro/contas-receber",
        "icon": "fa-wallet",
        "permission": "PAGE_FINANCEIRO_CONTAS_RECEBER",
        "section": "Controladoria",
        "tone": "red",
        "priority": 80,
        "keywords": ["contas", "receber", "controladoria", "boleto"],
        "metric_key": "boletos_gerados",
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
        "id": "consulta_boletos",
        "title": "Consulta de Boletos",
        "subtitle": "Contas a receber",
        "description": "Acesse a consulta externa de boletos disponivel para clientes.",
        "href": "/boletos",
        "icon": "fa-barcode",
        "permission": "PAGE_FINANCEIRO_CONTAS_RECEBER",
        "section": "Controladoria",
        "tone": "red",
        "priority": 79,
        "keywords": ["boleto", "consulta", "cliente", "controladoria"],
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
    "Controladoria": {
        "description": "Contas a receber, faturamento, boletos e classificação contábil.",
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
        "recebimento_pendente": 0,
        "notas_concluidas": 0,
        "notas_lancadas": 0,
        "auditoria_pendente": 0,
        "planner_abertas": 0,
        "agendamento_ativo": 0,
        "expedicao_aberta": 0,
        "boletos_gerados": 0,
        "sessoes_ativas": 0,
        "importadas_hoje": 0,
        "lancadas_hoje": 0,
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
        metrics["expedicao_aberta"] = (
            ExpedicaoConferencia.query
            .filter(ExpedicaoConferencia.status.in_(["Aberta", "PendenteDecisao"]))
            .count()
        ) + (
            ExpedicaoConferenciaSimples.query
            .filter(ExpedicaoConferenciaSimples.status == "Pendente de expedição")
            .count()
        )
        metrics["boletos_gerados"] = BoletoContaReceber.query.filter_by(status="Gerado").count()
        metrics["sessoes_ativas"] = ActiveSession.query.filter_by(is_active=True).count()
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
        "faturamento": _fmt_metric(value, "boleto gerado", "boletos gerados"),
        "contas_receber": _fmt_metric(value, "boleto gerado", "boletos gerados"),
        "consulta_boletos": _fmt_metric(value, "boleto gerado", "boletos gerados"),
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


@page_bp.route("/")
@login_required
def home():
    metrics = _build_home_metrics()
    modules = _build_available_modules(metrics)
    sections = _group_modules(modules)
    return render_template(
        "menu_principal.html",
        user=session.get("username", "Usuário"),
        user_context=_build_user_context(metrics),
        home_sections=sections,
        home_modules=modules,
        home_highlights=_build_home_highlights(metrics),
        priority_actions=_build_priority_actions(modules, metrics),
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


@page_bp.route("/financeiro/faturamento")
@permission_required("PAGE_FINANCEIRO_FATURAMENTO")
def financeiro_faturamento_page():
    return render_template("faturamento.html", user=session["username"])


@page_bp.route("/financeiro/contas-receber")
@permission_required("PAGE_FINANCEIRO_CONTAS_RECEBER")
def financeiro_contas_receber_page():
    return render_template("contas_receber.html", user=session["username"])


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
    return redirect("/logistica/viagens?tab=viagens", code=302)


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
        "viagens.html",
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
