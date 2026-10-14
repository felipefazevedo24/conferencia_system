"""Conferido x faturado: compara o que o conferente contou com o que saiu na
nota fiscal, item a item (código e quantidade). Pedido da logística em
13/10/2026.

É diferente do `divergente` da conferência, que compara a contagem com a
quantidade PREVISTA na ordem de faturamento. Aqui o outro lado é a NF de
verdade (XML autorizado, lido no ERP): pega nota emitida com quantidade ou
item diferente do que foi conferido.

Só existe comparação depois da conferência oficial (`conferido_at`): antes
disso devolver a quantidade da nota quebraria a conferência cega. Ordem
expedida sem conferência também fica de fora - a "contagem" dela é cópia do
previsto, não foi ninguém que contou.
"""
from __future__ import annotations

import json
import re
from datetime import timedelta
from typing import Any

from flask import current_app

from ..extensions import db
from ..models import ExpedicaoFatComparacaoNF, ExpedicaoOrdemFat
from ..tempo import agora_br
from . import danfe_service
from .erp_nfe_emitidas_service import buscar_nfe_emitida_erp
from .expedicao_fat_service import _nfs_da_string

# NF que o ERP não devolveu (bridge fora, XML ainda não autorizado): nova
# tentativa só depois disso, pra não gastar toda rodada nas mesmas ordens.
HORAS_NOVA_TENTATIVA = 6
TIPOS = {
    "quantidade": "Quantidade diferente",
    "nao_faturado": "Conferido, mas não está na NF",
    "nao_conferido": "Está na NF, mas não foi conferido",
}


def _codigo(valor: Any) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(valor or "").upper())


def _numero(valor: Any) -> float:
    try:
        return float(str(valor or "0").replace(",", "."))
    except (TypeError, ValueError):
        return 0.0


