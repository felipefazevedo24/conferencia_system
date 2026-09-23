"""Observação do solicitante, anexos e unidade do material.

As três nasceram de buracos reais: quem abre o pedido não tinha onde explicar
o caso, o laudo da garantia ia por fora, e "100" de cabo não dizia se eram
metros ou peças.
"""
import base64
from unittest.mock import patch

import pytest

from conferencia_app import create_app
from conferencia_app.extensions import db
from conferencia_app.models import SolicitacaoNF, SolicitacaoNFAnexo, SolicitacaoNFItem
from conferencia_app.services import solicitacao_nf_service as svc

FUNCIONARIO = {"codigo": "F01", "nome": "ANA SOUZA", "setor": "AT"}
CLIENTE = {"codigo": "C1", "nome": "CLIENTE UM", "documento": "12345678000199"}
MATERIAL = {"codigo_interno": "CABO-10", "nome": "CABO 10MM",
            "unidade": "MT", "localizacao_estoque": "R1", "estoque_disponivel_uso": 500}
PDF = base64.b64encode(b"%PDF-1.4 laudo do defeito").decode()


@pytest.fixture
def app(tmp_path):
    app = create_app({"TESTING": True,
                      "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'extras.db'}"})
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


def pedido(**extra):
    dados = {"solicitante_nome": FUNCIONARIO["nome"],
             "cliente_codigo": CLIENTE["codigo"], "cliente_nome": CLIENTE["nome"],
             "data_necessidade": "2026-09-24",
             "itens": [{"material_codigo": "CABO-10", "quantidade": 100,
                        "tipo_operacao": "Garantia"}]}
    dados.update(extra)
    return dados


def test_observacao_do_solicitante_e_guardada(app, erp):
    with app.app_context():
        solicitacao = svc.criar_solicitacao(pedido(
            observacoes="Bomba vazou com 3 meses de uso."))
        assert solicitacao.observacoes == "Bomba vazou com 3 meses de uso."
        assert svc._serializar(solicitacao)["observacoes"] == "Bomba vazou com 3 meses de uso."


def test_unidade_do_material_acompanha_o_item(app, erp):
    """Sem ela, 100 de cabo não diz se são metros ou peças."""
    with app.app_context():
        solicitacao = svc.criar_solicitacao(pedido())
        item = SolicitacaoNFItem.query.filter_by(solicitacao_id=solicitacao.id).one()
        assert item.material_unidade == "MT"
        assert svc._serializar(solicitacao)["itens"][0]["material_unidade"] == "MT"


def test_bridge_sem_unidade_nao_quebra(app):
    """A bridge da VM só devolve a unidade depois de atualizada."""
    antigo = dict(MATERIAL)
    antigo.pop("unidade")
    with patch.object(svc, "_buscar_material_por_codigo", return_value=antigo), \
         patch.object(svc, "_buscar_cliente_por_codigo", return_value=CLIENTE), \
         patch.object(svc, "_validar_solicitante", return_value=FUNCIONARIO), \
         patch.object(svc.teams_service, "notificar_solicitacao_nf", return_value=None), \
         app.app_context():
        solicitacao = svc.criar_solicitacao(pedido())
        item = SolicitacaoNFItem.query.filter_by(solicitacao_id=solicitacao.id).one()
        assert item.material_unidade is None


def test_anexo_chega_junto_do_pedido(app, erp):
    with app.app_context():
        solicitacao = svc.criar_solicitacao(pedido(
            anexos=[{"nome": "laudo.pdf", "mimetype": "application/pdf", "conteudo": PDF}]))
        anexo = SolicitacaoNFAnexo.query.filter_by(solicitacao_id=solicitacao.id).one()
        assert anexo.nome_arquivo == "laudo.pdf"
        assert anexo.dados.startswith(b"%PDF")
        assert anexo.enviado_por == FUNCIONARIO["nome"]
        assert svc._serializar(solicitacao)["anexos"][0]["nome"] == "laudo.pdf"


def test_anexo_aceita_o_prefixo_do_navegador(app, erp):
    """O input de arquivo entrega 'data:application/pdf;base64,...'."""
    with app.app_context():
        solicitacao = svc.criar_solicitacao(pedido(
            anexos=[{"nome": "foto.png", "mimetype": "image/png",
                     "conteudo": f"data:image/png;base64,{PDF}"}]))
        assert SolicitacaoNFAnexo.query.filter_by(solicitacao_id=solicitacao.id).count() == 1


def test_anexo_de_tipo_proibido_e_recusado(app, erp):
    with app.app_context():
        with pytest.raises(svc.SolicitacaoNFError, match="PDF ou imagem"):
            svc.criar_solicitacao(pedido(
                anexos=[{"nome": "virus.exe", "mimetype": "application/x-msdownload",
                         "conteudo": PDF}]))
        assert SolicitacaoNF.query.count() == 0


def test_anexo_grande_demais_e_recusado(app, erp):
    grande = base64.b64encode(b"x" * (svc.ANEXO_TAMANHO_MAXIMO + 10)).decode()
    with app.app_context():
        with pytest.raises(svc.SolicitacaoNFError, match="MB"):
            svc.criar_solicitacao(pedido(
                anexos=[{"nome": "grande.pdf", "mimetype": "application/pdf",
                         "conteudo": grande}]))


def test_limite_de_anexos_por_solicitacao(app, erp):
    muitos = [{"nome": f"f{i}.pdf", "mimetype": "application/pdf", "conteudo": PDF}
              for i in range(svc.ANEXO_MAXIMO_POR_SOLICITACAO + 1)]
    with app.app_context():
        with pytest.raises(svc.SolicitacaoNFError, match="No máximo"):
            svc.criar_solicitacao(pedido(anexos=muitos))


def test_anexo_so_baixa_para_quem_opera_o_modulo(app, erp):
    with app.app_context():
        solicitacao = svc.criar_solicitacao(pedido(
            anexos=[{"nome": "laudo.pdf", "mimetype": "application/pdf", "conteudo": PDF}]))
        anexo_id = SolicitacaoNFAnexo.query.filter_by(solicitacao_id=solicitacao.id).one().id

    alvo = f"/api/expedicao/conf-cega-avulso/anexos/{anexo_id}"
    assert app.test_client().get(alvo).status_code in (302, 401, 403)

    client = app.test_client()
    with client.session_transaction() as sessao:
        sessao["username"] = "fiscal"; sessao["role"] = "Fiscal"
    resposta = client.get(alvo)
    assert resposta.status_code == 200
    assert resposta.data.startswith(b"%PDF")


def test_formulario_traz_os_campos_novos(app):
    corpo = app.test_client().get("/solicitacao-nf").get_data(as_text=True)
    assert 'id="observacoes"' in corpo
    assert 'id="anexos"' in corpo
