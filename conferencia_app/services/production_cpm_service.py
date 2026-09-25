"""Adaptador entre o grafo CPM e os dados produtivos do GRV."""
from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from datetime import date, datetime, time
from typing import Any

from flask import current_app, has_app_context

from ..compras import queries
from ..compras.db import fetch_all
from ..tempo import agora_br
from . import producao_service
from .cpm_service import CpmActivity, CpmThresholds, WorkCalendar, calculate_cpm, simulate_delay


def _datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    if isinstance(value, date):
        return datetime.combine(value, time.min)
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=None)
    except (TypeError, ValueError):
        return None


def _number(value: Any) -> float | None:
    try:
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return None


def _thresholds() -> CpmThresholds:
    config = current_app.config if has_app_context() else {}
    return CpmThresholds(
        critical_tolerance_minutes=int(config.get("CPM_CRITICAL_TOLERANCE_MINUTES", 30)),
        high_risk_minutes=int(config.get("CPM_HIGH_RISK_MINUTES", 480)),
        attention_minutes=int(config.get("CPM_ATTENTION_MINUTES", 1440)),
    )


def _duration(row: dict[str, Any]) -> tuple[int | None, str | None]:
    planned_start = _datetime(row.get("dt_incio_previsto"))
    planned_finish = _datetime(row.get("dt_termino_previsto"))
    expected_hours = _number(row.get("duracao_prevista_horas"))
    if planned_start and planned_finish and planned_finish >= planned_start:
        expected = max(0, int((planned_finish - planned_start).total_seconds() // 60))
        source = "intervalo_planejado_grv"
    elif expected_hours is not None and expected_hours >= 0:
        expected = int(expected_hours * 60)
        source = "tempo_previsto_grv"
    else:
        return None, None
    completed = bool(row.get("finalizado") or row.get("concluido") or row.get("dt_finalizacao"))
    if completed:
        return 0, source
    performed = max(0, int((_number(row.get("hs_realizadas")) or 0) * 60))
    return max(0, expected - performed), source


def _delivery_deadline(value: Any) -> datetime | None:
    result = _datetime(value)
    if result and result.time() == time.min:
        return result.replace(hour=17)
    return result


def load_budget_graph(orcamento: str | int, now: datetime | None = None) -> dict[str, Any]:
    params = {"cod_empresa": 1, "orcamento": str(orcamento).strip()}
    rows = producao_service._metadata_read(  # leitura compartilhada e cacheada do modulo
        fetch_all, queries.SQL_PRODUCAO_CPM_ORCAMENTO_ATIVIDADES, params
    )
    if not rows:
        raise LookupError("Orcamento nao encontrado no GRV.")
    purchases = producao_service._metadata_read(
        fetch_all, queries.SQL_PRODUCAO_CPM_ORCAMENTO_COMPRAS, params
    )
    first = rows[0]
    project_start = now or agora_br()
    operations_by_component: dict[tuple[int, int], list[dict[str, Any]]] = defaultdict(list)
    component_rows: dict[tuple[int, int], dict[str, Any]] = {}
    order_numbers: dict[int, str] = {}
    for row in rows:
        if row.get("cod_os") is None:
            continue
        os_id = int(row["cod_os"])
        order_numbers[os_id] = str(row.get("n_os") or "")
        if row.get("cod_os_aux") is None:
            continue
        key = (os_id, int(row["cod_os_aux"]))
        component_rows[key] = row
        if row.get("cod_processo") is not None:
            operations_by_component[key].append(row)

    activities: list[CpmActivity] = []
    first_activity: dict[tuple[int, int], str] = {}
    last_activity: dict[tuple[int, int], str] = {}
    for component, operation_rows_raw in operations_by_component.items():
        sorted_rows = sorted(operation_rows_raw, key=lambda row: (
            row.get("seq_processo") is None, row.get("seq_processo") or 0, str(row.get("cod_processo") or "")
        ))
        previous_id = None
        for index, row in enumerate(sorted_rows):
            activity_id = f"PROC:{component[0]}:{component[1]}:{row.get('cod_processo') or index}"
            duration, duration_source = _duration(row)
            completed_at = _datetime(row.get("dt_finalizacao")) if (
                row.get("finalizado") or row.get("concluido") or row.get("dt_finalizacao")
            ) else None
            completed = bool(row.get("finalizado") or row.get("concluido") or row.get("dt_finalizacao"))
            planned_start = _datetime(row.get("dt_incio_previsto"))
            resource = str(row.get("maquina") or "").strip() or None
            if planned_start and planned_start > project_start and resource:
                cause = f"recurso {resource} indisponivel ate {planned_start.isoformat()} (fila/calendario GRV)"
            elif previous_id:
                cause = "processo predecessor atrasado"
            else:
                cause = None
            activity = CpmActivity(
                id=activity_id,
                name=str(row.get("tiposervico") or row.get("cod_processo") or "Processo"),
                duration_minutes=duration,
                predecessor_ids=[previous_id] if previous_id else [],
                component_id=f"{component[0]}:{component[1]}",
                resource_id=resource,
                order_number=order_numbers.get(component[0]),
                completed=completed,
                completed_at=completed_at,
                not_before=None if completed_at else planned_start,
                cause=cause,
                metadata={
                    "operation_code": str(row.get("cod_processo") or ""),
                    "component_code": str(row.get("cod_os_completo") or component[1]),
                    "component_description": str(row.get("subtitulo") or ""),
                    "duration_source": duration_source,
                    "planned_start": _datetime(row.get("dt_incio_previsto")),
                    "planned_finish": _datetime(row.get("dt_termino_previsto")),
                    "performed_hours": _number(row.get("hs_realizadas")) or 0,
                },
            )
            activities.append(activity)
            first_activity.setdefault(component, activity_id)
            last_activity[component] = activity_id
            previous_id = activity_id

    # Predecessoras explicitas dos componentes e filhos que liberam montagem.
    activity_by_id = {item.id: item for item in activities}
    for component, row in component_rows.items():
        first_id = first_activity.get(component)
        if not first_id:
            continue
        first_process = activity_by_id[first_id]
        for predecessor_field in ("predecessora1", "predecessora2"):
            predecessor = row.get(predecessor_field)
            if predecessor is not None:
                predecessor_id = last_activity.get((component[0], int(predecessor)))
                if predecessor_id and predecessor_id not in first_process.predecessor_ids:
                    first_process.predecessor_ids.append(predecessor_id)
        if "MONTAGEM" in first_process.name.upper():
            for child, child_row in component_rows.items():
                if child[0] == component[0] and child_row.get("os_pai") == component[1]:
                    child_last = last_activity.get(child)
                    if child_last and child_last not in first_process.predecessor_ids:
                        first_process.predecessor_ids.append(child_last)

    purchase_payloads: list[dict[str, Any]] = []
    missing_purchase_forecast = False
    for index, row in enumerate(purchases):
        component = (int(row["cod_os"]), int(row["cod_os_aux"]))
        successor = first_activity.get(component)
        if not successor:
            continue
        promised = _datetime(row.get("dt_recebimento") or row.get("data_prometida"))
        reference = row.get("pedido") or row.get("solicitacao")
        purchase_id = f"COMPRA:{reference}:{component[0]}:{component[1]}:{row.get('cod_produto')}:{index}"
        status = "SEM_PREVISAO" if not promised else "NO_PRAZO"
        missing_purchase_forecast = missing_purchase_forecast or not promised
        activity = CpmActivity(
            id=purchase_id,
            name=f"{'PC' if row.get('pedido') else 'SC'} {reference} - {row.get('material_descricao') or row.get('material_codigo')}",
            duration_minutes=0,
            kind="purchase",
            component_id=f"{component[0]}:{component[1]}",
            order_number=str(row.get("n_os") or ""),
            not_before=promised,
            cause="fornecedor sem confirmacao" if not promised else "pedido de compra aguardando disponibilidade",
            metadata={
                "purchase_order": row.get("pedido"), "purchase_request": row.get("solicitacao"),
                "supplier": row.get("fornecedor"),
                "material_code": row.get("material_codigo"), "material": row.get("material_descricao"),
                "quantity": row.get("quantidade"), "unit": row.get("unidade"),
                "promised_date": promised, "purchase_status": status,
            },
        )
        activities.append(activity)
        activity_by_id[successor].predecessor_ids.append(purchase_id)
        purchase_payloads.append(activity.metadata | {"activity_id": purchase_id, "successor_id": successor})

    return {
        "budget": {
            "id": first.get("cod_orcamento"), "number": first.get("n_orcamento"),
            "version": first.get("versao"), "contractual_delivery": _delivery_deadline(first.get("dt_previsao_entrega")),
            "customer": first.get("cliente"), "description": first.get("titulo"),
            "orders": sorted(set(order_numbers.values())),
        },
        "activities": activities,
        "purchases": purchase_payloads,
        "missing_purchase_forecast": missing_purchase_forecast,
        "project_start": project_start,
    }


def _serialize(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _serialize(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_serialize(item) for item in value]
    return value


def calculate_budget(orcamento: str | int, now: datetime | None = None, *, serialized: bool = True) -> dict[str, Any]:
    graph = load_budget_graph(orcamento, now)
    purchase_ids = {item.id for item in graph["activities"] if item.kind == "purchase"}
    production_activities = [deepcopy(item) for item in graph["activities"] if item.id not in purchase_ids]
    for activity in production_activities:
        activity.predecessor_ids = [item for item in activity.predecessor_ids if item not in purchase_ids]
    production_result = calculate_cpm(
        production_activities, project_start=graph["project_start"],
        contractual_delivery=graph["budget"]["contractual_delivery"],
        calendar=WorkCalendar(), thresholds=_thresholds(),
    )
    result = calculate_cpm(
        graph["activities"], project_start=graph["project_start"],
        contractual_delivery=graph["budget"]["contractual_delivery"],
        calendar=WorkCalendar(), thresholds=_thresholds(),
    )
    if not production_activities:
        result["calculable"] = False
        result["status"] = "RISCO_NAO_CALCULAVEL"
        result["warnings"].append("Orcamento sem processos produtivos disponiveis para calculo.")
    if graph["missing_purchase_forecast"]:
        result["calculable"] = False
        result["status"] = "RISCO_NAO_CALCULAVEL"
        result["warnings"].append("Compra obrigatoria sem previsao confirmada.")
    activity_index = {item["id"]: item for item in result["activities"]}
    production_index = {item["id"]: item for item in production_result["activities"]}
    purchases = []
    for purchase in graph["purchases"]:
        activity = activity_index[purchase["activity_id"]]
        promised = _datetime(purchase.get("promised_date"))
        needed = production_index[purchase["successor_id"]]["early_start"]
        slack = WorkCalendar().working_minutes_between(promised, needed) if promised else None
        if promised is None:
            status = "SEM_PREVISAO"
        elif slack < 0:
            status = "ATRASO_PROJETADO"
        elif slack <= _thresholds().critical_tolerance_minutes:
            status = "CRITICO"
        elif slack <= _thresholds().attention_minutes:
            status = "ATENCAO"
        else:
            status = "NO_PRAZO"
        purchases.append(purchase | {
            "needed_date": needed, "float_minutes": slack, "status": status,
            "impact_minutes": max(0, -(slack or 0)) if slack is not None else None,
        })
    activities = result["activities"]
    summary = {
        "projected_finish": result["projected_finish"],
        "contractual_delivery": result["contractual_delivery"],
        "total_float_minutes": result["delivery_float_minutes"],
        "pending_process_count": sum(item["kind"] == "process" and not item["completed"] for item in activities),
        "critical_process_count": sum(item["kind"] == "process" and item["is_critical"] and not item["completed"] for item in activities),
        "critical_purchase_count": sum(item["status"] in {"CRITICO", "ATRASO_PROJETADO", "SEM_PREVISAO"} for item in purchases),
        "status": result["status"], "calculable": result["calculable"],
    }
    payload = {"budget": graph["budget"], "summary": summary, **result, "purchases": purchases}
    return _serialize(payload) if serialized else payload


def simulate_budget(orcamento: str | int, activity_id: str, delay_minutes: int) -> dict[str, Any]:
    graph = load_budget_graph(orcamento)
    result = simulate_delay(
        graph["activities"], activity_id, delay_minutes,
        project_start=graph["project_start"], contractual_delivery=graph["budget"]["contractual_delivery"],
        calendar=WorkCalendar(), thresholds=_thresholds(),
    )
    return _serialize({"budget": graph["budget"], **result})


def graph_payload(orcamento: str | int) -> dict[str, Any]:
    payload = calculate_budget(orcamento, serialized=False)
    return _serialize({
        "budget": payload["budget"], "summary": payload["summary"],
        "nodes": payload["activities"],
        "edges": [
            {"source": predecessor, "target": item["id"], "type": "finish_to_start"}
            for item in payload["activities"] for predecessor in item["predecessor_ids"]
        ],
        "critical_path": payload["critical_path"], "warnings": payload["warnings"],
    })
