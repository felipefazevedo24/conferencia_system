from pathlib import Path
from threading import Thread

import pytest
from flask import Flask, session
from sqlalchemy import text

from conferencia_app.extensions import db
from conferencia_app.models import ItemNota, ChapaCalculo, ChapaControleExclusao, ChapaAuditoria
from conferencia_app.routes import logistica_inventario_routes as routes
from conferencia_app.services import erp_estoque_service


@pytest.fixture
def app(tmp_path, monkeypatch):
    root = Path(__file__).resolve().parents[1]
    app = Flask(__name__, template_folder=str(root/'templates'), static_folder=str(root/'static'))
    app.config.update(TESTING=True, SECRET_KEY='test', SQLALCHEMY_DATABASE_URI=f'sqlite:///{tmp_path/"chapas.db"}')
    db.init_app(app)
    app.register_blueprint(routes.logistica_inventario_bp)
    monkeypatch.setattr(routes, '_chapa_lotes_por_item', lambda itens: {})
    monkeypatch.setattr(erp_estoque_service, 'buscar_saldo_chapa_por_lote', lambda codigos: {})
    with app.app_context():
        db.create_all()
        db.session.execute(text('PRAGMA foreign_keys = ON'))
        db.session.add_all([ItemNota(numero_nota='123', codigo='CH-1', descricao='Chapa sem AR',
            qtd_chapas_und=2, qtd_real=50, status='Lançado', unidade_comercial='KG'),
            ItemNota(numero_nota='123', codigo='CH-1', descricao='Outra chapa',
            qtd_chapas_und=3, qtd_real=75, status='Lançado', unidade_comercial='KG', numero_lancamento='AR-2')])
        db.session.flush()
        db.session.add(ChapaCalculo(item_nota_id=1, numero_nota='123', codigo='CH-1',
                                    peso_por_peca=25, criado_por='conferente'))
        db.session.commit()
    yield app
    with app.app_context():
        db.session.remove(); db.engine.dispose()


def client_for(app, role='Admin'):
    client = app.test_client()
    with client.session_transaction() as sess:
        sess['username']='teste'; sess['role']=role
    return client


def test_admin_exclui_so_item_selecionado_preservando_nf_e_calculo(app):
    client = client_for(app)
    assert len(client.get('/api/logistica/chapas').get_json()['itens']) == 2
    for _ in range(2):
        r = client.delete('/api/logistica/chapas/1', json={'confirmacao_item_id':1})
        assert r.status_code == 200 and r.get_json()['sucesso']
    for query in ('', '?q=CH-1', '?q=123'):
        data = client.get('/api/logistica/chapas'+query).get_json()
        assert [i['item_id'] for i in data['itens']] == [2]
        assert data['resumo']['chapas'] == 1
        assert data['resumo']['total_kg'] == 75
    with app.app_context():
        item = db.session.get(ItemNota, 1)
        assert item.status == 'Lançado' and item.qtd_chapas_und == 2 and item.qtd_real == 50
        assert ChapaCalculo.query.one().peso_por_peca == 25
        assert ChapaControleExclusao.query.one().usuario == 'teste'
        log=ChapaAuditoria.query.filter_by(acao='Exclusão do controle').one()
        assert log.antes['und']==2 and log.depois=={'no_controle':False}
        assert log.usuario=='teste' and log.numero_nota=='123'
        item.status='Pendente';db.session.commit()
        item.status='Lançado';db.session.commit()
    assert [i['item_id'] for i in client.get('/api/logistica/chapas').get_json()['itens']] == [2]


@pytest.mark.parametrize('role', ['Logística', 'Fiscal', 'Conferente'])
def test_outros_papeis_nao_excluem_mesmo_chamando_api_diretamente(app, role):
    r=client_for(app, role).delete('/api/logistica/chapas/1', json={'confirmacao_item_id':1})
    assert r.status_code == 403
    with app.app_context():
        assert ChapaControleExclusao.query.count() == 0


