from datetime import date, datetime

import pytest
from flask import Flask

from conferencia_app.extensions import db
from conferencia_app.models import (
    AgendamentoMotorista, AgendamentoVeiculo, ExpedicaoConferenciaSimples,
    ExpedicaoRomaneio, ExpedicaoRomaneioNF, ItemNota, LogisticaInventarioInicial,
    Viagem,
)
from conferencia_app.services.perfil_service import indicadores_perfil, periodo_perfil


@pytest.mark.parametrize("dias", [30, 90])
def test_periodos_incluem_hoje(dias):
    periodo = periodo_perfil({"periodo": str(dias)}, date(2026, 9, 15))
    assert (periodo["fim"] - periodo["inicio"]).days == dias - 1


@pytest.mark.parametrize("args", [
    {"periodo": "todos"},
    {"periodo": "personalizado", "inicio": "errado", "fim": "2026-09-15"},
    {"periodo": "personalizado", "inicio": "2026-09-15", "fim": "2026-09-14"},
    {"periodo": "personalizado", "inicio": "2026-09-15", "fim": "2026-09-16"},
    {"periodo": "personalizado", "inicio": "2026-03-01", "fim": "2026-09-01"},
])
def test_periodos_invalidos(args):
    with pytest.raises(ValueError):
        periodo_perfil(args, date(2026, 9, 15))


def test_seis_meses_calendario():
    periodo = periodo_perfil({"periodo": "personalizado", "inicio": "2026-03-01", "fim": "2026-08-31"}, date(2026, 9, 15))
    assert periodo["inicio"] == date(2026, 3, 1)


def test_indicadores_isolam_usuario_datas_e_notas():
    app = Flask(__name__)
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
    db.init_app(app)
    with app.app_context():
        db.create_all()
        dentro = datetime(2026, 9, 15, 23, 59, 59)
        for autor, numero, data in [
            ("ana", "1", dentro), ("ana", "1", dentro),
            ("outro", "2", dentro), ("ana", "3", datetime(2026, 8, 16)),
            ("ana", "4", datetime(2026, 9, 16)),
            ("ana", "5", datetime(2026, 8, 17)),
        ]:
            db.session.add(ItemNota(numero_nota=numero, usuario_conferencia=autor, fim_conferencia=data))
        # Mesmo número de outro emitente é outra NF.
        db.session.add(ItemNota(numero_nota="1", cnpj_emitente="123", usuario_conferencia="ana", fim_conferencia=dentro))
        db.session.add(LogisticaInventarioInicial(local_codigo="A", codigo_produto="P", criado_por="ana", criado_em=dentro))
        db.session.add(ExpedicaoConferenciaSimples(orcamento="OR1", conferente="outro", numero_nf="10", expedido_by="ana", expedido_at=dentro))
        romaneio = ExpedicaoRomaneio(numero_romaneio="R1", criado_por="outro", expedido_por="ana", expedido_em=dentro, status="Expedido")
        db.session.add(romaneio)
        veiculo = AgendamentoVeiculo(codigo="V1", nome_exibicao="Veículo 1", placa="ABC1234")
        motorista = AgendamentoMotorista(nome="Ana", usuario_username="ana")
        db.session.add_all([veiculo, motorista])
        db.session.flush()
        db.session.add(ExpedicaoRomaneioNF(romaneio_id=romaneio.id, numero_nf="10", adicionado_por="outro"))
        db.session.add_all([
            Viagem(veiculo_id=veiculo.id, motorista_id=motorista.id, retorno_real=dentro, status="Concluida", criado_por="outro"),
            Viagem(veiculo_id=veiculo.id, retorno_real=dentro, status="Concluida", criado_por="ana", criado_em=dentro),
        ])
        db.session.commit()
        modulos = indicadores_perfil("ana", periodo_perfil({}, date(2026, 9, 15)))
        valores = {m["id"]: [v["valor"] for v in m["metricas"]] for m in modulos}
        assert valores["recebimento"] == [3, 4]
        assert valores["expedicao"] == [1, 0, 0, 0, 0, 1]
        assert valores["viagens"] == [1, 1, 0]
        assert valores["inventarios"] == [1, 0, 0]
        assert not any(m["ativo"] for m in indicadores_perfil("sem_atividade", periodo_perfil({}, date(2026, 9, 15))))


def test_pagina_perfil_renderiza_e_rejeita_filtro(tmp_path):
    from test_app import build_test_app, login_admin

    app = build_test_app(tmp_path)
    client = app.test_client()
    assert client.get("/perfil").status_code == 302
    login_admin(client)
    response = client.get("/perfil?periodo=90&username=outro")
    assert response.status_code == 200
    assert "Minha atividade" in response.get_data(as_text=True)
    assert 'data-modulo="inventarios"' in response.get_data(as_text=True)
    response = client.get("/perfil?periodo=personalizado&inicio=2025-01-01&fim=2026-01-01")
    assert "no máximo 6 meses" in response.get_data(as_text=True)
    assert '<article class="atividade-modulo"' not in response.get_data(as_text=True)
