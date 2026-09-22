"""Homologacao de fornecedor: digitar o CNPJ e sair do campo carrega os dados
cadastrais na tela (mesma consulta da Atualizacao Cadastral)."""
from pathlib import Path
from threading import Thread
from unittest.mock import patch

import pytest
from werkzeug.serving import make_server

playwright_api = pytest.importorskip("playwright.sync_api")
sync_playwright, expect = playwright_api.sync_playwright, playwright_api.expect

from tests.test_app import _CARTAO_CNPJ_FAKE, build_test_app


def test_homologacao_preenche_dados_ao_digitar_o_cnpj(tmp_path):
    app = build_test_app(tmp_path)
    server = make_server("127.0.0.1", 0, app, threaded=True)
    Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        with patch("conferencia_app.services.cadastro_workflow_service.consultar_cartao_cnpj",
                   return_value=dict(_CARTAO_CNPJ_FAKE)), \
                sync_playwright() as playwright:
            if not Path(playwright.chromium.executable_path).exists():
                pytest.skip("Instale o Chromium do Playwright para executar este teste.")
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            erros_js = []
            page.on("pageerror", lambda erro: erros_js.append(str(erro)))

            assert page.request.post(f"{base}/login", data={"username": "admin", "password": "admin1234"}).ok
            page.goto(f"{base}/compras/homologacao")
            page.get_by_role("button", name="Nova homologação").click()

            campo = lambda k: page.locator(f'#hf-corpo [data-campo="{k}"]')  # noqa: E731
            # Telefone ja' digitado pelo comprador: o do cartao (do contador)
            # nao pode sobrescrever.
            campo("telefone").fill("19 99999-0000")

            campo("cnpj").fill("11222333000181")
            campo("cnpj").blur()

            expect(campo("razao_social")).to_have_value("ACOS EXEMPLO LTDA")
            expect(campo("cnpj")).to_have_value("11.222.333/0001-81")
            expect(campo("nome_fantasia")).to_have_value("ACOS EXEMPLO")
            expect(campo("inscricao_estadual")).to_have_value("671234567890")
            expect(campo("cidade_estado")).to_have_value("Sumare - SP")
            assert "CEP 13170000" in campo("endereco").input_value()
            expect(campo("email")).to_have_value("fiscal@contador.com.br")
            expect(campo("telefone")).to_have_value("19 99999-0000")
            expect(page.locator("#hf-toast")).to_contain_text("carregados")

            assert erros_js == []
            browser.close()
    finally:
        server.shutdown()
