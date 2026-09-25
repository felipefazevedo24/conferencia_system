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
    monkeypatch.setattr(routes, '_chapa_lotes_por_item', lambda itens, entradas=None: {})
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
    admin=client_for(app).get('/logistica/estoque/chapas').data
    assert b'id="ch-delete-dialog"' in admin and b'id="ch-und-dialog"' in admin
    assert b'const PODE_ADMIN = true' in admin
    result=client_for(app,'Logística').get('/logistica/estoque/chapas')
    assert result.status_code == 200
    assert b'id="ch-delete-dialog"' not in result.data and b'id="ch-und-dialog"' not in result.data
    assert b'id="ch-lote-dialog"' not in result.data
    assert b'const PODE_ADMIN = false' in result.data


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
            excluir=lambda item: page.locator(f'[data-acao="excluir"][data-item="{item}"]')
            page.locator('.ch-row__main').click()
            page.locator('[data-menu="1"]').click()
            excluir(1).click()
            pw.expect(page.locator('#ch-delete-dialog')).to_be_visible()
            pw.expect(page.locator('#ch-delete-item')).to_contain_text('AR Não informado')
            page.locator('#ch-delete-cancel').click()
            page.locator('[data-menu="1"]').click()
            excluir(1).click()
            page.locator('#ch-delete-confirm').click()
            pw.expect(excluir(1)).to_have_count(0)
            pw.expect(excluir(2)).to_have_count(1)
            page.locator('#ch-audit-open').click()
            pw.expect(page.locator('#ch-audit-list')).to_contain_text('Exclusão do controle')
            pw.expect(page.locator('#ch-audit-list')).to_contain_text('UND: 2')
            pw.expect(page.locator('#ch-audit-list')).to_contain_text('teste')
            page.locator('#ch-audit-close').click()
            page.reload();page.locator('.ch-row__main').click()
            pw.expect(excluir(1)).to_have_count(0)
            role[0]='Logística';page.reload();page.locator('.ch-row__main').click()
            pw.expect(page.locator('[data-acao="excluir"], [data-acao="und"]')).to_have_count(0)
            pw.expect(page.locator('[data-acao="calc"]')).to_have_count(1)
            assert errors == []
            browser.close()
    finally:
        server.shutdown();thread.join(timeout=5)


def test_admin_altera_und_com_motivo_e_reflete_na_conferencia(app):
    client=client_for(app)
    assert client.patch('/api/logistica/chapas/1/und',json={'und':7}).status_code==400
    assert client.patch('/api/logistica/chapas/1/und',json={'und':0,'motivo':'x'}).status_code==400
    assert client.patch('/api/logistica/chapas/1/und',json={'und':'abc','motivo':'x'}).status_code==400
    assert client.patch('/api/logistica/chapas/999/und',json={'und':7,'motivo':'x'}).status_code==404
    for _ in range(2):
        r=client.patch('/api/logistica/chapas/1/und',json={'und':'7','motivo':'Recontagem fisica'})
        assert r.status_code==200 and r.get_json()['und']==7
    with app.app_context():
        # Mesmo campo que a conferência de recebimento lê: não há cópia pra sincronizar.
        assert db.session.get(ItemNota,1).qtd_chapas_und==7
        log=ChapaAuditoria.query.one()
        assert log.acao=='Unidades alteradas no controle' and log.usuario=='teste'
        assert log.antes=={'und':2} and log.depois=={'und':7,'motivo':'Recontagem fisica'}
    item=[i for i in client.get('/api/logistica/chapas').get_json()['itens'] if i['item_id']==1][0]
    assert item['und']==7
    with app.app_context():
        db.session.get(ItemNota,1).qtd_chapas_und=None;db.session.commit()
    assert client.patch('/api/logistica/chapas/1/und',json={'und':7,'motivo':'x'}).status_code==409


@pytest.mark.parametrize('role', ['Logística', 'Fiscal', 'Conferente'])
def test_outros_papeis_nao_alteram_und(app, role):
    r=client_for(app, role).patch('/api/logistica/chapas/1/und', json={'und':9,'motivo':'x'})
    assert r.status_code == 403
    with app.app_context():
        assert db.session.get(ItemNota,1).qtd_chapas_und == 2 and ChapaAuditoria.query.count() == 0


