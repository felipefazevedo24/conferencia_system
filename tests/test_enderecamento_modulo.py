from decimal import Decimal
from datetime import datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import text

from test_recebimento_enderecamento import app, state, payload
from conferencia_app.extensions import db
from conferencia_app.models import (EnderecoSaldo as Saldo, EnderecoMovimento as Movimento,
    RecebimentoEnderecamentoTrava as Trava, ItemNota, RecebimentoEnderecamento as Tarefa,
    RecebimentoEnderecamentoEvento as Evento, LogEventoFiscalNota, LogExclusaoNota,
    LocalizacaoArmazem)
from conferencia_app.services import enderecamento_service as svc
from conferencia_app.services import recebimento_enderecamento_service as receb
from conferencia_app.services import erp_estoque_service

# Formato real da bridge: uma linha por produto, com todos os endereços num
# campo de texto só, separados por ';' (ver ESTOQUE_SQL em
# scripts/erp_lancamento_api_bridge.py). O GRV não tem quantidade por endereço.
ESTOQUE_GRV = {'por_codigo': {
    'SKU-1': {'item': 'Material de teste', 'unidade': 'UN', 'qtde_total': 10,
              'familia': 'N - 01 - MATERIA PRIMA', 'localizacoes': ['A;B']},
    'SKU-2': {'item': 'Outro material', 'unidade': 'CX', 'qtde_total': 3,
              'familia': 'N - 01 - MATERIA PRIMA', 'localizacoes': ['B']},
    'SKU-3': {'item': 'Material sem endereço', 'unidade': 'UN', 'qtde_total': 7,
              'familia': 'N - 01 - MATERIA PRIMA', 'localizacoes': []},
    'SKU-4': {'item': 'Material zerado', 'unidade': 'UN', 'qtde_total': 0,
              'familia': 'N - 01 - MATERIA PRIMA', 'localizacoes': []},
    'SKU-9': {'item': 'Mão de obra de terceiro', 'unidade': 'UN', 'qtde_total': 0,
              'familia': 'N - 09 - SERVIÇOS', 'localizacoes': []},
    'SKU-8': {'item': 'Caneta', 'unidade': 'UN', 'qtde_total': 5,
              'familia': 'N - 41 - USO E CONSUMO (S/ ESTOQUE)', 'localizacoes': []}}}


def operacao(**kwargs):
    dados = dict(chave=str(uuid4()), sku='SKU-1', unidade='UN', origem='A', destino='B',
                 quantidade='10', motivo='Reorganização da prateleira')
    dados.update(kwargs)
    return dados


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


def test_movimenta_sem_conferencia_previa_de_saldo(state):
    """O Sync não controla saldo: mover só depende do endereço atual no GRV."""
    _, lookup, update = state
    assert svc.registrar(operacao(), 'operador')
    svc.sincronizar('SKU-1')
    update.assert_called_with('SKU-1', 'B')
    assert Saldo.query.count() == 0
    assert Movimento.query.one().tipo == 'Movimentação'
    assert Movimento.query.one().quantidade == Decimal('10')


def test_sem_origem_acrescenta_endereco_sem_tirar_do_atual(state):
    _, lookup, update = state
    svc.registrar(operacao(origem=''), 'operador')
    svc.sincronizar('SKU-1')
    update.assert_called_with('SKU-1', 'A;B')
    assert Movimento.query.one().origem is None


def test_movimentacao_sem_motivo_e_aceita(state):
    """Exigir motivo a cada operação travava o operador no chão de fábrica."""
    _, _, update = state
    svc.registrar(operacao(motivo=''), 'operador')
    svc.sincronizar('SKU-1')
    update.assert_called_with('SKU-1', 'B')
    assert Movimento.query.one().motivo == ''


def test_grafia_diferente_nao_cria_endereco_duplicado(state):
    """'r1 pd1', 'R1  PD1' e 'R1 PD1' são o mesmo lugar físico."""
    _, lookup, update = state
    svc.registrar(operacao(origem='a', destino='r1   pd1'), 'operador')
    svc.sincronizar('SKU-1')
    update.assert_called_with('SKU-1', 'R1 PD1')
    assert LocalizacaoArmazem.query.filter_by(codigo='R1 PD1').count() == 1
    lookup.return_value = 'R1 PD1'
    svc.registrar(operacao(origem='R1 pd1', destino='B'), 'operador')
    svc.sincronizar('SKU-1')
    update.assert_called_with('SKU-1', 'B')


