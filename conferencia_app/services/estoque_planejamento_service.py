"""Estoque mínimo e lote econômico sugeridos para a tela de Estoque.

O histórico vem do GRV (bridge /api/erp/estoque/planejamento): consumo mês a
mês desde 2019, tamanho das retiradas, lead time OC -> entrada e custo. Aqui
só entram as contas, pra poderem mudar sem redeploy da VM do bridge. As
contas usam só os últimos 12 meses (MESES_HISTORICO); o histórico inteiro
serve pra saber desde quando o item existe.

Por que não basta "saldo ÷ consumo médio": item que sai uma vez a cada tantos
meses tem média diária que não acontece nunca (o tubo metalon com 1 saída de
25 m em 90 dias virava "1 dia de cobertura"). Então a demanda é classificada
antes (Syntetos-Boylan: intervalo médio entre meses com saída × variação do
tamanho) e cada tipo tem sua conta:

- regular / irregular: ponto de pedido = consumo no lead time + estoque de
  segurança (z do nível de serviço da curva ABC, variação do consumo E do
  prazo do fornecedor);
- intermitente / esporádico: o mínimo é uma retirada típica (percentil 80
  das retiradas de 12 meses) - média não serve pra dimensionar;
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
# Base de cálculo: só os últimos 12 meses fechados. O kardex vem desde 2019,
# mas o histórico antigo não reflete o consumo de hoje (decisão da logística
# em 22/10/2026; antes a classificação usava 24 meses e o lead time 3 anos).
MESES_HISTORICO = 12
# Curva ABC: meses com saída, dentro dos últimos MESES_GIRO_A meses fechados.
MESES_GIRO_A = 6
MESES_GIRO_B = 3
# Com menos meses com saída que isso não dá pra medir variação: esporádico.
MIN_MESES_COM_SAIDA = 3
# Item novo: com menos de 3 meses fechados desde a 1ª movimentação não dá pra
# classificar - sem curva, mínimo, cobertura nem excedente até lá. Sem isso,
# item que entrou no mês virava "parado" com o saldo todo de excedente
# (19-01-00655, decisão da logística em 22/10/2026).
MESES_ITEM_NOVO = 3
DIAS_POR_MES = 30.4
# Lead time: só entregas dos últimos 12 meses; sem nenhuma, vale o padrão.
DIAS_JANELA_LEAD_TIME = 365
# Candidato a item de estoque: comprado pra OS várias vezes no ano e a maior
# parte do consumo veio dessas compras.
CANDIDATO_MIN_OC_PARA_OS = 4
CANDIDATO_MIN_PCT_OS = 50.0
# Slow moving: estoque permitido = 1 mês de consumo médio ou o mínimo
# sugerido, o que for maior (esporádico precisa de ao menos uma retirada
# típica, regular do ponto de pedido). O que passar disso é excedente
# (decisão da logística em 22/10/2026).
MESES_ESTOQUE_PERMITIDO = 1

TIPOS_DEMANDA = {
    "regular": "Regular",
    "irregular": "Irregular",
    "intermitente": "Intermitente",
    "esporadico": "Esporádica",
    "sem_giro": "Sem giro",
    "novo": "Item novo",
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


def classificar_giro(meses_com_saida: int, meses_analisados: int = MESES_GIRO_A) -> str:
    """Curva ABC pelo giro recente: A = saiu em todos os últimos 6 meses
    fechados, B = em pelo menos 3 deles, C = o resto. Item com menos de 6
    meses conta só os meses em que já existia (A = todos, B = metade).

    Decisão da logística em 13/10/2026, olhando o GRV: pelo valor consumido,
    1 saída de uma máquina de R$ 1 milhão era curva A; pelo acumulado de
    retiradas em 24 meses, a revenda (saídas espalhadas em 1.320 itens) tinha
    504 itens A, 384 deles com 3 retiradas ou menos. A só fica quem dá pra
    afirmar que gira: janela curta e saída em todos os meses."""
    if meses_analisados <= 0:
        return "C"
    if meses_com_saida >= meses_analisados:
        return "A"
    return "B" if meses_com_saida / meses_analisados >= MESES_GIRO_B / MESES_GIRO_A else "C"


def _lead_time(amostras: list[dict[str, Any]], hoje: date, padrao_dias: int) -> dict[str, Any]:
    recentes = [
        a for a in amostras
        if a.get("dt_entrada") and (hoje - date.fromisoformat(a["dt_entrada"])).days <= DIAS_JANELA_LEAD_TIME
    ]
    dias = [max(0, int(a.get("dias") or 0)) for a in recentes]
    if not dias:
        return {"dias": float(padrao_dias), "desvio": padrao_dias * 0.25, "amostras": 0, "fonte": "padrao"}
    media = max(1.0, mean(dias))
    # Com 1 ou 2 entregas o desvio medido não significa nada: assume 25% da média.
    desvio = pstdev(dias) if len(dias) >= 3 else media * 0.25
    return {"dias": media, "desvio": desvio, "amostras": len(dias), "fonte": "historico"}


def _confianca(meses_com_saida: int, amostras_lt: int, tem_custo: bool) -> tuple[str, list[str]]:
    motivos = [f"{meses_com_saida} de {MESES_HISTORICO} meses com saída"]
    motivos.append(f"{amostras_lt} entrega(s) com OC pra medir o lead time" if amostras_lt else "sem entrega com OC em 12 meses: lead time padrão")
    if not tem_custo:
        motivos.append("sem custo no GRV: lote econômico não calculado")
    # Mesma proporção de quando a janela era 24 meses (12 e 6): metade dos
    # meses com saída pra "alta", um quarto pra "média".
    if meses_com_saida >= 6 and amostras_lt >= 3:
        nivel = "alta"
    elif meses_com_saida >= 3 and amostras_lt >= 1:
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
    meses_12 = [m for m in _meses_fechados(hoje, MESES_HISTORICO) if m >= mes_inicio]
    valores_12 = [float((serie.get(m) or {}).get("saida") or 0) for m in meses_12]
    consumo_12m = sum(valores_12)
    consumo_os_12m = sum(float((serie.get(m) or {}).get("saida_os") or 0) for m in meses_12)
    consumo_mensal = consumo_12m / len(meses_12) if meses_12 else 0.0
    desvio_mensal = pstdev(valores_12) if len(valores_12) >= 2 else 0.0
    com_saida = [v for v in valores_12 if v > 0]

    adi = cv2 = None
    if consumo_12m <= 0:
        tipo = "sem_giro"
    elif len(com_saida) < MIN_MESES_COM_SAIDA:
        tipo = "esporadico"
    else:
        adi = len(valores_12) / len(com_saida)
        media_nz = mean(com_saida)
        cv2 = (pstdev(com_saida) / media_nz) ** 2 if media_nz > 0 else 0.0
        if adi < ADI_CORTE:
            tipo = "regular" if cv2 < CV2_CORTE else "irregular"
        else:
            tipo = "intermitente" if cv2 < CV2_CORTE else "esporadico"

    lt = _lead_time(historico.get("lead_times") or [], hoje, int(parametros["lead_time_padrao_dias"]))
    nivel_servico = float(parametros[f"nivel_servico_{classe_abc.lower()}_pct"])
    z = NormalDist().inv_cdf(nivel_servico / 100.0)
    # Bridge da VM ainda não atualizada manda as retiradas de 24 meses.
    retiradas = historico.get("retiradas_12m") or historico.get("retiradas_24m") or {}
    retirada_tipica = retiradas.get("p80") or (max(valores_12) if valores_12 else None)

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

    # Sem giro tem consumo e mínimo zero: o saldo inteiro é excedente.
    estoque_permitido = max(consumo_mensal * MESES_ESTOQUE_PERMITIDO, estoque_minimo)
    excedente = max(0.0, saldo - estoque_permitido)

    confianca, motivos = _confianca(len(com_saida), lt["amostras"], tem_custo)
    consumo_os_pct = round(consumo_os_12m / consumo_12m * 100, 1) if consumo_12m > 0 else 0.0
    oc_para_os = int(historico.get("oc_para_os_12m") or 0)

    def r(valor, casas=3):
        return round(valor, casas) if valor is not None else None

    resultado = {
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
        "meses_com_saida_12m": len(com_saida),
        "meses_analisados_12m": len(valores_12),
        "primeira_movimentacao": primeira,
        "lead_time_dias": r(lt["dias"], 1),
        "lead_time_desvio": r(lt["desvio"], 1),
        "lead_time_amostras": lt["amostras"],
        "lead_time_fonte": lt["fonte"],
        "retirada_tipica": r(retirada_tipica),
        "retiradas_12m": int(retiradas.get("quantidade") or 0),
        "cobre_retirada_pct": cobre_retirada_pct,
        "estoque_seguranca": r(estoque_seguranca),
        "estoque_minimo_sugerido": r(estoque_minimo),
        "lote_economico_sugerido": r(lote_economico),
        "estoque_permitido": r(estoque_permitido),
        "excedente": r(excedente),
        "excedente_valor": round(excedente * float(custo_unitario), 2) if tem_custo else None,
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
    if len(meses_12) < MESES_ITEM_NOVO:
        # Consumo dos meses que já tem fica; o que depende de classificar, não.
        resultado.update({
            "tipo_demanda": "novo",
            "tipo_demanda_label": TIPOS_DEMANDA["novo"],
            "adi": None,
            "cv2": None,
            "classe_abc": None,
            "nivel_servico_pct": None,
            "retirada_tipica": None,
            "cobre_retirada_pct": None,
            "estoque_seguranca": None,
            "estoque_minimo_sugerido": None,
            "lote_economico_sugerido": None,
            "estoque_permitido": None,
            "excedente": 0.0,
            "excedente_valor": None,
            "candidato_estoque": False,
            "confianca": "baixa",
            "confianca_motivos": [f"menos de {MESES_ITEM_NOVO} meses de histórico"],
            "cobertura_dias": None,
            "nivel": "novo",
            "nivel_label": "Item novo",
        })
    return resultado


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
    classes = {}
    meses_giro = _meses_fechados(hoje, MESES_GIRO_A)
    for codigo, item in itens.items():
        historico = historicos.get(codigo) or {}
        # tproduto.preco_custo vale mesmo com saldo zero; o custo médio do
        # saldo some quando o item zera.
        custo = historico.get("preco_custo") or item.get("custo_medio")
        custos[codigo] = float(custo) if custo else None
        inicio = str(historico.get("primeira_movimentacao") or "")[:7]
        existia = {m for m in meses_giro if m >= inicio}
        classes[codigo] = classificar_giro(sum(
            1 for m in historico.get("serie_mensal") or [] if m.get("mes") in existia and float(m.get("saida") or 0) > 0
        ), len(existia))
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


# ------------------------------------------------------------- fornecimentos

# Preço envelhece rápido: a chapa A36 5/8" foi de R$ 5,30 a 6,60/kg entre
# 12/2025 e 07/2026, e o R$ 5,50 de 06/2025 de outro fornecedor ganhava o
# "menor preço" de todo mundo. Só compara preço de compra dos últimos 6 meses.
MESES_PRECO_COMPARAVEL = 6
# 1 entrega não mede prazo de ninguém (1 dia numa entrega de 2025 levava o
# "mais rápido"): exige pelo menos 2.
MIN_ENTREGAS_MAIS_RAPIDO = 2


def _data(valor: Any) -> date | None:
    if not valor:
        return None
    try:
        return date.fromisoformat(str(valor)[:10])
    except ValueError:
        return None


def _meses_antes(hoje: date, meses: int) -> date:
    ano, mes = divmod(hoje.year * 12 + hoje.month - 1 - meses, 12)
    return date(ano, mes + 1, min(hoje.day, 28))


def _chave_fornecedor(linha: dict[str, Any]) -> str:
    """Mesma empresa com duas filiais vira um fornecedor só (a CECOFER tem os
    cadastros 577 e 843, CNPJ 33.699.114/0001 e /0002). Sem CNPJ, o código."""
    cnpj = str(linha.get("cnpj") or "")
    if len(cnpj) == 14:
        return f"cnpj:{cnpj[:8]}"
    return f"cod:{linha.get('cod_fornecedor') or linha.get('fornecedor')}"


def _preco_unidade_estoque(linha: dict[str, Any]) -> float | None:
    """preco_unitario vem na unidade de COMPRA (PÇ de 6 m, CX com 12 und):
    total da linha ÷ quantidade na unidade de estoque."""
    preco = float(linha.get("preco_unitario") or 0)
    if preco <= 0:
        return None
    qtde = float(linha.get("qtde") or 0)
    qtde_compra = float(linha.get("qtde_compra") or 0)
    if qtde > 0 and qtde_compra > 0:
        return preco * qtde_compra / qtde
    return preco


def resumir_fornecimentos(linhas: list[dict[str, Any]], hoje: date | None = None) -> dict[str, Any]:
    hoje = hoje or agora_br().date()
    fornecimentos = []
    # Lista e contas (prazo, preço, selos) só com OCs dos últimos 12 meses.
    limite_historico = _meses_antes(hoje, MESES_HISTORICO)
    for linha in linhas or []:
        dt_oc = _data(linha.get("dt_oc"))
        if dt_oc and dt_oc < limite_historico:
            continue
        dt_entrada = _data(linha.get("dt_entrada"))
        qtde = float(linha.get("qtde") or 0)
        entregue = float(linha.get("qtde_entregue") or 0)
        if dt_entrada and entregue >= qtde - 1e-6:
            situacao = "entregue"
        elif dt_entrada or entregue > 0:
            situacao = "parcial"
        else:
            situacao = "aberta"
        fornecimentos.append({
            "ordem_compra": str(linha.get("ordem_compra") or ""),
            "dt_oc": dt_oc.isoformat() if dt_oc else None,
            "dt_entrada": dt_entrada.isoformat() if dt_entrada else None,
            "lead_time_dias": (dt_entrada - dt_oc).days if dt_oc and dt_entrada else None,
            "fornecedor": str(linha.get("fornecedor") or ""),
            "chave_fornecedor": _chave_fornecedor(linha),
            "quantidade": qtde,
            "quantidade_compra": float(linha.get("qtde_compra") or 0) or None,
            "unidade_compra": str(linha.get("unidade_compra") or ""),
            "preco_compra": float(linha.get("preco_unitario") or 0) or None,
            "preco_unidade_estoque": _preco_unidade_estoque(linha),
            "situacao": situacao,
            "para_os": bool(linha.get("para_os")),
        })
    fornecimentos.sort(key=lambda f: (f["dt_oc"] or "", f["ordem_compra"]), reverse=True)

    grupos: dict[str, list[dict[str, Any]]] = {}
    for f in fornecimentos:
        grupos.setdefault(f["chave_fornecedor"], []).append(f)
    limite_preco = _meses_antes(hoje, MESES_PRECO_COMPARAVEL)
    fornecedores = []
    for chave, itens in grupos.items():
        # itens já vem do mais recente pro mais antigo
        ultima = itens[0]
        com_preco = next((f for f in itens if f["preco_unidade_estoque"]), None)
        prazos = [f["lead_time_dias"] for f in itens if f["lead_time_dias"] is not None]
        fornecedores.append({
            "chave": chave,
            "fornecedor": ultima["fornecedor"],
            "fornecimentos": len(itens),
            "entregas_com_prazo": len(prazos),
            "lead_time_medio": round(mean(prazos), 1) if prazos else None,
            "ultimo_preco": round(com_preco["preco_unidade_estoque"], 6) if com_preco else None,
            "ultimo_preco_data": com_preco["dt_oc"] if com_preco else None,
            "ultima_compra": ultima["dt_oc"],
            "mais_rapido": False,
            "menor_preco": False,
        })

    com_prazo = [f for f in fornecedores if f["lead_time_medio"] is not None and f["entregas_com_prazo"] >= MIN_ENTREGAS_MAIS_RAPIDO]
    # Com 1 fornecedor só não há o que comparar: nada de selo.
    if len(com_prazo) >= 2:
        min(com_prazo, key=lambda f: (f["lead_time_medio"], -f["entregas_com_prazo"]))["mais_rapido"] = True
    com_preco = [f for f in fornecedores if f["ultimo_preco"] and _data(f["ultimo_preco_data"]) >= limite_preco]
    if len(com_preco) >= 2:
        min(com_preco, key=lambda f: f["ultimo_preco"])["menor_preco"] = True
    for f in fornecedores:
        f["recomendado"] = f["mais_rapido"] and f["menor_preco"]
    fornecedores.sort(key=lambda f: (
        not f["recomendado"], not (f["mais_rapido"] or f["menor_preco"]),
        f["lead_time_medio"] if f["lead_time_medio"] is not None else 9999,
        f["ultimo_preco"] or float("inf"),
    ))
    return {
        "fornecedores": fornecedores,
        "fornecimentos": fornecimentos,
        "meses_historico": MESES_HISTORICO,
        "meses_preco_comparavel": MESES_PRECO_COMPARAVEL,
        "min_entregas_mais_rapido": MIN_ENTREGAS_MAIS_RAPIDO,
    }