def comparar_itens(conferidos: list[dict[str, Any]], faturados: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """`conferidos` e `faturados`: [{cod_interno, item, quantidade}]. O mesmo
    código em mais de uma linha é somado dos dois lados (a NF pode juntar ou
    separar linhas da ordem). Devolve só as divergências."""
    def agrupar(linhas):
        grupos: dict[str, dict[str, Any]] = {}
        for linha in linhas:
            chave = _codigo(linha.get("cod_interno"))
            if not chave:
                continue
            grupo = grupos.setdefault(chave, {
                "cod_interno": str(linha.get("cod_interno") or "").strip(),
                "item": str(linha.get("item") or "").strip(),
                "quantidade": 0.0,
            })
            grupo["quantidade"] += _numero(linha.get("quantidade"))
        return grupos

    lado_conf = agrupar(conferidos)
    lado_nf = agrupar(faturados)
    divergencias = []
    for chave in sorted(set(lado_conf) | set(lado_nf)):
        conf = lado_conf.get(chave)
        nf = lado_nf.get(chave)
        qtde_conf = conf["quantidade"] if conf else 0.0
        qtde_nf = nf["quantidade"] if nf else 0.0
        if abs(qtde_conf - qtde_nf) < 1e-6:
            continue
        tipo = "quantidade" if conf and nf else ("nao_faturado" if conf else "nao_conferido")
        base = conf or nf
        divergencias.append({
            "cod_interno": base["cod_interno"],
            "item": (conf or {}).get("item") or (nf or {}).get("item") or "",
            "conferido": qtde_conf if conf else None,
            "faturado": qtde_nf if nf else None,
            "tipo": tipo,
            "tipo_label": TIPOS[tipo],
        })
    return divergencias


def _comparavel(ordem: ExpedicaoOrdemFat) -> str:
    """'' se dá pra comparar; senão o motivo."""
    if not _nfs_da_string(ordem.numero_nf):
        return "sem_nf"
    if not ordem.conferido_at or ordem.expedido_sem_conferencia or any(it.qtde_conferida is None for it in ordem.itens):
        return "sem_conferencia"
    return ""


def _valida(linha: ExpedicaoFatComparacaoNF | None, ordem: ExpedicaoOrdemFat) -> bool:
    return bool(
        linha
        and (linha.numero_nf or "") == (ordem.numero_nf or "")
        and linha.conferido_at == ordem.conferido_at
    )


def _itens_das_nfs(numero_nf: str) -> list[dict[str, Any]]:
    """Itens de todas as NFs da ordem. Levanta exceção se alguma não puder ser lida."""
    itens = []
    for numero in _nfs_da_string(numero_nf):
        nota = buscar_nfe_emitida_erp(numero_nf=numero)
        xml_bytes = (nota or {}).get("xml_bytes")
        if not xml_bytes:
            raise ValueError(f"NF {numero} não encontrada no ERP (ou ainda sem XML autorizado).")
        for item in danfe_service.parse_nfe_xml(xml_bytes).get("itens") or []:
            itens.append({"cod_interno": item.get("cProd"), "item": item.get("xProd"), "quantidade": item.get("qCom")})
    return itens


def _resposta(linha: ExpedicaoFatComparacaoNF) -> dict[str, Any]:
    if linha.erro:
        return {"status": "indisponivel", "divergente": False, "divergencias": [], "numero_nf": linha.numero_nf, "erro": linha.erro}
    try:
        divergencias = json.loads(linha.detalhes) if linha.detalhes else []
    except (ValueError, TypeError):
        divergencias = []
    return {
        "status": "ok",
        "divergente": bool(linha.divergente),
        "divergencias": divergencias,
        "numero_nf": linha.numero_nf,
        "verificado_em": linha.verificado_em.isoformat() if linha.verificado_em else None,
    }


def verificar(ordem: ExpedicaoOrdemFat, forcar: bool = False) -> dict[str, Any]:
    """Comparação da ordem, refeita só se a guardada não vale mais. Nunca
    levanta exceção: ERP fora devolve status 'indisponivel'."""
    motivo = _comparavel(ordem)
    if motivo:
        return {"status": motivo, "divergente": False, "divergencias": [], "numero_nf": ordem.numero_nf}
    linha = ExpedicaoFatComparacaoNF.query.filter_by(ordem_id=ordem.id).first()
    if _valida(linha, ordem) and not linha.erro and not forcar:
        return _resposta(linha)

    if not linha:
        linha = ExpedicaoFatComparacaoNF(ordem_id=ordem.id)
        db.session.add(linha)
    linha.numero_nf = ordem.numero_nf
    linha.conferido_at = ordem.conferido_at
    linha.verificado_em = agora_br()
    try:
        faturados = _itens_das_nfs(ordem.numero_nf)
    except Exception as exc:
        current_app.logger.warning("Conferido x faturado: NF %s da OF %s indisponivel.", ordem.numero_nf, ordem.cod_ordem_fat, exc_info=True)
        linha.erro = str(exc)[:200] or "NF indisponível no ERP."
        linha.divergente = False
        linha.detalhes = None
    else:
        conferidos = [{"cod_interno": it.cod_interno, "item": it.item, "quantidade": it.qtde_conferida} for it in ordem.itens]
        divergencias = comparar_itens(conferidos, faturados)
        linha.erro = None
        linha.divergente = bool(divergencias)
        linha.detalhes = json.dumps(divergencias, ensure_ascii=False)
    db.session.commit()
    return _resposta(linha)


def ordens_divergentes(ordens: list[ExpedicaoOrdemFat]) -> set[int]:
    """Ids das ordens com divergência guardada e ainda válida. Só banco: é o
    que a lista usa, sem ir no ERP."""
    por_id = {ordem.id: ordem for ordem in ordens}
    if not por_id:
        return set()
    linhas = ExpedicaoFatComparacaoNF.query.filter_by(divergente=True).all()
    return {linha.ordem_id for linha in linhas if linha.ordem_id in por_id and _valida(linha, por_id[linha.ordem_id])}


def verificar_pendentes(limite: int = 10) -> int:
    """Compara as ordens conferidas e faturadas que ainda não têm comparação
    válida, das mais recentes pras mais antigas. Roda junto com a
    sincronização automática; o limite evita uma rajada de XML no bridge."""
    candidatas = (
        ExpedicaoOrdemFat.query
        .filter(
            ExpedicaoOrdemFat.excluido.is_(False),
            ExpedicaoOrdemFat.conferido_at.isnot(None),
            ExpedicaoOrdemFat.expedido_sem_conferencia.is_(False),
            ExpedicaoOrdemFat.numero_nf.isnot(None),
            ExpedicaoOrdemFat.numero_nf != "",
        )
        .order_by(ExpedicaoOrdemFat.faturado_at.desc(), ExpedicaoOrdemFat.id.desc())
        .all()
    )
    linhas = {linha.ordem_id: linha for linha in ExpedicaoFatComparacaoNF.query.all()}
    tentar_de_novo_antes_de = agora_br() - timedelta(hours=HORAS_NOVA_TENTATIVA)
    feitas = 0
    for ordem in candidatas:
        if feitas >= limite:
            break
        linha = linhas.get(ordem.id)
        if _valida(linha, ordem) and (not linha.erro or linha.verificado_em > tentar_de_novo_antes_de):
            continue
        if _comparavel(ordem):
            continue
        verificar(ordem, forcar=True)
        feitas += 1
    return feitas
