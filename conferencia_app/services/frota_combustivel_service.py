"""Combustível da frota: XMLs do posto, conferência da NF-e semanal e rateio.

Fluxo do posto (validado com XMLs reais de 11/2026): durante a semana cada
abastecimento gera uma NFC-e (modelo 65) com PLACA, KM e OPERADOR nas
informações complementares; na segunda o posto emite uma NF-e (modelo 55,
CFOP 5929) que lista no NFref as chaves de todas as NFC-e que está faturando.
"""
from __future__ import annotations

import json
import re
import statistics
import xml.etree.ElementTree as et
from collections import defaultdict
from datetime import datetime, time, timedelta

from sqlalchemy import func

from ..extensions import db
from ..models import (
    AgendamentoSolicitacao,
    AgendamentoVeiculo,
    FrotaAbastecimento,
    FrotaFaturaCombustivel,
    Viagem,
    ViagemParada,
)
from ..tempo import agora_br

NS = {"nfe": "http://www.portalfiscal.inf.br/nfe"}
DEPARTAMENTO_PADRAO = "LOGÍSTICA"
TAMANHO_MAX_XML = 1_000_000
# Consumo aceito em relação à mediana do próprio veículo. O MED impresso
# pelo posto não serve: sem km anterior ele divide o odômetro inteiro.
FAIXA_CONSUMO = (0.65, 1.35)


# ----------------------------------------------------------------------------- leitura do XML
def _txt(el, caminho: str) -> str:
    if el is None:
        return ""
    achado = el.find(caminho, NS)
    return (achado.text or "").strip() if achado is not None else ""


def _float(valor) -> float:
    try:
        return float(str(valor).replace(",", "."))
    except (TypeError, ValueError):
        return 0.0


def _data_emissao(valor: str) -> datetime | None:
    # dhEmi vem com fuso (-03:00); o sistema grava hora de Brasília sem fuso.
    try:
        return datetime.fromisoformat(valor).replace(tzinfo=None)
    except ValueError:
        return None


def normalizar_placa(placa) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(placa or "").upper())


def numero_da_chave(chave: str) -> str:
    return str(int(chave[25:34])) if len(chave) == 44 else chave


def serie_da_chave(chave: str) -> str:
    return str(int(chave[22:25])) if len(chave) == 44 else ""


def ler_xml(conteudo: bytes) -> dict:
    """Lê NFC-e ou NF-e. Levanta ValueError com mensagem para o usuário."""
    try:
        root = et.fromstring(conteudo)
    except et.ParseError:
        raise ValueError("Arquivo não é um XML válido.")
    inf = root.find(".//nfe:infNFe", NS)
    ide = inf.find("nfe:ide", NS) if inf is not None else None
    if inf is None or ide is None:
        raise ValueError("XML não é de nota fiscal eletrônica.")
    chave = str(inf.get("Id") or "").replace("NFe", "").strip()
    if not re.fullmatch(r"\d{44}", chave):
        raise ValueError("Chave de acesso não encontrada no XML.")
    cstat = _txt(root, ".//nfe:protNFe/nfe:infProt/nfe:cStat")
    if cstat and cstat != "100":
        raise ValueError(f"Nota {numero_da_chave(chave)} não está autorizada na SEFAZ (cStat {cstat}).")

    itens = []
    for det in inf.findall("nfe:det", NS):
        prod = det.find("nfe:prod", NS)
        itens.append({
            "descricao": _txt(prod, "nfe:xProd"),
            "anp": _txt(prod, "nfe:comb/nfe:descANP"),
            "combustivel": prod is not None and prod.find("nfe:comb", NS) is not None,
            "litros": _float(_txt(prod, "nfe:qCom")),
            "valor_unitario": _float(_txt(prod, "nfe:vUnCom")),
        })

    info = _txt(inf, "nfe:infAdic/nfe:infCpl")
    placa = re.search(r"PLACA:\s*([A-Za-z0-9-]{7,8})", info)
    # "KM:" aparece de novo depois ("Saldo KM:|4525", programa de pontos);
    # o primeiro é o odômetro informado no abastecimento.
    km = re.search(r"\bKM:\s*(\d+)", info)
    operador = re.search(r"OPERADOR:\s*([^|]+)", info)
    emit = inf.find("nfe:emit", NS)
    return {
        "chave": chave,
        "modelo": _txt(ide, "nfe:mod"),
        "numero": _txt(ide, "nfe:nNF") or numero_da_chave(chave),
        "serie": _txt(ide, "nfe:serie"),
        "emitida_em": _data_emissao(_txt(ide, "nfe:dhEmi")),
        "cnpj_emitente": _txt(emit, "nfe:CNPJ"),
        "emitente_nome": _txt(emit, "nfe:xFant") or _txt(emit, "nfe:xNome"),
        "valor_total": _float(_txt(inf, "nfe:total/nfe:ICMSTot/nfe:vNF")),
        "itens": itens,
        "referencias": [r.text.strip() for r in ide.findall("nfe:NFref/nfe:refNFe", NS) if r.text],
        "placa": normalizar_placa(placa.group(1)) if placa else "",
        "km": int(km.group(1)) if km else None,
        "operador": operador.group(1).strip() if operador else "",
    }


