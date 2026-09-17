from decimal import Decimal
from datetime import datetime, timedelta
from uuid import uuid4
from unittest.mock import Mock

import pytest
from sqlalchemy import text

from test_recebimento_enderecamento import app, state, payload
from conferencia_app.extensions import db
from conferencia_app.models import (EnderecoSaldo as Saldo, EnderecoMovimento as Movimento,
    RecebimentoEnderecamentoTrava as Trava, ItemNota, RecebimentoEnderecamento as Tarefa,
    RecebimentoEnderecamentoEvento as Evento, LogEventoFiscalNota, LogExclusaoNota)
from conferencia_app.services import enderecamento_service as svc
from conferencia_app.services import recebimento_enderecamento_service as receb


def operacao(tipo='Conferência de saldo', **kwargs):
    return dict(chave=str(uuid4()), tipo=tipo, sku='SKU-1', unidade='UN',
                origem='A', destino='A' if tipo == 'Conferência de saldo' else 'B',
                quantidade='10', motivo='Contagem física do material', **kwargs)


def contar(endereco='A', qtd='10'):
    data = operacao()
    data.update(destino=endereco, quantidade=qtd)
    return svc.registrar(data, 'admin', True)


@pytest.mark.parametrize('status', ['Concluído', 'Lançado'])
def test_recebimento_aceita_avanco_fiscal_e_credita_uma_vez(state, status):
    task, lookup, update = state
    task.item.status = status
    lookup.return_value = ''
    db.session.commit()
    dados = payload(task)
    receb.confirmar(task, dados)
    receb.sincronizar(task)
    receb.sincronizar(task)
    assert task.status == 'Concluído'
    assert Saldo.query.one().quantidade == Decimal('10')
    assert Saldo.query.one().conferido
    assert Movimento.query.one().tipo == 'Recebimento'
    update.assert_called_once()


def test_endereco_preexistente_requer_conferencia_inicial(state):
    task = state[0]
    receb.confirmar(task, payload(task))
    receb.sincronizar(task)
    assert not Saldo.query.one().conferido
    with pytest.raises(ValueError, match='saldo inicial'):
        svc.registrar(operacao('Transferência'), 'operador')
    assert Saldo.query.filter_by(endereco='B').count() == 0
    contar(qtd='25')
    assert Saldo.query.one().quantidade == Decimal('25')
    assert Saldo.query.one().conferido


def test_transferencia_parcial_total_e_endereco_externo_preservado(state):
    _, lookup, update = state
    contar()
    lookup.return_value = 'A;EXTERNO'
    data = operacao('Transferência'); data['quantidade'] = '4'
    svc.registrar(data, 'operador')
    svc.sincronizar('SKU-1')
    assert Saldo.query.filter_by(endereco='A').one().quantidade == Decimal('6')
    assert Saldo.query.filter_by(endereco='B').one().quantidade == Decimal('4')
    update.assert_called_with('SKU-1', 'A;EXTERNO;B')
    data = operacao('Transferência'); data['quantidade'] = '6'
    svc.registrar(data, 'operador')
    svc.sincronizar('SKU-1')
    update.assert_called_with('SKU-1', 'EXTERNO;B')
    assert sum(s.quantidade for s in Saldo.query.all()) == Decimal('10')
    assert Movimento.query.filter_by(sincronizado_em=None).count() == 0


def test_repeticao_de_requisicao_nao_duplica_saldo_e_rejeita_conteudo_diferente(state):
    contar()
    data = operacao('Transferência')
    first = svc.registrar(data, 'operador')
    assert svc.registrar(data, 'operador') == first
    assert Saldo.query.filter_by(endereco='B').one().quantidade == Decimal('10')
    data['quantidade'] = '3'
    with pytest.raises(ValueError, match='outra operação'):
        svc.registrar(data, 'operador')
    assert Movimento.query.count() == 2


@pytest.mark.parametrize('q', ['0', '-1', '11', 'NaN', 'Infinity', 'abc', '0.0000001'])
def test_transferencia_invalida_nao_altera_saldo(state, q):
    contar()
    data = operacao('Transferência'); data['quantidade'] = q
    with pytest.raises(ValueError):
        svc.registrar(data, 'operador')
    assert Saldo.query.one().quantidade == Decimal('10')
    assert Movimento.query.count() == 1


def test_mesmo_endereco_unidade_e_destino_nao_conferido_bloqueados(state):
    _, lookup, _ = state
    contar()
    for changes, message in [({'destino':'A'}, 'diferentes'), ({'unidade':'KG'}, 'mesma unidade')]:
        data = operacao('Transferência'); data.update(changes)
        with pytest.raises(ValueError, match=message):
            svc.registrar(data, 'operador')
    lookup.return_value = 'A;B'
    with pytest.raises(ValueError, match='saldo inicial'):
        svc.registrar(operacao('Transferência'), 'operador')
    assert Saldo.query.one().quantidade == Decimal('10')


