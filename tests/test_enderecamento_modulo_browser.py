"""Navegador real, backend real e GRV simulado; sem movimentar o estoque de produção."""
from pathlib import Path
from threading import Thread
from unittest.mock import Mock

import pytest
from flask import Flask, session
from werkzeug.serving import make_server

pw = pytest.importorskip('playwright.sync_api')
from conferencia_app.extensions import db
from conferencia_app.models import EnderecoSaldo, EnderecoMovimento
from conferencia_app.routes.recebimento_enderecamento_routes import recebimento_enderecamento_bp
from conferencia_app.services import recebimento_enderecamento_service as receb


def test_modulo_desktop_mobile_transferencias_e_retry(tmp_path, monkeypatch):
    root = Path(__file__).resolve().parents[1]
    app = Flask(__name__, template_folder=str(root/'templates'), static_folder=str(root/'static'))
    app.config.update(TESTING=True, SECRET_KEY='browser', SQLALCHEMY_DATABASE_URI=f'sqlite:///{tmp_path / "module.db"}')
    db.init_app(app); app.register_blueprint(recebimento_enderecamento_bp)

    @app.before_request
    def login():
        session['username']='operador'; session['role']='Admin'

    @app.context_processor
    def context():
        return {'can_access':lambda permission:True, 'user':'operador', 'asset_version':'test'}

    with app.app_context():
        db.create_all()
    erp = {'local':'A'}
    def update(sku, value):
        erp['local']=value
        return {}
    patch = Mock(side_effect=update)
    monkeypatch.setattr(receb, 'buscar_localizacao_produto_grv', lambda sku: erp['local'])
    monkeypatch.setattr(receb, 'atualizar_localizacao_estoque', patch)
    server=make_server('127.0.0.1',0,app)
    thread=Thread(target=server.serve_forever,daemon=True); thread.start()
    try:
        with pw.sync_playwright() as p:
            if not Path(p.chromium.executable_path).exists():
                pytest.skip('Chromium não instalado.')
            browser=p.chromium.launch(headless=True)
            page=browser.new_page(viewport={'width':1440,'height':1000})
            errors=[]; page.on('pageerror', lambda e: errors.append(str(e)))
            page.goto(f'http://127.0.0.1:{server.server_port}/wms/enderecamento')
            pw.expect(page.locator('#end-balances')).to_contain_text('Nenhum saldo encontrado')
            page.locator('#end-initial').click()
            page.locator('#end-sku').fill('SKU-1'); page.locator('#end-sku').press('Enter')
            pw.expect(page.locator('#end-destination')).to_be_focused()
            page.locator('#end-destination').fill('A'); page.locator('#end-destination').press('Enter')
            page.locator('#end-quantity').fill('10'); page.locator('#end-unit').fill('UN')
            page.locator('#end-reason').fill('Contagem inicial da prateleira')
            page.locator('#end-submit').click()
            pw.expect(page.locator('#end-work')).not_to_be_visible()
            pw.expect(page.locator('#end-balances')).to_contain_text('10 UN')
            page.locator('#end-balances').get_by_role('button',name='Movimentar',exact=True).click()
            page.locator('#end-destination').fill('B'); page.locator('#end-quantity').fill('12')
            page.locator('#end-reason').fill('Reorganização do material')
            page.locator('#end-submit').click()
            pw.expect(page.locator('#end-work-feedback')).to_contain_text('maior que o saldo')
            # Correção após erro de negócio deve funcionar na mesma tela.
            page.locator('#end-quantity').fill('4');page.locator('#end-submit').click()
            pw.expect(page.locator('#end-work')).not_to_be_visible()
            pw.expect(page.locator('#end-balances')).to_contain_text('6 UN')
            pw.expect(page.locator('#end-balances')).to_contain_text('4 UN')
            page.screenshot(path=str(tmp_path/'enderecamento-desktop.png'),full_page=True)
            page.set_viewport_size({'width':390,'height':844})
            page.locator('#menu-toggle').click()
            page.wait_for_function("document.getElementById('sidebar-wrapper').getBoundingClientRect().right <= 1")
            if page.locator('#bia-toast-close').is_visible():
                page.locator('#bia-toast-close').click()
            assert page.locator('.end-header h1').bounding_box()['y'] >= 56
            assert page.locator('.end-header p').evaluate('(e)=>e.getBoundingClientRect().right <= innerWidth')
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            page.screenshot(path=str(tmp_path/'enderecamento-mobile.png'),full_page=True)
            page.evaluate("document.documentElement.dataset.theme='escuro'")
            first=page.locator('#end-balances tr').first
            first.get_by_role('button',name='Movimentar',exact=True).click()
            page.locator('#end-destination').fill('B');page.locator('#end-quantity').fill('6')
            page.locator('#end-reason').fill('Transferência do restante')
            page.screenshot(path=str(tmp_path/'enderecamento-mobile-dark.png'),full_page=True)
            assert page.locator('#end-work').evaluate('(e)=>e.scrollWidth<=e.clientWidth')
            # A escrita no GRV falha depois de a operação física ser registrada.
            patch.side_effect=TimeoutError('offline')
            page.locator('#end-submit').click()
            pw.expect(page.locator('#end-work')).not_to_be_visible()
            pw.expect(page.locator('#end-feedback')).to_contain_text('envio ao GRV está pendente')
            page.locator('[data-panel="historico"]').click()
            page.locator('#end-only-pending').check()
            pw.expect(page.locator('#end-history')).to_contain_text('Envio pendente')
            patch.side_effect=update
            page.get_by_role('button',name='Tentar sincronizar',exact=True).click()
            pw.expect(page.locator('#end-history')).to_contain_text('Nenhuma movimentação')
            assert erp['local']=='B'
            with app.app_context():
                assert EnderecoSaldo.query.filter_by(endereco='B').one().quantidade==10
                assert EnderecoMovimento.query.count()==3
            page.locator('[data-panel="receber"]').click()
            pw.expect(page.locator('#pa-list')).to_contain_text('Nenhum material')
            assert errors==[]
            browser.close()
    finally:
        server.shutdown();thread.join(timeout=5)
        with app.app_context():
            db.session.remove();db.engine.dispose()
