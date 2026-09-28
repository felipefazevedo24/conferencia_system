"""Estoque mínimo e lote econômico sugeridos para a tela de Estoque.

O histórico vem do GRV (bridge /api/erp/estoque/planejamento): consumo mês a
mês desde 2019, tamanho das retiradas, lead time OC -> entrada e custo. Aqui
só entram as contas, pra poderem mudar sem redeploy da VM do bridge.

Por que não basta "saldo ÷ consumo médio": item que sai uma vez a cada tantos
meses tem média diária que não acontece nunca (o tubo metalon com 1 saída de
25 m em 90 dias virava "1 dia de cobertura"). Então a demanda é classificada
antes (Syntetos-Boylan: intervalo médio entre meses com saída × variação do
tamanho) e cada tipo tem sua conta:

- regular / irregular: ponto de pedido = consumo no lead time + estoque de
  segurança (z do nível de serviço da curva ABC, variação do consumo E do
  prazo do fornecedor);
- intermitente / esporádico: o mínimo é uma retirada típica (percentil 80
  das retiradas de 24 meses) - média não serve pra dimensionar;
- sem giro (nada em 12 meses): não sugere estocar.

Todo o consumo entra na conta, inclusive o de material comprado para uma OS
específica; a tela só mostra quanto dele foi (decisão da logística em
28/09/2026).
"""
from __future__ import annotations

import math
from datetime import date
from statistics import NormalDist, mean, pstdev
from typing import Any

from ..extensions import db
from ..models import EstoquePlanejamentoParametro
from ..tempo import agora_br


PADROES = {
    # Provisório até a logística levantar o custo real de emitir uma OC.
    "custo_pedido": 150.0,
    "taxa_manutencao_pct": 25.0,
    "nivel_servico_a_pct": 98.0,
    "nivel_servico_b_pct": 95.0,
    "nivel_servico_c_pct": 90.0,
    "lead_time_padrao_dias": 15,
}
_LIMITES = {
    "custo_pedido": (0.01, 100000.0, "Custo de emitir um pedido"),
    "taxa_manutencao_pct": (1.0, 100.0, "Custo de manter estoque (% ao ano)"),
    "nivel_servico_a_pct": (50.0, 99.9, "Nível de serviço da curva A"),
    "nivel_servico_b_pct": (50.0, 99.9, "Nível de serviço da curva B"),
    "nivel_servico_c_pct": (50.0, 99.9, "Nível de serviço da curva C"),
    "lead_time_padrao_dias": (1, 365, "Lead time padrão (dias)"),
}

# Cortes clássicos de Syntetos-Boylan (2005).
ADI_CORTE = 1.32
CV2_CORTE = 0.49
MESES_CLASSIFICACAO = 24
# Com menos meses com saída que isso não dá pra medir variação: esporádico.
MIN_MESES_COM_SAIDA = 3
DIAS_POR_MES = 30.4
# Lead time: amostras dos últimos 3 anos (fornecedor muda); sem nenhuma, todas.
DIAS_JANELA_LEAD_TIME = 3 * 365
# Candidato a item de estoque: comprado pra OS várias vezes no ano e a maior
# parte do consumo veio dessas compras.
CANDIDATO_MIN_OC_PARA_OS = 4
CANDIDATO_MIN_PCT_OS = 50.0

TIPOS_DEMANDA = {
    "regular": "Regular",
    "irregular": "Irregular",
    "intermitente": "Intermitente",
    "esporadico": "Esporádica",
    "sem_giro": "Sem giro",
}


# ---------------------------------------------------------------- parâmetros

def obter_parametros() -> dict[str, Any]:
    linha = EstoquePlanejamentoParametro.query.order_by(EstoquePlanejamentoParametro.id).first()
    if not linha:
        return {**PADROES, "atualizado_por": None, "atualizado_em": None}
    dados = {campo: getattr(linha, campo) for campo in PADROES}
    dados["atualizado_por"] = linha.atualizado_por
    dados["atualizado_em"] = linha.atualizado_em.isoformat() if linha.atualizado_em else None
    return dados


