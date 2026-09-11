import importlib
import sys
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
        "production_test_app.routes": PROJECT_ROOT / "conferencia_app" / "routes",
    }
    for package_name, package_path in package_paths.items():
        package = ModuleType(package_name)
        package.__path__ = [str(package_path)]
        sys.modules[package_name] = package
    database = importlib.import_module("production_test_app.compras.db")
    database.fetch_all = lambda *_args, **_kwargs: []
    database.fetch_one = lambda *_args, **_kwargs: None
    sys.modules[database.__name__] = database
    return importlib.import_module("production_test_app.services.producao_service")


producao_service = _load_production_service()


def test_abertura_exige_numero_exato_da_os_antes_de_buscar_binario():
    with patch.object(producao_service, "fetch_one", return_value=None) as fetch:
        with pytest.raises(LookupError, match="nao encontrada"):
            producao_service.obter_arquivo("%", 13, "drawing", 7)
    fetch.assert_called_once_with(
        producao_service.queries.SQL_PRODUCAO_OBTER_OS,
        {"cod_empresa": 1, "numero_os": "%"},
    )
    assert "n_os = %(numero_os)s" in fetch.call_args.args[0]
    assert "ILIKE" not in fetch.call_args.args[0]


def document(document_id, kind, filename, description=""):
    return {
        "id": document_id,
        "kind": kind,
        "filename": filename,
        "description": description,
        "size_bytes": 100,
    }


def test_conexao_producao_forca_somente_leitura():
    from unittest.mock import MagicMock

    spec = importlib.util.spec_from_file_location(
        "production_test_app.compras.readonly_db", PROJECT_ROOT / "conferencia_app/compras/db.py"
    )
    database = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(database)
    connection = MagicMock()
    with patch.object(database, "get_settings") as settings, patch.object(database.psycopg2, "connect", return_value=connection) as connect:
        settings.return_value.dsn = "synthetic"
        with database.get_connection(readonly=True):
            pass
    assert "default_transaction_read_only=on" in connect.call_args.kwargs["options"]
    assert "statement_timeout=15000" in connect.call_args.kwargs["options"]
    assert connection.autocommit is False
    connection.set_session.assert_called_once_with(readonly=True)
    connection.cursor.return_value.__enter__.return_value.execute.assert_called_once_with("SET TRANSACTION READ ONLY")
    connection.close.assert_called_once()
    with patch.object(database, "get_settings") as settings, patch.object(database.psycopg2, "connect", return_value=connection) as connect:
        settings.return_value.dsn = "synthetic"
        with database.get_connection():
            pass
    assert "options" not in connect.call_args.kwargs


def test_bridge_antiga_nao_faz_fallback_para_conexao_direta():
    spec = importlib.util.spec_from_file_location(
        "production_test_app.compras.readonly_bridge", PROJECT_ROOT / "conferencia_app/compras/db.py"
    )
    database = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(database)
    with patch.object(database, "get_settings") as settings, patch.object(database.requests, "post") as post, patch.object(database, "get_connection") as connection:
        settings.return_value.API_URL = "https://synthetic.invalid"
        settings.return_value.USE_API_BRIDGE = True
        settings.return_value.API_TOKEN = ""
        settings.return_value.API_TIMEOUT = 1
        post.return_value.status_code = 200
        post.return_value.json.return_value = {"sucesso": True, "row": {"codigo": 1}}
        with pytest.raises(database.ProducaoSourceError, match="somente leitura"):
            database.fetch_one(database.queries.SQL_PRODUCAO_OBTER_OS, {"cod_empresa": 1, "numero_os": "OS-DEMO"})
        connection.assert_not_called()
        post.return_value.json.return_value["read_only"] = True
        assert database.fetch_one(database.queries.SQL_PRODUCAO_OBTER_OS, {"cod_empresa": 1, "numero_os": "OS-DEMO"}) == {"codigo": 1}


def test_cms_usa_anexo_e_desempata_pelo_menor_id():
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

    assert selected["id"] == 3
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

    assert selected["id"] == 2
    assert selected["kind"] == "drawing"