def test_endereco_desativado_nao_recebe_mas_deixa_o_material_sair(state):
    _, lookup, update = state
    LocalizacaoArmazem.query.filter_by(codigo='B').update({'ativo': False})
    LocalizacaoArmazem.query.filter_by(codigo='A').update({'ativo': False})
    db.session.commit()
    with pytest.raises(ValueError, match='desativado'):
        svc.registrar(operacao(destino='B'), 'operador')
    # Sair de um endereço desativado precisa funcionar, senão desativar prende o material.
    svc.registrar(operacao(origem='A', destino='C'), 'operador')
    svc.sincronizar('SKU-1')
    update.assert_called_with('SKU-1', 'C')


def test_origem_fora_dos_enderecos_atuais_e_recusada(state):
    with pytest.raises(ValueError, match='não consta'):
        svc.registrar(operacao(origem='Z'), 'operador')
    assert Movimento.query.count() == 0


def test_operacoes_encadeiam_enquanto_o_envio_ao_grv_nao_passa(state):
    """Sem livro de saldo, é a operação pendente que diz onde o material está."""
    _, lookup, update = state
    update.side_effect = TimeoutError('offline')
    svc.registrar(operacao(), 'operador')
    svc.sincronizar('SKU-1')
    svc.registrar(operacao(origem='B', destino='C'), 'operador')
    update.side_effect = None
    svc.sincronizar('SKU-1')
    update.assert_called_with('SKU-1', 'C')
    assert Movimento.query.filter_by(sincronizado_em=None).count() == 0


def test_endereco_externo_e_preservado_na_movimentacao(state):
    _, lookup, update = state
    lookup.return_value = 'A;EXTERNO'
    svc.registrar(operacao(), 'operador')
    svc.sincronizar('SKU-1')
    update.assert_called_with('SKU-1', 'EXTERNO;B')
    lookup.return_value = 'EXTERNO;B'  # endereço readicionado fora do módulo
    svc.registrar(operacao(origem='EXTERNO', destino='C'), 'operador')
    svc.sincronizar('SKU-1')
    update.assert_called_with('SKU-1', 'B;C')


def test_repeticao_de_requisicao_nao_duplica_e_rejeita_conteudo_diferente(state):
    data = operacao()
    first = svc.registrar(data, 'operador')
    assert svc.registrar(data, 'operador') == first
    data['quantidade'] = '3'
    with pytest.raises(ValueError, match='outra operação'):
        svc.registrar(data, 'operador')
    assert Movimento.query.count() == 1


@pytest.mark.parametrize('q', ['0', '-1', 'NaN', 'Infinity', 'abc', '0.0000001'])
def test_quantidade_invalida_nao_registra_movimentacao(state, q):
    with pytest.raises(ValueError):
        svc.registrar(operacao(quantidade=q), 'operador')
    assert Movimento.query.count() == 0


def test_mesmo_endereco_na_origem_e_destino_bloqueado(state):
    with pytest.raises(ValueError, match='diferentes'):
        svc.registrar(operacao(destino='A'), 'operador')
    assert Movimento.query.count() == 0


def test_falha_no_grv_preserva_operacao_e_retry_nao_duplica(state):
    _, lookup, update = state
    data = operacao()
    svc.registrar(data, 'operador')
    update.side_effect = TimeoutError('segredo de conexão')
    svc.sincronizar('SKU-1')
    assert Movimento.query.filter_by(sincronizado_em=None).count() == 1
    assert all('segredo' not in m.erro for m in Movimento.query.all())
    update.side_effect = None
    svc.sincronizar('SKU-1')
    assert Movimento.query.filter_by(sincronizado_em=None).count() == 0
    assert svc.registrar(data, 'operador')
    assert Movimento.query.count() == 1


