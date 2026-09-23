"""Homologacao de fornecedor - self assessment: o comprador manda um link
por e-mail, o fornecedor responde sem login (com evidencia obrigatoria nos
itens Sim/Parcial/Conforme) e a homologacao volta pro comprador revisar."""
from datetime import datetime, timedelta
from io import BytesIO
from unittest.mock import patch

from tests.test_app import build_test_app, login_admin

SMTP = "conferencia_app.routes.compras_homologacao_routes.enviar_mensagem_smtp"


def _criar_homologacao(client, **extra):
    dados = {"razao_social": "ACOS EXEMPLO LTDA", "cnpj": "11.222.333/0001-81", "email": "fiscal@contador.com.br"}
    dados.update(extra)
    resp = client.post("/api/compras/homologacao", json=dados)
    assert resp.status_code == 200
    return resp.get_json()["homologacao"]["id"]


def _enviar_link(client, hid, email="qualidade@fornecedor.com.br"):
    with patch(SMTP) as smtp:
        resp = client.post(f"/api/compras/homologacao/{hid}/enviar-fornecedor", json={"email": email})
    assert resp.status_code == 200, resp.get_json()
    corpo = resp.get_json()
    token = corpo["link"].rsplit("/", 1)[-1]
    return token, corpo, smtp


def _respostas(valor_questionario="Sim", valor_legal="Conforme"):
    from conferencia_app.services import compras_homologacao_form as form

    return [
        {"secao": s["chave"], "item": i,
         "resposta": valor_legal if s["escala"] == form.RESPOSTAS_LEGAL else valor_questionario}
        for s in form.SECOES for i, _ in enumerate(s["itens"], start=1)
    ]


def _anexar(client, token, secao, item, nome="evidencia.pdf", conteudo=b"%PDF-1.4 teste"):
    return client.post(
        f"/api/homologacao-fornecedor/{token}/evidencias",
        data={"secao": secao, "item": str(item), "arquivo": (BytesIO(conteudo), nome)},
        content_type="multipart/form-data",
    )


def test_self_assessment_fluxo_completo_ate_aprovacao(tmp_path):
    app = build_test_app(tmp_path)
    comprador = app.test_client()
    login_admin(comprador)
    hid = _criar_homologacao(comprador)

    token, corpo, smtp = _enviar_link(comprador, hid)
    assert corpo["email_enviado"] is True
    assert "/homologacao-fornecedor/" in corpo["link"]
    msg = smtp.call_args[0][1]
    assert msg["To"] == "qualidade@fornecedor.com.br"
    assert corpo["link"] in msg.get_payload()[0].get_payload(decode=True).decode("utf-8")
    h = corpo["homologacao"]
    assert h["status"] == "Com fornecedor"
    assert h["convite"]["situacao"] == "pendente"
    # E-mail do cabecalho (do cartao CNPJ) nao e' sobrescrito pelo destinatario.
    assert h["email"] == "fiscal@contador.com.br"

    # Enquanto esta com o fornecedor, o comprador nao edita.
    assert comprador.put(f"/api/compras/homologacao/{hid}", json={"comentario": "x"}).status_code == 400

    # Fornecedor: sem login nenhum.
    fornecedor = app.test_client()
    assert fornecedor.get(f"/homologacao-fornecedor/{token}").status_code == 200
    dados = fornecedor.get(f"/api/homologacao-fornecedor/{token}").get_json()
    assert dados["editavel"] is True
    assert dados["cadastro"]["razao_social"] == "ACOS EXEMPLO LTDA"
    assert "resultado_auditoria" not in dados["contato"]

    # Salva rascunho: campo interno (resultado_auditoria) e' ignorado.
    salvo = fornecedor.put(f"/api/homologacao-fornecedor/{token}", json={
        "contato_principal": "Maria Qualidade", "resultado_auditoria": "HACK",
        "respostas": _respostas(),
    })
    assert salvo.status_code == 200

    # Enviar sem evidencia: bloqueado, e continua editavel.
    sem_evid = fornecedor.post(f"/api/homologacao-fornecedor/{token}/enviar", json={})
    assert sem_evid.status_code == 400
    assert "evidência" in sem_evid.get_json()["error"]

    # Item respondido "Nao" dispensa evidencia.
    from conferencia_app.services import compras_homologacao_form as form

    fornecedor.put(f"/api/homologacao-fornecedor/{token}", json={
        "respostas": [{"secao": form.SECAO_SEGURANCA, "item": 3, "resposta": "Nao"}],
    })
    for s in form.SECOES:
        for i, _ in enumerate(s["itens"], start=1):
            if (s["chave"], i) == (form.SECAO_SEGURANCA, 3):
                continue
            assert _anexar(fornecedor, token, s["chave"], i).status_code == 200

    # Comprador (quem mandou o link) recebe o aviso da resposta.
    from conferencia_app.extensions import db
    from conferencia_app.models import Usuario

    with app.app_context():
        enviado_por = comprador.get(f"/api/compras/homologacao/{hid}").get_json()["homologacao"]["convite"]["enviado_por"]
        usuario = Usuario.query.filter(db.func.lower(Usuario.username) == enviado_por.lower()).first()
        usuario.email = "comprador@columbiamachine.com.br"
        db.session.commit()
    with patch(SMTP) as smtp:
        enviado = fornecedor.post(f"/api/homologacao-fornecedor/{token}/enviar", json={})
    assert enviado.status_code == 200, enviado.get_json()
    aviso = smtp.call_args[0][1]
    assert aviso["To"] == "comprador@columbiamachine.com.br"
    assert "ACOS EXEMPLO LTDA" in aviso["Subject"]
    assert "Rascunho" in aviso.get_payload()[0].get_payload(decode=True).decode("utf-8")
    assert enviado.get_json()["editavel"] is False
    assert enviado.get_json()["situacao"] == "respondido"

    # Link respondido nao aceita mais nada.
    assert fornecedor.put(f"/api/homologacao-fornecedor/{token}", json={"telefone": "1"}).status_code == 400
    assert _anexar(fornecedor, token, form.SECAO_LEGAL, 1).status_code == 400

    # Volta pro comprador, em rascunho, com respostas, contato e evidencias.
    h = comprador.get(f"/api/compras/homologacao/{hid}").get_json()["homologacao"]
    assert h["status"] == "Rascunho"
    assert h["convite"]["situacao"] == "respondido"
    assert h["contato_principal"] == "Maria Qualidade"
    assert h["resultado_auditoria"] is None
    assert h["itens_faltando"] == 0 and h["itens_sem_evidencia"] == 0
    evid = h["secoes"][0]["itens"][0]["evidencias"][0]
    baixada = comprador.get(evid["url"])
    assert baixada.status_code == 200
    assert baixada.mimetype == "application/pdf"
    assert round(h["nota"], 6) == round(1 - 0.15 / 3, 6)

    assert comprador.post(f"/api/compras/homologacao/{hid}/enviar", json={}).status_code == 200


