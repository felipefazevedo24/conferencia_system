"""Custo de importacao (Comex > Simulador de custo) - motor de calculo.

Reproduz as planilhas da Controladoria, item a item:
  - "1.MODELO IMPORTACAO _ DESPACHANTE 2025" (DUIMP via despachante)
  - "1a.MODELO IMPORTACAO _ COURIER 2026_revisada" (courier)
com as aliquotas por NCM da "base imposto impo" (ComexNcmAliquota).

Separado da tela de proposito: recebe itens + parametros e devolve o custo
por item e o total, sem tocar em banco nem em request - a provisao de
custo de importacao deve chamar `calcular_despachante`/`calcular_courier`
direto (decisao de 23/10/2026). Compara so' o DESEMBOLSO (sem credito de
impostos).

Regras das planilhas (iguais nas duas, salvo onde indicado):
  CIF   = FOB + frete + seguro (+ outros acrescimos no despachante)
  II    = CIF x II            IPI    = (CIF + II) x IPI
          (courier: II fixo de 60% pra todos os itens, em `ii_courier`,
          e SEM IPI/PIS/COFINS - Controladoria, 28/10/2026)
  PIS   = CIF x PIS           COFINS = CIF x COFINS
  ICMS  "por dentro":
    despachante: (CIF + Siscomex + AFRMM + II + IPI + PIS + COFINS) / (1 - ICMS) x ICMS
    courier:     (CIF + multas + II) / (1 - 17%) x 17% - a
                 planilha usa 17% fixo; a aliquota do item so' liga/desliga
                 (0 = sem ICMS). Mantido assim a pedido (23/10/2026) e
                 parametrizavel em `icms_courier`.
  Rateios: frete pelo PESO no despachante (cai pro FOB se nao houver peso),
  pelo FOB no courier; seguro, acrescimos, despesas de desembaraco e taxas
  do courier pelo FOB; Siscomex e AFRMM pelo CIF.
"""
from __future__ import annotations

import re
from datetime import date, timedelta

import requests

ICMS_COURIER_PADRAO = 0.17
II_COURIER_PADRAO = 0.60  # courier: II fixo, nao o do NCM (Controladoria, 23/10/2026)

# "base imposto impo.xlsx" (23/10/2026), uma linha por NCM (a planilha
# repete NCM por equipamento, sempre com as mesmas aliquotas). O NCM
# 8471414000 veio com 10 digitos - cadastrado como 8471.41.40.
BASE_INICIAL = [
    ("84743100", "Central de Agregados / Misturador", 0.126, 0, 0.021, 0.1045, 0.088, None),
    ("84283990", "Esteiras (transportadores horizontais)", 0.126, 0, 0.021, 0.1045, 0.088, None),
    ("84748090", "Vibroprensa (máquina de prensagem)", 0.126, 0, 0.021, 0.1045, 0.18, 0.088),
    ("84289020", "UL (carregador e descarregador automático)", 0.126, 0, 0.021, 0.1045, 0.18, 0.12),
    ("84289090", "Paletizador (PSC200)", 0.126, 0, 0.021, 0.1045, 0.18, 0.12),
    ("84749000", "Partes e peças", 0.2, 0, 0.021, 0.1025, 0.18, None),
    ("90318012", "Rugosímetros (instrumentos de medida ou controle)", 0.2, 0, 0.021, 0.1025, 0.12, None),
    ("82077010", "Ferramentas de fresar - de topo", 0.162, 0.052, 0.021, 0.0965, 0.18, None),
    ("84714140", "Máquinas automáticas para processamento de dados", 0.16, 0.0975, 0.021, 0.0965, 0.18, None),
]


def normalizar_ncm(ncm) -> str:
    return re.sub(r"\D", "", str(ncm or ""))


def formatar_ncm(ncm) -> str:
    d = normalizar_ncm(ncm)
    return f"{d[:4]}.{d[4:6]}.{d[6:]}" if len(d) == 8 else d


def icms_a_considerar(icms, icms_reduzido) -> float:
    """Regra da planilha: =IF(reduzido="", icms, MIN(icms, reduzido))."""
    if icms_reduzido is None:
        return float(icms or 0)
    return min(float(icms or 0), float(icms_reduzido))


def _num(valor) -> float:
    if valor in (None, ""):
        return 0.0
    if isinstance(valor, str):
        texto = valor.strip().replace(" ", "")
        if "," in texto:
            texto = texto.replace(".", "").replace(",", ".")
        valor = texto
    return float(valor)


def _rateio(total: float, parte: float, base: float) -> float:
    return total * parte / base if base else 0.0


