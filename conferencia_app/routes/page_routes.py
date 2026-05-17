from datetime import datetime

from flask import Blueprint, render_template, session
from sqlalchemy import func

from ..auth import get_effective_permissions, login_required, permission_required
from ..extensions import db
from ..models import (
    ActiveSession,
    AgendamentoSolicitacao,
    BoletoContaReceber,
    ConsertoBaixa,
    ConsertoEstoque,
    ExpedicaoConferencia,
    ExpedicaoConferenciaSimples,
    ItemNota,
    ItemWMS,
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
        "id": "pre_nota",
        "title": "Pré-Nota de Entrada",
        "subtitle": "Compras",
        "description": "Central de importação, conferência inicial e preparo para auditoria.",
        "href": "/upload",
        "icon": "fa-file-import",
        "permission": "PAGE_UPLOAD",
        "section": "Compras",
        "tone": "orange",
        "priority": 96,
        "keywords": ["pre-nota", "compra", "importacao", "xml"],
        "metric_key": "recebimento_pendente",
    },
    {
        "id": "auditor_xml",
        "title": "Auditor XML",
        "subtitle": "Fiscal",
        "description": "Valide pedido, tributos e inconsistências antes de liberar a nota.",
        "href": "/compras/auditor-xml",
        "icon": "fa-shield-halved",
        "permission": "PAGE_XML_AUDITOR",
        "section": "Compras",
        "tone": "orange",
        "priority": 94,
        "keywords": ["auditor", "xml", "pedido", "fiscal"],
        "metric_key": "auditoria_pendente",
    },
    {
        "id": "documento_entrada",
        "title": "Documento de Entrada",
        "subtitle": "Fiscal",
        "description": "Feche o lançamento no GRV, manifeste e finalize a operação sem ruído.",
        "href": "/lancamento",
        "icon": "fa-file-signature",
        "permission": "PAGE_LANCAMENTO",
        "section": "Compras",
        "tone": "orange",
        "priority": 98,
        "keywords": ["documento", "entrada", "grv", "lancamento"],
        "metric_key": "notas_concluidas",
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
        "id": "etiquetas",
        "title": "Etiquetas",
        "subtitle": "Operação",
        "description": "Imprima etiquetas com velocidade e controle sobre reimpressões.",
        "href": "/recebimento/etiquetas",
        "icon": "fa-tags",
        "permission": "PAGE_ETIQUETAS",
        "section": "Logística",
        "tone": "teal",
        "priority": 76,
        "keywords": ["etiqueta", "impressao", "recebimento"],
        "metric_key": "notas_lancadas",
    },
    {
        "id": "conserto",
        "title": "Controle de Conserto",
        "subtitle": "Estoque especial",
        "description": "Acompanhe saldos enviados, retornos, sugestões e confirmações de baixa.",
        "href": "/conserto",
        "icon": "fa-screwdriver-wrench",
        "permission": "PAGE_CONSERTO",
        "section": "Logística",
        "tone": "violet",
        "priority": 90,
        "keywords": ["conserto", "retorno", "estoque", "baixa"],
        "metric_key": "conserto_pendente",
    },
    {
        "id": "wms",
        "title": "WMS",
        "subtitle": "Central de operações",
        "description": "Painel único de recebimento, endereçamento, estoque e governança do armazém.",
        "href": "/wms",
        "icon": "fa-warehouse",
        "permission": "PAGE_WMS",
        "section": "Logística",
        "tone": "cyan",
        "priority": 90,
        "keywords": ["wms", "estoque", "endereco", "armazem", "hub"],
        "metric_key": "wms_pendente",
    },
    {
        "id": "wms_enderecamento",
        "title": "Endereçamento WMS",
        "subtitle": "Operação",
        "description": "Endereça pendências de entrada fiscal às posições do armazém.",
        "href": "/wms/enderecamento",
        "icon": "fa-map-marker-alt",
        "permission": "PAGE_WMS",
        "section": "Logística",
        "tone": "cyan",
        "priority": 87,
        "keywords": ["wms", "enderecamento", "pendencia", "nota", "armazem"],
        "metric_key": "wms_pendente",
    },
    {
        "id": "wms_enderecos",
        "title": "Cadastro de Endereços WMS",
        "subtitle": "Admin logística",
        "description": "Mantenha o mapa mestre do armazém organizado e consistente.",
        "href": "/admin/wms-enderecos",
        "icon": "fa-map-location-dot",
        "permission": "PAGE_ADMIN_WMS_ENDERECOS",
        "section": "Logística",
        "tone": "cyan",
        "priority": 63,
        "keywords": ["wms", "endereco", "cadastro", "armazem"],
        "metric_key": "wms_pendente",
    },
    {
        "id": "wms_governanca",
        "title": "Governança WMS",
        "subtitle": "Gestão",
        "description": "Monitore parâmetros, divergências e capacidade operacional do armazém.",
        "href": "/admin/wms-governanca",
        "icon": "fa-sitemap",
        "permission": "PAGE_ADMIN_WMS_GOVERNANCA",
        "section": "Logística",
        "tone": "cyan",
        "priority": 65,
        "keywords": ["wms", "governanca", "divergencia", "politica"],
        "metric_key": "wms_pendente",
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
        "description": "Acompanhe em mapa onde estão os veículos da frota e abra o portal Locartrack com um clique.",
        "href": "/logistica/rastreamento",
        "icon": "fa-location-crosshairs",
        "permission": "PAGE_LOGISTICA_RASTREAMENTO",
        "section": "Logística",
        "tone": "cyan",
        "priority": 86,
        "keywords": ["rastreamento", "gps", "locartrack", "mapa", "veiculo", "frota"],
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
        "id": "expedicao_admin",
        "title": "Controle de Expedição",
        "subtitle": "Admin",
        "description": "Painel administrativo para decisões, aprovações e correções da saída.",
        "href": "/expedicao/admin",
        "icon": "fa-user-shield",
        "permission": "PAGE_EXPEDICAO_ADMIN",
        "section": "Logística",
        "tone": "slate",
        "priority": 70,
        "keywords": ["expedicao", "admin", "controle", "saida"],
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
        "subtitle": "Financeiro",
        "description": "Emita documentos fiscais e acompanhe o fluxo financeiro de saída.",
        "href": "/financeiro/faturamento",
        "icon": "fa-file-invoice-dollar",
        "tone": "navy",
        "tone": "navy",
        "permission": "PAGE_FINANCEIRO_FATURAMENTO",
        "section": "Financeiro",
        "tone": "red",
        "priority": 82,
        "keywords": ["faturamento", "nota", "financeiro", "emissao"],
        "metric_key": "boletos_gerados",
    },
    {
        "id": "contas_receber",
        "title": "Contas a Receber",
        "subtitle": "Financeiro",
        "description": "Acompanhe títulos, boletos e a saúde do recebimento financeiro.",
        "href": "/financeiro/contas-receber",
        "icon": "fa-wallet",
        "permission": "PAGE_FINANCEIRO_CONTAS_RECEBER",
        "section": "Financeiro",
        "tone": "red",
        "priority": 80,
        "keywords": ["contas", "receber", "financeiro", "boleto"],
        "metric_key": "boletos_gerados",
    },
    {
        "id": "classificacao_contabil",
        "title": "Classificacao Contabil",
        "subtitle": "Financeiro",
        "description": "Revise classificacoes de entradas, ajuste contas e acompanhe pendencias do contador.",
        "href": "/financeiro/classificacao-contabil",
        "icon": "fa-scale-balanced",
        "permission": "PAGE_FINANCEIRO_CLASSIFICACAO_CONTABIL",
        "section": "Financeiro",
        "tone": "red",
        "priority": 84,
        "keywords": ["contabil", "classificacao", "contador", "conta", "entrada"],
        "metric_key": "boletos_gerados",
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
        "id": "historico",
        "title": "Logs e Auditoria",
        "subtitle": "Administração",
        "description": "Rastreie lançamentos, divergências e decisões críticas do sistema.",
        "href": "/historico",
        "icon": "fa-clipboard-list",
        "permission": "PAGE_ADMIN_HISTORICO",
        "section": "Administração",
        "tone": "navy",
        "priority": 66,
        "keywords": ["historico", "log", "auditoria", "admin"],
        "metric_key": "sessoes_ativas",
    },
    {
        "id": "auditoria_acessos",
        "title": "Auditoria de Acessos",
        "subtitle": "Administração",
        "description": "Monitore acessos sensíveis e acompanhe a atividade administrativa.",
        "href": "/admin/acessos",
        "icon": "fa-user-clock",
        "permission": "PAGE_ADMIN_ACESSOS",
        "section": "Administração",
        "tone": "navy",
        "priority": 64,
        "keywords": ["acessos", "auditoria", "admin", "log"],
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
    "Financeiro": {
        "description": "Emissão, títulos e acompanhamento de recebíveis.",
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
        "conserto_aberto": 0,
        "conserto_pendente": 0,
        "wms_pendente": 0,
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
        metrics["conserto_aberto"] = ConsertoEstoque.query.filter_by(status="Em conserto").count()
        metrics["conserto_pendente"] = (
            ConsertoBaixa.query
            .filter(ConsertoBaixa.status_baixa.in_(["Pendente de confirmacao", "Pendente de confirmação"]))
            .count()
        )
        metrics["wms_pendente"] = ItemWMS.query.filter_by(status="Pendente Enderecamento", ativo=True).count()
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
        if not perms.get(item["permission"], False):
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
        "pre_nota": _fmt_metric(value, "NF na fila", "NFs na fila"),
        "auditor_xml": _fmt_metric(value, "auditoria pendente", "auditorias pendentes"),
        "documento_entrada": _fmt_metric(value, "nota pronta", "notas prontas"),
        "conferencia": _fmt_metric(value, "nota aguardando", "notas aguardando"),
        "notas_liberadas": _fmt_metric(value, "NF lançada", "NFs lançadas"),
        "etiquetas": _fmt_metric(value, "NF liberada", "NFs liberadas"),
        "conserto": _fmt_metric(value, "pendência de baixa", "pendências de baixa"),
        "wms": _fmt_metric(value, "item pendente", "itens pendentes"),
        "wms_enderecos": _fmt_metric(value, "item pendente", "itens pendentes"),
        "wms_governanca": _fmt_metric(value, "item pendente", "itens pendentes"),
        "expedicao_conferencia": _fmt_metric(value, "operação aberta", "operações abertas"),
        "expedicao_admin": _fmt_metric(value, "operação aberta", "operações abertas"),
        "romaneios": _fmt_metric(value, "operação aberta", "operações abertas"),
        "faturamento": _fmt_metric(value, "boleto gerado", "boletos gerados"),
        "contas_receber": _fmt_metric(value, "boleto gerado", "boletos gerados"),
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
        {
            "label": "Pendências de conserto",
            "value": int(metrics["conserto_pendente"]),
            "caption": "Retornos aguardando sua validação.",
            "tone": "violet",
        },
        {
            "label": "Pendências WMS",
            "value": int(metrics["wms_pendente"]),
            "caption": "Itens sem endereçamento fechado.",
            "tone": "cyan",
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

    if metrics["conserto_pendente"] > 0:
        add_action("conserto", "Validar baixas de conserto", f"{int(metrics['conserto_pendente'])} pendência(s) aguardando confirmação.")
    if metrics["notas_concluidas"] > 0:
        add_action("documento_entrada", "Fechar documento de entrada", f"{int(metrics['notas_concluidas'])} nota(s) prontas para lançamento.")
    if metrics["auditoria_pendente"] > 0:
        add_action("auditor_xml", "Tratar auditoria fiscal", f"{int(metrics['auditoria_pendente'])} nota(s) aguardando decisão fiscal.")
    if metrics["wms_pendente"] > 0:
        add_action("wms", "Endereçar itens no WMS", f"{int(metrics['wms_pendente'])} item(ns) sem endereço final.")

    if not actions:
        add_action("notas_liberadas", "Acompanhar histórico", f"Operação está estável em {today}. Use o painel para análise.")

    return actions[:4]


def _build_user_context(metrics: dict) -> dict:
    username = session.get("username", "Usuário")
    role = session.get("role", "Operação")
    ultima_sessao = (
        ActiveSession.query
        .filter(ActiveSession.username == username)
        .order_by(ActiveSession.last_activity.desc())
        .first()
    )
    return {
        "username": username,
        "role": role,
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
@permission_required("PAGE_UPLOAD")
def upload_page():
    return render_template("admin.html", user=session["username"])


@page_bp.route("/compras/auditor-xml")
@permission_required("PAGE_XML_AUDITOR")
def compras_auditor_xml_page():
    return render_template("auditor_xml.html", user=session["username"])


@page_bp.route("/lancamento")
@permission_required("PAGE_LANCAMENTO")
def lancamento_page():
    return render_template("lancamento.html", user=session.get("username", "Fiscal"))


@page_bp.route("/fiscal/liberadas")
@permission_required("PAGE_FISCAL_LIBERADAS")
def fiscal_liberadas_page():
    return render_template("notas_liberadas.html", user=session.get("username", "Fiscal"))


@page_bp.route("/recebimento/etiquetas")
@permission_required("PAGE_ETIQUETAS")
def etiquetas_page():
    return render_template("etiquetas.html", user=session.get("username", "Operação"))


@page_bp.route("/historico")
@permission_required("PAGE_ADMIN_HISTORICO")
def historico_page():
    return render_template("historico.html", user=session["username"])


@page_bp.route("/wms")
@permission_required("PAGE_WMS")
def wms_page():
    return render_template("wms_hub.html", user=session["username"])


@page_bp.route("/wms/enderecamento")
@permission_required("PAGE_WMS")
def wms_enderecamento_page():
    return render_template("wms.html", user=session["username"])


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


@page_bp.route("/admin/wms-enderecos")
@permission_required("PAGE_ADMIN_WMS_ENDERECOS")
def wms_enderecos_admin_page():
    return render_template("admin_wms_enderecos.html", user=session["username"])


@page_bp.route("/admin/wms-governanca")
@permission_required("PAGE_ADMIN_WMS_GOVERNANCA")
def wms_governanca_admin_page():
    return render_template("admin_wms_governanca.html", user=session["username"])


@page_bp.route("/wms/estoque")
@permission_required("PAGE_WMS")
def wms_estoque_tempo_real_page():
    return render_template("estoque_tempo_real.html", user=session["username"])


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
    from flask import redirect

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


@page_bp.route("/expedicao/admin")
@permission_required("PAGE_EXPEDICAO_ADMIN")
def expedicao_admin_page():
    return render_template("expedicao_admin.html", user=session["username"])


@page_bp.route("/logistica/painel-motorista")
def painel_motorista_page():
    # Rota legada. O painel antigo (login + sessão) foi substituído por link público
    # permanente HMAC compartilhado via WhatsApp/QR pelo gestor.
    from flask import redirect
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


@page_bp.route("/expedicao/romaneio")
@permission_required("PAGE_EXPEDICAO_ROMANEIO")
def expedicao_romaneio_page():
    return render_template("expedicao_romaneio.html", user=session["username"])


@page_bp.route("/admin/usuarios")
@permission_required("PAGE_ADMIN_USUARIOS")
def usuarios_page():
    return render_template("usuarios.html", user=session["username"])


@page_bp.route("/admin/atualizacoes")
@permission_required("PAGE_ADMIN_ATUALIZACOES")
def atualizacoes_admin_page():
    return render_template("admin_atualizacoes.html", user=session["username"])


@page_bp.route("/admin/acessos")
@permission_required("PAGE_ADMIN_ACESSOS")
def acessos_admin_page():
    return render_template("admin_acessos.html", user=session["username"])