def test_trava_impede_duas_movimentacoes_do_mesmo_sku(state):
    svc.registrar(operacao(), 'operador')
    reg = db.session.get(Trava, 'SKU-1')
    reg.token = 'outro'; reg.expira_em = datetime.now()+timedelta(minutes=5)
    db.session.commit()
    with pytest.raises(ValueError, match='sendo sincronizado'):
        svc.registrar(operacao(origem='B', destino='C'), 'operador')
    assert Movimento.query.count() == 1
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


def test_falha_ao_gravar_movimento_nao_deixa_rastro_nem_trava(state, monkeypatch):
    original = db.session.add
    def falhar(obj, *args, **kwargs):
        if isinstance(obj, Movimento):
            raise RuntimeError('interrompido antes de salvar')
        return original(obj, *args, **kwargs)
    monkeypatch.setattr(db.session, 'add', falhar)
    with pytest.raises(RuntimeError):
        svc.registrar(operacao(), 'operador')
    assert Movimento.query.count() == 0
    assert db.session.get(Trava, 'SKU-1').token is None


def test_retry_automatico_inclui_movimentacoes(app, state):
    _, _, update = state
    update.side_effect = TimeoutError('offline')
    svc.registrar(operacao(), 'operador')
    svc.sincronizar('SKU-1')
    assert not Movimento.query.one().sincronizado_em
    update.side_effect = None
    from conferencia_app.services.recebimento_enderecamento_scheduler import executar_ciclo
    executar_ciclo(app)
    assert Movimento.query.one().sincronizado_em


def test_fila_nao_mostra_sem_vinculo_como_pendente(app, state, monkeypatch):
    """Item sem SKU do GRV não tem como ser endereçado: sai da fila e da conta."""
    task = state[0]
    client = app.test_client()
    with client.session_transaction() as sess:
        sess['username'] = 'operador'; sess['role'] = 'Conferente'
    monkeypatch.setattr(erp_estoque_service, 'buscar_estoque_grv', lambda **kw: ESTOQUE_GRV)
    dados = client.get('/api/recebimento/enderecamento').get_json()
    assert dados['contadores']['Pendente'] == 1 and len(dados['itens']) == 1
    task.item.codigo_grv = ''
    db.session.commit()
    dados = client.get('/api/recebimento/enderecamento').get_json()
    assert dados['contadores']['Pendente'] == 0 and dados['itens'] == []
    # Volta sozinho quando o vínculo é corrigido no recebimento.
    task.item.codigo_grv = 'SKU-1'
    db.session.commit()
    assert client.get('/api/recebimento/enderecamento').get_json()['contadores']['Pendente'] == 1


def test_fila_nao_mostra_familia_que_nao_endereca(app, state, monkeypatch):
    client = app.test_client()
    with client.session_transaction() as sess:
        sess['username'] = 'operador'; sess['role'] = 'Conferente'
    estoque = {'por_codigo': {'SKU-1': {'item': 'Serviço de usinagem', 'unidade': 'UN',
                                        'familia': 'N - 09 - SERVIÇOS', 'localizacoes': []}}}
    monkeypatch.setattr(erp_estoque_service, 'buscar_estoque_grv', lambda **kw: estoque)
    dados = client.get('/api/recebimento/enderecamento').get_json()
    assert dados['contadores']['Pendente'] == 0 and dados['itens'] == []

    # A atualização automática da tela não espera a bridge: usa só o cache.
    monkeypatch.setattr(erp_estoque_service, 'estoque_grv_em_cache', lambda: None)
    auto = client.get('/api/recebimento/enderecamento?auto=1').get_json()
    assert auto['contadores']['Pendente'] == 1
    monkeypatch.setattr(erp_estoque_service, 'estoque_grv_em_cache', lambda: estoque)
    auto = client.get('/api/recebimento/enderecamento?auto=1').get_json()
    assert auto['contadores']['Pendente'] == 0

    # GRV fora do ar não pode esconder trabalho da fila.
    def explodir(**kw):
        raise RuntimeError('bridge fora do ar')
    monkeypatch.setattr(erp_estoque_service, 'buscar_estoque_grv', explodir)
    dados = client.get('/api/recebimento/enderecamento').get_json()
    assert dados['contadores']['Pendente'] == 1 and len(dados['itens']) == 1


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


