import pytest


@pytest.fixture(autouse=True)
def _sem_webhook_do_teams(monkeypatch):
    """O app de teste usa a mesma pasta instance/ da máquina, e o
    teams_config.json dela aponta pros canais de verdade: teste que não
    lembrava de dar patch no Teams postava card real ("CLIENTE TESTE -
    Orçamento 1234", "NF 903") no canal. Aqui nenhum teste enxerga webhook;
    quem quiser testar o envio dá patch por cima."""
    from conferencia_app.services import teams_service

    monkeypatch.setattr(teams_service, "_webhook_url", lambda *args, **kwargs: "")
