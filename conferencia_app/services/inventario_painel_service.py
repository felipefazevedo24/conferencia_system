"""Painel do Inventario: quanto foi ajustado, o que esta' em andamento,
acuracidade das contagens e os maiores ajustes. Pedido da Logistica em
27/11/2026, no molde do painel do Estoque.

Regras:
  - Ajustado = o Financeiro executou o ajuste no ERP (ele e' quem mexe no
    saldo): item no Fiscal ou ja' Concluido, com data de execucao. O mes e' o
    da execucao. O estorno limpa essa data, entao item estornado sai sozinho.
  - Saida avulsa (perda ja' baixada no ERP, Concluido sem passar pelo
    Financeiro) entra no ajustado, separada, pelo mes da baixa.
  - Valor = diferenca vigente x custo medio, a mesma conta do FORM-08.52.
    Item sem custo conta nos itens mas nao no R$.
  - Acuracidade = contagens registradas (avulso e fechamento do ciclico) que
    bateram com o saldo do GRV no momento da contagem.
"""
from __future__ import annotations

from datetime import datetime

from ..models import LogisticaInventarioAjuste as Ajuste, LogisticaInventarioInicial as Contagem
from ..tempo import agora_br
from .logistica_inventario_ajuste_service import TOLERANCIA_DIVERGENCIA

PERIODO_12M = "12m"
MESES_SERIE = 12
ETAPAS_ANDAMENTO = [
    ("Validacao", "Validação de Suprimentos"),
    ("Relatorio", "Emissão do Formulário"),
    ("Finance", "Ajuste do Financeiro"),
    ("Fiscal", "Emissão Fiscal (NF)"),
]
LABEL_ETAPA = dict(ETAPAS_ANDAMENTO) | {"Concluido": "Concluído", "Descartado": "Improcedente"}
# Prefixo que o ciclico grava na contagem que fecha a linha (inventario_ciclico_service._fechar).
OBS_CICLICO = "Inventário cíclico "


def meses_ate(hoje: datetime, quantidade: int = MESES_SERIE) -> list[str]:
    ano, mes = hoje.year, hoje.month
    saida = []
    for _ in range(quantidade):
        saida.append(f"{ano:04d}-{mes:02d}")
        mes -= 1
        if mes == 0:
            ano, mes = ano - 1, 12
    return saida[::-1]


def _valor(a: Ajuste) -> float | None:
    if a.custo_medio is None:
        return None
    return float(a.diferenca_vigente or 0) * float(a.custo_medio)


def _ajustado(a: Ajuste) -> tuple[str, datetime] | None:
    """(tipo, data) se o ajuste ja' conta como ajustado; None se ainda nao."""
    if a.status_modulo in ("Fiscal", "Concluido") and a.finance_concluido_em:
        return "financeiro", a.finance_concluido_em
    if a.status_modulo == "Concluido" and a.saida_avulsa_numero and not a.finance_concluido_em:
        data = a.saida_avulsa_em or (a.relatorio.criado_em if a.relatorio else None) or a.criado_em
        return "saida_avulsa", data
    return None


def _somar(destino: dict, tipo: str, valor: float | None) -> None:
    destino["itens"] += 1
    if valor is None:
        destino["sem_custo"] += 1
        return
    if tipo == "saida_avulsa":
        destino["saida_avulsa"] += abs(valor)
    elif valor < 0:
        destino["perda"] += -valor
    else:
        destino["sobra"] += valor


def _zerado() -> dict:
    return {"perda": 0.0, "sobra": 0.0, "saida_avulsa": 0.0, "itens": 0, "sem_custo": 0}


def _fechar(t: dict) -> dict:
    t = dict(t)
    t["liquido"] = round(t["sobra"] - t["perda"] - t["saida_avulsa"], 2)
    for k in ("perda", "sobra", "saida_avulsa"):
        t[k] = round(t[k], 2)
    return t


def linha_ajuste(a: Ajuste, tipo: str | None = None, data: datetime | None = None) -> dict:
    valor = _valor(a)
    rel = a.relatorio
    return {
        "id": a.id,
        "codigo": a.codigo_produto,
        "descricao": a.descricao_produto or "",
        "local": a.local_codigo,
        "unidade": a.unidade_medida,
        "diferenca": float(a.diferenca_vigente or 0),
        "custo_medio": a.custo_medio,
        "valor": round(valor, 2) if valor is not None else None,
        "etapa": a.status_modulo,
        "etapa_label": LABEL_ETAPA.get(a.status_modulo, a.status_modulo),
        "tipo": tipo,
        "data": data.strftime("%d/%m/%Y") if data else None,
        "formulario": rel.numero_documento if rel else None,
        "motivo": (rel.motivo_ajuste if rel else None) or a.motivo_inventario or "",
        "documento_grv": a.finance_documento_grv,
        "saida_avulsa_numero": a.saida_avulsa_numero,
        "nf": a.fiscal_nf_numero,
        "justificativa": a.gestor_justificativa or "",
        "criado_em": a.criado_em.strftime("%d/%m/%Y") if a.criado_em else None,
    }


