from conferencia_app.services.expedicao_assistente_service import _LLM_SYSTEM


def test_bia_specializes_in_documento_entrada_and_conferencia_cega_recebimento():
    texto = _LLM_SYSTEM.lower()

    assert "documento de entrada" in texto
    assert "conferência cega de recebimento" in texto or "conferencia cega de recebimento" in texto
    assert "especialista" in texto
