"""Expedição de ordem faturada SEM conferência cega.

Caminho de exceção: a NF saiu faturada sem o material ter sido conferido.
Como não houve conferência, não existe a foto do cliente que o romaneio exige
— em troca, quem libera precisa justificar, e a marca acompanha a NF até o
Registro de expedição, que nasce finalizado (não há canhoto a esperar).
"""
from datetime import datetime
from unittest.mock import patch

import pytest

from test_app import build_test_app, set_logged_user
from conferencia_app.extensions import db
from conferencia_app.models import (
    ExpedicaoConferenciaSimples,
    ExpedicaoOrdemFat,
    ExpedicaoOrdemST,
    ExpedicaoRomaneio,
    ExpedicaoRomaneioNF,
)
from conferencia_app.services import expedicao_fat_service as fat_svc
from conferencia_app.services import expedicao_st_service as st_svc

NF = "900123"
COD = 4321
MOTIVO = "Cliente retirou no contraturno, sem equipe de conferência no pátio."


def criar_ordem(status=None, numero_nf=NF, cod=COD):
    ordem = ExpedicaoOrdemFat(
        cod_ordem_fat=cod,
        codigo_interno=f"OF-{cod}",
        cliente="CLIENTE TESTE",
        orcamento="ORC-77",
        numero_nf=numero_nf,
        status=status or fat_svc.STATUS_FATURADO_SEM_CONF,
        faturado_at=datetime.now(),
    )
    db.session.add(ordem)
    db.session.commit()
    return ordem


def sem_bridge():
    return patch(
        "conferencia_app.routes.expedicao_romaneio_routes._dados_nf_do_bridge",
        return_value={},
    )


def url(cod=COD):
    return f"/api/expedicao/conf-cega/ordens/{cod}/expedir-sem-conferencia"


def test_foto_do_cliente_ainda_bloqueia_o_caminho_normal(tmp_path):
    """Baseline: sem o flag, a NF não entra no romaneio. É o que a ação contorna."""
    app = build_test_app(tmp_path)
    client = app.test_client()
    set_logged_user(client, "admin", "Admin")
    with app.app_context():
        criar_ordem(status=fat_svc.STATUS_FATURADO)
    with sem_bridge():
        resp = client.post(
            "/api/expedicao/romaneio-fat",
            json={"nfs": [{"numero_nf": NF}], "tipo_frete": "FOB"},
        )
    assert resp.status_code == 400
    assert "foto do cliente" in resp.get_json()["error"]


