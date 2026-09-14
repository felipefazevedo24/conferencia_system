import io
from unittest.mock import Mock, patch

import pytest

from test_app import build_test_app, set_logged_user
from conferencia_app.extensions import db
from conferencia_app.models import ExpedicaoConferenciaSimples, ExpedicaoOrdemFat, ExpedicaoOrdemST


@pytest.mark.parametrize('origem', ['fat', 'st'])
@pytest.mark.parametrize('armazenamento', ['local', 'drive', 'drive_sem_cota'])
def test_salvar_fotos_material_e_cliente(tmp_path, origem, armazenamento):
    app = build_test_app(tmp_path)
    fotos_dir = tmp_path / 'fotos'
    app.config.update(EXPEDICAO_CONFERENCIA_FOTOS_DIR=str(fotos_dir),
                      EXPEDICAO_FOTOS_STORAGE='local' if armazenamento == 'local' else 'drive')
    # Drive não deve depender de uma pasta local gravável.
    if armazenamento == 'drive':
        fotos_dir.write_bytes(b'arquivo que impede criar pasta no mesmo caminho')
    client = app.test_client()
    set_logged_user(client, 'admin', 'Admin')
    model = ExpedicaoOrdemFat if origem == 'fat' else ExpedicaoOrdemST
    with app.app_context():
        ordem = model(**({'cod_ordem_fat': 123, 'orcamento': '123'} if origem == 'fat' else {'cod_ordem_compra': '123'}),
                      status='Faturado', numero_nf='456')
        db.session.add(ordem)
        db.session.commit()
        ordem_id = ordem.id
    base = '/api/expedicao/conf-cega' + ('-st' if origem == 'st' else '')

    def upload(foto, nome):
        data = foto.read()  # Simula o stream consumido pelo Drive antes de falhar.
        assert data in (b'foto-material', b'foto-cliente')
        if armazenamento == 'drive_sem_cota':
            raise RuntimeError('Service Accounts do not have storage quota')
        return Mock(file_path='https://drive.google.com/thumbnail?id=teste&sz=w1600')

    with patch(f'conferencia_app.routes.expedicao_{origem}_routes.upload_to_drive', side_effect=upload) as drive:
        resposta = client.post(base + '/ordens/123/fotos-preexpedicao', data={
            'fotos_material': (io.BytesIO(b'foto-material'), 'material.jpg'),
            'foto_cliente': (io.BytesIO(b'foto-cliente'), 'cliente.jpg'),
        })
    assert resposta.status_code == 200, resposta.get_data(as_text=True)
    assert resposta.get_json()['sucesso'] is True
    assert drive.call_count == (0 if armazenamento == 'local' else 2)
    with app.app_context():
        registro_id = db.session.get(model, ordem_id).expedicao_registro_id
        registro = db.session.get(ExpedicaoConferenciaSimples, registro_id)
        assert registro.foto_cliente_uploaded_by == 'admin'
    listado = client.get(base + '/ordens/123/fotos-preexpedicao').get_json()
    assert len(listado['fotos_material']) == 1
    assert listado['foto_cliente_url']
    if armazenamento != 'drive':
        assert client.get(listado['fotos_material'][0]['url']).data == b'foto-material'
        assert client.get(listado['foto_cliente_url']).data == b'foto-cliente'


@pytest.mark.parametrize('origem', ['fat', 'st'])
def test_falha_de_pasta_retornada_em_json_sem_gravar_rascunho(tmp_path, origem):
    app = build_test_app(tmp_path)
    destino = tmp_path / 'nao_e_pasta'
    destino.write_bytes(b'arquivo')
    app.config.update(EXPEDICAO_CONFERENCIA_FOTOS_DIR=str(destino), EXPEDICAO_FOTOS_STORAGE='local')
    client = app.test_client()
    set_logged_user(client, 'admin', 'Admin')
    model = ExpedicaoOrdemFat if origem == 'fat' else ExpedicaoOrdemST
    with app.app_context():
        ordem = model(**({'cod_ordem_fat': 123} if origem == 'fat' else {'cod_ordem_compra': '123'}), status='Faturado')
        db.session.add(ordem)
        db.session.commit()
        ordem_id = ordem.id
    base = '/api/expedicao/conf-cega' + ('-st' if origem == 'st' else '')
    resposta = client.post(base + '/ordens/123/fotos-preexpedicao', data={
        'fotos_material': (io.BytesIO(b'foto'), 'material.jpg'),
    })
    assert resposta.status_code == 502
    assert 'Falha ao salvar as fotos' in resposta.get_json()['error']
    with app.app_context():
        assert db.session.get(model, ordem_id).expedicao_registro_id is None
        assert ExpedicaoConferenciaSimples.query.count() == 0
