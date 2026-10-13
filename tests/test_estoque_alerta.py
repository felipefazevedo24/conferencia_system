"""Avisos de estoque por e-mail: reserva sem saldo (checagem a cada 5 min) e
resumo da curva A em alerta (2x ao dia). GRV e SMTP sempre com patch."""
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest

from conferencia_app.models import EstoqueAlertaEstado
from conferencia_app.services import estoque_alerta_service as alerta
from conferencia_app.services import estoque_planejamento_service as planejamento_svc
from tests.test_app import build_test_app, login_admin
from tests.test_estoque_planejamento import FAMILIA_MP, HOJE, _regular

FAMILIA_PA = "N - 04 - PRODUTOS"


def _app(tmp_path, destinatarios="logistica@teste.com, compras@teste.com"):
    app = build_test_app(tmp_path)
    app.config["ESTOQUE_ALERTA_EMAIL_DESTINATARIOS"] = destinatarios
    return app


def _estoque(itens):
    return {"por_codigo": {
        codigo: {"familia": FAMILIA_MP, "item": codigo, "unidade": "KG", **dados} for codigo, dados in itens.items()
    }}


@contextmanager
def _grv(estoque, historicos=None, reservas=None, smtp_erro=None, ocs=None):
    planejamento = {"side_effect": historicos} if isinstance(historicos, Exception) else {"return_value": historicos or {}}
    with patch.object(alerta, "buscar_estoque_grv", return_value=estoque), \
         patch.object(alerta, "buscar_planejamento_grv", **planejamento), \
         patch.object(alerta, "buscar_reservas_produto_acabado_grv", return_value=reservas or {}), \
         patch.object(alerta, "buscar_ordens_compra_abertas_grv", return_value=ocs or {}), \
         patch.object(planejamento_svc, "agora_br", return_value=MagicMock(date=lambda: HOJE)), \
         patch.object(alerta, "enviar_mensagem_smtp", side_effect=smtp_erro) as smtp:
        yield smtp


def _assunto(smtp):
    return smtp.call_args.args[1]["Subject"]


def _html(smtp):
    return smtp.call_args.args[1].get_payload()[1].get_payload(decode=True).decode("utf-8")


def _estados(tipo):
    return {e.codigo: e for e in EstoqueAlertaEstado.query.filter_by(tipo=tipo)}


# ------------------------------------------------------------------ reservas

def test_reserva_sem_saldo_avisa_uma_vez_e_de_novo_se_o_reservado_aumentar(tmp_path):
    app = _app(tmp_path)
    reservas = {"190400012": [{"qtde": 8, "numero_orcamento": "4521", "versao_orcamento": "2", "os_descricao": "8465", "cliente_orcamento": "CLIENTE X"}]}

    def estoque(reservado):
        return _estoque({
            "19-04-00012": {"familia": FAMILIA_PA, "item": "PALETEIRA", "unidade": "UN", "qtde_total": 5, "qtde_reservada": reservado, "qtde_disponivel": 5 - reservado},
            # Reservado igual ao físico: atende, não avisa.
            "19-01-00001": {"qtde_total": 100, "qtde_reservada": 100, "qtde_disponivel": 0},
            # Família fora da tela de Estoque não entra.
            "99-00-00001": {"familia": "N - 09 - USO E CONSUMO", "qtde_total": 0, "qtde_reservada": 3},
        })

    with app.app_context():
        with _grv(estoque(8), reservas=reservas) as smtp:
            assert alerta.verificar_reservas() == {"sem_saldo": 1, "com_oc": 0, "avisados": ["19-04-00012"]}
        assert smtp.call_count == 1
        assert _assunto(smtp) == "Estoque: reserva sem saldo - 19-04-00012"
        assert smtp.call_args.args[1]["To"] == "logistica@teste.com, compras@teste.com"
        html = _html(smtp)
        assert "PALETEIRA" in html and "OS 8465" in html and "Orç. 4521/2" in html and "CLIENTE X" in html
        assert "19-01-00001" not in html
        # Produto acabado é fabricado: sem sugestão de compra.
        assert "fabricado" in html

        # Mesmo quadro 5 min depois: silêncio.
        with _grv(estoque(8)) as smtp:
            assert alerta.verificar_reservas()["avisados"] == []
        assert smtp.call_count == 0

        # Entrou outra reserva no mesmo item.
        with _grv(estoque(11)) as smtp:
            assert alerta.verificar_reservas()["avisados"] == ["19-04-00012"]
        assert _estados("reserva")["190400012"].quantidade == 11

        # Regularizou: o estado some, e se faltar de novo avisa de novo.
        with _grv(estoque(4)) as smtp:
            assert alerta.verificar_reservas() == {"sem_saldo": 0, "com_oc": 0, "avisados": []}
        assert _estados("reserva") == {}
        with _grv(estoque(6)) as smtp:
            alerta.verificar_reservas()
        assert smtp.call_count == 1