def test_cms_nao_faz_fallback_para_desenho():
    context = {"segmento": "CMS", "n_desenho": "DES-99", "revisao_desenho": ""}
    documents = [
        document(1, "drawing", "DES-99.pdf"),
        document(2, "attachment", "contrato.pdf"),
        document(3, "attachment", "pedido.pdf"),
    ]

    assert producao_service._selecionar_documento_previa(documents, context)["id"] == 2


def test_revisao_nao_substitui_pontuacao_da_referencia():
    context = {"segmento": "CMS", "n_desenho": "DES-99", "revisao_desenho": "03"}
    documents = [document(1, "attachment", "DES-99_REV02.pdf")]

    assert producao_service._selecionar_documento_previa(documents, context)["id"] == 1


@pytest.mark.parametrize("kind", ["image", "attachment", "drawing"])
def test_molde_permite_todas_as_fontes_e_exclui_origem(kind):
    documents = [document(7, kind, "PECA.PNG", "Suporte aco"), {**document(1, kind, "PECA.pdf"), "from_origin": True}]
    selected = producao_service._selecionar_documento_previa(documents, {"segmento": "MÓLDE", "subtitulo": "Suporte aço"})
    assert selected["id"] == 7


def test_formato_nao_renderizavel_nao_e_principal():
    assert producao_service._selecionar_documento_previa([document(1, "drawing", "arquivo.dwg")], {}) is None


@pytest.mark.skipif(producao_service.fitz is None, reason="PyMuPDF nao instalado")
def test_pdf_renderiza_somente_primeira_pagina():
    fitz = producao_service.fitz
    pdf = fitz.open()
    first = pdf.new_page(width=595, height=842)
    first.draw_rect(fitz.Rect(40, 40, 555, 802))
    for offset in range(8):
        first.draw_line((350, 650 + offset * 10), (540, 650 + offset * 10))
    first_page_content = pdf.tobytes()
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
    assert previews == producao_service._render_pdf_previews(first_page_content)


@pytest.mark.skipif(producao_service.fitz is None, reason="PyMuPDF nao instalado")
def test_pdf_sem_vista_isometrica_e_arquivo_invalido_falham_discretamente():
    fitz = producao_service.fitz
    pdf = fitz.open()
    page = pdf.new_page()
    page.insert_text((72, 72), "Somente texto administrativo")
    content = pdf.tobytes()
    pdf.close()

    assert producao_service._render_pdf_previews(content)["thumbnail"].startswith(b"\x89PNG")
    with pytest.raises(LookupError, match="invalido"):
        producao_service._render_pdf_previews(b"nao e pdf")


def test_cache_reutiliza_derivadas_e_muda_com_identidade():
    producao_service._PREVIEW_CACHE.clear()
    producao_service._PREVIEW_JOBS.clear()
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


def test_png_rgba_transparente_preserva_furo_e_componentes():
    import io
    from PIL import Image, ImageDraw

    source = Image.new("RGB", (400, 220), "white")
    drawing = ImageDraw.Draw(source)
    drawing.rectangle((40, 50, 280, 170), fill=(190, 200, 200))
    drawing.ellipse((90, 80, 150, 140), fill="white", outline="black", width=2)
    drawing.rectangle((320, 95, 350, 125), fill="black")
    content = io.BytesIO()
    source.save(content, format="PNG")
    previews = producao_service._gerar_previews(content.getvalue(), "sintetico.png")
    for binary in previews.values():
        with Image.open(io.BytesIO(binary)) as image:
            assert image.mode == "RGBA"
            assert image.getchannel("A").getextrema() == (0, 255)
            assert image.width > image.height


def test_limite_pixels_e_crop_invalidos():
    import io
    from PIL import Image

    with pytest.raises(LookupError, match="Configuracao"):
        producao_service.producao_render.options({"PRODUCAO_THUMBNAIL_CROP": "0.8,0,0.2,1"})
    content = io.BytesIO()
    Image.new("RGB", (100, 100)).save(content, "PNG")
    settings = producao_service.producao_render.options({"PRODUCAO_MAX_PIXELS": 100})
    with pytest.raises(LookupError, match="pixels"):
        producao_service._gerar_previews(content.getvalue(), "grande.png", settings)


