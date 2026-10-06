"""Endereçamento: SKU vigente, item que não controla estoque, unidade.

Casos reais de 06/10/2026: NF 24444 aparecia como "Sem vínculo" (vínculo
chegou depois da conferência, a tarefa ficou com SKU vazio); itens que não
controlam estoque na fila; conferente digitou "M" num SKU controlado em "MT"."""
from unittest.mock import patch

import pytest

from conferencia_app.models import EnderecoSaldo, ItemNota, RecebimentoEnderecamento as Tarefa
from conferencia_app.extensions import db
from conferencia_app.services import enderecamento_service as end
from conferencia_app.services import recebimento_enderecamento_service as svc
from tests.test_app import build_test_app, login_admin

ESTOQUE = "conferencia_app.services.erp_estoque_service.buscar_estoque_grv"


@pytest.fixture
def app(tmp_path):
    app = build_test_app(tmp_path)
    with app.app_context():
        yield app


def _item(**kw):
    dados = dict(numero_nota="24444", descricao="CONECTOR MACHO", codigo_grv="28-11-00145", qtd_real=4,
                 unidade_comercial="PC", status="Concluído", fornecedor="FORN")
    dados.update(kw)
    item = ItemNota(**dados)
    db.session.add(item)
    db.session.flush()
    return item


def test_unidades_sinonimas_nao_travam_e_mantem_a_grafia(app):
    assert end.unidade_canonica("m") == end.unidade_canonica("MT") == end.unidade_canonica("MTS")
    assert end.unidade_canonica("PÇ") == end.unidade_canonica("PC")
    assert end.unidade_canonica("PC") != end.unidade_canonica("UN")
    db.session.add(EnderecoSaldo(sku="X", endereco="A", unidade="MT", quantidade=0))
    db.session.flush()
    novo = end.saldo("X", "B", "M")
    assert novo.unidade == "MT"
    with pytest.raises(ValueError, match="controlado em MT"):
        end.saldo("X", "C", "KG")


def test_nao_endereca_quem_nao_controla_estoque(app):
    assert end.sem_enderecamento("N - 06 - INSUMOS", None, 0) is True
    assert end.sem_enderecamento("N - 06 - INSUMOS", None, 1) is False
    # Bridge antiga (sem o campo): vale a família; 33 = bens de pequeno valor.
    assert end.sem_enderecamento("N - 33 - BENS DE PEQUENO VALOR", None, None) is True
    assert end.sem_enderecamento("N - 06 - INSUMOS", None, None) is False


def test_tarefa_nasce_na_unidade_da_conversao(app):
    from flask import session
    ctx = app.test_request_context(); ctx.push(); session["username"] = "conf"
    item = _item(unidade_comercial="PÇ")
    svc.criar_pendencias([item], {str(item.id): "4"}, {str(item.id): {"fator": "6000", "unidade": "mm"}}, {item.id}, "conf")
    tarefa = Tarefa.query.filter_by(item_nota_id=item.id).one()
    assert (tarefa.quantidade, tarefa.unidade) == (24000, "MM")
    sem_conv = _item(numero_nota="2", unidade_comercial="PC")
    svc.criar_pendencias([sem_conv], {str(sem_conv.id): "4"}, {}, {sem_conv.id}, "conf")
    assert Tarefa.query.filter_by(item_nota_id=sem_conv.id).one().unidade == "PC"
    ctx.pop()


def test_fila_usa_o_sku_vigente_do_item_e_filtra_quem_nao_controla_estoque(app):
    client = app.test_client()
    login_admin(client)
    estoque_item = _item()
    servico = _item(numero_nota="24445", descricao="LOCAÇÃO", codigo_grv="39-06-00001")
    # Tarefas criadas antes do vínculo: SKU vazio.
    db.session.add_all([Tarefa(item_nota_id=estoque_item.id, sku="", quantidade=4, criado_por="c"),
                        Tarefa(item_nota_id=servico.id, sku="", quantidade=1, criado_por="c")])
    db.session.commit()
    sem_saldo = _item(numero_nota="24446", descricao="ZERADO", codigo_grv="28-11-00999")
    db.session.add(Tarefa(item_nota_id=sem_saldo.id, sku="28-11-00999", quantidade=1, criado_por="c"))
    db.session.commit()
    estoque = {"por_codigo": {
        "28-11-00145": {"familia": "N - 00 - MERCADORIA PARA REVENDA", "grupo": "1", "controla_estoque": 1, "qtde_total": 4},
        "39-06-00001": {"familia": "N - 26 - OUTROS", "grupo": "22", "controla_estoque": 0, "qtde_total": 1},
        # Sem saldo no GRV não há o que endereçar (06/10/2026).
        "28-11-00999": {"familia": "N - 00 - MERCADORIA PARA REVENDA", "grupo": "1", "controla_estoque": 1, "qtde_total": 0},
    }}
    with patch(ESTOQUE, return_value=estoque):
        dados = client.get("/api/recebimento/enderecamento?status=Pendente").get_json()
    assert [i["sku"] for i in dados["itens"]] == ["28-11-00145"]
    assert dados["contadores"]["Pendente"] == 1


