"""Trilha de auditoria da Conferencia de Expedicao: tudo que grava na ordem
(progresso salvo, tentativa bloqueada, fotos, faturamento, romaneio e
expedicao) precisa deixar log de quem fez, quando e o que mudou."""
import io
import json
from unittest.mock import patch

import pytest

from test_app import build_test_app, set_logged_user
from conferencia_app.extensions import db
from conferencia_app.models import (
    ExpedicaoConferenciaLog,
    ExpedicaoOrdemFat,
    ExpedicaoOrdemFatItem,
    ExpedicaoOrdemST,
    ExpedicaoOrdemSTItem,
)
from conferencia_app.services import expedicao_fat_service, expedicao_st_service


def _modelos(origem):
    if origem == 'fat':
        return ExpedicaoOrdemFat, ExpedicaoOrdemFatItem, {'cod_ordem_fat': 123}, expedicao_fat_service
    return ExpedicaoOrdemST, ExpedicaoOrdemSTItem, {'cod_ordem_compra': '123'}, expedicao_st_service


def _criar_ordem(app, origem, status='Pendente de conferência', numero_nf=None, qtdes=(15, 60)):
    Ordem, Item, chave, _ = _modelos(origem)
    with app.app_context():
        ordem = Ordem(**chave, status=status, numero_nf=numero_nf)
        db.session.add(ordem)
        db.session.flush()
        ids = []
        for linha, qtde in enumerate(qtdes):
            item = Item(ordem_id=ordem.id, linha=linha, cod_interno=f'COD-{linha}', item=f'Item {linha}',
                        qtde_a_faturar=qtde)
            db.session.add(item)
            db.session.flush()
            ids.append(item.id)
        db.session.commit()
        return ids


def _logs(app, origem):
    with app.app_context():
        linhas = (ExpedicaoConferenciaLog.query.filter_by(origem=origem)
                  .order_by(ExpedicaoConferenciaLog.id.asc()).all())
        return [
            {
                'acao': lg.acao, 'usuario': lg.usuario, 'codigo': lg.codigo_interno,
                'de': lg.status_anterior, 'para': lg.status_novo, 'divergente': bool(lg.divergente),
                **json.loads(lg.detalhes),
            }
            for lg in linhas
        ]


def _base(origem):
    return '/api/expedicao/conf-cega' + ('-st' if origem == 'st' else '') + '/ordens/123'


CABECALHO = {'peso_liquido': '120', 'peso_bruto': '135', 'qtde_volumes': '1', 'especie_volumes': 'Palete'}


@pytest.mark.parametrize('origem', ['fat', 'st'])
def test_salvar_progresso_registra_quem_contou_cada_item(tmp_path, origem):
    app = build_test_app(tmp_path)
    client = app.test_client()
    set_logged_user(client, 'davmor', 'Admin')
    id_a, id_b = _criar_ordem(app, origem)

    resposta = client.post(_base(origem) + '/salvar-parcial', json={
        'itens': [{'id': id_a, 'qtde_conferida': 15}], 'peso_liquido': '120',
    })
    assert resposta.status_code == 200, resposta.get_data(as_text=True)

    (log,) = _logs(app, origem)
    assert log['acao'] == 'salvar_parcial'
    assert log['usuario'] == 'davmor'
    assert log['codigo'].startswith('CNF-')
    assert log['de'] == log['para'] == 'Pendente de conferência'
    assert [(i['id'], i['de'], i['para']) for i in log['itens']] == [(id_a, None, 15)]
    assert [(c['campo'], c['de'], c['para']) for c in log['cabecalho']] == [('peso_liquido', '', '120')]

    # A conferencia final so' loga o que mudou nela; o item do checkpoint ja'
    # esta' coberto pelo log acima - juntos, os dois contam a historia inteira.
    resposta = client.post(_base(origem) + '/conferir', json={
        'itens': [{'id': id_a, 'qtde_conferida': 15}, {'id': id_b, 'qtde_conferida': 60}], **CABECALHO,
    })
    assert resposta.get_json()['sucesso'] is True, resposta.get_data(as_text=True)
    logs = _logs(app, origem)
    assert [lg['acao'] for lg in logs] == ['salvar_parcial', 'conferencia']
    assert [(i['id'], i['de'], i['para']) for i in logs[1]['itens']] == [(id_b, None, 60)]