def build_document_app(database_uri="sqlite:///:memory:"):
    from flask import Flask

    extensions = importlib.import_module("production_test_app.extensions")
    models = importlib.import_module("production_test_app.models")
    routes = importlib.import_module("production_test_app.routes.producao_routes")
    app = Flask(__name__, static_folder=str(PROJECT_ROOT / "static"))
    app.config.update(TESTING=True, SECRET_KEY="synthetic-test-only", SQLALCHEMY_DATABASE_URI=database_uri)
    extensions.db.init_app(app)
    app.register_blueprint(routes.producao_bp)
    with app.app_context():
        models.ProducaoDerivedAsset.__table__.create(extensions.db.engine)
        models.ProducaoObservacao.__table__.create(extensions.db.engine)
        models.ProducaoSequencia.__table__.create(extensions.db.engine)
    return app


@pytest.fixture
def document_app():
    app = build_document_app()
    with app.app_context():
        yield app
        extensions = importlib.import_module("production_test_app.extensions")
        extensions.db.session.remove()
        extensions.db.engine.dispose()


def test_cache_persistente_reutiliza_hash_e_resolve_conflito(document_app):
    assert producao_service._cached_asset("a" * 64, 1) is None
    args = ("a" * 64, 1, "OS-SINTETICA", 7, "peca.png", "b" * 64)
    assert producao_service._store_asset(*args, b"first") == b"first"
    assert producao_service._store_asset(*args, b"duplicate") == b"first"
    assert producao_service._cached_asset("a" * 64, 1) == b"first"
    assert producao_service._cached_asset("a" * 64, 2) is None


class SyntheticSource:
    def __init__(self):
        import io
        from PIL import Image, ImageDraw

        image = Image.new("RGB", (500, 220), "white")
        drawing = ImageDraw.Draw(image)
        drawing.polygon([(60, 80), (300, 40), (440, 100), (200, 155)], fill="#91b7ba", outline="black", width=3)
        drawing.polygon([(60, 80), (200, 155), (200, 195), (60, 120)], fill="#59858b", outline="black", width=3)
        drawing.polygon([(200, 155), (440, 100), (440, 140), (200, 195)], fill="#bed3d5", outline="black", width=3)
        output = io.BytesIO()
        image.save(output, "PNG")
        self.content = output.getvalue()
        self.calls = []
        self.order = {"codigo": 101, "n_os": "OS-DEMO", "titulo": "Montagem sintetica", "u_classificacao": "MOLDE", "cod_empresa": 1}
        self.items = [
            {"aux_code": 1, "cod_os_completo": "001", "n_desenho": "D-1", "subtitulo": "Conjunto de teste", "os_pai": None, "qtde_pecas": 1},
            {"aux_code": 7, "cod_os_completo": "007", "n_desenho": "D-7", "subtitulo": "Suporte sintetico com descricao longa para verificar enquadramento", "os_pai": 1, "qtde_pecas": 2},
            {"aux_code": 8, "cod_os_completo": "008", "n_desenho": None, "subtitulo": "Item sem documentos", "os_pai": None},
        ]
        self.rows = [
            {"document_id": 7, "cod_os_aux": 7, "kind": "image", "nome_arquivo": "D-7.png", "descricao": "Suporte sintetico", "size_bytes": len(self.content), "content_revision": "1"},
            {"document_id": 7, "cod_os_aux": 7, "kind": "attachment", "nome_arquivo": "Documento_administrativo_sintetico_com_nome_longo.txt", "descricao": "Anexo", "size_bytes": 10, "content_revision": "1"},
            {"document_id": 1, "cod_os_aux": 1, "kind": "drawing", "nome_arquivo": "D-1.png", "descricao": "Conjunto sintetico", "size_bytes": len(self.content), "content_revision": "1"},
        ]

    def fetch_all(self, query, params):
        self.calls.append((query, dict(params)))
        if params.get("cod_empresa") != 1:
            return []
        queries = producao_service.queries
        if query == queries.SQL_PRODUCAO_ESTRUTURA_OS:
            return self.items
        if query == queries.SQL_PRODUCAO_DOCUMENTOS_OS:
            return [dict(row) for row in self.rows]
        if query == queries.SQL_PRODUCAO_DOCUMENTOS_ITEM:
            if params["cod_os"] == 202:
                return [{**self.rows[0], "kind": "drawing", "cod_os_aux": 70, "nome_arquivo": "Origem.png"}]
            return [dict(row) for row in self.rows if row["cod_os_aux"] == params["cod_os_aux"]]
        return []

    def fetch_one(self, query, params):
        self.calls.append((query, dict(params)))
        if params.get("cod_empresa") != 1:
            return None
        queries = producao_service.queries
        if query == queries.SQL_PRODUCAO_OBTER_OS:
            return self.order if params["numero_os"] == "OS-DEMO" else None
        if query == queries.SQL_PRODUCAO_ITEM_PREVIEW_CONTEXT:
            return next((dict(item) for item in self.items if item["aux_code"] == params["cod_os_aux"]), None)
        if query == queries.SQL_PRODUCAO_ORIGEM_SCHEMA:
            return {"supported": True}
        if query == queries.SQL_PRODUCAO_ITEM_ORIGEM:
            return {"cod_os": 202, "cod_os_aux": 70} if params["cod_os_aux"] == 7 else None
        file_queries = {queries.SQL_PRODUCAO_DESENHO_ARQUIVO: "drawing", queries.SQL_PRODUCAO_ANEXO_ARQUIVO: "attachment", queries.SQL_PRODUCAO_IMAGEM_ARQUIVO: "image"}
        if query in file_queries:
            if params["cod_os"] == 202:
                return {"anexo": self.content, "nome_arquivo": "Origem.png", "size_bytes": len(self.content)}
            row = next((row for row in self.rows if row["document_id"] == params["document_id"] and row["cod_os_aux"] == params["cod_os_aux"] and row["kind"] == file_queries[query]), None)
            if row:
                return {**row, "anexo": self.content if row["kind"] != "attachment" else b"<html>synthetic</html>"}
        return None