def _no_periodo(mes: str, periodo: str, meses: list[str]) -> bool:
    return mes in meses if periodo == PERIODO_12M else mes == periodo


def montar(periodo: str | None = None, hoje: datetime | None = None) -> dict:
    hoje = hoje or agora_br()
    meses = meses_ate(hoje)
    if periodo != PERIODO_12M and periodo not in meses:
        periodo = meses[-1]

    serie = {m: _zerado() for m in meses}
    total, no_periodo = _zerado(), _zerado()
    andamento = {etapa: {"etapa": etapa, "label": label, "itens": 0, "perda": 0.0, "sobra": 0.0, "sem_custo": 0}
                 for etapa, label in ETAPAS_ANDAMENTO}
    ajustados_periodo = []

    for a in Ajuste.query.filter(Ajuste.status_modulo != "Descartado").all():
        if a.status_modulo in andamento:
            e = andamento[a.status_modulo]
            e["itens"] += 1
            v = _valor(a)
            if v is None:
                e["sem_custo"] += 1
            elif v < 0:
                e["perda"] += -v
            else:
                e["sobra"] += v
        ajustado = _ajustado(a)
        if not ajustado:
            continue
        tipo, data = ajustado
        valor = _valor(a)
        _somar(total, tipo, valor)
        mes = data.strftime("%Y-%m") if data else ""
        if mes in serie:
            _somar(serie[mes], tipo, valor)
        if _no_periodo(mes, periodo, meses):
            _somar(no_periodo, tipo, valor)
            ajustados_periodo.append(linha_ajuste(a, tipo, data))

    for e in andamento.values():
        e["perda"], e["sobra"] = round(e["perda"], 2), round(e["sobra"], 2)
        e["liquido"] = round(e["sobra"] - e["perda"], 2)

    contagens = {m: {"total": 0, "avulso": 0, "ciclico": 0, "comparadas": 0, "bateram": 0} for m in meses}
    inicio = datetime.strptime(meses[0] + "-01", "%Y-%m-%d")
    linhas = (
        Contagem.query.with_entities(Contagem.criado_em, Contagem.quantidade, Contagem.qtde_grv_no_momento, Contagem.observacao)
        .filter(Contagem.criado_em >= inicio).all()
    )
    for criado_em, qtd, grv, obs in linhas:
        c = contagens.get(criado_em.strftime("%Y-%m"))
        if c is None:
            continue
        c["total"] += 1
        c["ciclico" if str(obs or "").startswith(OBS_CICLICO) else "avulso"] += 1
        if grv is not None:
            c["comparadas"] += 1
            if abs(float(qtd or 0) - float(grv)) <= TOLERANCIA_DIVERGENCIA:
                c["bateram"] += 1
    for c in contagens.values():
        c["acuracidade"] = round(c["bateram"] / c["comparadas"] * 100, 1) if c["comparadas"] else None

    cont_periodo = {"total": 0, "avulso": 0, "ciclico": 0, "comparadas": 0, "bateram": 0}
    for m, c in contagens.items():
        if _no_periodo(m, periodo, meses):
            for k in cont_periodo:
                cont_periodo[k] += c[k]
    cont_periodo["acuracidade"] = (
        round(cont_periodo["bateram"] / cont_periodo["comparadas"] * 100, 1) if cont_periodo["comparadas"] else None
    )

    maiores = sorted(ajustados_periodo, key=lambda x: abs(x["valor"] or 0), reverse=True)
    return {
        "periodo": periodo,
        "meses": meses,
        "mes_atual": meses[-1],
        "ajustado_periodo": _fechar(no_periodo),
        "ajustado_total": _fechar(total),
        "serie": [{"mes": m, **_fechar(serie[m])} for m in meses],
        "andamento": list(andamento.values()),
        "contagens": [{"mes": m, **contagens[m]} for m in meses],
        "contagens_periodo": cont_periodo,
        "maiores": maiores[:20],
        "ajustados_periodo": maiores,
    }


def em_andamento() -> list[dict]:
    """Itens de cada etapa, pro Excel."""
    etapas = [e for e, _ in ETAPAS_ANDAMENTO]
    itens = Ajuste.query.filter(Ajuste.status_modulo.in_(etapas)).order_by(Ajuste.criado_em).all()
    ordem = {e: i for i, e in enumerate(etapas)}
    return [linha_ajuste(a) for a in sorted(itens, key=lambda a: ordem[a.status_modulo])]


MESES_NOME = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"]


def rotulo_mes(mes: str) -> str:
    return f"{MESES_NOME[int(mes[5:]) - 1]}/{mes[2:4]}"