def test_exige_login_confirmacao_e_item_existente(app):
    assert app.test_client().delete('/api/logistica/chapas/1').status_code in (302,401)
    client = client_for(app)
    for body in (None, [], {}, {'confirmacao_item_id':2}):
        assert client.delete('/api/logistica/chapas/1', json=body).status_code == 400
    assert client.delete('/api/logistica/chapas/999', json={'confirmacao_item_id':999}).status_code == 404
    with app.app_context():
        db.session.get(ItemNota,1).qtd_chapas_und=None;db.session.commit()
    assert client.delete('/api/logistica/chapas/1', json={'confirmacao_item_id':1}).status_code == 409


def test_botao_somente_admin(app):
    with app.app_context():
        app.jinja_env.globals.update(can_access=lambda key:True)
    assert b'data-excluir-item' in client_for(app).get('/logistica/estoque/chapas').data
    result=client_for(app,'Logística').get('/logistica/estoque/chapas')
    assert result.status_code == 200 and b'data-excluir-item' not in result.data


def test_migration_idempotente_e_cascade(app):
    import importlib.util
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    spec=importlib.util.spec_from_file_location('exclusao',Path('migrations/versions/20260918_chapa_controle_exclusao.py'))
    migration=importlib.util.module_from_spec(spec);spec.loader.exec_module(migration)
    with app.app_context():
        with db.engine.begin() as conn, Operations.context(MigrationContext.configure(conn)):
            migration.upgrade(); migration.downgrade(); migration.upgrade(); migration.upgrade()
        db.session.execute(text('PRAGMA foreign_keys = ON'))
        db.session.add(ChapaControleExclusao(item_nota_id=1, usuario='teste'));db.session.commit()
        ItemNota.query.filter_by(id=1).delete();db.session.commit()
        assert ChapaControleExclusao.query.count() == 0


def test_exclusao_do_controle_nao_bloqueia_excluir_nf_pendente(app):
    from conferencia_app.routes.api_routes import api_bp
    app.register_blueprint(api_bp)
    client = client_for(app)
    assert client.delete('/api/logistica/chapas/1', json={'confirmacao_item_id':1}).status_code == 200
    with app.app_context():
        db.session.get(ItemNota,1).status='Pendente'
        db.session.commit()
    result=client.post('/api/excluir_nota_pendente',json={
        'nota':'123', 'documento_ref':'item:1', 'confirmacao_nota':'123', 'motivo':'Nota importada por engano'})
    assert result.status_code == 200
    with app.app_context():
        assert db.session.get(ItemNota,1) is None
        assert db.session.get(ItemNota,2) is not None
        assert ChapaControleExclusao.query.count()==0
        assert ChapaAuditoria.query.filter_by(item_nota_id=1, acao='Exclusão do controle').count()==1
    logs=client.get('/api/logistica/chapas/auditoria?q=Exclusão').get_json()['logs']
    assert len(logs)==1 and logs[0]['codigo']=='CH-1' and logs[0]['nf']=='123'


def test_auditoria_unidades_grava_antes_depois_e_rollback(app):
    from conferencia_app.services.chapa_auditoria_service import alterar_unidades
    with app.app_context():
        item=db.session.get(ItemNota,1)
        alterar_unidades(item,5,'conferente');db.session.commit()
        alterar_unidades(item,5,'conferente');db.session.commit()
        log=ChapaAuditoria.query.one()
        assert log.acao=='Unidades alteradas' and log.antes=={'und':2} and log.depois=={'und':5}
        alterar_unidades(item,8,'outro');db.session.rollback()
        assert ChapaAuditoria.query.count()==1 and item.qtd_chapas_und==5


def test_calculo_editado_auditado_sem_duplicar_dados_iguais(app):
    client=client_for(app)
    body={'item_id':1,'material':'aco_carbono','formato':'chapa',
          'dimensoes':{'espessura':10,'largura':1000,'comprimento':2000},'peso_por_peca':157}
    for _ in range(2):
        assert client.post('/api/logistica/chapas/calculo',json=body).status_code==200
    result=client.get('/api/logistica/chapas/auditoria?item_id=1').get_json()
    assert result['total']==1
    log=result['logs'][0]
    assert log['acao']=='Cálculo alterado' and log['usuario']=='teste'
    assert log['antes']['peso_por_peca']==25 and log['depois']['peso_por_peca']==157
    assert client.get('/api/logistica/chapas/auditoria?q=inexistente').get_json()['total']==0
    assert app.test_client().get('/api/logistica/chapas/auditoria').status_code in (302,401)