@pytest.fixture
def synthetic_source():
    source = SyntheticSource()
    with patch.object(producao_service, "fetch_all", side_effect=source.fetch_all), patch.object(producao_service, "fetch_one", side_effect=source.fetch_one), patch.object(producao_service, "_empresa", return_value=1):
        yield source


@pytest.fixture
def document_client(document_app):
    auth = importlib.import_module("production_test_app.auth")
    client = document_app.test_client()
    with client.session_transaction() as session:
        session["username"] = "synthetic-user"
        session["role"] = "Producao"
    with patch.object(auth, "check_active_session"), patch.object(auth, "is_admin_session", return_value=False), patch.object(auth, "has_permission", return_value=True), patch.object(auth, "_registrar_acesso_admin"):
        yield client


def test_estrutura_continua_disponivel_sem_consulta_documental(document_client, synthetic_source):
    with patch.object(producao_service, "_document_sources", side_effect=RuntimeError("synthetic document failure")):
        response = document_client.get("/api/v1/orders/OS-DEMO/structure")
    assert response.status_code == 200
    assert len(response.json["nodes"]) == len(synthetic_source.items)
    assert all(node["thumbnail_url"] is None for node in response.json["nodes"])
    assert response.json["source"]["documents_available"] is False


@pytest.mark.parametrize("documents_failed_at_structure", [True, False])
def test_detalhe_preserva_peca_sem_documentos(document_client, synthetic_source, documents_failed_at_structure):
    owner = "_document_sources" if documents_failed_at_structure else "obter_documentos"
    with patch.object(producao_service, owner, side_effect=RuntimeError("synthetic private failure")):
        response = document_client.get("/api/v1/orders/OS-DEMO/items/7")
    assert response.status_code == 200
    assert response.json["node"]["aux_code"] == 7
    assert response.json["documents_available"] is False
    assert response.json["documents"] == response.json["drawings"] == []
    assert response.json["node"]["thumbnail_url"] is None
    assert b"synthetic private failure" not in response.data


