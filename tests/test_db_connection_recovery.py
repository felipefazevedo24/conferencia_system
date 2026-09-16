from unittest.mock import Mock

import pytest
from flask import Flask
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError

from conferencia_app.extensions import proteger_pool_entre_processos
from conferencia_app.routes import painel_tv_routes as painel


def test_pool_reconecta_quando_pid_muda(monkeypatch):
    engine = create_engine('sqlite://')
    proteger_pool_entre_processos(engine)
    with engine.connect() as conn:
        antiga = conn.connection.dbapi_connection
    monkeypatch.setattr('conferencia_app.extensions.os.getpid', lambda: -1)
    with engine.connect() as conn:
        assert conn.connection.dbapi_connection is not antiga
        assert conn.scalar(text('SELECT 1')) == 1
    engine.dispose()


@pytest.mark.parametrize('persistente', [False, True])
def test_painel_descarta_conexao_sem_rollback_e_limita_retry(monkeypatch, persistente):
    app = Flask(__name__)
    app.register_blueprint(painel.painel_tv_bp)
    erro = OperationalError('SELECT', {}, Exception(2014, 'Command Out of Sync'), connection_invalidated=True)
    consulta = Mock(side_effect=[erro, erro if persistente else {'total': 7}])
    sessao = Mock()
    monkeypatch.setattr(painel, '_coletar_indicadores', consulta)
    monkeypatch.setattr(painel, 'db', Mock(session=sessao))
    response = app.test_client().get('/api/painel/indicadores')
    assert response.status_code == (503 if persistente else 200)
    assert consulta.call_count == 2
    assert sessao.invalidate.call_count == (2 if persistente else 1)
    sessao.rollback.assert_not_called()


def test_painel_nao_repete_erro_de_consulta(monkeypatch):
    app = Flask(__name__)
    app.config['TESTING'] = True
    app.register_blueprint(painel.painel_tv_bp)
    erro = OperationalError('SELECT', {}, Exception('consulta invalida'))
    consulta = Mock(side_effect=erro)
    monkeypatch.setattr(painel, '_coletar_indicadores', consulta)
    monkeypatch.setattr(painel, 'db', Mock())
    with pytest.raises(OperationalError):
        app.test_client().get('/api/painel/indicadores')
    assert consulta.call_count == 1