def test_falha_no_grv_preserva_operacao_e_retry_nao_duplica(state):
    _, lookup, update = state
    contar()
    data = operacao('Transferência')
    svc.registrar(data, 'operador')
    update.side_effect = TimeoutError('segredo de conexão')
    svc.sincronizar('SKU-1')
    assert Movimento.query.filter_by(sincronizado_em=None).count() == 2
    assert all('segredo' not in m.erro for m in Movimento.query.all())
    update.side_effect = None
    svc.sincronizar('SKU-1')
    assert Movimento.query.filter_by(sincronizado_em=None).count() == 0
    assert Saldo.query.filter_by(endereco='B').one().quantidade == Decimal('10')
    assert svc.registrar(data, 'operador')
    assert Movimento.query.count() == 2


def test_trava_impede_duas_movimentacoes_do_mesmo_sku(state):
    contar()
    reg = db.session.get(Trava, 'SKU-1')
    reg.token = 'outro'; reg.expira_em = datetime.now()+timedelta(minutes=5)
    db.session.commit()
    with pytest.raises(ValueError, match='sendo sincronizado'):
        svc.registrar(operacao('Transferência'), 'operador')
    assert Saldo.query.one().quantidade == Decimal('10')
    assert db.session.get(Trava, 'SKU-1').token == 'outro'


def test_recebimento_fracionado_normaliza_residuo_binario(state):
    task, lookup, _ = state
    lookup.return_value = ''
    task.quantidade = .1 * 3
    db.session.commit()
    receb.confirmar(task, payload(task, quantity=.1 * 3))
    receb.sincronizar(task)
    assert task.status == 'Concluído'
    assert Saldo.query.one().quantidade == Decimal('0.3')


def test_falha_ao_gravar_movimento_reverte_os_dois_saldos(state, monkeypatch):
    contar()
    original = db.session.add
    def falhar(obj, *args, **kwargs):
        if isinstance(obj, Movimento) and obj.tipo == 'Transferência':
            raise RuntimeError('interrompido antes de salvar')
        return original(obj, *args, **kwargs)
    monkeypatch.setattr(db.session, 'add', falhar)
    with pytest.raises(RuntimeError):
        svc.registrar(operacao('Transferência'), 'operador')
    assert Saldo.query.one().quantidade == Decimal('10')
    assert Movimento.query.count() == 1
    assert db.session.get(Trava, 'SKU-1').token is None


def test_retry_automatico_inclui_movimentacoes(app, state):
    contar()
    from conferencia_app.services.recebimento_enderecamento_scheduler import executar_ciclo
    executar_ciclo(app)
    assert Movimento.query.one().sincronizado_em


def test_fila_exibe_impedimento_antes_das_leituras(app, state):
    task = state[0]
    task.item.status = 'Pendente'; db.session.commit()
    client = app.test_client()
    with client.session_transaction() as sess:
        sess['username'] = 'operador'; sess['role'] = 'Conferente'
    item = client.get('/api/recebimento/enderecamento').get_json()['itens'][0]
    assert 'não está concluído' in item['impedimento']
    r=client.post(f'/api/recebimento/enderecamento/{task.id}/leitura',json={'tipo':'sku','codigo':'SKU-1'})
    assert r.status_code == 409
    state[1].assert_not_called()


def test_endereco_zerado_reintroduzido_externamente_nao_e_apagado(state):
    _, lookup, update = state
    contar()
    svc.registrar(operacao('Transferência'), 'operador')
    svc.sincronizar('SKU-1')
    lookup.return_value = 'A;B'  # A foi readicionado fora do módulo após finalizar a transferência.
    data = operacao('Transferência'); data.update(origem='B', destino='C')
    svc.registrar(data, 'operador')
    svc.sincronizar('SKU-1')
    update.assert_called_with('SKU-1', 'A;C')


def test_conferencia_nao_sobrepoe_recebimento_aguardando_sync(state):
    task = state[0]
    receb.confirmar(task, payload(task))
    with pytest.raises(ValueError, match='recebimento deste SKU'):
        contar()
    assert Saldo.query.count() == 0


def test_api_permissoes_e_filtros(app, state):
    client = app.test_client()
    assert client.get('/api/enderecamento/saldos').status_code in (302, 401, 403)
    with client.session_transaction() as sess:
        sess['username'] = 'operador'; sess['role'] = 'Conferente'
    assert client.post('/api/enderecamento/movimentar', json=operacao()).status_code == 403
    contar()
    data = operacao('Transferência')
    r = client.post('/api/enderecamento/movimentar', json=data)
    assert r.status_code == 200 and r.get_json()['item']['sincronizado']
    assert client.get('/api/enderecamento/saldos?busca=B').get_json()['total'] == 1
    assert client.get('/api/enderecamento/historico?pendentes=1').get_json()['total'] == 0
    assert client.get('/api/enderecamento/historico?busca=INEXISTENTE').get_json()['total'] == 0
    assert client.post('/api/enderecamento/movimentar', json=[]).status_code == 400


