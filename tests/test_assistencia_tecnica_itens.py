"""Processamento por item: uma solicitação pode virar mais de uma nota.

Antes a NF, o status e o retorno ficavam no cabeçalho, então a solicitação
inteira andava junto e só podia ter uma nota. Agora cada item carrega os seus,
e o status do cabeçalho é derivado do que os itens mostram.
"""
from unittest.mock import patch

import pytest

from conferencia_app import create_app
from conferencia_app.extensions import db
from conferencia_app.models import SolicitacaoNF, SolicitacaoNFItem
from conferencia_app.services import solicitacao_nf_service as svc

FUNCIONARIO = {"codigo": "F01", "nome": "ANA SOUZA", "setor": "AT"}
CLIENTE = {"codigo": "C1", "nome": "CLIENTE UM", "documento": "12345678000199"}
MATERIAL = {"codigo_interno": "M1", "nome": "BOMBA", "localizacao_estoque": "R1"}


@pytest.fixture
def app(tmp_path):
    app = create_app({"TESTING": True,
                      "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'itens.db'}"})
    with app.app_context():
        db.create_all()  # os tipos ja' vem semeados no start da aplicacao
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


def test_alterar_a_operacao_de_um_item(app, erp):
    """A equipe corrige o tipo que o solicitante escolheu errado."""
    with app.app_context():
        solicitacao = criar(app, "Garantia", itens=2)
        alvo = solicitacao.itens[0]
        svc.alterar_item(solicitacao.id, alvo.id, "fiscal",
                         tipo_operacao="Remessa para Conserto")
        item = db.session.get(SolicitacaoNFItem, alvo.id)
        assert item.tipo_operacao == "Remessa para Conserto"
        # Conserto tem a nota antes do material: o item muda de etapa junto.
        assert item.status == svc.STATUS_AGUARDANDO_FAT
        assert item.necessita_retorno is True   # sugerido pelo tipo novo
        # O outro item não foi tocado.
        assert db.session.get(SolicitacaoNFItem,
                              solicitacao.itens[1].id).tipo_operacao == "Garantia"
        # E a solicitação mostra que os itens estão em etapas diferentes.
        assert db.session.get(SolicitacaoNF, solicitacao.id).status == svc.STATUS_PARCIAL


def test_nao_altera_item_que_ja_tem_nota(app, erp):
    with app.app_context():
        solicitacao = criar(app, itens=1)
        item = solicitacao.itens[0]
        svc.marcar_faturada(solicitacao.id, "fiscal", "NF-77", None)
        with pytest.raises(svc.SolicitacaoNFError, match="NF-77"):
            svc.alterar_item(solicitacao.id, item.id, "fiscal", tipo_operacao="Bonificação")


def test_operacoes_diferentes_viram_notas_diferentes(app, erp):
    """O fluxo completo: corrigir a operação de um item e faturar em dois grupos."""
    with app.app_context():
        solicitacao = criar(app, "Garantia", itens=2)
        garantia, conserto = solicitacao.itens
        svc.alterar_item(solicitacao.id, conserto.id, "fiscal",
                         tipo_operacao="Remessa para Conserto")

        svc.marcar_faturada(solicitacao.id, "fiscal", "NF-G1", None, item_ids=[garantia.id])
        svc.marcar_faturada(solicitacao.id, "fiscal", "NF-C1", None, item_ids=[conserto.id])

        assert db.session.get(SolicitacaoNFItem, garantia.id).numero_nf == "NF-G1"
        assert db.session.get(SolicitacaoNFItem, conserto.id).numero_nf == "NF-C1"
        # Garantia fecha; conserto volta, então fica aguardando retorno.
        assert db.session.get(SolicitacaoNFItem, garantia.id).status == svc.STATUS_NF_EMITIDA
        assert db.session.get(SolicitacaoNFItem,
                              conserto.id).status == svc.STATUS_ESTOQUE_TERCEIROS


def test_retorno_parcial_mantem_o_item_pendente(app, erp):
    with app.app_context():
        solicitacao = criar(app, "Remessa para Teste", necessita_retorno=True, itens=1)
        item = solicitacao.itens[0]
        svc.marcar_faturada(solicitacao.id, "fiscal", "NF-T", None)

        # Voltou metade (o item foi criado com quantidade 1).
        svc.registrar_retorno(solicitacao.id, "fiscal", "NF-R1", None, retornos={item.id: 0.4})
        item = db.session.get(SolicitacaoNFItem, item.id)
        assert item.quantidade_retornada == 0.4
        assert item.status == svc.STATUS_ESTOQUE_TERCEIROS   # ainda deve

        svc.registrar_retorno(solicitacao.id, "fiscal", "NF-R2", None, retornos={item.id: 0.6})
        item = db.session.get(SolicitacaoNFItem, item.id)
        assert item.status == svc.STATUS_ESTOQUE_RETORNADO
        assert item.data_efetiva_retorno is not None


