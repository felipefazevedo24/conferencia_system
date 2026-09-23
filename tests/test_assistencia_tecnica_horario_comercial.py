"""Redesenho da Assistência Técnica: tipo opcional, horário comercial de
Brasília decide se o material sai com ou sem romaneio de segurança, e só
Bonificação fica sem controle de retorno.
"""
from datetime import datetime
from unittest.mock import patch

import pytest

from conferencia_app import create_app
from conferencia_app.extensions import db
from conferencia_app.models import (
    ExpedicaoRomaneio,
    ExpedicaoRomaneioNF,
    SolicitacaoNF,
    TipoOperacaoNF,
)
from conferencia_app.services import solicitacao_nf_service as svc

FUNCIONARIO = {"codigo": "F01", "nome": "ANA SOUZA", "setor": "AT"}
CLIENTE = {"codigo": "C1", "nome": "CLIENTE UM", "documento": "12345678000199"}
MATERIAL = {"codigo_interno": "CABO-10", "nome": "CABO 10MM", "unidade": "MT",
            "localizacao_estoque": "R1", "estoque_disponivel_uso": 500}

DENTRO = datetime(2026, 9, 23, 10, 0)   # quarta-feira, 10h
FORA_NOITE = datetime(2026, 9, 23, 20, 0)  # quarta-feira, 20h
FIM_DE_SEMANA = datetime(2026, 9, 26, 10, 0)  # sábado, 10h


@pytest.fixture
def app(tmp_path):
    app = create_app({"TESTING": True,
                      "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'at_horario.db'}"})
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()


@pytest.fixture
def erp(app):
    with patch.object(svc, "_buscar_material_por_codigo", return_value=MATERIAL), \
         patch.object(svc, "_buscar_cliente_por_codigo", return_value=CLIENTE), \
         patch.object(svc, "_validar_solicitante", return_value=FUNCIONARIO), \
         patch.object(svc.teams_service, "notificar_solicitacao_nf", return_value=None):
        yield


def pedido(data_necessidade, tipo=None, **extra):
    item = {"material_codigo": "CABO-10", "quantidade": 10}
    if tipo:
        item["tipo_operacao"] = tipo
    dados = {"solicitante_nome": FUNCIONARIO["nome"], "cliente_codigo": CLIENTE["codigo"],
             "cliente_nome": CLIENTE["nome"], "data_necessidade": data_necessidade,
             "itens": [item]}
    dados.update(extra)
    return dados


# ── Horário comercial ───────────────────────────────────────────────────────

@pytest.mark.parametrize("momento,esperado", [
    (datetime(2026, 9, 23, 6, 0), True),   # abre as 6h em ponto
    (datetime(2026, 9, 23, 16, 59), True),
    (datetime(2026, 9, 23, 17, 0), False),  # fecha as 17h (nao inclui)
    (datetime(2026, 9, 23, 5, 59), False),
    (datetime(2026, 9, 26, 10, 0), False),  # sabado
    (datetime(2026, 9, 27, 10, 0), False),  # domingo
])
def test_esta_em_horario_comercial(momento, esperado):
    assert svc.esta_em_horario_comercial(momento) is esperado


# ── Tipo de operação opcional, com padrão ───────────────────────────────────

def test_tipo_em_branco_vira_atendimento_tecnico(app, erp):
    with app.app_context():
        solicitacao = svc.criar_solicitacao(pedido("2026-09-24"))
        assert solicitacao.itens[0].tipo_operacao == svc.TIPO_OPERACAO_PADRAO


def test_tipos_removidos_nao_sao_mais_aceitos(app, erp):
    with app.app_context():
        with pytest.raises(svc.SolicitacaoNFError, match="inválido"):
            svc.criar_solicitacao(pedido("2026-09-24", tipo="Remessa para Conserto"))


def test_data_necessidade_e_obrigatoria(app, erp):
    with app.app_context():
        with pytest.raises(svc.SolicitacaoNFError, match="necessário"):
            svc.criar_solicitacao(pedido(""))


# ── Romaneio automático fora do horário comercial ───────────────────────────

def test_dentro_do_horario_nao_gera_romaneio(app, erp):
    with app.app_context(), patch.object(svc, "agora_br", return_value=DENTRO):
        solicitacao = svc.criar_solicitacao(pedido(DENTRO.date().isoformat()))
        assert solicitacao.romaneio_id is None


