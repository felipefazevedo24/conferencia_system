"""Conferido x faturado na Conferência de Expedição: compara a contagem com os
itens da NF (código e quantidade) e marca a ordem quando não bate. ERP sempre
com patch; dados fictícios."""
from datetime import datetime, timedelta
from unittest.mock import patch

from conferencia_app.extensions import db
from conferencia_app.models import ExpedicaoFatComparacaoNF, ExpedicaoOrdemFat, ExpedicaoOrdemFatItem
from conferencia_app.services import expedicao_fat_comparacao_nf_service as svc
from conferencia_app.services import expedicao_sync_scheduler
from tests.test_app import build_test_app, set_logged_user

CONFERIDO_EM = datetime(2026, 10, 1, 9, 0)


def _criar(app, cod=500, numero_nf="9001", conferidas=((("AA-01", 15), ("AA-02", 60))), conferido_at=CONFERIDO_EM, **extra):
    with app.app_context():
        ordem = ExpedicaoOrdemFat(cod_ordem_fat=cod, status="Faturado", numero_nf=numero_nf, conferido_at=conferido_at,
                                  faturado_at=datetime(2026, 10, 2), **extra)
        db.session.add(ordem)
        db.session.flush()
        for linha, (codigo, qtde) in enumerate(conferidas):
            db.session.add(ExpedicaoOrdemFatItem(ordem_id=ordem.id, linha=linha, cod_interno=codigo, item=f"Item {codigo}",
                                                 qtde_a_faturar=qtde or 0, qtde_conferida=qtde))
        db.session.commit()
        return ordem.id


def _nf(*itens):
    """Patch do ERP devolvendo uma NF com estes (cProd, qCom)."""
    nota = patch.object(svc, "buscar_nfe_emitida_erp", return_value={"xml_bytes": b"<xml/>"})
    xml = patch.object(svc.danfe_service, "parse_nfe_xml", return_value={
        "itens": [{"cProd": codigo, "xProd": f"Item {codigo}", "qCom": qtde} for codigo, qtde in itens],
    })
    return nota, xml


def _cliente(app):
    client = app.test_client()
    set_logged_user(client, "conferente_teste", "Admin")
    return client


def test_comparar_itens_soma_linhas_do_mesmo_codigo_e_aponta_os_tres_tipos():
    conferidos = [
        {"cod_interno": "AA-01", "item": "Um", "quantidade": 10},
        {"cod_interno": "AA-01", "item": "Um", "quantidade": 5},     # mesma peça em 2 linhas da ordem
        {"cod_interno": "AA-02", "item": "Dois", "quantidade": 60},
        {"cod_interno": "AA-03", "item": "Três", "quantidade": 2},
        {"cod_interno": "AA-05", "item": "Cinco", "quantidade": 0},  # contou zero e não faturou: bate
    ]
    faturados = [
        {"cod_interno": "aa01", "item": "Um", "quantidade": "15.0000"},  # NF juntou as linhas; código sem pontuação
        {"cod_interno": "AA-02", "item": "Dois", "quantidade": "58.0000"},
        {"cod_interno": "AA-04", "item": "Quatro", "quantidade": "1.0000"},
    ]
    assert [(d["cod_interno"], d["conferido"], d["faturado"], d["tipo"]) for d in svc.comparar_itens(conferidos, faturados)] == [
        ("AA-02", 60.0, 58.0, "quantidade"),
        ("AA-03", 2.0, None, "nao_faturado"),
        ("AA-04", None, 1.0, "nao_conferido"),
    ]


def test_ordem_que_bate_com_a_nf_nao_ganha_triangulo(tmp_path):
    app = build_test_app(tmp_path)
    _criar(app)
    client = _cliente(app)
    nota, xml = _nf(("AA-01", "15.0000"), ("AA-02", "60.0000"))
    with nota as erp, xml:
        data = client.get("/api/expedicao/conf-cega/ordens/500/comparacao-nf").get_json()
        assert data["status"] == "ok" and data["divergente"] is False and data["divergencias"] == []
        # Segunda consulta usa o que ficou guardado: a NF autorizada não muda.
        client.get("/api/expedicao/conf-cega/ordens/500/comparacao-nf")
    assert erp.call_count == 1
    [ordem] = client.get("/api/expedicao/conf-cega/ordens").get_json()["ordens"]
    assert ordem["divergente_nf"] is False