def _tipo_combustivel(item: dict) -> str:
    texto = f"{item['anp']} {item['descricao']}".upper()
    for chave, nome in (("DIESEL", "Diesel"), ("ETANOL", "Etanol"), ("GASOLINA", "Gasolina")):
        if chave in texto:
            return nome
    return (item["descricao"] or "Outro")[:20]


def _veiculo_por_placa(placa: str) -> AgendamentoVeiculo | None:
    for veiculo in AgendamentoVeiculo.query.all():
        if normalizar_placa(veiculo.placa) == placa:
            return veiculo
    return None


def _fmt_moeda(valor: float) -> str:
    return f"R$ {valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


# ----------------------------------------------------------------------------- importação
def _importar_nfce(d: dict, conteudo: bytes, usuario: str) -> str:
    numero = d["numero"]
    if FrotaAbastecimento.query.filter_by(chave_nfce=d["chave"]).first():
        raise ValueError(f"NFC-e {numero} já foi lançada.")
    combustiveis = [i for i in d["itens"] if i["combustivel"]]
    if not combustiveis:
        raise ValueError(f"NFC-e {numero} não tem item de combustível.")
    if not d["placa"]:
        raise ValueError(f"NFC-e {numero} sem placa nas informações complementares.")
    veiculo = _veiculo_por_placa(d["placa"])
    if not veiculo:
        raise ValueError(f"Placa {d['placa']} (NFC-e {numero}) não está cadastrada em nenhum veículo.")
    if d["km"] is None:
        raise ValueError(f"NFC-e {numero} sem KM nas informações complementares.")
    litros = round(sum(i["litros"] for i in combustiveis), 3)
    db.session.add(FrotaAbastecimento(
        veiculo_id=veiculo.id,
        data=d["emitida_em"] or agora_br(),
        km_atual=d["km"],
        litros=litros,
        valor_litro=combustiveis[0]["valor_unitario"],
        valor_total=d["valor_total"],
        combustivel=_tipo_combustivel(combustiveis[0]),
        posto=d["emitente_nome"][:160] or None,
        tanque_cheio=True,
        criado_por=usuario,
        chave_nfce=d["chave"],
        numero_nfce=numero[:20],
        placa_nota=d["placa"][:12],
        operador=d["operador"][:120] or None,
        xml_nfce=conteudo,
    ))
    return f"NFC-e {numero} · {d['placa']} · {_fmt_moeda(d['valor_total'])}"


def _importar_fatura(d: dict, conteudo: bytes, usuario: str) -> str:
    numero = d["numero"]
    if FrotaFaturaCombustivel.query.filter_by(chave=d["chave"]).first():
        raise ValueError(f"NF-e {numero} já foi importada.")
    if not d["emitida_em"]:
        raise ValueError(f"NF-e {numero} sem data de emissão.")
    litros = round(sum(i["litros"] for i in d["itens"] if i["combustivel"]), 3)
    db.session.add(FrotaFaturaCombustivel(
        chave=d["chave"],
        numero=numero[:20],
        serie=d["serie"][:5],
        emitida_em=d["emitida_em"],
        cnpj_emitente=d["cnpj_emitente"][:14],
        emitente_nome=d["emitente_nome"][:160],
        valor_total=d["valor_total"],
        litros_total=litros,
        chaves_referenciadas=json.dumps(d["referencias"]),
        xml=conteudo,
        criado_por=usuario,
    ))
    msg = f"NF-e {numero} · {len(d['referencias'])} NFC-e · {_fmt_moeda(d['valor_total'])}"
    if not d["referencias"]:
        msg += " · sem NFC-e referenciadas, não dá para conferir"
    return msg


def importar_xmls(arquivos: list[tuple[str, bytes]], usuario: str) -> list[dict]:
    """Importa NFC-e e NF-e misturadas; cada arquivo dá certo ou errado sozinho."""
    resultados = []
    vistos: set[str] = set()
    for nome, conteudo in arquivos:
        try:
            if len(conteudo) > TAMANHO_MAX_XML:
                raise ValueError("Arquivo grande demais para ser um XML de nota.")
            d = ler_xml(conteudo)
            if d["chave"] in vistos:
                raise ValueError("Arquivo repetido neste envio.")
            if d["modelo"] == "65":
                msg = _importar_nfce(d, conteudo, usuario)
            elif d["modelo"] == "55":
                msg = _importar_fatura(d, conteudo, usuario)
            else:
                raise ValueError(f"Modelo de nota {d['modelo'] or '?'} não suportado.")
            vistos.add(d["chave"])
            resultados.append({"arquivo": nome, "ok": True, "msg": msg})
        except ValueError as exc:
            resultados.append({"arquivo": nome, "ok": False, "msg": str(exc)})
    db.session.commit()
    return resultados