def gerar_excel(periodo: str | None = None) -> tuple[bytes, str]:
    """Planilha do painel: resumo mes a mes, ajustados do periodo e o que
    esta' em andamento. Devolve (bytes, periodo: "2026-11" ou "12m")."""
    from io import BytesIO

    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    dados = montar(periodo)
    moeda = '"R$" #,##0.00'
    moeda_sinal = '+"R$" #,##0.00;-"R$" #,##0.00;"R$" 0,00'
    qtde = '#,##0.###'
    cabecalho_fill = PatternFill("solid", fgColor="1B3B6F")
    cabecalho_font = Font(bold=True, color="FFFFFF")

    def aba(ws, titulo, colunas, linhas):
        """colunas: (nome, largura, formato|None)."""
        ws.title = titulo
        ws.append([c[0] for c in colunas])
        for cel in ws[1]:
            cel.fill, cel.font = cabecalho_fill, cabecalho_font
            cel.alignment = Alignment(vertical="center", wrap_text=True)
        ws.row_dimensions[1].height = 30
        for linha in linhas:
            ws.append(linha)
        for i, (_, largura, formato) in enumerate(colunas, start=1):
            letra = get_column_letter(i)
            ws.column_dimensions[letra].width = largura
            if formato:
                for (cel,) in ws.iter_rows(min_row=2, min_col=i, max_col=i):
                    cel.number_format = formato
        ws.freeze_panes = "A2"
        if linhas:
            ws.auto_filter.ref = ws.dimensions

    wb = Workbook()
    cont = {c["mes"]: c for c in dados["contagens"]}
    resumo = []
    for s in dados["serie"]:
        c = cont[s["mes"]]
        resumo.append([
            rotulo_mes(s["mes"]), s["perda"], s["sobra"], s["saida_avulsa"], s["liquido"], s["itens"], s["sem_custo"],
            c["total"], c["avulso"], c["ciclico"], c["comparadas"], c["bateram"],
            (c["acuracidade"] / 100) if c["acuracidade"] is not None else None,
        ])
    t = dados["ajustado_total"]
    resumo.append(["Total (desde o início)", t["perda"], t["sobra"], t["saida_avulsa"], t["liquido"], t["itens"], t["sem_custo"],
                   None, None, None, None, None, None])
    aba(wb.active, "Resumo mensal", [
        ("Mês", 22, None), ("Perda ajustada", 16, moeda), ("Sobra ajustada", 16, moeda), ("Saída avulsa", 15, moeda),
        ("Líquido", 16, moeda_sinal), ("Itens ajustados", 11, "0"), ("Sem custo", 10, "0"),
        ("Contagens", 11, "0"), ("Avulso", 9, "0"), ("Cíclico", 9, "0"), ("Comparadas c/ GRV", 12, "0"),
        ("Bateram", 10, "0"), ("Acuracidade", 12, "0.0%"),
    ], resumo)
    ultima = wb.active.max_row
    for cel in wb.active[ultima]:
        cel.font = Font(bold=True)

    colunas_item = [
        ("Data", 12, None), ("Código", 16, None), ("Descrição", 40, None), ("Local", 14, None), ("Un.", 6, None),
        ("Diferença", 12, qtde), ("Custo médio", 13, moeda), ("Valor", 15, moeda_sinal), ("Tipo", 14, None),
        ("Etapa", 22, None), ("FORM-08.52", 14, None), ("Motivo", 26, None), ("Doc. GRV", 12, None),
        ("Saída avulsa nº", 14, None), ("NF", 10, None), ("Justificativa", 50, None),
    ]

    def linha(x):
        tipo = {"financeiro": "Financeiro", "saida_avulsa": "Saída avulsa"}.get(x["tipo"] or "", "")
        return [x["data"] or x["criado_em"], x["codigo"], x["descricao"], x["local"], x["unidade"], x["diferenca"],
                x["custo_medio"], x["valor"], tipo, x["etapa_label"], x["formulario"] or "", x["motivo"],
                x["documento_grv"] or "", x["saida_avulsa_numero"] or "", x["nf"] or "", x["justificativa"]]

    aba(wb.create_sheet(), "Ajustados no período", colunas_item, [linha(x) for x in dados["ajustados_periodo"]])
    andamento = em_andamento()
    colunas_and = [("Detectado em", 12, None)] + colunas_item[1:8] + [("Etapa", 24, None), ("Justificativa", 50, None)]
    aba(wb.create_sheet(), "Em andamento", colunas_and, [
        [x["criado_em"], x["codigo"], x["descricao"], x["local"], x["unidade"], x["diferenca"], x["custo_medio"],
         x["valor"], x["etapa_label"], x["justificativa"]] for x in andamento
    ])

    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue(), dados["periodo"]
