"""Inventario: quem conta informa o motivo. "Correção de saldo" so' abre
ajuste quando a quantidade difere do GRV; nos demais motivos a contagem
sempre vai pro gestor analisar, mesmo batendo."""
from unittest.mock import patch

import pytest

from conferencia_app.models import INVENTARIO_MOTIVOS, LogisticaInventarioAjuste, LogisticaInventarioInicial
from tests.test_app import build_test_app, login_admin

ROTAS = "conferencia_app.routes.logistica_inventario_routes"
ESTOQUE = {"por_local": {}, "por_codigo": {"SKU-A": {"qtde_total": 10.0}, "SKU-B": {"qtde_total": 8.0}}}


@pytest.fixture()
def contar(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()
    login_admin(client)
    with patch(f"{ROTAS}.buscar_estoque_grv", return_value=ESTOQUE), \
            patch(f"{ROTAS}.atualizar_localizacao_estoque", return_value={"ok": True}), \
            patch(f"{ROTAS}.teams_service.notificar_divergencia_inventario_gestor") as teams:
        def _contar(**dados):
            return client.post("/api/logistica/inventario-inicial", json={"codigo_produto": "SKU-A", "quantidade": 10, **dados})
        _contar.app, _contar.client, _contar.teams = app, client, teams
        yield _contar


def test_motivo_e_obrigatorio_e_vem_da_lista(contar):
    assert contar().status_code == 400
    assert contar(motivo="Porque sim").status_code == 400
    assert "motivo" in contar(motivo="").get_json()["error"].lower()
    with contar.app.app_context():
        assert LogisticaInventarioInicial.query.count() == 0

    resp = contar(motivo="Inventário rotativo")
    assert resp.status_code == 201, resp.get_json()
    assert resp.get_json()["registro"]["motivo"] == "Inventário rotativo"


def test_correcao_de_saldo_so_abre_ajuste_quando_a_quantidade_difere(contar):
    assert contar(motivo="Correção de saldo").get_json()["ajuste_aberto"] is None
    diverge = contar(motivo="Correção de saldo", codigo_produto="SKU-B", quantidade=5).get_json()
    assert diverge["ajuste_aberto"]["diferenca"] == -3
    with contar.app.app_context():
        ajuste = LogisticaInventarioAjuste.query.one()
        assert (ajuste.codigo_produto, ajuste.motivo_inventario) == ("SKU-B", "Correção de saldo")
    assert contar.teams.call_count == 1


@pytest.mark.parametrize("motivo", [m for m in INVENTARIO_MOTIVOS if m != "Correção de saldo"])
def test_demais_motivos_sempre_vao_para_analise_do_gestor(contar, motivo):
    # Quantidade igual a do GRV: mesmo assim abre o ajuste, com diferenca zero.
    resp = contar(motivo=motivo).get_json()
    assert resp["ajuste_aberto"]["diferenca"] == 0
    with contar.app.app_context():
        ajuste = LogisticaInventarioAjuste.query.one()
        assert (ajuste.motivo_inventario, ajuste.status_modulo, ajuste.qtde_contada, ajuste.qtde_estoque_no_momento) == (motivo, "Validacao", 10, 10)
    # Sem divergencia nao tem card de divergencia no Teams.
    contar.teams.assert_not_called()

    listado = contar.client.get("/api/logistica/inventario-ajustes").get_json()
    assert [a["motivo_inventario"] for a in listado["ajustes"]] == [motivo]

    # Com diferenca de verdade o card continua saindo.
    contar(motivo=motivo, codigo_produto="SKU-B", quantidade=5)
    assert contar.teams.call_count == 1


def test_tela_de_contagem_oferece_os_motivos(contar):
    html = contar.client.get("/logistica/inventario/novo").get_data(as_text=True)
    assert 'id="inv-motivo"' in html
    for motivo in INVENTARIO_MOTIVOS:
        assert f'<option value="{motivo}">' in html
