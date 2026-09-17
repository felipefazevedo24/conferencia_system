from datetime import datetime, timedelta
from unittest.mock import Mock

import pytest
from flask import Flask, session

from conferencia_app.extensions import db
from conferencia_app.models import (ItemNota, LocalizacaoArmazem,
    RecebimentoEnderecamento as Tarefa, RecebimentoEnderecamentoEvento as Evento,
    RecebimentoEnderecamentoTrava as Trava)
from conferencia_app.services import recebimento_enderecamento_service as svc
from conferencia_app.routes.recebimento_enderecamento_routes import recebimento_enderecamento_bp


@pytest.fixture
def app():
    app = Flask(__name__, template_folder='../templates', static_folder='../static')
    app.config.update(TESTING=True, SECRET_KEY='test-only', SQLALCHEMY_DATABASE_URI='sqlite://')
    db.init_app(app)
    app.register_blueprint(recebimento_enderecamento_bp)
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def state(app, monkeypatch):
    item = ItemNota(numero_nota='123', descricao='Material', codigo_grv='SKU-1',
                    qtd_real=10, unidade_comercial='UN', status='Concluído')
    db.session.add(item)
    for code in ('A', 'B', 'C'):
        db.session.add(LocalizacaoArmazem(codigo=code, corredor='', prateleira='', posicao=''))
    db.session.flush()
    task = Tarefa(item_nota_id=item.id, sku='SKU-1', quantidade=10, criado_por='conferente')
    db.session.add(task)
    db.session.commit()
    lookup, update = Mock(return_value='A'), Mock(return_value={})
    monkeypatch.setattr(svc, 'buscar_localizacao_produto_grv', lookup)
    monkeypatch.setattr(svc, 'atualizar_localizacao_estoque', update)
    with app.test_request_context():
        session['username'] = 'conferente'
        yield task, lookup, update


def payload(task, address='A', reason='normal', quantity=10):
    return {'sku_token': svc.registrar_leitura(task, 'sku', task.sku)['token'],
            'motivo': reason, 'alocacoes': [{'token': svc.registrar_leitura(task, 'local', address)['token'],
                                            'quantidade': quantity}]}


def test_existing_address_confirmation_and_duplicate(state):
    task, lookup, update = state
    data = payload(task)
    svc.confirmar(task, data)
    assert task.status == 'Aguardando sincronização'
    svc.sincronizar(task)
    svc.confirmar(task, data)
    svc.sincronizar(task)
    assert task.status == 'Concluído'
    update.assert_called_once_with('SKU-1', 'A')
    assert Evento.query.filter_by(tipo='Confirmado').count() == 1


def test_superlotacao_preserves_all_existing_addresses_and_splits_quantity(state):
    task, lookup, update = state
    lookup.return_value = 'A;C;A'
    data = payload(task, 'B', 'superlotado', 7)
    data['alocacoes'].append({'token':svc.registrar_leitura(task,'local','A')['token'], 'quantidade':3, 'lote':'L1'})
    svc.confirmar(task, data)
    svc.sincronizar(task)
    update.assert_called_once_with('SKU-1', 'A;C;B')
    assert task.alocacoes[1]['lote'] == 'L1'


def test_without_existing_address(state):
    task, lookup, update = state
    lookup.return_value = ''
    svc.confirmar(task, payload(task, 'B'))
    svc.sincronizar(task)
    update.assert_called_once_with('SKU-1', 'B')


@pytest.mark.parametrize('kind,code', [('sku','OTHER'),('local','A;B'),('invalid','A')])
def test_invalid_scan(state, kind, code):
    with pytest.raises(ValueError):
        svc.registrar_leitura(state[0], kind, code)
    state[2].assert_not_called()


def test_local_sem_cadastro_e_registrado_no_primeiro_uso(state):
    task, lookup, update = state
    lookup.return_value = ''
    data = payload(task, 'NOVO-01')
    local = LocalizacaoArmazem.query.filter_by(codigo='NOVO-01').first()
    assert local and local.ativo
    assert Evento.query.filter_by(tipo='Endereço registrado pela etiqueta').count() == 1
    svc.confirmar(task, data)
    svc.sincronizar(task)
    update.assert_called_once_with('SKU-1', 'NOVO-01')