def test_devolver_mais_do_que_saiu_e_recusado(app, erp):
    with app.app_context():
        solicitacao = criar(app, "Remessa para Teste", necessita_retorno=True, itens=1)
        item = solicitacao.itens[0]
        svc.marcar_faturada(solicitacao.id, "fiscal", "NF-T", None)
        with pytest.raises(svc.SolicitacaoNFError, match="passa da quantidade"):
            svc.registrar_retorno(solicitacao.id, "fiscal", "NF-R", None, retornos={item.id: 99})


def test_fiscal_informa_o_prazo_ao_faturar(app, erp):
    """O prazo de retorno é do Fiscal, junto com a nota, e só para quem volta."""
    from datetime import date, timedelta
    with app.app_context():
        solicitacao = criar(app, "Remessa para Teste", necessita_retorno=True, itens=1)
        prazo = (date.today() + timedelta(days=15)).isoformat()
        svc.marcar_faturada(solicitacao.id, "fiscal", "NF-P", None,
                            data_prevista_retorno=prazo)
        item = db.session.get(SolicitacaoNFItem, solicitacao.itens[0].id)
        assert item.data_prevista_retorno.isoformat() == prazo


def test_item_que_nao_volta_nao_ganha_prazo(app, erp):
    from datetime import date, timedelta
    with app.app_context():
        solicitacao = criar(app, "Garantia", necessita_retorno=False, itens=1)
        svc.marcar_faturada(solicitacao.id, "fiscal", "NF-X", None,
                            data_prevista_retorno=(date.today() + timedelta(days=9)).isoformat())
        assert db.session.get(SolicitacaoNFItem,
                              solicitacao.itens[0].id).data_prevista_retorno is None


def test_atraso_e_calculado_para_a_tela(app, erp):
    from datetime import date, timedelta
    with app.app_context():
        solicitacao = criar(app, "Remessa para Teste", necessita_retorno=True, itens=1)
        svc.marcar_faturada(solicitacao.id, "fiscal", "NF-A", None,
                            data_prevista_retorno=(date.today() - timedelta(days=3)).isoformat())
        item = svc._serializar(db.session.get(SolicitacaoNF, solicitacao.id))["itens"][0]
        assert item["dias_de_atraso"] == 3 and item["aguardando_retorno"] is True

        # Depois que volta, deixa de ser atraso.
        svc.registrar_retorno(solicitacao.id, "fiscal", "NF-R", None)
        item = svc._serializar(db.session.get(SolicitacaoNF, solicitacao.id))["itens"][0]
        assert item["dias_de_atraso"] == 0 and item["aguardando_retorno"] is False


def test_prazo_no_dia_ainda_nao_e_atraso(app, erp):
    from datetime import date
    with app.app_context():
        solicitacao = criar(app, "Remessa para Teste", necessita_retorno=True, itens=1)
        svc.marcar_faturada(solicitacao.id, "fiscal", "NF-H", None,
                            data_prevista_retorno=date.today().isoformat())
        item = svc._serializar(db.session.get(SolicitacaoNF, solicitacao.id))["itens"][0]
        assert item["dias_de_atraso"] == 0


def test_of_nao_fatura_sozinha_quando_ha_operacoes_diferentes(app, erp):
    """Uma OF é uma nota só; tipos diferentes não cabem nela."""
    from conferencia_app.models import ExpedicaoOrdemFat
    with app.app_context():
        solicitacao = criar(app, "Garantia", itens=2)
        svc.alterar_item(solicitacao.id, solicitacao.itens[1].id, "fiscal",
                         tipo_operacao="Remessa para Conserto")
        db.session.add(ExpedicaoOrdemFat(cod_ordem_fat=4242, numero_nf="NF-OF", excluido=False))
        solicitacao.ordem_faturamento = 4242
        db.session.commit()

        svc._reconciliar_of_vinculadas()
        # Nenhum item pode ter sido faturado automaticamente.
        assert all(i.numero_nf is None
                   for i in db.session.get(SolicitacaoNF, solicitacao.id).itens)

        # Com uma operação só, aí sim avança sozinha.
        svc.alterar_item(solicitacao.id, solicitacao.itens[1].id, "fiscal",
                         tipo_operacao="Garantia")
        svc._reconciliar_of_vinculadas()
        assert all(i.numero_nf == "NF-OF"
                   for i in db.session.get(SolicitacaoNF, solicitacao.id).itens)


