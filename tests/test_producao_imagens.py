import importlib
import sys
import threading
import time
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _load_production_service():
    package_paths = {
        "production_test_app": PROJECT_ROOT / "conferencia_app",
        "production_test_app.compras": PROJECT_ROOT / "conferencia_app" / "compras",
        "production_test_app.services": PROJECT_ROOT / "conferencia_app" / "services",
    }
    for package_name, package_path in package_paths.items():
        package = ModuleType(package_name)
        package.__path__ = [str(package_path)]
        sys.modules[package_name] = package
    database = ModuleType("production_test_app.compras.db")
    database.fetch_all = lambda *_args, **_kwargs: []
    database.fetch_one = lambda *_args, **_kwargs: None
    sys.modules[database.__name__] = database
    return importlib.import_module("production_test_app.services.producao_service")


producao_service = _load_production_service()


def document(document_id, kind, filename, description=""):
    return {
        "id": document_id,
        "kind": kind,
        "filename": filename,
        "description": description,
        "size_bytes": 100,
    }


def test_cms_usa_anexo_da_peca_e_revisao_corretos():
    context = {
        "segmento": " cms ",
        "cod_os_completo": "7807/013",
        "n_desenho": "20-04-00920",
        "revisao_desenho": "02",
        "posicao_desenho": "A1",
    }
    documents = [
        document(1, "drawing", "20-04-00920_REV02.pdf"),
        document(2, "attachment", "pedido_compra.pdf", "Documento administrativo"),
        document(3, "attachment", "20-04-00920_REV01.pdf"),
        document(4, "attachment", "20-04-00920_REV02.pdf"),
    ]

    selected = producao_service._selecionar_documento_previa(documents, context)

    assert selected["id"] == 4
    assert selected["kind"] == "attachment"


@pytest.mark.parametrize("segment", ["MOLDE", "Outros"])
def test_segmentos_nao_cms_usam_desenho(segment):
    context = {"segmento": segment, "n_desenho": "ABC-123", "revisao_desenho": "B"}
    documents = [
        document(1, "attachment", "ABC-123_REVB.pdf"),
        document(2, "drawing", "ABC-123_REVA.pdf"),
        document(3, "drawing", "ABC-123_REVB.pdf"),
    ]

    selected = producao_service._selecionar_documento_previa(documents, context)

    assert selected["id"] == 3
    assert selected["kind"] == "drawing"


def test_cms_nao_faz_fallback_nem_escolhe_anexo_ambiguo():
    context = {"segmento": "CMS", "n_desenho": "DES-99", "revisao_desenho": ""}
    documents = [
        document(1, "drawing", "DES-99.pdf"),
        document(2, "attachment", "contrato.pdf"),
        document(3, "attachment", "pedido.pdf"),
    ]

    assert producao_service._selecionar_documento_previa(documents, context) is None


def test_revisao_conflitante_nao_e_associada_mesmo_com_um_documento():
    context = {"segmento": "CMS", "n_desenho": "DES-99", "revisao_desenho": "03"}
    documents = [document(1, "attachment", "DES-99_REV02.pdf")]

    assert producao_service._selecionar_documento_previa(documents, context) is None


@pytest.mark.skipif(producao_service.fitz is None, reason="PyMuPDF nao instalado")
def test_pdf_localiza_vista_isometrica_fora_da_primeira_pagina():
    fitz = producao_service.fitz
    pdf = fitz.open()
    first = pdf.new_page(width=595, height=842)
    first.draw_rect(fitz.Rect(40, 40, 555, 802))
    for offset in range(8):
        first.draw_line((350, 650 + offset * 10), (540, 650 + offset * 10))
    second = pdf.new_page(width=595, height=842)
    shape = second.new_shape()
    points = [(160, 330), (280, 260), (410, 330), (290, 410), (160, 330), (160, 470), (290, 550), (410, 470), (410, 330)]
    for start, end in zip(points, points[1:]):
        shape.draw_line(start, end)
    shape.draw_line((290, 410), (290, 550))
    shape.draw_line((160, 470), (290, 410))
    shape.draw_line((410, 470), (290, 410))
    shape.finish(color=(0, 0, 0), width=1)
    shape.commit()
    content = pdf.tobytes()
    pdf.close()

    previews = producao_service._render_pdf_previews(content)

    assert previews["thumbnail"].startswith(b"\x89PNG")
    assert previews["detail"].startswith(b"\x89PNG")
    assert len(previews["detail"]) > len(previews["thumbnail"])