def test_tela_calculo_und_e_aviso_no_navegador(app):
    from werkzeug.serving import make_server
    pw=pytest.importorskip('playwright.sync_api')
    @app.before_request
    def login():
        session['username']='teste';session['role']='Admin'
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
            page.locator('.ch-row__main').click()
            # Calculadora é <dialog> modal: fica na camada do topo, cobrindo a tela toda.
            page.locator('[data-acao="calc"][data-item="1"]').click()
            assert page.evaluate("document.getElementById('ch-calc-dialog').matches(':modal')")
            page.locator('#ch-dim-espessura').fill('1510')
            page.locator('#ch-dim-largura').fill('1000')
            page.locator('#ch-dim-comprimento').fill('2000')
            pw.expect(page.locator('#ch-calc-warn')).to_contain_text('Confira as medidas')
            page.locator('#ch-btn-salvar').click()
            pw.expect(page.locator('#ch-btn-salvar')).to_contain_text('Salvar mesmo assim')
            page.keyboard.press('Escape')
            pw.expect(page.locator('#ch-calc-dialog')).not_to_be_visible()
            with app.app_context():
                assert ChapaCalculo.query.one().peso_por_peca == 25
            page.locator('[data-menu="1"]').click()
            page.locator('[data-acao="und"][data-item="1"]').click()
            pw.expect(page.locator('#ch-und-dialog')).to_be_visible()
            page.locator('#ch-und-valor').fill('4')
            pw.expect(page.locator('#ch-und-preview')).to_contain_text('12,5 kg por chapa')
            page.locator('#ch-und-save').click()
            pw.expect(page.locator('#ch-und-error')).to_contain_text('motivo')
            page.locator('#ch-und-motivo').fill('Recontagem')
            page.locator('#ch-und-save').click()
            pw.expect(page.locator('#ch-und-dialog')).not_to_be_visible()
            pw.expect(page.locator('.ch-lote:has([data-item="1"])')).to_contain_text('4 und')
            with app.app_context():
                assert db.session.get(ItemNota,1).qtd_chapas_und == 4
            page.locator('[data-menu="1"]').click()
            page.locator('[data-acao="historico"][data-item="1"]').click()
            pw.expect(page.locator('#ch-audit-list')).to_contain_text('Motivo: Recontagem')
            pw.expect(page.locator('#ch-audit-list .ch-audit-entry')).to_have_count(1)
            assert errors == []
            browser.close()
    finally:
        server.shutdown();thread.join(timeout=5)


def test_lote_por_linha_do_grv_com_dados_reais_da_nf_71663(monkeypatch):
    # Retorno real do GRV (25/09/2026): um lote por linha, e a mesma entrada
    # repetida uma vez por linha da NF. Antes todas as linhas do 00549 ficavam com -5.
    from types import SimpleNamespace
    from conferencia_app.services import erp_lancamento_service
    grv = [('19-01-00549', 1.35, 1), ('19-01-00549', 1.51, 2), ('19-01-00549', 1.52, 3),
           ('19-01-00549', 1.35, 4), ('19-01-00549', 1.34, 5), ('19-01-00558', 1.79, 6),
           ('19-01-00558', 1.8, 7), ('19-01-00558', 2.02, 8)]
    entrada = {'numero_nota': '71663', 'itens': [
        {'cod_interno': c, 'descricao': 'CHAPA A36', 'quantidade': q, 'lote': f'71663-16/09/2026-{n}'} for c, q, n in grv]}
    monkeypatch.setattr(erp_lancamento_service, 'buscar_entradas_chapa_lote', lambda itens: [entrada] * 8)
    sync = [(5170, '19-01-00558', 1.79), (5171, '19-01-00558', 1.8), (5172, '19-01-00549', 1.35),
            (5173, '19-01-00549', 1.35), (5174, '19-01-00549', 1.34), (5175, '19-01-00549', 1.51),
            (5176, '19-01-00549', 1.52), (5177, '19-01-00558', 2.02)]
    itens = [SimpleNamespace(id=i, numero_nota='71663', codigo_grv=c, codigo=c, descricao='CHAPA A36', qtd_real=q)
             for i, c, q in reversed(sync)]
    lotes = {k: v.rsplit('-', 1)[1] for k, v in routes._chapa_lotes_por_item(itens).items()}
    assert lotes == {5170: '6', 5171: '7', 5172: '1', 5173: '4', 5174: '5', 5175: '2', 5176: '3', 5177: '8'}