def test_cria_o_romaneio_e_carimba_a_marca(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()
    set_logged_user(client, "admin", "Admin")
    with app.app_context():
        criar_ordem()
    with sem_bridge():
        resp = client.post(url(), json={"motivo": MOTIVO})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    corpo = resp.get_json()
    assert corpo["numero_romaneio"].startswith("ROM-")

    with app.app_context():
        ordem = ExpedicaoOrdemFat.query.filter_by(cod_ordem_fat=COD).one()
        assert ordem.expedido_sem_conferencia is True
        assert ordem.expedido_sem_conferencia_motivo == MOTIVO
        # A ordem avança no fluxo normal: entrou num romaneio.
        assert ordem.status == fat_svc.STATUS_EM_ROMANEIO

        romaneio = db.session.get(ExpedicaoRomaneio, corpo["romaneio_id"])
        assert romaneio.status == "Rascunho"
        assert MOTIVO in (romaneio.observacao_1 or "")

        nf = ExpedicaoRomaneioNF.query.filter_by(romaneio_id=romaneio.id).one()
        assert nf.numero_nf == NF
        assert nf.sem_conferencia is True
        assert nf.sem_conferencia_motivo == MOTIVO


def test_motivo_e_obrigatorio(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()
    set_logged_user(client, "admin", "Admin")
    with app.app_context():
        criar_ordem()
    for corpo in ({}, {"motivo": "   "}):
        resp = client.post(url(), json=corpo)
        assert resp.status_code == 400
        assert "sem conferência" in resp.get_json()["error"]
    with app.app_context():
        # Nada foi marcado nem criado.
        assert ExpedicaoOrdemFat.query.one().expedido_sem_conferencia is False
        assert ExpedicaoRomaneio.query.count() == 0


@pytest.mark.parametrize(
    "status",
    [
        fat_svc.STATUS_PENDENTE,
        fat_svc.STATUS_CONFERIDO,
        fat_svc.STATUS_FATURADO,
        fat_svc.STATUS_EM_ROMANEIO,
    ],
)
def test_so_vale_para_ordem_faturada_sem_conferencia(tmp_path, status):
    app = build_test_app(tmp_path)
    client = app.test_client()
    set_logged_user(client, "admin", "Admin")
    with app.app_context():
        criar_ordem(status=status)
    resp = client.post(url(), json={"motivo": MOTIVO})
    assert resp.status_code == 400
    with app.app_context():
        assert ExpedicaoRomaneio.query.count() == 0


def test_ordem_sem_nf_nao_vira_romaneio(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()
    set_logged_user(client, "admin", "Admin")
    with app.app_context():
        criar_ordem(numero_nf="")
    resp = client.post(url(), json={"motivo": MOTIVO})
    assert resp.status_code == 400
    assert "NF" in resp.get_json()["error"]


def test_quem_abre_a_tela_consegue_usar(tmp_path):
    """Fiscal enxerga a Conferência de Expedição mas não gerencia romaneios —
    ainda assim precisa conseguir liberar esta saída."""
    app = build_test_app(tmp_path)
    client = app.test_client()
    set_logged_user(client, "fiscal", "Fiscal")
    with app.app_context():
        criar_ordem()
    with sem_bridge():
        resp = client.post(url(), json={"motivo": MOTIVO})
    assert resp.status_code == 200

    sem_acesso = app.test_client()
    set_logged_user(sem_acesso, "portaria", "Portaria")
    with app.app_context():
        criar_ordem(cod=COD + 1, numero_nf="900999")
    assert sem_acesso.post(url(COD + 1), json={"motivo": MOTIVO}).status_code == 403


def test_registro_de_expedicao_nasce_finalizado_sem_foto(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()
    set_logged_user(client, "admin", "Admin")
    with app.app_context():
        criar_ordem()
    with sem_bridge(), \
         patch("conferencia_app.routes.expedicao_romaneio_routes._gerar_solicitacao_entrega_cif",
               return_value=(False, None)), \
         patch("conferencia_app.routes.expedicao_romaneio_routes._gerar_viagem_automatica_st",
               return_value=(False, None)):
        romaneio_id = client.post(url(), json={"motivo": MOTIVO}).get_json()["romaneio_id"]
        base = f"/api/expedicao/romaneio-fat/{romaneio_id}"
        # Finaliza direto: a NF sem conferência fica fora da checagem de
        # modalidade de frete, então não há confirmação de CC-e a dar.
        finalizacao = client.post(base + "/finalizar")
        assert finalizacao.status_code == 200
        assert not finalizacao.get_json().get("requer_confirmacao")
        assert client.post(base + "/expedir").status_code == 200

    with app.app_context():
        registro = ExpedicaoConferenciaSimples.query.filter_by(numero_nf=NF).one()
        assert registro.status == "Finalizado"
        assert registro.finalizado_at is not None
        assert registro.sem_conferencia is True
        assert registro.sem_conferencia_justificativa == MOTIVO
        # Não há foto nem canhoto: é o ponto da exceção.
        assert registro.foto_cliente_file_name is None
        assert registro.canhoto_file_name is None

    # O romaneio não fica cobrando comprovante de entrega desta NF.
    detalhe = client.get("/api/expedicao/romaneio-fat").get_json()["romaneios"][0]
    assert detalhe["nfs"][0]["canhoto_pendente"] is False
    assert detalhe["nfs"][0]["sem_conferencia"] is True


def test_marca_chega_ao_documento_impresso(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()
    set_logged_user(client, "admin", "Admin")
    with app.app_context():
        criar_ordem()
    with sem_bridge():
        romaneio_id = client.post(url(), json={"motivo": MOTIVO}).get_json()["romaneio_id"]
    pagina = client.get(f"/expedicao/romaneio/{romaneio_id}/visualizar").get_data(as_text=True)
    assert "EXPEDIDO SEM CONFERÊNCIA" in pagina


def test_ordem_nao_fica_marcada_se_o_romaneio_falhar(tmp_path):
    """Ordem, romaneio e NF estão na mesma transação."""
    app = build_test_app(tmp_path)
    client = app.test_client()
    set_logged_user(client, "admin", "Admin")
    with app.app_context():
        criar_ordem()
    with sem_bridge(), patch(
        "conferencia_app.routes.expedicao_romaneio_routes.incluir_nf_no_romaneio",
        return_value=(None, "falha simulada na inclusão"),
    ):
        resp = client.post(url(), json={"motivo": MOTIVO})
    assert resp.status_code == 400
    with app.app_context():
        ordem = ExpedicaoOrdemFat.query.one()
        assert ordem.expedido_sem_conferencia is False
        assert ordem.status == fat_svc.STATUS_FATURADO_SEM_CONF
        assert ExpedicaoRomaneio.query.count() == 0


# ── Serviço de Terceiro (ST): mesmo caminho de exceção ──────────────────────

OC = "OC-5500"
NF_ST = "880777"


def criar_ordem_st(status=None, numero_nf=NF_ST, cod=OC):
    ordem = ExpedicaoOrdemST(
        cod_ordem_compra=cod,
        codigo_interno=f"OC-{cod}",
        fornecedor="TERCEIRO TESTE",
        n_os="OS-1",
        numero_nf=numero_nf,
        status=status or st_svc.STATUS_FATURADO_SEM_CONF,
        faturado_at=datetime.now(),
    )
    db.session.add(ordem)
    db.session.commit()
    return ordem


def url_st(cod=OC):
    return f"/api/expedicao/conf-cega-st/ordens/{cod}/expedir-sem-conferencia"


def test_st_cria_o_romaneio_e_carimba_a_marca(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()
    set_logged_user(client, "admin", "Admin")
    with app.app_context():
        criar_ordem_st()
    with sem_bridge():
        resp = client.post(url_st(), json={"motivo": MOTIVO})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    corpo = resp.get_json()

    with app.app_context():
        ordem = ExpedicaoOrdemST.query.one()
        assert ordem.expedido_sem_conferencia is True
        assert ordem.expedido_sem_conferencia_motivo == MOTIVO
        assert ordem.status == st_svc.STATUS_EM_ROMANEIO

        nf = ExpedicaoRomaneioNF.query.filter_by(romaneio_id=corpo["romaneio_id"]).one()
        assert nf.numero_nf == NF_ST
        assert nf.sem_conferencia is True
        assert nf.sem_conferencia_motivo == MOTIVO
        # ST não tem orçamento: a referência é a própria ordem de compra.
        assert nf.ordem_compra == OC


def test_st_exige_motivo_e_status_certo(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()
    set_logged_user(client, "admin", "Admin")
    with app.app_context():
        criar_ordem_st()
    assert client.post(url_st(), json={}).status_code == 400
    with app.app_context():
        ExpedicaoOrdemST.query.one().status = st_svc.STATUS_FATURADO
        db.session.commit()
    assert client.post(url_st(), json={"motivo": MOTIVO}).status_code == 400
    with app.app_context():
        assert ExpedicaoRomaneio.query.count() == 0


def test_st_registro_de_expedicao_nasce_finalizado(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()
    set_logged_user(client, "admin", "Admin")
    with app.app_context():
        criar_ordem_st()
    with sem_bridge(), \
         patch("conferencia_app.routes.expedicao_romaneio_routes._gerar_solicitacao_entrega_cif",
               return_value=(False, None)), \
         patch("conferencia_app.routes.expedicao_romaneio_routes._gerar_viagem_automatica_st",
               return_value=(False, None)):
        romaneio_id = client.post(url_st(), json={"motivo": MOTIVO}).get_json()["romaneio_id"]
        base = f"/api/expedicao/romaneio-fat/{romaneio_id}"
        assert client.post(base + "/finalizar").status_code == 200
        assert client.post(base + "/expedir").status_code == 200

    with app.app_context():
        registro = ExpedicaoConferenciaSimples.query.filter_by(numero_nf=NF_ST).one()
        assert registro.status == "Finalizado"
        assert registro.sem_conferencia is True
        assert registro.sem_conferencia_justificativa == MOTIVO
        assert registro.canhoto_file_name is None


# ── Carta de correção de frete ──────────────────────────────────────────────

def nf_fob():
    """A bridge devolve a NF como FOB (modFrete 1)."""
    return patch(
        "conferencia_app.routes.expedicao_romaneio_routes._dados_nf_do_bridge",
        return_value={"modfrete": "1"},
    )


def test_nf_sem_conferencia_nunca_pede_cce_mesmo_fob_em_romaneio_cif(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()
    set_logged_user(client, "admin", "Admin")
    with app.app_context():
        criar_ordem()
    with nf_fob():
        romaneio_id = client.post(url(), json={"motivo": MOTIVO}).get_json()["romaneio_id"]
        # Romaneio CIF com NF FOB: pelo caminho normal isso pediria CC-e.
        assert client.put(f"/api/expedicao/romaneio-fat/{romaneio_id}",
                          json={"tipo_frete": "CIF"}).status_code == 200
        resposta = client.post(f"/api/expedicao/romaneio-fat/{romaneio_id}/finalizar")

    assert resposta.status_code == 200
    corpo = resposta.get_json()
    assert not corpo.get("requer_confirmacao"), corpo
    with app.app_context():
        romaneio = db.session.get(ExpedicaoRomaneio, romaneio_id)
        assert romaneio.status == "Pronto"
        assert romaneio.cce_modalidade_pendente is False
        assert romaneio.cce_modalidade_detalhe is None


def test_nf_conferida_continua_pedindo_cce(tmp_path):
    """Baseline: a dispensa vale só para a NF sem conferência."""
    app = build_test_app(tmp_path)
    client = app.test_client()
    set_logged_user(client, "admin", "Admin")
    with nf_fob():
        # NF avulsa (sem ordem de faturamento), para não esbarrar na foto.
        romaneio_id = client.post(
            "/api/expedicao/romaneio-fat",
            json={"nfs": [{"numero_nf": "555001"}], "tipo_frete": "CIF"},
        ).get_json()["id"]
        corpo = client.post(
            f"/api/expedicao/romaneio-fat/{romaneio_id}/finalizar").get_json()
    assert corpo.get("requer_confirmacao") is True
    assert corpo["divergentes"][0]["numero_nf"] == "555001"
