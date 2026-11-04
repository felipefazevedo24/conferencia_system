"""Inventario ciclico dirigido - regras (definidas pela Logistica em 04/11/2026).

  - O gestor distribui por codigo, familia, grupo ou familia + grupo do GRV
    para um conferente. A lista e' congelada na hora: uma linha por codigo +
    endereco com saldo (por codigo, entra mesmo sem saldo). Linha que ja
    esta em outra distribuicao em aberto nao entra de novo.
  - 1a contagem (conferente, cega): bateu com o GRV -> Confere; nao bateu ->
    Divergente na 1a, e o gestor libera a 2a.
  - 2a contagem (mesmo conferente, cega): bateu -> Confere; nao bateu ->
    Aguardando 3a.
  - 3a contagem (gestor): bateu -> Confere; nao bateu -> Divergencia
    confirmada e abre o ajuste normal (Ajuste de Estoque).
  - Cada rodada compara com o saldo do GRV no momento dela. Linha fechada
    vira uma LogisticaInventarioInicial, igual a contagem avulsa.
"""
from __future__ import annotations

import math
import re

from ..extensions import db
from ..models import (
    InventarioCiclicoDistribuicao as Distribuicao,
    InventarioCiclicoItem as Item,
    LogisticaInventarioInicial,
)
from ..tempo import agora_br
from . import erp_estoque_service as erp
from . import logistica_inventario_ajuste_service as ajuste_svc

CRITERIOS = {
    "codigo": "Código",
    "familia": "Família",
    "grupo": "Grupo",
    "familia_grupo": "Família + grupo",
}
STATUS_FECHADOS = {Item.STATUS_CONFERE, Item.STATUS_AJUSTE, Item.STATUS_CANCELADO}
STATUS_CONFERENTE = {Item.STATUS_AGUARDA_1A: 1, Item.STATUS_AGUARDA_2A: 2}
MOTIVO = "Inventário Cíclico"


def numero(dist: Distribuicao) -> str:
    return f"IC-{dist.id:04d}"


def _norm(valor) -> str:
    return re.sub(r"\s+", " ", str(valor or "")).strip().upper()


def opcoes_familias(estoque: dict) -> dict[str, list[str]]:
    """{familia: [grupos]} de tudo que existe no GRV, para os filtros da tela."""
    mapa: dict[str, set[str]] = {}
    for item in (estoque.get("por_codigo") or {}).values():
        familia = str(item.get("familia") or "").strip()
        if not familia:
            continue
        grupos = mapa.setdefault(familia, set())
        grupo = str(item.get("grupo") or "").strip()
        if grupo:
            grupos.add(grupo)
    return {f: sorted(g) for f, g in sorted(mapa.items())}


def _codigos_do_texto(texto: str) -> list[str]:
    vistos, codigos = set(), []
    for parte in re.split(r"[\s,;]+", str(texto or "")):
        codigo = parte.strip().upper()
        if codigo and codigo not in vistos:
            vistos.add(codigo)
            codigos.append(codigo)
    return codigos


def _linhas_do_codigo(codigo: str, agregado: dict, estoque: dict, *, so_com_saldo: bool) -> list[dict]:
    base = {
        "codigo": codigo,
        "descricao": str(agregado.get("item") or "").strip(),
        "unidade": str(agregado.get("unidade") or "UN").strip().upper() or "UN",
    }
    linhas = []
    for local in sorted(set(agregado.get("localizacoes") or [])):
        bruto = (estoque.get("por_local") or {}).get(f"{codigo}|{local}") or {}
        if so_com_saldo and float(bruto.get("qtde_total") or 0) <= 0:
            continue
        linhas.append({**base, "endereco": local})
    # Codigo sem localizacao no GRV: conta o total, sem endereco.
    if not agregado.get("localizacoes") and (not so_com_saldo or float(agregado.get("qtde_total") or 0) > 0):
        linhas.append({**base, "endereco": ""})
    return linhas


def montar_linhas(estoque: dict, criterio: str, familia: str, grupo: str, codigos: list[str]) -> tuple[list[dict], list[str]]:
    """Linhas a contar e codigos digitados que nao existem no GRV."""
    por_codigo = estoque.get("por_codigo") or {}
    linhas, nao_encontrados = [], []
    if criterio == "codigo":
        for codigo in codigos:
            agregado = por_codigo.get(codigo)
            if agregado is None:
                nao_encontrados.append(codigo)
                continue
            linhas.extend(_linhas_do_codigo(codigo, agregado, estoque, so_com_saldo=False))
        return linhas, nao_encontrados

    familia_n, grupo_n = _norm(familia), _norm(grupo)
    for codigo in sorted(por_codigo):
        agregado = por_codigo[codigo]
        if criterio in {"familia", "familia_grupo"} and _norm(agregado.get("familia")) != familia_n:
            continue
        if criterio in {"grupo", "familia_grupo"} and _norm(agregado.get("grupo")) != grupo_n:
            continue
        linhas.extend(_linhas_do_codigo(codigo, agregado, estoque, so_com_saldo=True))
    return linhas, nao_encontrados


