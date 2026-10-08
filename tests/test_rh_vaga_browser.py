"""RH - Requisicao de Vaga pela tela: cadastro do cargo, abertura, as tres
aprovacoes, publicacao e a candidatura pela pagina publica."""
from pathlib import Path
from threading import Thread
from unittest.mock import patch

import pytest
from werkzeug.serving import make_server

playwright_api = pytest.importorskip("playwright.sync_api")
sync_playwright, expect = playwright_api.sync_playwright, playwright_api.expect

from tests.test_app import build_test_app


def test_rh_vaga_fluxo_pela_tela(tmp_path):
    app = build_test_app(tmp_path)
    server = make_server("127.0.0.1", 0, app, threaded=True)
    Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}"
    curriculo = tmp_path / "curriculo.pdf"
    curriculo.write_bytes(b"%PDF-1.4\n%%EOF")
    try:
        with patch("conferencia_app.routes.rh_vaga_routes.enviar_mensagem_smtp"), sync_playwright() as playwright:
            if not Path(playwright.chromium.executable_path).exists():
                pytest.skip("Instale o Chromium do Playwright para executar este teste.")
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            erros_js, popups = [], []
            page.on("pageerror", lambda erro: erros_js.append(str(erro)))
            # A tela nao pode usar alert/confirm/prompt do navegador.
            page.on("dialog", lambda d: (popups.append(d.message), d.dismiss()))

            assert page.request.post(f"{base}/login", data={"username": "admin", "password": "admin1234"}).ok
            page.goto(f"{base}/rh/vagas")

            # Cadastro do cargo com perfil e faixa.
            page.get_by_role("button", name="Cargos e faixas salariais").click()
            page.get_by_role("button", name="Novo cargo").click()
            page.locator("#rh-c-nome").fill("Soldador")
            page.locator("#rh-c-perfil").fill("Solda MIG/MAG e leitura de desenho.")
            page.locator("#rh-c-min").fill("3.500,00")
            page.locator("#rh-c-max").fill("4.800,00")
            page.locator("#rh-c-salvar").click()
            expect(page.locator("#rh-cargos-tbody")).to_contain_text("Soldador")
            expect(page.locator("#rh-cargos-tbody")).to_contain_text("3.500,00")

            # Abertura: o perfil vem do cadastro e o substituido so' aparece em substituicao.
            page.get_by_role("button", name="Requisições", exact=True).click()
            page.get_by_role("button", name="Nova requisição").click()
            expect(page.locator("#rh-w-substituido")).to_be_hidden()
            page.locator("#rh-f-tipo").select_option("substituicao")
            expect(page.locator("#rh-w-substituido")).to_be_visible()
            page.locator("#rh-f-cargo").select_option(label="Soldador")
            expect(page.locator("#rh-f-perfil")).to_have_value("Solda MIG/MAG e leitura de desenho.")
            page.locator("#rh-f-substituido").fill("João da Silva")
            page.locator("#rh-f-departamento").fill("Manufatura")
            page.locator("#rh-f-justificativa").fill("Desligamento.")
            page.locator("#rh-form-salvar").click()
            expect(page.locator("#rh-tbody")).to_contain_text("Aguardando Diretoria")

            # Tres aprovacoes pelo dialogo da propria tela.
            page.locator("#rh-tbody [data-abrir]").first.click()
            for etapa, depois in (("Diretoria", "Aguardando Financeiro"), ("Financeiro", "Aguardando RH"), ("Gerência de RH", "Aprovada")):
                page.locator("#rh-d-acoes").get_by_role("button", name=f"Aprovar ({etapa})").click()
                expect(page.locator("#rh-dialogo")).to_be_visible()
                page.locator("#rh-dg-ok").click()
                expect(page.locator("#rh-d-titulo")).to_contain_text(depois)

            # Publicar sem a descricao nao passa do dialogo.
            page.locator("#rh-d-acoes").get_by_role("button", name="Publicar vaga").click()
            expect(page.locator("#rh-dg-c0")).to_have_value("Soldador")
            page.locator("#rh-dg-c1").fill("")
            page.locator("#rh-dg-ok").click()
            expect(page.locator("#rh-dg-erro")).to_contain_text("Descrição para divulgação")
            page.locator("#rh-dg-c1").fill("Venha soldar com a gente.")
            page.locator("#rh-dg-ok").click()
            expect(page.locator("#rh-d-titulo")).to_contain_text("Publicada")
            link = page.locator("#rh-d-link").input_value()
            assert "/vagas/" in link
            expect(page.locator('.rh-link img')).to_be_visible()
            # Espera o QR code carregar de fato (a imagem aparece antes de terminar de baixar).
            page.wait_for_function("() => { const i = document.querySelector('.rh-link img'); return i && i.complete && i.naturalWidth > 0; }")

            # Pagina publica, sem sessao.
            publico = browser.new_context().new_page()
            publico.on("pageerror", lambda erro: erros_js.append(str(erro)))
            publico.on("dialog", lambda d: (popups.append(d.message), d.dismiss()))
            publico.goto(link)
            expect(publico.locator("h1")).to_have_text("Soldador")
            expect(publico.locator(".descricao")).to_have_text("Venha soldar com a gente.")
            publico.locator("#enviar").click()
            expect(publico.locator("#msg")).to_contain_text("Preencha nome, e-mail e telefone")
            publico.locator("#nome").fill("Maria Souza")
            publico.locator("#email").fill("maria@exemplo.com")
            publico.locator("#telefone").fill("15 99999-0000")
            publico.locator("#curriculo").set_input_files(str(curriculo))
            publico.locator("#consentimento").check()
            publico.locator("#enviar").click()
            expect(publico.locator("#msg")).to_contain_text("Candidatura recebida")
            expect(publico.locator("#form")).to_be_hidden()

            # O RH ve o candidato na requisicao.
            page.locator("[data-fechar-detalhe]").click()
            page.reload()
            page.locator("#rh-tbody [data-abrir]").first.click()
            expect(page.locator("#rh-d-candidatos")).to_contain_text("Maria Souza")
            page.get_by_role("button", name="Registro de acessos aos currículos").click()
            expect(page.locator("#rh-d-acessos")).to_contain_text("Nenhum currículo foi baixado ainda")
            page.screenshot(path=str(tmp_path / "rh_detalhe.png"), full_page=True)

            browser.close()
            assert erros_js == []
            assert popups == []
    finally:
        server.shutdown()