@pytest.mark.parametrize("status,payload,code", [
    (400, {"sucesso": False, "erro": "query_nao_permitida"}, "bridge_catalog_outdated"),
    (200, {"sucesso": True, "row": {}}, "bridge_readonly_required"),
    (401, {}, "bridge_access_denied"),
    (403, {"erro": "empresa_nao_autorizada"}, "bridge_access_denied"),
    (503, {"erro": "sensitive SQL details"}, "bridge_unavailable"),
    (200, [], "bridge_unavailable"),
])
def test_erro_bridge_identificado_sem_expor_resposta(status, payload, code, document_client):
    database = importlib.import_module("production_test_app.compras.db")
    with patch.object(database, "get_settings") as settings, patch.object(database.requests, "post") as post, patch.object(database, "get_connection") as connection:
        settings.return_value.API_URL = "https://synthetic.invalid"
        settings.return_value.USE_API_BRIDGE = True
        settings.return_value.API_TOKEN = ""
        settings.return_value.API_TIMEOUT = 1
        post.return_value.status_code = status
        post.return_value.json.return_value = payload
        with patch.object(producao_service, "fetch_one", side_effect=lambda query, params: database._exec_fetch(query, params, one=True)):
            response = document_client.get("/api/v1/orders/7807/structure")
    assert response.status_code == 503
    assert response.json["code"] == code
    assert "sensitive" not in response.json["detail"]
    assert "synthetic.invalid" not in response.json["detail"]
    connection.assert_not_called()


def test_falha_conexao_principal_nao_vira_estrutura_vazia(document_client):
    database = importlib.import_module("production_test_app.compras.db")
    with patch.object(database, "get_settings") as settings, patch.object(database, "get_connection", side_effect=database.psycopg2.OperationalError("private connection details")):
        settings.return_value.API_URL = ""
        with patch.object(producao_service, "fetch_one", side_effect=lambda query, params: database._exec_fetch(query, params, one=True)):
            response = document_client.get("/api/v1/orders/7807/structure")
    assert response.status_code == 503
    assert response.json["code"] == "grv_unavailable"
    assert "private" not in response.json["detail"]
    assert "nodes" not in response.json


def test_item_inexistente_continua_404_sem_documentos(document_client, synthetic_source):
    with patch.object(producao_service, "_document_sources", side_effect=RuntimeError("document failure")):
        response = document_client.get("/api/v1/orders/OS-DEMO/items/999")
    assert response.status_code == 404
    assert response.json["detail"] == "Item nao encontrado"


def test_detalhe_lista_sem_binarios_com_identidade_e_origem(document_client, synthetic_source):
    response = document_client.get("/api/v1/orders/OS-DEMO/items/7")
    assert response.status_code == 200
    detail = response.json
    primary = next(doc for doc in detail["documents"] if doc["is_primary"])
    assert primary["source_kind"] == "image"
    assert primary["kind"] == "attachment"
    assert primary["open_url"].endswith("/images/7")
    assert detail["node"]["thumbnail_url"].startswith("/api/v1/orders/OS-DEMO/items/7/thumbnail?v=")
    assert len({doc["open_url"] for doc in detail["documents"]}) == 3
    assert not next(doc for doc in detail["documents"] if doc["from_origin"])["is_primary"]
    assert not any(" anexo\nFROM" in query for query, _params in synthetic_source.calls)


@pytest.mark.parametrize("path", ["images/7", "drawings/7?origin=true", "thumbnail"])
def test_binarios_negam_acesso_sem_login_e_sem_permissao(document_app, path):
    auth = importlib.import_module("production_test_app.auth")
    client = document_app.test_client()
    url = f"/api/v1/orders/OS-DEMO/items/7/{path}"
    with patch.object(producao_service, "fetch_one") as fetch:
        assert client.get(url).status_code == 401
        with client.session_transaction() as session:
            session["username"] = "denied"
        with patch.object(auth, "check_active_session"), patch.object(auth, "is_admin_session", return_value=False), patch.object(auth, "has_permission", return_value=False):
            assert client.get(url).status_code == 403
        fetch.assert_not_called()