def test_pareamento_com_pedido_nao_troca_codigo_de_linha_lancada(app):
    from conferencia_app.routes.api_routes import _sincronizar_codigo_interno_por_pedido
    with app.app_context():
        lancado, pendente = db.session.get(ItemNota, 1), db.session.get(ItemNota, 2)
        lancado.codigo_grv = '19-01-00549'
        pendente.status = 'Pendente'
        db.session.commit()
        resultado = {'pares': [{'item_id': 1, 'po_index': 0, 'po_codigo_material': '19-01-00564'},
                               {'item_id': 2, 'po_index': 1, 'po_codigo_material': '19-01-00564'}]}
        _sincronizar_codigo_interno_por_pedido('123', 'PED-1', resultado)
        assert db.session.get(ItemNota, 1).codigo_grv == '19-01-00549'
        assert db.session.get(ItemNota, 1).linha_po_vinculada == 0
        assert db.session.get(ItemNota, 2).codigo_grv == '19-01-00564'


def _grv_71663_e_21728():
    # Linhas reais do GRV (DBeaver, 25/09/2026). qtde é a unidade de COMPRA;
    # o lote -4 entrou com 1.345 kg (qtde_movimentada), não os 1.350 do XML.
    linhas_71663 = [(1, '19-01-00549', 1.35), (2, '19-01-00549', 1.51), (3, '19-01-00549', 1.52),
                    (4, '19-01-00549', 1.35), (5, '19-01-00549', 1.34), (6, '19-01-00558', 1.79),
                    (7, '19-01-00558', 1.8), (8, '19-01-00558', 2.02)]
    return [
        {'numero_nota': '71663', 'codigo_lancamento': '17089', 'itens': [{
            'numero_item': n, 'guid_linha': f'G71663-{n}', 'cod_interno': c, 'descricao': 'CHAPA A36',
            'codigo_na_fabrica': 'CFQ047518000005' if c.endswith('549') else 'CFQ063018000001',
            'quantidade': q, 'unidade': 'TO', 'qtde_estoque': 1345 if n == 4 else q * 1000,
            'unidade_estoque': 'KG', 'lote_qtde_movimentada': 1345 if n == 4 else q * 1000,
            'lote': f'71663-16/09/2026-{n}'} for n, c, q in linhas_71663]},
        {'numero_nota': '21728', 'codigo_lancamento': '17096', 'itens': [{
            'numero_item': 1, 'guid_linha': 'G21728-1', 'cod_interno': '19-01-00599',
            'descricao': 'CHAPA SAE FINA FRIA 1006 / 1008 1,90MM', 'codigo_na_fabrica': 'CFF019012000003',
            'quantidade': 5954, 'unidade': 'KG', 'qtde_estoque': 5954, 'unidade_estoque': 'KG',
            'lote_qtde_movimentada': 5954, 'lote': '21728-21/09/2026-1'}]},
    ]