def test_gestao_de_acessos_cria_cargo_e_muda_cargo_do_usuario(tmp_path):
    from conferencia_app.extensions import db
    from conferencia_app.models import Usuario

    app = build_test_app(tmp_path)
    with app.app_context():
        db.session.add(Usuario(username="FULANO", email="fulano@exemplo.com", role="Compras"))
        db.session.commit()
    server = make_server("127.0.0.1", 0, app, threaded=True)
    Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        with sync_playwright() as playwright:
            if not Path(playwright.chromium.executable_path).exists():
                pytest.skip("Instale o Chromium do Playwright para executar este teste.")
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            erros_js, popups = [], []
            page.on("pageerror", lambda erro: erros_js.append(str(erro)))
            page.on("dialog", lambda d: (popups.append(d.message), d.dismiss()))

            assert page.request.post(f"{base}/login", data={"username": "admin", "password": "admin1234"}).ok
            page.goto(f"{base}/admin/usuarios")
            page.locator('.ua-tab[data-tab="permissoes"]').click()

            # Cargo novo entra nos dois selects e ja' fica selecionado, sem permissao nenhuma.
            page.get_by_role("button", name="Novo cargo").click()
            page.locator("#novo-cargo-nome").fill("Gerente de Manufatura")
            page.get_by_role("button", name="Criar cargo").click()
            expect(page.locator("#perm-role")).to_have_value("Gerente de Manufatura")
            expect(page.locator("#perms-meta")).to_contain_text("Cargo: Gerente de Manufatura")
            assert page.locator("#reg-role option[value='Gerente de Manufatura']").count() == 1
            assert page.locator("#reg-role option[value='RH']").count() == 1
            assert page.locator("#perms-groups input[type=checkbox]:checked").count() == 0

            # Excluir so' aparece pra cargo criado aqui.
            expect(page.locator("#btn-excluir-cargo")).to_be_visible()
            page.locator("#perm-role").select_option("Compras")
            expect(page.locator("#btn-excluir-cargo")).to_be_hidden()

            # Mudar o cargo de um usuario pela gaveta.
            page.locator('.ua-tab[data-tab="usuarios"]').click()
            page.get_by_role("button", name="FULANO", exact=True).click()
            expect(page.locator("#ua-detail-cargo")).to_have_value("Compras")
            page.locator("#ua-detail-cargo").select_option("Gerente de Manufatura")
            page.get_by_role("button", name="Alterar cargo").click()
            expect(page.locator("#ua-drawer-sub")).to_contain_text("Gerente de Manufatura")

            browser.close()
            assert erros_js == []
            assert popups == []
        with app.app_context():
            assert Usuario.query.filter_by(username="FULANO").one().role == "Gerente de Manufatura"
    finally:
        server.shutdown()