def salvar_parametros(dados: dict[str, Any], usuario: str) -> dict[str, Any]:
    valores = {}
    for campo, (minimo, maximo, rotulo) in _LIMITES.items():
        bruto = (dados or {}).get(campo)
        try:
            valor = float(str(bruto).replace(",", "."))
        except (TypeError, ValueError):
            raise ValueError(f"{rotulo}: informe um número.")
        if not math.isfinite(valor) or valor < minimo or valor > maximo:
            raise ValueError(f"{rotulo}: use um valor entre {minimo:g} e {maximo:g}.")
        valores[campo] = int(round(valor)) if campo == "lead_time_padrao_dias" else valor
    if not (valores["nivel_servico_a_pct"] >= valores["nivel_servico_b_pct"] >= valores["nivel_servico_c_pct"]):
        raise ValueError("O nível de serviço deve ser A ≥ B ≥ C.")

    linha = EstoquePlanejamentoParametro.query.order_by(EstoquePlanejamentoParametro.id).first()
    if not linha:
        linha = EstoquePlanejamentoParametro()
        db.session.add(linha)
    for campo, valor in valores.items():
        setattr(linha, campo, valor)
    linha.atualizado_por = usuario
    linha.atualizado_em = agora_br()
    db.session.commit()
    return obter_parametros()


# ------------------------------------------------------------------- cálculo

def _mes(ano: int, mes: int) -> str:
    return f"{ano:04d}-{mes:02d}"


def _meses_fechados(hoje: date, quantidade: int) -> list[str]:
    """Os N últimos meses completos, do mais antigo pro mais recente. O mês
    corrente fica de fora: parcial, puxaria a média pra baixo."""
    ano, mes = hoje.year, hoje.month
    meses = []
    for _ in range(quantidade):
        mes -= 1
        if mes == 0:
            ano, mes = ano - 1, 12
        meses.append(_mes(ano, mes))
    return list(reversed(meses))


def classificar_abc(valores: dict[str, float]) -> dict[str, str]:
    """Curva ABC pelo valor consumido: A até 80% do acumulado, B até 95%, C o
    resto. Item sem consumo (ou sem custo) é C."""
    total = sum(v for v in valores.values() if v > 0)
    classes = {codigo: "C" for codigo in valores}
    if total <= 0:
        return classes
    acumulado = 0.0
    for codigo, valor in sorted(valores.items(), key=lambda kv: kv[1], reverse=True):
        if valor <= 0:
            break
        # A classe é decidida pelo acumulado ANTES do item: o item que cruza os
        # 80% ainda é A (senão uma base com 1 item dominante não teria A).
        classes[codigo] = "A" if acumulado < 0.80 * total else ("B" if acumulado < 0.95 * total else "C")
        acumulado += valor
    return classes


def _lead_time(amostras: list[dict[str, Any]], hoje: date, padrao_dias: int) -> dict[str, Any]:
    recentes = [
        a for a in amostras
        if a.get("dt_entrada") and (hoje - date.fromisoformat(a["dt_entrada"])).days <= DIAS_JANELA_LEAD_TIME
    ] or list(amostras)
    dias = [max(0, int(a.get("dias") or 0)) for a in recentes]
    if not dias:
        return {"dias": float(padrao_dias), "desvio": padrao_dias * 0.25, "amostras": 0, "fonte": "padrao"}
    media = max(1.0, mean(dias))
    # Com 1 ou 2 entregas o desvio medido não significa nada: assume 25% da média.
    desvio = pstdev(dias) if len(dias) >= 3 else media * 0.25
    return {"dias": media, "desvio": desvio, "amostras": len(dias), "fonte": "historico"}


def _confianca(meses_com_saida_24m: int, amostras_lt: int, tem_custo: bool) -> tuple[str, list[str]]:
    motivos = [f"{meses_com_saida_24m} de {MESES_CLASSIFICACAO} meses com saída"]
    motivos.append(f"{amostras_lt} entrega(s) com OC pra medir o lead time" if amostras_lt else "sem entrega com OC: lead time padrão")
    if not tem_custo:
        motivos.append("sem custo no GRV: lote econômico não calculado")
    if meses_com_saida_24m >= 12 and amostras_lt >= 3:
        nivel = "alta"
    elif meses_com_saida_24m >= 6 and amostras_lt >= 1:
        nivel = "media"
    else:
        nivel = "baixa"
    return nivel, motivos


