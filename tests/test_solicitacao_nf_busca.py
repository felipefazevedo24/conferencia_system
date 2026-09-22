from pathlib import Path
from threading import Thread
from unittest.mock import Mock

import pytest
from flask import Flask

from conferencia_app.routes.solicitacao_nf_routes import solicitacao_nf_bp
from conferencia_app.services import solicitacao_nf_service as svc


@pytest.fixture
def app(monkeypatch):
    app = Flask(__name__, template_folder=str(Path('templates').resolve()))
    app.config.update(TESTING=True, SECRET_KEY='test')
    app.register_blueprint(solicitacao_nf_bp)
    monkeypatch.setattr(svc, 'listar_tipos_operacao', lambda: [
        {'nome': 'Garantia', 'descricao_ajuda': '', 'requer_retorno_padrao': False}])
    monkeypatch.setattr(svc, 'listar_funcionarios_para_solicitacao', lambda: [])
    return app


@pytest.mark.parametrize('bridge', [False, True])
@pytest.mark.parametrize('recurso', ['materiais', 'clientes'])
def test_indisponibilidade_nao_vira_lista_vazia(app, monkeypatch, bridge, recurso):
    monkeypatch.setattr(svc, '_resolver_config', lambda: {
        'api_url': 'https://bridge.invalid' if bridge else '',
        'host': '', 'database': '', 'user': ''})
    monkeypatch.setattr(svc, '_executar_bridge', Mock(side_effect=RuntimeError('segredo interno')))
    response = app.test_client().get(f'/api/solicitacao-nf/{recurso}?q=M01')
    assert response.status_code == 503
    assert response.json['sucesso'] is False
    assert 'segredo' not in response.get_data(as_text=True)


def test_bridge_busca_e_consulta_exata_usam_catalogo(app, monkeypatch):
    monkeypatch.setattr(svc, '_resolver_config', lambda: {
        'api_url': 'https://bridge.invalid', 'api_token': 'teste',
        'host': '', 'database': '', 'user': ''})
    material = {'codigo_interno': '000123', 'nome': 'BOMBA'}
    response = Mock()
    response.json.return_value = {'sucesso': True, 'rows': [material]}
    post = Mock(return_value=response)
    monkeypatch.setattr(svc.requests, 'post', post)
    with app.app_context():
        assert svc.buscar_materiais('000123') == [material]
        assert post.call_args.kwargs['json']['query'] == 'SQL_MATERIAL_BUSCAR'
        assert svc._buscar_material_por_codigo('000123') == material
        assert post.call_args.kwargs['json']['query'] == 'SQL_MATERIAL_POR_CODIGO'
        assert post.call_args.kwargs['json']['params']['codigo'] == '000123'


def test_busca_por_codigo_preserva_codigo_e_parametros(app, monkeypatch):
    query = Mock(return_value=[{'codigo_interno': '000123', 'nome': 'BOMBA'}])
    monkeypatch.setattr(svc, '_executar', query)
    response = app.test_client().get('/api/solicitacao-nf/materiais?q=%20000123%20')
    assert response.json['materiais'][0]['codigo_interno'] == '000123'
    assert query.call_args.args[1]['termo'] == '%000123%'
    query.return_value = []
    assert app.test_client().get('/api/solicitacao-nf/materiais?q=inexistente').json == {
        'sucesso': True, 'materiais': []}


def test_busca_selecao_erro_e_resposta_atrasada_no_navegador(app):
    from playwright.sync_api import sync_playwright, expect
    from werkzeug.serving import make_server
    server = make_server('127.0.0.1', 0, app)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.route('https://**/*', lambda route: route.abort())
            page.goto(f'http://127.0.0.1:{server.server_port}/solicitacao-nf')
            page.evaluate('''() => {
              const original = window.fetch;
              window.fetch = (url, options) => {
                if (!url.includes('/materiais?')) return original(url, options);
                return new Promise(resolve => {
                  const q = new URL(url, location.origin).searchParams.get('q');
                  const body = q === 'erro' ? {sucesso:false, erro:'ERP indisponível'} :
                    {sucesso:true, materiais:[{codigo_interno:q,nome:'BOMBA',estoque_disponivel_uso:5}]};
                  if(q === 'antigo') window.resolverAntigo = () => resolve(new Response(JSON.stringify(body)));
                  else resolve(new Response(JSON.stringify(body), {status:q === 'erro' ? 503 : 200}));
                });
              };
            }''')
            field = page.locator('#buscaMaterial')
            results = page.locator('#sugestoesMaterial')
            field.fill('antigo')
            page.wait_for_function('window.resolverAntigo !== undefined')
            field.fill('000123')
            expect(results).to_contain_text('000123')
            page.evaluate('window.resolverAntigo()')
            expect(results).not_to_contain_text('antigo')
            results.locator('.item').click()
            expect(field).to_have_value('000123 - BOMBA')
            expect(page.locator('#helpMaterial')).to_contain_text('5')
            field.fill('erro')
            expect(results).to_contain_text('ERP indisponível')
            page.evaluate('window.adicionarItem()')
            expect(page.locator('#feedback')).to_contain_text('Selecione um material')
            browser.close()
    finally:
        server.shutdown()
        thread.join(timeout=5)