def test_self_assessment_reenvio_cancelamento_e_expiracao_derrubam_o_link(tmp_path):
    from conferencia_app.extensions import db
    from conferencia_app.models import ComprasHomologacaoConvite

    app = build_test_app(tmp_path)
    comprador = app.test_client()
    login_admin(comprador)
    hid = _criar_homologacao(comprador)
    fornecedor = app.test_client()

    token_velho, _, _ = _enviar_link(comprador, hid)
    token_novo, corpo, _ = _enviar_link(comprador, hid, email="outro@fornecedor.com.br")
    assert corpo["homologacao"]["convite"]["email"] == "outro@fornecedor.com.br"
    assert fornecedor.get(f"/api/homologacao-fornecedor/{token_velho}").get_json()["editavel"] is False
    assert fornecedor.put(f"/api/homologacao-fornecedor/{token_velho}", json={"telefone": "1"}).status_code == 400
    assert fornecedor.put(f"/api/homologacao-fornecedor/{token_novo}", json={"telefone": "1"}).status_code == 200

    # Expirado: link morre mesmo com status "Com fornecedor".
    with app.app_context():
        convite = ComprasHomologacaoConvite.query.order_by(ComprasHomologacaoConvite.id.desc()).first()
        convite.expira_em = datetime.now() - timedelta(minutes=1)
        db.session.commit()
    expirado = fornecedor.put(f"/api/homologacao-fornecedor/{token_novo}", json={"telefone": "2"})
    assert expirado.status_code == 400
    assert "expirou" in expirado.get_json()["error"]
    h = comprador.get(f"/api/compras/homologacao/{hid}").get_json()["homologacao"]
    assert h["convite"]["situacao"] == "expirado"

    # Cancelar: volta pra rascunho e o comprador edita de novo.
    cancelado = comprador.post(f"/api/compras/homologacao/{hid}/cancelar-fornecedor", json={})
    assert cancelado.get_json()["homologacao"]["status"] == "Rascunho"
    assert comprador.put(f"/api/compras/homologacao/{hid}", json={"comentario": "ok"}).status_code == 200

    # Token inexistente.
    assert fornecedor.get("/api/homologacao-fornecedor/nao-existe").status_code == 404
    assert fornecedor.get("/homologacao-fornecedor/nao-existe").status_code == 404


