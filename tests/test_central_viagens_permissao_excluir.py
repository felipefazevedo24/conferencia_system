from datetime import datetime, timedelta

import pytest

from test_app import build_test_app, set_logged_user
from conferencia_app.extensions import db
from conferencia_app.models import (
    AgendamentoMotorista, AgendamentoSolicitacao, AgendamentoVeiculo,
    PermissaoAcesso, Viagem,
)

PERM = "MANAGE_LOGISTICA_VIAGEM_EXCLUIR"
USUARIO = "gestor_viagens"


def _montar(tmp_path, permitido, role="Portaria"):
    """Usuário não-admin que enxerga a Central; a permissão de apagar varia."""
    app = build_test_app(tmp_path)
    client = app.test_client()
    set_logged_user(client, USUARIO, role)
    with app.app_context():
        chaves = ["PAGE_LOGISTICA_AGENDAMENTO", "PAGE_LOGISTICA_VIAGEM"]
        if permitido:
            chaves.append(PERM)
        for chave in chaves:
            db.session.add(PermissaoAcesso(scope_type="USER", scope_id=USUARIO,
                                           permission_key=chave, allow=True))
        veiculo = AgendamentoVeiculo(codigo="TEST-PERM", nome_exibicao="Teste")
        motorista = AgendamentoMotorista(nome="Motorista Teste")
        db.session.add_all([veiculo, motorista])
        db.session.flush()
        viagem = Viagem(codigo="TEST-APAGAR", veiculo_id=veiculo.id, motorista_id=motorista.id,
                        status="Planejada", saida_prevista=datetime.now() + timedelta(days=1))
        sols = [
            AgendamentoSolicitacao(tipo="COLETA", status="Pendente", codigo=f"TEST-SOL-{i}",
                                   solicitante="admin", documento_tipo="OC", documento_numero=f"OC-{i}",
                                   parceiro_tipo="Fornecedor", parceiro_nome="Fornecedor Teste",
                                   logradouro="Rua Teste", cidade="Campinas", uf="SP",
                                   origem_documento="ORDEM_DE_COMPRA")
            for i in (1, 2)
        ]
        db.session.add_all([viagem, *sols])
        db.session.commit()
        return app, client, viagem.id, [s.id for s in sols]


@pytest.mark.parametrize("permitido", [False, True])
def test_apagar_na_central_exige_permissao_propria(tmp_path, permitido):
    app, client, viagem_id, (sol_a, sol_b) = _montar(tmp_path, permitido)
    esperado = 200 if permitido else 403
    base = "/api/logistica/central-viagens/solicitacoes"

    assert client.delete(f"{base}/{sol_a}/excluir", json={}).status_code == esperado
    assert client.post(f"{base}/excluir-lote", json={"ids": [sol_b]}).status_code == esperado
    assert client.delete(f"/api/viagem/{viagem_id}").status_code == esperado

    with app.app_context():
        status = {s.id: s.status for s in AgendamentoSolicitacao.query.all()}
        assert status[sol_a] == ("Cancelada" if permitido else "Pendente")
        assert status[sol_b] == ("Cancelada" if permitido else "Pendente")
        assert (db.session.get(Viagem, viagem_id) is None) == permitido

    # A tela só mostra os botões de apagar para quem tem a permissão.
    pagina = client.get("/logistica/viagens")
    assert pagina.status_code == 200
    marca = b'data-pode-excluir="true"' if permitido else b'data-pode-excluir="false"'
    assert marca in pagina.data


def test_admin_continua_podendo_apagar(tmp_path):
    app, client, viagem_id, (sol_a, _) = _montar(tmp_path, permitido=False)
    set_logged_user(client, "admin", "Admin")
    assert client.delete(f"/api/logistica/central-viagens/solicitacoes/{sol_a}/excluir", json={}).status_code == 200
    assert client.delete(f"/api/viagem/{viagem_id}").status_code == 200
    assert b'data-pode-excluir="true"' in client.get("/logistica/viagens").data
