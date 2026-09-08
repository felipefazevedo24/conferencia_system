from datetime import datetime
from unittest.mock import patch

import pytest

from test_app import build_test_app, set_logged_user
from conferencia_app.extensions import db
from conferencia_app.models import ExpedicaoOrdemFat, ExpedicaoOrdemST
from conferencia_app.services.expedicao_log_service import registrar_log


@pytest.fixture
def auditoria(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()
    set_logged_user(client, "admin", "Admin")
    with app.app_context():
        fat = ExpedicaoOrdemFat(cod_ordem_fat=123, codigo_interno="OF-000123", cliente="Cliente A",
                               excluido=True, excluido_at=datetime(2026, 9, 8, 23, 59),
                               excluido_by="ana", excluido_motivo="Pedido duplicado", numero_nf="900")
        st = ExpedicaoOrdemST(cod_ordem_compra="OC99", fornecedor="Fornecedor B", excluido=True,
                             excluido_at=datetime(2026, 9, 9, 0, 0), excluido_by="bruno",
                             excluido_motivo="Cancelamento 100%")
        db.session.add_all([fat, st, ExpedicaoOrdemFat(cod_ordem_fat=456, cliente="Ordem ativa")])
        db.session.flush()
        registrar_log(origem="fat", ordem_id=fat.id, cod_ordem=123, acao="exclusao", usuario="ana",
                      status_anterior="Conferido", status_novo="Conferido", divergente=False,
                      pos_faturamento=False, diff_cabecalho=[{"campo": "excluido", "para": "Pedido duplicado"}], diff_itens=[])
        db.session.commit()
        ids = {"fat": fat.id, "st": st.id}
    return client, ids


def test_listagem_automatica_excluidas_paginada(auditoria):
    client, ids = auditoria
    with patch("conferencia_app.routes.expedicao_auditoria_routes.log_svc.listar_logs") as logs:
        resp = client.get("/api/expedicao/auditoria/excluidas?por_pagina=1")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["total"] == 2
        assert data["totais"] == {"fat": 1, "st": 1}
        assert data["paginas"] == 2
        assert data["resultados"][0]["origem"] == "st"
        assert data["resultados"][0]["excluido_by"] == "bruno"
        assert "historico" not in data["resultados"][0]
        logs.assert_not_called()
    data = client.get("/api/expedicao/auditoria/excluidas?por_pagina=1&pagina=2").get_json()
    assert data["resultados"][0]["id"] == ids["fat"]
    assert data["resultados"][0]["origem"] == "fat"


def test_filtros_exclusao_e_datas_inclusivas(auditoria):
    client, _ = auditoria
    for q in ("ana", "duplicado", "Cliente A", "900", "OF-000123", "123"):
        data = client.get("/api/expedicao/auditoria/excluidas", query_string={"q": q}).get_json()
        assert data["total"] == 1
        assert data["resultados"][0]["origem"] == "fat"
    for filtros in ({"origem": "fat"}, {"inicio": "2026-09-08", "fim": "2026-09-08"}):
        data = client.get("/api/expedicao/auditoria/excluidas", query_string=filtros).get_json()
        assert data["total"] == 1
        assert data["resultados"][0]["origem"] == "fat"
    data = client.get("/api/expedicao/auditoria/excluidas", query_string={"q": "%"}).get_json()
    assert data["total"] == 1  # Percentual literal, sem virar curinga SQL.
    assert data["resultados"][0]["origem"] == "st"
    assert client.get("/api/expedicao/auditoria/excluidas?q=inexistente").get_json()["total"] == 0
    for filtros in ({"inicio": "2026-09-09", "fim": "2026-09-08"}, {"fim": "invalida"}, {"pagina": "abc"}, {"origem": "outra"}):
        assert client.get("/api/expedicao/auditoria/excluidas", query_string=filtros).status_code == 400


def test_historico_exato_sem_codigo_interno_e_acesso(auditoria):
    client, ids = auditoria
    data = client.get(f'/api/expedicao/auditoria/ordem/fat/{ids["fat"]}').get_json()
    assert data["historico"][0]["acao"] == "exclusao"
    assert data["historico"][0]["usuario"] == "ana"
    data = client.get(f'/api/expedicao/auditoria/ordem/st/{ids["st"]}').get_json()
    assert data["codigo_interno"] is None
    assert data["cod_ordem"] == "OC99"
    assert client.get('/api/expedicao/auditoria/ordem/outra/1').status_code == 404
    assert client.get('/api/expedicao/auditoria/buscar?q=OF-000123').get_json()["resultados"][0]["historico"]
    set_logged_user(client, "fiscal_auditoria", "Fiscal")
    for url in ("/expedicao/auditoria", "/api/expedicao/auditoria/excluidas", f'/api/expedicao/auditoria/ordem/fat/{ids["fat"]}'):
        assert client.get(url).status_code == 403
