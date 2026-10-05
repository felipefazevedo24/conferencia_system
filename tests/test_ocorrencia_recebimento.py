"""Divergências de recebimento (Compras > Divergências de recebimento).

A conferência com pendência abre a ocorrência e avisa no Teams; Compras diz o
que será feito; devolução total passa pelo Fiscal; parada gera lembrete.
Teams sempre com patch - teste não pode depender de rede."""
from datetime import timedelta
from unittest.mock import patch

from conferencia_app.extensions import db
from conferencia_app.models import ItemNota, OcorrenciaRecebimento
from conferencia_app.services import ocorrencia_recebimento_service as svc
from conferencia_app.tempo import agora_br
from tests.test_app import build_test_app, login_admin, set_logged_user

TEAMS = "conferencia_app.services.teams_service.notificar_ocorrencia_recebimento"


def _nota(app, numero="7001", qtd=10.0):
    with app.app_context():
        item = ItemNota(numero_nota=numero, fornecedor="Aços Brasil", cnpj_emitente="12345678000199",
                        codigo="A1", descricao="Chapa A36", qtd_real=qtd, status="Pendente", pedido_compra="4500")
        db.session.add(item)
        db.session.commit()
        return item.id


def _conferir_com_falta(client, item_id, numero="7001", contado="7"):
    return client.post("/validar", json={
        "nota": numero,
        "contagens": {str(item_id): contado},
        "forcar_pendencia": True,
        "motivos_itens": {str(item_id): "Falta de item"},
        "motivos_tipos": {str(item_id): "Falta de item"},
        "destinos_itens": {str(item_id): "Quarentena"},
        "checklist": {"lacre_ok": True, "volumes_ok": True, "avaria_visual": True, "etiqueta_ok": True},
    })


def _abrir(app):
    """Conferência com falta de 3 -> ocorrência aberta. Devolve o id."""
    client = app.test_client()
    login_admin(client)
    item_id = _nota(app)
    with patch(TEAMS) as teams:
        resp = _conferir_com_falta(client, item_id)
    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()["pendencia_confirmada"] is True
    with app.app_context():
        oc = OcorrenciaRecebimento.query.one()
        return client, oc.id, teams


def test_conferencia_com_pendencia_abre_ocorrencia_e_avisa_no_teams(tmp_path):
    app = build_test_app(tmp_path)
    _client, oc_id, teams = _abrir(app)

    with app.app_context():
        oc = db.session.get(OcorrenciaRecebimento, oc_id)
        assert (oc.numero_nota, oc.fornecedor, oc.pedido_compra, oc.origem, oc.status) == ("7001", "Aços Brasil", "4500", "Conferencia", "Aberta")
        assert len(oc.itens) == 1
        assert (oc.itens[0].qtd_esperada, oc.itens[0].qtd_contada) == (10.0, 7.0)
        assert [e.tipo for e in oc.eventos] == ["Abertura"]

    teams.assert_called_once()
    assert teams.call_args.args[0] == "aberta"
    kwargs = teams.call_args.kwargs
    assert kwargs["numero_nota"] == "7001"
    assert "faltam 3" in kwargs["linhas"][0]


def test_nova_conferencia_da_mesma_nf_junta_na_ocorrencia_aberta(tmp_path):
    app = build_test_app(tmp_path)
    client, oc_id, _ = _abrir(app)
    with app.app_context():
        item_id = ItemNota.query.filter_by(numero_nota="7001").one().id
        ItemNota.query.filter_by(numero_nota="7001").update({"status": "Pendente"})
        db.session.commit()

    with patch(TEAMS) as teams:
        resp = _conferir_com_falta(client, item_id, contado="8")
    assert resp.status_code == 200, resp.get_json()

    with app.app_context():
        assert OcorrenciaRecebimento.query.count() == 1
        oc = db.session.get(OcorrenciaRecebimento, oc_id)
        assert len(oc.itens) == 2
        assert [e.tipo for e in oc.eventos] == ["Abertura", "NovosItens"]
    assert teams.call_args.args[0] == "novos_itens"


