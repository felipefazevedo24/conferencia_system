"""Relatorio de ajuste (FORM-08.52): o Tipo de Ajuste ja' abre igual ao
motivo escolhido na contagem, e itens de motivos diferentes nao entram no
mesmo relatorio."""
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


def test_relatorio_abre_com_o_tipo_igual_ao_motivo_da_contagem(tmp_path):
    app = build_test_app(tmp_path)
    with app.app_context():
        for codigo, motivo in (("SKU-GERAL-1", "Inventário Geral"), ("SKU-GERAL-2", "Inventário Geral"), ("SKU-OUTROS", "Outros")):
            db.session.add(LogisticaInventarioAjuste(
                codigo_produto=codigo, local_codigo="A01", unidade_medida="UN", motivo_inventario=motivo,
                qtde_contada=7, qtde_estoque_no_momento=10, diferenca=-3, custo_medio=2.0,
                status_modulo="Relatorio", status_slug="relatorio", gestor_justificativa="Conferido.",
            ))
        db.session.commit()

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
            linha = lambda codigo: page.locator("#ia-tbody tr", has_text=codigo)  # noqa: E731
            expect(linha("SKU-OUTROS")).to_contain_text("Motivo: Outros")

            # Motivos diferentes no mesmo lote: avisa e nao abre o formulario.
            linha("SKU-GERAL-1").locator(".ia-select-item").check()
            linha("SKU-OUTROS").locator(".ia-select-item").check()
            page.locator("#ia-bulk-gerar").click()
            expect(page.locator("#ia-toast, .ia-toast").first).to_contain_text("motivos de inventário diferentes")
            expect(page.locator("#ia-rel-tipo-ajuste")).to_be_hidden()

            # Mesmo motivo: o tipo ja' vem selecionado e continua editavel.
            linha("SKU-OUTROS").locator(".ia-select-item").uncheck()
            linha("SKU-GERAL-2").locator(".ia-select-item").check()
            page.locator("#ia-bulk-gerar").click()
            expect(page.locator("#ia-rel-tipo-ajuste")).to_be_visible()
            expect(page.locator("#ia-rel-tipo-ajuste")).to_have_value("Inventário Geral")
            expect(page.locator("#ia-rel-tipo-ajuste")).to_be_enabled()

            browser.close()
            assert erros_js == []
    finally:
        server.shutdown()