def _chaves_em_aberto() -> set[tuple[str, str]]:
    rows = (
        db.session.query(Item.codigo, Item.endereco)
        .join(Distribuicao)
        .filter(Item.status.notin_(STATUS_FECHADOS), Distribuicao.cancelado_em.is_(None))
        .all()
    )
    return {(c, e or "") for c, e in rows}


def distribuir(criterio: str, familia: str, grupo: str, codigos_texto: str, conferente: str,
               usuario: str, *, simular: bool = False) -> dict:
    criterio = str(criterio or "").strip()
    if criterio not in CRITERIOS:
        raise ValueError("Escolha como distribuir: código, família, grupo ou família + grupo.")
    familia, grupo = str(familia or "").strip(), str(grupo or "").strip()
    codigos = _codigos_do_texto(codigos_texto)
    if criterio == "codigo" and not codigos:
        raise ValueError("Informe ao menos um código.")
    if criterio in {"familia", "familia_grupo"} and not familia:
        raise ValueError("Escolha a família.")
    if criterio in {"grupo", "familia_grupo"} and not grupo:
        raise ValueError("Escolha o grupo.")
    conferente = str(conferente or "").strip()
    if not simular and not conferente:
        raise ValueError("Escolha o conferente.")

    try:
        estoque = erp.buscar_estoque_grv()
    except Exception as exc:  # noqa: BLE001
        raise ValueError("Não deu para consultar o GRV agora. Tente de novo em instantes.") from exc

    linhas, nao_encontrados = montar_linhas(estoque, criterio, familia, grupo, codigos)
    em_aberto = _chaves_em_aberto()
    novas = [l for l in linhas if (l["codigo"], l["endereco"]) not in em_aberto]
    resultado = {
        "total": len(novas),
        "ja_em_aberto": len(linhas) - len(novas),
        "nao_encontrados": nao_encontrados,
        "codigos": len({l["codigo"] for l in novas}),
    }
    if simular:
        return resultado
    if not novas:
        if linhas:
            raise ValueError("Todos os itens desse filtro já estão em outra distribuição aberta.")
        raise ValueError("Nenhum item para distribuir com esse filtro.")

    dist = Distribuicao(
        criterio=criterio,
        familia=(familia or None) if criterio in {"familia", "familia_grupo"} else None,
        grupo=(grupo or None) if criterio in {"grupo", "familia_grupo"} else None,
        codigos="\n".join(codigos) if criterio == "codigo" else None,
        conferente=conferente,
        criado_por=usuario,
        criado_em=agora_br(),
    )
    for l in novas:
        dist.itens.append(Item(codigo=l["codigo"], descricao=l["descricao"][:300], unidade=l["unidade"][:20],
                               endereco=l["endereco"][:120], status=Item.STATUS_AGUARDA_1A))
    db.session.add(dist)
    db.session.commit()
    resultado["distribuicao"] = dist
    return resultado


def itens_do_conferente(username: str) -> list[Item]:
    return (
        Item.query.join(Distribuicao)
        .filter(
            Distribuicao.conferente == username,
            Distribuicao.cancelado_em.is_(None),
            Item.status.in_(list(STATUS_CONFERENTE)),
        )
        .order_by(Item.endereco, Item.codigo)
        .all()
    )


def _quantidade(valor) -> float:
    try:
        qtd = float(str(valor).replace(",", "."))
    except (TypeError, ValueError):
        raise ValueError("Quantidade inválida.") from None
    if not math.isfinite(qtd) or qtd < 0:
        raise ValueError("Quantidade inválida.")
    return qtd


def _saldo_grv(item: Item) -> tuple[float, float | None, dict]:
    try:
        estoque = erp.buscar_estoque_grv()  # cache de 5 min, igual a contagem avulsa
    except Exception as exc:  # noqa: BLE001
        raise ValueError("Não deu para consultar o saldo no GRV agora. Tente de novo em instantes.") from exc
    # Codigo que saiu do GRV conta como saldo zero: o contado e' a divergencia.
    qtde = erp.qtde_grv_para(item.codigo, item.endereco, estoque)
    custo = erp.custo_medio_para(item.codigo, item.endereco, estoque)
    return float(qtde or 0), custo, estoque


