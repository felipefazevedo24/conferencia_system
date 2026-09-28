from datetime import datetime, time

import pytest

from conferencia_app.services.cpm_service import (
    CircularDependencyError,
    CpmActivity,
    WorkCalendar,
    calculate_cpm,
    simulate_delay,
)


START = datetime(2026, 9, 21, 8)
CALENDAR = WorkCalendar(shifts=((time(8), time(18)),))


def activity(identifier, duration, predecessors=(), **changes):
    return CpmActivity(identifier, identifier, duration, list(predecessors), **changes)


def by_id(result):
    return {item["id"]: item for item in result["activities"]}


def test_01_caminho_linear_calcula_es_ef_ls_lf():
    result = calculate_cpm(
        [activity("A", 60), activity("B", 120, ["A"]), activity("C", 60, ["B"])],
        project_start=START, calendar=CALENDAR,
    )
    rows = by_id(result)
    assert rows["A"]["early_start"] == START
    assert rows["C"]["early_finish"] == datetime(2026, 9, 21, 12)
    assert rows["B"]["late_start"] == datetime(2026, 9, 21, 9)
    assert result["critical_path"] == ["A", "B", "C"]


def test_02_caminhos_paralelos_identificam_o_mais_longo():
    result = calculate_cpm([
        activity("A", 60), activity("B", 180, ["A"]),
        activity("C", 60, ["A"]), activity("D", 60, ["B", "C"]),
    ], project_start=START, calendar=CALENDAR)
    assert result["critical_path"] == ["A", "B", "D"]


def test_03_multiplos_predecessores_aguardam_todos():
    result = calculate_cpm([
        activity("A", 60), activity("B", 180), activity("C", 30, ["A", "B"]),
    ], project_start=START, calendar=CALENDAR)
    assert by_id(result)["C"]["early_start"] == datetime(2026, 9, 21, 11)


def test_04_folga_positiva_no_caminho_curto():
    result = calculate_cpm([
        activity("A", 60), activity("B", 180), activity("F", 30, ["A", "B"]),
    ], project_start=START, calendar=CALENDAR)
    assert by_id(result)["A"]["schedule_float_minutes"] == 120


def test_05_folga_zero_define_caminho_critico():
    result = calculate_cpm([activity("A", 60), activity("B", 60, ["A"])], project_start=START, calendar=CALENDAR)
    assert all(item["schedule_float_minutes"] == 0 and item["is_critical"] for item in result["activities"])


def test_06_atraso_negativo_considera_meta_da_os():
    result = calculate_cpm(
        [activity("A", 180)], project_start=START,
        target_finish=datetime(2026, 9, 21, 10), calendar=CALENDAR,
    )
    assert result["project_float_minutes"] == -60
    assert by_id(result)["A"]["total_float_minutes"] == -60
    assert result["status"] == "ATRASO_PROJETADO"


def test_07_processo_concluido_permanece_visivel_sem_duracao_futura():
    finished = datetime(2026, 9, 21, 9)
    result = calculate_cpm([
        activity("feito", 480, completed_at=finished), activity("proximo", 60, ["feito"]),
    ], project_start=START, calendar=CALENDAR)
    assert by_id(result)["feito"]["early_start"] == by_id(result)["feito"]["early_finish"] == finished
    assert by_id(result)["proximo"]["early_finish"] == datetime(2026, 9, 21, 10)


def test_08_processo_em_andamento_usa_duracao_restante_recebida():
    result = calculate_cpm([activity("andamento", 90)], project_start=START, calendar=CALENDAR)
    assert by_id(result)["andamento"]["early_finish"] == datetime(2026, 9, 21, 9, 30)


def test_09_fila_de_recurso_adiciona_espera_ao_planejamento():
    result = calculate_cpm([
        activity("solda", 60, resource_id="Solda 02", queue_minutes=120),
    ], project_start=START, calendar=CALENDAR)
    row = by_id(result)["solda"]
    assert row["early_finish"] == datetime(2026, 9, 21, 11)
    assert row["critical_reason"] == "Fila do recurso"


def test_10_compra_atrasada_bloqueia_inicio_do_processo():
    result = calculate_cpm([
        activity("PC", 0, kind="purchase", not_before=datetime(2026, 9, 21, 11)),
        activity("laser", 60, ["PC"]),
    ], project_start=START, calendar=CALENDAR)
    assert by_id(result)["laser"]["early_start"] == datetime(2026, 9, 21, 11)


def test_11_compra_compartilhada_mantem_dependencias_individuais():
    result = calculate_cpm([
        activity("PC:A", 0, kind="purchase", not_before=datetime(2026, 9, 21, 9)),
        activity("PC:B", 0, kind="purchase", not_before=datetime(2026, 9, 21, 12)),
        activity("A", 60, ["PC:A"]), activity("B", 60, ["PC:B"]),
    ], project_start=START, calendar=CALENDAR)
    assert by_id(result)["A"]["early_start"] == datetime(2026, 9, 21, 9)
    assert by_id(result)["B"]["early_start"] == datetime(2026, 9, 21, 12)


def test_12_dependencia_circular_retorna_erro_controlado():
    with pytest.raises(CircularDependencyError, match="dependencia circular"):
        calculate_cpm([activity("A", 60, ["B"]), activity("B", 60, ["A"])], project_start=START, calendar=CALENDAR)


def test_13_simulacao_nao_muta_dados_e_recalcula_impacto():
    activities = [activity("A", 60), activity("B", 60, ["A"])]
    result = simulate_delay(activities, "A", 120, project_start=START, calendar=CALENDAR)
    assert activities[0].duration_minutes == 60
    assert result["projected_finish"] == datetime(2026, 9, 21, 12)
    assert result["simulation"]["affected_activity_ids"] == ["A", "B"]


def test_14_duracao_ausente_e_explicita_sem_fallback_arbitrario():
    result = calculate_cpm([activity("A", None)], project_start=START, calendar=CALENDAR)
    assert result["calculable"] is False
    assert result["status"] == "RISCO_NAO_CALCULAVEL"
    assert by_id(result)["A"]["duration_label"] == "Duracao nao definida"
