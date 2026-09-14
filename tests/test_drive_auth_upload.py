from unittest.mock import Mock, patch

import pytest
from flask import Flask
from google.auth.exceptions import RefreshError

from conferencia_app.services import expedicao_photo_storage as storage


@pytest.mark.parametrize('etapa', ['conexao', 'upload', 'compartilhamento'])
def test_autenticacao_drive_invalida_tem_mensagem_clara_sem_fallback_local(etapa):
    app = Flask(__name__)
    app.config['EXPEDICAO_GOOGLE_DRIVE_FOLDER_ID'] = 'pasta-teste'
    erro = RefreshError('invalid_grant: Invalid grant: account not found',
                        {'error': 'invalid_grant', 'error_description': 'Invalid grant: account not found'})
    service = Mock()
    service.files.return_value.create.return_value.execute.return_value = {'id': 'foto-teste'}
    if etapa == 'upload':
        service.files.return_value.create.return_value.execute.side_effect = erro
    elif etapa == 'compartilhamento':
        service.permissions.return_value.create.return_value.execute.side_effect = erro
    with app.app_context(), patch.object(storage, '_drive_service', return_value=service,
                                         side_effect=erro if etapa == 'conexao' else None):
        with pytest.raises(RuntimeError, match='reconectar uma conta válida') as recebido:
            storage.upload_bytes_to_drive(b'foto', 'foto.jpg', 'image/jpeg')
    assert recebido.value.__cause__ is erro
    from conferencia_app.routes.expedicao_fat_routes import _is_drive_quota_service_account_error as quota_fat
    from conferencia_app.routes.expedicao_st_routes import _is_drive_quota_service_account_error as quota_st
    assert not quota_fat(recebido.value)
    assert not quota_st(recebido.value)