def test_movimentacao_nao_sobrepoe_recebimento_aguardando_sync(state):
    task = state[0]
    receb.confirmar(task, payload(task))
    with pytest.raises(ValueError, match='recebimento deste SKU'):
        svc.registrar(operacao(), 'operador')
    assert Movimento.query.count() == 0


def test_lista_de_enderecos_vem_do_grv_e_falha_nao_derruba_a_tela(app, state, monkeypatch):
    client = app.test_client()
    assert client.get('/api/enderecamento/saldos').status_code in (302, 401, 403)
    with client.session_transaction() as sess:
        sess['username'] = 'operador'; sess['role'] = 'Conferente'
    monkeypatch.setattr(erp_estoque_service, 'buscar_estoque_grv', lambda **kw: ESTOQUE_GRV)
    dados = client.get('/api/enderecamento/saldos?filtro=com_endereco').get_json()
    # "A;B" no GRV é um material em dois endereços, não um endereço chamado "A;B".
    assert [(i['endereco'], i['sku']) for i in dados['itens']] == [
        ('A', 'SKU-1'), ('B', 'SKU-1'), ('B', 'SKU-2')]
    assert dados['itens'][0]['descricao'] == 'Material de teste'
    assert dados['metricas'] == {'materiais': 2, 'enderecos': 2, 'sem_endereco': 2, 'sincronizar': 0}
    assert client.get('/api/enderecamento/saldos?filtro=com_endereco&busca=B').get_json()['total'] == 2
    assert client.get('/api/enderecamento/saldos?busca=SKU-2').get_json()['total'] == 1  # SKU
    assert client.get('/api/enderecamento/saldos?busca=OUTRO').get_json()['total'] == 1  # descrição

    def explodir(**kw):
        raise RuntimeError('bridge fora do ar')
    monkeypatch.setattr(erp_estoque_service, 'buscar_estoque_grv', explodir)
    falha = client.get('/api/enderecamento/saldos')
    assert falha.status_code == 200 and falha.get_json()['indisponivel']
    assert 'GRV' in falha.get_json()['erro']


def test_api_do_material_mostra_onde_ele_esta_agora(app, state):
    """Alimenta a prévia do diálogo: onde está hoje, para o operador ver o depois."""
    client = app.test_client()
    with client.session_transaction() as sess:
        sess['username'] = 'operador'; sess['role'] = 'Conferente'
    assert client.get('/api/enderecamento/material?sku=SKU-1').get_json()['enderecos'] == ['A']
    assert client.get('/api/enderecamento/material').status_code == 409
    svc.registrar(operacao(), 'operador')  # pendente de envio ao GRV
    assert client.get('/api/enderecamento/material?sku=SKU-1').get_json()['enderecos'] == ['B']


def test_catalogo_lista_enderecos_conhecidos_e_a_ocupacao(app, state, monkeypatch):
    client = app.test_client()
    with client.session_transaction() as sess:
        sess['username'] = 'operador'; sess['role'] = 'Conferente'
    LocalizacaoArmazem.query.filter_by(codigo='C').update({'ativo': False})
    db.session.commit()
    monkeypatch.setattr(erp_estoque_service, 'buscar_estoque_grv', lambda **kw: ESTOQUE_GRV)
    itens = client.get('/api/enderecamento/locais').get_json()['itens']
    por_codigo = {i['codigo']: i for i in itens}
    # A e B estão no catálogo; C está no catálogo e vazio; nada some da lista.
    assert por_codigo['A']['materiais'] == 1 and por_codigo['A']['ativo']
    assert por_codigo['B']['materiais'] == 2
    assert por_codigo['C']['materiais'] == 0 and not por_codigo['C']['ativo']
    assert client.get('/api/enderecamento/locais?busca=C').get_json()['total'] == 1

    def explodir(**kw):
        raise RuntimeError('bridge fora do ar')
    monkeypatch.setattr(erp_estoque_service, 'buscar_estoque_grv', explodir)
    sem_grv = client.get('/api/enderecamento/locais').get_json()
    assert sem_grv['indisponivel'] and len(sem_grv['itens']) == 3


