"""Faturamento x Custo (aba Faturamento).

Fonte: API do ERP (columbia.consultoriarf.net), um item de NF por linha:
  /custo_faturamento   saida:   vl_faturado, impostos, faturado_menos_impostos
                                (= vl_faturado - impostos)
  /custo_recebimento   entrada: vl_produto, icms, pis, cofins, ipi, custo_total
                                (= vl_produto + ipi - icms - pis - cofins)
As duas contas foram conferidas em 19/11/2026 contra a resposta real (3.200 e
7.330 linhas): fecham em todas. Os impostos ja vem deduzidos na origem, o
Sync nao recalcula nada.

Definicoes acertadas com o usuario em 19/11/2026:
  faturamento bruto = vl_faturado        faturamento net = faturado_menos_impostos
  custo bruto       = vl_produto (s/IPI) custo net (= custo total) = custo_total

Os endpoints nao tem filtro de periodo e devolvem o ano inteiro devagar (o de
recebimento ~3 min). Por isso a tela le um espelho local; a atualizacao roda
numa thread e a tela acompanha pelo status. Totais por mes sao calculados na
leitura, nunca gravados.
"""
from __future__ import annotations

import threading
from datetime import date, datetime, timedelta

import requests
from flask import current_app
from sqlalchemy import func

from ..extensions import db
from ..models import FaturamentoCustoEntrada as Custo
from ..models import FaturamentoSaida as Saida
from ..models import FaturamentoSync as Sync
from ..tempo import agora_br

URL_BASE_PADRAO = "https://columbia.consultoriarf.net"
# Passado disso a tela abre e ja pede a atualizacao por tras.
IDADE_MAXIMA_MIN = 360
# Atualizacao "em andamento" mais velha que isso morreu (reload do servidor
# no meio): libera uma nova.
ANDAMENTO_EXPIRA_MIN = 20

ERRO_API = "Não deu para buscar os dados de faturamento e custo no ERP agora. Tente de novo em instantes."


def _config(nome: str, padrao):
    return current_app.config.get(nome, padrao) if current_app else padrao


def _num(valor) -> float:
    # a API manda ipi "" em algumas linhas
    try:
        return float(valor or 0)
    except (TypeError, ValueError):
        return 0.0


def _texto(valor, limite: int) -> str:
    return str(valor if valor is not None else "").strip()[:limite]