def test_originais_cabecalhos_e_vinculos(document_client, synthetic_source):
    image = document_client.get("/api/v1/orders/OS-DEMO/items/7/images/7")
    assert image.status_code == 200
    assert image.mimetype == "image/png"
    assert image.headers["X-Content-Type-Options"] == "nosniff"
    assert "private" in image.headers["Cache-Control"]
    assert "sandbox" in image.headers["Content-Security-Policy"]
    text = document_client.get("/api/v1/orders/OS-DEMO/items/7/attachments/7")
    assert text.headers["Content-Disposition"].startswith("attachment;")
    assert text.mimetype == "application/octet-stream"
    origin = document_client.get("/api/v1/orders/OS-DEMO/items/7/drawings/7?origin=true")
    assert origin.status_code == 200
    assert document_client.get("/api/v1/orders/OS-DEMO/items/1/images/7").status_code == 404
    assert document_client.get("/api/v1/orders/OS-DEMO/items/999/images/7").status_code == 404
    assert document_client.get("/api/v1/orders/OS-DEMO/items/1/drawings/7?origin=true").status_code == 404
    assert document_client.get("/api/v1/orders/OS-DEMO/items/7/images/7?origin=202").status_code == 400


def test_cache_http_etag_e_invalidacao(document_client, synthetic_source):
    producao_service._PREVIEW_CACHE.clear()
    url = "/api/v1/orders/OS-DEMO/items/7/thumbnail"
    with patch.object(producao_service, "_gerar_previews", wraps=producao_service._gerar_previews) as render:
        first = document_client.get(url)
        assert first.status_code == 200, first.json
        second = document_client.get(url, headers={"If-None-Match": first.headers["ETag"]})
        assert second.status_code == 304
        assert second.data == b""
        assert render.call_count == 1
        producao_service._PREVIEW_CACHE.clear()
        assert document_client.get(url).data == first.data
        assert render.call_count == 1
        variant = document_client.get(url + "?variant=detail")
        assert variant.headers["ETag"] != first.headers["ETag"]
        with patch.object(producao_service, "_PREVIEW_CACHE_VERSION", "next-version"):
            changed = document_client.get(url)
        assert changed.status_code == 200
        assert changed.headers["ETag"] != first.headers["ETag"]
        document_client.application.config["PRODUCAO_THUMBNAIL_DPI"] = 200
        assert document_client.get(url).headers["ETag"] != first.headers["ETag"]
    assert first.mimetype == "image/png"
    assert first.headers["Cache-Control"] == "private, max-age=0, must-revalidate"
    assert first.headers["Content-Disposition"].startswith("inline")


def test_limite_original_antes_do_binario(document_client, synthetic_source):
    document_client.application.config["PRODUCAO_DOCUMENT_MAX_BYTES"] = 10
    response = document_client.get("/api/v1/orders/OS-DEMO/items/7/images/7")
    assert response.status_code == 413
    assert all(query != producao_service.queries.SQL_PRODUCAO_IMAGEM_ARQUIVO for query, _params in synthetic_source.calls)


def test_fontes_por_numero_ancestral_e_ciclo():
    domain = producao_service.documents_domain
    items = [{"aux_code": 1, "n_desenho": "D-1"}, {"aux_code": 2, "n_desenho": "D 1"}, {"aux_code": 3, "os_pai": 2}, {"aux_code": 4, "os_pai": 5}, {"aux_code": 5, "os_pai": 4}]
    drawing = {**document(10, "drawing", "D-1.pdf"), "aux_code": 1}
    sources = domain.resolve_sources(items, [drawing], {})
    assert all(sources[item_id]["id"] == 10 for item_id in (1, 2, 3))
    assert sources[4] is sources[5] is None
    assert all(source is None for source in domain.resolve_sources(items, [drawing], {"u_classificacao": "CMS"}).values())


def test_url_estavel_e_muda_somente_com_revisao():
    domain = producao_service.documents_domain
    primary = {**document(1, "drawing", "arquivo com espaço.pdf"), "source_kind": "drawing", "aux_code": 7, "content_revision": "1"}
    original = domain.thumbnail_url("OS/1", 7, primary)
    assert original == domain.thumbnail_url("OS/1", 7, dict(primary))
    assert original != domain.thumbnail_url("OS/1", 7, {**primary, "content_revision": "2"})
    assert "OS%2F1" in original
    assert domain.thumbnail_url("OS/1", 7, None) is None


