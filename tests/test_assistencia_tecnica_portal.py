"""Portal do solicitante: tipos vindos da tabela e item nascendo preenchido.

O formulário continua público, sem login — o solicitante é validado contra a
lista de funcionários ativos, como já era.
"""
from unittest.mock import patch

import pytest

from conferencia_app import create_app
from conferencia_app.extensions import db
from conferencia_app.models import SolicitacaoNF, SolicitacaoNFItem, TipoOperacaoNF
from conferencia_app.services import solicitacao_nf_service as svc

FUNCIONARIO = {"codigo": "F01", "nome": "ANA SOUZA", "setor": "Assistência Técnica"}
CLIENTE = {"codigo": "C1", "nome": "CLIENTE UM", "documento": "12345678000199"}
MATERIAL = {"codigo_interno": "M1", "nome": "BOMBA HIDRAULICA",
            "localizacao_estoque": "R1-PD1", "estoque_disponivel_uso": 5}


@pytest.fixture
def app(tmp_path):
    app = create_app({"TESTING": True,
                      "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'portal.db'}"})
    with app.app_context():
        db.create_all()
        # Os tipos ja' vem semeados no start da aplicacao (e' o proprio
        # svc.TIPOS_OPERACAO, ja' sem os dois removidos no redesenho de
        # 2026-09); aqui so' o texto de ajuda vira algo previsivel.
        for nome in svc.TIPOS_OPERACAO:
            TipoOperacaoNF.query.filter_by(nome=nome).update(
                {"descricao_ajuda": f"Ajuda de {nome}.", "ativo": True})
        db.session.commit()
        yield app
        db.session.remove()


@pytest.fixture
def erp(app):
    """Bridge do ERP sempre com patch: teste não pode depender de rede."""
    with patch.object(svc, "_buscar_material_por_codigo", return_value=MATERIAL), \
         patch.object(svc, "_buscar_cliente_por_codigo", return_value=CLIENTE), \
         patch.object(svc, "_validar_solicitante", return_value=FUNCIONARIO), \
         patch.object(svc.teams_service, "notificar_solicitacao_nf", return_value=None):
        yield


def payload(tipo="Garantia", **extra):
    dados = {"solicitante_nome": FUNCIONARIO["nome"], "tipo_operacao": tipo,
             "cliente_codigo": CLIENTE["codigo"], "cliente_nome": CLIENTE["nome"],
             "venda_posterior": False, "data_necessidade": "2026-09-24",
             "itens": [{"material_codigo": "M1", "quantidade": 2}]}
    dados.update(extra)
    return dados


def test_tipos_vem_da_tabela_e_o_inativo_nao_aparece(app):
    with app.app_context():
        TipoOperacaoNF.query.filter_by(nome="Garantia").update({"ativo": False})
        db.session.commit()
        nomes = [t["nome"] for t in svc.listar_tipos_operacao()]
    assert nomes == ["Bonificação", "Remessa para Teste",
                     "Materiais para atendimento técnico no cliente"]


def test_tabela_vazia_cai_nos_tipos_do_codigo(app):
    """A tela não pode ficar sem opção se a semente não rodou."""
    with app.app_context():
        TipoOperacaoNF.query.delete()
        db.session.commit()
        nomes = [t["nome"] for t in svc.listar_tipos_operacao()]
    assert set(nomes) == set(svc.TIPOS_OPERACAO)


def test_item_nasce_com_operacao_status_e_retorno_sugerido(app, erp):
    with app.app_context():
        solicitacao = svc.criar_solicitacao(payload("Remessa para Teste"))
        item = SolicitacaoNFItem.query.filter_by(solicitacao_id=solicitacao.id).one()
        assert item.tipo_operacao == "Remessa para Teste"
        assert item.necessita_retorno is True       # sugerido pelo tipo
        assert item.status == svc.STATUS_SOLICITADO
        assert item.sera_vendido is False
        assert item.quantidade_retornada == 0
        assert item.numero_nf is None


def test_tipo_sem_retorno_nao_sugere_retorno_no_item(app, erp):
    """Desde o redesenho de 2026-09, só Bonificação fica sem controle de
    retorno — Garantia passou a exigir (ver test_item_nasce_com_operacao...)."""
    with app.app_context():
        solicitacao = svc.criar_solicitacao(payload("Bonificação"))
        item = SolicitacaoNFItem.query.filter_by(solicitacao_id=solicitacao.id).one()
        assert item.necessita_retorno is False