def calcular_item(
    historico: dict[str, Any],
    *,
    saldo_disponivel: float,
    custo_unitario: float | None,
    classe_abc: str,
    parametros: dict[str, Any],
    hoje: date,
) -> dict[str, Any]:
    serie = {m["mes"]: m for m in historico.get("serie_mensal") or []}
    primeira = historico.get("primeira_movimentacao")
    mes_inicio = primeira[:7] if primeira else "0000-00"

    # Item mais novo que a janela: só conta a partir do primeiro movimento,
    # senão os meses em que ele nem existia viram "meses sem saída".
    meses_12 = [m for m in _meses_fechados(hoje, 12) if m >= mes_inicio]
    meses_24 = [m for m in _meses_fechados(hoje, MESES_CLASSIFICACAO) if m >= mes_inicio]
    valores_12 = [float((serie.get(m) or {}).get("saida") or 0) for m in meses_12]
    valores_24 = [float((serie.get(m) or {}).get("saida") or 0) for m in meses_24]
    consumo_12m = sum(valores_12)
    consumo_os_12m = sum(float((serie.get(m) or {}).get("saida_os") or 0) for m in meses_12)
    consumo_mensal = consumo_12m / len(meses_12) if meses_12 else 0.0
    desvio_mensal = pstdev(valores_12) if len(valores_12) >= 2 else 0.0
    com_saida_24 = [v for v in valores_24 if v > 0]

    adi = cv2 = None
    if consumo_12m <= 0:
        tipo = "sem_giro"
    elif len(com_saida_24) < MIN_MESES_COM_SAIDA:
        tipo = "esporadico"
    else:
        adi = len(valores_24) / len(com_saida_24)
        media_nz = mean(com_saida_24)
        cv2 = (pstdev(com_saida_24) / media_nz) ** 2 if media_nz > 0 else 0.0
        if adi < ADI_CORTE:
            tipo = "regular" if cv2 < CV2_CORTE else "irregular"
        else:
            tipo = "intermitente" if cv2 < CV2_CORTE else "esporadico"

    lt = _lead_time(historico.get("lead_times") or [], hoje, int(parametros["lead_time_padrao_dias"]))
    nivel_servico = float(parametros[f"nivel_servico_{classe_abc.lower()}_pct"])
    z = NormalDist().inv_cdf(nivel_servico / 100.0)
    retiradas = historico.get("retiradas_24m") or {}
    retirada_tipica = retiradas.get("p80") or (max(valores_24) if valores_24 else None)

    saldo = float(saldo_disponivel or 0)
    consumo_diario = consumo_mensal / DIAS_POR_MES
    estoque_seguranca = None
    cobertura_dias = None
    cobre_retirada_pct = None
    if tipo in ("regular", "irregular"):
        lt_meses = lt["dias"] / DIAS_POR_MES
        desvio_lt_meses = lt["desvio"] / DIAS_POR_MES
        estoque_seguranca = z * math.sqrt(lt_meses * desvio_mensal ** 2 + consumo_mensal ** 2 * desvio_lt_meses ** 2)
        estoque_minimo = consumo_mensal * lt_meses + estoque_seguranca
        cobertura_dias = int(math.floor(saldo / consumo_diario)) if consumo_diario > 0 else None
        # Crítico = acaba antes de chegar uma compra feita hoje; monitorar =
        # já passou do ponto de pedido.
        if cobertura_dias is not None and cobertura_dias < lt["dias"]:
            nivel, nivel_label = "critico", "Acaba antes da reposição"
        elif saldo < estoque_minimo:
            nivel, nivel_label = "atencao", "Abaixo do mínimo sugerido"
        else:
            nivel, nivel_label = "normal", "Cobertura adequada"
    elif tipo in ("intermitente", "esporadico"):
        estoque_minimo = float(retirada_tipica or 0)
        if retirada_tipica:
            cobre_retirada_pct = round(saldo / retirada_tipica * 100, 1)
        nivel, nivel_label = "esporadico", "Demanda esporádica"
    else:
        estoque_minimo = 0.0
        consumo_diario = 0.0
        nivel, nivel_label = "sem_consumo", "Sem consumo em 12 meses"

    tem_custo = bool(custo_unitario and custo_unitario > 0)
    lote_economico = None
    if tipo != "sem_giro" and tem_custo and consumo_12m > 0:
        demanda_anual = consumo_mensal * 12
        custo_manter = float(custo_unitario) * float(parametros["taxa_manutencao_pct"]) / 100.0
        lote_economico = math.sqrt(2 * demanda_anual * float(parametros["custo_pedido"]) / custo_manter)
        if tipo in ("intermitente", "esporadico") and retiradas.get("p50"):
            # Comprar menos que uma retirada comum não atende nem o próximo pedido.
            lote_economico = max(lote_economico, float(retiradas["p50"]))

    confianca, motivos = _confianca(len(com_saida_24), lt["amostras"], tem_custo)
    consumo_os_pct = round(consumo_os_12m / consumo_12m * 100, 1) if consumo_12m > 0 else 0.0
    oc_para_os = int(historico.get("oc_para_os_12m") or 0)
    meses_com_saida_total = sum(1 for m in serie.values() if float(m.get("saida") or 0) > 0)

    def r(valor, casas=3):
        return round(valor, casas) if valor is not None else None

    return {
        "tipo_demanda": tipo,
        "tipo_demanda_label": TIPOS_DEMANDA[tipo],
        "adi": r(adi, 2),
        "cv2": r(cv2, 2),
        "classe_abc": classe_abc,
        "nivel_servico_pct": nivel_servico,
        "consumo_12m": r(consumo_12m),
        "consumo_mensal": r(consumo_mensal),
        "consumo_diario": r(consumo_diario, 6),
        "desvio_mensal": r(desvio_mensal),
        "meses_com_saida_24m": len(com_saida_24),
        "meses_analisados_24m": len(valores_24),
        "meses_com_saida_total": meses_com_saida_total,
        "primeira_movimentacao": primeira,
        "lead_time_dias": r(lt["dias"], 1),
        "lead_time_desvio": r(lt["desvio"], 1),
        "lead_time_amostras": lt["amostras"],
        "lead_time_fonte": lt["fonte"],
        "retirada_tipica": r(retirada_tipica),
        "retiradas_24m": int(retiradas.get("quantidade") or 0),
        "cobre_retirada_pct": cobre_retirada_pct,
        "estoque_seguranca": r(estoque_seguranca),
        "estoque_minimo_sugerido": r(estoque_minimo),
        "lote_economico_sugerido": r(lote_economico),
        "custo_unitario": r(custo_unitario, 6) if tem_custo else None,
        "consumo_os_pct_12m": consumo_os_pct,
        "oc_para_os_12m": oc_para_os,
        "oc_estoque_12m": int(historico.get("oc_estoque_12m") or 0),
        "candidato_estoque": oc_para_os >= CANDIDATO_MIN_OC_PARA_OS and consumo_os_pct >= CANDIDATO_MIN_PCT_OS,
        "confianca": confianca,
        "confianca_motivos": motivos,
        "cobertura_dias": cobertura_dias,
        "nivel": nivel,
        "nivel_label": nivel_label,
    }


