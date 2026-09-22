"""A ação "Expedir sem conferência" exercitada no navegador de verdade.

Vale a pena em navegador porque o caminho inteiro é de tela: o item só existe
no menu de Ações de uma ordem faturada sem conferência, o comentário é
obrigatório num modal, e o romaneio nasce do próprio clique.
"""
from datetime import datetime
from pathlib import Path
from threading import Thread
from unittest.mock import patch

import pytest
from flask import session
from werkzeug.serving import make_server

playwright_api = pytest.importorskip("playwright.sync_api")
sync_playwright, expect = playwright_api.sync_playwright, playwright_api.expect

from tests.test_app import build_test_app
from conferencia_app.extensions import db
from conferencia_app.models import ExpedicaoOrdemFat, ExpedicaoRomaneio, ExpedicaoRomaneioNF
from conferencia_app.services import expedicao_fat_service as fat_svc

COD = 9001
NF = "770001"
MOTIVO = "Cliente retirou no sábado, sem equipe de conferência no pátio."


def test_expedir_sem_conferencia_cria_o_romaneio_pela_tela(tmp_path):
    app = build_test_app(tmp_path)

    @app.before_request
    def _login():
        session["username"] = "tester"
        session["role"] = "Admin"

    with app.app_context():
        db.session.add(ExpedicaoOrdemFat(
            cod_ordem_fat=COD,
            codigo_interno=f"OF-{COD}",
            cliente="CLIENTE DO PATIO",
            orcamento="ORC-9001",
            numero_nf=NF,
            status=fat_svc.STATUS_FATURADO_SEM_CONF,
            faturado_at=datetime.now(),
        ))
        db.session.commit()

    server = make_server("127.0.0.1", 0, app, threaded=True)
    Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        with patch("conferencia_app.routes.expedicao_romaneio_routes._dados_nf_do_bridge",
                   return_value={}), sync_playwright() as playwright:
            if not Path(playwright.chromium.executable_path).exists():
                pytest.skip("Instale o Chromium do Playwright para executar este teste.")
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            erros_js = []
            page.on("pageerror", lambda erro: erros_js.append(str(erro)))

            page.goto(f"{base}/expedicao/conferencia-cega")
            linha = page.locator("#fat-tbody tr", has_text=f"#{COD}")
            expect(linha).to_be_visible(timeout=15000)

            linha.locator(".ce-actions-btn").click()
            acao = page.locator('#fat-acoes-menu button[data-act="expedir-sem-conf"]')
            expect(acao).to_be_visible()
            acao.click()

            modal = page.locator("#ce-motivo-modal")
            expect(modal).to_have_class("ce-modal open")
            # O comentário é obrigatório: confirmar em branco não fecha nada.
            page.locator("#ce-motivo-confirmar").click()
            expect(page.locator("#ce-motivo-err")).not_to_have_text("")
            expect(modal).to_have_class("ce-modal open")

            page.locator("#ce-motivo-input").fill(MOTIVO)
            page.locator("#ce-motivo-confirmar").click()
            expect(page.locator("#ce-toast")).to_contain_text("criado sem conferência", timeout=15000)

            with app.app_context():
                romaneio = ExpedicaoRomaneio.query.one()
                nf = ExpedicaoRomaneioNF.query.one()
                assert nf.numero_nf == NF
                assert nf.sem_conferencia is True
                assert nf.sem_conferencia_motivo == MOTIVO
                romaneio_id = romaneio.id

            # A marca acompanha a NF no romaneio impresso.
            page.goto(f"{base}/expedicao/romaneio/{romaneio_id}/visualizar")
            expect(page.locator("body")).to_contain_text("EXPEDIDO SEM CONFERÊNCIA")

            assert erros_js == []
            browser.close()
    finally:
        server.shutdown()