def _impostos(cif: float, aliq: dict) -> dict:
    ii = cif * aliq["ii"]
    return {"ii": ii, "ipi": (cif + ii) * aliq["ipi"], "pis": cif * aliq["pis"], "cofins": cif * aliq["cofins"]}


def _arredondar(d: dict) -> dict:
    return {k: (round(v, 2) if isinstance(v, float) else v) for k, v in d.items()}


def _preparar(itens: list[dict], cambio) -> tuple[list[dict], float]:
    cambio = _num(cambio)
    if cambio <= 0:
        raise ValueError("Informe a taxa de câmbio.")
    if not itens:
        raise ValueError("Informe ao menos um item.")
    prontos = []
    for n, item in enumerate(itens, start=1):
        aliq = item.get("aliquotas") or {}
        faltando = [k for k in ("ii", "ipi", "pis", "cofins", "icms") if aliq.get(k) is None]
        if faltando:
            raise ValueError(f"Item {n} (NCM {formatar_ncm(item.get('ncm')) or '—'}): sem alíquota de {', '.join(faltando).upper()}.")
        valor_usd = _num(item.get("valor_usd"))
        if valor_usd <= 0:
            raise ValueError(f"Item {n}: informe o valor em US$.")
        prontos.append({
            **item,
            "quantidade": _num(item.get("quantidade")) or 1.0,
            "valor_usd": valor_usd,
            "peso": _num(item.get("peso")),
            "aliq": {k: _num(aliq[k]) for k in ("ii", "ipi", "pis", "cofins", "icms")},
            "fob": valor_usd * cambio,
        })
    return prontos, cambio


def _resultado(modalidade: str, linhas: list[dict], avisos: list[str]) -> dict:
    campos = ("fob", "frete", "seguro", "acrescimos", "cif", "ii", "ipi", "pis", "cofins", "icms", "taxas", "despesas", "total")
    totais = {c: round(sum(l[c] for l in linhas), 2) for c in campos}
    totais["impostos"] = round(sum(totais[c] for c in ("ii", "ipi", "pis", "cofins", "icms")), 2)
    return {"modalidade": modalidade, "itens": [_arredondar(l) for l in linhas], "totais": totais, "avisos": avisos}


def calcular_despachante(itens: list[dict], cambio, frete_usd=0, seguro_usd=0, acrescimos_brl=0,
                         taxa_siscomex_brl=0, afrmm_brl=0, despesas_desembaraco_brl=0) -> dict:
    """Modelo despachante (DUIMP). `itens`: [{codigo, descricao, ncm,
    quantidade, valor_usd (total da linha), peso (liquido, kg),
    aliquotas: {ii, ipi, pis, cofins, icms}}] - icms ja' o "a considerar"."""
    prontos, cambio = _preparar(itens, cambio)
    frete, seguro = _num(frete_usd) * cambio, _num(seguro_usd) * cambio
    acrescimos, siscomex, afrmm = _num(acrescimos_brl), _num(taxa_siscomex_brl), _num(afrmm_brl)
    despesas = _num(despesas_desembaraco_brl)
    fob_total = sum(i["fob"] for i in prontos)
    peso_total = sum(i["peso"] for i in prontos)
    avisos = []
    por_peso = peso_total > 0 and all(i["peso"] > 0 for i in prontos)
    if frete and not por_peso:
        avisos.append("Frete rateado pelo valor FOB: informe o peso líquido de todos os itens para ratear pelo peso, como na planilha.")
    linhas = []
    for i in prontos:
        frete_i = _rateio(frete, i["peso"], peso_total) if por_peso else _rateio(frete, i["fob"], fob_total)
        linha = {"fob": i["fob"], "frete": frete_i, "seguro": _rateio(seguro, i["fob"], fob_total),
                 "acrescimos": _rateio(acrescimos, i["fob"], fob_total)}
        linha["cif"] = linha["fob"] + linha["frete"] + linha["seguro"] + linha["acrescimos"]
        linhas.append({**i, **linha})
    cif_total = sum(l["cif"] for l in linhas)
    for l in linhas:
        sisc, afr = _rateio(siscomex, l["cif"], cif_total), _rateio(afrmm, l["cif"], cif_total)
        imp = _impostos(l["cif"], l["aliq"])
        icms = l["aliq"]["icms"]
        base_icms = (l["cif"] + sisc + afr + imp["ii"] + imp["ipi"] + imp["pis"] + imp["cofins"]) / (1 - icms) if icms < 1 else 0
        l.update(imp, icms=base_icms * icms, taxas=sisc + afr, despesas=_rateio(despesas, l["fob"], fob_total))
        l["total"] = l["cif"] + l["ii"] + l["ipi"] + l["pis"] + l["cofins"] + l["icms"] + l["taxas"] + l["despesas"]
        l["unitario"] = l["total"] / l["quantidade"]
        del l["aliq"]
    return _resultado("despachante", linhas, avisos)