def test_fora_do_horario_com_necessidade_futura_nao_gera_romaneio(app, erp):
    with app.app_context(), patch.object(svc, "agora_br", return_value=FORA_NOITE):
        solicitacao = svc.criar_solicitacao(pedido("2026-09-30"))
        assert solicitacao.romaneio_id is None


def test_fora_do_horario_com_necessidade_hoje_gera_romaneio_placeholder(app, erp):
    with app.app_context(), patch.object(svc, "agora_br", return_value=FORA_NOITE):
        solicitacao = svc.criar_solicitacao(pedido(FORA_NOITE.date().isoformat()))
        assert solicitacao.romaneio_id is not None

        romaneio = db.session.get(ExpedicaoRomaneio, solicitacao.romaneio_id)
        assert romaneio.origem_assistencia_tecnica is True
        assert romaneio.status == "Rascunho"
        assert solicitacao.protocolo in romaneio.observacao_1
        assert "CABO 10MM" in (romaneio.observacao_2 or "")

        linha = ExpedicaoRomaneioNF.query.filter_by(romaneio_id=romaneio.id).one()
        assert linha.numero_nf == solicitacao.protocolo


def test_fim_de_semana_com_necessidade_hoje_tambem_gera_romaneio(app, erp):
    with app.app_context(), patch.object(svc, "agora_br", return_value=FIM_DE_SEMANA):
        solicitacao = svc.criar_solicitacao(pedido(FIM_DE_SEMANA.date().isoformat()))
        assert solicitacao.romaneio_id is not None


def test_faturar_troca_o_placeholder_pela_nf_real(app, erp):
    with app.app_context(), patch.object(svc, "agora_br", return_value=FORA_NOITE):
        solicitacao = svc.criar_solicitacao(pedido(FORA_NOITE.date().isoformat()))
        svc.marcar_separada(solicitacao.id, "ana", [i.id for i in solicitacao.itens], None)
        with patch.object(svc, "_dados_parceiro_nf", return_value=None):
            svc.marcar_faturada(solicitacao.id, "fiscal", "NF-9999", None)

        linha = ExpedicaoRomaneioNF.query.filter_by(romaneio_id=solicitacao.romaneio_id).one()
        assert linha.numero_nf == "NF-9999"


# ── Estorno também desfaz/reverte o romaneio automático ─────────────────────

def test_estornar_a_separacao_desfaz_o_romaneio_automatico(app, erp):
    with app.app_context(), patch.object(svc, "agora_br", return_value=FORA_NOITE):
        solicitacao = svc.criar_solicitacao(pedido(FORA_NOITE.date().isoformat()))
        romaneio_id = solicitacao.romaneio_id
        svc.marcar_separada(solicitacao.id, "ana", [i.id for i in solicitacao.itens], None)

        svc.estornar_solicitacao(solicitacao.id, "admin", "engano na separação")

        atualizado = db.session.get(SolicitacaoNF, solicitacao.id)
        assert atualizado.romaneio_id is None
        assert atualizado.status == svc.STATUS_SOLICITADO
        assert db.session.get(ExpedicaoRomaneio, romaneio_id) is None


def test_estornar_o_faturamento_devolve_o_placeholder(app, erp):
    with app.app_context(), patch.object(svc, "agora_br", return_value=FORA_NOITE):
        solicitacao = svc.criar_solicitacao(pedido(FORA_NOITE.date().isoformat()))
        svc.marcar_separada(solicitacao.id, "ana", [i.id for i in solicitacao.itens], None)
        with patch.object(svc, "_dados_parceiro_nf", return_value=None):
            svc.marcar_faturada(solicitacao.id, "fiscal", "NF-1234", None)

        svc.estornar_solicitacao(solicitacao.id, "admin", "NF errada")

        linha = ExpedicaoRomaneioNF.query.filter_by(romaneio_id=solicitacao.romaneio_id).one()
        assert linha.numero_nf == solicitacao.protocolo


