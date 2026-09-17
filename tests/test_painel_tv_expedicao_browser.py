"""/painel/expedicao - Torre de Controle travada no slide da Expedicao,
pra TV do setor."""
from pathlib import Path
from threading import Thread

import pytest
from werkzeug.serving import make_server

playwright_api = pytest.importorskip("playwright.sync_api")
sync_playwright, expect = playwright_api.sync_playwright, playwright_api.expect

from tests.test_app import build_test_app


def _servir(app):
    server = make_server("127.0.0.1", 0, app, threaded=True)
    Thread(target=server.serve_forever, daemon=True).start()
    return server


def test_painel_tv_expedicao_abre_travado_no_slide_da_expedicao(tmp_path):
    app = build_test_app(tmp_path)
    server = _servir(app)
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        with sync_playwright() as playwright:
            if not Path(playwright.chromium.executable_path).exists():
                pytest.skip("Instale o Chromium do Playwright para executar este teste.")
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1920, "height": 1080})
            erros_js = []
            page.on("pageerror", lambda erro: erros_js.append(str(erro)))

            page.goto(f"{base}/painel/expedicao")

            # Abre JA na Expedicao (sem isso a tela abriria em branco, porque o
            # HTML estatico marca "recebimento" como ativo).
            expect(page.locator('.slide[data-slide="expedicao"]')).to_have_class("slide slide-exp is-active")
            expect(page.locator('.slide[data-slide="recebimento"]')).not_to_have_class("slide slide-rec is-active")
            expect(page.locator("#exp-kb-board")).to_be_visible()

            # Sem rodizio: os indicadores de troca de slide ficam escondidos.
            expect(page.locator("#slides-nav")).to_be_hidden()

            # As secoes pesadas nem existem no DOM (nao gastam polling/mapa).
            for slide in ("frota", "planejamento", "comex"):
                assert page.locator(f'.slide[data-slide="{slide}"]').count() == 0, slide

            # O carrossel esta preso: continua na Expedicao depois do tempo
            # minimo de troca (15s) - checa uma amostra sem esperar tudo isso.
            page.wait_for_timeout(2000)
            expect(page.locator('.slide[data-slide="expedicao"]')).to_have_class("slide slide-exp is-active")

            assert erros_js == []
            browser.close()
    finally:
        server.shutdown()


def test_painel_tv_completo_segue_com_todos_os_slides(tmp_path):
    """A rota /painel original nao muda: continua com o carrossel completo."""
    app = build_test_app(tmp_path)
    server = _servir(app)
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        with sync_playwright() as playwright:
            if not Path(playwright.chromium.executable_path).exists():
                pytest.skip("Instale o Chromium do Playwright para executar este teste.")
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1920, "height": 1080})
            page.goto(f"{base}/painel")
            for slide in ("recebimento", "expedicao", "frota", "planejamento", "comex"):
                assert page.locator(f'.slide[data-slide="{slide}"]').count() == 1, slide
            expect(page.locator("#slides-nav")).to_be_visible()
            browser.close()
    finally:
        server.shutdown()