def test_worker_timeout_controlado():
    import subprocess

    with patch.object(producao_service.producao_render.subprocess, "run", side_effect=subprocess.TimeoutExpired("synthetic", 1)):
        with pytest.raises(LookupError, match="Tempo limite"):
            producao_service._gerar_previews(b"synthetic", "image.png")


def test_conteudo_diferente_mesmo_nome_e_tamanho_invalida_cache(document_client, synthetic_source):
    import io
    from PIL import Image

    output = io.BytesIO()
    image = Image.new("RGB", (40, 40), "white")
    image.putpixel((20, 20), (0, 0, 0))
    image.save(output, "BMP")
    synthetic_source.content = output.getvalue()
    synthetic_source.rows[0].update(nome_arquivo="D-7.bmp", size_bytes=len(synthetic_source.content))
    preview = io.BytesIO()
    image.convert("RGBA").save(preview, "PNG")
    with patch.object(producao_service, "_gerar_previews", return_value={"thumbnail": preview.getvalue(), "detail": preview.getvalue()}) as render:
        first = document_client.get("/api/v1/orders/OS-DEMO/items/7/thumbnail")
        output = io.BytesIO()
        image.putpixel((20, 20), (255, 0, 0))
        image.save(output, "BMP")
        changed = output.getvalue()
        assert len(changed) == len(synthetic_source.content)
        synthetic_source.content = changed
        second = document_client.get("/api/v1/orders/OS-DEMO/items/7/thumbnail")
    assert first.status_code == second.status_code == 200
    assert first.headers["ETag"] != second.headers["ETag"]
    assert render.call_count == 2


def test_empresa_diferente_nao_abre_mesmo_id(document_client, synthetic_source):
    with patch.object(producao_service, "_empresa", return_value=2):
        assert document_client.get("/api/v1/orders/OS-DEMO/items/7/images/7").status_code == 404
    assert all(params["cod_empresa"] == 2 for _query, params in synthetic_source.calls)


def test_pontuacao_acentos_descricao_titulo_e_desempate():
    docs = [document(4, "drawing", "SUPORTE-ACO.pdf", "Elevador"), document(2, "drawing", "suporte_aco.PDF", "Elevador"), document(1, "drawing", "D-1.pdf", "Base")]
    selected = producao_service._selecionar_documento_previa(docs, {"subtitulo": "Suporte aço", "titulo": "Elevador", "n_desenho": "D-1"})
    assert selected["id"] == 2


def test_pdf_rotulado_prioriza_vista_proxima():
    import io
    from PIL import Image

    fitz = producao_service.fitz
    with fitz.open() as pdf:
        page = pdf.new_page(width=600, height=400)
        page.draw_rect(fitz.Rect(50, 60, 200, 180), fill=(1, 0, 0))
        page.insert_text((65, 205), "VISTA ISOMETRICA")
        page.draw_rect(fitz.Rect(300, 40, 550, 300), fill=(0, 0, 1))
        previews = producao_service._render_pdf_previews(pdf.tobytes())
    with Image.open(io.BytesIO(previews["thumbnail"])) as image:
        pixels = list(zip(*[iter(image.tobytes())] * 4))
        assert sum(red > blue + 50 and alpha > 100 for red, _green, blue, alpha in pixels) > 100
        assert sum(blue > red + 50 and alpha > 100 for red, _green, blue, alpha in pixels) == 0


def test_pdf_raster_e_aprovacao_usam_ramos_distintos():
    import io
    from PIL import Image

    fitz = producao_service.fitz
    raster = io.BytesIO()
    Image.new("RGB", (200, 200), "blue").save(raster, "PNG")
    with fitz.open() as pdf:
        page = pdf.new_page(width=600, height=400)
        page.draw_rect(fitz.Rect(50, 60, 200, 180), fill=(1, 0, 0))
        page.insert_image(fitz.Rect(300, 40, 550, 300), stream=raster.getvalue())
        content = pdf.tobytes()
    normal = producao_service.producao_render.render(content, "desenho.pdf")
    approval = producao_service.producao_render.render(content, "desenho.pdf", description="Aprovacao")
    assert normal["thumbnail"] != approval["thumbnail"]