def test_catalogo_mostra_endereco_que_so_existe_no_grv(app, state, monkeypatch):
    client = app.test_client()
    with client.session_transaction() as sess:
        sess['username'] = 'operador'; sess['role'] = 'Conferente'
    monkeypatch.setattr(erp_estoque_service, 'buscar_estoque_grv', lambda **kw: {'por_codigo': {
        'SKU-9': {'item': 'Material antigo', 'unidade': 'UN', 'localizacoes': ['FORA-DO-SYNC']}}})
    itens = {i['codigo']: i for i in client.get('/api/enderecamento/locais').get_json()['itens']}
    assert itens['FORA-DO-SYNC']['materiais'] == 1
    assert itens['FORA-DO-SYNC']['fora_do_catalogo']


def test_filtros_de_sem_endereco_e_somente_com_saldo(app, state, monkeypatch):
    client = app.test_client()
    with client.session_transaction() as sess:
        sess['username'] = 'operador'; sess['role'] = 'Conferente'
    monkeypatch.setattr(erp_estoque_service, 'buscar_estoque_grv', lambda **kw: ESTOQUE_GRV)
    # Serviço (09) e uso e consumo sem estoque (41) não têm endereçamento: ficam fora.
    sem = client.get('/api/enderecamento/saldos?filtro=sem_endereco').get_json()
    assert [i['sku'] for i in sem['itens']] == ['SKU-3', 'SKU-4']
    assert all(i['endereco'] == '' for i in sem['itens'])
    com_saldo = client.get('/api/enderecamento/saldos?filtro=sem_endereco&saldo=1').get_json()
    assert [i['sku'] for i in com_saldo['itens']] == ['SKU-3']
    todos = client.get('/api/enderecamento/saldos').get_json()
    assert [i['sku'] for i in todos['itens']] == ['SKU-1', 'SKU-1', 'SKU-2', 'SKU-3', 'SKU-4']
    assert todos['itens'][0]['saldo'] == 10


def test_familias_sem_enderecamento_vem_da_configuracao(app, state, monkeypatch):
    from conferencia_app.services import enderecamento_service as modulo
    assert modulo.sem_enderecamento('N - 09 - SERVIÇOS')
    assert modulo.sem_enderecamento('N - 41 - USO E CONSUMO (S/ ESTOQUE)')
    assert not modulo.sem_enderecamento('N - 01 - MATERIA PRIMA')
    assert not modulo.sem_enderecamento('')
    app.config['ENDERECAMENTO_FAMILIAS_SEM_ENDERECO'] = ['09', '41', '7']
    assert modulo.sem_enderecamento('N - 07 - FERRAMENTAL')


def test_familia_03_so_sai_no_cruzamento_com_o_grupo(app, state):
    """Produto em processo só fica fora quando é produção por terceiros; o que
    é feito aqui dentro continua sendo endereçado."""
    from conferencia_app.services import enderecamento_service as modulo
    processo = 'N - 03 - PRODUTO EM PROCESSO'
    # Sem o código do grupo configurado, o cruzamento não casa com nada.
    assert app.config.get('ENDERECAMENTO_FAMILIA_GRUPO_SEM_ENDERECO', ()) == ()
    assert not modulo.sem_enderecamento(processo, '12')
    app.config['ENDERECAMENTO_FAMILIA_GRUPO_SEM_ENDERECO'] = [('03', '12')]
    assert modulo.sem_enderecamento(processo, '12')          # família e grupo
    assert not modulo.sem_enderecamento(processo, '99')       # outro grupo: endereça
    assert not modulo.sem_enderecamento(processo, '')         # sem grupo: endereça
    assert not modulo.sem_enderecamento('N - 01 - MATERIA PRIMA', '12')  # outra família