def test_nao_estorna_separacao_se_romaneio_ja_avancou(app, erp):
    with app.app_context(), patch.object(svc, "agora_br", return_value=FORA_NOITE):
        solicitacao = svc.criar_solicitacao(pedido(FORA_NOITE.date().isoformat()))
        svc.marcar_separada(solicitacao.id, "ana", [i.id for i in solicitacao.itens], None)
        romaneio = db.session.get(ExpedicaoRomaneio, solicitacao.romaneio_id)
        romaneio.status = "Pronto"
        db.session.commit()

        with pytest.raises(svc.SolicitacaoNFError, match="já avançou"):
            svc.estornar_solicitacao(solicitacao.id, "admin", "tentativa")

        # Nada foi desfeito.
        atualizado = db.session.get(SolicitacaoNF, solicitacao.id)
        assert atualizado.romaneio_id == romaneio.id
        assert atualizado.status != svc.STATUS_SOLICITADO


# ── Retorno: só Bonificação fica sem controle ───────────────────────────────

@pytest.mark.parametrize("tipo,espera_retorno", [
    ("Garantia", True),
    ("Bonificação", False),
    ("Remessa para Teste", True),
    ("Materiais para atendimento técnico no cliente", True),
])
def test_apenas_bonificacao_fica_sem_retorno(app, erp, tipo, espera_retorno):
    with app.app_context():
        item = svc._validar_itens([{"material_codigo": "CABO-10", "quantidade": 1,
                                    "tipo_operacao": tipo}])[0]
        assert item["necessita_retorno"] is espera_retorno


# ── Migração de dados: tipos antigos ficam inativos, Garantia passa a exigir retorno ──

def test_migracao_desativa_tipos_antigos_e_corrige_garantia(app):
    from conferencia_app import bootstrap
    with app.app_context():
        TipoOperacaoNF.query.delete()
        db.session.add_all([
            TipoOperacaoNF(nome="Garantia", ativo=True, requer_retorno_padrao=False, ordem_exibicao=1),
            TipoOperacaoNF(nome="Bonificação", ativo=True, requer_retorno_padrao=False, ordem_exibicao=2),
            TipoOperacaoNF(nome="Remessa para Conserto", ativo=True, requer_retorno_padrao=True, ordem_exibicao=5),
            TipoOperacaoNF(nome="Remessa de retorno de demonstração", ativo=True,
                           requer_retorno_padrao=False, ordem_exibicao=6),
        ])
        db.session.commit()

        bootstrap._ensure_tipo_operacao_nf_ajustes()

        garantia = TipoOperacaoNF.query.filter_by(nome="Garantia").one()
        conserto = TipoOperacaoNF.query.filter_by(nome="Remessa para Conserto").one()
        demo = TipoOperacaoNF.query.filter_by(nome="Remessa de retorno de demonstração").one()
        assert garantia.requer_retorno_padrao is True
        assert conserto.ativo is False
        assert demo.ativo is False

        # Idempotente: rodar de novo nao quebra nem reverte.
        bootstrap._ensure_tipo_operacao_nf_ajustes()
        assert TipoOperacaoNF.query.filter_by(nome="Garantia").one().requer_retorno_padrao is True


def test_formulario_publico_traz_o_campo_de_data(app):
    corpo = app.test_client().get("/solicitacao-nf").get_data(as_text=True)
    assert 'id="dataNecessidade"' in corpo


def test_rota_criar_devolve_aviso_e_link_do_romaneio(app, erp):
    with patch.object(svc, "agora_br", return_value=FORA_NOITE):
        resposta = app.test_client().post(
            "/api/solicitacao-nf",
            json=pedido(FORA_NOITE.date().isoformat()),
        )
    assert resposta.status_code == 200
    corpo = resposta.get_json()
    assert corpo["sucesso"] is True
    assert corpo["romaneio_id"]
    assert "romaneio_url" in corpo
    assert "próximo dia útil" in corpo["aviso"]

    # O link publico funciona sem login e mostra o romaneio certo.
    pagina = app.test_client().get(corpo["romaneio_url"])
    assert pagina.status_code == 200
    assert corpo["protocolo"] in pagina.get_data(as_text=True)


def test_link_do_romaneio_recusa_token_errado(app, erp):
    with patch.object(svc, "agora_br", return_value=FORA_NOITE):
        resposta = app.test_client().post(
            "/api/solicitacao-nf",
            json=pedido(FORA_NOITE.date().isoformat()),
        )
    romaneio_id = resposta.get_json()["romaneio_id"]
    pagina = app.test_client().get(f"/solicitacao-nf/romaneio/{romaneio_id}/token-errado")
    assert pagina.status_code == 404
