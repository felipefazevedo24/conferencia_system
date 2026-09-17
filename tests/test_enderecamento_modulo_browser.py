"""Navegador real, backend real e GRV simulado; sem movimentar o estoque de produção."""
from pathlib import Path
from threading import Thread
from unittest.mock import Mock

import pytest
from flask import Flask, session
from werkzeug.serving import make_server

pw = pytest.importorskip('playwright.sync_api')
from conferencia_app.extensions import db
from conferencia_app.models import EnderecoMovimento, EnderecoSaldo
from conferencia_app.routes.recebimento_enderecamento_routes import recebimento_enderecamento_bp
from conferencia_app.services import erp_estoque_service
from conferencia_app.services import recebimento_enderecamento_service as receb


def test_modulo_desktop_mobile_movimentacoes_e_retry(tmp_path, monkeypatch):
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
    # O painel lê o estoque do GRV; aqui ele espelha o que a integração gravou,
    # no formato real da bridge: um produto, os endereços num campo só.
    monkeypatch.setattr(erp_estoque_service, 'buscar_estoque_grv', lambda **kw: {'por_codigo': {
        'SKU-1': {'item': 'Material de teste', 'unidade': 'UN', 'qtde_total': 10,
                  'localizacoes': [erp['local']]}}})
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
            pw.expect(page.locator('#end-balances')).to_contain_text('Material de teste')
            # Mover com origem preenchida SUBSTITUI o endereço, não acumula.
            page.locator('#end-balances').get_by_role('button',name='Movimentar',exact=True).click()
            pw.expect(page.locator('#end-origin')).to_have_value('A')
            page.locator('#end-destination').fill('B')
            page.locator('#end-quantity').fill('10'); page.locator('#end-unit').fill('UN')
            page.locator('#end-submit').click()  # sem motivo: campo é opcional
            pw.expect(page.locator('#end-work')).not_to_be_visible()
            assert erp['local']=='B'
            pw.expect(page.locator('#end-balances')).to_contain_text('B')
            # Sem origem, o material passa a constar nos dois endereços.
            page.locator('#end-move').click()
            page.locator('#end-sku').fill('SKU-1'); page.locator('#end-sku').press('Enter')
            pw.expect(page.locator('#end-origin')).to_be_focused()
            # A prévia diz onde está hoje e como fica depois, antes de confirmar.
            pw.expect(page.locator('#end-current')).to_contain_text('Hoje em: B')
            page.locator('#end-destination').fill('C'); page.locator('#end-quantity').fill('4')
            pw.expect(page.locator('#end-preview')).to_contain_text('Depois: B · C')
            page.locator('#end-unit').fill('UN')
            page.locator('#end-reason').fill('Sobra guardada em outro vão')
            page.locator('#end-submit').click()
            pw.expect(page.locator('#end-work')).not_to_be_visible()
            assert erp['local']=='B;C'
            # Aba de endereços: catálogo com ocupação e desativação.
            page.locator('[data-panel="locais"]').click()
            pw.expect(page.locator('#end-places')).to_contain_text('B')
            linha=page.locator('#end-places tr').filter(has_text='C').first
            pw.expect(linha).to_contain_text('1 material(is)')
            # Desativar escreve em vários materiais no GRV: tem que confirmar antes.
            avisos=[]
            page.on('dialog', lambda d: (avisos.append(d.message), d.accept()))
            linha.get_by_role('button',name='Desativar',exact=True).click()
            pw.expect(page.locator('#end-places')).to_contain_text('Desativado')
            assert any('tirar este endereço de 1 material' in a for a in avisos), avisos
            # C saiu do material, que continua em B.
            assert erp['local']=='B'
            pw.expect(page.locator('#end-feedback')).to_contain_text('deixaram de apontar')
            page.locator('[data-panel="saldos"]').click()
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
            page.locator('#end-balances tr').first.get_by_role('button',name='Movimentar',exact=True).click()
            page.locator('#end-destination').fill('D');page.locator('#end-quantity').fill('6')
            page.locator('#end-unit').fill('UN')
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
            assert erp['local']=='D'
            with app.app_context():
                # 2 movimentações + a limpeza da desativação + a última movimentação
                assert EnderecoMovimento.query.count()==4
                assert EnderecoMovimento.query.filter_by(tipo='Endereço desativado').count()==1
                assert EnderecoSaldo.query.count()==0  # o Sync não controla saldo
            page.locator('[data-panel="receber"]').click()
            pw.expect(page.locator('#pa-list')).to_contain_text('Nenhum material')
            assert errors==[]
            browser.close()
    finally:
        server.shutdown();thread.join(timeout=5)
        with app.app_context():
            db.session.remove();db.engine.dispose()
