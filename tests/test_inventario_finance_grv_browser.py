"""Etapa Finance do Inventario: confirmar o ajuste executado exige o numero
do documento do GRV, informado num pop-up - por item ou pro lote inteiro."""
from pathlib import Path
from threading import Thread
from unittest.mock import patch

import pytest
from werkzeug.serving import make_server

playwright_api = pytest.importorskip("playwright.sync_api")
sync_playwright, expect = playwright_api.sync_playwright, playwright_api.expect

from conferencia_app.extensions import db
from conferencia_app.models import LogisticaInventarioAjuste
from tests.test_app import build_test_app


def _criar_ajustes(app, quantos, status="Finance"):
    with app.app_context():
        ids = []
        for i in range(quantos):
            ajuste = LogisticaInventarioAjuste(
                codigo_produto=f"SKU-{i}", local_codigo="A01", unidade_medida="UN",
                qtde_contada=7, qtde_estoque_no_momento=10, diferenca=-3, custo_medio=2.0,
                status_modulo=status, status_slug=status.lower(),
            )
            db.session.add(ajuste)
            db.session.flush()
            ids.append(ajuste.id)
        db.session.commit()
        return ids


def test_finance_exige_documento_grv_no_popup_e_confirma_em_lote(tmp_path):
    app = build_test_app(tmp_path)
    ids = _criar_ajustes(app, 3)

    server = make_server("127.0.0.1", 0, app, threaded=True)
    Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        with patch("conferencia_app.services.erp_estoque_service.buscar_estoque_grv", return_value={}), \
                sync_playwright() as playwright:
            if not Path(playwright.chromium.executable_path).exists():
                pytest.skip("Instale o Chromium do Playwright para executar este teste.")
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1500, "height": 900})
            erros_js = []
            page.on("pageerror", lambda erro: erros_js.append(str(erro)))

            assert page.request.post(f"{base}/login", data={"username": "admin", "password": "admin1234"}).ok
            page.goto(f"{base}/logistica/inventario?aba=ajustes")
            expect(page.locator("#ia-tbody tr")).to_have_count(3)

            # ── 1 item pelo menu de acoes: o pop-up exige o documento ──────
            # A lista vem por data decrescente - pega o codigo da linha em vez
            # de supor qual id esta em cima.
            primeira = page.locator("#ia-tbody tr").first
            codigo_clicado = primeira.locator("td").nth(3).inner_text().strip()
            primeira.locator(".ia-actions-btn").click()
            page.locator('#ia-acoes-menu button[data-act="finance"]').click()
            expect(page.locator("#ia-finance-modal")).to_be_visible()

            # Sem o numero, nao confirma (o item continua no Finance).
            page.locator("#ia-finance-salvar").click()
            expect(page.locator("#ia-toast")).to_contain_text("documento do GRV")
            expect(page.locator("#ia-finance-modal")).to_be_visible()

            page.locator("#ia-finance-documento").fill("GRV-000123")
            page.locator("#ia-finance-observacao").fill("lançado junto com o cíclico")
            page.locator("#ia-finance-salvar").click()
            expect(page.locator("#ia-finance-modal")).to_be_hidden()
            expect(page.locator("#ia-tbody tr")).to_have_count(3)

            with app.app_context():
                confirmado = LogisticaInventarioAjuste.query.filter_by(
                    codigo_produto=codigo_clicado).first()
                assert confirmado.status_modulo == "Fiscal"
                assert confirmado.finance_documento_grv == "GRV-000123"
                assert confirmado.finance_observacao == "lançado junto com o cíclico"
            # O documento aparece no historico da linha.
            expect(page.locator("#ia-tbody")).to_contain_text("GRV GRV-000123")

            # ── Lote: seleciona os 2 que sobraram no Finance ──────────────
            caixas = page.locator("#ia-tbody .ia-select-item")
            expect(caixas).to_have_count(2)  # o ja confirmado (Fiscal) nao e' selecionavel
            caixas.nth(0).check()
            caixas.nth(1).check()
            expect(page.locator("#ia-bulk-etapa")).to_have_text("aguardando Finance")
            # A acao oferecida e' a do Finance, nao a de gerar relatorio.
            expect(page.locator("#ia-bulk-finance")).to_be_visible()
            expect(page.locator("#ia-bulk-gerar")).to_be_hidden()

            page.locator("#ia-bulk-finance").click()
            expect(page.locator("#ia-finance-sub")).to_contain_text("2 itens")
            page.locator("#ia-finance-documento").fill("GRV-000999")
            page.locator("#ia-finance-salvar").click()
            expect(page.locator("#ia-finance-modal")).to_be_hidden()

            with app.app_context():
                restantes = LogisticaInventarioAjuste.query.filter(
                    LogisticaInventarioAjuste.codigo_produto != codigo_clicado).all()
                assert len(restantes) == 2
                for ajuste in restantes:
                    assert ajuste.status_modulo == "Fiscal"
                    assert ajuste.finance_documento_grv == "GRV-000999"

            assert erros_js == []
            browser.close()
    finally:
        server.shutdown()


def test_selecao_em_lote_nao_mistura_etapas(tmp_path):
    """Relatorio e Finance tem acoes em lote diferentes - selecionar itens
    das duas etapas juntos nao faria sentido, entao e' barrado."""
    app = build_test_app(tmp_path)
    _criar_ajustes(app, 1, status="Finance")
    _criar_ajustes(app, 1, status="Relatorio")

    server = make_server("127.0.0.1", 0, app, threaded=True)
    Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        with patch("conferencia_app.services.erp_estoque_service.buscar_estoque_grv", return_value={}), \
                sync_playwright() as playwright:
            if not Path(playwright.chromium.executable_path).exists():
                pytest.skip("Instale o Chromium do Playwright para executar este teste.")
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1500, "height": 900})
            assert page.request.post(f"{base}/login", data={"username": "admin", "password": "admin1234"}).ok
            page.goto(f"{base}/logistica/inventario?aba=ajustes")
            expect(page.locator("#ia-tbody .ia-select-item")).to_have_count(2)

            caixas = page.locator("#ia-tbody .ia-select-item")
            caixas.nth(0).check()
            caixas.nth(1).click()  # etapa diferente -> recusado (por isso nao usa check())
            expect(page.locator("#ia-toast")).to_contain_text("mesma etapa")
            expect(caixas.nth(1)).not_to_be_checked()
            expect(page.locator("#ia-bulk-count")).to_have_text("1")
            browser.close()
    finally:
        server.shutdown()