def test_abrir_ocorrencia_nao_derruba_quando_teams_falha(tmp_path):
    app = build_test_app(tmp_path)
    _nota(app)
    with app.app_context(), patch(TEAMS, side_effect=RuntimeError("webhook fora")):
        oc = svc.abrir_ocorrencia("7001", [{"descricao": "X", "qtd_esperada": 1, "qtd_contada": 0}], "ana")
        assert oc is not None and oc.status == "Aberta"


def test_compras_atualiza_tratativa_e_resolve(tmp_path):
    app = build_test_app(tmp_path)
    client, oc_id, _ = _abrir(app)

    with patch(TEAMS) as teams:
        resp = client.post(f"/api/compras/divergencias-recebimento/{oc_id}/atualizar", json={
            "acao": "Reposicao", "status": "AguardandoFornecedor", "responsavel": "Bruna",
            "previsao": "2026-10-10", "comentario": "Fornecedor envia as 3 peças na próxima coleta",
        })
    assert resp.status_code == 200, resp.get_json()
    dados = resp.get_json()
    assert (dados["status"], dados["acao"], dados["responsavel"], dados["previsao"]) == ("AguardandoFornecedor", "Reposicao", "Bruna", "2026-10-10")
    assert dados["eventos"][-1]["comentario"].startswith("Fornecedor envia")
    teams.assert_not_called()  # só avisa em devolução total e no encerramento

    sem_mudanca = client.post(f"/api/compras/divergencias-recebimento/{oc_id}/atualizar", json={})
    assert sem_mudanca.status_code == 400

    with patch(TEAMS) as teams:
        resp = client.post(f"/api/compras/divergencias-recebimento/{oc_id}/atualizar", json={"status": "Resolvida", "comentario": "Chegou"})
    assert resp.get_json()["finalizada"] is True
    assert teams.call_args.args[0] == "encerrada"

    encerrada = client.post(f"/api/compras/divergencias-recebimento/{oc_id}/atualizar", json={"comentario": "de novo"})
    assert encerrada.status_code == 400


def test_regras_de_validacao_da_tratativa(tmp_path):
    app = build_test_app(tmp_path)
    client, oc_id, _ = _abrir(app)
    url = f"/api/compras/divergencias-recebimento/{oc_id}/atualizar"

    assert client.post(url, json={"acao": "Outro"}).status_code == 400  # "Outro" sem comentário
    assert client.post(url, json={"status": "Cancelada", "comentario": "x"}).status_code == 400  # cancelar sem motivo
    assert client.post(url, json={"status": "Inventado"}).status_code == 400
    assert client.post(url, json={"previsao": "10/10/2026"}).status_code == 400
    assert client.get("/api/compras/divergencias-recebimento/999").status_code == 404


def test_devolucao_total_passa_pelo_fiscal(tmp_path):
    app = build_test_app(tmp_path)
    client, oc_id, _ = _abrir(app)
    base = f"/api/compras/divergencias-recebimento/{oc_id}"

    # Fiscal antes da decisão: não há etapa.
    assert client.post(f"{base}/fiscal", json={"tipo": "Recusa"}).status_code == 400

    with patch(TEAMS) as teams:
        resp = client.post(f"{base}/atualizar", json={"acao": "DevolucaoTotal", "status": "EmTratativa", "comentario": "Material fora de especificação"})
    assert resp.get_json()["status"] == "AguardandoFiscal"
    assert teams.call_args.args[0] == "aguardando_fiscal"

    # Compras não encerra devolução total.
    assert client.post(f"{base}/atualizar", json={"status": "Resolvida", "comentario": "ok"}).status_code == 400
    # NF de devolução exige o número.
    assert client.post(f"{base}/fiscal", json={"tipo": "NFDevolucao"}).status_code == 400
    # ...e a NF lançada antes.
    nao_lancada = client.post(f"{base}/fiscal", json={"tipo": "NFDevolucao", "numero_nf": "12345"})
    assert nao_lancada.status_code == 400
    assert "precisa estar lançada" in nao_lancada.get_json()["error"]
    assert client.get(base).get_json()["nf_lancada"] is False
    with app.app_context():
        ItemNota.query.filter_by(numero_nota="7001").update({"status": "Lançado"})
        db.session.commit()
    assert client.get(base).get_json()["nf_lancada"] is True

    with patch(TEAMS) as teams:
        resp = client.post(f"{base}/fiscal", json={"tipo": "NFDevolucao", "numero_nf": "12345", "data": "2026-10-06", "observacao": "Saiu na coleta"})
    assert resp.status_code == 200, resp.get_json()
    dados = resp.get_json()
    assert dados["status"] == "Resolvida"
    assert (dados["fiscal"]["tipo"], dados["fiscal"]["numero_nf"], dados["fiscal"]["data"]) == ("NFDevolucao", "12345", "2026-10-06")
    assert dados["eventos"][-1]["tipo"] == "Fiscal"
    assert teams.call_args.args[0] == "encerrada"


