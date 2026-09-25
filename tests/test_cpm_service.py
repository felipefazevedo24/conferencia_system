from datetime import datetime

import pytest

from conferencia_app.services.cpm_service import (
    CircularDependencyError,
    CpmActivity,
    WorkCalendar,
    calculate_cpm,
    simulate_delay,
)


START = datetime(2026, 9, 21, 8)
CALENDAR = WorkCalendar(shifts=((START.time(), datetime(2026, 9, 21, 18).time()),))


def activity(identifier, duration, predecessors=(), **changes):
    return CpmActivity(identifier, identifier, duration, list(predecessors), **changes)


def by_id(result):
    return {item["id"]: item for item in result["activities"]}


def test_caminho_simples_calcula_es_ef_ls_lf_e_folga():
    result = calculate_cpm(
        [activity("A", 60), activity("B", 120, ["A"]), activity("C", 60, ["B"])],
        project_start=START, calendar=CALENDAR,
    )
    rows = by_id(result)
    assert rows["A"]["early_start"] == START
    assert rows["C"]["early_finish"] == datetime(2026, 9, 21, 12)
    assert all(row["total_float_minutes"] == 0 for row in rows.values())
    assert result["critical_path"] == ["A", "B", "C"]


def test_caminhos_paralelos_e_folga_identificam_caminho_critico():
    result = calculate_cpm([
        activity("A", 60), activity("B", 180, ["A"]),
        activity("C", 60, ["A"]), activity("D", 60, ["B", "C"]),
    ], project_start=START, calendar=CALENDAR)
    rows = by_id(result)
    assert result["critical_path"] == ["A", "B", "D"]
    assert rows["C"]["total_float_minutes"] == 120


def test_concluido_nao_recalcula_e_em_andamento_usa_duracao_restante_recebida():
    completed_at = datetime(2026, 9, 21, 9)
    result = calculate_cpm([
        activity("feito", 480, completed_at=completed_at),
        activity("andamento", 90, ["feito"]),
    ], project_start=START, calendar=CALENDAR)
    rows = by_id(result)
    assert rows["feito"]["duration_minutes"] == 480
    assert rows["feito"]["early_start"] == rows["feito"]["early_finish"] == completed_at
    assert rows["andamento"]["early_finish"] == datetime(2026, 9, 21, 10, 30)


def test_compra_atrasada_e_fila_de_recurso_restringem_inicio():
    material = activity("PC-1", 0, kind="purchase", not_before=datetime(2026, 9, 21, 11))
    process = activity("laser", 60, ["PC-1"], not_before=datetime(2026, 9, 21, 13), resource_id="LASER-1")
    result = calculate_cpm([material, process], project_start=START, calendar=CALENDAR,
                           contractual_delivery=datetime(2026, 9, 21, 13, 30))
    rows = by_id(result)
    assert rows["laser"]["early_start"] == datetime(2026, 9, 21, 13)
    assert result["projected_finish"] == datetime(2026, 9, 21, 14)
    assert result["delivery_float_minutes"] == -30
    assert result["status"] == "ATRASO_PROJETADO"


def test_compra_compartilhada_tem_criticidade_por_demanda():
    first = calculate_cpm([activity("PC:1:ORC1", 0, kind="purchase", not_before=datetime(2026, 9, 21, 10))],
                          project_start=START, contractual_delivery=datetime(2026, 9, 21, 10), calendar=CALENDAR)
    second = calculate_cpm([activity("PC:1:ORC2", 0, kind="purchase", not_before=datetime(2026, 9, 21, 10))],
                           project_start=START, contractual_delivery=datetime(2026, 9, 21, 14), calendar=CALENDAR)
    assert first["delivery_float_minutes"] == 0
    assert second["delivery_float_minutes"] == 240


def test_dependencia_circular_retorna_erro_controlado():
    with pytest.raises(CircularDependencyError, match="dependencia circular"):
        calculate_cpm([activity("A", 60, ["B"]), activity("B", 60, ["A"])],
                      project_start=START, calendar=CALENDAR)


def test_simulacao_nao_muta_dados_reais_e_recalcula_impacto():
    activities = [activity("A", 60), activity("B", 60, ["A"])]
    result = simulate_delay(activities, "A", 120, project_start=START, calendar=CALENDAR,
                            contractual_delivery=datetime(2026, 9, 21, 12))
    assert activities[0].duration_minutes == 60
    assert result["projected_finish"] == datetime(2026, 9, 21, 12)
    assert result["simulation"]["previous_projected_finish"] == datetime(2026, 9, 21, 10)
    assert result["simulation"]["affected_activity_ids"] == ["A", "B"]


def test_duracao_ausente_e_explicita_e_nao_recebe_fallback_arbitrario():
    result = calculate_cpm([activity("A", None)], project_start=START, calendar=CALENDAR)
    assert result["calculable"] is False
    assert result["status"] == "RISCO_NAO_CALCULAVEL"
    assert by_id(result)["A"]["duration_label"] == "Duracao nao definida"


def test_processo_concluido_sem_data_e_inconsistencia_explicita():
    result = calculate_cpm([activity("A", 60, completed=True)], project_start=START, calendar=CALENDAR)
    assert result["calculable"] is False
    assert by_id(result)["A"]["completed"] is True
    assert any("sem data de conclusao" in warning for warning in result["warnings"])
