"""Módulo Assistência Técnica: a tela saiu da Conferência de Expedição (cega).

A gestão das Solicitações de NF era uma aba dentro de /expedicao/conferencia-cega.
Virou módulo próprio, com permissão própria. As rotas de ação não mudaram, e com
elas os papéis: separação de Logística/Comex/Fiscal/Admin, faturamento e retorno
só de Fiscal/Admin.
"""
import pytest

from conferencia_app import create_app
from conferencia_app.auth import BASE_ROLE_PERMISSIONS, PERMISSION_CATALOG
from conferencia_app.extensions import db

PERMISSAO = "PAGE_ASSISTENCIA_TECNICA"


@pytest.fixture
def app(tmp_path):
    app = create_app({"TESTING": True,
                      "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'modulo.db'}"})
    with app.app_context():
        db.create_all()
    return app


def entrar(app, role):
    client = app.test_client()
    with client.session_transaction() as sessao:
        sessao["username"] = "usuario"
        sessao["role"] = role
    return client


def test_permissao_esta_no_catalogo_e_nos_cargos_certos():
    assert PERMISSAO in PERMISSION_CATALOG
    # Quem via a aba "Faturamento avulso" continua vendo o módulo.
    assert PERMISSAO in BASE_ROLE_PERMISSIONS["Fiscal"]
    assert PERMISSAO in BASE_ROLE_PERMISSIONS["Logística"]


@pytest.mark.parametrize("role", ["Fiscal", "Logística", "Admin"])
def test_quem_tem_a_permissao_abre_a_tela(app, role):
    resposta = entrar(app, role).get("/assistencia-tecnica")
    assert resposta.status_code == 200
    assert "Assistência Técnica" in resposta.get_data(as_text=True)


def test_quem_nao_tem_a_permissao_nao_abre(app):
    assert entrar(app, "Portaria").get("/assistencia-tecnica").status_code in (302, 401, 403)


def test_sem_login_nao_abre(app):
    assert app.test_client().get("/assistencia-tecnica").status_code in (302, 401, 403)


def test_link_do_menu_aparece_para_quem_tem_a_permissao(app):
    """Esquecer a permissão no {% if %} da seção esconderia o grupo inteiro."""
    corpo = entrar(app, "Fiscal").get("/assistencia-tecnica").get_data(as_text=True)
    assert 'href="/assistencia-tecnica"' in corpo


def test_a_aba_antiga_saiu_da_conferencia_cega(app):
    """Duas telas fazendo a mesma coisa confundiria quem opera."""
    corpo = entrar(app, "Fiscal").get("/expedicao/conferencia-cega").get_data(as_text=True)
    assert 'data-tab="avulso"' not in corpo
    assert "avulso-panel" not in corpo
    # As outras duas abas continuam lá.
    assert 'data-tab="st"' in corpo


def test_rotas_de_acao_mantem_os_papeis(app):
    """O Fiscal continua sendo quem emite a nota."""
    alvo = "/api/expedicao/conf-cega-avulso/ordens/1/faturar"
    assert entrar(app, "Logística").post(alvo, json={}).status_code in (302, 401, 403)
    # Fiscal passa da permissão (404/409 por não existir a solicitação, não 403).
    assert entrar(app, "Fiscal").post(alvo, json={}).status_code not in (302, 401, 403)
