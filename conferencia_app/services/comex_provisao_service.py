"""Comex > provisao de custo da importacao e gestao financeira (provisao x
realizado). Decisoes do usuario: memoria comex-provisao-impostos-spec.

Provisao (na instrucao de embarque, so' importacao, cotacao escolhida):
  - impostos pelo motor do simulador (comex_custo_service), Despachante e
    Courier lado a lado - o operador escolhe qual vira a provisao;
  - itens = POs do embarque (principal + incluidas); NCM sem aliquota
    cadastrada bloqueia;
  - base CIF pelo Incoterm (CUSTOS_CIF_POR_INCOTERM); Destination, Docs
    release e Delivery ficam fora da base;
  - impostos na PTAX pura (taxa fiscal); frete provisionado = cotacao
    escolhida em R$ com o markup do forwarder;
  - grava retrato com versao; recalcular cria versao nova.
Realizado: impostos federais lidos da DUIMP/DIR (comex_di_parser); ICMS,
fatura do frete e despesas digitados.
"""
from __future__ import annotations

import json

from ..extensions import db
from ..models import ComexCotacao, ComexProcesso, ComexProvisao, ComexRealizado
from ..tempo import agora_br
from . import comex_custo_service as custo
from . import comex_embarque_service as embarque
from . import comex_service as svc

INCOTERMS = ("EXW", "FCA", "FAS", "FOB", "CFR", "CIF", "CPT", "CIP", "DAP", "DPU", "DDP")
EMAIL_CONTROLADORIA = "lucmor@colmac.com"
_ATE_O_DESTINO = ("pick_up", "origem_charges", "frete_internacional", "seguro")
# Quem paga ate' o embarque e' o fornecedor (FCA/FAS/FOB): so' frete e seguro
# internacionais entram. EXW: a coleta e a origem tambem. Termos C/D: o
# fornecedor ja' pagou o frete - entra o que a cotacao trouxer.
CUSTOS_CIF_POR_INCOTERM = {
    "EXW": _ATE_O_DESTINO, "FCA": ("frete_internacional", "seguro"), "FAS": ("frete_internacional", "seguro"),
    "FOB": ("frete_internacional", "seguro"),
    **{t: _ATE_O_DESTINO for t in ("CFR", "CIF", "CPT", "CIP", "DAP", "DPU", "DDP")},
}
CAMPOS_REALIZADO_NUM = ("cambio", "ii", "ipi", "pis", "cofins", "taxa_siscomex", "afrmm", "cbs_ibs", "multas", "icms",
                        "frete_fatura_brl", "despesa_despachante", "despesa_armazenagem", "despesa_capatazia", "despesa_outras")


def _cotacao_escolhida(processo: ComexProcesso) -> ComexCotacao | None:
    return db.session.get(ComexCotacao, processo.cotacao_vencedora_id) if processo.cotacao_vencedora_id else None


def motivo_sem_provisao(processo: ComexProcesso) -> str | None:
    if processo.direcao_operacao == "EXPO" or svc.exportacao_da_expedicao(processo):
        return "Provisão de impostos é só para importação."
    if processo.embarque_principal:
        return f"Esta PO está no embarque de {processo.embarque_principal.principal.id_op} - provisione por lá."
    if not processo.cotacao_vencedora_id:
        return "Escolha a cotação de frete antes de provisionar."
    return None


def _itens(processo: ComexProcesso) -> list[dict]:
    itens = [{"codigo": it.codigo, "descricao": it.descricao, "ncm": it.ncm, "quantidade": it.quantidade,
              "valor_usd": it.valor_total, "po": p.po_numero}
             for p in embarque.processos_do_embarque(processo) for it in svc.listar_itens_po(p)]
    if not itens:
        raise ValueError("A PO não tem itens lançados - lance os itens (NCM e valor) antes de provisionar.")
    aliquotas = custo.aliquotas_do_cadastro(i["ncm"] for i in itens)
    faltam = sorted({custo.formatar_ncm(i["ncm"]) or "(sem NCM)" for i in itens if custo.normalizar_ncm(i["ncm"]) not in aliquotas})
    if faltam:
        raise ValueError(f"NCM sem alíquota cadastrada: {', '.join(faltam)}. Cadastre no Simulador de custo > NCMs.")
    for i in itens:
        i["aliquotas"] = aliquotas[custo.normalizar_ncm(i["ncm"])]
    return itens