@pytest.mark.skipif(producao_service.fitz is None, reason="PyMuPDF nao instalado")
def test_rotulo_isometrico_escolhe_a_regiao_correta_do_desenho():
    fitz = producao_service.fitz
    pdf = fitz.open()
    page = pdf.new_page(width=700, height=600)

    def draw_part(offset_x):
        shape = page.new_shape()
        points = [
            (offset_x, 170), (offset_x + 90, 120), (offset_x + 180, 170),
            (offset_x + 90, 225), (offset_x, 170), (offset_x, 300),
            (offset_x + 90, 355), (offset_x + 180, 300), (offset_x + 180, 170),
        ]
        for start, end in zip(points, points[1:]):
            shape.draw_line(start, end)
        shape.draw_line((offset_x + 90, 225), (offset_x + 90, 355))
        shape.finish(color=(0, 0, 0), width=1)
        shape.commit()

    draw_part(50)
    draw_part(420)
    page.insert_text((445, 390), "VISTA ISOMETRICA", fontsize=11)

    candidates = producao_service._page_isometric_candidates(page)
    pdf.close()

    assert candidates[0][2] is True
    assert candidates[0][1].x0 > 350


@pytest.mark.skipif(producao_service.fitz is None, reason="PyMuPDF nao instalado")
def test_localiza_peca_isometrica_vertical_sem_incluir_carimbo():
    fitz = producao_service.fitz
    pdf = fitz.open()
    page = pdf.new_page(width=595, height=842)
    part = page.new_shape()
    for start, end in [
        ((190, 150), (350, 80)), ((350, 80), (440, 125)), ((440, 125), (275, 200)),
        ((275, 200), (190, 150)), ((220, 165), (220, 520)), ((275, 550), (275, 200)),
        ((220, 520), (275, 550)), ((275, 550), (360, 510)), ((360, 510), (360, 160)),
        ((220, 470), (275, 500)), ((275, 500), (360, 460)),
    ]:
        part.draw_line(start, end)
    part.draw_circle((315, 120), 12)
    part.draw_circle((310, 360), 10)
    part.draw_circle((310, 430), 10)
    part.finish(color=(0, 0, 0), width=1)
    part.commit()
    page.draw_rect(fitz.Rect(390, 690, 565, 815), color=(0, 0, 0), width=1)

    candidates = producao_service._page_isometric_candidates(page)
    previews = producao_service._render_pdf_previews(pdf.tobytes())
    pdf.close()

    assert candidates
    assert candidates[0][1].height > candidates[0][1].width
    assert candidates[0][1].y1 < 650
    assert previews["detail"].startswith(b"\x89PNG")


@pytest.mark.skipif(producao_service.fitz is None, reason="PyMuPDF nao instalado")
def test_pdf_sem_vista_isometrica_e_arquivo_invalido_falham_discretamente():
    fitz = producao_service.fitz
    pdf = fitz.open()
    page = pdf.new_page()
    page.insert_text((72, 72), "Somente texto administrativo")
    content = pdf.tobytes()
    pdf.close()

    with pytest.raises(LookupError, match="Vista isometrica"):
        producao_service._render_pdf_previews(content)
    with pytest.raises(LookupError, match="invalido"):
        producao_service._render_pdf_previews(b"nao e pdf")


def test_cache_reutiliza_derivadas_e_muda_com_identidade():
    producao_service._PREVIEW_CACHE.clear()
    producao_service._PREVIEW_JOBS.clear()
    producao_service._PREVIEW_FAILURES.clear()
    generated = {"thumbnail": b"thumb", "detail": b"detail"}

    with patch.object(producao_service, "_gerar_previews", return_value=generated) as generate:
        first = producao_service._previews_em_cache("document-v1", b"pdf", "drawing.pdf")
        second = producao_service._previews_em_cache("document-v1", b"pdf", "drawing.pdf")
        changed = producao_service._previews_em_cache("document-v2", b"pdf2", "drawing.pdf")

    assert first is second
    assert changed == generated
    assert generate.call_count == 2


def test_cache_assincrono_reutiliza_o_mesmo_processamento():
    producao_service._PREVIEW_CACHE.clear()
    producao_service._PREVIEW_JOBS.clear()
    producao_service._PREVIEW_FAILURES.clear()
    generated = {"thumbnail": b"thumb", "detail": b"detail"}

    with patch.object(producao_service, "_gerar_previews", return_value=generated) as generate:
        pending = producao_service._previews_em_cache("async-v1", b"pdf", "drawing.pdf", wait=False)
        completed = producao_service._previews_em_cache("async-v1", b"pdf", "drawing.pdf")

    assert pending is None
    assert completed == generated
    assert generate.call_count == 1


