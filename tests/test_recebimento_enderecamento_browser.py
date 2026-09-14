"""Mobile UI integration: decoder callback simulated; server validates real receipts."""
from pathlib import Path
from threading import Thread
from unittest.mock import Mock

from flask import Flask, session, render_template
import pytest
from werkzeug.serving import make_server

playwright_api = pytest.importorskip('playwright.sync_api')
sync_playwright, expect = playwright_api.sync_playwright, playwright_api.expect

from conferencia_app.extensions import db
from conferencia_app.models import ItemNota, LocalizacaoArmazem, RecebimentoEnderecamento
from conferencia_app.routes.recebimento_enderecamento_routes import recebimento_enderecamento_bp
from conferencia_app.services import recebimento_enderecamento_service as svc


def test_mobile_camera_workflow(tmp_path, monkeypatch):
    root = Path(__file__).resolve().parents[1]
    app = Flask(__name__, template_folder=str(root/'templates'), static_folder=str(root/'static'))
    app.config.update(TESTING=True, SECRET_KEY='browser-test', SQLALCHEMY_DATABASE_URI=f'sqlite:///{tmp_path / "mobile.db"}')
    db.init_app(app)
    app.register_blueprint(recebimento_enderecamento_bp)

    @app.get('/conferencia')
    def conferencia():
        return render_template('conferente.html', user='tester')

    @app.before_request
    def login():
        session['username']='tester'; session['role']='Admin'

    @app.context_processor
    def context():
        return {'can_access': lambda permission: True, 'user':'tester'}

    with app.app_context():
        db.create_all()
        item = ItemNota(numero_nota='321',codigo_grv='SKU-1',descricao='Peça recebida',status='Concluído',qtd_real=10)
        db.session.add(item); db.session.flush()
        db.session.add(RecebimentoEnderecamento(item_nota_id=item.id,sku='SKU-1',quantidade=10,criado_por='tester'))
        for local in ('A','B'):
            db.session.add(LocalizacaoArmazem(codigo=local,corredor='',prateleira='',posicao=''))
        db.session.commit()
    monkeypatch.setattr(svc,'buscar_localizacao_produto_grv',Mock(return_value='A'))
    update = Mock(return_value={})
    monkeypatch.setattr(svc,'atualizar_localizacao_estoque',update)
    server = make_server('127.0.0.1',0,app)
    thread = Thread(target=server.serve_forever,daemon=True); thread.start()
    try:
        with sync_playwright() as playwright:
            if not Path(playwright.chromium.executable_path).exists():
                pytest.skip('Instale o Chromium do Playwright para executar o teste mobile.')
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={'width':390,'height':844},is_mobile=True,has_touch=True)
            page.goto(f'http://127.0.0.1:{server.server_port}/conferencia')
            expect(page.locator('#receb-enderecamento-painel')).to_be_hidden()
            cards = page.locator('#dashboard-resumo [data-filter]').evaluate_all('(els) => els.map(e => e.dataset.filter)')
            assert cards.index('enderecamento') == cards.index('conferido') + 1
            assert page.locator('a[href="/recebimento/enderecamento"]').count() == 0
            page.locator('[data-filter="enderecamento"]').click()
            expect(page.locator('#receb-notas-painel')).to_be_hidden()
            expect(page.get_by_role('button',name='Endereçar material',exact=True)).to_be_visible()
            assert page.evaluate('typeof Html5Qrcode') == 'function'
            page.screenshot(path=str(tmp_path/'enderecamento-mobile.png'),full_page=True)
            page.get_by_role('button',name='Endereçar material',exact=True).click()
            expect(page.locator('#pa-submit')).to_be_disabled()
            assert page.locator('#pa-work input[name="sku"], #pa-work input[name="endereco"]').count() == 0
            page.evaluate('''() => {
                window.Html5Qrcode = class {
                    async start(camera, options, success) { this.isScanning=true; setTimeout(()=>success(window.testCode),20); }
                    async stop() {this.isScanning=false;}
                    clear() {}
                };
                window.testCode='WRONG';
            }''')
            page.locator('#pa-sku').click()
            expect(page.locator('#pa-work-feedback')).to_contain_text('não corresponde')
            expect(page.locator('#pa-address')).to_be_disabled()
            page.evaluate("window.testCode='SKU-1'")
            page.locator('#pa-sku').click()
            expect(page.locator('#pa-work-feedback')).to_contain_text('já está endereçado em: A')
            page.locator('#pa-reason').select_option('superlotado')
            page.evaluate("window.testCode='B'")
            page.locator('#pa-address').click()
            expect(page.locator('#pa-submit')).to_be_enabled()
            page.locator('#pa-submit').click()
            expect(page.locator('#pa-feedback')).to_contain_text('sincronizado com o GRV')
            update.assert_called_once_with('SKU-1','A;B')
            page.get_by_role('button',name='Ver histórico e origem').click()
            expect(page.locator('#pa-history-body')).to_contain_text('A;B')
            page.locator('#pa-history-close').click()
            page.locator('[data-filter="conferido"]').click()
            expect(page.locator('#receb-enderecamento-painel')).to_be_hidden()
            expect(page.locator('#receb-notas-painel')).to_be_visible()
            page.goto(f'http://127.0.0.1:{server.server_port}/conferencia?etapa=enderecamento&ids=1')
            expect(page.locator('#receb-enderecamento-painel')).to_be_visible()
            browser.close()
    finally:
        server.shutdown(); thread.join(timeout=5)
        with app.app_context():
            db.session.remove(); db.engine.dispose()