def test_reserva_de_item_comprado_traz_sugestao_e_oc_que_cobre_a_falta_silencia(tmp_path):
    app = _app(tmp_path)
    # Reservou 300 com 100 no físico: faltam 200.
    estoque = _estoque({"19-01-00001": {"qtde_total": 100, "qtde_reservada": 300, "qtde_disponivel": -200}})
    historicos = {"190100001": _regular(mensal=1000, preco=50)}

    def oc(pendente):
        return {"190100001": [{"ordem_compra": 12594, "quantidade_pendente": pendente}]}

    with app.app_context():
        # OC aberta cobre a falta: reposição a caminho, nem avisa nem guarda estado.
        with _grv(estoque, historicos, ocs=oc(250)) as smtp:
            assert alerta.verificar_reservas() == {"sem_saldo": 0, "com_oc": 1, "avisados": []}
        assert smtp.call_count == 0
        assert _estados("reserva") == {}

        # OC de 50 não resolve uma falta de 200: avisa, dizendo quanto a OC cobre.
        with _grv(estoque, historicos, ocs=oc(50)) as smtp:
            assert alerta.verificar_reservas()["avisados"] == ["19-01-00001"]
        html = _html(smtp)
        assert "OC aberta cobre só 50 KG" in html
        plano = planejamento_svc.calcular_item(
            historicos["190100001"], saldo_disponivel=-200, custo_unitario=50, classe_abc="A",
            parametros=dict(planejamento_svc.PADROES), hoje=HOJE,
        )
        # A compra cobre a reserva (200) e ainda volta ao mínimo.
        comprar = plano["estoque_minimo_sugerido"] + 200
        assert f"{alerta._fmt(comprar)} KG" in html
        assert alerta._reais(comprar * 50) in html
        assert "curva A" in html and "Dilui em" in html


def test_sugestao_de_reposicao_usa_minimo_lote_custo_e_tempo_de_consumo():
    plano = {"tipo_demanda": "regular", "estoque_minimo_sugerido": 1000.0, "lote_economico_sugerido": 400.0,
             "custo_unitario": 12.5, "consumo_mensal": 200.0}
    # Zerado: precisa de 1000 pra voltar ao mínimo (mais que o lote de 400).
    assert alerta.sugestao_reposicao(plano, 0) == {"manter": 1000.0, "comprar": 1000.0, "custo": 12500.0, "meses": 5.0}
    # Faltam só 100 pro mínimo: compra o lote econômico, senão pede compra de novo logo.
    assert alerta.sugestao_reposicao(plano, 900)["comprar"] == 400.0
    # Sem custo no GRV não inventa valor; sem giro não sugere estocar.
    assert alerta.sugestao_reposicao({**plano, "custo_unitario": None}, 0)["custo"] is None
    assert alerta.sugestao_reposicao({**plano, "tipo_demanda": "sem_giro"}, 0) is None
    assert alerta._duracao(5.0) == "5 meses" and alerta._duracao(0.5) == "15 dias" and alerta._duracao(None) == "—"


def test_envio_de_teste_vai_so_pro_endereco_pedido_e_nao_grava_estado(tmp_path):
    app = _app(tmp_path)
    reserva = _estoque({"19-01-00001": {"qtde_total": 1, "qtde_reservada": 4}})
    with app.app_context():
        with _grv(reserva) as smtp:
            assert alerta.verificar_reservas(teste_para="eu@teste.com")["avisados"] == ["19-01-00001"]
        assert smtp.call_args.args[1]["To"] == "eu@teste.com"
        assert _assunto(smtp).startswith("[TESTE] ")
        with _grv(_estoque_curva(), HISTORICOS) as smtp:
            assert alerta.enviar_resumo_curva_a(teste_para="eu@teste.com")["enviado"] is True
        assert smtp.call_args.args[1]["To"] == "eu@teste.com"
        assert _assunto(smtp).startswith("[TESTE] Estoque curva A")
        # O aviso de verdade ainda sai depois: nada ficou marcado como já avisado.
        assert EstoqueAlertaEstado.query.count() == 0


