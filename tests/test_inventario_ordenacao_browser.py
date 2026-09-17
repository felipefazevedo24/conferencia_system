"""Ordenacao por clique no cabecalho das listagens do Inventario, testada no
navegador de verdade (a logica e' toda JS - static/js/tabela_ordenavel.js)."""
from datetime import datetime
from pathlib import Path
from threading import Thread
from unittest.mock import patch

import pytest
from werkzeug.serving import make_server

playwright_api = pytest.importorskip("playwright.sync_api")
sync_playwright, expect = playwright_api.sync_playwright, playwright_api.expect

from conferencia_app.extensions import db
from conferencia_app.models import LogisticaInventarioAjuste
from tests.test_app import build_test_app

# Indices das colunas da tabela de Ajuste de Estoque.
COL_CHECKBOX, COL_DETECTADO, COL_CODIGO = 0, 1, 3
COL_DIFERENCA, COL_DIF_REAIS, COL_ACAO = 6, 8, 10


def _coluna(page, indice):
    return page.locator("#ia-tbody tr").evaluate_all(
        f"(trs) => trs.map((tr) => tr.cells[{indice}].textContent.replace(/\\s+/g, ' ').trim())"
    )


def _num(texto):
    """'+R$ 37,50' / '-3' / 'R$ -1.250,00' -> float (espelha o parser do JS)."""
    limpo = texto.replace("R$", "").replace(" ", "").replace(".", "").replace(",", ".")
    return float(limpo)


def test_inventario_ordena_colunas_clicando_no_cabecalho(tmp_path):
    app = build_test_app(tmp_path)
    with app.app_context():
        # Datas escolhidas pra ordem de TEXTO ("01/09" < "15/07") ser diferente
        # da ordem cronologica - se ordenasse como string, o teste pega.
        for codigo, criado_em, diferenca, custo in [
            ("SKU-B", datetime(2026, 9, 1, 8, 0), -3.0, 12.5),
            ("SKU-A", datetime(2026, 7, 15, 10, 0), 10.0, None),   # sem custo -> "—"
            ("SKU-D", datetime(2026, 9, 16, 17, 9), -150.0, 2.0),
            ("SKU-C", datetime(2026, 8, 20, 9, 30), 0.5, 100.0),
        ]:
            db.session.add(LogisticaInventarioAjuste(
                codigo_produto=codigo, local_codigo="A01", unidade_medida="UN",
                qtde_contada=10 + diferenca, qtde_estoque_no_momento=10, diferenca=diferenca,
                custo_medio=custo, status_modulo="Validacao", status_slug="validacao",
                criado_em=criado_em,
            ))
        db.session.commit()

    server = make_server("127.0.0.1", 0, app, threaded=True)
    Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        # A aba Realizados tambem carrega ao abrir a pagina e tenta falar com o
        # GRV - sem ERP no teste, devolve vazio na hora.
        with patch("conferencia_app.services.erp_estoque_service.buscar_estoque_grv", return_value={}), \
                sync_playwright() as playwright:
            if not Path(playwright.chromium.executable_path).exists():
                pytest.skip("Instale o Chromium do Playwright para executar este teste.")
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            erros_js = []
            page.on("pageerror", lambda erro: erros_js.append(str(erro)))

            assert page.request.post(f"{base}/login", data={"username": "admin", "password": "admin1234"}).ok
            page.goto(f"{base}/logistica/inventario?aba=ajustes")
            expect(page.locator("#ia-tbody tr")).to_have_count(4)

            cabecalhos = page.locator("table:has(#ia-tbody) thead th")

            # Checkbox e Acao nao ordenam; as colunas de dado, sim.
            assert "to-ordenavel" not in (cabecalhos.nth(COL_CHECKBOX).get_attribute("class") or "")
            assert "to-ordenavel" not in (cabecalhos.nth(COL_ACAO).get_attribute("class") or "")
            assert "to-ordenavel" in cabecalhos.nth(COL_DIFERENCA).get_attribute("class")

            # Diferenca: numerica (nao alfabetica: "-150" < "-3" < "+0,5" < "+10").
            cabecalhos.nth(COL_DIFERENCA).click()
            valores = [_num(v) for v in _coluna(page, COL_DIFERENCA)]
            assert valores == sorted(valores) == [-150.0, -3.0, 0.5, 10.0]
            expect(cabecalhos.nth(COL_DIFERENCA)).to_have_attribute("aria-sort", "ascending")

            # Segundo clique inverte.
            cabecalhos.nth(COL_DIFERENCA).click()
            assert [_num(v) for v in _coluna(page, COL_DIFERENCA)] == [10.0, 0.5, -3.0, -150.0]
            expect(cabecalhos.nth(COL_DIFERENCA)).to_have_attribute("aria-sort", "descending")

            # Diferenca R$: item sem custo ("—") fica no FIM nos dois sentidos.
            cabecalhos.nth(COL_DIF_REAIS).click()
            reais = _coluna(page, COL_DIF_REAIS)
            assert reais[-1] == "—"
            assert [_num(v) for v in reais[:-1]] == sorted(_num(v) for v in reais[:-1])
            cabecalhos.nth(COL_DIF_REAIS).click()
            reais = _coluna(page, COL_DIF_REAIS)
            assert reais[-1] == "—"
            assert [_num(v) for v in reais[:-1]] == sorted((_num(v) for v in reais[:-1]), reverse=True)
            # So' a coluna ativa fica marcada.
            expect(cabecalhos.nth(COL_DIFERENCA)).to_have_attribute("aria-sort", "none")

            # Detectado em: cronologica, nao por texto.
            cabecalhos.nth(COL_DETECTADO).click()
            assert _coluna(page, COL_CODIGO) == ["SKU-A", "SKU-C", "SKU-B", "SKU-D"]

            # A ordem sobrevive quando a lista recarrega ("Atualizar" refaz o <tbody>).
            page.locator("#ia-btn-atualizar").click()
            page.wait_for_timeout(800)
            expect(page.locator("#ia-tbody tr")).to_have_count(4)
            assert _coluna(page, COL_CODIGO) == ["SKU-A", "SKU-C", "SKU-B", "SKU-D"]

            # Teclado tambem ordena (acessibilidade).
            cabecalhos.nth(COL_CODIGO).focus()
            page.keyboard.press("Enter")
            assert _coluna(page, COL_CODIGO) == ["SKU-A", "SKU-B", "SKU-C", "SKU-D"]

            # As outras duas abas tambem ficaram ordenaveis.
            for tbody_id in ("tb-consulta", "ac-tbody"):
                ordenaveis = page.locator(f"table:has(#{tbody_id}) thead th.to-ordenavel").count()
                assert ordenaveis > 0, tbody_id

            assert erros_js == []
            browser.close()
    finally:
        server.shutdown()
