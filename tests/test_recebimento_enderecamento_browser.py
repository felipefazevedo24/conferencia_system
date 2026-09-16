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
        item = ItemNota(numero_nota='321',chave_acesso='1'*44,codigo_grv='SKU-1',descricao='Peça recebida',status='Concluído',qtd_real=10)
        db.session.add(item); db.session.flush()
        db.session.add(RecebimentoEnderecamento(item_nota_id=item.id,sku='SKU-1',quantidade=10,criado_por='tester'))
        segundo = ItemNota(numero_nota='321',chave_acesso='1'*44,codigo_grv='SKU-1',descricao='Segundo material',status='Concluído',qtd_real=10)
        db.session.add(segundo); db.session.flush()
        db.session.add(RecebimentoEnderecamento(item_nota_id=segundo.id,sku='SKU-1',quantidade=10,criado_por='tester'))
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
            erros_js, popups = [], []
            page.on('pageerror', lambda error: erros_js.append(str(error)))
            page.on('dialog', lambda dialog: (popups.append(dialog.type), dialog.dismiss()))
            page.goto(f'http://127.0.0.1:{server.server_port}/conferencia')
            expect(page.locator('#receb-enderecamento-painel')).to_be_hidden()
            cards = page.locator('#dashboard-resumo [data-filter]').evaluate_all('(els) => els.map(e => e.dataset.filter)')
            assert cards.index('enderecamento') == cards.index('conferido') + 1
            assert page.locator('a[href="/recebimento/enderecamento"]').count() == 0
            page.evaluate("""() => {
                document.getElementById('lista-view').classList.add('hidden');
                document.getElementById('conf-view').classList.remove('hidden');
                renderItensConferencia([{id:99,codigo:'CH-99',descricao:'Chapa teste',unidade:'KG',qtd_real:314}]);
            }""")
            page.locator('#chapa-btn-99').click()
            page.locator('#chapa-modal-switch').check()
            page.locator('#chapa-modal-input').fill('2')
            page.locator('[data-chapa-medida="espessura"]').fill('10')
            page.locator('[data-chapa-medida="largura"]').fill('1000')
            page.locator('[data-chapa-medida="comprimento"]').fill('2000')
            expect(page.locator('#chapa-modal-preview')).to_contain_text('157 kg/peça')
            page.locator('#btn-salvar-chapa').click()
            chapa_payload = page.evaluate('() => coletarChapas()["99"]')
            assert chapa_payload == {'quantidade':'2','material':'aco_carbono','formato':'chapa','dimensoes':{'espessura':10,'largura':1000,'comprimento':2000}}
            page.evaluate("""() => { document.getElementById('conf-view').classList.add('hidden'); document.getElementById('lista-view').classList.remove('hidden'); }""")
            await_url = page.url
            page.evaluate('() => { void oferecerEnderecamento([1, 2]); }')
            expect(page.locator('#receb-dialog-title')).to_have_text('Recebimento concluído')
            page.get_by_role('button', name='Deixar para depois', exact=True).click()
            expect(page.locator('#receb-dialog')).not_to_be_visible()
            expect(page.locator('#receb-enderecamento-painel')).to_be_hidden()
            page.route('**/validar', lambda route: route.fulfill(json={'enderecamento_ids': [1, 2]}))
            page.evaluate("""() => {
                document.getElementById('conf-view').classList.remove('hidden');
                document.getElementById('lista-view').classList.add('hidden');
            }""")
            page.evaluate('() => { void efetivarGravacaoFinal(); }')
            expect(page.locator('#receb-dialog')).to_be_visible()
            dialog_box = page.locator('#receb-dialog').bounding_box()
            assert dialog_box and abs((dialog_box['y'] + dialog_box['height'] / 2) - 422) < 3
            assert dialog_box['y'] >= 12 and dialog_box['y'] + dialog_box['height'] <= 832
            page.get_by_role('button', name='Endereçar agora', exact=True).click()
            assert page.url == await_url
            expect(page.locator('#conf-view')).to_be_hidden()
            expect(page.locator('#lista-view')).to_be_visible()
            expect(page.locator('#receb-notas-painel')).to_be_hidden()
            expect(page.get_by_role('button',name='Endereçar material',exact=True).first).to_be_visible()
            assert page.locator('.pa-table thead').evaluate('(e) => getComputedStyle(e).display') == 'none'
            assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
            assert page.evaluate('typeof Html5Qrcode') == 'function'
            page.locator('#putaway').screenshot(path=str(tmp_path/'enderecamento-mobile.png'))
            page.get_by_role('button',name='Endereçar material',exact=True).first.click()
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
            expect(page.locator('#pa-work-feedback')).to_contain_text('Próximo item carregado')
            expect(page.locator('#pa-sku-manual')).to_be_focused()
            expect(page.locator('#pa-submit')).to_be_disabled()
            page.locator('#pa-sku-manual').fill('ERRADO')
            page.locator('#pa-sku-manual').press('Enter')
            expect(page.locator('#pa-work-feedback')).to_contain_text('não corresponde')
            expect(page.locator('#pa-sku-manual')).to_have_value('')
            expect(page.locator('#pa-sku-manual')).to_be_focused()
            page.locator('#pa-sku-manual').fill('SKU-1')
            page.locator('#pa-sku-manual').press('Enter')
            expect(page.locator('#pa-address-manual')).to_be_focused()
            page.locator('#pa-address-manual').fill('A')
            page.locator('#pa-address-manual').press('Enter')
            expect(page.locator('#pa-submit')).to_be_enabled()
            expect(page.locator('#pa-address-manual')).to_have_value('')
            expect(page.locator('#pa-address-manual')).to_be_focused()
            expect(page.locator('.pa-progress')).to_have_attribute('aria-valuenow', '10')
            page.evaluate("document.documentElement.dataset.theme='escuro'")
            assert page.locator('#pa-sku-manual').evaluate('(e) => getComputedStyle(e).backgroundColor') != 'rgb(255, 255, 255)'
            page.screenshot(path=str(tmp_path/'enderecamento-wizard-dark.png'),full_page=True)
            page.locator('#pa-submit').click()
            expect(page.locator('#pa-feedback')).to_contain_text('sincronizado com o GRV')
            assert update.call_count == 2
            assert update.call_args_list[0].args == ('SKU-1', 'A;B')
            expect(page.locator('#pa-work')).not_to_be_visible()
            page.get_by_role('button',name='Mais ações').first.click()
            page.get_by_role('button',name='Ver histórico e origem').click()
            expect(page.locator('#pa-history-body')).to_contain_text('A;B')
            page.locator('#pa-history-close').click()
            page.locator('[data-filter="conferido"]').click()
            expect(page.locator('#receb-enderecamento-painel')).to_be_hidden()
            expect(page.locator('#receb-notas-painel')).to_be_visible()
            page.goto(f'http://127.0.0.1:{server.server_port}/conferencia?etapa=enderecamento&ids=1')
            expect(page.locator('#receb-enderecamento-painel')).to_be_visible()
            assert popups == []
            assert erros_js == []
            browser.close()
    finally:
        server.shutdown(); thread.join(timeout=5)
        with app.app_context():
            db.session.remove(); db.engine.dispose()