def test_lote_do_grv_e_a_base_do_controle_com_dados_reais(app, monkeypatch):
    # Linhas do Sync como estão em produção: 5173 com codigo_grv errado pelo
    # pareamento com o pedido; 21728 com 5 linhas no código do fornecedor.
    sync = [(5170, '71663', '19-01-00558', '19-01-00558', 1.79, 'TO', 8), (5171, '71663', '19-01-00558', '19-01-00558', 1.8, 'TO', 9),
            (5172, '71663', '19-01-00549', '19-01-00549', 1.35, 'TO', 9), (5173, '71663', '19-01-00549', '19-01-00564', 1.35, 'TO', 8),
            (5174, '71663', '19-01-00549', '19-01-00549', 1.34, 'TO', 8), (5175, '71663', '19-01-00549', '19-01-00549', 1.51, 'TO', 8),
            (5176, '71663', '19-01-00549', '19-01-00549', 1.52, 'TO', 8), (5177, '71663', '19-01-00558', '19-01-00558', 2.02, 'TO', 9),
            (5194, '21728', '19-01-00599', '19-01-00599', 5954.0, 'KG', 18), (5195, '21728', 'CFF019012000003', None, 0.96, 'T', 18),
            (5196, '21728', 'CFF019012000003', None, 0.956, 'T', 18), (5197, '21728', 'CFF019012000003', None, 0.96, 'T', 18),
            (5198, '21728', 'CFF019012000003', None, 1.062, 'T', 20), (5199, '21728', 'CFF019012000003', None, 1.058, 'T', 20)]
    with app.app_context():
        for id_, nota, cod, grv, qtd, un, und in sync:
            db.session.add(ItemNota(id=id_, numero_nota=nota, codigo=cod, codigo_grv=grv, descricao='CHAPA',
                                    qtd_real=qtd, unidade_comercial=un, qtd_chapas_und=und, status='Lançado',
                                    usuario_conferencia='ROBDAC'))
        db.session.add(ChapaCalculo(item_nota_id=5194, numero_nota='21728', codigo='19-01-00599', peso_por_peca=53.2))
        db.session.commit()
    monkeypatch.setattr(routes, '_chapa_buscar_entradas', lambda itens: _grv_71663_e_21728() * 3)
    data = client_for(app).get('/api/logistica/chapas').get_json()
    assert data['lotes_do_grv'] is True
    por_lote = {l['lote']: l for l in data['itens']}

    lote4 = por_lote['71663-16/09/2026-4']
    assert lote4['kg_nf'] == 1345 and lote4['codigo'] == '19-01-00549' and lote4['itens_ids'] == [5173]
    ligacoes = {l['lote'].rsplit('-', 1)[1]: l['itens_ids'] for l in data['itens'] if l['numero_nota'] == '71663'}
    assert ligacoes == {'1': [5172], '2': [5175], '3': [5176], '4': [5173], '5': [5174],
                        '6': [5170], '7': [5171], '8': [5177]}
    assert not any(l['codigo'] == '19-01-00564' for l in data['itens'])

    nf21728 = [l for l in data['itens'] if l['numero_nota'] == '21728']
    assert len(nf21728) == 1
    lote = nf21728[0]
    assert lote['codigo'] == '19-01-00599' and lote['kg_nf'] == 5954 and lote['und'] == 112
    assert sorted(lote['itens_ids']) == [5194, 5195, 5196, 5197, 5198, 5199]
    assert lote['item_id'] == 5194 and lote['und_outros'] == 94 and lote['peso_por_peca'] == 53.2

    # Itens da fixture (NF 123) não existem no GRV: continuam aparecendo, pelo XML.
    orfaos = [l for l in data['itens'] if l['numero_nota'] == '123']
    assert {l['item_id'] for l in orfaos} == {1, 2} and all(l['fonte'] == 'xml' for l in orfaos)


def test_bridge_sem_campos_de_lote_usa_montagem_antiga(app, monkeypatch):
    antiga = _grv_71663_e_21728()
    for e in antiga:
        for it in e['itens']:
            for campo in ('qtde_estoque', 'unidade_estoque', 'codigo_na_fabrica', 'lote_qtde_movimentada', 'guid_linha'):
                it.pop(campo)
    monkeypatch.setattr(routes, '_chapa_buscar_entradas', lambda itens: antiga)
    data = client_for(app).get('/api/logistica/chapas').get_json()
    assert data['lotes_do_grv'] is False
    assert sorted(l['item_id'] for l in data['itens']) == [1, 2]
    assert all(l['itens_ids'] == [l['item_id']] for l in data['itens'])