def test_leitura_manual_fica_no_historico(state):
    task, _, _ = state
    svc.registrar_leitura(task, 'sku', task.sku, manual=True)
    svc.registrar_leitura(task, 'local', 'B', manual=True)
    assert Evento.query.filter_by(tipo='Leitura digitada').count() == 2


def test_retry_automatico_sincroniza_tarefas_com_falha(app, state):
    task, lookup, update = state
    svc.confirmar(task, payload(task))
    update.side_effect = TimeoutError('boom')
    svc.sincronizar(task)
    assert task.status == 'Aguardando sincronização' and task.erro
    update.side_effect = None
    from conferencia_app.services import recebimento_enderecamento_scheduler as sched
    resultado = sched.executar_ciclo(app)
    assert resultado['sincronizadas'] == 1
    db.session.refresh(task)
    assert task.status == 'Concluído' and task.erro is None


def test_inactive_address(state):
    LocalizacaoArmazem.query.filter_by(codigo='A').first().ativo = False
    db.session.commit()
    with pytest.raises(ValueError, match='desativado'):
        payload(state[0])


@pytest.mark.parametrize('interrompida', [False, True])
def test_retry_recupera_confirmacao_sem_erro(app, state, interrompida):
    task, _, update = state
    svc.confirmar(task, payload(task))
    if interrompida:
        task.executando_em = datetime.now() - timedelta(minutes=11)
        db.session.add(Trava(sku=task.sku, token='interrompido', expira_em=task.executando_em))
        db.session.commit()
    from conferencia_app.services.recebimento_enderecamento_scheduler import executar_ciclo
    assert executar_ciclo(app)['sincronizadas'] == 1
    update.assert_called_once_with('SKU-1', 'A')


def test_proximo_mesma_nf_fora_da_pagina_e_sem_misturar_fornecedor(app, state):
    from conferencia_app.routes.recebimento_enderecamento_routes import proximo_da_nota
    task = state[0]
    task.item.fornecedor = 'Fornecedor A'
    for n in range(42):
        item = ItemNota(numero_nota='123', fornecedor='Fornecedor A' if n == 0 else 'Fornecedor B',
                        codigo_grv='SKU-2', status='Concluído')
        db.session.add(item)
        db.session.flush()
        outra = Tarefa(item_nota_id=item.id, sku='SKU-2', quantidade=1, criado_por='teste')
        db.session.add(outra)
        db.session.flush()
        if n == 0:
            esperado = outra.id
    db.session.commit()
    assert proximo_da_nota(task)['id'] == esperado
    db.session.get(Tarefa, esperado).status = 'Concluído'
    db.session.commit()
    assert proximo_da_nota(task) is None


def test_kpi_concluidos_hoje_preserva_total_historico(app, state):
    task = state[0]
    task.status = 'Concluído'
    task.concluido_em = datetime.now() - timedelta(days=1)
    db.session.commit()
    client = app.test_client()
    with client.session_transaction() as sess:
        sess['username'] = 'admin'
        sess['role'] = 'Admin'
    result = client.get('/api/recebimento/enderecamento').get_json()
    assert result['contadores']['Concluído'] == 1
    assert result['concluidos_hoje'] == 0
    task.concluido_em = datetime.now()
    db.session.commit()
    assert client.get('/api/recebimento/enderecamento').get_json()['concluidos_hoje'] == 1


@pytest.mark.parametrize('quantity', [9, 11, -1, 'NaN', 'Infinity', 'bad'])
def test_invalid_quantities_do_not_confirm(state, quantity):
    with pytest.raises(ValueError):
        svc.confirmar(state[0], payload(state[0], quantity=quantity))
    assert state[0].status == 'Pendente'
    state[2].assert_not_called()


def test_unscanned_or_other_users_token_rejected(state):
    task, _, _ = state
    data = payload(task)
    data['alocacoes'][0]['token'] = 'A'
    with pytest.raises(ValueError, match='Leitura'):
        svc.confirmar(task, data)
    data = payload(task)
    session['username'] = 'outro'
    with pytest.raises(ValueError, match='usuário'):
        svc.confirmar(task, data)