# ----------------------------------------------------------------------------- conferência
def chaves_faturadas() -> dict[str, FrotaFaturaCombustivel]:
    mapa = {}
    for fatura in FrotaFaturaCombustivel.query.all():
        for chave in json.loads(fatura.chaves_referenciadas or "[]"):
            mapa[chave] = fatura
    return mapa


def _veiculos() -> dict[int, AgendamentoVeiculo]:
    return {v.id: v for v in AgendamentoVeiculo.query.all()}


def _veiculo_label(v: AgendamentoVeiculo | None) -> str:
    if not v:
        return "—"
    return f"{v.nome_exibicao}" + (f" ({v.placa})" if v.placa else "")


def conferir_fatura(fatura: FrotaFaturaCombustivel) -> dict:
    refs = json.loads(fatura.chaves_referenciadas or "[]")
    lancadas = (
        {a.chave_nfce: a for a in FrotaAbastecimento.query.filter(FrotaAbastecimento.chave_nfce.in_(refs)).all()}
        if refs else {}
    )
    veiculos = _veiculos()
    notas = []
    for chave in refs:
        a = lancadas.get(chave)
        notas.append({
            "chave": chave,
            "numero": numero_da_chave(chave),
            "serie": serie_da_chave(chave),
            "lancada": a is not None,
            "abastecimento_id": a.id if a else None,
            "veiculo_id": a.veiculo_id if a else None,
            "veiculo_label": _veiculo_label(veiculos.get(a.veiculo_id)) if a else None,
            "data": a.data.isoformat() if a and a.data else None,
            "litros": a.litros if a else None,
            "valor": a.valor_total if a else None,
        })
    soma = round(sum(a.valor_total or 0 for a in lancadas.values()), 2)
    diferenca = round((fatura.valor_total or 0) - soma, 2)
    faltando = sum(1 for n in notas if not n["lancada"])
    return {
        "notas": notas,
        "soma_nfce": soma,
        "diferenca": diferenca,
        "faltando": faltando,
        "status": "Conferida" if refs and not faltando and abs(diferenca) < 0.01 else "Divergente",
    }


def fatura_dict(fatura: FrotaFaturaCombustivel, conferencia: dict | None = None) -> dict:
    conferencia = conferencia or conferir_fatura(fatura)
    return {
        "id": fatura.id,
        "chave": fatura.chave,
        "numero": fatura.numero,
        "serie": fatura.serie,
        "emitida_em": fatura.emitida_em.isoformat() if fatura.emitida_em else None,
        "emitente_nome": fatura.emitente_nome,
        "valor_total": fatura.valor_total,
        "litros_total": fatura.litros_total,
        "qtd_nfce": len(conferencia["notas"]),
        "qtd_lancadas": len(conferencia["notas"]) - conferencia["faltando"],
        "soma_nfce": conferencia["soma_nfce"],
        "diferenca": conferencia["diferenca"],
        "faltando": conferencia["faltando"],
        "status": conferencia["status"],
    }


def alertas_abastecimentos() -> dict[int, dict]:
    """km/l de cada abastecimento (método do tanque cheio) e o que estiver fora do padrão."""
    por_veiculo: dict[int, list[FrotaAbastecimento]] = defaultdict(list)
    for a in FrotaAbastecimento.query.order_by(FrotaAbastecimento.data, FrotaAbastecimento.id).all():
        por_veiculo[a.veiculo_id].append(a)
    saida: dict[int, dict] = {}
    for regs in por_veiculo.values():
        historico: list[float] = []
        anterior = None
        for a in regs:
            info = {"kml": None, "alertas": []}
            if anterior is not None and a.km_atual is not None and anterior.km_atual is not None:
                delta = a.km_atual - anterior.km_atual
                if delta <= 0:
                    info["alertas"].append(
                        f"KM {a.km_atual} não é maior que o do abastecimento anterior ({anterior.km_atual})."
                    )
                elif (a.litros or 0) > 0:
                    kml = delta / a.litros
                    info["kml"] = round(kml, 2)
                    if len(historico) >= 3:
                        mediana = statistics.median(historico)
                        if not (FAIXA_CONSUMO[0] * mediana <= kml <= FAIXA_CONSUMO[1] * mediana):
                            info["alertas"].append(
                                f"Consumo de {kml:.1f} km/l fora do padrão do veículo ({mediana:.1f} km/l).".replace(".", ",")
                            )
                    historico.append(kml)
            saida[a.id] = info
            anterior = a
    return saida


