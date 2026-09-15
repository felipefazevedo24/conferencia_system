from datetime import date, datetime, timedelta
import hashlib
import hmac
import json
from unittest.mock import patch

import pytest

from test_app import build_test_app, set_logged_user
from conferencia_app.extensions import db
from conferencia_app.models import (
    AgendamentoMotorista, AgendamentoSolicitacao, AgendamentoVeiculo,
    ExpedicaoRomaneio, ExpedicaoRomaneioNF, Viagem, ViagemParada,
)
from conferencia_app.services.solicitacao_logistica_cif_service import gerar_solicitacoes_entrega_cif


@pytest.fixture
def ambiente(tmp_path):
    app = build_test_app(tmp_path)
    app.config.update(SOLICITACAO_CIF_AUTO_ENABLED=True, SOLICITACAO_CIF_ENTREGA_ENABLED=True)
    client = app.test_client()
    set_logged_user(client, "admin", "Admin")
    return app, client


def criar_romaneio(app, frete, status="Rascunho"):
    with app.app_context():
        rom = ExpedicaoRomaneio(numero_romaneio=f"ROM-{frete}", tipo_frete=frete,
                               status=status, criado_por="admin", cliente="Cliente Teste",
                               data_romaneio=date.today())
        db.session.add(rom)
        db.session.flush()
        for nf in ("101", "102"):
            db.session.add(ExpedicaoRomaneioNF(romaneio_id=rom.id, numero_nf=nf,
                                              adicionado_por="admin", cliente="Cliente Teste",
                                              peso_bruto=20, qtde_volumes=2))
        db.session.commit()
        return rom.id


@pytest.mark.parametrize("frete,origem", [
    ("PROP_REM", "AutoDAP"), ("DAP", "AutoDAP"), ("CIF", "AutoCIF"),
    ("FOB", None), ("PROP_DEST", None),
])
def test_finalizar_romaneio_gera_entregas_na_central(ambiente, frete, origem):
    app, client = ambiente
    rid = criar_romaneio(app, frete)
    with patch('conferencia_app.routes.expedicao_romaneio_routes._analisar_divergencia_frete',
               return_value={"divergentes": [], "nao_validadas": []}), \
         patch('conferencia_app.services.solicitacao_logistica_cif_service._parceiro_cliente_nf',
               return_value={"nome": "Cliente Teste"}):
        response = client.post(f'/api/expedicao/romaneio-fat/{rid}/finalizar')
        assert response.status_code == 200, response.get_json()
        with app.app_context():
            # Reprocessamento (inclui romaneios já existentes) não duplica NFs.
            gerar_solicitacoes_entrega_cif()
            rows = AgendamentoSolicitacao.query.filter_by(tipo="ENTREGA").all()
            assert len(rows) == (2 if origem else 0)
            for row in rows:
                assert row.origem_documento == origem
                assert row.status == "Pendente"
                payload = json.loads(row.payload_origem)
                assert payload['romaneio_id'] == rid
                assert payload['modalidade_frete'] == ("DAP" if origem == "AutoDAP" else "CIF")
    data = client.get('/api/logistica/central-viagens/dashboard').get_json()
    assert len(data['entregas']) == (2 if origem else 0)
    for row in data['entregas']:
        assert row['viagem'] is None
        assert row['origem_documento_label'] == ('Automático (DAP)' if origem == 'AutoDAP' else 'Automático (CIF)')


def test_scheduler_recupera_dap_existente_e_estorno_cancela_entregas(ambiente):
    app, client = ambiente
    rid = criar_romaneio(app, 'PROP_REM', status='Pronto')
    with app.app_context(), patch('conferencia_app.services.solicitacao_logistica_cif_service._parceiro_cliente_nf', return_value={"nome": "Cliente Teste"}):
        assert gerar_solicitacoes_entrega_cif()['criadas'] == 1
        assert gerar_solicitacoes_entrega_cif()['criadas'] == 0
    response = client.post(f'/api/expedicao/romaneio-fat/{rid}/estornar-finalizacao')
    assert response.status_code == 200, response.get_json()
    with app.app_context():
        rows = AgendamentoSolicitacao.query.filter_by(origem_documento='AutoDAP').all()
        assert len(rows) == 2
        assert all(row.status == 'Cancelada' for row in rows)


def criar_tentativa(app):
    with app.app_context():
        veiculo = AgendamentoVeiculo(codigo='TEST-RET', nome_exibicao='Teste')
        motorista = AgendamentoMotorista(nome='Motorista Teste')
        db.session.add_all([veiculo, motorista])
        db.session.flush()
        # A viagem destino pode ter ID menor que o da viagem que falhou.
        destino = Viagem(codigo='TEST-DESTINO', veiculo_id=veiculo.id, motorista_id=motorista.id,
                         status='Planejada', saida_prevista=datetime.now() + timedelta(days=2))
        antiga = Viagem(codigo='TEST-ANTIGA', veiculo_id=veiculo.id, motorista_id=motorista.id,
                        status='EmAndamento', liberada=True, saida_prevista=datetime.now() - timedelta(days=1),
                        retorno_previsto=datetime.now() - timedelta(hours=1))
        sol = AgendamentoSolicitacao(tipo='ENTREGA', status='EmRota', codigo='TEST-SOL',
                                    solicitante='admin', documento_tipo='NF', documento_numero='101',
                                    parceiro_tipo='Cliente', parceiro_nome='Cliente Teste',
                                    logradouro='Rua Teste', cidade='Campinas', uf='SP',
                                    origem_documento='SOLICITACAO_USUARIO', veiculo_id=veiculo.id,
                                    motorista_id=motorista.id, alocado_em=datetime.now())
        db.session.add_all([destino, antiga, sol])
        db.session.flush()
        parada = ViagemParada(viagem_id=antiga.id, solicitacao_id=sol.id, sequencia=1,
                             tipo='ENTREGA', status='Pendente', parceiro_nome='Cliente Teste')
        db.session.add(parada)
        db.session.commit()
        return sol.id, parada.id, antiga.id, destino.id, veiculo.id, motorista.id