def _data(valor) -> date | None:
    try:
        return datetime.strptime(str(valor or "")[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _buscar(caminho: str) -> list[dict]:
    base = str(_config("FATURAMENTO_API_URL", URL_BASE_PADRAO) or "").strip().rstrip("/") or URL_BASE_PADRAO
    # timeout de leitura entre pedacos da resposta, nao do total: a API vai
    # mandando aos poucos por minutos.
    timeout = int(_config("FATURAMENTO_API_TIMEOUT", 300))
    resp = requests.get(
        f"{base}/{caminho}",
        headers={"Accept": "application/json", "User-Agent": "ColumbiaSync/1.0 (faturamento)"},
        timeout=(30, timeout),
    )
    resp.raise_for_status()
    dados = resp.json()
    if not isinstance(dados, list):
        raise ValueError(f"Resposta inesperada de /{caminho} (esperado uma lista).")
    return [r for r in dados if isinstance(r, dict)]


def _linha_saida(r: dict) -> dict | None:
    dia = _data(r.get("dt_emissao"))
    if not dia:
        return None
    return {
        "competencia": dia.strftime("%Y-%m"), "data_emissao": dia,
        "nota_fiscal": _texto(r.get("nf"), 20), "orcamento": _texto(r.get("orcamento"), 40),
        "cliente": _texto(r.get("cliente"), 200), "codigo": _texto(r.get("codigo"), 80),
        "produto": _texto(r.get("produto"), 400), "segmento": _texto(r.get("segmento"), 80),
        "valor_bruto": _num(r.get("vl_faturado")), "impostos": _num(r.get("impostos")),
        "valor_net": _num(r.get("faturado_menos_impostos")),
    }


def _linha_custo(r: dict) -> dict | None:
    dia = _data(r.get("dt_lancamento"))
    if not dia:
        return None
    return {
        "competencia": dia.strftime("%Y-%m"), "data_lancamento": dia,
        "nota_fiscal": _texto(r.get("n_nf"), 60), "fornecedor": _texto(r.get("fornecedor"), 200),
        "familia": _texto(r.get("familia"), 160), "grupo": _texto(r.get("grupo"), 160),
        "codigo_interno": _texto(r.get("codigo_interno"), 80), "item": _texto(r.get("item"), 400),
        "unidade": _texto(r.get("unidade"), 20), "quantidade": _num(r.get("qtde")),
        "valor_bruto": _num(r.get("vl_produto")), "icms": _num(r.get("icms")), "pis": _num(r.get("pis")),
        "cofins": _num(r.get("cofins")), "ipi": _num(r.get("ipi")), "custo_net": _num(r.get("custo_total")),
        "os_rastreada": _texto(r.get("os_rastreada"), 60), "segmento": _texto(r.get("segmento"), 80),
    }


def _gravar(modelo, linhas: list[dict]) -> None:
    # Regrava a partir do primeiro mes da resposta: hoje a API manda o ano
    # corrente, e na virada do ano isso preserva o ano anterior no espelho.
    # Do primeiro mes em diante apaga tudo, inclusive mes que sumiu da
    # resposta (nota cancelada), para nao ficar valor velho.
    if linhas:
        desde = min(x["competencia"] for x in linhas)
        modelo.query.filter(modelo.competencia >= desde).delete(synchronize_session=False)
    for i in range(0, len(linhas), 1000):
        db.session.bulk_insert_mappings(modelo, linhas[i:i + 1000])


# ---------------------------------------------------------------------------
# Atualizacao do espelho
# ---------------------------------------------------------------------------
def _ultima(situacao: str | None = None):
    q = Sync.query
    if situacao:
        q = q.filter(Sync.status == situacao)
    return q.order_by(Sync.id.desc()).first()


def status() -> dict:
    ok = _ultima(Sync.STATUS_OK)
    ultima = _ultima()
    agora = agora_br()
    andamento = bool(
        ultima and ultima.status == Sync.STATUS_ANDAMENTO
        and ultima.iniciado_em > agora - timedelta(minutes=ANDAMENTO_EXPIRA_MIN)
    )
    idade = int((agora - ok.concluido_em).total_seconds() // 60) if ok and ok.concluido_em else None
    return {
        "atualizado_em": ok.concluido_em.strftime("%d/%m/%Y %H:%M") if ok and ok.concluido_em else None,
        "idade_min": idade,
        "vazio": ok is None,
        "desatualizado": ok is None or idade is None or idade >= IDADE_MAXIMA_MIN,
        "em_andamento": andamento,
        "erro": ultima.erro if ultima and ultima.status == Sync.STATUS_ERRO else None,
    }


def _executar(sync_id: int) -> dict:
    try:
        saidas = [x for x in map(_linha_saida, _buscar("custo_faturamento")) if x]
        custos = [x for x in map(_linha_custo, _buscar("custo_recebimento")) if x]
        # Resposta vazia nas duas = API com problema, nao "nada faturado no
        # ano": nao marca como atualizado.
        if not saidas and not custos:
            raise ValueError("A API devolveu faturamento e custo vazios.")
        _gravar(Saida, saidas)
        _gravar(Custo, custos)
        sync = db.session.get(Sync, sync_id)
        sync.status = Sync.STATUS_OK
        sync.linhas_faturamento = len(saidas)
        sync.linhas_custo = len(custos)
        sync.concluido_em = agora_br()
        db.session.commit()
        return {"linhas_faturamento": len(saidas), "linhas_custo": len(custos)}
    except Exception as exc:  # noqa: BLE001 - API fora nao pode deixar a atualizacao "em andamento" para sempre
        db.session.rollback()
        sync = db.session.get(Sync, sync_id)
        sync.status = Sync.STATUS_ERRO
        sync.erro = str(exc)[:500]
        sync.concluido_em = agora_br()
        db.session.commit()
        raise ValueError(ERRO_API) from exc


def _novo_sync(usuario: str) -> int:
    sync = Sync(status=Sync.STATUS_ANDAMENTO, iniciado_em=agora_br(), iniciado_por=usuario)
    db.session.add(sync)
    db.session.commit()
    return sync.id


def atualizar(usuario: str) -> dict:
    """Atualiza na hora, esperando terminar (tarefa agendada)."""
    return _executar(_novo_sync(usuario))


def _em_thread(app, sync_id: int) -> None:
    def rodar():
        with app.app_context():
            try:
                _executar(sync_id)
            except ValueError:
                pass  # o erro ficou gravado no FaturamentoSync; a tela le pelo status
            finally:
                db.session.remove()

    threading.Thread(target=rodar, daemon=True, name=f"faturamento-sync-{sync_id}").start()


def iniciar_atualizacao(usuario: str) -> dict:
    """Dispara a atualizacao por tras e volta na hora: a busca leva minutos e
    nao pode segurar a requisicao. Se ja tiver uma rodando, nao abre outra."""
    if status()["em_andamento"]:
        return {"ja_em_andamento": True}
    _em_thread(current_app._get_current_object(), _novo_sync(usuario))
    return {"iniciada": True}


# ---------------------------------------------------------------------------
# Painel
# ---------------------------------------------------------------------------
def _competencia_valida(valor) -> str | None:
    texto = str(valor or "").strip()
    try:
        datetime.strptime(texto, "%Y-%m")
    except ValueError:
        return None
    return texto if len(texto) == 7 else None


def _meses_entre(de: str, ate: str) -> list[str]:
    a, m = int(de[:4]), int(de[5:])
    fim = (int(ate[:4]), int(ate[5:]))
    saida = []
    while (a, m) <= fim:
        saida.append(f"{a:04d}-{m:02d}")
        a, m = (a + 1, 1) if m == 12 else (a, m + 1)
    return saida


def _somas(modelo, campos, de: str, ate: str) -> dict:
    q = (db.session.query(modelo.competencia, *[func.sum(getattr(modelo, c)) for c in campos])
         .filter(modelo.competencia >= de, modelo.competencia <= ate)
         .group_by(modelo.competencia))
    return {linha[0]: [float(v or 0) for v in linha[1:]] for linha in q}


def painel(de=None, ate=None) -> dict:
    base = status()
    existentes = sorted(
        {c for (c,) in db.session.query(Saida.competencia).distinct()}
        | {c for (c,) in db.session.query(Custo.competencia).distinct()}
    )
    if not existentes:
        return {"vazio": True, "base": base}
    competencias = _meses_entre(existentes[0], existentes[-1])
    ultimo = competencias[-1]
    # padrao: o ano do ultimo mes com dado, de janeiro ate ele
    de = _competencia_valida(de) or max(competencias[0], ultimo[:4] + "-01")
    ate = _competencia_valida(ate) or ultimo
    if de > ate:
        de, ate = ate, de

    fat = _somas(Saida, ("valor_bruto", "valor_net"), de, ate)
    custo = _somas(Custo, ("valor_bruto", "custo_net"), de, ate)
    por_mes = []
    for c in _meses_entre(de, ate):
        fb, fn = fat.get(c, (0.0, 0.0))
        cb, cn = custo.get(c, (0.0, 0.0))
        por_mes.append({"competencia": c, "fat_bruto": round(fb, 2), "fat_net": round(fn, 2),
                        "custo_bruto": round(cb, 2), "custo_net": round(cn, 2)})
    kpis = {k: round(sum(m[k] for m in por_mes), 2) for k in ("fat_bruto", "fat_net", "custo_bruto", "custo_net")}
    return {
        "vazio": False,
        "base": base,
        "competencias": competencias,
        "filtro": {"de": de, "ate": ate},
        "por_mes": por_mes,
        "kpis": kpis,
    }


# ---------------------------------------------------------------------------
# Excel
# ---------------------------------------------------------------------------
# Colunas na ordem da API, com o nome do campo de la' entre parenteses quando
# o titulo muda: quem confere contra a API acha a coluna. (titulo, atributo, formato, largura)
_DATA, _VALOR, _QTDE = "DD/MM/YYYY", "#,##0.00", "#,##0.####"
_XLSX_ENTRADAS = (
    ("NF (n_nf)", "nota_fiscal", None, 12), ("Data lançamento", "data_lancamento", _DATA, 14),
    ("Fornecedor", "fornecedor", None, 40), ("Família", "familia", None, 34), ("Grupo", "grupo", None, 24),
    ("Código interno", "codigo_interno", None, 15), ("Item", "item", None, 50), ("Unidade", "unidade", None, 9),
    ("Qtde", "quantidade", _QTDE, 10), ("Valor produto (vl_produto)", "valor_bruto", _VALOR, 16),
    ("ICMS", "icms", _VALOR, 12), ("PIS", "pis", _VALOR, 12), ("COFINS", "cofins", _VALOR, 12),
    ("IPI", "ipi", _VALOR, 12), ("Custo total (custo_total)", "custo_net", _VALOR, 16),
    ("OS rastreada", "os_rastreada", None, 13), ("Segmento", "segmento", None, 16),
)
_XLSX_FATURAMENTO = (
    ("Data emissão", "data_emissao", _DATA, 13), ("NF", "nota_fiscal", None, 10), ("Orçamento", "orcamento", None, 11),
    ("Cliente", "cliente", None, 40), ("Código", "codigo", None, 15), ("Produto", "produto", None, 50),
    ("Segmento", "segmento", None, 18), ("Valor faturado (vl_faturado)", "valor_bruto", _VALOR, 17),
    ("Impostos", "impostos", _VALOR, 14), ("Faturado menos impostos", "valor_net", _VALOR, 17),
)


def _aba(wb, titulo: str, colunas, registros) -> None:
    from openpyxl.styles import Font, PatternFill

    ws = wb.create_sheet(titulo)
    for col, (cab, _, _, largura) in enumerate(colunas, start=1):
        cel = ws.cell(row=1, column=col, value=cab)
        cel.font = Font(bold=True, color="FFFFFF")
        cel.fill = PatternFill(start_color="0F62C9", end_color="0F62C9", fill_type="solid")
        ws.column_dimensions[cel.column_letter].width = largura
    for lin, r in enumerate(registros, start=2):
        for col, (_, attr, formato, _) in enumerate(colunas, start=1):
            cel = ws.cell(row=lin, column=col, value=getattr(r, attr))
            if formato:
                cel.number_format = formato
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions


def exportar_xlsx():
    """Tudo o que o espelho tem (sem o filtro da tela, pedido de 23/11/2026):
    aba Entradas (custo_recebimento) e aba Faturamento (custo_faturamento).
    Devolve (BytesIO, nome do arquivo) ou None com o espelho vazio."""
    from io import BytesIO

    from openpyxl import Workbook

    base = status()
    if base["vazio"]:
        return None
    wb = Workbook()
    wb.remove(wb.active)
    _aba(wb, "Entradas", _XLSX_ENTRADAS, Custo.query.order_by(Custo.data_lancamento, Custo.nota_fiscal, Custo.id))
    _aba(wb, "Faturamento", _XLSX_FATURAMENTO, Saida.query.order_by(Saida.data_emissao, Saida.nota_fiscal, Saida.id))
    buffer = BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    quando = datetime.strptime(base["atualizado_em"], "%d/%m/%Y %H:%M").strftime("%Y-%m-%d-%Hh%M")
    return buffer, f"faturamento-entradas-{quando}.xlsx"