def test_self_assessment_validacoes_e_isolamento_entre_fornecedores(tmp_path):
    from conferencia_app.services import compras_homologacao_form as form

    app = build_test_app(tmp_path)
    comprador = app.test_client()
    login_admin(comprador)

    # CNPJ e e-mail valido sao obrigatorios pro envio.
    sem_cnpj = _criar_homologacao(comprador, cnpj="")
    r = comprador.post(f"/api/compras/homologacao/{sem_cnpj}/enviar-fornecedor", json={"email": "a@b.com"})
    assert r.status_code == 400 and "CNPJ" in r.get_json()["error"]
    hid_a = _criar_homologacao(comprador)
    r = comprador.post(f"/api/compras/homologacao/{hid_a}/enviar-fornecedor", json={"email": "invalido"})
    assert r.status_code == 400

    hid_b = _criar_homologacao(comprador, razao_social="OUTRO FORNECEDOR")
    token_a, _, _ = _enviar_link(comprador, hid_a)
    token_b, _, _ = _enviar_link(comprador, hid_b)
    fornecedor = app.test_client()

    # So' PDF/JPG/PNG; tipo vem da extensao, nao do navegador.
    assert _anexar(fornecedor, token_a, form.SECAO_LEGAL, 1, nome="x.html", conteudo=b"<script>").status_code == 400
    assert _anexar(fornecedor, token_a, form.SECAO_LEGAL, 99).status_code == 400
    ok = _anexar(fornecedor, token_a, form.SECAO_LEGAL, 1, nome="foto.PNG", conteudo=b"\x89PNG")
    assert ok.status_code == 200
    evid_id = ok.get_json()["secoes"][0]["itens"][0]["evidencias"][0]["id"]

    # O link do fornecedor B nao enxerga nem remove evidencia do A.
    assert fornecedor.get(f"/api/homologacao-fornecedor/{token_b}/evidencias/{evid_id}").status_code == 404
    assert fornecedor.delete(f"/api/homologacao-fornecedor/{token_b}/evidencias/{evid_id}").status_code == 404
    baixada = fornecedor.get(f"/api/homologacao-fornecedor/{token_a}/evidencias/{evid_id}")
    assert baixada.status_code == 200 and baixada.mimetype == "image/png"
    assert fornecedor.delete(f"/api/homologacao-fornecedor/{token_a}/evidencias/{evid_id}").status_code == 200

    # Rota interna de evidencia exige login.
    assert app.test_client().get(f"/api/compras/homologacao/evidencias/{evid_id}").status_code in (302, 401, 403)


def test_self_assessment_falha_no_email_devolve_o_link(tmp_path):
    app = build_test_app(tmp_path)
    comprador = app.test_client()
    login_admin(comprador)
    hid = _criar_homologacao(comprador)
    with patch(SMTP, side_effect=RuntimeError("SMTP fora")):
        resp = comprador.post(f"/api/compras/homologacao/{hid}/enviar-fornecedor", json={"email": "a@b.com.br"})
    corpo = resp.get_json()
    assert resp.status_code == 200
    assert corpo["email_enviado"] is False
    assert "NÃO foi enviado" in corpo["message"]
    assert corpo["link"]
    token = corpo["link"].rsplit("/", 1)[-1]
    assert app.test_client().get(f"/api/homologacao-fornecedor/{token}").get_json()["editavel"] is True


def test_self_assessment_falha_no_aviso_ao_comprador_nao_perde_o_envio(tmp_path):
    """SMTP fora na hora do aviso: o envio do fornecedor continua gravado."""
    from conferencia_app.services import compras_homologacao_form as form

    app = build_test_app(tmp_path)
    comprador = app.test_client()
    login_admin(comprador)
    hid = _criar_homologacao(comprador)
    token, _, _ = _enviar_link(comprador, hid)
    fornecedor = app.test_client()
    # Tudo "Nao": nenhum item exige evidencia.
    fornecedor.put(f"/api/homologacao-fornecedor/{token}",
                   json={"respostas": _respostas(valor_questionario="Nao", valor_legal="Nao Conforme")})
    with patch(SMTP, side_effect=RuntimeError("SMTP fora")):
        enviado = fornecedor.post(f"/api/homologacao-fornecedor/{token}/enviar", json={})
    assert enviado.status_code == 200
    h = comprador.get(f"/api/compras/homologacao/{hid}").get_json()["homologacao"]
    assert h["status"] == "Rascunho"
    assert h["convite"]["situacao"] == "respondido"
    assert form.RESPOSTA_NAO_CONFORME == h["secoes"][0]["itens"][0]["resposta"]