def test_quem_decide_o_retorno_e_o_solicitante(app, erp):
    """O tipo sugere, mas quem pede é quem sabe se aquele material volta."""
    with app.app_context():
        # Bonificação sugere "não volta"; o solicitante diz que volta.
        volta = svc.criar_solicitacao(payload("Bonificação", necessita_retorno=True))
        assert SolicitacaoNFItem.query.filter_by(
            solicitacao_id=volta.id).one().necessita_retorno is True

        # Remessa para Teste sugere "volta"; o solicitante diz que não.
        fica = svc.criar_solicitacao(payload("Remessa para Teste", necessita_retorno=False))
        assert SolicitacaoNFItem.query.filter_by(
            solicitacao_id=fica.id).one().necessita_retorno is False


def test_sem_resposta_do_solicitante_vale_a_sugestao_do_tipo(app, erp):
    with app.app_context():
        solicitacao = svc.criar_solicitacao(payload("Remessa para Teste"))
        assert SolicitacaoNFItem.query.filter_by(
            solicitacao_id=solicitacao.id).one().necessita_retorno is True


def test_tipo_invalido_e_recusado(app, erp):
    with app.app_context():
        with pytest.raises(svc.SolicitacaoNFError, match="Tipo de operação"):
            svc.criar_solicitacao(payload("Inventado"))
        assert SolicitacaoNF.query.count() == 0


def test_tipos_removidos_no_redesenho_nao_sao_mais_aceitos(app, erp):
    """"Remessa para Conserto" e "Remessa de retorno de demonstração" saíram
    de circulação no redesenho de 2026-09 — solicitações antigas com esses
    tipos continuam no banco, mas não dá mais para criar uma nova assim."""
    with app.app_context():
        with pytest.raises(svc.SolicitacaoNFError, match="Tipo de operação"):
            svc.criar_solicitacao(payload("Remessa para Conserto"))
        assert SolicitacaoNF.query.count() == 0


def test_formulario_publico_responde_sem_login(app):
    """A operação saiu do topo: os tipos vão para o seletor de cada material."""
    resposta = app.test_client().get("/solicitacao-nf")
    corpo = resposta.get_data(as_text=True)
    assert resposta.status_code == 200
    assert 'id="dados-tipos"' in corpo
    assert '"Garantia"' in corpo
    assert "Ajuda de Garantia." in corpo
    # Nada de escolher operação/retorno/venda para a solicitação inteira.
    assert 'id="tipoSeg"' not in corpo
    assert 'id="vendaSeg"' not in corpo
    assert 'id="addOperacao"' in corpo


def test_operacao_e_venda_sao_de_cada_item(app, erp):
    """Dois materiais, operações e condições diferentes, na mesma solicitação."""
    with app.app_context():
        solicitacao = svc.criar_solicitacao({
            "solicitante_nome": FUNCIONARIO["nome"],
            "cliente_codigo": CLIENTE["codigo"], "cliente_nome": CLIENTE["nome"],
            "data_necessidade": "2026-09-24",
            "itens": [
                {"material_codigo": "M1", "quantidade": 2,
                 "tipo_operacao": "Remessa para Teste", "sera_vendido": True},
                {"material_codigo": "M1", "quantidade": 5,
                 "tipo_operacao": "Garantia", "sera_vendido": False},
            ],
        })
        itens = SolicitacaoNFItem.query.filter_by(
            solicitacao_id=solicitacao.id).order_by(SolicitacaoNFItem.linha).all()
        assert [i.tipo_operacao for i in itens] == ["Remessa para Teste", "Garantia"]
        assert [i.sera_vendido for i in itens] == [True, False]
        # O retorno vem sugerido pela operação de cada um. Desde o redesenho
        # de 2026-09, Garantia também exige retorno (só Bonificação não).
        assert [i.necessita_retorno for i in itens] == [True, True]
        # O cabeçalho guarda o que vale para o primeiro item.
        assert solicitacao.tipo_operacao == "Remessa para Teste"
        assert solicitacao.venda_posterior is True


