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

- todo item com consumo tem estoque de segurança (z do nível de serviço da
  curva ABC, variação do consumo E do prazo do fornecedor) e ponto de
  pedido = consumo no lead time + segurança;
- regular / irregular: o mínimo é o ponto de pedido;
- intermitente / esporádico: o mínimo é o maior entre o ponto de pedido e
  uma retirada típica (percentil 80 das retiradas da janela);
- sem giro (nada na janela): não sugere estocar.

Política mínimo/máximo (decisão da logística em 17/11/2026): compra o lote
quando chega no mínimo, então o estoque não passa de mínimo + lote. O lote é
o econômico, limitado a N meses de consumo (cobertura do lote, geral ou por
item). Antes o teto era 1 mês de consumo e quem comprava o lote sugerido
virava "excedente"; a planilha de outubro/2026 usava o lote comprado, que
validava qualquer compra exagerada. Acima do máximo é excedente.

Slow mover é outra pergunta (o item gira devagar?): saldo para mais de N
meses de consumo, ou parado (sem saída na janela).

Todo o consumo entra na conta, inclusive o de material comprado para uma OS
específica; a tela só mostra quanto dele foi (decisão da logística em
28/09/2026).
"""
from __future__ import annotations

import math
import re
from datetime import date
from statistics import NormalDist, mean
from typing import Any

from ..extensions import db
from ..models import EstoqueParametroItem, EstoquePlanejamentoParametro
from ..tempo import agora_br


PADROES = {
    # Provisório até a logística levantar o custo real de emitir uma OC.
    "custo_pedido": 150.0,
    "taxa_manutencao_pct": 25.0,
    "nivel_servico_a_pct": 98.0,
    "nivel_servico_b_pct": 95.0,
    "nivel_servico_c_pct": 90.0,
    "lead_time_padrao_dias": 15,
    "janela_meses": 12,
    "cobertura_lote_meses": 3.0,
    "slow_mover_meses": 6.0,
    "crescimento_vendas_pct": 0.0,
}
_LIMITES = {
    "custo_pedido": (0.01, 100000.0, "Custo de emitir um pedido"),
    "taxa_manutencao_pct": (1.0, 100.0, "Custo de manter estoque (% ao ano)"),
    "nivel_servico_a_pct": (50.0, 99.9, "Nível de serviço da curva A"),
    "nivel_servico_b_pct": (50.0, 99.9, "Nível de serviço da curva B"),
    "nivel_servico_c_pct": (50.0, 99.9, "Nível de serviço da curva C"),
    "lead_time_padrao_dias": (1, 365, "Lead time padrão (dias)"),
    # A foto da tela guarda 37 meses de série: janela maior não teria dado.
    "janela_meses": (3, 36, "Janela de análise (meses)"),
    "cobertura_lote_meses": (0.5, 24.0, "Cobertura máxima do lote (meses)"),
    "slow_mover_meses": (1.0, 60.0, "Slow mover acima de (meses)"),
    "crescimento_vendas_pct": (-90.0, 300.0, "Crescimento de vendas previsto (%)"),
}
_CAMPOS_INTEIROS = {"lead_time_padrao_dias", "janela_meses"}

# Cortes clássicos de Syntetos-Boylan (2005).
ADI_CORTE = 1.32
CV2_CORTE = 0.49
# Base de cálculo padrão: os últimos 12 meses fechados. O kardex vem desde
# 2019, mas o histórico antigo não reflete o consumo de hoje (decisão da
# logística em 22/10/2026). Desde 17/11/2026 a janela é parâmetro
# (janela_meses); esta constante é só o padrão.
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
# Candidato a item de estoque: comprado pra OS várias vezes no ano e a maior
# parte do consumo veio dessas compras.
CANDIDATO_MIN_OC_PARA_OS = 4
CANDIDATO_MIN_PCT_OS = 50.0
# Unidade contada em inteiro: segurança, mínimo, lote e máximo arredondam
# pra cima (não existe 2,3 peças). Lista da planilha de outubro/2026.
UNIDADES_INTEIRAS = {"UN", "UND", "UNID", "PÇ", "PÇS", "PC", "PCS", "P", "CJ", "PAR", "JG", "CX", "CT"}

# Faixas de quanto tempo o saldo dura no consumo médio (as da coluna
# "Motivo" da planilha de outubro/2026).
FAIXAS_COBERTURA = {
    "parado": "Parado",
    "mais_12": "Mais de 12 meses",
    "6_12": "6 a 12 meses",
    "3_6": "3 a 6 meses",
    "1_3": "1 a 3 meses",
    "ate_1": "Até 1 mês",
}

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
    dados = {campo: getattr(linha, campo, None) for campo in PADROES}
    dados = {campo: PADROES[campo] if valor is None else valor for campo, valor in dados.items()}
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
        valores[campo] = int(round(valor)) if campo in _CAMPOS_INTEIROS else valor
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


# ---------------------------------------------------------- ajuste por item

def chave_codigo(codigo: Any) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(codigo or "").strip().upper())


def obter_ajustes() -> dict[str, dict[str, Any]]:
    """codigo (sem pontuação) -> ajuste do item."""
    return {
        linha.codigo: {
            "codigo": linha.codigo_exibicao or linha.codigo,
            "cobertura_lote_meses": linha.cobertura_lote_meses,
            "fora_analise": bool(linha.fora_analise),
            "nao_comprar": bool(linha.nao_comprar),
            "observacao": linha.observacao or "",
            "atualizado_por": linha.atualizado_por,
            "atualizado_em": linha.atualizado_em.isoformat() if linha.atualizado_em else None,
        }
        for linha in EstoqueParametroItem.query.all()
    }


def salvar_ajuste(codigo: str, dados: dict[str, Any], usuario: str) -> dict[str, Any] | None:
    """Grava o ajuste do item. Tudo vazio apaga: o item volta ao geral."""
    chave = chave_codigo(codigo)
    if not chave:
        raise ValueError("Informe o código do item.")
    dados = dados or {}
    bruto = str(dados.get("cobertura_lote_meses") if dados.get("cobertura_lote_meses") is not None else "").strip()
    cobertura = None
    if bruto:
        minimo, maximo, rotulo = _LIMITES["cobertura_lote_meses"]
        try:
            cobertura = float(bruto.replace(",", "."))
        except ValueError:
            raise ValueError(f"{rotulo}: informe um número.")
        if not math.isfinite(cobertura) or cobertura < minimo or cobertura > maximo:
            raise ValueError(f"{rotulo}: use um valor entre {minimo:g} e {maximo:g}.")
    fora = bool(dados.get("fora_analise"))
    nao_comprar = bool(dados.get("nao_comprar"))
    observacao = str(dados.get("observacao") or "").strip()[:300]

    linha = EstoqueParametroItem.query.filter_by(codigo=chave).first()
    if cobertura is None and not fora and not nao_comprar and not observacao:
        if linha:
            db.session.delete(linha)
            db.session.commit()
        return None
    if not linha:
        linha = EstoqueParametroItem(codigo=chave)
        db.session.add(linha)
    linha.codigo_exibicao = str(codigo).strip()[:60]
    linha.cobertura_lote_meses = cobertura
    linha.fora_analise = fora
    linha.nao_comprar = nao_comprar
    linha.observacao = observacao or None
    linha.atualizado_por = usuario
    linha.atualizado_em = agora_br()
    db.session.commit()
    return obter_ajustes().get(chave)


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


def _media(valores: list[float]) -> float:
    return math.fsum(valores) / len(valores) if valores else 0.0


def _desvio(valores: list[float]) -> float:
    """Desvio padrão populacional. O pstdev do statistics é exato (frações) e
    pesa quando roda para a família inteira a cada abertura da tela."""
    if not valores:
        return 0.0
    m = _media(valores)
    return math.sqrt(math.fsum((v - m) ** 2 for v in valores) / len(valores))


def _estatistica_lead_time(dias: list[int], padrao_dias: int, fonte: str) -> dict[str, Any]:
    if not dias:
        return {"dias": float(padrao_dias), "desvio": padrao_dias * 0.25, "amostras": 0, "fonte": "padrao"}
    media = max(1.0, _media(dias))
    # Com 1 ou 2 entregas o desvio medido não significa nada: assume 25% da média.
    desvio = _desvio(dias) if len(dias) >= 3 else media * 0.25
    return {"dias": media, "desvio": desvio, "amostras": len(dias), "fonte": fonte}


def _nome_fornecedor(valor: Any) -> str:
    return re.sub(r"\s+", " ", str(valor or "").strip().upper())


def _lead_times(
    historicos: dict[str, dict[str, Any]], codigos: list[str], hoje: date, janela: int, padrao_dias: int,
) -> dict[str, dict[str, Any]]:
    """Lead time em três níveis (como na planilha de outubro/2026): entregas
    do próprio item na janela; sem nenhuma, as do último fornecedor dele (em
    qualquer item da família); sem isso, o padrão. Antes item sem compra na
    janela caía direto no padrão."""
    limite = _meses_antes(hoje, janela)
    por_fornecedor: dict[str, list[int]] = {}
    recentes: dict[str, list[int]] = {}
    ultimo_fornecedor: dict[str, str] = {}
    for codigo in codigos:
        amostras = sorted(
            (historicos.get(codigo) or {}).get("lead_times") or [], key=lambda a: str(a.get("dt_entrada") or ""),
        )
        for amostra in amostras:
            fornecedor = _nome_fornecedor(amostra.get("fornecedor"))
            if fornecedor:
                ultimo_fornecedor[codigo] = fornecedor
            entrada = _data(amostra.get("dt_entrada"))
            if not entrada or entrada < limite:
                continue
            dias = max(0, int(amostra.get("dias") or 0))
            recentes.setdefault(codigo, []).append(dias)
            if fornecedor:
                por_fornecedor.setdefault(fornecedor, []).append(dias)
    resultado = {}
    for codigo in codigos:
        if recentes.get(codigo):
            resultado[codigo] = _estatistica_lead_time(recentes[codigo], padrao_dias, "historico")
            continue
        fornecedor = ultimo_fornecedor.get(codigo)
        lt = _estatistica_lead_time(por_fornecedor.get(fornecedor) or [], padrao_dias, "fornecedor")
        if lt["fonte"] == "fornecedor":
            lt["fornecedor"] = fornecedor
        resultado[codigo] = lt
    return resultado


def _confianca(meses_com_saida: int, janela: int, lead_time: dict[str, Any], tem_custo: bool) -> tuple[str, list[str]]:
    motivos = [f"{meses_com_saida} de {janela} meses com saída"]
    amostras_lt = lead_time["amostras"] if lead_time["fonte"] == "historico" else 0
    if lead_time["fonte"] == "historico":
        motivos.append(f"{amostras_lt} entrega(s) com OC pra medir o lead time")
    elif lead_time["fonte"] == "fornecedor":
        motivos.append(f"sem entrega deste item na janela: prazo do último fornecedor ({lead_time['amostras']} entrega(s))")
    else:
        motivos.append("sem entrega com OC na janela: lead time padrão")
    if not tem_custo:
        motivos.append("sem custo no GRV: lote pela cobertura, sem lote econômico")
    # Metade dos meses com saída pra "alta", um quarto pra "média" (com 12
    # meses: 6 e 3, como sempre foi).
    if meses_com_saida >= janela / 2 and amostras_lt >= 3:
        nivel = "alta"
    elif meses_com_saida >= janela / 4 and amostras_lt >= 1:
        nivel = "media"
    else:
        nivel = "baixa"
    return nivel, motivos


def _faixa_cobertura(saldo: float, tipo: str, cobertura_meses: float | None) -> str | None:
    if saldo <= 0:
        return None
    if tipo == "sem_giro":
        return "parado"
    if cobertura_meses is None:
        return None
    if cobertura_meses > 12:
        return "mais_12"
    if cobertura_meses > 6:
        return "6_12"
    if cobertura_meses > 3:
        return "3_6"
    return "1_3" if cobertura_meses >= 1 else "ate_1"


def calcular_item(
    historico: dict[str, Any],
    *,
    saldo_disponivel: float,
    custo_unitario: float | None,
    classe_abc: str,
    parametros: dict[str, Any],
    hoje: date,
    lead_time: dict[str, Any] | None = None,
    unidade: str = "",
    ajuste: dict[str, Any] | None = None,
) -> dict[str, Any]:
    janela = int(parametros.get("janela_meses") or MESES_HISTORICO)
    crescimento_pct = float(parametros.get("crescimento_vendas_pct") or 0)
    # Vender X% a mais com o mesmo mix = consumir X% a mais de material.
    fator = 1 + crescimento_pct / 100.0
    ajuste = ajuste or {}
    serie = {m["mes"]: m for m in historico.get("serie_mensal") or []}
    primeira = historico.get("primeira_movimentacao")
    mes_inicio = primeira[:7] if primeira else "0000-00"

    # Item mais novo que a janela: só conta a partir do primeiro movimento,
    # senão os meses em que ele nem existia viram "meses sem saída".
    meses = [m for m in _meses_fechados(hoje, janela) if m >= mes_inicio]
    valores = [float((serie.get(m) or {}).get("saida") or 0) for m in meses]
    consumo_janela = math.fsum(valores)
    consumo_os = math.fsum(float((serie.get(m) or {}).get("saida_os") or 0) for m in meses)
    consumo_mensal = (consumo_janela / len(meses) if meses else 0.0) * fator
    desvio_mensal = (_desvio(valores) if len(valores) >= 2 else 0.0) * fator
    com_saida = [v for v in valores if v > 0]

    adi = cv2 = None
    if consumo_janela <= 0:
        tipo = "sem_giro"
    elif len(com_saida) < MIN_MESES_COM_SAIDA:
        tipo = "esporadico"
    else:
        adi = len(valores) / len(com_saida)
        media_nz = _media(com_saida)
        cv2 = (_desvio(com_saida) / media_nz) ** 2 if media_nz > 0 else 0.0
        if adi < ADI_CORTE:
            tipo = "regular" if cv2 < CV2_CORTE else "irregular"
        else:
            tipo = "intermitente" if cv2 < CV2_CORTE else "esporadico"

    lt = lead_time or _estatistica_lead_time([], int(parametros["lead_time_padrao_dias"]), "padrao")
    nivel_servico = float(parametros[f"nivel_servico_{classe_abc.lower()}_pct"])
    z = NormalDist().inv_cdf(nivel_servico / 100.0)
    # Bridge da VM ainda não atualizada manda as retiradas de 24 meses.
    retiradas = historico.get("retiradas_12m") or historico.get("retiradas_24m") or {}
    retirada_tipica = retiradas.get("p80") or (max(valores) if valores else None)

    inteiro = str(unidade or historico.get("unidade") or "").strip().upper() in UNIDADES_INTEIRAS

    def arred(valor: float) -> float:
        return float(math.ceil(valor - 1e-9)) if inteiro else valor

    saldo = float(saldo_disponivel or 0)
    tem_custo = bool(custo_unitario and custo_unitario > 0)
    consumo_diario = consumo_mensal / DIAS_POR_MES
    lt_meses = lt["dias"] / DIAS_POR_MES
    desvio_lt_meses = lt["desvio"] / DIAS_POR_MES
    estoque_seguranca = None
    ponto_pedido = None
    cobertura_dias = None
    cobre_retirada_pct = None
    lote_economico = None
    lote = None
    lote_origem = None
    cobertura_lote = float(
        ajuste.get("cobertura_lote_meses") or parametros.get("cobertura_lote_meses") or PADROES["cobertura_lote_meses"]
    )

    if tipo == "sem_giro":
        estoque_minimo = 0.0
        consumo_diario = 0.0
        nivel, nivel_label = "sem_consumo", f"Sem consumo em {janela} meses"
    else:
        estoque_seguranca = arred(z * math.sqrt(lt_meses * desvio_mensal ** 2 + consumo_mensal ** 2 * desvio_lt_meses ** 2))
        ponto_pedido = consumo_mensal * lt_meses + estoque_seguranca
        if tipo in ("regular", "irregular"):
            estoque_minimo = arred(ponto_pedido)
            cobertura_dias = int(math.floor(saldo / consumo_diario)) if consumo_diario > 0 else None
            # Crítico = acaba antes de chegar uma compra feita hoje; abaixo do
            # mínimo = já passou do ponto de pedido.
            if cobertura_dias is not None and cobertura_dias < lt["dias"]:
                nivel, nivel_label = "critico", "Acaba antes da reposição"
            elif saldo < estoque_minimo:
                nivel, nivel_label = "atencao", "Abaixo do mínimo sugerido"
            else:
                nivel, nivel_label = "normal", "Cobertura adequada"
        else:
            # Só a retirada típica não considerava o prazo de entrega.
            estoque_minimo = arred(max(float(retirada_tipica or 0), ponto_pedido))
            if retirada_tipica:
                cobre_retirada_pct = round(saldo / retirada_tipica * 100, 1)
            nivel, nivel_label = "esporadico", "Demanda esporádica"

        teto_lote = consumo_mensal * cobertura_lote
        if tem_custo:
            custo_manter = float(custo_unitario) * float(parametros["taxa_manutencao_pct"]) / 100.0
            lote_economico = math.sqrt(2 * consumo_mensal * 12 * float(parametros["custo_pedido"]) / custo_manter)
            if tipo in ("intermitente", "esporadico") and retiradas.get("p50"):
                # Comprar menos que uma retirada comum não atende nem o próximo pedido.
                lote_economico = max(lote_economico, float(retiradas["p50"]))
            lote = min(lote_economico, teto_lote)
            lote_origem = "limitado" if lote_economico > teto_lote else "economico"
        else:
            lote = teto_lote
            lote_origem = "cobertura"
        lote = max(arred(lote), 1.0) if inteiro else lote

    # Sem giro tem mínimo e lote zero: o saldo inteiro é excedente.
    estoque_maximo = estoque_minimo + (lote or 0.0)
    excedente = max(0.0, saldo - estoque_maximo)
    cobertura_meses = saldo / consumo_mensal if consumo_mensal > 0 else None
    faixa = _faixa_cobertura(saldo, tipo, cobertura_meses)
    limite_slow = float(parametros.get("slow_mover_meses") or PADROES["slow_mover_meses"])
    slow_mover = saldo > 0 and (tipo == "sem_giro" or (cobertura_meses is not None and cobertura_meses > limite_slow))
    # Estoque médio de quem segue a política: a segurança efetiva (o mínimo
    # menos o consumo no prazo) + meio lote.
    estoque_alvo_medio = 0.0 if tipo == "sem_giro" else max(0.0, estoque_minimo - consumo_mensal * lt_meses) + (lote or 0.0) / 2

    confianca, motivos = _confianca(len(com_saida), janela, lt, tem_custo)
    consumo_os_pct = round(consumo_os / consumo_janela * 100, 1) if consumo_janela > 0 else 0.0
    oc_para_os = int(historico.get("oc_para_os_12m") or 0)

    def r(valor, casas=3):
        return round(valor, casas) if valor is not None else None

    def rs(qtde):
        return round(qtde * float(custo_unitario), 2) if tem_custo and qtde is not None else None

    resultado = {
        "janela_meses": janela,
        "crescimento_vendas_pct": crescimento_pct,
        "tipo_demanda": tipo,
        "tipo_demanda_label": TIPOS_DEMANDA[tipo],
        "adi": r(adi, 2),
        "cv2": r(cv2, 2),
        "classe_abc": classe_abc,
        "nivel_servico_pct": nivel_servico,
        "consumo_janela": r(consumo_janela),
        "consumo_mensal": r(consumo_mensal),
        "consumo_diario": r(consumo_diario, 6),
        "desvio_mensal": r(desvio_mensal),
        "meses_com_saida": len(com_saida),
        "meses_analisados": len(valores),
        "primeira_movimentacao": primeira,
        "lead_time_dias": r(lt["dias"], 1),
        "lead_time_desvio": r(lt["desvio"], 1),
        "lead_time_amostras": lt["amostras"],
        "lead_time_fonte": lt["fonte"],
        "lead_time_fornecedor": lt.get("fornecedor"),
        "retirada_tipica": r(retirada_tipica),
        "retiradas_janela": int(retiradas.get("quantidade") or 0),
        "cobre_retirada_pct": cobre_retirada_pct,
        "estoque_seguranca": r(estoque_seguranca),
        "ponto_pedido": r(ponto_pedido),
        "estoque_minimo_sugerido": r(estoque_minimo),
        "lote_economico": r(lote_economico),
        "lote_economico_sugerido": r(lote),
        "lote_origem": lote_origem,
        "cobertura_lote_meses": cobertura_lote,
        "cobertura_lote_ajustada": ajuste.get("cobertura_lote_meses") is not None,
        "estoque_maximo": r(estoque_maximo),
        "excedente": r(excedente),
        "excedente_valor": rs(excedente),
        "custo_unitario": r(custo_unitario, 6) if tem_custo else None,
        "valor_saldo": rs(saldo),
        "cobertura_meses": r(cobertura_meses, 2),
        "faixa_cobertura": faixa,
        "faixa_cobertura_label": FAIXAS_COBERTURA.get(faixa) if faixa else None,
        "slow_mover": slow_mover,
        "parado": faixa == "parado",
        "estoque_alvo_medio": r(estoque_alvo_medio),
        "estoque_alvo_medio_valor": rs(estoque_alvo_medio),
        "estoque_maximo_valor": rs(estoque_maximo),
        "fora_analise": bool(ajuste.get("fora_analise")),
        "nao_comprar": bool(ajuste.get("nao_comprar")),
        "consumo_os_pct": consumo_os_pct,
        "oc_para_os": oc_para_os,
        "oc_estoque": int(historico.get("oc_estoque_12m") or 0),
        "candidato_estoque": oc_para_os >= CANDIDATO_MIN_OC_PARA_OS and consumo_os_pct >= CANDIDATO_MIN_PCT_OS,
        "confianca": confianca,
        "confianca_motivos": motivos,
        "cobertura_dias": cobertura_dias,
        "nivel": nivel,
        "nivel_label": nivel_label,
    }
    if len(meses) < MESES_ITEM_NOVO:
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
            "ponto_pedido": None,
            "estoque_minimo_sugerido": None,
            "lote_economico": None,
            "lote_economico_sugerido": None,
            "lote_origem": None,
            "estoque_maximo": None,
            "excedente": 0.0,
            "excedente_valor": None,
            "faixa_cobertura": None,
            "faixa_cobertura_label": None,
            "slow_mover": False,
            "parado": False,
            "estoque_alvo_medio": None,
            "estoque_alvo_medio_valor": None,
            "estoque_maximo_valor": None,
            "candidato_estoque": False,
            "confianca": "baixa",
            "confianca_motivos": [f"menos de {MESES_ITEM_NOVO} meses de histórico"],
            "cobertura_dias": None,
            "nivel": "novo",
            "nivel_label": "Item novo",
        })
    if resultado["nao_comprar"]:
        # Descontinuado: nada a repor e o saldo que restar sobra inteiro -
        # é o dinheiro parado que falta consumir ou vender.
        resultado.update({
            "estoque_seguranca": None, "ponto_pedido": None,
            "estoque_minimo_sugerido": 0.0, "lote_economico_sugerido": 0.0, "lote_origem": None,
            "estoque_maximo": 0.0, "estoque_maximo_valor": 0.0 if tem_custo else None,
            "excedente": r(saldo), "excedente_valor": rs(saldo),
            "estoque_alvo_medio": 0.0, "estoque_alvo_medio_valor": 0.0 if tem_custo else None,
            "slow_mover": saldo > 0, "candidato_estoque": False,
            "nivel": "descontinuado", "nivel_label": "Não comprar mais",
        })
    if resultado["fora_analise"]:
        # Ativo da empresa cadastrado como produto: não é estoque a girar.
        resultado.update({"excedente": 0.0, "excedente_valor": None, "slow_mover": False, "parado": False})
    return resultado


def calcular_planejamento(
    itens: dict[str, dict[str, Any]],
    historicos: dict[str, dict[str, Any]],
    parametros: dict[str, Any],
    hoje: date | None = None,
    ajustes: dict[str, dict[str, Any]] | None = None,
) -> dict[str, dict[str, Any]]:
    """`itens`: codigo (sem pontuação) -> {saldo_disponivel, custo_medio,
    unidade} da família INTEIRA, não só do que está filtrado na tela - o
    prazo do último fornecedor sai das entregas da família toda.
    `ajustes`: obter_ajustes() (None = nenhum)."""
    hoje = hoje or agora_br().date()
    ajustes = ajustes or {}
    janela = int(parametros.get("janela_meses") or MESES_HISTORICO)
    lead_times = _lead_times(historicos, list(itens), hoje, janela, int(parametros["lead_time_padrao_dias"]))
    meses_giro = _meses_fechados(hoje, MESES_GIRO_A)
    # Com crescimento previsto, o "estoque necessário" sem crescimento sai
    # junto pra comparar (quanto a meta de vendas pede a mais).
    crescimento = float(parametros.get("crescimento_vendas_pct") or 0)
    parametros_base = {**parametros, "crescimento_vendas_pct": 0.0} if crescimento else None
    resultado = {}
    for codigo, item in itens.items():
        historico = historicos.get(codigo) or {}
        # tproduto.preco_custo vale mesmo com saldo zero; o custo médio do
        # saldo some quando o item zera.
        custo = historico.get("preco_custo") or item.get("custo_medio")
        inicio = str(historico.get("primeira_movimentacao") or "")[:7]
        existia = {m for m in meses_giro if m >= inicio}
        classe = classificar_giro(sum(
            1 for m in historico.get("serie_mensal") or [] if m.get("mes") in existia and float(m.get("saida") or 0) > 0
        ), len(existia))
        argumentos = {
            "saldo_disponivel": float(item.get("saldo_disponivel") or 0),
            "custo_unitario": float(custo) if custo else None,
            "classe_abc": classe,
            "hoje": hoje,
            "lead_time": lead_times[codigo],
            "unidade": item.get("unidade") or "",
            "ajuste": ajustes.get(codigo),
        }
        plano = calcular_item(historico, parametros=parametros, **argumentos)
        plano["estoque_alvo_medio_valor_base"] = (
            calcular_item(historico, parametros=parametros_base, **argumentos)["estoque_alvo_medio_valor"]
            if parametros_base else plano["estoque_alvo_medio_valor"]
        )
        resultado[codigo] = plano
    return resultado


def resumir_slow_mover(planos: list[dict[str, Any]]) -> dict[str, Any]:
    """Totais em R$ dos itens analisados (os fora da análise ficam de fora).
    Item sem custo no GRV entra nas contagens, não nos valores."""
    analisados = [p for p in planos if p and not p.get("fora_analise")]

    def soma(campo, filtro=lambda p: True):
        return round(math.fsum(float(p.get(campo) or 0) for p in analisados if filtro(p)), 2)

    faixas = {
        chave: {
            "label": rotulo,
            "itens": sum(1 for p in analisados if p.get("faixa_cobertura") == chave),
            "valor": soma("valor_saldo", lambda p, c=chave: p.get("faixa_cobertura") == c),
        }
        for chave, rotulo in FAIXAS_COBERTURA.items()
    }
    return {
        "valor_estoque": soma("valor_saldo"),
        "slow_mover_itens": sum(1 for p in analisados if p.get("slow_mover")),
        "slow_mover_valor": soma("valor_saldo", lambda p: p.get("slow_mover")),
        "parado_itens": sum(1 for p in analisados if p.get("parado")),
        "parado_valor": soma("valor_saldo", lambda p: p.get("parado")),
        "excedente_itens": sum(1 for p in analisados if float(p.get("excedente") or 0) > 0),
        "excedente_valor": soma("excedente_valor"),
        "necessario_medio_valor": soma("estoque_alvo_medio_valor"),
        "necessario_medio_valor_base": soma("estoque_alvo_medio_valor_base"),
        "necessario_maximo_valor": soma("estoque_maximo_valor"),
        "sem_custo_itens": sum(1 for p in analisados if p.get("custo_unitario") is None),
        "fora_analise_itens": sum(1 for p in planos if p and p.get("fora_analise")),
        "faixas": faixas,
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


def resumir_fornecimentos(linhas: list[dict[str, Any]], hoje: date | None = None, meses: int = MESES_HISTORICO) -> dict[str, Any]:
    hoje = hoje or agora_br().date()
    fornecimentos = []
    # Lista e contas (prazo, preço, selos) só com OCs da janela.
    limite_historico = _meses_antes(hoje, meses)
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
        "meses_historico": meses,
        "meses_preco_comparavel": MESES_PRECO_COMPARAVEL,
        "min_entregas_mais_rapido": MIN_ENTREGAS_MAIS_RAPIDO,
    }
