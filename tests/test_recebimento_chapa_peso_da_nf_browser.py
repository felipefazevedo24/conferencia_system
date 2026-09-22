"""A tela do conferente para de pedir o peso quando o item é chapa.

Em navegador porque tudo o que mudou é comportamento de tela: o campo de KG
trava, a barra de progresso passa a contar a linha como pronta e o botão de
validar libera sem ninguém digitar peso nenhum.
"""
from pathlib import Path
from threading import Thread

import pytest
from flask import session
from werkzeug.serving import make_server

playwright_api = pytest.importorskip("playwright.sync_api")
sync_playwright, expect = playwright_api.sync_playwright, playwright_api.expect

from tests.test_app import build_test_app
from conferencia_app.extensions import db
from conferencia_app.models import ItemNota

NOTA = "900A"


def test_item_de_chapa_nao_pede_peso_na_tela(tmp_path):
    app = build_test_app(tmp_path)

    @app.before_request
    def _login():
        session["username"] = "tester"
        session["role"] = "Admin"

    with app.app_context():
        db.session.add_all([
            ItemNota(numero_nota=NOTA, fornecedor="Aços", codigo="CH-1", codigo_grv="GRV-1",
                     descricao="Chapa aço 10mm", qtd_real=314.0,
                     unidade_comercial="KG", status="Pendente"),
            ItemNota(numero_nota=NOTA, fornecedor="Aços", codigo="PC-1", codigo_grv="GRV-2",
                     descricao="Parafuso", qtd_real=10.0,
                     unidade_comercial="PC", status="Pendente"),
        ])
        db.session.commit()
        chapa_id = ItemNota.query.filter_by(codigo="CH-1").one().id
        parafuso_id = ItemNota.query.filter_by(codigo="PC-1").one().id

    server = make_server("127.0.0.1", 0, app, threaded=True)
    Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        with sync_playwright() as playwright:
            if not Path(playwright.chromium.executable_path).exists():
                pytest.skip("Instale o Chromium do Playwright para executar este teste.")
            navegador = playwright.chromium.launch(headless=True)
            pagina = navegador.new_page(viewport={"width": 1280, "height": 900})
            erros = []
            pagina.on("pageerror", lambda erro: erros.append(str(erro)))

            pagina.goto(f"{base}/conferencia")
            pagina.evaluate(f"abrir('{NOTA}')")
            expect(pagina.locator(f'.qtd-input[data-id="{chapa_id}"]')).to_be_visible(timeout=15000)

            campo_chapa = pagina.locator(f'.qtd-input[data-id="{chapa_id}"]')
            campo_parafuso = pagina.locator(f'.qtd-input[data-id="{parafuso_id}"]')
            botao_validar = pagina.locator("#btn-finalizar")

            # Estado inicial: os dois itens pedem contagem.
            expect(campo_chapa).to_be_enabled()
            expect(botao_validar).to_be_disabled()
            campo_parafuso.fill("10")
            expect(botao_validar).to_be_disabled()
            expect(pagina.locator("#conf-progress-count")).to_have_text("1 / 2")

            # Marca como chapa: só UND e medidas.
            pagina.locator(f'#chapa-btn-{chapa_id}').click()
            pagina.locator("#chapa-modal-switch").check()
            pagina.locator("#chapa-modal-input").fill("2")
            for medida, valor in (("espessura", "10"), ("largura", "1000"), ("comprimento", "2000")):
                pagina.locator(f'[data-chapa-medida="{medida}"]').fill(valor)
            pagina.locator("#btn-salvar-chapa").click()
            pagina.wait_for_timeout(300)

            # O peso deixou de ser pedido: campo travado e linha já conta.
            expect(campo_chapa).to_be_disabled()
            assert "peso-da-nf" in (campo_chapa.get_attribute("class") or "")
            assert campo_chapa.input_value() == ""
            expect(pagina.locator(f'.item-alerta[data-id="{chapa_id}"]')).to_contain_text("vem da NF")
            expect(pagina.locator("#conf-progress-count")).to_have_text("2 / 2")
            expect(botao_validar).to_be_enabled()

            # Validar fecha sem divergência, com o peso da nota.
            botao_validar.click()
            expect(pagina.locator("#modal-conteudo")).to_contain_text("Conferência validada", timeout=15000)

            with app.app_context():
                assert db.session.get(ItemNota, chapa_id).qtd_chapas_und == 2

            # Desmarcar devolve o campo ao conferente.
            pagina.locator("#modal-resultado").evaluate("el => el.style.display = 'none'")
            pagina.locator(f'#chapa-btn-{chapa_id}').click()
            pagina.locator("#chapa-modal-switch").uncheck()
            pagina.locator("#btn-salvar-chapa").click()
            pagina.wait_for_timeout(300)
            expect(campo_chapa).to_be_enabled()
            expect(botao_validar).to_be_disabled()

            assert erros == []
            navegador.close()
    finally:
        server.shutdown()