def test_item_sem_operacao_vira_atendimento_tecnico(app, erp):
    """Desde o redesenho de 2026-09, o solicitante nem sempre sabe qual é a
    operação — em branco, cai no padrão em vez de travar o pedido."""
    with app.app_context():
        solicitacao = svc.criar_solicitacao({
            "solicitante_nome": FUNCIONARIO["nome"],
            "cliente_codigo": CLIENTE["codigo"], "cliente_nome": CLIENTE["nome"],
            "data_necessidade": "2026-09-24",
            "itens": [{"material_codigo": "M1", "quantidade": 1}],
        })
        item = SolicitacaoNFItem.query.filter_by(solicitacao_id=solicitacao.id).one()
        assert item.tipo_operacao == svc.TIPO_OPERACAO_PADRAO


FUNCIONARIOS = [
    {"codigo": "F01", "nome": "ANA SOUZA", "setor": "AT", "email": "ana@columbia.com"},
    {"codigo": "F02", "nome": "BRUNO LIMA", "setor": "AT", "email": ""},
]


@pytest.fixture
def contas(app):
    from conferencia_app.models import Usuario
    with app.app_context():
        db.session.add(Usuario(username="ANA", email="ana@columbia.com", role="Solicitante"))
        db.session.add(Usuario(username="BRUNO", email="sem-par@x.com", role="Solicitante"))
        db.session.commit()
    with patch.object(svc.FacilitiesGRVService, "listar_funcionarios", return_value=FUNCIONARIOS):
        yield


def test_consulta_das_proprias_solicitacoes_exige_login(app, contas):
    """Antes bastava escolher o código de outra pessoa para ver as dela."""
    resposta = app.test_client().get("/api/solicitacao-nf/minhas?codigo=F01")
    assert resposta.status_code == 401


def test_codigo_da_url_e_ignorado_em_favor_da_conta_logada(app, contas, erp):
    with app.app_context():
        svc.criar_solicitacao(payload())  # fica com o solicitante F01 (ANA)
    client = app.test_client()
    with client.session_transaction() as sessao:
        sessao["username"] = "ANA"; sessao["role"] = "Solicitante"
    # Pede as de outro funcionário: tem que devolver as da conta logada.
    dados = client.get("/api/solicitacao-nf/minhas?codigo=F02").get_json()
    assert dados["funcionario"] == "ANA SOUZA"
    assert len(dados["solicitacoes"]) == 1


def test_vinculo_sai_do_email_e_fica_gravado(app, contas):
    from conferencia_app.models import UsuarioFuncionario
    with app.app_context():
        assert svc.resolver_funcionario_do_usuario("ANA")["codigo"] == "F01"
        vinculo = UsuarioFuncionario.query.filter_by(username="ANA").one()
        assert vinculo.funcionario_codigo == "F01" and vinculo.origem == "email"


def test_sem_par_de_email_a_tela_pede_para_vincular(app, contas):
    client = app.test_client()
    with client.session_transaction() as sessao:
        sessao["username"] = "BRUNO"; sessao["role"] = "Solicitante"
    resposta = client.get("/api/solicitacao-nf/minhas")
    assert resposta.status_code == 409 and resposta.get_json()["precisa_vincular"]

    assert client.post("/api/solicitacao-nf/vincular", json={"codigo": "F02"}).status_code == 200
    assert client.get("/api/solicitacao-nf/minhas").get_json()["funcionario"] == "BRUNO LIMA"


def test_vinculo_para_funcionario_inexistente_e_recusado(app, contas):
    client = app.test_client()
    with client.session_transaction() as sessao:
        sessao["username"] = "BRUNO"; sessao["role"] = "Solicitante"
    resposta = client.post("/api/solicitacao-nf/vincular", json={"codigo": "NAO-EXISTE"})
    assert resposta.status_code == 400


def test_nenhum_item_novo_fica_sem_tipo(app, erp):
    """O --check da migração reprova item sem tipo; o fluxo novo não pode criar um."""
    with app.app_context():
        svc.criar_solicitacao(payload("Garantia"))
        svc.criar_solicitacao(payload("Remessa para Teste"))
        assert SolicitacaoNFItem.query.filter_by(tipo_operacao=None).count() == 0
