from conferencia_app.compras import queries


def test_spend_baseline_nao_limita_antes_da_consolidacao_por_fornecedor():
    sql = queries.SQL_SPEND_BASELINE.upper()

    assert "LIMIT %(LIMITE)S" not in sql
    assert "FROM AGG" in sql


def test_spend_baseline_composicao_mantem_limite_de_detalhe():
    sql = queries.SQL_SPEND_BASELINE_COMPOSICAO.upper()

    assert "LIMIT %(LIMITE)S" in sql
