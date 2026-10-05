"""Saúde do estoque (Logística > Inventário).

As checagens foram rodadas contra o GRV real em 05/10/2026 (todas < 1 s;
ex.: 19 itens com unidade x custo incoerente, 65 itens parados em terceiros
somando R$ 214.710,63). Aqui a bridge vai sempre com patch."""
from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest

from conferencia_app.extensions import db
from conferencia_app.models import LogisticaEstoqueSaudeFoto
from conferencia_app.services import estoque_saude_service as svc
from conferencia_app.tempo import agora_br
from tests.test_app import build_test_app, login_admin, set_logged_user

BRIDGE = "conferencia_app.services.estoque_saude_service._consultar_bridge"
RESPOSTA = {"sucesso": True, "chave": "terceiros_parados", "quantidade": 65, "valor": 214710.63, "linhas_total": 1,
            "linhas": [{"parceiro": "SUBLIME SERVICOS ESPECIAIS (1618)", "codigo_interno": "6334/015-1",
                        "nome": "OS 6334 - SUPORTE DE SENSORES", "saldo": 1.0, "ultimo_movimento": "2025-01-23",
                        "dias": 620, "valor": 81958.42}]}


@pytest.fixture(autouse=True)
def _limpa_cache():
    svc._CACHE.clear()
    yield
    svc._CACHE.clear()


def test_pagina_renderiza_com_todas_as_checagens_e_link_no_menu(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()
    login_admin(client)
    html = client.get("/logistica/saude-estoque").get_data(as_text=True)
    assert "Saúde do estoque" in html
    assert 'href="/logistica/saude-estoque"' in html
    for c in svc.CHECAGENS:
        assert c["chave"] in html


def test_api_consulta_bridge_usa_cache_e_grava_foto_do_dia(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()
    login_admin(client)
    with patch(BRIDGE, return_value=RESPOSTA) as bridge:
        r1 = client.get("/api/logistica/saude-estoque/terceiros_parados").get_json()
        r2 = client.get("/api/logistica/saude-estoque/terceiros_parados").get_json()
        assert bridge.call_count == 1  # cache
        client.get("/api/logistica/saude-estoque/terceiros_parados?forcar=1")
        assert bridge.call_count == 2
    assert r1["disponivel"] is True and r1["quantidade"] == 65 and r1["valor"] == 214710.63
    assert r1["tendencia"] is None and r2["linhas"][0]["codigo_interno"] == "6334/015-1"
    with app.app_context():
        fotos = LogisticaEstoqueSaudeFoto.query.all()
        assert [(f.chave, f.quantidade) for f in fotos] == [("terceiros_parados", 65)]  # 1 por dia


def test_tendencia_compara_com_foto_mais_antiga_dos_30_dias(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()
    login_admin(client)
    hoje = agora_br().date()
    with app.app_context():
        db.session.add_all([
            LogisticaEstoqueSaudeFoto(dia=hoje - timedelta(days=40), chave="terceiros_parados", quantidade=200),
            LogisticaEstoqueSaudeFoto(dia=hoje - timedelta(days=7), chave="terceiros_parados", quantidade=80, valor=300000.0),
            LogisticaEstoqueSaudeFoto(dia=hoje - timedelta(days=2), chave="terceiros_parados", quantidade=70),
        ])
        db.session.commit()
    with patch(BRIDGE, return_value=RESPOSTA):
        r = client.get("/api/logistica/saude-estoque/terceiros_parados").get_json()
    assert r["tendencia"] == {"dia": (hoje - timedelta(days=7)).isoformat(), "quantidade": 80, "valor": 300000.0}


def test_grv_fora_nao_derruba_tela_e_usa_cache_se_houver(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()
    login_admin(client)
    with patch(BRIDGE, side_effect=RuntimeError("bridge fora")):
        r = client.get("/api/logistica/saude-estoque/terceiros_parados")
    assert r.status_code == 200 and r.get_json()["disponivel"] is False

    with patch(BRIDGE, return_value=RESPOSTA):
        client.get("/api/logistica/saude-estoque/terceiros_parados")
    with patch(BRIDGE, side_effect=RuntimeError("bridge fora")):
        r = client.get("/api/logistica/saude-estoque/terceiros_parados?forcar=1").get_json()
    assert r["disponivel"] is True and r["desatualizado"] is True and r["quantidade"] == 65


def test_checagem_desconhecida_e_permissao(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()
    login_admin(client)
    assert client.get("/api/logistica/saude-estoque/inexistente").status_code == 404
    outro = app.test_client()
    set_logged_user(outro, "portaria_x", "Portaria")
    assert outro.get("/api/logistica/saude-estoque/terceiros_parados").status_code in (302, 403)
    assert outro.get("/logistica/saude-estoque").status_code in (302, 403)


def test_executar_checagem_soma_sobre_todas_as_linhas_e_corta_o_envio():
    conn = MagicMock()
    cur = conn.cursor.return_value.__enter__.return_value
    cur.description = [("codigo_interno",), ("valor",)]
    cur.fetchall.return_value = [(f"C{i}", -10.0 if i % 2 else 10.0) for i in range(svc.MAX_LINHAS + 20)]
    r = svc.executar_checagem(conn, "ajuste_recorrente")
    assert r["quantidade"] == svc.MAX_LINHAS + 20 and r["linhas_total"] == svc.MAX_LINHAS + 20
    assert len(r["linhas"]) == svc.MAX_LINHAS
    assert r["valor"] == 10.0 * (svc.MAX_LINHAS + 20)  # valor em módulo: ajuste negativo também é problema
    assert cur.execute.call_args_list[0].args[0].startswith("SET statement_timeout")

    cur.description = [("retirou",), ("sem_os",)]
    cur.fetchall.return_value = [("ANA", 5), ("JOAO", 7)]
    assert svc.executar_checagem(conn, "saidas_sem_os")["quantidade"] == 12  # conta saídas, não pessoas


def test_sql_das_checagens_escapa_porcentagem_e_so_le():
    for c in svc.CHECAGENS:
        sql = c["sql"] % {"empresa": 1}  # psycopg2 usa a mesma formatação: % solto quebraria
        assert sql.lstrip().upper().startswith(("SELECT", "WITH")), c["chave"]
        assert not any(p in sql.upper() for p in ("INSERT ", "UPDATE ", "DELETE ", "ALTER ", "DROP ")), c["chave"]
        campos = {campo for campo, _, _ in c["colunas"]}
        assert c.get("valor") is None or c["valor"] in campos, c["chave"]


def test_bridge_endpoint_roda_a_checagem_em_conexao_so_leitura(monkeypatch):
    from scripts import erp_lancamento_api_bridge as bridge

    monkeypatch.setattr(bridge, "_config", lambda: {"host": "h", "database": "d", "user": "u"})
    monkeypatch.setattr(bridge, "_authorized", lambda cfg: True)
    conn = MagicMock()
    cur = conn.__enter__.return_value.cursor.return_value.__enter__.return_value
    cur.description = [("codigo_interno",), ("valor",)]
    cur.fetchall.return_value = [("6334/015-1", 81958.42)]
    chamadas = []
    monkeypatch.setattr(bridge, "_conectar", lambda cfg, **kw: chamadas.append(kw) or conn)
    client = bridge.create_app().test_client()

    resp = client.post("/api/erp/estoque/saude", json={"chave": "terceiros_parados"}).get_json()
    assert resp["sucesso"] is True and resp["quantidade"] == 1 and resp["valor"] == 81958.42
    assert chamadas == [{"readonly": True}]
    assert client.post("/api/erp/estoque/saude", json={"chave": "x"}).status_code == 400