def test_compras_pode_desistir_da_devolucao_total(tmp_path):
    app = build_test_app(tmp_path)
    client, oc_id, _ = _abrir(app)
    url = f"/api/compras/divergencias-recebimento/{oc_id}/atualizar"
    with patch(TEAMS):
        client.post(url, json={"acao": "DevolucaoTotal", "comentario": "devolver"})
        resp = client.post(url, json={"acao": "NotaDebito", "comentario": "Fornecedor aceitou abater"})
    assert resp.get_json()["status"] == "EmTratativa"


def test_lembrete_so_para_ocorrencia_parada(tmp_path):
    app = build_test_app(tmp_path)
    _abrir(app)
    with app.app_context():
        with patch(TEAMS) as teams:
            assert svc.enviar_lembretes(dias=3)["enviados"] == 0
        teams.assert_not_called()

        daqui_4_dias = agora_br() + timedelta(days=4)
        with patch(TEAMS) as teams:
            resultado = svc.enviar_lembretes(agora=daqui_4_dias, dias=3)
        assert resultado["enviados"] == 1
        assert teams.call_args.args[0] == "lembrete"
        assert teams.call_args.kwargs["sync"] is True
        # Não repete no dia seguinte; repete depois de mais 3 dias.
        with patch(TEAMS):
            assert svc.enviar_lembretes(agora=daqui_4_dias + timedelta(days=1), dias=3)["enviados"] == 0
            assert svc.enviar_lembretes(agora=daqui_4_dias + timedelta(days=3), dias=3)["enviados"] == 1


def test_painel_renderiza_e_respeita_permissoes(tmp_path):
    app = build_test_app(tmp_path)
    client, oc_id, _ = _abrir(app)

    pagina = client.get("/compras/divergencias-recebimento")
    assert pagina.status_code == 200
    html = pagina.get_data(as_text=True)
    assert "Divergências de recebimento" in html
    assert "const PODE_TRATAR = true" in html
    assert 'href="/compras/divergencias-recebimento"' in html  # link no menu

    lista = client.get("/api/compras/divergencias-recebimento?abertas=1").get_json()
    assert [o["id"] for o in lista["ocorrencias"]] == [oc_id]
    assert lista["contagem"]["Aberta"] == 1
    assert client.get("/api/compras/divergencias-recebimento?busca=Aços").get_json()["ocorrencias"]
    assert not client.get("/api/compras/divergencias-recebimento?busca=zzz").get_json()["ocorrencias"]

    # Portaria não vê o painel.
    outro = app.test_client()
    set_logged_user(outro, "portaria_x", "Portaria")
    assert outro.get("/api/compras/divergencias-recebimento").status_code in (302, 403)


def test_documento_entrada_mostra_tratativa_de_compras(tmp_path):
    app = build_test_app(tmp_path)
    client, oc_id, _ = _abrir(app)
    with patch(TEAMS):
        client.post(f"/api/compras/divergencias-recebimento/{oc_id}/atualizar",
                    json={"acao": "Reposicao", "responsavel": "Bruna", "comentario": "Cobrado"})
    with app.app_context():
        from conferencia_app.routes.api_routes import _build_documento_entrada_pendencias

        itens = ItemNota.query.filter_by(numero_nota="7001").all()
        pendencias = _build_documento_entrada_pendencias("7001", itens, "4500")
    tratativa = next(p for p in pendencias if p["tipo"] == "tratativa_compras")
    assert "Cobrar reposição do fornecedor" in tratativa["descricao"]
    assert "Bruna" in tratativa["descricao"]
    assert "Cobrado" in tratativa["descricao"]