def calcular_planejamento(
    itens: dict[str, dict[str, Any]],
    historicos: dict[str, dict[str, Any]],
    parametros: dict[str, Any],
    hoje: date | None = None,
) -> dict[str, dict[str, Any]]:
    """`itens`: codigo (sem pontuação) -> {saldo_disponivel, custo_medio} da
    família INTEIRA, não só do que está filtrado na tela - a curva ABC é
    relativa ao conjunto e mudaria a cada busca."""
    hoje = hoje or agora_br().date()
    custos = {}
    valores = {}
    meses_12 = set(_meses_fechados(hoje, 12))
    for codigo, item in itens.items():
        historico = historicos.get(codigo) or {}
        # tproduto.preco_custo vale mesmo com saldo zero; o custo médio do
        # saldo some quando o item zera.
        custo = historico.get("preco_custo") or item.get("custo_medio")
        custos[codigo] = float(custo) if custo else None
        consumo_12m = sum(
            float(m.get("saida") or 0) for m in historico.get("serie_mensal") or [] if m.get("mes") in meses_12
        )
        valores[codigo] = consumo_12m * (custos[codigo] or 0)
    classes = classificar_abc(valores)
    return {
        codigo: calcular_item(
            historicos.get(codigo) or {},
            saldo_disponivel=float(item.get("saldo_disponivel") or 0),
            custo_unitario=custos[codigo],
            classe_abc=classes[codigo],
            parametros=parametros,
            hoje=hoje,
        )
        for codigo, item in itens.items()
    }