def test_pdf_protegido_e_vazio_sao_controlados():
    fitz = producao_service.fitz
    with fitz.open() as pdf:
        pdf.new_page()
        protected = pdf.tobytes(encryption=fitz.PDF_ENCRYPT_AES_256, owner_pw="synthetic", user_pw="synthetic")
        with pytest.raises(LookupError, match="protegido"):
            producao_service._render_pdf_previews(protected)
        with pytest.raises(LookupError, match="Vista ausente"):
            producao_service._render_pdf_previews(pdf.tobytes())


def test_peca_longa_mantem_canvas_legivel():
    import io
    from PIL import Image, ImageDraw

    source = Image.new("RGB", (4000, 120), "white")
    ImageDraw.Draw(source).rectangle((40, 50, 3960, 70), fill=(190, 190, 190), outline="black", width=1)
    output = io.BytesIO()
    source.save(output, "PNG")
    previews = producao_service._gerar_previews(output.getvalue(), "longa.png")
    with Image.open(io.BytesIO(previews["detail"])) as image:
        assert image.width / image.height <= 14
        assert image.getchannel("A").getbbox()


def test_migracao_apenas_banco_sintetico():
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import create_engine, inspect

    spec = importlib.util.spec_from_file_location("producao_asset_migration", PROJECT_ROOT / "migrations/versions/20260911_producao_derived_assets.py")
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
            assert inspect(connection).get_table_names() == ["derived_assets"]
            migration.downgrade()
            assert inspect(connection).get_table_names() == []
    engine.dispose()


def test_requisicoes_concorrentes_reutilizam_mesmo_asset(tmp_path, synthetic_source):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    app = build_document_app(f"sqlite:///{(tmp_path / 'cache.db').as_posix()}")
    models = importlib.import_module("production_test_app.models")
    extensions = importlib.import_module("production_test_app.extensions")
    barrier = Barrier(2)
    producao_service._PREVIEW_CACHE.clear()

    def simultaneous_miss(_cache_key, _company):
        barrier.wait(timeout=10)
        return None

    def request_preview(aux_code):
        with app.app_context():
            return producao_service.obter_preview("OS-DEMO", aux_code)

    with patch.object(producao_service, "_cached_asset", side_effect=simultaneous_miss):
        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(request_preview, 1)
            second = executor.submit(request_preview, 7)
            assert first.result(timeout=30) == second.result(timeout=30)
    with app.app_context():
        assert models.ProducaoDerivedAsset.query.count() == 1
        extensions.db.session.remove()
        extensions.db.engine.dispose()


def serve_synthetic_preview():
    from contextlib import ExitStack
    from flask import session
    from werkzeug.serving import make_server

    app = build_document_app()
    source = SyntheticSource()
    auth = importlib.import_module("production_test_app.auth")

    @app.before_request
    def synthetic_session():
        session["username"] = "synthetic-user"
        session["role"] = "Producao"

    with ExitStack() as stack:
        stack.enter_context(patch.object(producao_service, "fetch_all", side_effect=source.fetch_all))
        stack.enter_context(patch.object(producao_service, "fetch_one", side_effect=source.fetch_one))
        stack.enter_context(patch.object(producao_service, "_empresa", return_value=1))
        stack.enter_context(patch.object(auth, "check_active_session"))
        stack.enter_context(patch.object(auth, "is_admin_session", return_value=False))
        stack.enter_context(patch.object(auth, "has_permission", return_value=True))
        stack.enter_context(patch.object(auth, "_registrar_acesso_admin"))
        server = make_server("127.0.0.1", 0, app, threaded=True)
        print(f"SYNTHETIC_PREVIEW_URL=http://127.0.0.1:{server.server_port}/producao-original/?os=OS-DEMO", flush=True)
        server.serve_forever()


if __name__ == "__main__" and "--serve" in sys.argv:
    serve_synthetic_preview()