def _bate(qtd: float, grv: float) -> bool:
    return abs(qtd - grv) <= ajuste_svc.TOLERANCIA_DIVERGENCIA


def _fechar(item: Item, qtd: float, grv: float, custo: float | None, usuario: str, rodada: int, bateu: bool) -> None:
    agora = agora_br()
    contagem = LogisticaInventarioInicial(
        local_codigo=item.endereco or "",
        codigo_produto=item.codigo,
        unidade_medida=(item.unidade or "UN")[:20],
        quantidade=qtd,
        motivo=MOTIVO,
        observacao=f"Inventário cíclico {numero(item.distribuicao)} - {rodada}ª contagem",
        criado_por=usuario,
        atualizado_em=agora,
        qtde_grv_no_momento=grv,
        custo_medio_no_momento=custo,
        grv_consultado_em=agora,
    )
    db.session.add(contagem)
    db.session.flush()
    item.contagem_id = contagem.id
    item.status = Item.STATUS_CONFERE if bateu else Item.STATUS_AJUSTE
    db.session.commit()
    if not bateu:
        # None quando ja ha ajuste aberto pro mesmo codigo + endereco (nao duplica).
        ajuste = ajuste_svc.detectar_divergencia(contagem, grv, custo, item.descricao)
        if ajuste:
            item.ajuste_id = ajuste.id
            db.session.commit()


def registrar_contagem_conferente(item: Item, quantidade, usuario: str) -> None:
    """1a ou 2a contagem. Nao devolve o resultado: a contagem e' cega."""
    if item.distribuicao.cancelado_em or item.distribuicao.conferente != usuario:
        raise ValueError("Este item não está com você para contar.")
    rodada = STATUS_CONFERENTE.get(item.status)
    if not rodada:
        raise ValueError("Este item já foi contado.")
    qtd = _quantidade(quantidade)
    grv, custo, _ = _saldo_grv(item)
    bateu = _bate(qtd, grv)
    agora = agora_br()
    if rodada == 1:
        item.contagem1, item.contagem1_por, item.contagem1_em, item.grv1 = qtd, usuario, agora, grv
    else:
        item.contagem2, item.contagem2_por, item.contagem2_em, item.grv2 = qtd, usuario, agora, grv
    if bateu:
        _fechar(item, qtd, grv, custo, usuario, rodada, True)
        return
    item.status = Item.STATUS_DIVERGENTE_1A if rodada == 1 else Item.STATUS_AGUARDA_3A
    db.session.commit()


def liberar_recontagem(item: Item, usuario: str) -> None:
    if item.status != Item.STATUS_DIVERGENTE_1A or item.distribuicao.cancelado_em:
        raise ValueError("Só dá para liberar a 2ª contagem de item divergente na 1ª.")
    item.status = Item.STATUS_AGUARDA_2A
    item.recontagem_liberada_por = usuario
    item.recontagem_liberada_em = agora_br()
    db.session.commit()


def registrar_contagem_gestor(item: Item, quantidade, usuario: str) -> bool:
    """3a contagem. Devolve True se bateu com o GRV."""
    if item.status != Item.STATUS_AGUARDA_3A or item.distribuicao.cancelado_em:
        raise ValueError("Este item não está aguardando a 3ª contagem.")
    qtd = _quantidade(quantidade)
    grv, custo, _ = _saldo_grv(item)
    bateu = _bate(qtd, grv)
    item.contagem3, item.contagem3_por, item.contagem3_em, item.grv3 = qtd, usuario, agora_br(), grv
    _fechar(item, qtd, grv, custo, usuario, 3, bateu)
    return bateu


def cancelar_distribuicao(dist: Distribuicao, usuario: str) -> int:
    """Cancela o que ainda nao fechou; o que ja foi contado fica como esta."""
    if dist.cancelado_em:
        raise ValueError("Distribuição já cancelada.")
    abertos = [i for i in dist.itens if i.status not in STATUS_FECHADOS]
    for item in abertos:
        item.status = Item.STATUS_CANCELADO
    dist.cancelado_por = usuario
    dist.cancelado_em = agora_br()
    db.session.commit()
    return len(abertos)


def resumo(dist: Distribuicao) -> dict:
    contagem: dict[str, int] = {}
    for item in dist.itens:
        contagem[item.status] = contagem.get(item.status, 0) + 1
    fechados = sum(contagem.get(s, 0) for s in (Item.STATUS_CONFERE, Item.STATUS_AJUSTE))
    total = sum(v for s, v in contagem.items() if s != Item.STATUS_CANCELADO) or 0
    return {"por_status": contagem, "total": total, "fechados": fechados}