def test_reserva_falha_de_envio_ou_do_grv_nao_grava_estado(tmp_path):
    app = _app(tmp_path)
    estoque = _estoque({"19-01-00001": {"qtde_total": 1, "qtde_reservada": 4}})
    with app.app_context():
        with _grv(estoque, smtp_erro=RuntimeError("smtp fora")):
            with pytest.raises(RuntimeError):
                alerta.verificar_reservas()
        assert _estados("reserva") == {}
        # Próximo ciclo, SMTP de volta: o aviso sai.
        with _grv(estoque) as smtp:
            assert alerta.verificar_reservas()["avisados"] == ["19-01-00001"]
        assert smtp.call_count == 1

        # Bridge devolvendo estoque vazio não pode apagar o que já foi avisado.
        with _grv({"por_codigo": {}}) as smtp:
            with pytest.raises(RuntimeError):
                alerta.verificar_reservas()
        assert list(_estados("reserva")) == ["190100001"]


def test_sem_destinatario_configurado_nao_envia_nem_grava(tmp_path):
    app = _app(tmp_path, destinatarios="")
    with app.app_context():
        with _grv(_estoque({"19-01-00001": {"qtde_total": 1, "qtde_reservada": 4}})) as smtp:
            with pytest.raises(ValueError, match="ESTOQUE_ALERTA_EMAIL_DESTINATARIOS"):
                alerta.verificar_reservas()
            with pytest.raises(ValueError):
                alerta.enviar_resumo_curva_a()
        assert smtp.call_count == 0
        assert EstoqueAlertaEstado.query.count() == 0


# ------------------------------------------------------------------- curva A

HISTORICOS = {
    "190100001": _regular(mensal=1000, preco=50),
    "190100002": _regular(mensal=800, preco=50),
    # Sem saída nos 2 últimos meses: 4 de 6, não é curva A.
    "190100003": {**_regular(mensal=10, preco=1), "serie_mensal": _regular(mensal=10, preco=1)["serie_mensal"][:-2]},
    "190100004": _regular(mensal=900, preco=50),
}


def _estoque_curva(saldo_4=810):
    return _estoque({
        "19-01-00001": {"qtde_total": 0, "qtde_disponivel": 0},        # A, zerado
        "19-01-00002": {"qtde_total": 50, "qtde_disponivel": 50},      # A, 1 dia de saldo pra 25 de lead time
        "19-01-00003": {"qtde_total": 0, "qtde_disponivel": 0},        # zerado, mas curva C: fora
        "19-01-00004": {"qtde_total": saldo_4, "qtde_disponivel": saldo_4},  # A, cobre o lead time mas está abaixo do mínimo
    })


def test_resumo_curva_a_lista_completa_e_marca_o_que_e_novo(tmp_path):
    app = _app(tmp_path)
    with app.app_context():
        with _grv(_estoque_curva(), HISTORICOS) as smtp:
            resultado = alerta.enviar_resumo_curva_a()
        assert resultado == {"enviado": True, "novos": 3, "com_oc": 0, "sem_resposta": [], "zerado": 1, "critico": 1, "abaixo_minimo": 1}
        assert _assunto(smtp) == "Estoque curva A: 1 sem estoque, 1 críticos, 1 abaixo do mínimo (3 novos)"
        html = _html(smtp)
        assert "19-01-00003" not in html
        assert html.count("NOVO") == 3
        # Cada item traz quanto manter, quanto comprar (com custo) e em quanto tempo dilui.
        assert "Manter em estoque" in html and "Comprar agora" in html and "Dilui em" in html
        assert html.count("R$") >= 4
        assert {c: e.situacao for c, e in _estados("curva_a").items()} == {
            "190100001": "zerado", "190100002": "critico", "190100004": "abaixo_minimo",
        }

        # Segundo envio do dia, nada mudou: lista completa de novo, sem "novo".
        with _grv(_estoque_curva(), HISTORICOS) as smtp:
            assert alerta.enviar_resumo_curva_a()["novos"] == 0
        assert smtp.call_count == 1
        assert "novos" not in _assunto(smtp) and "NOVO" not in _html(smtp)

        # O item que estava abaixo do mínimo zerou: piorou, volta a ser novo.
        with _grv(_estoque_curva(saldo_4=0), HISTORICOS) as smtp:
            resultado = alerta.enviar_resumo_curva_a()
        assert (resultado["zerado"], resultado["abaixo_minimo"], resultado["novos"]) == (2, 0, 1)
        assert _estados("curva_a")["190100004"].situacao == "zerado"


