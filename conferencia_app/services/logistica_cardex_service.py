"""Cardex (Logística > Inventário > Cardex): movimento do estoque valorizado
por custo médio, no formato do Registro de Inventário Modelo 7 que a
Contabilidade usa.

De onde vem cada coisa:
- Quantidade: kardex do GRV (public.tproduto_cardex), via bridge. A bridge
  devolve o movimento cru; toda conta é feita aqui.
- Custo da entrada: a NF de entrada (tcompras/tcom_aux), líquido dos impostos
  recuperáveis (ICMS, PIS, COFINS) - é o que dá os 4,07 da planilha da
  Contabilidade, com ICMS 0,61 / PIS 0,07 / COFINS 0,34 por unidade.
- Saída, transferência, ajuste e entrada sem NF (produção, devolução): pelo
  custo médio do momento ("operações de saída utilizam o médio").

O Cardex é o livro do DEPÓSITO 1 (DEPOSITO_CARDEX), pela quantidade total
do depósito (tproduto_deposito.qtde_total, não o disponível): saldo e
movimento dos outros depósitos ficam de fora. Transferência do 1 para outro
depósito aparece como saída de transferência, pelo médio. O custo médio é o
do saldo do depósito 1.

Saldo inicial do período, em ordem de preferência:
1. Fechamento congelado do mês anterior (LogisticaCardexFechamento) - a
   abertura de um mês é o fechamento do outro, como na contabilidade.
2. Sem fechamento anterior: parte do saldo e do custo médio ATUAIS do GRV e
   desfaz, de trás pra frente, todo movimento desde o início do período.

Um mês fechado é servido da foto gravada, não do GRV (o GRV aceita
lançamento retroativo; ver o model).

Arredondamento: cada lançamento é valorizado em centavos (2 casas) e o saldo
em R$ é a soma desses centavos - assim saldo inicial + movimentos = saldo
final fecha exato, e não "quase" como fecharia multiplicando quantidade por
custo médio com todas as casas. O custo médio em si fica com todas as casas.

Desconsiderados (LogisticaCardexDesconsiderado): item ou família que aparece
no Cardex mas não entra nos totais. O cálculo do item é o mesmo; só a soma
do resumo é que os deixa de fora."""
from __future__ import annotations

import calendar
import json
import re
import time
import zlib
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from typing import Any

from flask import current_app

from ..extensions import db
from ..models import (
    LogisticaCardexDesconsiderado,
    LogisticaCardexFechamento,
    LogisticaCardexFechamentoItem,
    LogisticaInventarioAjuste,
)
from ..tempo import agora_br
from .erp_estoque_service import buscar_cardex_grv

EPS = 1e-6
# Decisão da Contabilidade: o Cardex considera sempre e só o depósito 1
# (principal). Somar os outros (em produção, terceiros...) inflava o saldo e
# não batia com o que o GRV mostra no depósito principal.
DEPOSITO_CARDEX = "1"
# Tipos do tproduto_cardex que mexem no saldo (mesma regra do consumo e do
# planejamento). 8 (inventário retroativo) e 9 (troca de material) aparecem
# no Cardex como informativos até a conciliação mostrar que o saldo do GRV
# os considera - ver conciliar().
TIPOS_QUE_MOVEM = (0, 1, 2)
TIPO_ROTULO = {0: "Entrada", 1: "Saída", 2: "Transferência", 8: "Inventário retroativo", 9: "Troca de material"}
CLASSES = ("entrada", "saida", "transferencia", "ajuste")
CLASSE_ROTULO = {
    "entrada": "Entrada",
    "saida": "Saída",
    "transferencia": "Transferência",
    "ajuste": "Ajuste de inventário",
    "informativo": "Informativo",
}

# Colunas candidatas da tcom_aux (item da NF de entrada). Ainda não
# confirmadas no GRV real: a primeira que existir com valor ganha, e a
# coluna usada vai junto na linha (custo_fonte) para a conciliação mostrar.
# Calibrar com /api/erp/estoque/cardex-diag e ajustar SÓ aqui.
CHAVES_VALOR_ITEM = (
    "valor_total", "vl_total", "vlr_total", "valor_total_item", "vl_total_item", "total_item",
    "valor_produto", "vl_produto", "vlr_produto", "valor_mercadoria", "total",
)
CHAVES_PRECO_UNIT = ("preco_unitario", "valor_unitario", "vl_unitario", "vlr_unitario", "preco")
CHAVES_QTDE = ("qtde", "quantidade", "qtd")
CHAVES_IMPOSTO = {
    "icms": ("valor_icms", "vl_icms", "vlr_icms", "icms_valor"),
    "pis": ("valor_pis", "vl_pis", "vlr_pis", "pis_valor"),
    "cofins": ("valor_cofins", "vl_cofins", "vlr_cofins", "cofins_valor"),
    "ibs": ("valor_ibs", "vl_ibs", "vlr_ibs", "ibs_valor"),
    "cbs": ("valor_cbs", "vl_cbs", "vlr_cbs", "cbs_valor"),
}
CHAVES_ACRESCIMO = ("valor_frete", "vl_frete", "valor_seguro", "vl_seguro", "valor_outras", "vl_outras", "vl_despesas")
CHAVES_DESCONTO = ("valor_desconto", "vl_desconto", "desconto")
# Recuperáveis: saem do custo. IBS/CBS em 2026 são só informativos (ano de
# teste da reforma, compensados no PIS/COFINS) - aparecem, mas não abatem.
IMPOSTOS_RECUPERAVEIS = ("icms", "pis", "cofins")

_CACHE: dict[tuple, tuple[float, dict]] = {}
_CACHE_TTL_SEGUNDOS = 600


# --------------------------------------------------------------------------
# Utilitários
# --------------------------------------------------------------------------

def normalizar_codigo(valor: Any) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(valor or "").strip().upper())


def _float(valor: Any) -> float | None:
    if valor is None or valor == "":
        return None
    try:
        return float(valor)
    except (TypeError, ValueError):
        return None


def _centavos(valor: Any) -> float:
    """Valor em R$ com 2 casas. Soma 0.0 para nunca devolver -0.0."""
    return round(float(valor or 0), 2) + 0.0


def _data(valor: Any) -> date:
    if isinstance(valor, datetime):
        return valor.date()
    if isinstance(valor, date):
        return valor
    return datetime.fromisoformat(str(valor)[:10]).date()


def ultimo_dia(ano: int, mes: int) -> date:
    return date(ano, mes, calendar.monthrange(ano, mes)[1])


def periodo_padrao(hoje: date | None = None) -> tuple[date, date]:
    """Mês vigente, do dia 1 ao último dia."""
    hoje = hoje or agora_br().date()
    return date(hoje.year, hoje.month, 1), ultimo_dia(hoje.year, hoje.month)


def parse_periodo(inicio: str | None, fim: str | None) -> tuple[date, date]:
    if not inicio and not fim:
        return periodo_padrao()
    try:
        d_inicio = date.fromisoformat(str(inicio or "")[:10])
        d_fim = date.fromisoformat(str(fim or "")[:10])
    except ValueError:
        raise ValueError("Período inválido: informe início e fim no formato AAAA-MM-DD.")
    if d_inicio > d_fim:
        raise ValueError("A data inicial do período é maior que a final.")
    if (d_fim - d_inicio).days > 366:
        raise ValueError("Período máximo de 1 ano. Para mais que isso, consulte ano a ano.")
    return d_inicio, d_fim


def parse_depositos(valor: str | None) -> set[int] | None:
    """"1,2" -> {1, 2}. Vazio = todos os depósitos."""
    if not valor:
        return None
    depositos = set()
    for parte in str(valor).split(","):
        parte = parte.strip()
        if not parte:
            continue
        if not parte.lstrip("-").isdigit():
            raise ValueError(f"Depósito inválido: {parte}")
        depositos.add(int(parte))
    return depositos or None


