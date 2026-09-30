"""Tela do Cardex no navegador de verdade: resumo agrupado por família,
clique no item abrindo o Modelo 7 e aba de fechamentos. A lógica de
montagem é toda JS; a fixture é a mesma do test_logistica_cardex."""
from pathlib import Path
from threading import Thread
from unittest.mock import patch

import pytest
from werkzeug.serving import make_server

playwright_api = pytest.importorskip("playwright.sync_api")
sync_playwright, expect = playwright_api.sync_playwright, playwright_api.expect

from conferencia_app.services import logistica_cardex_service as svc
from tests.test_app import build_test_app
from tests.test_logistica_cardex import _grv


def test_cardex_resumo_item_e_fechamentos_no_navegador(tmp_path):
    app = build_test_app(tmp_path)
    svc.limpar_cache()
    server = make_server("127.0.0.1", 0, app, threaded=True)
    Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        with patch.object(svc, "buscar_cardex_grv", return_value=_grv()), sync_playwright() as playwright:
            if not Path(playwright.chromium.executable_path).exists():
                pytest.skip("Instale o Chromium do Playwright para executar este teste.")
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            erros_js = []
            page.on("pageerror", lambda erro: erros_js.append(str(erro)))

            assert page.request.post(f"{base}/login", data={"username": "admin", "password": "admin1234"}).ok
            page.goto(f"{base}/logistica/cardex?inicio=2026-09-01")
            page.fill("#cx-inicio", "2026-09-01")
            page.fill("#cx-fim", "2026-09-30")
            page.click("#cx-btn-consultar")

            # Uma família só: já abre com o item e o total geral.
            expect(page.locator("#cx-resumo-body tr.cx-item")).to_have_count(1)
            expect(page.locator("#cx-resumo-foot tr.cx-total")).to_contain_text("TOTAL GERAL")
            expect(page.locator("#cx-s-saidas")).to_have_text("R$ 85,47")
            expect(page.locator("#cx-deps")).to_contain_text("1 · PRINCIPAL")

            # Grade de ERP: clique seleciona, teclado navega, Enter abre.
            page.locator("#cx-resumo-body tr.cx-item").click()
            expect(page.locator("#cx-resumo-body tr.cx-item.is-sel")).to_have_count(1)
            expect(page.locator("[data-pane=resumo]")).to_be_visible()

            page.locator("#cx-resumo-grid").focus()
            page.keyboard.press("Enter")
            expect(page.locator("[data-pane=item]")).to_be_visible()
            linhas = page.locator("#cx-item-body tr")
            expect(linhas).to_have_count(5)  # saldo inicial + 3 movimentos + saldo final
            expect(linhas.nth(0)).to_contain_text("4.518,620")
            expect(linhas.nth(2)).to_contain_text("Saída 53918")
            expect(linhas.nth(2).locator("td.cx-medio")).to_have_text("4,1168")
            expect(linhas.nth(2)).to_contain_text("28.315,03")
            # Entradas e saídas em colunas separadas; imposto da NF por unidade.
            expect(linhas.nth(1)).to_contain_text("0,6100")
            expect(linhas.nth(3)).to_contain_text("sem imposto na NF")
            linhas.nth(1).click()
            expect(page.locator("#cx-item-body tr.cx-det")).to_contain_text("NF 78851")

            page.click("[data-tab=fechamentos]")
            expect(page.locator("#cx-fech-body")).to_contain_text("Nenhum mês fechado ainda.")
            assert erros_js == []
            browser.close()
    finally:
        server.shutdown()
        svc.limpar_cache()
