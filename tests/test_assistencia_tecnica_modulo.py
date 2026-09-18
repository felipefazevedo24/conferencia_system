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


def test_modulo_nao_depende_da_permissao_da_conferencia_cega(app):
    """O módulo é independente: quem só tem a permissão dele tem que operar."""
    from conferencia_app.auth import BASE_ROLE_PERMISSIONS
    original = BASE_ROLE_PERMISSIONS.get("Qualidade")
    BASE_ROLE_PERMISSIONS["Qualidade"] = {PERMISSAO}
    try:
        client = entrar(app, "Qualidade")
        assert client.get("/assistencia-tecnica").status_code == 200
        assert client.get("/api/expedicao/conf-cega-avulso/ordens").status_code == 200
    finally:
        if original is None:
            BASE_ROLE_PERMISSIONS.pop("Qualidade", None)
        else:
            BASE_ROLE_PERMISSIONS["Qualidade"] = original


def test_menu_e_grupo_proprio_fora_de_logistica(app):
    """O módulo é independente: não é um item dentro de Logística."""
    corpo = entrar(app, "Fiscal").get("/assistencia-tecnica").get_data(as_text=True)
    assert 'id="menu-assistencia-tecnica"' in corpo
    assert 'href="/assistencia-tecnica"' in corpo

    # O link não pode estar dentro do painel de Logística.
    inicio = corpo.index('id="menu-logistica"')
    fim = corpo.index('id="menu-assistencia-tecnica"')
    assert 'href="/assistencia-tecnica"' not in corpo[inicio:fim]


def test_banco_antigo_se_conserta_no_boot(tmp_path):
    """Sem as colunas por item o painel devolvia "Falha ao carregar solicitações".

    O schema é aplicado no start da aplicação, e não só pelo script de
    migração, porque depender de alguém rodar o script na mão já quebrou."""
    import sqlite3
    caminho = tmp_path / "antigo.db"
    conn = sqlite3.connect(caminho)
    conn.executescript("""
        create table solicitacao_nf (
          id integer primary key, protocolo varchar(20), solicitante_nome varchar(160) not null,
          solicitante_codigo varchar(40), solicitante_setor varchar(120),
          tipo_operacao varchar(40) not null, venda_posterior boolean not null default 0,
          cliente_codigo varchar(40), cliente_nome varchar(160) not null, cliente_documento varchar(30),
          status varchar(60) not null default 'Solicitado',
          separado_por varchar(100), separado_at datetime, observacoes_separacao varchar(500),
          faturado_por varchar(100), faturado_at datetime, numero_nf varchar(80),
          observacoes_faturamento varchar(500), numero_nf_retorno varchar(80),
          retorno_por varchar(100), retorno_at datetime, observacoes_retorno varchar(500),
          nf_parceiro_nome varchar(200), nf_parceiro_endereco varchar(400),
          ordem_faturamento integer, ip_solicitante varchar(64),
          created_at datetime not null, updated_at datetime not null);
        create table solicitacao_nf_item (
          id integer primary key, solicitacao_id integer not null, linha integer not null default 0,
          material_codigo varchar(80), material_nome varchar(200), material_local varchar(160),
          quantidade float not null default 0, separado boolean not null default 0);
        create table solicitacao_nf_log (
          id integer primary key, solicitacao_id integer not null, acao varchar(30) not null,
          usuario varchar(100), status_anterior varchar(20), status_novo varchar(20),
          detalhes text, created_at datetime not null);
        insert into solicitacao_nf (id,protocolo,solicitante_nome,tipo_operacao,venda_posterior,
          cliente_nome,status,created_at,updated_at)
          values (1,'SNF-000001','ANA','Garantia',0,'CLIENTE','Solicitado','2026-09-18','2026-09-18');
        insert into solicitacao_nf_item values (1,1,0,'M1','BOMBA','R1',2,0);
    """)
    conn.commit()
    conn.close()

    app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": f"sqlite:///{caminho}"})
    client = app.test_client()
    with client.session_transaction() as sessao:
        sessao["username"] = "fiscal"; sessao["role"] = "Admin"
    resposta = client.get("/api/expedicao/conf-cega-avulso/ordens")
    assert resposta.status_code == 200, resposta.get_data(as_text=True)[:300]
    item = resposta.get_json()["ordens"][0]["itens"][0]
    assert item["tipo_operacao"] == "Garantia"   # histórico preenchido no boot
    # E os tipos ficam semeados, senão o formulário não mostra o texto de ajuda.
    assert "dentro do prazo de garantia" in client.get("/solicitacao-nf").get_data(as_text=True)


def test_rotas_de_acao_mantem_os_papeis(app):
    """O Fiscal continua sendo quem emite a nota."""
    alvo = "/api/expedicao/conf-cega-avulso/ordens/1/faturar"
    assert entrar(app, "Logística").post(alvo, json={}).status_code in (302, 401, 403)
    # Fiscal passa da permissão (404/409 por não existir a solicitação, não 403).
    assert entrar(app, "Fiscal").post(alvo, json={}).status_code not in (302, 401, 403)
