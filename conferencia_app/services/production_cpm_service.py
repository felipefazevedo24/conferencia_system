"""Adapta a estrutura produtiva de uma OS ao motor CPM.

O modulo nao consulta nem depende do Cronograma de Entrega. Estrutura,
operacoes, filas e compras sao carregadas somente para a OS selecionada.
"""
from __future__ import annotations

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
        critical_tolerance_minutes=int(config.get("CPM_CRITICAL_TOLERANCE_MINUTES", 0)),
        high_risk_minutes=int(config.get("CPM_HIGH_RISK_MINUTES", 480)),
        attention_minutes=int(config.get("CPM_ATTENTION_MINUTES", 1440)),
    )


def _target_finish(value: Any) -> datetime | None:
    result = _datetime(value)
    return result.replace(hour=17) if result and result.time() == time.min else result


def _duration(operation: dict[str, Any]) -> tuple[int | None, str | None]:
    planned_start = _datetime(operation.get("inicio_previsto"))
    planned_finish = _datetime(operation.get("fim_previsto"))
    expected_hours = _number(operation.get("duracao_prevista_horas"))
    if planned_start and planned_finish and planned_finish >= planned_start:
        expected = max(0, int((planned_finish - planned_start).total_seconds() // 60))
        source = "intervalo_planejado_grv"
    elif expected_hours is not None and expected_hours >= 0:
        expected = int(expected_hours * 60)
        source = "tempo_previsto_grv"
    else:
        return None, None
    if operation.get("finalizada"):
        return 0, source
    performed = max(0, int((_number(operation.get("horas_realizadas")) or 0) * 60))
    return max(0, expected - performed), source


def _safe_batch(query: str, params: dict[str, Any], warning: str, warnings: list[str]) -> list[dict[str, Any]]:
    try:
        return producao_service._metadata_read(fetch_all, query, params)
    except Exception:
        warnings.append(warning)
        if has_app_context():
            current_app.logger.exception(warning)
        return []


def load_order_graph(numero_os: str, now: datetime | None = None) -> dict[str, Any]:
    structure = producao_service.obter_estrutura(numero_os)
    order = structure["ordem"]
    project_start = (now or agora_br()).replace(tzinfo=None)
    warnings = list(structure.get("avisos_estrutura") or [])
    params = {"cod_empresa": 1, "cod_os": order["codigo"]}
    purchases = _safe_batch(
        queries.SQL_PRODUCAO_CPM_COMPRAS_OS, params,
        "Compras indisponiveis para a analise CPM.", warnings,
    )
    queue_rows = _safe_batch(
        queries.SQL_PRODUCAO_CPM_FILAS_OS, params,
        "Filas de recursos indisponiveis para a analise CPM.", warnings,
    )
    queues = {str(row.get("recurso") or "").strip(): int(row.get("fila") or 0) for row in queue_rows}

    activities: list[CpmActivity] = []
    first_by_component: dict[str, str] = {}
    last_by_component: dict[str, str] = {}
    component_by_id = {str(item["aux_code"]): item for item in structure.get("nos", [])}
    for component_id, component in component_by_id.items():
        operations = sorted(
            component.get("operacoes") or [],
            key=lambda item: (item.get("sequencia") is None, item.get("sequencia") or 0, str(item.get("codigo") or "")),
        )
        previous_id: str | None = None
        for index, operation in enumerate(operations):
            operation_code = str(operation.get("codigo") or index)
            activity_id = f"PROC:{component_id}:{operation_code}"
            duration, duration_source = _duration(operation)
            completed = bool(operation.get("finalizada"))
            completed_at = _datetime(operation.get("fim")) if completed else None
            resource = str(operation.get("maquina") or "").strip() or None
            planned_start = _datetime(operation.get("inicio_previsto"))
            queue_count = queues.get(resource or "", 0)
            resource_wait = (
                WorkCalendar().working_minutes_between(project_start, planned_start)
                if planned_start and planned_start > project_start else 0
            )
            cause = None
            if operation.get("travada"):
                cause = "Processo bloqueado no GRV"
            elif resource_wait > 0 and resource:
                cause = f"Fila ou disponibilidade do recurso {resource}"
            elif previous_id:
                cause = "Processo predecessor"
            activity = CpmActivity(
                id=activity_id,
                name=str(operation.get("nome") or operation_code or "Processo"),
                duration_minutes=duration,
                predecessor_ids=[previous_id] if previous_id else [],
                component_id=component_id,
                resource_id=resource,
                completed=completed,
                completed_at=completed_at,
                not_before=None if completed else planned_start,
                cause=cause,
                metadata={
                    "operation_code": operation_code,
                    "operation_sequence": operation.get("sequencia"),
                    "component_aux_code": int(component["aux_code"]),
                    "component_code": str(component.get("codigo") or component_id),
                    "component_description": str(component.get("descricao") or ""),
                    "duration_source": duration_source,
                    "performed_hours": _number(operation.get("horas_realizadas")) or 0,
                    "queue_count": queue_count,
                    "estimated_wait_minutes": resource_wait,
                    "locked": bool(operation.get("travada")),
                },
            )
            activities.append(activity)
            first_by_component.setdefault(component_id, activity_id)
            last_by_component[component_id] = activity_id
            previous_id = activity_id

    activity_by_id = {item.id: item for item in activities}
    # Dependencias Finish-to-Start declaradas na estrutura.
    for component_id, component in component_by_id.items():
        first_id = first_by_component.get(component_id)
        if not first_id:
            if component.get("operacoes_total"):
                warnings.append(f"Componente {component.get('codigo')} sem operacoes validas para CPM.")
            continue
        first = activity_by_id[first_id]
        for predecessor_id in component.get("predecessor_ids") or []:
            predecessor_last = last_by_component.get(str(predecessor_id))
            if predecessor_last and predecessor_last not in first.predecessor_ids:
                first.predecessor_ids.append(predecessor_last)
        # Montagem do pai aguarda a conclusao de todos os filhos.
        if "MONTAGEM" in first.name.upper():
            for child_id in component.get("child_ids") or []:
                child_last = last_by_component.get(str(child_id))
                if child_last and child_last not in first.predecessor_ids:
                    first.predecessor_ids.append(child_last)

    purchase_links: list[dict[str, Any]] = []
    missing_purchase_forecast = False
    for index, row in enumerate(purchases):
        component_id = str(row.get("cod_os_aux"))
        successor_id = first_by_component.get(component_id)
        if not successor_id:
            continue
        promised = _datetime(row.get("dt_recebimento") or row.get("data_prometida"))
        reference = row.get("pedido") or row.get("solicitacao") or "sem-numero"
        purchase_id = f"COMPRA:{reference}:{component_id}:{row.get('cod_produto')}:{index}"
        missing_purchase_forecast = missing_purchase_forecast or promised is None
        activity = CpmActivity(
            id=purchase_id,
            name=f"{'PC' if row.get('pedido') else 'SC'} {reference} - {row.get('material_descricao') or row.get('material_codigo')}",
            duration_minutes=0,
            kind="purchase",
            component_id=component_id,
            not_before=promised,
            cause="Compra sem previsao confirmada" if not promised else "Material aguardando disponibilidade",
            metadata={
                "component_aux_code": int(row["cod_os_aux"]),
                "component_code": str(component_by_id.get(component_id, {}).get("codigo") or component_id),
                "purchase_order": row.get("pedido"), "purchase_request": row.get("solicitacao"),
                "supplier": row.get("fornecedor"), "material_code": row.get("material_codigo"),
                "material": row.get("material_descricao"), "quantity": row.get("quantidade"),
                "unit": row.get("unidade"), "promised_date": promised,
            },
        )
        activities.append(activity)
        activity_by_id[successor_id].predecessor_ids.append(purchase_id)
        purchase_links.append({"activity_id": purchase_id, "successor_id": successor_id, **activity.metadata})

    return {
        "order": {
            "number": order["numero"], "title": order.get("titulo"),
            "target_finish": _target_finish(order.get("data_prevista")),
        },
        "activities": activities, "purchases": purchase_links,
        "missing_purchase_forecast": missing_purchase_forecast,
        "project_start": project_start, "warnings": warnings,
    }


def _serialize(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _serialize(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_serialize(item) for item in value]
    return value


def _calculate_graph(graph: dict[str, Any]) -> dict[str, Any]:
    purchase_ids = {item.id for item in graph["activities"] if item.kind == "purchase"}
    production_activities = [deepcopy(item) for item in graph["activities"] if item.id not in purchase_ids]
    for activity in production_activities:
        activity.predecessor_ids = [item for item in activity.predecessor_ids if item not in purchase_ids]
    production_result = calculate_cpm(
        production_activities, project_start=graph["project_start"],
        target_finish=graph["order"]["target_finish"], calendar=WorkCalendar(), thresholds=_thresholds(),
    )
    result = calculate_cpm(
        graph["activities"], project_start=graph["project_start"],
        target_finish=graph["order"]["target_finish"], calendar=WorkCalendar(), thresholds=_thresholds(),
    )
    result["warnings"] = list(dict.fromkeys([*graph["warnings"], *result["warnings"]]))
    if graph["missing_purchase_forecast"]:
        result["calculable"] = False
        result["status"] = "RISCO_NAO_CALCULAVEL"
        result["warnings"].append("Compra obrigatoria sem previsao confirmada.")
    activity_index = {item["id"]: item for item in result["activities"]}
    production_index = {item["id"]: item for item in production_result["activities"]}
    purchases = []
    for purchase in graph["purchases"]:
        purchase_activity = activity_index[purchase["activity_id"]]
        process_activity = activity_index[purchase["successor_id"]]
        promised = _datetime(purchase.get("promised_date"))
        needed = production_index.get(purchase["successor_id"], process_activity)["early_start"]
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
            status = "NORMAL"
        purchases.append({
            **purchase, "needed_date": needed, "float_minutes": slack, "status": status,
            "impact_minutes": max(0, -(slack or 0)) if slack is not None else None,
            "is_critical": purchase_activity["is_critical"],
        })
    activities = result["activities"]
    pending = [item for item in activities if item["kind"] == "process" and not item["completed"]]
    bottleneck = max(
        pending,
        key=lambda item: (item["metadata"].get("estimated_wait_minutes") or 0, item["queue_minutes"]),
        default=None,
    )
    result["summary"] = {
        "status": result["status"], "calculable": result["calculable"],
        "projected_finish": result["projected_finish"], "target_finish": result["target_finish"],
        "total_float_minutes": result["project_float_minutes"],
        "pending_process_count": len(pending),
        "critical_process_count": sum(item["is_critical"] for item in pending),
        "critical_purchase_count": sum(item["status"] in {"CRITICO", "ATRASO_PROJETADO", "SEM_PREVISAO"} for item in purchases),
        "bottleneck": bottleneck["resource_id"] if bottleneck and bottleneck["metadata"].get("estimated_wait_minutes") else None,
    }
    return {"order": graph["order"], **result, "purchases": purchases}


def calculate_order(numero_os: str, now: datetime | None = None, *, serialized: bool = True) -> dict[str, Any]:
    payload = _calculate_graph(load_order_graph(numero_os, now))
    return _serialize(payload) if serialized else payload


def simulate_order(numero_os: str, activity_id: str, delay_minutes: int) -> dict[str, Any]:
    graph = load_order_graph(numero_os)
    result = simulate_delay(
        graph["activities"], activity_id, delay_minutes,
        project_start=graph["project_start"], target_finish=graph["order"]["target_finish"],
        calendar=WorkCalendar(), thresholds=_thresholds(),
    )
    return _serialize({"order": graph["order"], **result})


def component_payload(numero_os: str, aux_code: int) -> dict[str, Any]:
    payload = calculate_order(numero_os, serialized=False)
    activities = [item for item in payload["activities"] if item["metadata"].get("component_aux_code") == aux_code]
    purchases = [item for item in payload["purchases"] if item.get("component_aux_code") == aux_code]
    if not activities and not purchases:
        raise LookupError("Componente nao encontrado na analise CPM.")
    return _serialize({"order": payload["order"], "summary": payload["summary"], "activities": activities, "purchases": purchases})


def process_payload(numero_os: str, activity_id: str) -> dict[str, Any]:
    payload = calculate_order(numero_os, serialized=False)
    activity = next((item for item in payload["activities"] if item["id"] == activity_id), None)
    if not activity:
        raise LookupError("Processo nao encontrado na analise CPM.")
    return _serialize({"order": payload["order"], "activity": activity})