@pytest.mark.parametrize('origem', ['fat', 'st'])
def test_tentativa_com_divergencia_fica_registrada_sem_gravar_contagem(tmp_path, origem):
    app = build_test_app(tmp_path)
    client = app.test_client()
    set_logged_user(client, 'davmor', 'Admin')
    id_a, id_b = _criar_ordem(app, origem)
    _, Item, _, _ = _modelos(origem)

    resposta = client.post(_base(origem) + '/conferir', json={
        'itens': [{'id': id_a, 'qtde_conferida': 15}, {'id': id_b, 'qtde_conferida': 59}], **CABECALHO,
    })
    assert resposta.get_json()['bloqueado'] is True

    (log,) = _logs(app, origem)
    assert log['acao'] == 'tentativa_bloqueada'
    assert log['usuario'] == 'davmor'
    assert log['divergente'] is True
    assert log['de'] == log['para'] == 'Pendente de conferência'
    assert [(i['id'], i['para'], i['divergente']) for i in log['itens']] == [(id_a, 15, False), (id_b, 59, True)]
    with app.app_context():
        assert [it.qtde_conferida for it in Item.query.order_by(Item.linha).all()] == [None, None]


@pytest.mark.parametrize('origem', ['fat', 'st'])
def test_fotos_registram_arquivos_e_substituicao_da_foto_do_cliente(tmp_path, origem):
    app = build_test_app(tmp_path)
    app.config.update(EXPEDICAO_CONFERENCIA_FOTOS_DIR=str(tmp_path / 'fotos'), EXPEDICAO_FOTOS_STORAGE='local')
    client = app.test_client()
    set_logged_user(client, 'davmor', 'Admin')
    _criar_ordem(app, origem, status='Faturado', numero_nf='456')

    def enviar():
        resposta = client.post(_base(origem) + '/fotos-preexpedicao', data={
            'fotos_material': (io.BytesIO(b'foto-material'), 'material.jpg'),
            'foto_cliente': (io.BytesIO(b'foto-cliente'), 'cliente.jpg'),
        })
        assert resposta.status_code == 200, resposta.get_data(as_text=True)

    enviar()
    enviar()

    primeiro, segundo = _logs(app, origem)
    assert primeiro['acao'] == segundo['acao'] == 'fotos'
    assert primeiro['usuario'] == 'davmor'
    assert [c['campo'] for c in primeiro['cabecalho']] == ['foto_material', 'foto_cliente']
    foto_cliente_1 = primeiro['cabecalho'][1]
    foto_cliente_2 = segundo['cabecalho'][1]
    assert foto_cliente_1['de'] == '' and foto_cliente_1['para'].startswith('cliente_reg')
    # Segunda foto do cliente sobrescreve a primeira: o log guarda qual era.
    assert foto_cliente_2['de'] == foto_cliente_1['para']


@pytest.mark.parametrize('origem', ['fat', 'st'])
def test_faturamento_pela_sincronizacao_fica_em_nome_do_sistema(tmp_path, origem):
    app = build_test_app(tmp_path)
    _criar_ordem(app, origem, status='Conferido/Ag. Fat')
    _, _, _, servico = _modelos(origem)
    linha = ({'cod_ordem_fat': 123, 'numero_nf': '12209', 'status': 'faturado'} if origem == 'fat'
             else {'cod_ordem_compra': '123', 'numero_nf': '12209', 'status': 'faturado'})

    with app.app_context(), patch.object(servico, 'buscar_ordens_api', return_value=[linha]):
        servico.sincronizar_ordens()
        servico.sincronizar_ordens()  # ja' faturada: nao pode logar de novo

    (log,) = _logs(app, origem)
    assert log['acao'] == 'faturamento'
    assert log['usuario'] == 'SISTEMA'
    assert (log['de'], log['para']) == ('Conferido/Ag. Fat', 'Faturado')
    assert [(c['campo'], c['para']) for c in log['cabecalho']] == [('numero_nf', '12209')]