def test_lancamento_bloqueado_ate_compras_decidir(tmp_path):
    app = build_test_app(tmp_path)
    client, oc_id, _ = _abrir(app)
    with app.app_context():
        ItemNota.query.filter_by(numero_nota="7001").update({"status": "Concluído"})
        db.session.commit()
        from conferencia_app.routes.api_routes import _build_documento_entrada_pendencias

        pendencias = _build_documento_entrada_pendencias("7001", ItemNota.query.filter_by(numero_nota="7001").all(), "4500")
    bloqueio = next(p for p in pendencias if p["tipo"] == "tratativa_compras")
    assert bloqueio["bloqueia_lancamento"] is True and bloqueio["severidade"] == "alta"

    lancar = {"nota": "7001", "codigo": "ERP-7001", "manifestar_destinatario": False}
    resp = client.post("/api/confirmar_lancamento", json=lancar)
    assert resp.status_code == 400
    assert "aguardando decisão de Compras" in resp.get_json()["msg"]

    # Só mudar a situação não é decidir: continua bloqueado.
    with patch(TEAMS):
        client.post(f"/api/compras/divergencias-recebimento/{oc_id}/atualizar", json={"status": "EmTratativa"})
    assert client.post("/api/confirmar_lancamento", json=lancar).status_code == 400

    with patch(TEAMS):
        client.post(f"/api/compras/divergencias-recebimento/{oc_id}/atualizar", json={"acao": "Reposicao"})
    with app.app_context():
        assert svc.lancamento_bloqueado("7001") is None
    # O aviso de entrada de chapa consulta o bridge do ERP: fora da rede no teste.
    with patch("conferencia_app.services.entrada_chapa_email_service.notificar_entrada_chapa_lancada", return_value={}):
        resp = client.post("/api/confirmar_lancamento", json=lancar)
    assert resp.status_code == 200, resp.get_json()


def _estornar_conferencia(client):
    return client.post("/api/fiscal/estornar_conferencia", json={"nota": "7001", "motivo": "Contagem errada"})


def test_estorno_de_conferencia_sem_decisao_cancela_e_nova_conferencia_abre_outra(tmp_path):
    app = build_test_app(tmp_path)
    client, oc_id, _ = _abrir(app)

    with patch(TEAMS) as teams:
        resp = _estornar_conferencia(client)
    assert resp.status_code == 200, resp.get_json()
    assert teams.call_args.args[0] == "encerrada"
    with app.app_context():
        oc = db.session.get(OcorrenciaRecebimento, oc_id)
        assert oc.status == "Cancelada"
        assert oc.eventos[-1].tipo == "Estorno" and "Contagem errada" in oc.eventos[-1].comentario
        # Não trava mais o lançamento.
        assert svc.lancamento_bloqueado("7001") is None
        item_id = ItemNota.query.filter_by(numero_nota="7001").one().id

    # Recontagem com diferença de novo: ocorrência nova.
    with patch(TEAMS) as teams:
        assert _conferir_com_falta(client, item_id, contado="9").status_code == 200
    assert teams.call_args.args[0] == "aberta"
    with app.app_context():
        assert OcorrenciaRecebimento.query.count() == 2


def test_estorno_de_conferencia_com_decisao_mantem_e_pede_reavaliacao(tmp_path):
    app = build_test_app(tmp_path)
    client, oc_id, _ = _abrir(app)
    with patch(TEAMS):
        client.post(f"/api/compras/divergencias-recebimento/{oc_id}/atualizar", json={"acao": "Reposicao"})
        with patch(TEAMS) as teams:
            assert _estornar_conferencia(client).status_code == 200
    assert teams.call_args.args[0] == "conferencia_estornada"
    with app.app_context():
        oc = db.session.get(OcorrenciaRecebimento, oc_id)
        assert (oc.status, oc.acao) == ("Aberta", "Reposicao")
        assert oc.eventos[-1].tipo == "Estorno"


