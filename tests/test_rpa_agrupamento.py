"""Regras de consulta e preparo do RPA agrupamento."""
import importlib

import pytest

from test_producao_imagens import producao_service  # carrega o pacote de teste isolado


service = importlib.import_module("production_test_app.services.rpa_agrupamento_service")


def row(code="167245", material="CHAPA A36 - 1/2 (12,70MM)", **overrides):
    value = dict(n_os="9321", cod_os_completo="9321/001", codigo=code,
                 u_classificacao="CMS", descricao="4-142", material=material,
                 largura_mm=1000, altura_mm=2000, comprimento_mm=0,
                 qtde_formato=2, obs='CORTAR CHAPA A36 ESP 1/2"',
                 cliente="CLIENTE", status_os="ABERTA", cancelada=0,
                 processo_finalizado=0, tipo_servico="CORTE LASER")
    value.update(overrides)
    return value


@pytest.mark.parametrize("overrides", [
    {"status_os": "CONCLUIDA"}, {"status_os": "CANCELADA", "cancelada": 1},
    {"processo_finalizado": 1}, {"tipo_servico": "DOBRA"},
])
def test_processo_invalido_nao_e_elegivel(overrides):
    assert not service.preparar_registros([row(**overrides)])[0]["elegivel"]


def test_processo_valido_e_payload_idempotente():
    items = service.preparar_registros([row(), row("167241"), row()])
    assert all(item["elegivel"] for item in items)
    first = service.montar_payload(items)
    assert first == service.montar_payload(items)
    assert first["codigos_destacados_para_agrupamento"] == ["167241", "167245"]
    assert first["os_selecionadas"] == ["9321"]
    assert first["espessura_extraida"] == "12.70MM"
    assert first["norma_extraida"] == "A36"


def test_codigo_de_processo_e_a_unidade_de_deduplicacao(monkeypatch):
    monkeypatch.setattr(service, "fetch_all", lambda *_args, **_kwargs: [
        row(),
        row(cod_os_completo="9321/002"),
        row(cod_os_completo="9321/003"),
    ])
    items = service.consultar()
    assert [item["codigo_processo"] for item in items] == ["167245"]


def test_consulta_retorna_somente_elegiveis_e_aplica_limite_apos_deduplicar(monkeypatch):
    rows = [row(), row(), row("167241"), row("167240", status_os="CONCLUIDA")]
    captured = {}

    def fake_fetch_all(_query, params):
        captured.update(params)
        return rows

    monkeypatch.setattr(service, "fetch_all", fake_fetch_all)
    result = service.consultar(limite=2)

    assert [item["codigo_processo"] for item in result] == ["167245", "167241"]
    assert captured["limite"] == 20


def test_diagnostico_contabiliza_etapas_por_codigo():
    rows = [
        row(), row(),
        row("167241", status_os="CONCLUIDA"),
        row("167240", processo_finalizado=1),
        row("167239", tipo_servico="DOBRA"),
    ]
    result = [item for item in service.preparar_registros(rows) if item["elegivel"]]
    diagnostic = service.diagnosticar_registros(rows, result)

    assert diagnostic == {
        "linhas_brutas": 5,
        "apos_corte_laser": 3,
        "apos_status_valido": 2,
        "apos_nao_finalizados": 1,
        "duplicados_removidos": 1,
        "apos_deduplicacao": 1,
        "descartados_por_material": 0,
        "total_final": 1,
    }


def test_espessura_incompativel_descarta_linha():
    assert service.preparar_registros([row(obs='CORTAR CHAPA A36 ESP 3/8"')]) == []


def test_material_com_maior_pontuacao_prevalece():
    items = service.preparar_registros([
        row(material="CHAPA A36 - 1/2 (12,70MM)"),
        row(material="CHAPA A572 - 1/2 (12,70MM)"),
    ])
    assert [item["norma"] for item in items] == ["A36"]


def test_materiais_diferentes_nao_formam_grupo():
    items = service.preparar_registros([
        row(), row("167241", material="CHAPA A572 - 1/2 (12,70MM)", obs="CORTAR A572 ESP 1/2\""),
    ])
    with pytest.raises(ValueError, match="mesma chapa"):
        service.montar_payload(items)


def test_codigo_unico_e_invalido_sao_recusados():
    item = service.preparar_registros([row()])[0]
    with pytest.raises(ValueError, match="dois códigos"):
        service.montar_payload([item, item])
    item["elegivel"] = False
    with pytest.raises(ValueError, match="deixou de ser elegível"):
        service.montar_payload([item])