def test_changed_address_requires_rescan(state):
    task, lookup, _ = state
    data = payload(task)
    lookup.return_value = 'C'
    with pytest.raises(ValueError, match='mudou'):
        svc.confirmar(task, data)


def test_alternative_requires_permission_and_reason(state):
    task, _, update = state
    with pytest.raises(ValueError, match='já endereçado'):
        svc.confirmar(task, payload(task,'B'))
    data = payload(task,'B','alternativo')
    with pytest.raises(ValueError, match='permissão'):
        svc.confirmar(task, data)
    data['justificativa'] = 'Área reservada para este recebimento'
    svc.confirmar(task, data, pode_alternar=True)
    svc.sincronizar(task)
    update.assert_called_once_with('SKU-1','A;B')


def test_failure_persists_and_retry_does_not_duplicate_allocations(state):
    task, lookup, update = state
    svc.confirmar(task, payload(task,'B','superlotado'))
    update.side_effect = TimeoutError('secret connection details')
    svc.sincronizar(task)
    assert task.status == 'Aguardando sincronização'
    assert 'secret' not in task.erro
    assert task.concluido_em is None
    lookup.return_value = 'A;C'  # another process appended an address
    update.side_effect = None
    svc.sincronizar(task)
    assert task.status == 'Concluído'
    update.assert_called_with('SKU-1','A;C;B')
    assert len(task.alocacoes) == 1
    assert Evento.query.filter_by(tipo='Falha de sincronização').count() == 1


def test_remote_business_failure_not_success(state):
    task, _, update = state
    svc.confirmar(task,payload(task))
    update.return_value = {'sucesso':False}
    svc.sincronizar(task)
    assert task.status == 'Aguardando sincronização'


def test_sku_lock_prevents_parallel_updates(state):
    task, _, update = state
    svc.confirmar(task,payload(task))
    db.session.add(Trava(sku=task.sku,token='other',expira_em=datetime.now()+timedelta(minutes=5)))
    db.session.commit()
    with pytest.raises(ValueError, match='sendo sincronizado'):
        svc.sincronizar(task)
    update.assert_not_called()


def test_reopen_pending_sync_requires_new_scans_and_preserves_audit(state):
    task, _, update = state
    previous = payload(task)
    svc.confirmar(task,previous)
    with pytest.raises(ValueError, match='justificativa'):
        svc.reabrir(task,'')
    svc.reabrir(task,'Destino precisa de revisão')
    assert task.status == 'Pendente'
    assert task.alocacoes is None
    assert Evento.query.filter_by(tipo='Reaberto para nova leitura').count() == 1
    with pytest.raises(ValueError, match='anterior à revisão'):
        svc.confirmar(task,previous)
    with pytest.raises(ValueError,match='Leitura'):
        svc.confirmar(task,{'alocacoes':[]})
    update.assert_not_called()


def test_estorno_com_saldo_exige_movimento_auditavel(state):
    task, _, update = state
    svc.confirmar(task, payload(task))
    svc.sincronizar(task)
    assert task.status == 'Concluído'
    with pytest.raises(ValueError, match='saldo por endereço'):
        svc.reabrir(task, 'Endereçado no local errado')
    assert task.status == 'Concluído'
    assert task.alocacoes and task.concluido_em
    assert Evento.query.filter_by(tipo='Estornado').count() == 0


def test_receiving_reopened_blocks_address_update(state):
    task, _, update = state
    data = payload(task)
    task.item.status='Pendente'
    db.session.commit()
    with pytest.raises(ValueError,match='recebimento não está concluído'):
        svc.confirmar(task,data)
    update.assert_not_called()


def test_creation_only_conforming_and_idempotent(app):
    with app.test_request_context():
        session['username']='tester'
        good = ItemNota(numero_nota='1',codigo_grv='X',qtd_real=5)
        bad = ItemNota(numero_nota='1',codigo_grv='Y',qtd_real=5)
        db.session.add_all([good,bad]); db.session.flush()
        counts = {str(good.id):2.5,str(bad.id):3}
        conversions = {str(good.id):{'fator':2}}
        ids = svc.criar_pendencias([good,bad],counts,conversions,{good.id},'tester')
        db.session.commit()
        assert svc.criar_pendencias([good,bad],counts,conversions,{good.id},'tester') == ids
        assert Tarefa.query.count() == 1
        assert Tarefa.query.first().quantidade == 5