def calcular_courier(itens: list[dict], cambio, frete_usd=0, seguro_usd=0, servico_courier_brl=0,
                     tarifa_aeroportuaria_brl=0, multas_brl=0, icms_courier=ICMS_COURIER_PADRAO,
                     ii_courier=II_COURIER_PADRAO) -> dict:
    """Modelo courier. Mesmos `itens` do despachante (peso nao e' usado; o
    II do NCM e' ignorado - vale `ii_courier` - e nao ha' IPI/PIS/COFINS)."""
    prontos, cambio = _preparar(itens, cambio)
    frete, seguro = _num(frete_usd) * cambio, _num(seguro_usd) * cambio
    servico, tarifa, multas = _num(servico_courier_brl), _num(tarifa_aeroportuaria_brl), _num(multas_brl)
    icms_fixo, ii_fixo = _num(icms_courier), _num(ii_courier)
    if not 0 <= icms_fixo < 1:
        raise ValueError("ICMS do courier inválido.")
    if not 0 <= ii_fixo < 10:
        raise ValueError("II do courier inválido.")
    fob_total = sum(i["fob"] for i in prontos)
    linhas = []
    for i in prontos:
        l = {**i, "fob": i["fob"], "frete": _rateio(frete, i["fob"], fob_total),
             "seguro": _rateio(seguro, i["fob"], fob_total), "acrescimos": 0.0}
        l["cif"] = l["fob"] + l["frete"] + l["seguro"]
        multa_i = _rateio(multas, i["fob"], fob_total)
        imp = _impostos(l["cif"], {**i["aliq"], "ii": ii_fixo, "ipi": 0.0, "pis": 0.0, "cofins": 0.0})
        icms = 0.0
        if i["aliq"]["icms"]:
            icms = (l["cif"] + imp["ii"] + imp["ipi"] + imp["pis"] + imp["cofins"] + multa_i) / (1 - icms_fixo) * icms_fixo
        # Servico do courier e tarifa aeroportuaria ficam fora da base do
        # ICMS (vao em "outras despesas acessorias" da NF - nota da planilha).
        l.update(imp, icms=icms, taxas=multa_i,
                 despesas=_rateio(servico, i["fob"], fob_total) + _rateio(tarifa, i["fob"], fob_total))
        l["total"] = l["cif"] + l["ii"] + l["ipi"] + l["pis"] + l["cofins"] + l["icms"] + l["taxas"] + l["despesas"]
        l["unitario"] = l["total"] / l["quantidade"]
        del l["aliq"]
        linhas.append(l)
    return _resultado("courier", linhas, [])


def comparar(itens: list[dict], cambio, despachante: dict, courier: dict) -> dict:
    r_desp = calcular_despachante(itens, cambio, **despachante)
    r_cour = calcular_courier(itens, cambio, **courier)
    t_d, t_c = r_desp["totais"]["total"], r_cour["totais"]["total"]
    return {
        "cambio": _num(cambio), "despachante": r_desp, "courier": r_cour,
        "diferenca": round(abs(t_d - t_c), 2),
        "mais_barato": None if round(t_d - t_c, 2) == 0 else ("courier" if t_c < t_d else "despachante"),
    }


# ── PTAX ────────────────────────────────────────────────────────────────
PTAX_URL = ("https://olinda.bcb.gov.br/olinda/servico/PTAX/versao/v1/odata/"
            "CotacaoDolarDia(dataCotacao=@dataCotacao)")


def ptax_venda(hoje: date | None = None, timeout: int = 8) -> dict | None:
    """PTAX de venda mais recente (volta ate' 7 dias: fim de semana/feriado
    nao tem). Integracao externa nunca derruba a tela: falhou, None e o
    operador digita o cambio."""
    dia = hoje or date.today()
    for _ in range(8):
        try:
            resp = requests.get(PTAX_URL, params={"@dataCotacao": f"'{dia:%m-%d-%Y}'", "$format": "json"}, timeout=timeout)
            resp.raise_for_status()
            valores = resp.json().get("value") or []
        except Exception:
            return None
        if valores:
            return {"cotacao": float(valores[-1]["cotacaoVenda"]), "data": dia.isoformat()}
        dia -= timedelta(days=1)
    return None


