"""Tela de contagem do Inventario seguindo o cadastro do GRV: a unidade trava
no valor do GRV e o lote vira obrigatorio pra item controlado por lote."""
from pathlib import Path
from threading import Thread
from unittest.mock import patch

import pytest
from werkzeug.serving import make_server

playwright_api = pytest.importorskip("playwright.sync_api")
sync_playwright, expect = playwright_api.sync_playwright, playwright_api.expect

from conferencia_app.models import LogisticaInventarioInicial
from tests.test_app import build_test_app

ESTOQUE_FAKE = {
    "por_local": {},
    "por_codigo": {
        "CHAPA-01": {"qtde_total": 500.0, "item": "CHAPA 3/16", "unidade": "KG", "tipo_controle": "1"},
        "PARAF-01": {"qtde_total": 90.0, "item": "PARAFUSO M8", "unidade": "PC", "tipo_controle": "0"},
    },
}


def test_contagem_trava_unidade_e_exige_lote_conforme_grv(tmp_path):
    app = build_test_app(tmp_path)
    server = make_server("127.0.0.1", 0, app, threaded=True)
    Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        with patch("conferencia_app.routes.logistica_inventario_routes.buscar_estoque_grv", return_value=ESTOQUE_FAKE), \
                patch("conferencia_app.routes.logistica_inventario_routes.atualizar_localizacao_estoque", return_value={}), \
                sync_playwright() as playwright:
            if not Path(playwright.chromium.executable_path).exists():
                pytest.skip("Instale o Chromium do Playwright para executar este teste.")
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            erros_js = []
            page.on("pageerror", lambda erro: erros_js.append(str(erro)))

            assert page.request.post(f"{base}/login", data={"username": "admin", "password": "admin1234"}).ok
            page.goto(f"{base}/logistica/inventario?aba=inventariar")

            unidade = page.locator("#inv-unidade")
            lote_req = page.locator("#inv-lote-req")

            # Item com lote: unidade trava em KG e o asterisco do lote aparece.
            page.fill("#inv-local", "A01")
            page.fill("#inv-codigo", "CHAPA-01")
            page.locator("#inv-codigo").blur()
            expect(unidade).to_be_disabled()
            expect(unidade).to_have_value("KG")
            expect(page.locator("#inv-unidade-hint")).to_contain_text("CHAPA 3/16")
            expect(lote_req).to_be_visible()

            page.fill("#inv-quantidade", "480")
            page.click("#inv-salvar")
            expect(page.locator("#inv-status")).to_contain_text("informe o lote")

            page.fill("#inv-lote", "L-2026-07")
            page.click("#inv-salvar")
            expect(page.locator("#inv-status")).to_contain_text("salvo com sucesso")
            # Limpar volta o formulario ao estado livre.
            expect(unidade).to_be_enabled()
            expect(lote_req).to_be_hidden()

            # Item sem controle de lote: unidade PC, lote opcional.
            page.fill("#inv-local", "A02")
            page.fill("#inv-codigo", "PARAF-01")
            page.locator("#inv-codigo").blur()
            expect(unidade).to_have_value("PC")
            expect(lote_req).to_be_hidden()
            page.fill("#inv-quantidade", "90")
            page.click("#inv-salvar")
            # A mensagem "salvo" do item anterior ainda esta na tela - espera o
            # formulario limpar, que so' acontece depois DESTE salvamento.
            expect(page.locator("#inv-codigo")).to_have_value("")

            # Codigo desconhecido: libera a unidade e avisa.
            page.fill("#inv-codigo", "NAO-EXISTE")
            page.locator("#inv-codigo").blur()
            expect(page.locator("#inv-unidade-hint")).to_contain_text("não encontrado no GRV")
            expect(unidade).to_be_enabled()

            assert erros_js == []
            browser.close()
    finally:
        server.shutdown()

    with app.app_context():
        gravados = {r.codigo_produto: r for r in LogisticaInventarioInicial.query.all()}
    assert gravados["CHAPA-01"].unidade_medida == "KG"
    assert gravados["CHAPA-01"].lote == "L-2026-07"
    assert gravados["PARAF-01"].unidade_medida == "PC"