def test_estorno_de_lancamento_bloqueado_depois_da_nf_de_devolucao(tmp_path):
    app = build_test_app(tmp_path)
    client, oc_id, _ = _abrir(app)
    with app.app_context():
        ItemNota.query.filter_by(numero_nota="7001").update({"status": "Lançado", "numero_lancamento": "ERP-1"})
        db.session.commit()
    estornar = {"nota": "7001", "motivo": "Lançado errado"}

    # Antes da devolução: estorna e registra no histórico.
    with patch(TEAMS):
        client.post(f"/api/compras/divergencias-recebimento/{oc_id}/atualizar", json={"acao": "DevolucaoTotal"})
    assert client.post("/api/fiscal/estornar_lancamento", json=estornar).status_code == 200
    with app.app_context():
        oc = db.session.get(OcorrenciaRecebimento, oc_id)
        assert oc.eventos[-1].tipo == "Estorno" and "Lançado errado" in oc.eventos[-1].comentario
        ItemNota.query.filter_by(numero_nota="7001").update({"status": "Lançado", "numero_lancamento": "ERP-1"})
        db.session.commit()

    with patch(TEAMS):
        resp = client.post(f"/api/compras/divergencias-recebimento/{oc_id}/fiscal", json={"tipo": "NFDevolucao", "numero_nf": "555"})
    assert resp.status_code == 200, resp.get_json()

    bloqueado = client.post("/api/fiscal/estornar_lancamento", json=estornar)
    assert bloqueado.status_code == 400
    assert "555" in bloqueado.get_json()["msg"]
    with app.app_context():
        assert ItemNota.query.filter_by(numero_nota="7001").one().status == "Lançado"


def test_estornar_resolucao_de_compras_volta_para_tratativa(tmp_path):
    app = build_test_app(tmp_path)
    client, oc_id, _ = _abrir(app)
    base = f"/api/compras/divergencias-recebimento/{oc_id}"
    with patch(TEAMS):
        client.post(f"{base}/atualizar", json={"acao": "Reposicao", "status": "Resolvida"})

    assert client.post(f"{base}/estornar", json={"motivo": "x"}).status_code == 400  # motivo curto
    with patch(TEAMS) as teams:
        resp = client.post(f"{base}/estornar", json={"motivo": "Reposição chegou incompleta"})
    assert resp.status_code == 200, resp.get_json()
    dados = resp.get_json()
    assert (dados["status"], dados["acao"], dados["resolvida_em"]) == ("EmTratativa", "Reposicao", None)
    assert dados["eventos"][-1]["tipo"] == "Reabertura"
    assert teams.call_args.args[0] == "reaberta"
    # Aberta não se estorna.
    assert client.post(f"{base}/estornar", json={"motivo": "de novo agora"}).status_code == 400


def test_estornar_resolucao_do_fiscal_volta_para_aguardando_fiscal(tmp_path):
    app = build_test_app(tmp_path)
    client, oc_id, _ = _abrir(app)
    base = f"/api/compras/divergencias-recebimento/{oc_id}"
    with app.app_context():
        ItemNota.query.filter_by(numero_nota="7001").update({"status": "Lançado", "numero_lancamento": "ERP-1"})
        db.session.commit()
    with patch(TEAMS):
        client.post(f"{base}/atualizar", json={"acao": "DevolucaoTotal"})
        client.post(f"{base}/fiscal", json={"tipo": "NFDevolucao", "numero_nf": "555"})
    assert client.post("/api/fiscal/estornar_lancamento", json={"nota": "7001", "motivo": "teste"}).status_code == 400

    # Compras (sem PAGE_LANCAMENTO) não desfaz a etapa do Fiscal.
    compras = app.test_client()
    set_logged_user(compras, "bruna", "Compras")
    assert compras.post(f"{base}/estornar", json={"motivo": "NF de devolução errada"}).status_code == 403

    with patch(TEAMS):
        resp = client.post(f"{base}/estornar", json={"motivo": "NF de devolução errada"})
    dados = resp.get_json()
    assert (dados["status"], dados["fiscal"]) == ("AguardandoFiscal", None)
    assert "555" in dados["eventos"][-1]["comentario"]
    # Sem a NF de devolução registrada, o lançamento volta a poder ser estornado.
    with app.app_context():
        assert svc.estorno_lancamento_bloqueado("7001") is None
