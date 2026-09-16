"""Depuração temporária do kebab no fluxo mobile (apagar depois)."""
from pathlib import Path
from threading import Thread
from unittest.mock import Mock
import tempfile

from flask import Flask, session, render_template
from werkzeug.serving import make_server
from playwright.sync_api import sync_playwright, expect

from conferencia_app.extensions import db
from conferencia_app.models import ItemNota, LocalizacaoArmazem, RecebimentoEnderecamento
from conferencia_app.routes.recebimento_enderecamento_routes import recebimento_enderecamento_bp
from conferencia_app.services import recebimento_enderecamento_service as svc

tmp = tempfile.mkdtemp()
root = Path(__file__).resolve().parent
app = Flask(__name__, template_folder=str(root / 'templates'), static_folder=str(root / 'static'))
app.config.update(TESTING=True, SECRET_KEY='x', SQLALCHEMY_DATABASE_URI=f'sqlite:///{tmp}/m.db')
db.init_app(app)
app.register_blueprint(recebimento_enderecamento_bp)


@app.get('/conferencia')
def conferencia():
    return render_template('conferente.html', user='tester')


@app.before_request
def login():
    session['username'] = 'tester'
    session['role'] = 'Admin'


@app.context_processor
def ctx():
    return {'can_access': lambda p: True, 'user': 'tester'}


with app.app_context():
    db.create_all()
    item = ItemNota(numero_nota='321', codigo_grv='SKU-1', descricao='Peca', status='Concluído', qtd_real=10)
    db.session.add(item)
    db.session.flush()
    db.session.add(RecebimentoEnderecamento(item_nota_id=item.id, sku='SKU-1', quantidade=10, criado_por='t'))
    for local in ('A', 'B'):
        db.session.add(LocalizacaoArmazem(codigo=local, corredor='', prateleira='', posicao=''))
    db.session.commit()

svc.buscar_localizacao_produto_grv = Mock(return_value='A')
svc.atualizar_localizacao_estoque = Mock(return_value={})

server = make_server('127.0.0.1', 0, app)
Thread(target=server.serve_forever, daemon=True).start()

MOCK_CAMERA = """() => {
    window.Html5Qrcode = class {
        async start(c, o, s) { this.isScanning = true; setTimeout(() => s(window.testCode), 20); }
        async stop() { this.isScanning = false; }
        clear() {}
    };
    window.testCode = 'SKU-1';
}"""

with sync_playwright() as pw:
    b = pw.chromium.launch(headless=True)
    page = b.new_page(viewport={'width': 390, 'height': 844}, is_mobile=True, has_touch=True)
    erros = []
    page.on('console', lambda m: erros.append(m.text) if m.type == 'error' else None)
    page.on('pageerror', lambda e: erros.append(str(e)))
    page.goto(f'http://127.0.0.1:{server.server_port}/conferencia')
    page.locator('[data-filter="enderecamento"]').click()
    page.get_by_role('button', name='Endereçar material', exact=True).click()
    page.evaluate(MOCK_CAMERA)
    page.locator('#pa-sku').click()
    page.locator('#pa-reason').select_option('superlotado')
    page.evaluate("window.testCode='B'")
    page.locator('#pa-address').click()
    expect(page.locator('#pa-submit')).to_be_enabled()
    page.locator('#pa-submit').click()
    expect(page.locator('#pa-feedback')).to_contain_text('sincronizado')
    print('KEBABS NO DOM:', page.locator('.pa-menu-btn').count())
    page.get_by_role('button', name='Mais ações').click()
    page.wait_for_timeout(800)
    print('MENUS NO DOM:', page.locator('.pa-menu-list').count())
    print('ITENS:', page.locator('.pa-menu-item').all_text_contents())
    print('ERROS JS:', erros)
    page.screenshot(path=str(root / 'tmp_kebab.png'))
    b.close()
server.shutdown()
