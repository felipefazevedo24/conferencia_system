"""CMV do painel de Estoque (pedido de 21/11/2026):

    CMV = EI + C + DV - DC - EF

- EI / EF: valor em estoque no fim do mês anterior / do mês, o mesmo da
  linha do gráfico (informado à mão ou gravado; estoque_foto_service).
- C: compras do mês (CFOP de compra, a mesma regra do Spend), pelo valor
  da NF LÍQUIDO dos impostos recuperáveis (custo_da_nf do Cardex). O
  estoque é valorizado pelo custo médio, que já é líquido: com o valor
  cheio da nota o CMV sairia inflado pelos impostos.
- DV: devolução de venda volta ao estoque pelo custo (o do kardex da
  entrada; sem ele, o custo médio atual), não pelo preço de venda.
- DC: devolução de compra (NF de saída), pelo valor da NF líquido dos
  impostos; sem valor reconhecido, quantidade x custo médio atual.

Só entram itens das famílias das visões do Estoque (classificar). C, DV e
DC vêm do GRV e ficam no espelho EstoqueCmvMes, regravado a partir do mês
anterior ao da última atualização boa (pega nota lançada com atraso).
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from typing import Callable

from ..compras import db as grv
from ..compras import queries
from ..compras.config import get_settings
from ..extensions import db
from ..models import EstoqueCmvMes as Mes
from ..models import EstoqueCmvSync as Sync
from ..tempo import agora_br
from .compras_spend_service import cfop_e_compra
from .logistica_cardex_service import custo_da_nf

# Sufixo do CFOP de entrada que é devolução de venda (inclui a de venda com
# ST, x410/x411, e a de exportação, x503/x504).
CFOP_DEVOLUCAO_VENDA = frozenset({"201", "202", "203", "204", "410", "411", "503", "504"})
IDADE_MAXIMA_H = 6
ANDAMENTO_EXPIRA_MIN = 20
# Na primeira atualização: os meses do gráfico (12 fechados + o corrente)
# e o anterior a eles.
MESES_INICIAIS = 14


def _ultima(status: str | None = None) -> Sync | None:
    q = Sync.query
    if status:
        q = q.filter(Sync.status == status)
    return q.order_by(Sync.id.desc()).first()


def status() -> dict:
    agora = agora_br()
    ok = _ultima(Sync.STATUS_OK)
    ultima = _ultima()
    em_andamento = bool(
        ultima and ultima.status == Sync.STATUS_ANDAMENTO
        and ultima.iniciado_em > agora - timedelta(minutes=ANDAMENTO_EXPIRA_MIN)
    )
    return {
        "atualizado_em": ok.concluido_em.strftime("%d/%m/%Y %H:%M") if ok and ok.concluido_em else None,
        "desatualizado": not ok or not ok.concluido_em or agora - ok.concluido_em > timedelta(hours=IDADE_MAXIMA_H),
        "em_andamento": em_andamento,
        "erro": ultima.erro if ultima and ultima.status == Sync.STATUS_ERRO else None,
    }


def _mes_anterior(mes: date) -> date:
    return (mes - timedelta(days=1)).replace(day=1)


def _meses(desde: date, hoje: date) -> list[tuple[date, date]]:
    meses = []
    ini = desde.replace(day=1)
    while ini <= hoje:
        fim = (ini + timedelta(days=32)).replace(day=1)
        meses.append((ini, fim))
        ini = fim
    return meses


def _num(valor) -> float:
    try:
        return float(valor or 0)
    except (TypeError, ValueError):
        return 0.0


def valor_entrada(r: dict) -> tuple[str, float] | None:
    """('compras' | 'devolucao_vendas', valor) de uma linha de entrada, ou
    None se o CFOP não é nenhum dos dois."""
    cfop = str(r.get("cfop") or "")
    if len(cfop) != 4:
        return None
    if cfop[1:] in CFOP_DEVOLUCAO_VENDA:
        campos = r.get("campos") or {}
        qtde = _num(campos.get("qtde_estoque")) or _num(campos.get("qtde"))
        custo = _num(r.get("custo_kardex")) or _num(r.get("preco_custo"))
        return "devolucao_vendas", qtde * custo
    # Mesma regra do Spend: CFOP de compra e tipo de movimento que lança
    # financeiro (o resto é remessa, retorno, bonificação...).
    if cfop_e_compra(cfop) and int(_num(r.get("lancar_financeiro"))) == 1:
        nf = custo_da_nf([r.get("campos") or {}], cfop)
        return ("compras", nf["valor_liquido"]) if nf else None
    return None


def valor_devolucao_compra(r: dict) -> float:
    nf = custo_da_nf([r.get("campos") or {}], str(r.get("cfop") or ""))
    if nf and nf["valor_liquido"] > 0:
        return nf["valor_liquido"]
    return _num(r.get("qtde")) * _num(r.get("preco_custo"))


def atualizar(usuario: str, classificar: Callable[[str, str], str | None]) -> dict:
    """Regrava C, DV e DC por mês e visão. `classificar(familia, grupo)`
    devolve a visão do item (ou None: fora do Estoque)."""
    if status()["em_andamento"]:
        return {"ja_em_andamento": True}
    hoje = agora_br().date()
    ok = _ultima(Sync.STATUS_OK)
    if ok and ok.concluido_em:
        desde = _mes_anterior(ok.concluido_em.date().replace(day=1))
    else:
        desde = hoje.replace(day=1)
        for _ in range(MESES_INICIAIS - 1):
            desde = _mes_anterior(desde)
    sync = Sync(status=Sync.STATUS_ANDAMENTO, iniciado_em=agora_br(), iniciado_por=usuario, desde=desde.strftime("%Y-%m"))
    db.session.add(sync)
    db.session.commit()
    try:
        empresa = get_settings().PG_COD_EMPRESA
        totais: dict[tuple[str, str], dict[str, float]] = defaultdict(
            lambda: {"compras": 0.0, "devolucao_vendas": 0.0, "devolucao_compras": 0.0, "linhas": 0})
        for ini, fim in _meses(desde, hoje):
            params = {"cod_empresa": empresa, "data_de": ini.isoformat(), "data_ate": fim.isoformat()}
            for r in grv.fetch_all(queries.SQL_ESTOQUE_CMV_ENTRADAS, params):
                visao = classificar(r.get("familia") or "", r.get("grupo") or "")
                parte = valor_entrada(r) if visao else None
                if parte:
                    t = totais[(r["competencia"], visao)]
                    t[parte[0]] += parte[1]
                    t["linhas"] += 1
            for r in grv.fetch_all(queries.SQL_ESTOQUE_CMV_DEV_COMPRAS, params):
                visao = classificar(r.get("familia") or "", r.get("grupo") or "")
                if visao:
                    t = totais[(r["competencia"], visao)]
                    t["devolucao_compras"] += valor_devolucao_compra(r)
                    t["linhas"] += 1
        Mes.query.filter(Mes.competencia >= desde.strftime("%Y-%m")).delete(synchronize_session=False)
        for (competencia, visao), t in totais.items():
            db.session.add(Mes(
                competencia=competencia, visao=visao, linhas=int(t["linhas"]),
                compras=round(t["compras"], 2), devolucao_vendas=round(t["devolucao_vendas"], 2),
                devolucao_compras=round(t["devolucao_compras"], 2),
            ))
        sync.status = Sync.STATUS_OK
        sync.linhas = sum(int(t["linhas"]) for t in totais.values())
        sync.concluido_em = agora_br()
        db.session.commit()
        return {"linhas": sync.linhas, "desde": sync.desde}
    except Exception as exc:  # noqa: BLE001 - GRV fora nao pode deixar a atualizacao "em andamento" para sempre
        db.session.rollback()
        sync = db.session.get(Sync, sync.id)
        sync.status = Sync.STATUS_ERRO
        sync.erro = str(exc)[:500]
        sync.concluido_em = agora_br()
        db.session.commit()
        raise ValueError("Não deu para buscar as compras e devoluções do GRV agora. Tente de novo em instantes.") from exc


def movimentos_por_mes(visoes: list[str], meses: list[str]) -> dict[str, dict[str, dict[str, float]]]:
    """{mes: {visao: {compras, devolucao_vendas, devolucao_compras}}}."""
    resultado: dict[str, dict[str, dict[str, float]]] = {}
    for linha in Mes.query.filter(Mes.competencia.in_(meses), Mes.visao.in_(visoes)).all():
        resultado.setdefault(linha.competencia, {})[linha.visao] = {
            "compras": float(linha.compras or 0),
            "devolucao_vendas": float(linha.devolucao_vendas or 0),
            "devolucao_compras": float(linha.devolucao_compras or 0),
        }
    return resultado


def calcular(meses: list[str], estoque_fim: dict[str, dict[str, float]], movimentos: dict, visoes: list[str]) -> list[dict | None]:
    """CMV de cada mês de `meses` (o 1º é só a abertura e não sai na série).
    `estoque_fim[mes][visao]` = EF. Entram só as visões com estoque no fim
    do mês e do anterior; as que faltam vão em `sem_estoque` (C, DV e DC
    delas ficam de fora também, senão a conta mistura bases)."""
    serie = []
    for anterior, mes in zip(meses, meses[1:]):
        ini, fim = estoque_fim.get(anterior) or {}, estoque_fim.get(mes) or {}
        com = [v for v in visoes if v in ini and v in fim]
        if not com:
            serie.append(None)
            continue
        mov = movimentos.get(mes) or {}
        partes = {
            "ei": sum(ini[v] for v in com),
            "ef": sum(fim[v] for v in com),
            "compras": sum((mov.get(v) or {}).get("compras", 0.0) for v in com),
            "devolucao_vendas": sum((mov.get(v) or {}).get("devolucao_vendas", 0.0) for v in com),
            "devolucao_compras": sum((mov.get(v) or {}).get("devolucao_compras", 0.0) for v in com),
        }
        cmv = partes["ei"] + partes["compras"] + partes["devolucao_vendas"] - partes["devolucao_compras"] - partes["ef"]
        serie.append({
            "cmv": round(cmv, 2),
            **{k: round(v, 2) for k, v in partes.items()},
            "sem_estoque": [v for v in visoes if v not in com],
        })
    return serie