@pytest.mark.parametrize('origem', ['fat', 'st'])
def test_romaneio_e_expedicao_registram_cada_mudanca_de_etapa(tmp_path, origem):
    app = build_test_app(tmp_path)
    _criar_ordem(app, origem, status='Faturado', numero_nf='456')
    _, _, _, servico = _modelos(origem)

    with app.app_context():
        assert servico.marcar_em_romaneio_por_nf('456') == 1
        assert servico.marcar_expedido_por_nf('456', registro_id=7, usuario='robdac') == 1
        assert servico.reverter_expedicao_por_nf('456') == 1
        assert servico.reverter_romaneio_por_nf('456') == 1

    logs = _logs(app, origem)
    assert [(lg['acao'], lg['de'], lg['para'], lg['usuario']) for lg in logs] == [
        ('romaneio', 'Faturado', 'Em Romaneio', 'SISTEMA'),
        ('expedicao', 'Em Romaneio', 'Expedido', 'robdac'),
        ('expedicao_estorno', 'Expedido', 'Em Romaneio', 'SISTEMA'),
        ('romaneio_estorno', 'Em Romaneio', 'Faturado', 'SISTEMA'),
    ]


@pytest.mark.parametrize('origem', ['fat', 'st'])
def test_previsto_alterado_pelo_erp_fica_logado_e_so_admin_enxerga(tmp_path, origem):
    app = build_test_app(tmp_path)
    client = app.test_client()
    id_a, id_b = _criar_ordem(app, origem)
    _, _, _, servico = _modelos(origem)
    chave = {'cod_ordem_fat': 123} if origem == 'fat' else {'cod_ordem_compra': '123'}
    # O ERP reduz o item A de 15 para 12 e tira o item B da ordem.
    linhas = [{**chave, 'cod_interno': 'COD-0', 'item': 'Item 0', 'qtde_a_faturar': 12}]

    with app.app_context(), patch.object(servico, 'buscar_ordens_api', return_value=linhas):
        servico.sincronizar_ordens()
        servico.sincronizar_ordens()  # nada mudou de novo: nao pode repetir o log

    (log,) = _logs(app, origem)
    assert log['acao'] == 'previsto_alterado'
    assert log['usuario'] == 'SISTEMA'
    assert [(i['id'], i['de'], i['para']) for i in log['itens']] == [(id_a, 15, 12), (id_b, 60, None)]

    def acoes_no_historico():
        return [h['acao'] for h in client.get(_base(origem)).get_json()['historico']]

    set_logged_user(client, 'davmor', 'Conferente')
    with app.app_context():
        from conferencia_app.models import PermissaoAcesso
        db.session.add(PermissaoAcesso(scope_type='ROLE', scope_id='Conferente',
                                       permission_key='PAGE_EXPEDICAO_CONF_CEGA', allow=True))
        db.session.commit()
    assert acoes_no_historico() == []
    set_logged_user(client, 'admin', 'Admin')
    assert acoes_no_historico() == ['previsto_alterado']


def _trilha(app):
    from conferencia_app.models import ExpedicaoTrilhaLog
    with app.app_context():
        return [
            {
                'entidade': t.entidade, 'entidade_id': t.entidade_id, 'tabela': t.tabela, 'operacao': t.operacao,
                'usuario': t.usuario, 'rota': t.rota, 'nf': t.numero_nf,
                'mudancas': {m['campo']: (m['de'], m['para']) for m in json.loads(t.detalhes)},
            }
            for t in ExpedicaoTrilhaLog.query.order_by(ExpedicaoTrilhaLog.id.asc()).all()
        ]


