"""Inventario: o local de estoque e' opcional na contagem. Sem local, a
contagem compara com o saldo total do codigo no GRV e nao mexe na
localizacao que o ERP ja' tem."""
from unittest.mock import patch

from conferencia_app.extensions import db
from conferencia_app.models import LogisticaInventarioAjuste, LogisticaInventarioInicial
from tests.test_app import build_test_app, login_admin

ROTAS = "conferencia_app.routes.logistica_inventario_routes"
MOTIVO = "Correção de saldo"
ESTOQUE = {
    "por_local": {"SKU-A|A01-02": {"qtde_total": 4}},
    "por_codigo": {"SKU-A": {"qtde_total": 10.0}, "SKU-B": {"qtde_total": 8.0}},
}


def test_contagem_sem_local_usa_saldo_total_e_nao_sincroniza_localizacao(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()
    login_admin(client)

    with patch(f"{ROTAS}.buscar_estoque_grv", return_value=ESTOQUE), \
            patch(f"{ROTAS}.atualizar_localizacao_estoque", return_value={"ok": True}) as sincronizar, \
            patch(f"{ROTAS}.teams_service.notificar_divergencia_inventario_gestor"):
        bate = client.post("/api/logistica/inventario-inicial", json={"motivo": MOTIVO, "codigo_produto": "SKU-A", "quantidade": 10})
        diverge = client.post("/api/logistica/inventario-inicial", json={"motivo": MOTIVO, "local_codigo": "  ", "codigo_produto": "SKU-B", "quantidade": 5})
        assert bate.status_code == 201, bate.get_json()
        assert diverge.status_code == 201, diverge.get_json()
        sincronizar.assert_not_called()

        # Com local, a sincronizacao com o ERP continua acontecendo.
        com_local = client.post("/api/logistica/inventario-inicial", json={"motivo": MOTIVO, "local_codigo": "a01-02", "codigo_produto": "SKU-A", "quantidade": 4})
        assert com_local.status_code == 201
        sincronizar.assert_called_once_with("SKU-A", "A01-02")

    assert bate.get_json()["registro"]["local_codigo"] == ""
    assert bate.get_json()["ajuste_aberto"] is None
    assert diverge.get_json()["ajuste_aberto"]["diferenca"] == -3
    with app.app_context():
        sem_local = LogisticaInventarioInicial.query.filter_by(local_codigo="").order_by(LogisticaInventarioInicial.id).all()
        # Saldo comparado = total do codigo, nao o de um local.
        assert [(r.codigo_produto, r.qtde_grv_no_momento) for r in sem_local] == [("SKU-A", 10.0), ("SKU-B", 8.0)]
        ajuste = LogisticaInventarioAjuste.query.one()
        assert (ajuste.codigo_produto, ajuste.local_codigo) == ("SKU-B", "")

    # Codigo do produto continua obrigatorio.
    assert client.post("/api/logistica/inventario-inicial", json={"motivo": MOTIVO, "quantidade": 1}).status_code == 400