@pytest.mark.skipif(producao_service.fitz is None, reason="PyMuPDF nao instalado")
def test_imagem_original_isolada_gera_as_duas_variantes():
    fitz = producao_service.fitz
    source = fitz.open()
    page = source.new_page(width=500, height=500)
    shape = page.new_shape()
    shape.draw_line((130, 220), (250, 150))
    shape.draw_line((250, 150), (370, 220))
    shape.draw_line((370, 220), (250, 300))
    shape.draw_line((250, 300), (130, 220))
    shape.draw_line((130, 220), (130, 320))
    shape.draw_line((130, 320), (250, 390))
    shape.draw_line((250, 390), (370, 320))
    shape.draw_line((370, 320), (370, 220))
    shape.finish(color=(0, 0, 0), width=3)
    shape.commit()
    pixmap = page.get_pixmap(alpha=False)
    content = pixmap.tobytes("png")
    source.close()

    previews = producao_service._gerar_previews(content, "peca.png")

    assert previews["thumbnail"].startswith(b"\x89PNG")
    assert previews["detail"].startswith(b"\x89PNG")


def test_estrutura_reutiliza_consulta_recente_sem_compartilhar_mutacoes():
    producao_service._STRUCTURE_CACHE.clear()
    order = {"codigo": 9, "n_os": "7807"}
    payload = {"ordem": {"numero": "7807"}, "nos": [], "raizes": []}

    with (
        patch.object(producao_service, "fetch_one", return_value=order) as fetch_one,
        patch.object(producao_service, "fetch_all", side_effect=[[], []]) as fetch_all,
        patch.object(producao_service, "_estrutura_payload", return_value=payload),
        patch.object(producao_service, "_rncs", return_value=[]),
    ):
        first = producao_service.obter_estrutura("7807")
        first["nos"].append({"id": "alterado"})
        second = producao_service.obter_estrutura("7807")

    assert second["nos"] == []
    assert fetch_one.call_count == 1
    assert fetch_all.call_count == 2


def test_requisicao_assincrona_nao_bloqueia_enquanto_busca_documento():
    producao_service._PREVIEW_REQUEST_CACHE.clear()
    producao_service._PREVIEW_REQUEST_JOBS.clear()
    started = threading.Event()
    release = threading.Event()
    expected = (b"preview", "image/png", "etag")

    def slow_preview(*_args):
        started.set()
        release.wait(timeout=2)
        return expected

    with patch.object(producao_service, "obter_preview", side_effect=slow_preview):
        pending = producao_service._obter_preview_assincrono("7807", 2, "thumbnail")
        assert pending[0] is None
        assert started.wait(timeout=1)
        release.set()
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            completed = producao_service._obter_preview_assincrono("7807", 2, "thumbnail")
            if completed[0] is not None:
                break
            time.sleep(0.01)

    assert completed == expected


def test_aviso_de_previa_fica_limitado_a_miniatura():
    css = (PROJECT_ROOT / "static" / "css" / "producao_panel.css").read_text(encoding="utf-8")

    assert ".node-thumbnail {" in css
    assert "position: relative;" in css.split(".node-thumbnail {", 1)[1].split("}", 1)[0]
    assert "width: 54px;" in css.split(".node-thumbnail {", 1)[1].split("}", 1)[0]


def test_controles_solicitados_nao_sao_criados_na_producao():
    script = (PROJECT_ROOT / "static" / "js" / "producao_panel.js").read_text(encoding="utf-8")

    assert "Ver em lista" not in script
    assert "production-sequence-toggle" not in script
    assert "^Ampliar(?: imagem)?$" in script


def test_detalhes_identifica_e_limita_a_miniatura_isometrica():
    css = (PROJECT_ROOT / "static" / "css" / "producao_panel.css").read_text(encoding="utf-8")

    assert 'content: "Visão isométrica";' in css
    detail_rule = css.split(".detail-preview {", 1)[1].split("}", 1)[0]
    assert "width: 160px;" in detail_rule
    assert "height: 160px;" in detail_rule


def test_arvore_exibe_somente_numero_da_os():
    css = (PROJECT_ROOT / "static" / "css" / "producao_panel.css").read_text(encoding="utf-8")

    assert ".tree-copy small { display: none; }" in css


def test_hierarquia_compacta_usa_as_cores_solicitadas_sem_rotulo_extra():
    css = (PROJECT_ROOT / "static" / "css" / "producao_panel.css").read_text(encoding="utf-8")
    script = (PROJECT_ROOT / "static" / "js" / "producao_panel.js").read_text(encoding="utf-8")

    assert ".status-not_started { color: #111827; }" in css
    assert ".status-available { color: #7c3aed; }" in css
    assert "data-production-depth" in css
    assert "productionDepth" in script
    assert "Hierarquia compacta" not in css
    assert "Hierarquia compacta" not in script
