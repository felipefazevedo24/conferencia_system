"""Contrato de dados do cronograma integrado ao painel visual."""
from datetime import datetime
from unittest.mock import patch

from test_producao_contexto import api, service  # noqa: F401 - fixture compartilhada


def budget_row(**changes):
    row = {
        "cod_orcamento": 72, "n_orcamento": 7222, "versao": "A",
        "dt_previsao_entrega": datetime(2026, 9, 25),
        "cod_os": 801, "n_os": "7807/001", "cliente": "Cliente real",
        "titulo": "Transportador", "u_classificacao": "CMS",
        "status_servico": "EM PRODUÇÃO", "principal": True,
        "operacoes_total": 10, "operacoes_concluidas": 10,
    }
    row.update(changes)
    return row


def test_orcamento_agrupa_os_e_calcula_progresso_sem_consulta_por_os():
    rows = [budget_row(), budget_row(cod_os=802, n_os="7807/002", principal=False,
                                     operacoes_total=25, operacoes_concluidas=20)]
    with patch.object(service, "_metadata_read", return_value=rows) as fetch:
        delivery = service.listar_entregas_cronograma(9, 2026)[0]
    assert fetch.call_count == 1
    assert delivery["data_entrega"] == "2026-09-25T00:00:00"
    assert [item["numero"] for item in delivery["os"]] == ["7807/001", "7807/002"]
    assert [item["principal"] for item in delivery["os"]] == [True, False]
    assert (delivery["operacoes_concluidas"], delivery["operacoes_total"], delivery["percentual"]) == (30, 35, 86)


def test_filtros_usam_intervalo_semiaberto_e_pesquisa_combinada():
    with patch.object(service, "_metadata_read", return_value=[]) as fetch:
        assert service.listar_entregas_cronograma(12, 2026, "CMS", "7807/002") == []
    params = fetch.call_args.args[2]
    assert params == {"cod_empresa": 1, "inicio": "2026-12-01",
                      "fim": "2027-01-01", "classificacao": "CMS",
                      "pesquisa": "%7807/002%"}


def test_orcamento_sem_os_e_classificacoes_dinamicas():
    with patch.object(service, "_metadata_read", return_value=[budget_row(cod_os=None, n_os=None)]):
        delivery = service.listar_entregas_cronograma(9, 2026)[0]
    assert delivery["os"] == []
    assert delivery["status"] == "OS não gerada"
    with patch.object(service, "_metadata_read", return_value=[
        {"classificacao": "Moldes"}, {"classificacao": "CMS"}, {"classificacao": "CMS"}
    ]):
        assert service.listar_classificacoes_cronograma() == ["CMS", "Moldes"]


def test_rotas_validam_periodo_e_repassam_filtros(api):
    client, _ = api
    assert client.get("/api/producao/cronograma-entregas?mes=13&ano=2026").status_code == 400
    with patch.object(service, "listar_entregas_cronograma", return_value=[]) as list_deliveries:
        response = client.get("/api/producao/cronograma-entregas?mes=9&ano=2026&classificacao=CMS&pesquisa=7807")
    assert response.status_code == 200
    assert response.get_json() == {"periodo": {"mes": 9, "ano": 2026}, "entregas": []}
    list_deliveries.assert_called_once_with(9, 2026, "CMS", "7807")