def _frete_cif(cotacao: ComexCotacao, incoterm: str, ptax: float) -> tuple[float, float]:
    """(frete_usd, seguro_usd) que entram na base - parte em R$ da cotacao
    convertida pela PTAX."""
    def usd(etapa):
        return float(getattr(cotacao, f"{etapa}_usd") or 0) + float(getattr(cotacao, f"{etapa}_brl") or 0) / ptax
    etapas = CUSTOS_CIF_POR_INCOTERM[incoterm]
    return round(sum(usd(e) for e in etapas if e != "seguro"), 2), round(usd("seguro") if "seguro" in etapas else 0, 2)


def calcular(processo: ComexProcesso, dados: dict) -> dict:
    motivo = motivo_sem_provisao(processo)
    if motivo:
        raise ValueError(motivo)
    incoterm = str(dados.get("incoterm") or "").strip().upper()
    if incoterm not in INCOTERMS:
        raise ValueError("Escolha o Incoterm.")
    ptax = custo._num(dados.get("ptax"))
    if not ptax or ptax <= 0:
        raise ValueError("Informe a PTAX.")
    cotacao = _cotacao_escolhida(processo)
    itens = _itens(processo)
    frete_usd, seguro_usd = _frete_cif(cotacao, incoterm, ptax)
    desp = {c: dados.get(c) for c in ("acrescimos_brl", "taxa_siscomex_brl", "afrmm_brl", "despesas_desembaraco_brl")}
    cour = {c: dados.get(c) for c in ("servico_courier_brl", "tarifa_aeroportuaria_brl", "multas_brl")}
    resultado = custo.comparar(itens, ptax, despachante={"frete_usd": frete_usd, "seguro_usd": seguro_usd, **desp},
                               courier={"frete_usd": frete_usd, "seguro_usd": seguro_usd, **cour})
    # Rateio do frete pelo FOB e' a regra da provisao (a PO nao tem peso) - o
    # aviso do simulador pedindo peso nao se aplica aqui.
    for modelo in ("despachante", "courier"):
        resultado[modelo]["avisos"] = [a for a in resultado[modelo]["avisos"] if not a.startswith("Frete rateado pelo valor FOB")]
    cambio = svc.cambio_da_cotacao(cotacao, ptax)
    resultado.update({
        "incoterm": incoterm, "frete_cif_usd": frete_usd, "seguro_cif_usd": seguro_usd,
        "etapas_na_base": list(CUSTOS_CIF_POR_INCOTERM[incoterm]),
        "frete_brl": cambio["custo_total_consolidado_brl"], "taxa_agente": cambio["taxa_agente"],
        "markup_pct": cambio["markup_pct"], "forwarder": cotacao.fornecedor_frete,
        "pos": [p.po_numero for p in embarque.processos_do_embarque(processo)],
    })
    return resultado


def _impostos(totais: dict) -> float:
    return round(totais["impostos"] + totais["taxas"], 2)


def gravar(processo: ComexProcesso, dados: dict, usuario: str) -> ComexProvisao:
    modelo = str(dados.get("modelo") or "")
    if modelo not in ("despachante", "courier"):
        raise ValueError("Escolha o modelo da provisão: Despachante ou Courier.")
    resultado = calcular(processo, dados)
    totais = resultado[modelo]["totais"]
    versao = (processo.provisoes[-1].versao + 1) if processo.provisoes else 1
    provisao = ComexProvisao(
        processo=processo, versao=versao, modelo=modelo, incoterm=resultado["incoterm"], ptax=custo._num(dados["ptax"]),
        impostos_brl=_impostos(totais), frete_brl=resultado["frete_brl"], despesas_brl=totais["despesas"],
        parametros=json.dumps({k: v for k, v in dados.items() if k != "modelo"}, ensure_ascii=False, default=str),
        resultado=json.dumps(resultado, ensure_ascii=False, default=str), criado_em=agora_br(), criado_por=usuario,
    )
    db.session.add(provisao)
    db.session.commit()
    return provisao


def vigente(processo: ComexProcesso) -> ComexProvisao | None:
    return processo.provisoes[-1] if processo.provisoes else None


def sem_provisao(processo: ComexProcesso) -> bool:
    """Marca "Sem provisao" na lista: importacao que ja' passou da cotacao
    (instrucao em diante) e nao tem provisao. Opcional, so' aviso."""
    return (motivo_sem_provisao(processo) is None and not processo.provisoes
            and svc._indice_modulo(processo.status_modulo) >= svc._indice_modulo("Instrucao"))