def test_trilha_registra_criacao_alteracao_e_exclusao_do_romaneio(tmp_path):
    from conferencia_app.models import ExpedicaoRomaneio, ExpedicaoRomaneioNF
    app = build_test_app(tmp_path)
    with app.app_context():
        romaneio = ExpedicaoRomaneio(numero_romaneio='ROM-1', criado_por='davmor', status='Rascunho')
        db.session.add(romaneio)
        db.session.flush()
        db.session.add(ExpedicaoRomaneioNF(romaneio_id=romaneio.id, numero_nf='456', adicionado_por='davmor'))
        romaneio_id = romaneio.id
        db.session.commit()

        # Objeto expirado pelo commit: o valor anterior so' existe no banco, e
        # mesmo assim precisa aparecer no "de".
        romaneio.status = 'Expedido'
        romaneio.placa = 'ABC1D23'
        db.session.commit()

        # Exclusao em massa nao passa pelo flush do ORM e mesmo assim precisa de rastro.
        ExpedicaoRomaneioNF.query.filter_by(romaneio_id=romaneio_id).delete()
        db.session.delete(romaneio)
        db.session.commit()

    trilha = _trilha(app)
    assert [(t['tabela'], t['operacao']) for t in trilha] == [
        ('expedicao_romaneio', 'criado'),
        ('expedicao_romaneio_nf', 'criado'),
        ('expedicao_romaneio', 'alterado'),
        ('expedicao_romaneio_nf', 'excluido'),
        ('expedicao_romaneio', 'excluido'),
    ]
    assert {t['entidade'] for t in trilha} == {'romaneio'}
    assert {t['entidade_id'] for t in trilha} == {romaneio_id}
    assert trilha[0]['mudancas']['numero_romaneio'] == (None, 'ROM-1')
    assert trilha[1]['nf'] == '456'
    assert trilha[2]['mudancas'] == {'status': ('Rascunho', 'Expedido'), 'placa': (None, 'ABC1D23')}
    assert trilha[3]['mudancas']['numero_nf'] == ('456', None)
    assert trilha[4]['mudancas']['status'] == ('Expedido', None)
    assert trilha[0]['usuario'] == 'SISTEMA'  # fora de requisicao


def test_trilha_do_registro_de_expedicao_guarda_usuario_e_rota_e_some_no_rollback(tmp_path):
    from conferencia_app.models import ExpedicaoConferenciaSimples
    app = build_test_app(tmp_path)
    app.config.update(EXPEDICAO_CONFERENCIA_FOTOS_DIR=str(tmp_path / 'fotos'), EXPEDICAO_FOTOS_STORAGE='local')
    client = app.test_client()
    set_logged_user(client, 'davmor', 'Admin')
    _criar_ordem(app, 'fat', status='Faturado', numero_nf='456')

    resposta = client.post(_base('fat') + '/fotos-preexpedicao', data={
        'fotos_material': (io.BytesIO(b'foto-material'), 'material.jpg'),
    })
    assert resposta.status_code == 200, resposta.get_data(as_text=True)

    trilha = _trilha(app)
    assert [(t['tabela'], t['operacao']) for t in trilha] == [
        ('expedicao_conferencia_simples', 'criado'),
        ('expedicao_conferencia_simples_foto', 'criado'),
    ]
    assert all(t['usuario'] == 'davmor' and t['entidade'] == 'registro' for t in trilha)
    assert trilha[0]['rota'] == 'POST expedicao_fat.upload_fotos_preexpedicao'
    assert trilha[0]['mudancas']['status'] == (None, 'Pendente de expedição')

    eventos = client.get('/api/expedicao/auditoria/trilha?q=456').get_json()['eventos']
    assert [e['operacao'] for e in eventos] == ['criado', 'criado']

    # Mudanca desfeita nao pode deixar log de algo que nunca valeu.
    with app.app_context():
        registro = ExpedicaoConferenciaSimples.query.one()
        registro.status = 'Finalizado'
        db.session.flush()
        db.session.rollback()
    assert len(_trilha(app)) == 2