def test_divergencia_aparece_na_lista_e_no_detalhe_e_e_refeita_se_reconferir(tmp_path):
    app = build_test_app(tmp_path)
    ordem_id = _criar(app)
    client = _cliente(app)
    nota, xml = _nf(("AA-01", "15.0000"), ("AA-02", "58.0000"))
    with nota, xml:
        data = client.get("/api/expedicao/conf-cega/ordens/500/comparacao-nf").get_json()
    assert data["divergente"] is True and data["numero_nf"] == "9001"
    [d] = data["divergencias"]
    assert (d["cod_interno"], d["conferido"], d["faturado"], d["tipo_label"]) == ("AA-02", 60.0, 58.0, "Quantidade diferente")
    assert client.get("/api/expedicao/conf-cega/ordens").get_json()["ordens"][0]["divergente_nf"] is True

    # Corrigiu a conferência: a comparação guardada deixa de valer na hora
    # (some da lista) e é refeita na próxima consulta.
    with app.app_context():
        ordem = db.session.get(ExpedicaoOrdemFat, ordem_id)
        ordem.conferido_at = CONFERIDO_EM + timedelta(hours=2)
        ordem.itens[1].qtde_conferida = 58
        db.session.commit()
    assert client.get("/api/expedicao/conf-cega/ordens").get_json()["ordens"][0]["divergente_nf"] is False
    with nota, xml:
        assert client.get("/api/expedicao/conf-cega/ordens/500/comparacao-nf").get_json()["divergente"] is False


def test_conferencia_cega_nao_vaza_a_nf_antes_de_conferir(tmp_path):
    app = build_test_app(tmp_path)
    _criar(app, cod=501, conferido_at=None, conferidas=(("AA-01", None),))          # faturado sem conferência
    _criar(app, cod=502, numero_nf=None)                                              # conferido, ainda sem NF
    _criar(app, cod=503, expedido_sem_conferencia=True)                               # "contagem" copiada do previsto
    client = _cliente(app)
    with patch.object(svc, "buscar_nfe_emitida_erp") as erp:
        assert client.get("/api/expedicao/conf-cega/ordens/501/comparacao-nf").get_json()["status"] == "sem_conferencia"
        assert client.get("/api/expedicao/conf-cega/ordens/502/comparacao-nf").get_json()["status"] == "sem_nf"
        assert client.get("/api/expedicao/conf-cega/ordens/503/comparacao-nf").get_json()["status"] == "sem_conferencia"
        with app.app_context():
            assert svc.verificar_pendentes() == 0
    erp.assert_not_called()
    assert client.get("/api/expedicao/conf-cega/ordens/999/comparacao-nf").status_code == 404


def test_erp_fora_nao_derruba_e_tenta_de_novo_depois(tmp_path):
    app = build_test_app(tmp_path)
    ordem_id = _criar(app)
    client = _cliente(app)
    with patch.object(svc, "buscar_nfe_emitida_erp", side_effect=RuntimeError("bridge fora")):
        resp = client.get("/api/expedicao/conf-cega/ordens/500/comparacao-nf")
    assert resp.status_code == 200 and resp.get_json()["status"] == "indisponivel"
    assert client.get("/api/expedicao/conf-cega/ordens").get_json()["ordens"][0]["divergente_nf"] is False

    nota, xml = _nf(("AA-01", "15.0000"), ("AA-02", "58.0000"))
    with app.app_context(), nota as erp, xml:
        # Falhou agora há pouco: a rodada automática não insiste.
        assert svc.verificar_pendentes() == 0
        linha = ExpedicaoFatComparacaoNF.query.filter_by(ordem_id=ordem_id).one()
        linha.verificado_em = linha.verificado_em - timedelta(hours=svc.HORAS_NOVA_TENTATIVA + 1)
        db.session.commit()
        assert svc.verificar_pendentes() == 1
        assert erp.call_count == 1
        assert ExpedicaoFatComparacaoNF.query.filter_by(ordem_id=ordem_id).one().divergente is True
        # Já comparada e válida: nada a fazer na rodada seguinte.
        assert svc.verificar_pendentes() == 0


def test_sincronizacao_automatica_compara_as_ordens_faturadas(tmp_path):
    app = build_test_app(tmp_path)
    _criar(app)
    _criar(app, cod=510, numero_nf="9002, 9003", conferidas=(("AA-01", 4),))   # ordem com 2 NFs: soma as duas
    nota, xml = _nf(("AA-01", "2.0000"))
    with nota as erp, xml, patch("conferencia_app.services.expedicao_fat_service.sincronizar_ordens", return_value={}):
        app.config["EXPEDICAO_SYNC_ST_ENABLED"] = False
        resumo = expedicao_sync_scheduler.executar_ciclo(app)
    assert resumo["fat_comparacoes_nf"] == 2
    assert sorted(c.kwargs["numero_nf"] for c in erp.call_args_list) == ["9001", "9002", "9003"]
    with app.app_context():
        por_cod = {o.cod_ordem_fat: ExpedicaoFatComparacaoNF.query.filter_by(ordem_id=o.id).one().divergente
                   for o in ExpedicaoOrdemFat.query.all()}
    # 500: conferiu 15 e 60, NF tem só 2 do AA-01. 510: conferiu 4, as duas NFs somam 4.
    assert por_cod == {500: True, 510: False}


def test_tela_tem_o_cartao_e_o_triangulo_da_lista(tmp_path):
    app = build_test_app(tmp_path)
    html = _cliente(app).get("/expedicao/conferencia-cega").get_data(as_text=True)
    assert "card-comparacao-nf" in html
    assert "function renderComparacaoNf" in html
    assert "Conferido ≠ faturado" in html