def test_busca_por_os_acha_o_produto_acabado_mesmo_sem_saldo(app):
    client = app.test_client()
    login_admin(client)
    estoque = {"por_codigo": {
        "23-01-05879": {"item": "MOLD HTX1100 FP SEXT25CM", "unidade": "PÇ", "qtde_total": 0, "localizacoes": [],
                        "familia": "N - 04 - PRODUTOS", "grupo": "1", "controla_estoque": 1},
        "19-01-00001": {"item": "CHAPA 11078", "unidade": "KG", "qtde_total": 5, "localizacoes": ["A-01"],
                        "familia": "N - 01 - MATÉRIA-PRIMA", "grupo": "1", "controla_estoque": 1},
    }}
    os_grv = [{"n_os": "11078", "codigo_interno": "23-01-05879", "nome": "MOLD", "titulo": "MOLD HTX1100", "concluido": 0, "cancelado": 0}]
    with patch(ESTOQUE, return_value=estoque), patch("conferencia_app.compras.db.fetch_all", return_value=os_grv) as consulta:
        dados = client.get("/api/enderecamento/saldos?busca=11078&saldo=1").get_json()
    assert consulta.call_args.args[1] == {"cod_empresa": 1, "n_os": "11078"}
    # O produto da OS vem primeiro, mesmo sem saldo e com "Somente com saldo" marcado.
    primeiro = dados["itens"][0]
    assert (primeiro["sku"], primeiro["endereco"], primeiro["os"]["n_os"], primeiro["os"]["situacao"]) == ("23-01-05879", "", "11078", "Em aberto")
    # A busca por texto continua valendo junto ("11078" na descrição da chapa).
    assert [i["sku"] for i in dados["itens"]] == ["23-01-05879", "19-01-00001"]
    # "Buscar só por OS": só o produto da OS, sem o material de número parecido.
    with patch(ESTOQUE, return_value=estoque), patch("conferencia_app.compras.db.fetch_all", return_value=os_grv):
        so_os = client.get("/api/enderecamento/saldos?busca=11078&os=1").get_json()
    assert [i["sku"] for i in so_os["itens"]] == ["23-01-05879"]

    # Texto que não é número de OS não consulta o GRV; GRV fora não derruba a busca.
    with patch(ESTOQUE, return_value=estoque), patch("conferencia_app.compras.db.fetch_all") as consulta:
        client.get("/api/enderecamento/saldos?busca=CHAPA")
    consulta.assert_not_called()
    with patch(ESTOQUE, return_value=estoque), patch("conferencia_app.compras.db.fetch_all", side_effect=RuntimeError("bridge fora")):
        r = client.get("/api/enderecamento/saldos?busca=11078")
    assert r.status_code == 200 and [i["sku"] for i in r.get_json()["itens"]] == ["19-01-00001"]


def test_familias_que_saem_das_listas(app):
    # 06/10/2026: 07, 37 e 42 inteiras; 03 só "PRODUÇÃO POR TERCEIROS" (grupo 2).
    for familia in ("N - 07 - INSUMOS ADMINISTRATIVOS", "N - 37 - MATERIAL DE TERCEIRO", "N - 42 - MATÉRIA-PRIMA - MATERIAL ESPECÍFICO"):
        assert end.sem_enderecamento(familia, "1", 1) is True
    assert end.sem_enderecamento("N - 03 - PRODUTO EM PROCESSO", "2", 1) is True
    assert end.sem_enderecamento("N - 03 - PRODUTO EM PROCESSO", "1", 1) is False
    assert end.sem_enderecamento("N - 01 - MATÉRIA-PRIMA", "1", 1) is False


