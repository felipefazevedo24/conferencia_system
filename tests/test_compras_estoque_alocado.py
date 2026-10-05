"""Cobertura por estoque do Compras/Facilities/Solicitação de NF.

tproduto.estoque / estoque_disponivel_uso não acompanham o saldo do GRV
(05/10/2026: nulo em 8.095 produtos - todas as chapas - e divergente em
2.602). As consultas leem tproduto_deposito (depósito 1) e o Compras aloca
o disponível entre as OS abertas por ordem de necessidade. Validado contra o
GRV real; aqui só se trava a regressão (teste não depende de rede)."""
import re

from conferencia_app.compras import queries
from conferencia_app.services import solicitacao_nf_service
from conferencia_app.services.facilities_grv_service import FacilitiesGRVService
import inspect

CAMPOS_DESATUALIZADOS = re.compile(r"\bp\.estoque_disponivel_uso\b|\bp\.estoque\b|\bp\.estoque_reservado\b")


def test_consultas_de_compras_nao_leem_saldo_do_cadastro():
    for nome in ("SQL_MATERIAIS_POR_OS", "SQL_OS_PAINEL", "SQL_VISIBILITY_DETALHADA"):
        sql = getattr(queries, nome)
        assert not CAMPOS_DESATUALIZADOS.search(sql), nome
        assert "estoque_alocado" in sql, nome


def test_alocacao_respeita_ordem_de_necessidade_e_deposito_1():
    cte = queries._ALOCACAO_ESTOQUE_CTE
    assert "pd.cod_deposito = 1" in cte
    assert "ORDER BY l.dt_necessidade" in cte
    assert "ROWS UNBOUNDED PRECEDING" in cte


def test_reservado_vem_antes_de_coberto():
    for nome in ("SQL_MATERIAIS_POR_OS", "SQL_VISIBILITY_DETALHADA"):
        sql = getattr(queries, nome)
        assert sql.index("'RESERVADO'") < sql.index("'COBERTO ESTOQUE'"), nome


def test_cte_compartilhada_nao_vira_consulta_da_bridge():
    # A bridge expõe toda constante SQL_* do módulo; a CTE é só um trecho.
    catalogo = {n for n in vars(queries) if n.startswith("SQL_")}
    assert "_ALOCACAO_ESTOQUE_CTE" not in catalogo
    assert all("estoque_alocado AS (" not in v or v.lstrip().startswith("WITH") for n, v in vars(queries).items() if n.startswith("SQL_") and isinstance(v, str))


def test_solicitacao_nf_e_facilities_leem_deposito():
    for sql in (solicitacao_nf_service.SQL_MATERIAL_BUSCAR, solicitacao_nf_service.SQL_MATERIAL_POR_CODIGO):
        assert "tproduto_deposito" in sql and "as estoque_disponivel_uso" in sql
    fonte = inspect.getsource(FacilitiesGRVService._listar_materiais_epi_uniforme_postgres)
    assert "tproduto_deposito" in fonte and not CAMPOS_DESATUALIZADOS.search(fonte)
