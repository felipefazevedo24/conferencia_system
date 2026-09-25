"""Motor de Critical Path Method (CPM) independente de banco e interface.

Todas as duracoes sao expressas em minutos produtivos. Datas sao calculadas
por :class:`WorkCalendar`, evitando que regras de expediente sejam duplicadas
nos adaptadores do GRV ou no frontend.
"""
from __future__ import annotations

from collections import defaultdict, deque
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any, Iterable


class CpmError(ValueError):
    """Erro de dados que impede um calculo CPM confiavel."""


class CircularDependencyError(CpmError):
    """O grafo possui ao menos uma dependencia circular."""


@dataclass(frozen=True)
class CpmThresholds:
    critical_tolerance_minutes: int = 30
    high_risk_minutes: int = 8 * 60
    attention_minutes: int = 24 * 60


@dataclass
class WorkCalendar:
    """Calendario produtivo semanal com feriados e indisponibilidades.

    O padrao representa dois turnos de quatro horas em dias uteis. Intervalos
    adicionais podem ser informados sem alterar o algoritmo CPM.
    """

    shifts: tuple[tuple[time, time], ...] = (
        (time(8), time(12)),
        (time(13), time(17)),
    )
    working_weekdays: frozenset[int] = frozenset({0, 1, 2, 3, 4})
    holidays: frozenset[date] = frozenset()
    unavailable: tuple[tuple[datetime, datetime], ...] = ()

    def intervals(self, day: date) -> list[tuple[datetime, datetime]]:
        if day.weekday() not in self.working_weekdays or day in self.holidays:
            return []
        result: list[tuple[datetime, datetime]] = []
        for start_at, finish_at in self.shifts:
            fragments = [(datetime.combine(day, start_at), datetime.combine(day, finish_at))]
            for blocked_start, blocked_finish in self.unavailable:
                next_fragments = []
                for start, finish in fragments:
                    if blocked_finish <= start or blocked_start >= finish:
                        next_fragments.append((start, finish))
                    else:
                        if blocked_start > start:
                            next_fragments.append((start, blocked_start))
                        if blocked_finish < finish:
                            next_fragments.append((blocked_finish, finish))
                fragments = next_fragments
            result.extend((start, finish) for start, finish in fragments if finish > start)
        return result

    def next_work_time(self, value: datetime) -> datetime:
        cursor = value
        for _ in range(3660):
            for start, finish in self.intervals(cursor.date()):
                if cursor <= start:
                    return start
                if start <= cursor < finish:
                    return cursor
            cursor = datetime.combine(cursor.date() + timedelta(days=1), time.min)
        raise CpmError("Calendario sem periodo produtivo nos proximos dez anos.")

    def previous_work_time(self, value: datetime) -> datetime:
        cursor = value
        for _ in range(3660):
            intervals = self.intervals(cursor.date())
            for start, finish in reversed(intervals):
                if cursor >= finish:
                    return finish
                if start < cursor <= finish:
                    return cursor
            cursor = datetime.combine(cursor.date() - timedelta(days=1), time.max)
        raise CpmError("Calendario sem periodo produtivo nos dez anos anteriores.")

    def add_minutes(self, value: datetime, minutes: int) -> datetime:
        if minutes < 0:
            return self.subtract_minutes(value, -minutes)
        cursor = self.next_work_time(value)
        remaining = int(minutes)
        while remaining:
            interval = next((pair for pair in self.intervals(cursor.date()) if pair[0] <= cursor < pair[1]), None)
            if interval is None:
                cursor = self.next_work_time(cursor)
                continue
            available = int((interval[1] - cursor).total_seconds() // 60)
            used = min(remaining, available)
            cursor += timedelta(minutes=used)
            remaining -= used
            if remaining:
                cursor = self.next_work_time(cursor + timedelta(microseconds=1))
        return cursor

    def subtract_minutes(self, value: datetime, minutes: int) -> datetime:
        cursor = self.previous_work_time(value)
        remaining = int(minutes)
        while remaining:
            interval = next((pair for pair in reversed(self.intervals(cursor.date())) if pair[0] < cursor <= pair[1]), None)
            if interval is None:
                cursor = self.previous_work_time(cursor)
                continue
            available = int((cursor - interval[0]).total_seconds() // 60)
            used = min(remaining, available)
            cursor -= timedelta(minutes=used)
            remaining -= used
            if remaining:
                cursor = self.previous_work_time(cursor - timedelta(microseconds=1))
        return cursor

    def working_minutes_between(self, start: datetime, finish: datetime) -> int:
        if finish < start:
            return -self.working_minutes_between(finish, start)
        total = 0
        day = start.date()
        while day <= finish.date():
            for interval_start, interval_finish in self.intervals(day):
                overlap_start = max(start, interval_start)
                overlap_finish = min(finish, interval_finish)
                if overlap_finish > overlap_start:
                    total += int((overlap_finish - overlap_start).total_seconds() // 60)
            day += timedelta(days=1)
        return total


@dataclass
class CpmActivity:
    id: str
    name: str
    duration_minutes: int | None
    predecessor_ids: list[str] = field(default_factory=list)
    kind: str = "process"
    component_id: str | None = None
    resource_id: str | None = None
    order_number: str | None = None
    completed: bool = False
    completed_at: datetime | None = None
    not_before: datetime | None = None
    cause: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.completed_at is not None:
            self.completed = True


def _activity(value: CpmActivity | dict[str, Any]) -> CpmActivity:
    if isinstance(value, CpmActivity):
        return deepcopy(value)
    known = {field_name for field_name in CpmActivity.__dataclass_fields__}
    return CpmActivity(**{key: deepcopy(item) for key, item in value.items() if key in known})


def _topological_order(activities: dict[str, CpmActivity]) -> tuple[list[str], dict[str, list[str]]]:
    successors: dict[str, list[str]] = defaultdict(list)
    indegree = {activity_id: 0 for activity_id in activities}
    for activity in activities.values():
        for predecessor in dict.fromkeys(activity.predecessor_ids):
            if predecessor not in activities:
                raise CpmError(f"Predecessora inexistente: {predecessor} -> {activity.id}")
            successors[predecessor].append(activity.id)
            indegree[activity.id] += 1
    queue = deque(sorted(key for key, degree in indegree.items() if degree == 0))
    ordered: list[str] = []
    while queue:
        current = queue.popleft()
        ordered.append(current)
        for successor in sorted(successors[current]):
            indegree[successor] -= 1
            if indegree[successor] == 0:
                queue.append(successor)
    if len(ordered) != len(activities):
        cycle_nodes = sorted(key for key, degree in indegree.items() if degree > 0)
        raise CircularDependencyError(
            "Nao foi possivel calcular CPM devido a dependencia circular: " + ", ".join(cycle_nodes)
        )
    return ordered, successors


def classify_float(minutes: int, thresholds: CpmThresholds) -> str:
    if minutes < 0:
        return "ATRASO_PROJETADO"
    if minutes <= thresholds.critical_tolerance_minutes:
        return "CRITICO"
    if minutes <= thresholds.high_risk_minutes:
        return "RISCO_ALTO"
    if minutes <= thresholds.attention_minutes:
        return "ATENCAO"
    return "NORMAL"


def calculate_cpm(
    raw_activities: Iterable[CpmActivity | dict[str, Any]],
    *,
    project_start: datetime,
    contractual_delivery: datetime | None = None,
    calendar: WorkCalendar | None = None,
    thresholds: CpmThresholds | None = None,
) -> dict[str, Any]:
    calendar = calendar or WorkCalendar()
    thresholds = thresholds or CpmThresholds()
    activity_list = list(map(_activity, raw_activities))
    activities = {item.id: item for item in activity_list}
    if not activities:
        return {
            "calculable": True, "activities": [], "critical_path": [],
            "projected_finish": project_start, "contractual_delivery": contractual_delivery,
            "delivery_float_minutes": None, "status": "NORMAL", "warnings": [],
        }
    if len(activities) != len(activity_list):
        raise CpmError("Identificador de atividade duplicado.")
    ordered, successors = _topological_order(activities)
    warnings: list[str] = []
    schedule: dict[str, dict[str, Any]] = {}
    for activity_id in ordered:
        activity = activities[activity_id]
        missing_duration = activity.duration_minutes is None
        if missing_duration:
            warnings.append(f"Duracao nao definida: {activity.name} ({activity.id})")
        if activity.completed and not activity.completed_at:
            warnings.append(f"Processo concluido sem data de conclusao: {activity.name} ({activity.id})")
        duration = max(0, int(activity.duration_minutes or 0))
        predecessor_finish = max(
            (schedule[item]["early_finish"] for item in activity.predecessor_ids),
            default=project_start,
        )
        start = max(project_start, predecessor_finish)
        if activity.not_before and activity.not_before > start:
            start = activity.not_before
        start = calendar.next_work_time(start)
        if activity.completed:
            finish = activity.completed_at or project_start
            start = finish
            remaining = 0
        else:
            remaining = duration
            finish = calendar.add_minutes(start, remaining)
        schedule[activity_id] = {
            "early_start": start,
            "early_finish": finish,
            "duration_minutes": duration,
            "remaining_minutes": remaining,
            "duration_missing": missing_duration,
        }
    projected_finish = max(item["early_finish"] for item in schedule.values())
    for activity_id in reversed(ordered):
        activity = activities[activity_id]
        successor_starts = [schedule[item]["late_start"] for item in successors[activity_id]]
        late_finish = min(successor_starts) if successor_starts else projected_finish
        remaining = schedule[activity_id]["remaining_minutes"]
        late_start = calendar.subtract_minutes(late_finish, remaining) if remaining else late_finish
        total_float = calendar.working_minutes_between(schedule[activity_id]["early_start"], late_start)
        schedule[activity_id].update(
            late_start=late_start,
            late_finish=late_finish,
            total_float_minutes=total_float,
        )
    delivery_float = (
        calendar.working_minutes_between(projected_finish, contractual_delivery)
        if contractual_delivery else None
    )
    result_activities = []
    for activity_id in ordered:
        activity = activities[activity_id]
        data = schedule[activity_id]
        is_critical = data["total_float_minutes"] <= thresholds.critical_tolerance_minutes
        cause = activity.cause
        if not cause and activity.not_before and activity.not_before > project_start:
            cause = "material nao disponivel" if activity.kind == "purchase" else "recurso indisponivel ou fila"
        result_activities.append({
            "id": activity.id,
            "name": activity.name,
            "kind": activity.kind,
            "component_id": activity.component_id,
            "resource_id": activity.resource_id,
            "order_number": activity.order_number,
            "predecessor_ids": list(activity.predecessor_ids),
            "early_start": data["early_start"],
            "early_finish": data["early_finish"],
            "late_start": data["late_start"],
            "late_finish": data["late_finish"],
            "duration_minutes": None if data["duration_missing"] else data["duration_minutes"],
            "duration_label": "Duracao nao definida" if data["duration_missing"] else None,
            "total_float_minutes": data["total_float_minutes"],
            "is_critical": is_critical,
            "status": classify_float(data["total_float_minutes"], thresholds),
            "critical_reason": cause if is_critical else None,
            "completed": activity.completed,
            "metadata": activity.metadata,
        })
    calculable = not warnings
    status = "RISCO_NAO_CALCULAVEL" if not calculable else (
        classify_float(delivery_float, thresholds) if delivery_float is not None else "NORMAL"
    )
    return {
        "calculable": calculable,
        "activities": result_activities,
        "critical_path": [item["id"] for item in result_activities if item["is_critical"]],
        "projected_finish": projected_finish,
        "contractual_delivery": contractual_delivery,
        "delivery_float_minutes": delivery_float,
        "status": status,
        "warnings": warnings,
    }


def simulate_delay(
    raw_activities: Iterable[CpmActivity | dict[str, Any]],
    activity_id: str,
    delay_minutes: int,
    **calculation_options: Any,
) -> dict[str, Any]:
    if delay_minutes < 0:
        raise CpmError("O atraso simulado nao pode ser negativo.")
    activities = [_activity(item) for item in raw_activities]
    selected = next((item for item in activities if item.id == activity_id), None)
    if selected is None:
        raise CpmError("Atividade da simulacao nao encontrada.")
    if selected.duration_minutes is None:
        raise CpmError("Nao e possivel simular uma atividade sem duracao definida.")
    if selected.completed:
        raise CpmError("Nao e possivel simular atraso em uma atividade concluida.")
    baseline = calculate_cpm(activities, **calculation_options)
    selected.duration_minutes += int(delay_minutes)
    simulated = calculate_cpm(activities, **calculation_options)
    simulated["simulation"] = {
        "activity_id": activity_id,
        "delay_minutes": int(delay_minutes),
        "previous_projected_finish": baseline["projected_finish"],
        "previous_delivery_float_minutes": baseline["delivery_float_minutes"],
        "previous_status": baseline["status"],
        "affected_activity_ids": [
            item["id"] for item in simulated["activities"]
            if next(base for base in baseline["activities"] if base["id"] == item["id"])["early_finish"] != item["early_finish"]
        ],
    }
    return simulated