def test_saldo_zero_ultima_localizacao_fica_pendente_sem_falso_sucesso(state):
    contar(qtd='0')
    svc.sincronizar('SKU-1')
    assert not Movimento.query.one().sincronizado_em
    assert 'última localização' in Movimento.query.one().erro
    state[2].assert_not_called()


def test_migration_idempotente_e_recriacao(app, state):
    import importlib.util
    from pathlib import Path
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    spec = importlib.util.spec_from_file_location('endereco_migration', Path('migrations/versions/20260917_enderecamento_movimentos.py'))
    migration = importlib.util.module_from_spec(spec); spec.loader.exec_module(migration)
    with db.engine.begin() as conn, Operations.context(MigrationContext.configure(conn)):
        migration.upgrade()
        migration.downgrade()
        migration.upgrade()
        migration.upgrade()
    contar()
    assert Saldo.query.count() == 1


def test_exclusao_nf_reaberta_arquiva_tarefa_sem_apagar_movimentacao(tmp_path):
    from conferencia_app import create_app
    app = create_app({'TESTING':True, 'SQLALCHEMY_DATABASE_URI':f'sqlite:///{tmp_path / "exclusao.db"}'})
    client = app.test_client()
    with client.session_transaction() as sess:
        sess['username']='admin'; sess['role']='Admin'
    with app.app_context():
        db.session.execute(text('PRAGMA foreign_keys = ON'))
        item = ItemNota(numero_nota='999', status='Pendente', fornecedor='Teste')
        db.session.add(item); db.session.flush()
        tarefa = Tarefa(item_nota_id=item.id, sku='SKU', quantidade=1, criado_por='admin', status='Concluído')
        db.session.add(tarefa); db.session.flush()
        db.session.add(Evento(tarefa_id=tarefa.id, tipo='Criado', usuario='admin'))
        db.session.add(Movimento(chave=f'recebimento:{tarefa.id}', sku='SKU', unidade='UN',
            tipo='Recebimento', quantidade=1, usuario='admin', sincronizado_em=datetime.now()))
        db.session.commit()
    r=client.post('/api/excluir_nota_pendente', json={'nota':'999','confirmacao_nota':'999','motivo':'Importação duplicada'})
    assert r.status_code == 200
    with app.app_context():
        assert ItemNota.query.filter_by(numero_nota='999').count() == 0
        assert Tarefa.query.count() == 0 and Evento.query.count() == 0
        assert Movimento.query.count() == 1
        assert 'Criado' in LogEventoFiscalNota.query.filter_by(numero_nota='999').one().payload_json
        assert LogExclusaoNota.query.filter_by(numero_nota='999').count() == 1


def test_migration_cli_aplica_e_verifica_sem_iniciar_aplicacao(tmp_path):
    import os
    import subprocess
    import sys
    env = dict(os.environ, DB_PATH=str(tmp_path/'migration.db'))
    env.pop('DATABASE_URL', None)
    cmd = [sys.executable, 'scripts/aplicar_migracao_enderecamento.py']
    for args, code in [(['--check'], 1), ([], 0), ([], 0), (['--check'], 0)]:
        result = subprocess.run(cmd+args, env=env, capture_output=True, text=True)
        assert result.returncode == code, result.stdout+result.stderr


def test_duas_sessoes_concorrentes_nao_consumem_o_mesmo_saldo(tmp_path, monkeypatch):
    from flask import Flask
    from threading import Thread, Event
    app = Flask('concorrencia')
    app.config.update(SQLALCHEMY_DATABASE_URI=f'sqlite:///{tmp_path/"concorrencia.db"}')
    db.init_app(app)
    monkeypatch.setattr(receb, 'buscar_localizacao_produto_grv', lambda sku: 'A')
    with app.app_context():
        db.create_all()
        contar()
    iniciou, liberar = Event(), Event()
    resultados = []
    def consulta(sku):
        iniciou.set()
        assert liberar.wait(10)
        return 'A'
    monkeypatch.setattr(receb, 'buscar_localizacao_produto_grv', consulta)
    def primeira():
        with app.app_context():
            try:
                resultados.append(svc.registrar(operacao('Transferência'), 'primeiro'))
            except Exception as exc:
                resultados.append(exc)
            finally:
                db.session.remove()
    worker = Thread(target=primeira)
    worker.start()
    try:
        assert iniciou.wait(10)
        with app.app_context(), pytest.raises(ValueError, match='sendo sincronizado'):
            svc.registrar(operacao('Transferência'), 'segundo')
    finally:
        liberar.set(); worker.join(10)
    assert not worker.is_alive()
    assert len(resultados)==1 and isinstance(resultados[0], int), resultados
    with app.app_context():
        assert Saldo.query.filter_by(endereco='A').one().quantidade==0
        assert Saldo.query.filter_by(endereco='B').one().quantidade==10
        assert Movimento.query.count()==2
        db.session.remove(); db.engine.dispose()