# ── Cadastro de NCM (banco) ─────────────────────────────────────────────
# Unica parte do modulo que toca o banco - o motor acima recebe as
# aliquotas prontas.
def _ncm_model():
    from ..models import ComexNcmAliquota
    return ComexNcmAliquota


def garantir_base_inicial(usuario: str = "sistema") -> None:
    """Tabela vazia (primeiro uso): carrega a base da planilha."""
    from ..extensions import db
    Ncm = _ncm_model()
    if Ncm.query.first():
        return
    for ncm, descricao, ii, ipi, pis, cofins, icms, reduzido in BASE_INICIAL:
        db.session.add(Ncm(ncm=ncm, descricao=descricao, ii=ii, ipi=ipi, pis=pis, cofins=cofins,
                           icms=icms, icms_reduzido=reduzido, atualizado_por=usuario))
    db.session.commit()


def ncm_dict(n) -> dict:
    return {
        "id": n.id, "ncm": n.ncm, "ncm_formatado": formatar_ncm(n.ncm), "descricao": n.descricao,
        "ii": n.ii, "ipi": n.ipi, "pis": n.pis, "cofins": n.cofins, "icms": n.icms, "icms_reduzido": n.icms_reduzido,
        "icms_considerar": icms_a_considerar(n.icms, n.icms_reduzido), "observacao": n.observacao,
        "atualizado_em": n.atualizado_em.strftime("%d/%m/%Y %H:%M") if n.atualizado_em else None,
        "atualizado_por": n.atualizado_por,
    }


def listar_ncms() -> list:
    garantir_base_inicial()
    Ncm = _ncm_model()
    return Ncm.query.order_by(Ncm.ncm).all()


def _percentual(dados: dict, campo: str, rotulo: str, obrigatorio: bool = True):
    """A tela manda em % (12,6); grava em fracao (0,126)."""
    valor = dados.get(campo)
    if valor in (None, ""):
        if obrigatorio:
            raise ValueError(f"Informe a alíquota de {rotulo}.")
        return None
    try:
        numero = _num(valor)
    except ValueError:
        raise ValueError(f"Alíquota de {rotulo} inválida.")
    if not 0 <= numero < 100:
        raise ValueError(f"Alíquota de {rotulo} deve estar entre 0 e 100%.")
    return round(numero / 100, 6)


def salvar_ncm(dados: dict, usuario: str, ncm_id: int | None = None):
    from ..extensions import db
    from ..tempo import agora_br
    Ncm = _ncm_model()
    ncm = normalizar_ncm(dados.get("ncm"))
    if len(ncm) != 8:
        raise ValueError("NCM deve ter 8 dígitos (ex.: 8474.31.00).")
    existente = Ncm.query.filter_by(ncm=ncm).first()
    registro = db.session.get(Ncm, ncm_id) if ncm_id else None
    if ncm_id and not registro:
        raise ValueError("NCM não encontrado.")
    if existente and existente is not registro:
        raise ValueError(f"O NCM {formatar_ncm(ncm)} já está cadastrado.")
    registro = registro or Ncm()
    registro.ncm = ncm
    registro.descricao = str(dados.get("descricao") or "").strip()[:300] or None
    registro.ii = _percentual(dados, "ii", "II")
    registro.ipi = _percentual(dados, "ipi", "IPI")
    registro.pis = _percentual(dados, "pis", "PIS")
    registro.cofins = _percentual(dados, "cofins", "COFINS")
    registro.icms = _percentual(dados, "icms", "ICMS")
    registro.icms_reduzido = _percentual(dados, "icms_reduzido", "ICMS base reduzida", obrigatorio=False)
    registro.observacao = str(dados.get("observacao") or "").strip() or None
    registro.atualizado_em, registro.atualizado_por = agora_br(), usuario
    db.session.add(registro)
    db.session.commit()
    return registro


def excluir_ncm(ncm_id: int) -> None:
    from ..extensions import db
    registro = db.session.get(_ncm_model(), ncm_id)
    if not registro:
        raise ValueError("NCM não encontrado.")
    db.session.delete(registro)
    db.session.commit()


def aliquotas_do_cadastro(ncms) -> dict[str, dict]:
    """{ncm (digitos): aliquotas prontas pro motor} - ICMS ja' o a considerar."""
    garantir_base_inicial()
    Ncm = _ncm_model()
    chaves = {normalizar_ncm(n) for n in ncms if normalizar_ncm(n)}
    return {
        n.ncm: {"ii": n.ii, "ipi": n.ipi, "pis": n.pis, "cofins": n.cofins,
                "icms": icms_a_considerar(n.icms, n.icms_reduzido)}
        for n in Ncm.query.filter(Ncm.ncm.in_(chaves)).all()
    } if chaves else {}