def test_api_permissions_and_filtered_list(app, state):
    client = app.test_client()
    assert client.get('/api/recebimento/enderecamento').status_code in (302,401,403)
    with client.session_transaction() as sess:
        sess['username']='operator'; sess['role']='Conferente'
    result = client.get('/api/recebimento/enderecamento').get_json()
    assert result['contadores']['Pendente'] == 1
    assert client.get('/api/recebimento/enderecamento?busca=OTHER').get_json()['itens'] == []
    assert client.post('/api/recebimento/enderecamento/locais',json={'codigo':'X','ativo':True}).status_code == 403


def test_final_receiving_creates_queue_but_preliminary_validation_does_not(tmp_path):
    from conferencia_app import create_app
    app = create_app({'TESTING':True, 'SQLALCHEMY_DATABASE_URI':f'sqlite:///{tmp_path / "receiving.db"}'})
    client = app.test_client()
    with client.session_transaction() as sess:
        sess['username']='admin'; sess['role']='Admin'
    with app.app_context():
        item = ItemNota(numero_nota='987',codigo_grv='SKU',descricao='Material',qtd_real=5,status='Pendente')
        db.session.add(item); db.session.commit(); item_id = item.id
    data = {'nota':'987','contagens':{str(item_id):'5'}}
    first = client.post('/validar',json=data)
    assert first.status_code == 200
    with app.app_context():
        assert Tarefa.query.count() == 0
    final = client.post('/validar',json={**data,'forcar_pendencia':True})
    assert final.status_code == 200
    assert len(final.get_json()['enderecamento_ids']) == 1
    with app.app_context():
        assert Tarefa.query.first().status == 'Pendente'
        assert db.session.get(ItemNota,item_id).status == 'Concluído'
    page = client.get('/conferencia')
    assert page.status_code == 200
    assert b'pa-camera' in page.data
    assert b'receb-enderecamento-painel' in page.data
    assert b'href="/recebimento/enderecamento"' not in page.data
    assert client.get('/recebimento/enderecamento').status_code == 200
    assert client.get('/wms/enderecamento').status_code == 200


def test_bridge_location_query_is_authenticated_and_parameterized(monkeypatch):
    from scripts import erp_lancamento_api_bridge as bridge
    from unittest.mock import MagicMock
    monkeypatch.setattr(bridge, '_config', lambda: {})
    monkeypatch.setattr(bridge, '_authorized', lambda cfg: False)
    client = bridge.create_app().test_client()
    assert client.post('/api/erp/produto-localizacao',json={'codigo_interno':'SKU'}).status_code == 401
    monkeypatch.setattr(bridge, '_authorized', lambda cfg: True)
    conn = MagicMock()
    cursor = conn.__enter__.return_value.cursor.return_value.__enter__.return_value
    cursor.fetchall.return_value = [('A;B',)]
    monkeypatch.setattr(bridge, '_conectar', lambda cfg: conn)
    sku = "SKU' OR 1=1 --"
    result = client.post('/api/erp/produto-localizacao',json={'codigo_interno':sku})
    assert result.get_json()['localizacao_estoque'] == 'A;B'
    assert cursor.execute.call_args.args[1] == (1, sku)
    assert sku not in cursor.execute.call_args.args[0]


def test_migration_creates_tables_and_can_run_after_bootstrap(app):
    import importlib.util
    from pathlib import Path
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    spec = importlib.util.spec_from_file_location('putaway_migration', Path('migrations/versions/20260914_recebimento_enderecamento.py'))
    migration = importlib.util.module_from_spec(spec); spec.loader.exec_module(migration)
    with db.engine.begin() as conn:
        with Operations.context(MigrationContext.configure(conn)):
            migration.upgrade()
            migration.downgrade()
            migration.upgrade()
    db.session.add(ItemNota(numero_nota='1')); db.session.flush()
    db.session.add(Tarefa(item_nota_id=ItemNota.query.first().id,sku='X',quantidade=1,criado_por='tester'))
    db.session.commit()
    assert Tarefa.query.count() == 1