def test_item_caro_de_poucas_saidas_nao_e_curva_a_nem_entra_no_resumo(tmp_path):
    from tests.test_estoque_planejamento import METALON

    app = _app(tmp_path)
    # Saídas caras, mas só 1 nos últimos 6 meses: pelo valor seria curva A; pelo giro não é.
    historicos = {**HISTORICOS, "190100009": {**METALON, "preco_custo": 15.0}}
    estoque = _estoque_curva()
    estoque["por_codigo"]["19-01-00009"] = {"familia": FAMILIA_MP, "item": "MAQUINA SOB ENCOMENDA", "unidade": "UN", "qtde_total": 0, "qtde_disponivel": 0}
    with app.app_context():
        with _grv(estoque, historicos):
            planos = alerta._planos_da_visao(estoque["por_codigo"], "materia_prima", dict(planejamento_svc.PADROES))[2]
        assert (planos["190100009"]["classe_abc"], planos["190100009"]["tipo_demanda"]) == ("C", "esporadico")
        assert planos["190100001"]["classe_abc"] == "A"
        with _grv(estoque, historicos) as smtp:
            alerta.enviar_resumo_curva_a()
        assert "19-01-00009" not in _html(smtp)
        assert "190100009" not in _estados("curva_a")


def test_resumo_curva_a_deixa_de_fora_item_com_oc_aberta(tmp_path):
    app = _app(tmp_path)
    ocs = {"190100002": [{"ordem_compra": 12594, "quantidade_pendente": 500}]}
    with app.app_context():
        with _grv(_estoque_curva(), HISTORICOS, ocs=ocs) as smtp:
            resultado = alerta.enviar_resumo_curva_a()
        assert (resultado["critico"], resultado["com_oc"], resultado["novos"]) == (0, 1, 2)
        html = _html(smtp)
        assert "19-01-00002" not in html
        assert "1 item de curva A em alerta ficou de fora por já ter ordem de compra aberta." in html
        assert sorted(_estados("curva_a")) == ["190100001", "190100004"]

        # Todos com OC: não há o que comprar, não há e-mail.
        todos = {chave: ocs["190100002"] for chave in ("190100001", "190100002", "190100004")}
        with _grv(_estoque_curva(), HISTORICOS, ocs=todos) as smtp:
            assert alerta.enviar_resumo_curva_a()["enviado"] is False
        assert smtp.call_count == 0

        # A OC do 19-01-00002 chegou e ele continua crítico: entra como novo.
        with _grv(_estoque_curva(), HISTORICOS) as smtp:
            assert alerta.enviar_resumo_curva_a()["novos"] == 3
        assert "19-01-00002" in _html(smtp)


def test_resumo_curva_a_sem_alerta_nao_envia_e_grv_fora_preserva_o_estado(tmp_path):
    app = _app(tmp_path)
    with app.app_context():
        with _grv(_estoque_curva(), HISTORICOS):
            alerta.enviar_resumo_curva_a()
        assert len(_estados("curva_a")) == 3

        # Histórico do GRV fora: sem e-mail, e ninguém sai do estado (senão
        # voltaria tudo como "novo" no envio seguinte).
        with _grv(_estoque_curva(), RuntimeError("bridge fora")) as smtp:
            resultado = alerta.enviar_resumo_curva_a()
        assert resultado["enviado"] is False and resultado["sem_resposta"] == ["Matéria-prima"]
        assert smtp.call_count == 0
        assert len(_estados("curva_a")) == 3

        # Tudo reposto: lista vazia não gera e-mail e o estado é limpo.
        cheio = _estoque({codigo: {"qtde_total": 99999, "qtde_disponivel": 99999} for codigo in _estoque_curva()["por_codigo"]})
        with _grv(cheio, HISTORICOS) as smtp:
            assert alerta.enviar_resumo_curva_a()["enviado"] is False
        assert smtp.call_count == 0
        assert _estados("curva_a") == {}


# ---------------------------------------------------------------------- tela

def test_tela_de_estoque_tem_filtro_e_ordenacao_por_coluna(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()
    login_admin(client)
    html = client.get("/logistica/estoque").get_data(as_text=True)
    assert 'id="mp-filtro"' in html
    assert 'id="mp-limpar-filtros"' in html
    assert "function abrirFiltro" in html
    # O filtro de coluna roda no navegador: a tela pede o máximo que a API devolve.
    assert "params.set('limit', '800')" in html