def test_desativar_endereco_tira_ele_dos_materiais(app, state, monkeypatch):
    """Desativar B: SKU-1 fica só em A; SKU-2 não tem outro endereço e é reportado."""
    _, lookup, update = state
    client = app.test_client()
    with client.session_transaction() as sess:
        sess['username'] = 'admin'; sess['role'] = 'Admin'
    monkeypatch.setattr(erp_estoque_service, 'buscar_estoque_grv', lambda **kw: ESTOQUE_GRV)
    monkeypatch.setattr(receb, 'buscar_localizacao_produto_grv',
                        lambda sku: {'SKU-1': 'A;B', 'SKU-2': 'B'}.get(sku, ''))
    r = client.post('/api/recebimento/enderecamento/locais', json={'codigo': 'B', 'ativo': False})
    assert r.status_code == 200
    relatorio = r.get_json()['relatorio']
    assert relatorio['limpos'] == ['SKU-1']
    assert relatorio['sem_outro_endereco'] == ['SKU-2']
    assert relatorio['falhas'] == []
    update.assert_called_once_with('SKU-1', 'A')
    assert not LocalizacaoArmazem.query.filter_by(codigo='B').one().ativo
    assert Movimento.query.filter_by(sku='SKU-1').one().tipo == 'Endereço desativado'
    assert Movimento.query.filter_by(sku='SKU-1').one().sincronizado_em


def test_desativar_endereco_e_somente_do_admin(app, state, monkeypatch):
    client = app.test_client()
    with client.session_transaction() as sess:
        sess['username'] = 'gestor'; sess['role'] = 'Supervisor Logística'
    monkeypatch.setattr(erp_estoque_service, 'buscar_estoque_grv', lambda **kw: ESTOQUE_GRV)
    r = client.post('/api/recebimento/enderecamento/locais', json={'codigo': 'B', 'ativo': False})
    assert r.status_code in (403, 302)
    assert LocalizacaoArmazem.query.filter_by(codigo='B').one().ativo
    assert Movimento.query.count() == 0


def test_api_movimenta_e_filtra_historico(app, state):
    client = app.test_client()
    with client.session_transaction() as sess:
        sess['username'] = 'operador'; sess['role'] = 'Conferente'
    r = client.post('/api/enderecamento/movimentar', json=operacao())
    assert r.status_code == 200 and r.get_json()['item']['sincronizado']
    assert client.get('/api/enderecamento/historico?pendentes=1').get_json()['total'] == 0
    assert client.get('/api/enderecamento/historico?busca=INEXISTENTE').get_json()['total'] == 0
    assert client.get('/api/enderecamento/historico?busca=B').get_json()['total'] == 1
    assert client.post('/api/enderecamento/movimentar', json=[]).status_code == 400


def test_lista_vazia_de_enderecos_nao_vira_sucesso_ficticio(state):
    """Guarda defensiva: o GRV não aceita limpar a última localização."""
    db.session.add(Movimento(chave='operacao:vazia', sku='SKU-1', unidade='UN',
        tipo='Movimentação', origem='A', destino='B', quantidade=Decimal('1'),
        usuario='operador', motivo='Registro sem endereço resultante',
        detalhes={'depois': []}))
    db.session.commit()
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
    svc.registrar(operacao(), 'operador')
    assert Movimento.query.count() == 1


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


def test_duas_sessoes_concorrentes_nao_registram_a_mesma_movimentacao(tmp_path, monkeypatch):
    from flask import Flask
    from threading import Thread, Event
    app = Flask('concorrencia')
    app.config.update(SQLALCHEMY_DATABASE_URI=f'sqlite:///{tmp_path/"concorrencia.db"}')
    db.init_app(app)
    with app.app_context():
        db.create_all()
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
                resultados.append(svc.registrar(operacao(), 'primeiro'))
            except Exception as exc:
                resultados.append(exc)
            finally:
                db.session.remove()
    worker = Thread(target=primeira)
    worker.start()
    try:
        assert iniciou.wait(10)
        with app.app_context(), pytest.raises(ValueError, match='sendo sincronizado'):
            svc.registrar(operacao(), 'segundo')
    finally:
        liberar.set(); worker.join(10)
    assert not worker.is_alive()
    assert len(resultados)==1 and isinstance(resultados[0], int), resultados
    with app.app_context():
        assert Movimento.query.count()==1
        assert Movimento.query.one().detalhes['depois']==['B']
        db.session.remove(); db.engine.dispose()
