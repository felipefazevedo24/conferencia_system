from datetime import datetime

from conferencia_app import create_app
from conferencia_app.extensions import db
from conferencia_app.models import ItemNota, LogReversaoConferencia
from conferencia_app.services.expedicao_assistente_service import (
    _LLM_SYSTEM,
    _interpretar_acao_recebimento,
    responder,
)


def test_bia_specializes_in_documento_entrada_and_conferencia_cega_recebimento():
    texto = _LLM_SYSTEM.lower()

    assert "documento de entrada" in texto
    assert "conferência cega de recebimento" in texto or "conferencia cega de recebimento" in texto
    assert "especialista" in texto


def test_bia_can_answer_who_received_a_nf_in_past():
    app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})

    with app.app_context():
        db.create_all()
        db.session.add(
            ItemNota(
                numero_nota="12345",
                fornecedor="Fornecedor Delta",
                status="Lançado",
                usuario_importacao="maria",
                data_importacao=datetime(2026, 8, 10, 9, 15, 0),
                usuario_conferencia="joao",
                fim_conferencia=datetime(2026, 8, 10, 10, 0, 0),
                usuario_lancamento="ana",
                data_lancamento=datetime(2026, 8, 10, 11, 0, 0),
            )
        )
        db.session.add(
            LogReversaoConferencia(
                numero_nota="12345",
                usuario_reversao="admin",
                motivo="Reabertura por ajuste",
            )
        )
        db.session.commit()

        resposta = responder("quem recebeu a nota 12345 do fornecedor fornecedor delta no dia 10/08/2026?")
        texto = resposta["resposta"].lower()
        assert "maria" in texto
        assert "joao" in texto
        assert "ana" in texto
        assert "fornecedor delta" in texto


def test_bia_can_estornar_conferencia_of_a_nf_and_reopen_it():
    app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})

    with app.app_context():
        db.create_all()
        db.session.add(
            ItemNota(
                numero_nota="54321",
                fornecedor="Fornecedor Alfa",
                status="Concluído",
                usuario_importacao="maria",
                data_importacao=datetime(2026, 8, 12, 8, 0, 0),
                usuario_conferencia="joao",
                fim_conferencia=datetime(2026, 8, 12, 9, 30, 0),
            )
        )
        db.session.commit()

        resp = _interpretar_acao_recebimento("estorna a conferência da NF 54321 porque houve ajuste", {"username": "bia", "role": "Fiscal"})
        assert resp is not None
        item = ItemNota.query.filter_by(numero_nota="54321").first()
        assert item.status == "Pendente"
        assert item.usuario_conferencia is None
        assert item.fim_conferencia is None
        assert "estornada" in resp["resposta"].lower()


def test_bia_can_advance_without_conferencia_for_a_pending_nf():
    app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})

    with app.app_context():
        db.create_all()
        db.session.add(
            ItemNota(
                numero_nota="99999",
                fornecedor="Fornecedor Beta",
                status="Pendente",
                usuario_importacao="maria",
                data_importacao=datetime(2026, 8, 13, 7, 15, 0),
            )
        )
        db.session.commit()

        resp = _interpretar_acao_recebimento("avança a NF 99999 sem conferência por feriado", {"username": "bia", "role": "Logística"})
        assert resp is not None
        item = ItemNota.query.filter_by(numero_nota="99999").first()
        assert item.status == "Concluído"
        assert item.sem_conferencia_logistica is True
        assert "avançada" in resp["resposta"].lower()