def test_validar_registra_alteracao_de_und_mesmo_com_calculo_igual(app):
    from conferencia_app.routes.api_routes import api_bp
    app.register_blueprint(api_bp)
    with app.app_context():
        db.session.get(ItemNota,1).status='Pendente';db.session.commit()
    client=client_for(app)
    dados={'nota':'123','documento_ref':'item:1','contagens':{'1':'50'},
        'chapas_itens':{'1':{'quantidade':4,'material':'aco_carbono','formato':'chapa',
            'dimensoes':{'espessura':10,'largura':1000,'comprimento':2000}}}}
    assert client.post('/validar',json=dados).status_code==200
    assert client.post('/validar',json=dados).status_code==200
    dados['chapas_itens']['1']['quantidade']=6
    assert client.post('/validar',json=dados).status_code==200
    with app.app_context():
        logs=ChapaAuditoria.query.filter_by(acao='Unidades alteradas').order_by(ChapaAuditoria.id).all()
        assert [(l.antes['und'],l.depois['und']) for l in logs]==[(2,4),(4,6)]
        assert ChapaAuditoria.query.filter_by(acao='Cálculo alterado').count()==1


def test_migration_auditoria_preserva_historico_sem_fk(app):
    import importlib.util
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    spec=importlib.util.spec_from_file_location('audit',Path('migrations/versions/20260918_chapa_auditoria.py'))
    migration=importlib.util.module_from_spec(spec);spec.loader.exec_module(migration)
    with app.app_context():
        with db.engine.begin() as conn, Operations.context(MigrationContext.configure(conn)):
            migration.upgrade();migration.downgrade();migration.upgrade();migration.upgrade()
        db.session.add(ChapaAuditoria(item_nota_id=999, usuario='admin', acao='Exclusão do controle',
            antes={'und':2},depois={'no_controle':False}))
        db.session.commit()
        assert ChapaAuditoria.query.one().antes=={'und':2}


def test_exclusao_no_navegador_com_cancelamento_e_recarregamento(app):
    from werkzeug.serving import make_server
    pw=pytest.importorskip('playwright.sync_api')
    role=['Admin']
    @app.before_request
    def login():
        session['username']='teste';session['role']=role[0]
    @app.context_processor
    def context():
        return dict(can_access=lambda key:True, user='teste', asset_version='test')
    server=make_server('127.0.0.1',0,app)
    thread=Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        with pw.sync_playwright() as p:
            if not Path(p.chromium.executable_path).exists():
                pytest.skip('Chromium não instalado')
            browser=p.chromium.launch(headless=True)
            page=browser.new_page(viewport={'width':1366,'height':900})
            errors=[];page.on('pageerror', lambda error:errors.append(str(error)))
            page.goto(f'http://127.0.0.1:{server.server_port}/logistica/estoque/chapas')
            page.locator('.ch-group__head').click()
            page.locator('[data-excluir-item="1"]').click()
            pw.expect(page.locator('#ch-delete-dialog')).to_be_visible()
            pw.expect(page.locator('#ch-delete-item')).to_contain_text('AR Não informado')
            page.locator('#ch-delete-cancel').click()
            pw.expect(page.locator('[data-excluir-item="1"]')).to_be_visible()
            page.locator('[data-excluir-item="1"]').click()
            page.locator('#ch-delete-confirm').click()
            pw.expect(page.locator('[data-excluir-item="1"]')).to_have_count(0)
            pw.expect(page.locator('[data-excluir-item="2"]')).to_be_visible()
            page.locator('#ch-audit-open').click()
            pw.expect(page.locator('#ch-audit-list')).to_contain_text('Exclusão do controle')
            pw.expect(page.locator('#ch-audit-list')).to_contain_text('Quantidade (UND): 2')
            pw.expect(page.locator('#ch-audit-list')).to_contain_text('teste')
            page.locator('#ch-audit-close').click()
            page.reload();page.locator('.ch-group__head').click()
            pw.expect(page.locator('[data-excluir-item="1"]')).to_have_count(0)
            role[0]='Logística';page.reload();page.locator('.ch-group__head').click()
            pw.expect(page.locator('[data-excluir-item]')).to_have_count(0)
            assert errors == []
            browser.close()
    finally:
        server.shutdown();thread.join(timeout=5)