def test_corrigir_requisicao_abre_so_o_formulario_e_o_fundo_cobre_a_tela(tmp_path):
    app = build_test_app(tmp_path)
    server = make_server("127.0.0.1", 0, app, threaded=True)
    Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        with patch("conferencia_app.routes.rh_vaga_routes.enviar_mensagem_smtp"), sync_playwright() as playwright:
            if not Path(playwright.chromium.executable_path).exists():
                pytest.skip("Instale o Chromium do Playwright para executar este teste.")
            browser = playwright.chromium.launch(headless=True)
            # Monitor largo: o conteudo do layout para em 1560px, o fundo do modal nao pode parar junto.
            page = browser.new_page(viewport={"width": 1906, "height": 905})
            erros_js = []
            page.on("pageerror", lambda erro: erros_js.append(str(erro)))

            assert page.request.post(f"{base}/login", data={"username": "admin", "password": "admin1234"}).ok
            criada = page.request.post(f"{base}/api/rh/vagas", data={
                "tipo": "nova", "cargo_nome": "Gerente", "perfil": "Teste", "departamento": "Suprimentos",
                "justificativa": "Área nova.",
            })
            rid = criada.json()["requisicao"]["id"]
            assert page.request.post(f"{base}/api/rh/vagas/{rid}/reprovar", data={"motivo": "Falta a faixa."}).ok

            page.goto(f"{base}/rh/vagas")
            page.locator("#rh-tbody [data-abrir]").first.click()
            fundo = page.locator("#rh-modal-detalhe").bounding_box()
            assert (fundo["x"], fundo["width"]) == (0, 1906)

            page.locator("#rh-d-acoes").get_by_role("button", name="Corrigir e reenviar").click()
            expect(page.locator("#rh-modal-detalhe")).to_be_hidden()
            expect(page.locator("#rh-modal-form")).to_be_visible()
            fundo = page.locator("#rh-modal-form").bounding_box()
            assert (fundo["x"], fundo["width"]) == (0, 1906)
            expect(page.locator("#rh-f-cargo-nome")).to_have_value("Gerente")

            page.locator("#rh-f-justificativa").fill("Área nova, com faixa revisada.")
            page.locator("#rh-form-salvar").click()
            expect(page.locator("#rh-modal-form")).to_be_hidden()
            expect(page.locator("#rh-tbody")).to_contain_text("Aguardando Diretoria")

            browser.close()
            assert erros_js == []
    finally:
        server.shutdown()