# ----------------------------------------------------------------------------- rateio
def periodo_da_fatura(fatura: FrotaFaturaCombustivel) -> tuple[datetime, datetime]:
    """Os 7 dias antes da emissão: a NF-e de segunda fatura a semana anterior."""
    fim = datetime.combine(fatura.emitida_em.date(), time.min)
    return fim - timedelta(days=7), fim


def _departamento(sol: AgendamentoSolicitacao | None) -> str:
    dep = str(getattr(sol, "departamento_solicitante", "") or "").strip().upper()
    if not dep or dep == "LOGISTICA":
        return DEPARTAMENTO_PADRAO
    return dep


def km_por_departamento(veiculo_id: int, inicio: datetime, fim: datetime) -> dict[str, float]:
    """Km das viagens concluídas do veículo no período, dividido entre os
    departamentos das paradas. Viagem sem solicitação vai para Logística."""
    saida = func.coalesce(Viagem.saida_real, Viagem.saida_prevista)
    viagens = Viagem.query.filter(
        Viagem.veiculo_id == veiculo_id,
        Viagem.status == "Concluida",
        saida >= inicio,
        saida < fim,
    ).all()
    km_dep: dict[str, float] = defaultdict(float)
    for v in viagens:
        km = float(v.km_percorrido or 0)
        if km <= 0:
            continue
        paradas = ViagemParada.query.filter(
            ViagemParada.viagem_id == v.id,
            ViagemParada.solicitacao_id.isnot(None),
            ViagemParada.status != "Cancelada",
        ).all()
        if not paradas:
            km_dep[DEPARTAMENTO_PADRAO] += km
            continue
        sols = {
            s.id: s
            for s in AgendamentoSolicitacao.query.filter(
                AgendamentoSolicitacao.id.in_({p.solicitacao_id for p in paradas})
            ).all()
        }
        parte = km / len(paradas)
        for p in paradas:
            km_dep[_departamento(sols.get(p.solicitacao_id))] += parte
    return dict(km_dep)


def ratear_fatura(fatura: FrotaFaturaCombustivel, conferencia: dict | None = None) -> dict:
    conferencia = conferencia or conferir_fatura(fatura)
    inicio, fim = periodo_da_fatura(fatura)
    veiculos = _veiculos()

    custo_veiculo: dict[int, float] = defaultdict(float)
    for nota in conferencia["notas"]:
        if nota["lancada"]:
            custo_veiculo[nota["veiculo_id"]] += nota["valor"] or 0

    dep_valor: dict[str, float] = defaultdict(float)
    dep_km: dict[str, float] = defaultdict(float)
    por_veiculo = []
    avisos = []
    for vid, custo in custo_veiculo.items():
        km_dep = km_por_departamento(vid, inicio, fim)
        total_km = sum(km_dep.values())
        label = _veiculo_label(veiculos.get(vid))
        if total_km <= 0:
            dep_valor[DEPARTAMENTO_PADRAO] += custo
            avisos.append(f"{label} sem viagem concluída com km na semana: custo vai para {DEPARTAMENTO_PADRAO}.")
        else:
            for dep, km in km_dep.items():
                dep_valor[dep] += custo * km / total_km
                dep_km[dep] += km
        por_veiculo.append({
            "veiculo_label": label,
            "custo": round(custo, 2),
            "km": round(total_km, 1),
            "custo_km": round(custo / total_km, 2) if total_km > 0 else None,
        })

    total = round(sum(custo_veiculo.values()), 2)
    departamentos = [
        {"departamento": dep, "km": round(dep_km.get(dep, 0), 1), "valor": round(valor, 2)}
        for dep, valor in dep_valor.items()
    ]
    departamentos.sort(key=lambda x: -x["valor"])
    # Arredondar cada parte pode deixar centavos sobrando: vão para a maior,
    # para o rateio fechar exatamente com o valor das NFC-e.
    if departamentos:
        departamentos[0]["valor"] = round(departamentos[0]["valor"] + total - sum(d["valor"] for d in departamentos), 2)
    for d in departamentos:
        d["pct"] = round(100 * d["valor"] / total, 1) if total else 0

    if conferencia["faltando"]:
        avisos.insert(0, (
            f"Faltam {conferencia['faltando']} NFC-e: {_fmt_moeda(conferencia['diferenca'])} "
            "da NF-e ainda não entra no rateio."
        ))
    return {
        "periodo": {"inicio": inicio.isoformat(), "fim": (fim - timedelta(days=1)).date().isoformat()},
        "total_rateado": total,
        "departamentos": departamentos,
        "veiculos": por_veiculo,
        "avisos": avisos,
    }
