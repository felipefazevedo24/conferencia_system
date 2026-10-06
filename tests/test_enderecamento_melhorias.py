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
    estoque = {"por_codigo": {
        "28-11-00145": {"familia": "N - 00 - MERCADORIA PARA REVENDA", "grupo": "1", "controla_estoque": 1},
        "39-06-00001": {"familia": "N - 26 - OUTROS", "grupo": "22", "controla_estoque": 0},
    }}
    with patch(ESTOQUE, return_value=estoque):
        dados = client.get("/api/recebimento/enderecamento?status=Pendente").get_json()
    assert [i["sku"] for i in dados["itens"]] == ["28-11-00145"]
    assert dados["contadores"]["Pendente"] == 1