def test_voltou_menos_e_esta_certo_assim(app, erp):
    """Saiu 100 de cabo, voltou 50, e acabou: nao e pendencia."""
    with app.app_context():
        solicitacao = criar(app, "Remessa para Teste", necessita_retorno=True, itens=1)
        item = solicitacao.itens[0]
        item.quantidade = 100
        db.session.commit()
        svc.marcar_faturada(solicitacao.id, "fiscal", "NF-C", None)

        svc.registrar_retorno(solicitacao.id, "fiscal", "NF-R", "Resto consumido no cliente",
                              retornos={item.id: 50}, encerrar=[item.id])
        item = db.session.get(SolicitacaoNFItem, item.id)
        assert item.status == svc.STATUS_ESTOQUE_RETORNADO
        assert item.quantidade_retornada == 50      # o que voltou de verdade
        assert item.data_efetiva_retorno is not None
        assert db.session.get(SolicitacaoNF, solicitacao.id).status == svc.STATUS_ESTOQUE_RETORNADO


def test_a_diferenca_fica_no_historico(app, erp):
    """Garantia precisa conseguir rastrear o que nao voltou."""
    import json
    from conferencia_app.models import SolicitacaoNFLog
    with app.app_context():
        solicitacao = criar(app, "Remessa para Teste", necessita_retorno=True, itens=1)
        item = solicitacao.itens[0]
        item.quantidade = 100
        db.session.commit()
        svc.marcar_faturada(solicitacao.id, "fiscal", "NF-C", None)
        svc.registrar_retorno(solicitacao.id, "fiscal", "NF-R", None,
                              retornos={item.id: 40}, encerrar=[item.id])

        log = (SolicitacaoNFLog.query.filter_by(solicitacao_id=solicitacao.id, acao="retorno")
               .order_by(SolicitacaoNFLog.id.desc()).first())
        diferenca = json.loads(log.detalhes)["encerrados_com_diferenca"][0]
        assert diferenca["enviado"] == 100 and diferenca["devolvido"] == 40


def test_sem_encerrar_o_que_falta_continua_pendente(app, erp):
    with app.app_context():
        solicitacao = criar(app, "Remessa para Teste", necessita_retorno=True, itens=1)
        item = solicitacao.itens[0]
        item.quantidade = 100
        db.session.commit()
        svc.marcar_faturada(solicitacao.id, "fiscal", "NF-C", None)
        svc.registrar_retorno(solicitacao.id, "fiscal", "NF-R", None, retornos={item.id: 50})
        assert db.session.get(SolicitacaoNFItem, item.id).status == svc.STATUS_ESTOQUE_TERCEIROS


def test_encerrar_sem_nada_voltar_tambem_vale(app, erp):
    """Nada voltou e nao vai voltar: fecha com zero devolvido."""
    with app.app_context():
        solicitacao = criar(app, "Remessa para Teste", necessita_retorno=True, itens=1)
        item = solicitacao.itens[0]
        svc.marcar_faturada(solicitacao.id, "fiscal", "NF-C", None)
        svc.registrar_retorno(solicitacao.id, "fiscal", "NF-R", "Consumido no atendimento",
                              retornos={}, encerrar=[item.id])
        item = db.session.get(SolicitacaoNFItem, item.id)
        assert item.status == svc.STATUS_ESTOQUE_RETORNADO
        assert item.quantidade_retornada == 0


def test_encerrar_um_item_nao_encerra_o_outro(app, erp):
    with app.app_context():
        solicitacao = criar(app, "Remessa para Teste", necessita_retorno=True, itens=2)
        primeiro, segundo = solicitacao.itens
        svc.marcar_faturada(solicitacao.id, "fiscal", "NF-C", None)
        svc.registrar_retorno(solicitacao.id, "fiscal", "NF-R", None,
                              retornos={primeiro.id: 0.5}, encerrar=[primeiro.id])
        assert db.session.get(SolicitacaoNFItem, primeiro.id).status == svc.STATUS_ESTOQUE_RETORNADO
        assert db.session.get(SolicitacaoNFItem, segundo.id).status == svc.STATUS_ESTOQUE_TERCEIROS
