"""Processamento por item: uma solicitação pode virar mais de uma nota.

Antes a NF, o status e o retorno ficavam no cabeçalho, então a solicitação
inteira andava junto e só podia ter uma nota. Agora cada item carrega os seus,
e o status do cabeçalho é derivado do que os itens mostram.
"""
from unittest.mock import patch

import pytest

from conferencia_app import create_app
from conferencia_app.extensions import db
from conferencia_app.models import SolicitacaoNF, SolicitacaoNFItem, TipoOperacaoNF
from conferencia_app.services import solicitacao_nf_service as svc

FUNCIONARIO = {"codigo": "F01", "nome": "ANA SOUZA", "setor": "AT"}
CLIENTE = {"codigo": "C1", "nome": "CLIENTE UM", "documento": "12345678000199"}
MATERIAL = {"codigo_interno": "M1", "nome": "BOMBA", "localizacao_estoque": "R1"}


@pytest.fixture
def app(tmp_path):
    app = create_app({"TESTING": True,
                      "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'itens.db'}"})
    with app.app_context():
        db.create_all()
        db.session.add(TipoOperacaoNF(nome="Garantia", requer_retorno_padrao=False, ativo=True))
        db.session.commit()
        yield app
        db.session.remove()


@pytest.fixture
def erp(app):
    with patch.object(svc, "_buscar_material_por_codigo", return_value=MATERIAL), \
         patch.object(svc, "_buscar_cliente_por_codigo", return_value=CLIENTE), \
         patch.object(svc, "_validar_solicitante", return_value=FUNCIONARIO), \
         patch.object(svc.teams_service, "notificar_solicitacao_nf", return_value=None), \
         patch.object(svc, "_dados_parceiro_nf", return_value=None):
        yield


def criar(app, tipo="Garantia", necessita_retorno=False, itens=2):
    dados = {"solicitante_nome": FUNCIONARIO["nome"], "tipo_operacao": tipo,
             "cliente_codigo": CLIENTE["codigo"], "cliente_nome": CLIENTE["nome"],
             "necessita_retorno": necessita_retorno,
             "itens": [{"material_codigo": "M1", "quantidade": i + 1} for i in range(itens)]}
    solicitacao = svc.criar_solicitacao(dados)
    svc.marcar_separada(solicitacao.id, "logistica",
                        [i.id for i in solicitacao.itens], None)
    return db.session.get(SolicitacaoNF, solicitacao.id)


def test_separacao_marca_o_status_de_cada_item(app, erp):
    with app.app_context():
        solicitacao = criar(app)
        assert {i.status for i in solicitacao.itens} == {svc.STATUS_EXPEDIDO_SEM_NF}
        assert solicitacao.status == svc.STATUS_EXPEDIDO_SEM_NF


def test_uma_solicitacao_vira_duas_notas(app, erp):
    """O caso que motivou a mudança: grupos de itens em notas diferentes."""
    with app.app_context():
        solicitacao = criar(app, itens=3)
        primeiro, *resto = solicitacao.itens

        svc.marcar_faturada(solicitacao.id, "fiscal", "NF-111", None, item_ids=[primeiro.id])
        solicitacao = db.session.get(SolicitacaoNF, solicitacao.id)
        # Parte faturada, parte não: o cabeçalho diz isso.
        assert solicitacao.status == svc.STATUS_PARCIAL
        assert db.session.get(SolicitacaoNFItem, primeiro.id).numero_nf == "NF-111"
        assert db.session.get(SolicitacaoNFItem, resto[0].id).numero_nf is None

        svc.marcar_faturada(solicitacao.id, "fiscal", "NF-222", None,
                            item_ids=[i.id for i in resto])
        solicitacao = db.session.get(SolicitacaoNF, solicitacao.id)
        assert solicitacao.status == svc.STATUS_NF_EMITIDA
        assert {i.numero_nf for i in solicitacao.itens} == {"NF-111", "NF-222"}
        # O cabeçalho lista as notas que saíram.
        assert "NF-111" in solicitacao.numero_nf and "NF-222" in solicitacao.numero_nf


def test_sem_itens_informados_fatura_o_que_falta(app, erp):
    with app.app_context():
        solicitacao = criar(app, itens=2)
        svc.marcar_faturada(solicitacao.id, "fiscal", "NF-UNICA", None)
        solicitacao = db.session.get(SolicitacaoNF, solicitacao.id)
        assert solicitacao.status == svc.STATUS_NF_EMITIDA
        assert {i.numero_nf for i in solicitacao.itens} == {"NF-UNICA"}


def test_faturar_de_novo_o_que_ja_tem_nota_e_recusado(app, erp):
    with app.app_context():
        solicitacao = criar(app, itens=1)
        svc.marcar_faturada(solicitacao.id, "fiscal", "NF-1", None)
        with pytest.raises(svc.SolicitacaoNFError, match="já foram faturados"):
            svc.marcar_faturada(solicitacao.id, "fiscal", "NF-2", None)


def test_quem_decide_o_destino_do_item_e_o_retorno_informado(app, erp):
    """O solicitante diz se volta; o tipo só decide em poder de quem fica."""
    with app.app_context():
        fica = criar(app, "Garantia", necessita_retorno=False, itens=1)
        svc.marcar_faturada(fica.id, "fiscal", "NF-A", None)
        assert db.session.get(SolicitacaoNF, fica.id).status == svc.STATUS_NF_EMITIDA

        # Mesma operação, mas o solicitante avisou que o material volta.
        volta = criar(app, "Garantia", necessita_retorno=True, itens=1)
        svc.marcar_faturada(volta.id, "fiscal", "NF-B", None)
        assert db.session.get(SolicitacaoNF, volta.id).status == svc.STATUS_ESTOQUE_TERCEIROS


def test_atendimento_tecnico_que_volta_fica_em_poder_da_assistencia(app, erp):
    with app.app_context():
        solicitacao = criar(app, "Materiais para atendimento técnico no cliente",
                            necessita_retorno=True, itens=1)
        svc.marcar_faturada(solicitacao.id, "fiscal", "NF-C", None)
        assert db.session.get(SolicitacaoNF,
                              solicitacao.id).status == svc.STATUS_ESTOQUE_ASSISTENCIA


def test_status_do_cabecalho_vem_dos_itens(app, erp):
    with app.app_context():
        solicitacao = criar(app, itens=2)
        assert svc._recalcular_status(solicitacao) == svc.STATUS_EXPEDIDO_SEM_NF
        solicitacao.itens[0].status = svc.STATUS_NF_EMITIDA
        assert svc._recalcular_status(solicitacao) == svc.STATUS_PARCIAL
        solicitacao.itens[1].status = svc.STATUS_NF_EMITIDA
        assert svc._recalcular_status(solicitacao) == svc.STATUS_NF_EMITIDA


def test_a_tela_recebe_os_campos_do_item(app, erp):
    with app.app_context():
        solicitacao = criar(app, itens=1)
        svc.marcar_faturada(solicitacao.id, "fiscal", "NF-9", None)
        item = svc._serializar(db.session.get(SolicitacaoNF, solicitacao.id))["itens"][0]
        assert item["numero_nf"] == "NF-9"
        assert item["tipo_operacao"] == "Garantia"
        assert item["status_slug"] == "nf_emitida"
        assert item["pode_faturar"] is False