@pytest.mark.parametrize('modo', ['anexar', 'nova', 'lote-anexar', 'lote-nova'])
@pytest.mark.parametrize('canal', ['gestor', 'motorista'])
def test_nao_realizada_volta_central_e_pode_remontar(ambiente, modo, canal):
    app, client = ambiente
    sid, pid, antiga, destino, veiculo, motorista = criar_tentativa(app)
    if canal == 'gestor':
        response = client.post(f'/api/viagem/paradas/{pid}/nao-realizada', json={'motivo': 'Cliente fechado'})
    else:
        token = hmac.new(app.config['SECRET_KEY'].encode(), f'viagem:{antiga}'.encode(), hashlib.sha256).hexdigest()[:16]
        response = client.post(f'/motorista/viagem/{antiga}/{token}/parada/{pid}/concluir',
                               json={'resultado': 'NaoRealizada', 'observacao': 'Cliente fechado'})
    assert response.status_code == 200, response.get_json()
    card = client.get('/api/logistica/central-viagens/dashboard?status=Pendente').get_json()['entregas'][0]
    assert card['id'] == sid and card['status'] == 'Pendente' and card['viagem'] is None
    assert sid in [s['id'] for s in client.get('/api/viagem/auxiliares').get_json()['solicitacoes']]
    with app.app_context():
        sol = db.session.get(AgendamentoSolicitacao, sid)
        assert sol.veiculo_id is None and sol.alocado_em is None
        sol.veiculo_id, sol.motorista_id = veiculo, motorista
        sol.status, sol.alocado_em = 'Alocada', datetime.now()
        sol.data_hora_saida_prevista = datetime.combine(date.today(), datetime.min.time()) + timedelta(hours=12)
        sol.data_hora_retorno_prevista = sol.data_hora_saida_prevista + timedelta(hours=1)
        db.session.commit()
    for url in ('/api/viagem/rotas-planejadas', '/api/viagem/assistente/candidatos'):
        response = client.get(url)
        assert response.status_code == 200, response.get_json()
        assert 'TEST-SOL' in response.get_data(as_text=True)
    if canal == 'motorista':
        assert client.post(f'/api/viagem/{antiga}/concluir').status_code == 200
    if modo == 'anexar':
        response = client.post(f'/api/viagem/{destino}/anexar-solicitacao/{sid}')
    elif modo == 'nova':
        response = client.post(f'/api/viagem/nova-de-solicitacao/{sid}')
    else:
        response = client.post('/api/viagem/montar-com-solicitacoes', json={
            'ids': [sid], 'modo': modo.split('-')[1], 'viagem_id': destino,
        })
    assert response.status_code == 200, response.get_json()
    nova = response.get_json()['viagem']['id']
    assert nova != antiga
    card = client.get('/api/logistica/central-viagens/dashboard').get_json()['entregas'][0]
    assert card['viagem']['id'] == nova
    assert card['status'] == 'Alocada'
    # Reabrir a tentativa antiga não pode interferir na nova viagem.
    assert client.post(f'/api/viagem/paradas/{pid}/chegar').status_code == 409
    assert client.post(f'/api/viagem/paradas/{pid}/concluir').status_code == 409
    if canal == 'gestor':
        assert client.post(f'/api/viagem/{antiga}/concluir').status_code == 200
    with app.app_context():
        sol = db.session.get(AgendamentoSolicitacao, sid)
        assert sol.status == 'Alocada' and sol.veiculo_id == veiculo
        historico = db.session.get(ViagemParada, pid)
        assert historico.viagem_id == antiga and historico.status == 'Nao_realizada'
        assert historico.observacao == 'Cliente fechado'
        assert ViagemParada.query.filter_by(solicitacao_id=sid).count() == 2
    # Uma alocação válida continua bloqueando duplicidade.
    response = client.post(f'/api/viagem/nova-de-solicitacao/{sid}')
    assert response.status_code == 409
    assert response.get_json()['viagem_id'] == nova


def test_dashboard_repara_status_legado_sem_voltar_a_viagem_antiga(ambiente):
    app, client = ambiente
    sid, pid, antiga, *_ = criar_tentativa(app)
    with app.app_context():
        p = db.session.get(ViagemParada, pid)
        p.status = 'Nao_realizada'
        sol = db.session.get(AgendamentoSolicitacao, sid)
        sol.status, sol.veiculo_id, sol.motorista_id, sol.alocado_em = 'EmRota', None, None, None
        db.session.commit()
    card = client.get('/api/logistica/central-viagens/dashboard?status=Pendente').get_json()['entregas'][0]
    assert card['id'] == sid and card['status'] == 'Pendente' and card['viagem'] is None


def test_cancelar_viagem_antiga_preserva_realocacao(ambiente):
    app, client = ambiente
    sid, pid, antiga, destino, veiculo, _ = criar_tentativa(app)
    assert client.post(f'/api/viagem/paradas/{pid}/nao-realizada', json={'motivo': 'Cliente fechado'}).status_code == 200
    assert client.post(f'/api/viagem/{destino}/anexar-solicitacao/{sid}').status_code == 200
    assert client.post(f'/api/viagem/{antiga}/cancelar', json={'motivo': 'Viagem interrompida'}).status_code == 200
    with app.app_context():
        sol = db.session.get(AgendamentoSolicitacao, sid)
        assert sol.status == 'Alocada' and sol.veiculo_id == veiculo