def test_bridge_oc_aberta_calcula_pendente_na_unidade_do_estoque(monkeypatch):
    # 1 PÇ comprada = 5.999,88 mm no estoque: o pendente é em mm, não "1".
    from unittest.mock import MagicMock
    from scripts import erp_lancamento_api_bridge as bridge

    monkeypatch.setattr(bridge, "_config", lambda: {"host": "h", "database": "d", "user": "u"})
    monkeypatch.setattr(bridge, "_authorized", lambda cfg: True)
    conn = MagicMock()
    cur = conn.__enter__.return_value.cursor.return_value.__enter__.return_value
    cur.description = [("codigo_key",), ("codigo_interno",), ("ordem_compra",), ("fornecedor",), ("prazo_entrega",), ("quantidade_pendente",)]
    cur.fetchall.return_value = []
    monkeypatch.setattr(bridge, "_conectar", lambda cfg, **kw: conn)
    bridge.create_app().test_client().post("/api/erp/estoque/ordens-compra-abertas", json={"codigos": ["19-01-00296"]})
    sql = cur.execute.call_args.args[0]
    assert "coalesce(item.qtde, 0) - coalesce(item.qtde_entregue, 0)" in sql
    assert "coalesce(item.qtde_compra" not in sql


def test_api_de_localizacao_so_manda_vazio_quando_autorizado(app):
    from unittest.mock import MagicMock
    from conferencia_app.services import erp_estoque_service as erp

    with pytest.raises(ValueError, match="obrigatório"):
        erp.atualizar_localizacao_estoque("27-08-00147", "")
    resposta = MagicMock(status_code=200, content=b"{}", json=lambda: {"status": "sucesso"})
    with patch.object(erp.requests, "patch", return_value=resposta) as chamada:
        erp.atualizar_localizacao_estoque("27-08-00147", "", permitir_vazio=True)
    assert chamada.call_args.kwargs["json"] == {"localizacao_estoque": ""}


def test_buscar_por_endereco_e_exato_com_prefixo_opcional(app):
    # 06/10/2026: buscar "AL-BI-A1" trazia também A10, A11 e descrições com o trecho.
    client = app.test_client()
    login_admin(client)
    item = lambda loc, desc="X": {"item": desc, "unidade": "UN", "qtde_total": 1, "localizacoes": [loc],
                                  "familia": "N - 01 - MATÉRIA-PRIMA", "grupo": "1", "controla_estoque": 1}
    estoque = {"por_codigo": {"S1": item("AL-BI-A1"), "S2": item("AL-BI-A10"), "S3": item("AL-BI-A11"),
                              "S4": item("ZZ-01", "PEÇA AL-BI-A1 VELHA"), "S5": item("AL-BX-01")}}
    with patch(ESTOQUE, return_value=estoque), patch("conferencia_app.compras.db.fetch_all") as os_grv:
        exato = client.get("/api/enderecamento/saldos?por=endereco&busca=al-bi-a1").get_json()
        prefixo = client.get("/api/enderecamento/saldos?por=endereco&busca=AL-BI*").get_json()
        material = client.get("/api/enderecamento/saldos?por=material&busca=velha peça").get_json()
    assert [i["sku"] for i in exato["itens"]] == ["S1"]
    assert sorted(i["sku"] for i in prefixo["itens"]) == ["S1", "S2", "S3"]
    assert [i["sku"] for i in material["itens"]] == ["S4"]
    os_grv.assert_not_called()  # endereço/material não consultam OS no GRV


def test_aba_enderecos_busca_exata_ou_por_prefixo(app):
    from conferencia_app.models import LocalizacaoArmazem
    client = app.test_client()
    login_admin(client)
    for codigo in ("AL-BI-A1", "AL-BI-A10", "AL-BX-01"):
        db.session.add(LocalizacaoArmazem(codigo=codigo, corredor="", prateleira="", posicao=""))
    db.session.commit()
    with patch(ESTOQUE, return_value={"por_codigo": {}}):
        exato = client.get("/api/enderecamento/locais?busca=al-bi-a1").get_json()["itens"]
        prefixo = client.get("/api/enderecamento/locais?busca=AL-BI*").get_json()["itens"]
    assert [l["codigo"] for l in exato] == ["AL-BI-A1"]
    assert sorted(l["codigo"] for l in prefixo) == ["AL-BI-A1", "AL-BI-A10"]