def _comprimir(dados: dict) -> bytes:
    return zlib.compress(json.dumps(dados, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def _descomprimir(blob: bytes) -> dict:
    return json.loads(zlib.decompress(blob).decode("utf-8"))


def limpar_cache() -> None:
    _CACHE.clear()


# --------------------------------------------------------------------------
# Custo da NF de entrada
# --------------------------------------------------------------------------

def _primeiro(campos: dict, chaves: tuple[str, ...]) -> tuple[float | None, str | None]:
    for chave in chaves:
        valor = _float(campos.get(chave))
        if valor is not None:
            return valor, chave
    return None, None


# Os nomes exatos acima são chute; o GRV real pode chamar de icms_vl,
# vicms, total_icms... Reconhece pelo padrão: token do imposto + marcador de
# valor, descartando base, alíquota e ST (que não são o imposto próprio).
_MARCADORES_VALOR = {"v", "vl", "vlr", "vr", "valor", "total", "vtotal"}


def _tokens(chave: str) -> set[str]:
    return {t for t in re.split(r"[^a-z0-9]+", chave) if t}


def _imposto(campos: dict, nome: str) -> tuple[float | None, str | None]:
    valor, chave = _primeiro(campos, CHAVES_IMPOSTO[nome])
    if valor is not None:
        return valor, chave
    base = aliq = None
    for chave, bruto in sorted(campos.items()):
        tokens = _tokens(chave)
        colado = re.fullmatch(rf"(v|vl|vlr|vr|valor){nome}|{nome}(v|vl|vlr|vr|valor)", chave)
        if nome not in tokens and not colado:
            continue
        numero = _float(bruto)
        if numero is None:
            continue
        if tokens & {"st", "ret", "retido", "fcp", "difal", "desonerado"}:
            continue
        if tokens & {"base", "bc"}:
            base = base or (numero, chave)
        elif tokens & {"aliq", "aliquota", "perc", "p"}:
            aliq = aliq or (numero, chave)
        elif colado or tokens & _MARCADORES_VALOR:
            return numero, chave
    if base and aliq:
        return base[0] * aliq[0] / 100, f"{base[1]}*{aliq[1]}/100"
    return None, None


def custo_da_nf(linhas_nf: list[dict]) -> dict | None:
    """Soma as linhas da NF que são do produto (pode vir em mais de uma
    linha) e devolve o valor líquido dos impostos recuperáveis. None se
    nenhuma linha tiver valor."""
    bruto = acrescimo = desconto = qtde_nf = 0.0
    impostos = {nome: 0.0 for nome in CHAVES_IMPOSTO}
    fontes: set[str] = set()
    achou = False
    for campos in linhas_nf:
        campos = {str(k).lower(): v for k, v in (campos or {}).items()}
        valor, chave = _primeiro(campos, CHAVES_VALOR_ITEM)
        qtde, chave_qtde = _primeiro(campos, CHAVES_QTDE)
        if valor is None:
            preco, chave_preco = _primeiro(campos, CHAVES_PRECO_UNIT)
            if preco is not None and qtde is not None:
                valor, chave = preco * qtde, f"{chave_preco}*{chave_qtde}"
        if valor is None:
            continue
        achou = True
        bruto += valor
        fontes.add(chave)
        qtde_nf += qtde or 0.0
        for nome in CHAVES_IMPOSTO:
            imposto, chave_imp = _imposto(campos, nome)
            if imposto is not None:
                impostos[nome] += imposto
                fontes.add(chave_imp)
        for chave_extra in CHAVES_ACRESCIMO:
            extra = _float(campos.get(chave_extra))
            if extra:
                acrescimo += extra
                fontes.add(chave_extra)
        desc, chave_desc = _primeiro(campos, CHAVES_DESCONTO)
        if desc:
            desconto += desc
            fontes.add(chave_desc)
    if not achou:
        return None
    liquido = bruto + acrescimo - desconto - sum(impostos[n] for n in IMPOSTOS_RECUPERAVEIS)
    return {
        "valor_bruto": bruto,
        "acrescimo": acrescimo,
        "desconto": desconto,
        "impostos": impostos,
        "valor_liquido": liquido,
        "qtde_nf": qtde_nf,
        "fontes": sorted(fontes),
    }


def _linhas_nf_do_produto(nota: dict, cod_produto: Any, codigo: str) -> list[dict]:
    linhas = []
    for campos in nota.get("itens") or []:
        campos = campos or {}
        if str(campos.get("cod_produto") or "") == str(cod_produto):
            linhas.append(campos)
        elif codigo and normalizar_codigo(campos.get("cod_interno")) == codigo:
            linhas.append(campos)
    return linhas


def _cod_compra(mov: dict) -> str | None:
    if mov.get("tabela") != "TCOMPRAS":
        return None
    partes = str(mov.get("chave") or "").split(";")
    if len(partes) == 2 and partes[1].isdigit():
        return partes[1]
    return None


# --------------------------------------------------------------------------
# Classificação e documento de origem
# --------------------------------------------------------------------------

def _classe(mov: dict) -> str:
    tipo = mov.get("tipo")
    if tipo not in TIPOS_QUE_MOVEM:
        return "informativo"
    if mov.get("tabela") == "TINVENT_DEP" and tipo in (0, 1):
        return "ajuste"
    return {0: "entrada", 1: "saida", 2: "transferencia"}[tipo]


def _ultima_parte(chave: str) -> str:
    return str(chave or "").split(";")[-1].strip()


def _documento(mov: dict, nota: dict | None) -> str:
    tabela = mov.get("tabela") or ""
    ultima = _ultima_parte(mov.get("chave"))
    if tabela == "TCOMPRAS":
        if nota and nota.get("n_nf"):
            fornecedor = str(nota.get("fornecedor") or "").strip()
            return f"NF {nota['n_nf']}" + (f" · {fornecedor}" if fornecedor else "")
        return f"Entrada {ultima}".strip()
    if tabela == "TSAIDA_E":
        achado = re.search(r"SA.DA C.D\. ?(\d+)", str(mov.get("obs") or "").upper())
        return f"Saída {achado.group(1) if achado else ultima}".strip()
    if tabela == "TOS":
        return f"OS {ultima}".strip()
    if tabela == "TINVENT_DEP":
        return f"Inventário {ultima}".strip()
    if tabela == "TNOTA_FISCAL":
        return f"NF emitida {ultima}".strip()
    if tabela == "TPRODUTO":
        return "Cadastro do produto"
    return f"{tabela} {ultima}".strip()


# --------------------------------------------------------------------------
# Motor de valorização
# --------------------------------------------------------------------------

class _Estado:
    """Saldo por depósito + valor e custo médio do produto (global)."""

    def __init__(self, q_por_deposito: dict[str, float], custo_medio: float):
        self.q: dict[str, float] = defaultdict(float, {str(k): float(v or 0) for k, v in q_por_deposito.items()})
        self.m = float(custo_medio or 0)
        self.v = _centavos(self.total_q * self.m)

    @property
    def total_q(self) -> float:
        return sum(self.q.values())

    def aplicar(self, mov: dict) -> dict:
        """Aplica o movimento e devolve custo unitário, valor e o médio depois."""
        if mov["classe"] == "informativo":
            return {"custo_unit": None, "valor": None, "custo_medio": self.m, "saldo_negativo": False}
        qtde = mov["qtde"]
        unit = mov["custo_nf"] if mov.get("custo_nf") is not None else self.m
        valor = _centavos(qtde * unit)
        self.q[str(mov["deposito"])] += qtde
        total = self.total_q
        # Saída/ajuste pelo médio que zera o estoque leva o saldo em R$ inteiro
        # (inclusive o centavo que sobrou dos arredondamentos anteriores): não
        # pode ficar valor pendurado num saldo de quantidade zero.
        if abs(total) <= EPS and mov.get("custo_nf") is None:
            valor = -self.v
        self.v = _centavos(self.v + valor)
        if total > EPS:
            self.m = self.v / total
        else:
            if mov.get("custo_nf") is not None:
                self.m = unit
            # Zerou (ou ficou negativo): o valor acompanha a quantidade.
            self.v = _centavos(total * self.m)
        return {"custo_unit": unit, "valor": valor, "custo_medio": self.m, "saldo_negativo": total < -EPS}

    def foto(self) -> dict:
        return {"q": {k: v for k, v in self.q.items() if abs(v) > EPS}, "m": self.m}


def _abertura_retroativa(saldo_atual: dict[str, float], custo_atual: float, movs: list[dict], alertas: set) -> dict:
    """Desfaz os movimentos (do mais novo pro mais antigo) a partir do saldo e
    do custo médio atuais do GRV. Na volta, a entrada com NF sai pelo custo da
    NF e o resto pelo médio vigente. Quando a quantidade passa por zero o
    médio de antes se perde - usa o custo da NF anterior mais próxima (ou o
    atual) e marca a abertura como estimada."""
    q = defaultdict(float, {str(k): float(v or 0) for k, v in saldo_atual.items()})
    total = sum(q.values())
    valor = total * custo_atual

    # Referência para quando o médio se perde: custo da última NF antes de cada posição.
    referencia: list[float] = []
    ultimo = None
    for mov in movs:
        if mov.get("custo_nf") is not None:
            ultimo = mov["custo_nf"]
        referencia.append(ultimo if ultimo is not None else custo_atual)

    for idx in range(len(movs) - 1, -1, -1):
        mov = movs[idx]
        if mov["classe"] == "informativo":
            continue
        qtde = mov["qtde"]
        q[str(mov["deposito"])] -= qtde
        total_antes = total - qtde
        if mov.get("custo_nf") is not None:
            valor_antes = valor - qtde * mov["custo_nf"]
        else:
            if total > EPS:
                medio = valor / total
            else:
                medio = referencia[idx - 1] if idx > 0 else custo_atual
                alertas.add("custo_estimado")
            valor_antes = total_antes * medio
        if abs(total_antes) <= EPS:
            if abs(valor_antes) > 0.05:
                alertas.add("custo_divergente")
            valor_antes = 0.0
        total, valor = total_antes, valor_antes

    if total > EPS:
        medio = valor / total
        if medio < 0:
            alertas.add("custo_divergente")
            medio = referencia[0] if referencia else custo_atual
    else:
        medio = referencia[0] if referencia else custo_atual
    return {"q": {k: v for k, v in q.items() if abs(v) > EPS}, "m": medio}


def _preparar_movimentos(movs: list[dict], prod: dict, notas: dict) -> list[dict]:
    """Classifica, liga na NF e calcula o custo unitário de entrada."""
    codigo = prod.get("codigo") or normalizar_codigo(prod.get("codigo_interno"))
    # Quantidade que entrou por NF (a mesma NF pode lançar em mais de uma
    # linha, e o estorno vem negativo no mesmo documento).
    qtde_entrada_nf: dict[str, float] = defaultdict(float)
    for mov in movs:
        cod_compra = _cod_compra(mov)
        if cod_compra and float(mov.get("qtde") or 0) > 0:
            qtde_entrada_nf[cod_compra] += float(mov["qtde"])

    custos_nf: dict[str, dict | None] = {}
    preparados = []
    for mov in movs:
        classe = _classe(mov)
        cod_compra = _cod_compra(mov)
        nota = notas.get(cod_compra) if cod_compra else None
        custo_nf = None
        impostos_unit = None
        custo_fonte: list[str] = []
        origem_custo = "medio"
        sem_imposto = False
        if cod_compra and classe == "entrada":
            if cod_compra not in custos_nf:
                custos_nf[cod_compra] = custo_da_nf(_linhas_nf_do_produto(nota or {}, prod.get("cod_produto"), codigo))
            custo = custos_nf[cod_compra]
            base_qtde = qtde_entrada_nf.get(cod_compra) or (custo or {}).get("qtde_nf") or 0
            if custo and base_qtde > EPS:
                custo_nf = custo["valor_liquido"] / base_qtde
                impostos_unit = {nome: valor / base_qtde for nome, valor in custo["impostos"].items()}
                custo_fonte = custo["fontes"]
                origem_custo = "nf"
                # Sem imposto recuperável achado, o custo fica bruto: avisa em
                # vez de deixar a coluna em branco em silêncio.
                sem_imposto = not any(custo["impostos"][n] for n in IMPOSTOS_RECUPERAVEIS)
            else:
                origem_custo = "nf_nao_encontrada"
        nf = None
        if nota:
            nf = {
                "numero": str(nota.get("n_nf") or ""),
                "data": str(nota.get("dt_nf") or "")[:10] or None,
                "fornecedor": nota.get("fornecedor") or "",
                "chave": nota.get("chave_nfe") or "",
                "cfop": nota.get("cfop") or "",
            }
        preparados.append({
            "id": str(mov.get("id") or ""),
            "data": str(mov.get("data") or ""),
            "dia": _data(mov.get("data")).isoformat(),
            "tipo": mov.get("tipo"),
            "tipo_rotulo": TIPO_ROTULO.get(mov.get("tipo"), str(mov.get("tipo"))),
            "classe": classe,
            "deposito": str(mov.get("cod_deposito") if mov.get("cod_deposito") is not None else mov.get("deposito")),
            "qtde": float(mov.get("qtde") or 0),
            "tabela": mov.get("tabela") or "",
            "chave": mov.get("chave") or "",
            "obs": mov.get("obs") or "",
            "documento": _documento(mov, nota),
            "doc_inventario": _ultima_parte(mov.get("chave")) if classe == "ajuste" else None,
            "nf": nf,
            "custo_nf": custo_nf,
            "impostos_unit": impostos_unit,
            "custo_fonte": custo_fonte,
            "origem_custo": origem_custo,
            "sem_imposto": sem_imposto,
        })
    return preparados


def _ncm(prod: dict) -> str:
    fiscal = prod.get("fiscal") or {}
    for chave in ("classificacao_fiscal", "ncm", "cod_ncm", "class_fiscal", "codigo_ncm"):
        if str(fiscal.get(chave) or "").strip():
            return str(fiscal[chave]).strip()
    for valor in fiscal.values():
        if str(valor or "").strip():
            return str(valor).strip()
    return ""


def calcular_item(
    prod: dict,
    movs: list[dict],
    notas: dict,
    *,
    inicio: date,
    fim: date,
    abertura_ancora: dict | None = None,
    comparar_saldo_atual: bool = False,
) -> dict | None:
    """Cardex de um produto no período. `movs` em ordem cronológica: com
    âncora, do dia seguinte ao fechamento anterior até `fim`; sem âncora, de
    `inicio` até hoje (para desfazer a partir do saldo atual). None se o item
    não tem saldo nem movimento no período."""
    alertas: set[str] = set()
    # Só o depósito do Cardex. O filtro vem DEPOIS de preparar: o custo
    # unitário da NF divide pelo que a nota lançou em todos os depósitos.
    preparados = [m for m in _preparar_movimentos(movs, prod, notas) if m["deposito"] == DEPOSITO_CARDEX]
    inicio_iso, fim_iso = inicio.isoformat(), fim.isoformat()
    saldos_grv = {
        str(k): float(v or 0) for k, v in (prod.get("saldos") or {}).items() if str(k) == DEPOSITO_CARDEX
    }

    if abertura_ancora is not None:
        # Fechamento gravado antes desta regra pode ter outros depósitos na foto.
        q_ancora = {k: v for k, v in (abertura_ancora.get("q") or {}).items() if str(k) == DEPOSITO_CARDEX}
        estado = _Estado(q_ancora, abertura_ancora.get("m") or 0)
        for mov in preparados:
            if mov["dia"] < inicio_iso:
                estado.aplicar(mov)
        abertura = estado.foto()
    else:
        custo_atual = _float(prod.get("preco_custo"))
        if custo_atual is None:
            alertas.add("sem_custo_grv")
            custo_atual = 0.0
        abertura = _abertura_retroativa(saldos_grv, custo_atual, [m for m in preparados if m["dia"] >= inicio_iso], alertas)

    estado = _Estado(abertura["q"], abertura["m"])
    linhas = []
    for mov in preparados:
        if mov["dia"] < inicio_iso or mov["dia"] > fim_iso:
            continue
        resultado = estado.aplicar(mov)
        if resultado["saldo_negativo"]:
            alertas.add("saldo_negativo")
        if mov["origem_custo"] == "nf_nao_encontrada":
            alertas.add("nf_sem_custo")
        if mov.get("sem_imposto"):
            alertas.add("nf_sem_imposto")
        linha = {k: v for k, v in mov.items() if k not in ("custo_nf", "dia")}
        linha.update(resultado)
        linhas.append(linha)
    fechamento = estado.foto()

    if comparar_saldo_atual:
        for dep in set(saldos_grv) | set(fechamento["q"]):
            if abs(saldos_grv.get(dep, 0) - fechamento["q"].get(dep, 0)) > 0.001:
                alertas.add("divergente_grv")
                break

    if not linhas and not abertura["q"] and not fechamento["q"]:
        return None
    return {
        "cod_produto": str(prod.get("cod_produto") or ""),
        "codigo": prod.get("codigo") or normalizar_codigo(prod.get("codigo_interno")),
        "codigo_interno": prod.get("codigo_interno") or "",
        "descricao": prod.get("descricao") or "",
        "unidade": prod.get("unidade") or "",
        "familia": prod.get("familia") or "",
        "grupo": prod.get("grupo") or "",
        "ncm": prod.get("ncm") if "ncm" in prod else _ncm(prod),
        "abertura": abertura,
        "fechamento": fechamento,
        "linhas": linhas,
        "alertas": sorted(alertas),
    }


def _metadados_de_item(item: dict) -> dict:
    """Produto a partir de um item de fechamento (item que o GRV não devolveu)."""
    return {k: item.get(k) for k in ("cod_produto", "codigo", "codigo_interno", "descricao", "unidade", "familia", "grupo", "ncm")}


def montar_itens(
    grv: dict,
    *,
    inicio: date,
    fim: date,
    ancoras: dict[str, dict] | None,
    hoje: date,
) -> list[dict]:
    produtos = list(grv.get("produtos") or [])
    movs_por_produto: dict[str, list[dict]] = defaultdict(list)
    for mov in grv.get("movimentos") or []:
        movs_por_produto[str(mov.get("cod_produto"))].append(mov)
    notas = {str(k): v for k, v in (grv.get("notas") or {}).items()}
    comparar = fim >= hoje

    itens = []
    vistos = set()
    for prod in produtos:
        prod = dict(prod)
        prod["codigo"] = prod.get("codigo") or normalizar_codigo(prod.get("codigo_interno"))
        vistos.add(prod["codigo"])
        abertura = None
        if ancoras is not None:
            ancora = ancoras.get(prod["codigo"])
            abertura = ancora["fechamento"] if ancora else {"q": {}, "m": _float(prod.get("preco_custo")) or 0.0}
        item = calcular_item(
            prod,
            movs_por_produto.get(str(prod.get("cod_produto")), []),
            notas,
            inicio=inicio,
            fim=fim,
            abertura_ancora=abertura,
            comparar_saldo_atual=comparar,
        )
        if item:
            itens.append(item)
    # Item com saldo no fechamento anterior que o GRV não devolveu (sem saldo
    # hoje e sem movimento): continua no Cardex com a abertura da âncora.
    for codigo, ancora in (ancoras or {}).items():
        if codigo in vistos or not ancora["fechamento"].get("q"):
            continue
        item = calcular_item(
            _metadados_de_item(ancora), [], notas, inicio=inicio, fim=fim,
            abertura_ancora=ancora["fechamento"], comparar_saldo_atual=comparar,
        )
        if item:
            if comparar:
                item["alertas"] = sorted(set(item["alertas"]) | {"divergente_grv"})
            itens.append(item)
    itens.sort(key=lambda i: (i["familia"], i["codigo_interno"] or i["codigo"]))
    return itens


# --------------------------------------------------------------------------
# Visões (resumo e detalhe) para uma seleção de depósitos
# --------------------------------------------------------------------------

def _q_sel(q_por_dep: dict[str, float], depositos: set[int] | None) -> float:
    return sum(v for k, v in q_por_dep.items() if depositos is None or int(k) in depositos)


def _no_filtro(dep: str, depositos: set[int] | None) -> bool:
    return depositos is None or int(dep) in depositos


def detalhe(item: dict, depositos: set[int] | None = None) -> dict:
    """Modelo 7: saldo inicial, cada movimento com saldo acumulado, saldo final."""
    m_ini = item["abertura"]["m"]
    q = _q_sel(item["abertura"]["q"], depositos)
    inicial = {"qtde": q, "valor": _centavos(q * m_ini), "custo_medio": m_ini}
    linhas = []
    for linha in item["linhas"]:
        if not _no_filtro(linha["deposito"], depositos):
            continue
        if linha["classe"] != "informativo":
            q += linha["qtde"]
        linhas.append({**linha, "saldo_qtde": q, "saldo_valor": _centavos(q * linha["custo_medio"])})
    m_fim = item["fechamento"]["m"]
    q_fim = _q_sel(item["fechamento"]["q"], depositos)
    return {
        **{k: item[k] for k in ("codigo", "codigo_interno", "descricao", "unidade", "familia", "grupo", "ncm", "alertas")},
        "inicial": inicial,
        "linhas": linhas,
        "final": {"qtde": q_fim, "valor": _centavos(q_fim * m_fim), "custo_medio": m_fim},
    }


def resumo(item: dict, depositos: set[int] | None = None) -> dict:
    m_ini, m_fim = item["abertura"]["m"], item["fechamento"]["m"]
    q_ini = _q_sel(item["abertura"]["q"], depositos)
    q_fim = _q_sel(item["fechamento"]["q"], depositos)
    linha = {
        **{k: item[k] for k in ("codigo", "codigo_interno", "descricao", "unidade", "familia", "grupo", "ncm", "alertas")},
        "qtde_inicial": q_ini,
        "valor_inicial": _centavos(q_ini * m_ini),
        "custo_medio_inicial": m_ini,
        "qtde_final": q_fim,
        "valor_final": _centavos(q_fim * m_fim),
        "custo_medio_final": m_fim,
        "movimentos": 0,
    }
    for classe in CLASSES:
        linha[f"qtde_{classe}"] = 0.0
        linha[f"valor_{classe}"] = 0.0
    for mov in item["linhas"]:
        if mov["classe"] == "informativo" or not _no_filtro(mov["deposito"], depositos):
            continue
        linha["movimentos"] += 1
        linha[f"qtde_{mov['classe']}"] += mov["qtde"]
        linha[f"valor_{mov['classe']}"] += mov["valor"] or 0
    for classe in CLASSES:
        linha[f"valor_{classe}"] = _centavos(linha[f"valor_{classe}"])
    # Com um depósito só, a entrada em outro depósito muda o médio e reavalia
    # este sem linha própria. Com todos os depósitos isso dá zero.
    linha["valor_reavaliacao"] = _centavos(linha["valor_final"] - linha["valor_inicial"] - sum(
        linha[f"valor_{c}"] for c in CLASSES
    ))
    return linha


CAMPOS_TOTAIS = (
    ["qtde_inicial", "valor_inicial", "qtde_final", "valor_final", "valor_reavaliacao", "movimentos"]
    + [f"qtde_{c}" for c in CLASSES]
    + [f"valor_{c}" for c in CLASSES]
)


def totalizar(linhas: list[dict]) -> dict:
    totais = {campo: sum(l.get(campo) or 0 for l in linhas) for campo in CAMPOS_TOTAIS}
    for campo in CAMPOS_TOTAIS:
        if campo.startswith("valor_"):
            totais[campo] = _centavos(totais[campo])
    return totais


# --------------------------------------------------------------------------
# Desconsiderados: aparecem, mas não entram nos totais
# --------------------------------------------------------------------------

TIPOS_DESCONSIDERADO = ("ITEM", "FAMILIA")


def desconsiderado_para_dict(reg: LogisticaCardexDesconsiderado) -> dict:
    return {
        "id": reg.id,
        "tipo": reg.tipo,
        "chave": reg.chave,
        "descricao": reg.descricao or "",
        "motivo": reg.motivo,
        "por": reg.criado_por,
        "em": reg.criado_em.isoformat(timespec="seconds") if reg.criado_em else None,
    }


def listar_desconsiderados() -> list[dict]:
    regs = (
        LogisticaCardexDesconsiderado.query
        .filter_by(ativo=True)
        .order_by(LogisticaCardexDesconsiderado.tipo, LogisticaCardexDesconsiderado.chave)
        .all()
    )
    return [desconsiderado_para_dict(r) for r in regs]


def _mapa_desconsiderados() -> dict[str, dict[str, dict]]:
    mapa: dict[str, dict[str, dict]] = {"ITEM": {}, "FAMILIA": {}}
    for reg in listar_desconsiderados():
        mapa[reg["tipo"]][reg["chave"]] = reg
    return mapa


def _marca_desconsiderado(item: dict, mapa: dict | None) -> dict | None:
    """A marca que vale para o item. `mapa` None = mês congelado: vale a que
    foi gravada na foto (mês fechado não muda quando alguém desconsidera
    depois). Item marcado ganha da família."""
    if mapa is None:
        return item.get("desconsiderado") or None
    return mapa["ITEM"].get(item["codigo"]) or mapa["FAMILIA"].get(item.get("familia") or "") or None


def desconsiderar(tipo: str, chave: str, motivo: str, usuario: str, descricao: str = "") -> dict:
    tipo = str(tipo or "").strip().upper()
    if tipo not in TIPOS_DESCONSIDERADO:
        raise ValueError("Informe se é para desconsiderar um item ou uma família.")
    chave = normalizar_codigo(chave) if tipo == "ITEM" else str(chave or "").strip()
    if not chave:
        raise ValueError("Informe o código do item." if tipo == "ITEM" else "Informe a família.")
    motivo = str(motivo or "").strip()
    if len(motivo) < 5:
        raise ValueError("Informe o motivo (mínimo de 5 caracteres).")
    if LogisticaCardexDesconsiderado.query.filter_by(tipo=tipo, chave=chave, ativo=True).first():
        raise ValueError("Este item já está desconsiderado." if tipo == "ITEM" else "Esta família já está desconsiderada.")
    reg = LogisticaCardexDesconsiderado(
        tipo=tipo, chave=chave[:120], descricao=str(descricao or "").strip()[:300],
        motivo=motivo[:500], criado_por=usuario, criado_em=agora_br(), ativo=True,
    )
    db.session.add(reg)
    db.session.commit()
    current_app.logger.info("Cardex: %s %s desconsiderado por %s (%s)", tipo, chave, usuario, motivo)
    return desconsiderado_para_dict(reg)


def reconsiderar(desconsiderado_id: int, usuario: str) -> dict | None:
    reg = db.session.get(LogisticaCardexDesconsiderado, desconsiderado_id)
    if not reg:
        return None
    if not reg.ativo:
        raise ValueError("Este registro já foi desfeito.")
    reg.ativo = False
    reg.removido_em = agora_br()
    reg.removido_por = usuario
    db.session.commit()
    current_app.logger.info("Cardex: %s %s voltou a ser considerado por %s", reg.tipo, reg.chave, usuario)
    return desconsiderado_para_dict(reg)


# --------------------------------------------------------------------------
# Período: foto congelada, âncora ou GRV
# --------------------------------------------------------------------------

def _fechamento_exato(inicio: date, fim: date) -> LogisticaCardexFechamento | None:
    return LogisticaCardexFechamento.query.filter_by(status="fechado", data_inicio=inicio, data_fim=fim).first()


def _ancora_para(inicio: date) -> LogisticaCardexFechamento | None:
    return (
        LogisticaCardexFechamento.query
        .filter(LogisticaCardexFechamento.status == "fechado", LogisticaCardexFechamento.data_fim < inicio)
        .order_by(LogisticaCardexFechamento.data_fim.desc())
        .first()
    )


def _itens_do_fechamento(fechamento: LogisticaCardexFechamento, codigo: str | None = None) -> list[dict]:
    query = fechamento.itens
    if codigo:
        query = query.filter(LogisticaCardexFechamentoItem.codigo == codigo)
    return [_descomprimir(i.dados) for i in query.order_by(LogisticaCardexFechamentoItem.id).all()]


def _meta(fechamento: LogisticaCardexFechamento) -> dict:
    try:
        return json.loads(fechamento.meta_json or "{}")
    except ValueError:
        return {}


def fechamento_para_dict(f: LogisticaCardexFechamento | None) -> dict | None:
    if not f:
        return None
    return {
        "id": f.id,
        "ano": f.ano,
        "mes": f.mes,
        "rotulo": f"{f.mes:02d}/{f.ano}",
        "data_inicio": f.data_inicio.isoformat(),
        "data_fim": f.data_fim.isoformat(),
        "status": f.status,
        "origem_abertura": f.origem_abertura,
        "total_itens": f.total_itens,
        "valor_inicial": f.valor_inicial,
        "valor_entradas": f.valor_entradas,
        "valor_saidas": f.valor_saidas,
        "valor_ajustes": f.valor_ajustes,
        "valor_final": f.valor_final,
        "fechado_em": f.fechado_em.isoformat() if f.fechado_em else None,
        "fechado_por": f.fechado_por,
        "reaberto_em": f.reaberto_em.isoformat() if f.reaberto_em else None,
        "reaberto_por": f.reaberto_por,
        "motivo_reabertura": f.motivo_reabertura,
        "avisos": _meta(f).get("avisos") or [],
        "desconsiderados": _meta(f).get("desconsiderados") or {"itens": 0, "valor_final": 0.0},
    }


def _depositos_nomes(grv_depositos: list[dict]) -> dict[str, str]:
    return {str(d.get("codigo")): str(d.get("nome") or "") for d in grv_depositos or [] if d.get("codigo") is not None}


def calcular_ao_vivo(inicio: date, fim: date, codigo: str | None = None, forcar: bool = False) -> dict:
    """Calcula no GRV (com a âncora do último fechamento antes do período)."""
    hoje = agora_br().date()
    ancora = _ancora_para(inicio)
    chave = (inicio, fim, codigo, ancora.id if ancora else None)
    agora = time.monotonic()
    if not forcar and chave in _CACHE and _CACHE[chave][0] > agora:
        return _CACHE[chave][1]
    # O resumo do período inteiro já tem o item: não precisa ir ao GRV de novo.
    if codigo and not forcar:
        chave_todos = (inicio, fim, None, ancora.id if ancora else None)
        if chave_todos in _CACHE and _CACHE[chave_todos][0] > agora:
            todos = _CACHE[chave_todos][1]
            return {**todos, "itens": [i for i in todos["itens"] if i["codigo"] == codigo]}

    ancoras = None
    if ancora:
        ancoras = {i["codigo"]: i for i in _itens_do_fechamento(ancora, codigo)}
        grv = buscar_cardex_grv(ancora.data_fim + timedelta(days=1), fim, codigo=codigo)
    else:
        grv = buscar_cardex_grv(inicio, None, codigo=codigo)

    resultado: dict[str, Any] = {
        "disponivel": bool(grv.get("disponivel")),
        "erro": grv.get("erro"),
        "fonte": "ancora" if ancora else "grv",
        "fechamento": None,
        "ancora": fechamento_para_dict(ancora),
        "depositos_nomes": _depositos_nomes(grv.get("depositos") or []),
        "itens": [],
        "avisos": [],
        "gerado_em": agora_br().isoformat(timespec="seconds"),
    }
    if not grv.get("disponivel"):
        return resultado
    resultado["itens"] = montar_itens(grv, inicio=inicio, fim=fim, ancoras=ancoras, hoje=hoje)
    anterior = inicio.replace(day=1) - timedelta(days=1)
    if ancora and ancora.data_fim < anterior:
        resultado["avisos"].append(
            f"O mês anterior não está fechado: a abertura parte do fechamento de {ancora.mes:02d}/{ancora.ano} "
            f"e refaz o movimento até {inicio.strftime('%d/%m/%Y')} pelo GRV de hoje."
        )
    if not ancora:
        resultado["avisos"].append(
            "Sem mês fechado antes deste período: o saldo inicial foi recalculado de trás pra frente a partir "
            "do saldo e do custo médio atuais do GRV."
        )
    _CACHE[chave] = (agora + _CACHE_TTL_SEGUNDOS, resultado)
    return resultado


def obter_periodo(inicio: date, fim: date, codigo: str | None = None, forcar: bool = False) -> dict:
    fechamento = _fechamento_exato(inicio, fim)
    if fechamento:
        meta = _meta(fechamento)
        return {
            "disponivel": True,
            "erro": None,
            "fonte": "congelado",
            "fechamento": fechamento_para_dict(fechamento),
            "ancora": None,
            "depositos_nomes": meta.get("depositos") or {},
            "itens": _itens_do_fechamento(fechamento, codigo),
            "avisos": list(meta.get("avisos") or []),
            "gerado_em": fechamento.fechado_em.isoformat(timespec="seconds") if fechamento.fechado_em else None,
        }
    return calcular_ao_vivo(inicio, fim, codigo=codigo, forcar=forcar)


def _depositos_do_periodo(itens: list[dict], nomes: dict[str, str]) -> list[dict]:
    codigos: set[str] = set()
    for item in itens:
        codigos |= set(item["abertura"]["q"]) | set(item["fechamento"]["q"])
        codigos |= {l["deposito"] for l in item["linhas"]}
    return [
        {"codigo": int(c), "nome": nomes.get(c) or f"Depósito {c}"}
        for c in sorted(codigos, key=lambda c: int(c) if c.lstrip("-").isdigit() else 0)
        if c.lstrip("-").isdigit()
    ]


def _cabecalho(inicio: date, fim: date, base: dict, depositos: set[int] | None) -> dict:
    return {
        "periodo": {"inicio": inicio.isoformat(), "fim": fim.isoformat()},
        "disponivel": base["disponivel"],
        "erro": base.get("erro"),
        "fonte": base["fonte"],
        "fechamento": base["fechamento"],
        "ancora": base["ancora"],
        "avisos": base["avisos"],
        "gerado_em": base.get("gerado_em"),
        "depositos": _depositos_do_periodo(base["itens"], base["depositos_nomes"]),
        "depositos_selecionados": sorted(depositos) if depositos else [],
    }


def consultar_resumo(
    inicio: date,
    fim: date,
    depositos: set[int] | None = None,
    familia: str = "",
    busca: str = "",
    somente_movimento: bool = False,
    forcar: bool = False,
) -> dict:
    base = obter_periodo(inicio, fim, forcar=forcar)
    saida = _cabecalho(inicio, fim, base, depositos)
    mapa = None if base["fonte"] == "congelado" else _mapa_desconsiderados()
    linhas_todas = [
        {**resumo(item, depositos), "desconsiderado": _marca_desconsiderado(item, mapa)}
        for item in base["itens"]
    ]
    # Item sem nada no(s) depósito(s) escolhido(s) some do resumo.
    linhas_todas = [
        l for l in linhas_todas
        if l["movimentos"] or abs(l["qtde_inicial"]) > EPS or abs(l["qtde_final"]) > EPS
    ]
    saida["familias"] = sorted({l["familia"] for l in linhas_todas})

    termo = normalizar_codigo(busca)
    termo_texto = str(busca or "").strip().upper()
    sem_familia = []  # passou em busca/movimento; ainda não filtrado por família
    for l in linhas_todas:
        if somente_movimento and not l["movimentos"]:
            continue
        if termo_texto and termo not in l["codigo"] and termo_texto not in l["descricao"].upper():
            continue
        sem_familia.append(l)
    linhas = [l for l in sem_familia if not familia or l["familia"] == familia]

    # Painel por família: sempre com todas as famílias (o filtro de família
    # não o esconde), para dar para comparar e desconsiderar uma inteira.
    por_familia_todas: dict[str, list[dict]] = defaultdict(list)
    for l in sem_familia:
        por_familia_todas[l["familia"]].append(l)
    saida["familias_resumo"] = [
        {
            "familia": fam,
            "itens": len(ls),
            "itens_desconsiderados": sum(1 for l in ls if l["desconsiderado"]),
            "desconsiderada": (mapa or {}).get("FAMILIA", {}).get(fam),
            "totais": totalizar([l for l in ls if not l["desconsiderado"]]),
            "valor_desconsiderado": _centavos(sum(l["valor_final"] for l in ls if l["desconsiderado"])),
        }
        for fam, ls in sorted(por_familia_todas.items())
    ]

    # Só os considerados entram em subtotal, total e indicador. A família
    # inteira desconsiderada continua com subtotal (zerado) para a exportação.
    considerados = [l for l in linhas if not l["desconsiderado"]]
    desconsiderados = [l for l in linhas if l["desconsiderado"]]
    por_familia: dict[str, list[dict]] = {l["familia"]: [] for l in linhas}
    for l in considerados:
        por_familia[l["familia"]].append(l)
    saida["linhas"] = linhas
    saida["subtotais_familia"] = {fam: totalizar(ls) for fam, ls in por_familia.items()}
    totais = totalizar(considerados)
    saida["totais"] = totais
    saida["desconsiderados"] = {"itens": len(desconsiderados), "totais": totalizar(desconsiderados)}
    saida["indicadores"] = {
        "itens": len(considerados),
        "itens_desconsiderados": len(desconsiderados),
        "valor_desconsiderado": saida["desconsiderados"]["totais"]["valor_final"],
        "itens_com_movimento": sum(1 for l in considerados if l["movimentos"]),
        "itens_sem_movimento": sum(1 for l in considerados if not l["movimentos"] and abs(l["qtde_final"]) > EPS),
        "itens_com_alerta": sum(1 for l in considerados if l["alertas"]),
        "valor_inicial": totais["valor_inicial"],
        "valor_entradas": totais["valor_entrada"],
        "valor_saidas": totais["valor_saida"],
        "valor_ajustes": totais["valor_ajuste"],
        "valor_transferencias": totais["valor_transferencia"],
        "valor_final": totais["valor_final"],
        "variacao": _centavos(totais["valor_final"] - totais["valor_inicial"]),
        # Tudo o que não é entrada nem saída (inventário, transferência e
        # reavaliação): é a parcela que faz inicial + entradas + saídas fechar no final.
        "valor_outros": _centavos(
            totais["valor_final"] - totais["valor_inicial"] - totais["valor_entrada"] - totais["valor_saida"]
        ),
    }
    return saida


def _ajustes_sync_por_documento(codigos: set[str], inicio: date, fim: date) -> dict[tuple[str, str], LogisticaInventarioAjuste]:
    """Ajustes do módulo de Inventário lançados no GRV (documento informado no Finance)."""
    if not codigos:
        return {}
    ajustes = (
        LogisticaInventarioAjuste.query
        .filter(LogisticaInventarioAjuste.finance_documento_grv.isnot(None))
        .filter(LogisticaInventarioAjuste.finance_concluido_em >= datetime.combine(inicio - timedelta(days=31), datetime.min.time()))
        .filter(LogisticaInventarioAjuste.finance_concluido_em < datetime.combine(fim + timedelta(days=32), datetime.min.time()))
        .all()
    )
    mapa = {}
    for ajuste in ajustes:
        codigo = normalizar_codigo(ajuste.codigo_produto)
        if codigo in codigos:
            mapa[(codigo, str(ajuste.finance_documento_grv or "").strip())] = ajuste
    return mapa


def consultar_item(codigo: str, inicio: date, fim: date, depositos: set[int] | None = None, forcar: bool = False) -> dict | None:
    codigo = normalizar_codigo(codigo)
    if not codigo:
        raise ValueError("Informe o código do item.")
    base = obter_periodo(inicio, fim, codigo=codigo, forcar=forcar)
    saida = _cabecalho(inicio, fim, base, depositos)
    if not base["disponivel"]:
        saida["item"] = None
        return saida
    item = next((i for i in base["itens"] if i["codigo"] == codigo), None)
    if item is None:
        return None
    ajustes = _ajustes_sync_por_documento({codigo}, inicio, fim)
    visao = detalhe(item, depositos)
    for linha in visao["linhas"]:
        if linha["classe"] == "ajuste":
            ajuste = ajustes.get((codigo, str(linha.get("doc_inventario") or "")))
            linha["ajuste_sync"] = (
                {"id": ajuste.id, "status": ajuste.status_modulo, "diferenca": ajuste.diferenca} if ajuste else None
            )
    saida["item"] = visao
    saida["resumo"] = resumo(item, depositos)
    saida["desconsiderado"] = _marca_desconsiderado(
        item, None if base["fonte"] == "congelado" else _mapa_desconsiderados()
    )
    return saida


# --------------------------------------------------------------------------
# Conciliação
# --------------------------------------------------------------------------

ALERTA_ROTULO = {
    "nf_sem_custo": "Entrada de NF sem valor encontrado na NF (valorizada pelo custo médio)",
    "nf_sem_imposto": "Entrada de NF sem ICMS/PIS/COFINS encontrado (custo pelo valor bruto da NF)",
    "custo_estimado": "Saldo passou por zero: custo médio de abertura estimado pela NF anterior",
    "custo_divergente": "Custo do GRV não fecha com o histórico de entradas (resíduo ao zerar o saldo)",
    "saldo_negativo": "Saldo ficou negativo no período",
    "sem_custo_grv": "Produto sem custo médio no GRV",
    "divergente_grv": "Saldo final calculado diferente do saldo atual do GRV",
}


def conciliar(inicio: date, fim: date, forcar: bool = False) -> dict:
    base = obter_periodo(inicio, fim, forcar=forcar)
    saida: dict[str, Any] = {
        "periodo": {"inicio": inicio.isoformat(), "fim": fim.isoformat()},
        "disponivel": base["disponivel"],
        "erro": base.get("erro"),
        "fonte": base["fonte"],
        "fechamento": base["fechamento"],
        "ancora": base["ancora"],
    }
    if not base["disponivel"]:
        return saida
    itens = base["itens"]

    # 1. Alertas de custo/saldo por item.
    alertas: dict[str, list[dict]] = defaultdict(list)
    for item in itens:
        for alerta in item["alertas"]:
            alertas[alerta].append({"codigo": item["codigo"], "codigo_interno": item["codigo_interno"], "descricao": item["descricao"]})
    saida["alertas"] = [
        {"alerta": a, "rotulo": ALERTA_ROTULO.get(a, a), "itens": lista}
        for a, lista in sorted(alertas.items(), key=lambda kv: -len(kv[1]))
    ]

    # 2. De onde saiu o custo das entradas (as colunas da NF usadas).
    fontes: Counter = Counter()
    origens: Counter = Counter()
    for item in itens:
        for linha in item["linhas"]:
            if linha["classe"] == "entrada":
                origens[linha.get("origem_custo") or "medio"] += 1
                for fonte in linha.get("custo_fonte") or []:
                    fontes[fonte] += 1
    saida["custo_entradas"] = {"origens": dict(origens), "colunas": dict(fontes.most_common())}

    # 3. Ajustes de inventário: GRV x módulo de Inventário do Sync.
    codigos = {i["codigo"] for i in itens}
    ajustes_sync = _ajustes_sync_por_documento(codigos, inicio, fim)
    no_grv = set()
    ajustes_grv = []
    for item in itens:
        por_doc: dict[str, float] = defaultdict(float)
        for linha in item["linhas"]:
            if linha["classe"] == "ajuste":
                por_doc[str(linha.get("doc_inventario") or "")] += linha["qtde"]
        for doc, liquido in por_doc.items():
            no_grv.add((item["codigo"], doc))
            ajuste = ajustes_sync.get((item["codigo"], doc))
            ajustes_grv.append({
                "codigo": item["codigo"], "codigo_interno": item["codigo_interno"], "descricao": item["descricao"],
                "documento": doc, "qtde_liquida": liquido,
                "ajuste_sync_id": ajuste.id if ajuste else None,
                "diferenca_sync": ajuste.diferenca if ajuste else None,
            })
    inicio_dt = datetime.combine(inicio, datetime.min.time())
    fim_dt = datetime.combine(fim + timedelta(days=1), datetime.min.time())
    saida["ajustes"] = {
        "no_grv": ajustes_grv,
        "sem_movimento_no_grv": [
            {"ajuste_id": a.id, "codigo": cod, "documento": doc, "descricao": a.descricao_produto, "diferenca": a.diferenca}
            for (cod, doc), a in ajustes_sync.items()
            if (cod, doc) not in no_grv and a.finance_concluido_em and inicio_dt <= a.finance_concluido_em < fim_dt
        ],
    }

    # 4. O kardex fecha com o saldo do GRV? Soma de tudo o que já moveu por
    # depósito contra o tproduto_deposito, com e sem os tipos 8/9.
    historico = buscar_cardex_grv(inicio, None, apenas_historico=True)
    saldo = {"disponivel": bool(historico.get("disponivel")), "erro": historico.get("erro")}
    if historico.get("disponivel"):
        soma: dict[tuple[str, str], dict[int, float]] = defaultdict(lambda: defaultdict(float))
        for h in historico.get("historico") or []:
            soma[(str(h["cod_produto"]), str(h["deposito"]))][int(h["tipo"])] += float(h["qtde"] or 0)
        divergentes = []
        fecha_sem, fecha_com = 0, 0
        for prod in historico.get("produtos") or []:
            cod = str(prod.get("cod_produto"))
            saldos = {str(k): float(v or 0) for k, v in (prod.get("saldos") or {}).items()}
            deps = set(saldos) | {d for (c, d) in soma if c == cod}
            for dep in deps:
                if dep != DEPOSITO_CARDEX:
                    continue
                tipos = soma.get((cod, dep), {})
                movem = sum(v for t, v in tipos.items() if t in TIPOS_QUE_MOVEM)
                extras = sum(v for t, v in tipos.items() if t not in TIPOS_QUE_MOVEM)
                grv = saldos.get(dep, 0.0)
                if abs(movem - grv) <= 0.001:
                    fecha_sem += 1
                    continue
                if abs(extras) > EPS and abs(movem + extras - grv) <= 0.001:
                    fecha_com += 1
                divergentes.append({
                    "codigo": prod.get("codigo") or normalizar_codigo(prod.get("codigo_interno")),
                    "codigo_interno": prod.get("codigo_interno"), "descricao": prod.get("descricao"),
                    "deposito": dep, "saldo_grv": grv, "soma_kardex": movem, "soma_tipos_8_9": extras,
                    "diferenca": grv - movem,
                })
        divergentes.sort(key=lambda d: -abs(d["diferenca"]))
        saldo.update({
            "conferidos": fecha_sem + len(divergentes),
            "fecham": fecha_sem,
            "fecham_so_com_tipos_8_9": fecha_com,
            "divergentes": divergentes[:500],
            "total_divergentes": len(divergentes),
        })
    saida["saldo"] = saldo
    return saida


# --------------------------------------------------------------------------
# Fechamento (congelar), reabertura e conferência
# --------------------------------------------------------------------------

def listar_fechamentos() -> list[dict]:
    fechamentos = LogisticaCardexFechamento.query.order_by(
        LogisticaCardexFechamento.data_inicio.desc(), LogisticaCardexFechamento.id.desc()
    ).all()
    return [fechamento_para_dict(f) for f in fechamentos]


def fechar_mes(ano: int, mes: int, usuario: str, hoje: date | None = None) -> dict:
    try:
        ano, mes = int(ano), int(mes)
        inicio = date(ano, mes, 1)
    except (TypeError, ValueError):
        raise ValueError("Mês inválido.")
    fim = ultimo_dia(ano, mes)
    hoje = hoje or agora_br().date()
    if fim >= hoje:
        raise ValueError(f"Só dá para fechar {mes:02d}/{ano} depois que o mês terminar (a partir de {(fim + timedelta(days=1)).strftime('%d/%m/%Y')}).")
    if _fechamento_exato(inicio, fim):
        raise ValueError(f"{mes:02d}/{ano} já está fechado. Para refazer, reabra o mês primeiro.")

    base = calcular_ao_vivo(inicio, fim, forcar=True)
    if not base["disponivel"]:
        raise ValueError(base.get("erro") or "GRV indisponível: não foi possível fechar o mês.")

    avisos = list(base["avisos"])
    posterior = (
        LogisticaCardexFechamento.query
        .filter(LogisticaCardexFechamento.status == "fechado", LogisticaCardexFechamento.data_inicio > fim)
        .first()
    )
    if posterior:
        avisos.append(
            f"Fechado depois de {posterior.mes:02d}/{posterior.ano}, que já estava fechado com outra abertura."
        )
    # A marca de desconsiderado vai gravada na foto: o mês fechado continua
    # igual mesmo que alguém desconsidere (ou volte a considerar) depois.
    mapa = _mapa_desconsiderados()
    itens = [{**item, "desconsiderado": _marca_desconsiderado(item, mapa)} for item in base["itens"]]
    linhas = [resumo(item, None) for item in itens]
    fora = [l for item, l in zip(itens, linhas) if item["desconsiderado"]]
    totais = totalizar([l for item, l in zip(itens, linhas) if not item["desconsiderado"]])
    resumo_fora = {"itens": len(fora), "valor_final": _centavos(sum(l["valor_final"] for l in fora))}
    if fora:
        valor_br = f"{resumo_fora['valor_final']:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        avisos.append(
            f"{len(fora)} item(ns) desconsiderado(s) ficaram fora dos totais deste fechamento "
            f"(R$ {valor_br} de saldo final)."
        )
    fechamento = LogisticaCardexFechamento(
        ano=ano,
        mes=mes,
        data_inicio=inicio,
        data_fim=fim,
        status="fechado",
        origem_abertura=base["fonte"],
        total_itens=len(base["itens"]),
        valor_inicial=totais["valor_inicial"],
        valor_entradas=totais["valor_entrada"],
        valor_saidas=totais["valor_saida"],
        valor_ajustes=totais["valor_ajuste"],
        valor_final=totais["valor_final"],
        meta_json=json.dumps(
            {"depositos": base["depositos_nomes"], "avisos": avisos, "desconsiderados": resumo_fora},
            ensure_ascii=False,
        ),
        fechado_em=agora_br(),
        fechado_por=usuario,
    )
    db.session.add(fechamento)
    db.session.flush()
    for item, linha in zip(itens, linhas):
        db.session.add(LogisticaCardexFechamentoItem(
            fechamento_id=fechamento.id,
            codigo=item["codigo"],
            codigo_interno=(item["codigo_interno"] or "")[:60],
            descricao=(item["descricao"] or "")[:300],
            familia=(item["familia"] or "")[:120],
            qtde_final=linha["qtde_final"],
            valor_final=linha["valor_final"],
            custo_medio_final=linha["custo_medio_final"],
            dados=_comprimir(item),
        ))
    db.session.commit()
    limpar_cache()
    current_app.logger.info("Cardex %02d/%s fechado por %s (%s itens)", mes, ano, usuario, len(base["itens"]))
    return fechamento_para_dict(fechamento)


def reabrir_fechamento(fechamento_id: int, usuario: str, motivo: str) -> dict | None:
    fechamento = db.session.get(LogisticaCardexFechamento, fechamento_id)
    if not fechamento:
        return None
    if fechamento.status != "fechado":
        raise ValueError("Este mês já está reaberto.")
    motivo = str(motivo or "").strip()
    if len(motivo) < 5:
        raise ValueError("Informe o motivo da reabertura.")
    posterior = (
        LogisticaCardexFechamento.query
        .filter(LogisticaCardexFechamento.status == "fechado", LogisticaCardexFechamento.data_inicio > fechamento.data_fim)
        .order_by(LogisticaCardexFechamento.data_inicio)
        .first()
    )
    if posterior:
        raise ValueError(
            f"{posterior.mes:02d}/{posterior.ano} está fechado e abriu pelo fechamento deste mês. "
            "Reabra os meses seguintes primeiro, do mais recente para o mais antigo."
        )
    fechamento.itens.delete(synchronize_session=False)
    fechamento.status = "reaberto"
    fechamento.reaberto_em = agora_br()
    fechamento.reaberto_por = usuario
    fechamento.motivo_reabertura = motivo[:500]
    db.session.commit()
    limpar_cache()
    return fechamento_para_dict(fechamento)


def conferir_fechamento(fechamento_id: int) -> dict | None:
    """Recalcula o mês fechado pelo GRV de hoje e mostra o que mudou."""
    fechamento = db.session.get(LogisticaCardexFechamento, fechamento_id)
    if not fechamento:
        return None
    if fechamento.status != "fechado":
        raise ValueError("Só dá para conferir um mês fechado.")
    atual = calcular_ao_vivo(fechamento.data_inicio, fechamento.data_fim, forcar=True)
    saida: dict[str, Any] = {"fechamento": fechamento_para_dict(fechamento), "disponivel": atual["disponivel"], "erro": atual.get("erro")}
    if not atual["disponivel"]:
        return saida
    congelado = {i["codigo"]: resumo(i, None) for i in _itens_do_fechamento(fechamento)}
    recalculado = {i["codigo"]: resumo(i, None) for i in atual["itens"]}
    diferencas = []
    for codigo in sorted(set(congelado) | set(recalculado)):
        antes, depois = congelado.get(codigo), recalculado.get(codigo)
        q_antes = antes["qtde_final"] if antes else 0.0
        q_depois = depois["qtde_final"] if depois else 0.0
        v_antes = antes["valor_final"] if antes else 0.0
        v_depois = depois["valor_final"] if depois else 0.0
        if abs(q_antes - q_depois) <= 0.001 and abs(v_antes - v_depois) <= 0.01:
            continue
        ref = depois or antes
        diferencas.append({
            "codigo": codigo, "codigo_interno": ref["codigo_interno"], "descricao": ref["descricao"],
            "qtde_congelada": q_antes, "qtde_grv_hoje": q_depois,
            "valor_congelado": v_antes, "valor_grv_hoje": v_depois,
            "movimentos_congelados": antes["movimentos"] if antes else 0,
            "movimentos_grv_hoje": depois["movimentos"] if depois else 0,
        })
    saida["diferencas"] = diferencas
    saida["itens_conferidos"] = len(set(congelado) | set(recalculado))
    return saida