# ── Realizado ──────────────────────────────────────────────────────────
def salvar_realizado(processo: ComexProcesso, dados: dict, arquivos: dict, usuario: str) -> ComexRealizado:
    """`arquivos`: {documento|icms_guia|frete_fatura|despesas: (nome, bytes)}.
    O documento (DUIMP/DIR) e' lido e preenche os impostos federais; o que
    o operador digitar por cima prevalece."""
    from .comex_di_parser import ler_documento
    if processo.direcao_operacao == "EXPO":
        raise ValueError("Realizado de impostos é só para importação.")
    r = processo.realizado or ComexRealizado(processo=processo)
    lidos = {}
    if arquivos.get("documento"):
        nome, conteudo = arquivos["documento"]
        lidos = ler_documento(conteudo)
        r.documento_nome, r.documento_arquivo = nome[:255], conteudo
        r.documento_tipo, r.documento_numero = lidos["documento_tipo"], (lidos["documento_numero"] or "")[:40] or None
        r.referencia_importador = (lidos.get("referencia_importador") or "")[:80] or None
        for campo in ("cambio", "ii", "ipi", "pis", "cofins", "taxa_siscomex", "afrmm", "cbs_ibs", "multas"):
            setattr(r, campo, lidos.get(campo))
    for campo in CAMPOS_REALIZADO_NUM:
        if campo in dados and campo not in lidos:
            valor = custo._num(dados.get(campo)) if dados.get(campo) not in (None, "") else None
            if valor is not None and valor < 0:
                raise ValueError("Valores do realizado não podem ser negativos.")
            setattr(r, campo, valor)
    for chave, (attr_nome, attr_arq) in {"icms_guia": ("icms_guia_nome", "icms_guia_arquivo"),
                                         "frete_fatura": ("frete_fatura_nome", "frete_fatura_arquivo"),
                                         "despesas": ("despesas_nome", "despesas_arquivo")}.items():
        if arquivos.get(chave):
            setattr(r, attr_nome, arquivos[chave][0][:255])
            setattr(r, attr_arq, arquivos[chave][1])
    r.atualizado_em, r.atualizado_por = agora_br(), usuario
    db.session.add(r)
    db.session.commit()
    return r


def totais_realizado(r: ComexRealizado | None) -> dict:
    if not r:
        return {"impostos": None, "frete": None, "despesas": None}
    soma = lambda campos: round(sum(getattr(r, c) or 0 for c in campos), 2) if any(getattr(r, c) is not None for c in campos) else None
    return {
        "impostos": soma(("ii", "ipi", "pis", "cofins", "icms", "taxa_siscomex", "afrmm", "cbs_ibs", "multas")),
        "frete": r.frete_fatura_brl,
        "despesas": soma(("despesa_despachante", "despesa_armazenagem", "despesa_capatazia", "despesa_outras")),
    }


# ── Gestao financeira ──────────────────────────────────────────────────
def processos_da_gestao() -> list[ComexProcesso]:
    """Importacoes do embarque principal, da Instrucao em diante ou ja'
    provisionadas."""
    indice_instrucao = svc._indice_modulo("Instrucao")
    processos = (ComexProcesso.query
                 .filter(ComexProcesso.po_processo_principal_id.is_(None), svc._fora_de_embarque(),
                         ComexProcesso.status_modulo != svc.STATUS_CANCELADO)
                 .all())
    return [p for p in processos
            if motivo_sem_provisao(p) in (None, "Escolha a cotação de frete antes de provisionar.")
            and (p.provisoes or svc._indice_modulo(p.status_modulo) >= indice_instrucao)]


def linha_gestao(p: ComexProcesso) -> dict:
    prov = vigente(p)
    real = totais_realizado(p.realizado)
    previsto = {"impostos": prov.impostos_brl if prov else None, "frete": prov.frete_brl if prov else None,
                "despesas": prov.despesas_brl if prov else None}
    situacao = "Realizado" if p.realizado else ("Provisionado" if prov else "Sem provisão")
    return {
        "id": p.id, "id_op": p.id_op, "fornecedor": p.fornecedor, "status_modulo": svc.LABELS_MODULO.get(p.status_modulo, p.status_modulo),
        "eta": p.em_transito_eta.isoformat() if p.em_transito_eta else None,
        "mes": p.em_transito_eta.strftime("%Y-%m") if p.em_transito_eta else None,
        "situacao": situacao, "provisao": previsto, "realizado": real,
        "provisao_versao": prov.versao if prov else None, "provisao_modelo": prov.modelo if prov else None,
        "pos": [x.po_numero for x in embarque.processos_do_embarque(p) if x.po_numero],
    }