def test_admin_corrige_lote_e_consumo_segue_o_lote_corrigido(app, monkeypatch):
    monkeypatch.setattr(routes, '_chapa_buscar_entradas', lambda itens: _grv_71663_e_21728())
    monkeypatch.setattr(erp_estoque_service, 'buscar_saldo_chapa_por_lote', lambda codigos: {'por_lote': [
        {'codigo': '19-01-00599', 'lote': '21728-LOTE-FISICO', 'kg_saida': 954, 'kg_reservado': 100}]})
    with app.app_context():
        db.session.add(ItemNota(id=5194, numero_nota='21728', codigo='19-01-00599', codigo_grv='19-01-00599',
                                descricao='CHAPA', qtd_real=5954, unidade_comercial='KG', qtd_chapas_und=112, status='Lançado'))
        db.session.commit()
    client = client_for(app)
    lote = lambda: [l for l in client.get('/api/logistica/chapas').get_json()['itens'] if l['numero_nota'] == '21728'][0]
    assert lote()['lote'] == '21728-21/09/2026-1' and lote()['kg_saida'] == 0
    corpo = {'item_id': 5194, 'numero_nota': '21728', 'codigo': '19-01-00599',
             'lote_original': '21728-21/09/2026-1', 'lote': '21728-LOTE-FISICO'}
    assert client.put('/api/logistica/chapas/lote', json=corpo).status_code == 400  # sem motivo
    assert client.put('/api/logistica/chapas/lote', json={**corpo, 'item_id': 1, 'motivo': 'x'}).status_code == 404
    for _ in range(2):
        assert client.put('/api/logistica/chapas/lote', json={**corpo, 'motivo': 'Etiqueta física'}).status_code == 200
    atual = lote()
    assert atual['lote'] == '21728-LOTE-FISICO' and atual['lote_original'] == '21728-21/09/2026-1'
    assert atual['kg_saida'] == 954 and atual['kg_reservado'] == 100 and atual['kg_saldo'] == 5000
    assert client.get('/api/logistica/chapas?q=LOTE-FISICO').get_json()['itens'][0]['item_id'] == 5194
    with app.app_context():
        log = ChapaAuditoria.query.one()
        assert log.acao == 'Lote corrigido' and log.antes == {'lote': '21728-21/09/2026-1'}
        assert log.depois == {'lote': '21728-LOTE-FISICO', 'lote_grv': '21728-21/09/2026-1', 'motivo': 'Etiqueta física'}
    # Voltar ao lote do GRV desfaz a correção.
    assert client.put('/api/logistica/chapas/lote', json={**corpo, 'lote': '21728-21/09/2026-1', 'motivo': 'Desfaz'}).status_code == 200
    assert lote()['lote'] == '21728-21/09/2026-1'
    with app.app_context():
        from conferencia_app.models import ChapaLoteCorrecao
        assert ChapaLoteCorrecao.query.count() == 0 and ChapaAuditoria.query.count() == 2


@pytest.mark.parametrize('role', ['Logística', 'Fiscal', 'Conferente'])
def test_outros_papeis_nao_corrigem_lote(app, role):
    r = client_for(app, role).put('/api/logistica/chapas/lote', json={
        'item_id': 1, 'numero_nota': '123', 'codigo': 'CH-1', 'lote_original': 'A', 'lote': 'B', 'motivo': 'x'})
    assert r.status_code == 403


def test_tolerancia_de_divergencia_e_5_por_cento(app):
    with app.app_context():
        # Item 1: 2 und x 25 kg/peça = 50 kg, igual à NF. Com 26,5 kg/peça dá +6%; com 26 dá +4%.
        calc = ChapaCalculo.query.one()
        calc.peso_por_peca = 26; db.session.commit()
    client = client_for(app)
    assert client.get('/api/logistica/chapas').get_json()['resumo']['com_divergencia'] == 0
    with app.app_context():
        ChapaCalculo.query.one().peso_por_peca = 26.5; db.session.commit()
    assert client.get('/api/logistica/chapas').get_json()['resumo']['com_divergencia'] == 1
    app.jinja_env.globals.update(can_access=lambda key: True)
    html = client.get('/logistica/estoque/chapas').data.decode()
    assert 'Divergência &gt; 5%' in html and 'const TOLERANCIA_PCT = 5.0' in html
