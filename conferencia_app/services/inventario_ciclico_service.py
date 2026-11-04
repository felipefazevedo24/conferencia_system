"""Inventario ciclico dirigido - regras (definidas pela Logistica em 04/11/2026).

  - O gestor distribui por codigo, familia, grupo ou familia + grupo do GRV
    para um conferente. A lista e' congelada na hora: uma linha por codigo +
    endereco, com ou sem saldo (item zerado no sistema pode estar no fisico -
    pedido da Logistica em 04/11/2026). Linha que ja esta em outra
    distribuicao em aberto nao entra de novo.
  - 1a contagem (conferente, cega): bateu com o GRV -> Confere; nao bateu ->
    Divergente na 1a, e o gestor libera a 2a.
  - 2a contagem (mesmo conferente, cega): bateu -> Confere; nao bateu ->
    Aguardando 3a.
  - 3a contagem (gestor): bateu -> Confere; nao bateu -> Divergencia
    confirmada e abre o ajuste normal (Ajuste de Estoque).
  - Divergente na 1a ou aguardando a 3a, o gestor pode pular a recontagem e
    mandar direto pro ajuste com a ultima contagem do conferente.
  - Ajuste aberto pelo ciclico ja foi validado pelo gestor: se ele tem a
    permissao de Validacao de Suprimentos, o ajuste pula essa etapa e entra
    em Emissao do Formulario (pedido da Logistica em 04/11/2026).
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
from ..auth import has_permission
from ..tempo import agora_br
from flask import current_app, has_request_context, request

from . import erp_estoque_service as erp
from . import teams_service
from . import estoque_espelho_service as espelho
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
PERM_VALIDACAO_SUPRIMENTOS = "PAGE_LOGISTICA_INVENTARIO_VALIDACAO"


def numero(dist: Distribuicao) -> str:
    return f"IC-{dist.id:04d}"


def _norm(valor) -> str:
    return re.sub(r"\s+", " ", str(valor or "")).strip().upper()


def _ordem_grupo(codigo: str):
    return (0, int(codigo)) if codigo.isdigit() else (1, codigo)


def opcoes_familias(estoque: dict) -> dict[str, list[dict]]:
    """{familia: [{codigo, nome}]} de tudo que existe no GRV, para os filtros.
    O filtro e' pelo codigo do grupo (o nome se repete entre familias)."""
    mapa: dict[str, dict[str, str]] = {}
    for item in (estoque.get("por_codigo") or {}).values():
        familia = str(item.get("familia") or "").strip()
        if not familia:
            continue
        grupos = mapa.setdefault(familia, {})
        grupo = str(item.get("grupo") or "").strip()
        if grupo:
            grupos[grupo] = str(item.get("grupo_nome") or "").strip() or grupos.get(grupo, "")
    return {
        f: [{"codigo": g, "nome": grupos[g]} for g in sorted(grupos, key=_ordem_grupo)]
        for f, grupos in sorted(mapa.items())
    }


def _codigos_do_texto(texto: str) -> list[str]:
    vistos, codigos = set(), []
    for parte in re.split(r"[\s,;]+", str(texto or "")):
        codigo = parte.strip().upper()
        if codigo and codigo not in vistos:
            vistos.add(codigo)
            codigos.append(codigo)
    return codigos


def _linhas_do_codigo(codigo: str, agregado: dict, estoque: dict) -> list[dict]:
    base = {
        "codigo": codigo,
        "descricao": str(agregado.get("item") or "").strip(),
        "unidade": str(agregado.get("unidade") or "UN").strip().upper() or "UN",
    }
    linhas = []
    for local in sorted(set(agregado.get("localizacoes") or [])):
        bruto = (estoque.get("por_local") or {}).get(f"{codigo}|{local}") or {}
        linhas.append({**base, "endereco": local, "saldo": float(bruto.get("qtde_total") or 0)})
    # Codigo sem localizacao no GRV: conta o total, sem endereco.
    if not agregado.get("localizacoes"):
        linhas.append({**base, "endereco": "", "saldo": float(agregado.get("qtde_total") or 0)})
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
            linhas.extend(_linhas_do_codigo(codigo, agregado, estoque))
        return linhas, nao_encontrados

    familia_n, grupo_n = _norm(familia), _norm(grupo)
    for codigo in sorted(por_codigo):
        agregado = por_codigo[codigo]
        if criterio in {"familia", "familia_grupo"} and _norm(agregado.get("familia")) != familia_n:
            continue
        if criterio in {"grupo", "familia_grupo"} and _norm(agregado.get("grupo")) != grupo_n:
            continue
        linhas.extend(_linhas_do_codigo(codigo, agregado, estoque))
    return linhas, nao_encontrados


def _chaves_em_aberto() -> set[tuple[str, str]]:
    rows = (
        db.session.query(Item.codigo, Item.endereco)
        .join(Distribuicao)
        .filter(Item.status.notin_(STATUS_FECHADOS), Distribuicao.cancelado_em.is_(None))
        .all()
    )
    return {(c, e or "") for c, e in rows}


def _validar_filtro(criterio: str, familia: str, grupo: str, codigos_texto: str) -> tuple[str, str, str, list[str]]:
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
    return criterio, familia, grupo, codigos


def _estoque() -> dict:
    """Listas saem do espelho (abre na hora); o saldo da contagem, nao."""
    estoque = espelho.carregar()
    if not estoque.get("por_codigo"):
        raise ValueError("O estoque do GRV ainda não foi carregado. Clique em \"Atualizar agora\" e aguarde.")
    return estoque


def previa(criterio: str, familia: str, grupo: str, codigos_texto: str) -> dict:
    """Linhas do filtro para o gestor escolher; as que ja estao em outra
    distribuicao aberta vem marcadas e nao podem ser escolhidas."""
    criterio, familia, grupo, codigos = _validar_filtro(criterio, familia, grupo, codigos_texto)
    linhas, nao_encontrados = montar_linhas(_estoque(), criterio, familia, grupo, codigos)
    em_aberto = _chaves_em_aberto()
    for l in linhas:
        l["em_aberto"] = (l["codigo"], l["endereco"]) in em_aberto
    return {"linhas": linhas, "nao_encontrados": nao_encontrados}


def distribuir(criterio: str, familia: str, grupo: str, codigos_texto: str, conferente: str,
               usuario: str, selecionados: list) -> dict:
    """Distribui so' as linhas escolhidas na previa. A lista e' refeita aqui
    contra o GRV: linha que saiu do filtro ou entrou em outra distribuicao
    no meio do caminho fica de fora."""
    criterio, familia, grupo, codigos = _validar_filtro(criterio, familia, grupo, codigos_texto)
    conferente = str(conferente or "").strip()
    if not conferente:
        raise ValueError("Escolha o conferente.")
    escolhidas = {
        (str(x.get("codigo") or "").strip().upper(), str(x.get("endereco") or "").strip().upper())
        for x in (selecionados or []) if isinstance(x, dict)
    }
    if not escolhidas:
        raise ValueError("Selecione ao menos um item.")

    linhas, _ = montar_linhas(_estoque(), criterio, familia, grupo, codigos)
    em_aberto = _chaves_em_aberto()
    novas = [l for l in linhas if (l["codigo"], l["endereco"]) in escolhidas and (l["codigo"], l["endereco"]) not in em_aberto]
    if not novas:
        raise ValueError("Os itens selecionados não estão mais disponíveis: já entraram em outra distribuição ou saíram do filtro.")

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
    return {"distribuicao": dist, "total": len(novas), "fora": len(escolhidas) - len(novas)}


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
    """Saldo do momento, so' daquele codigo. Bridge ainda sem o endpoint:
    cai no estoque inteiro (cache de 5 min, igual a contagem avulsa)."""
    try:
        try:
            estoque = erp.buscar_estoque_produtos_grv([item.codigo])
        except LookupError:
            estoque = erp.buscar_estoque_grv()
    except Exception as exc:  # noqa: BLE001
        raise ValueError("Não deu para consultar o saldo no GRV agora. Tente de novo em instantes.") from exc
    # Codigo que saiu do GRV conta como saldo zero: o contado e' a divergencia.
    qtde = erp.qtde_grv_para(item.codigo, item.endereco, estoque)
    custo = erp.custo_medio_para(item.codigo, item.endereco, estoque)
    return float(qtde or 0), custo, estoque


def _bate(qtd: float, grv: float) -> bool:
    return abs(qtd - grv) <= ajuste_svc.TOLERANCIA_DIVERGENCIA


def _link_ajustes() -> str:
    base = str(current_app.config.get("PUBLIC_BASE_URL") or "").strip().rstrip("/")
    if not base and has_request_context():
        base = request.url_root.rstrip("/")
    return f"{base}/logistica/inventario?aba=ajustes"


def _avisar_teams(ajustes: list, origem: str) -> None:
    """Mesmo aviso da contagem avulsa (grupo do gestor). Um ajuste: card
    normal; varios: um card com a lista. Falha no Teams nunca desfaz nada."""
    # Ajuste que ja saiu confirmado do ciclico espera o formulario, nao a validacao.
    def _etapa(a):
        return "Emissão do Formulário" if a.status_modulo == "Relatorio" else "Validação de Suprimentos"

    try:
        if len(ajustes) == 1:
            a, contado_por = ajustes[0]
            teams_service.notificar_divergencia_inventario_gestor(
                a.codigo_produto, a.local_codigo, a.diferenca,
                descricao=a.descricao_produto, unidade=a.unidade_medida,
                qtde_contada=a.qtde_contada, qtde_sistema=a.qtde_estoque_no_momento,
                custo_medio=a.custo_medio, contado_por=contado_por, link=_link_ajustes(),
                aguardando=_etapa(a),
            )
        elif ajustes:
            teams_service.notificar_divergencias_inventario_lote(
                [{"codigo": a.codigo_produto, "local": a.local_codigo, "diferenca": a.diferenca,
                  "unidade": a.unidade_medida, "custo_medio": a.custo_medio} for a, _ in ajustes],
                origem=origem, link=_link_ajustes(),
                aguardando=" / ".join(sorted({_etapa(a) for a, _ in ajustes})),
            )
    except Exception as exc:  # noqa: BLE001
        current_app.logger.warning("Falha ao avisar o Teams do inventario ciclico: %s", exc)


def _fechar(item: Item, qtd: float, grv: float, custo: float | None, usuario: str, rodada: int, bateu: bool,
            obs_extra: str = "", validado_por: str = "", validacao: str = ""):
    """Fecha a linha; devolve o ajuste aberto (ou None). `validado_por` e'
    o gestor que decidiu no ciclico; com a permissao de Validacao, o ajuste
    ja sai confirmado (ver confirmar_pelo_inventario_ciclico)."""
    agora = agora_br()
    contagem = LogisticaInventarioInicial(
        local_codigo=item.endereco or "",
        codigo_produto=item.codigo,
        unidade_medida=(item.unidade or "UN")[:20],
        quantidade=qtd,
        motivo=MOTIVO,
        observacao=f"Inventário cíclico {numero(item.distribuicao)} - {rodada}ª contagem{obs_extra}",
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
            if validado_por and has_permission(PERM_VALIDACAO_SUPRIMENTOS, username=validado_por):
                ajuste_svc.confirmar_pelo_inventario_ciclico(
                    ajuste, validado_por, f"Validado no inventário cíclico {numero(item.distribuicao)}: {validacao}")
        return ajuste
    return None


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


def seguir_para_ajuste(item: Item, usuario: str, custo: float | None = None, *, avisar: bool = True):
    """Gestor dispensa a recontagem: fecha com a ultima contagem do conferente
    e abre o ajuste. A diferenca usa o saldo do GRV gravado naquela contagem
    (o que o conferente confrontou). O custo medio vem do espelho - ir no GRV
    so' por ele deixava o botao lento (ver acao_em_lote)."""
    if item.distribuicao.cancelado_em or item.status not in {Item.STATUS_DIVERGENTE_1A, Item.STATUS_AGUARDA_3A}:
        raise ValueError("Só dá para seguir para o ajuste com item divergente aguardando recontagem.")
    if item.status == Item.STATUS_DIVERGENTE_1A:
        rodada, qtd, grv, contou = 1, item.contagem1, item.grv1, item.contagem1_por
    else:
        rodada, qtd, grv, contou = 2, item.contagem2, item.grv2, item.contagem2_por
    ajuste = _fechar(item, float(qtd or 0), float(grv or 0), custo, contou or usuario, rodada, False,
                     obs_extra=f" (recontagem dispensada por {usuario})", validado_por=usuario,
                     validacao=f"{rodada}ª contagem divergente, recontagem dispensada")
    if ajuste and avisar:
        _avisar_teams([(ajuste, contou)], f"Inventário cíclico {numero(item.distribuicao)}")
    return ajuste


ACOES_LOTE = {"liberar": "Liberar 2ª contagem", "ajuste": "Seguir para ajuste"}


def acao_em_lote(acao: str, ids: list, usuario: str) -> dict:
    """Liberar a 2a ou seguir para ajuste varios itens de uma vez. Item que
    nao pode (status mudou, outra aba ja tratou) entra em "erros" e o resto
    segue - um nao trava os outros."""
    if acao not in ACOES_LOTE:
        raise ValueError("Ação inválida.")
    try:
        ids = sorted({int(i) for i in (ids or [])})
    except (TypeError, ValueError):
        raise ValueError("Seleção inválida.") from None
    if not ids:
        raise ValueError("Selecione ao menos um item.")
    itens = Item.query.filter(Item.id.in_(ids)).order_by(Item.id).all()
    custos = {}
    if acao == "ajuste":
        # Reserva quando o Cardex nao tem custo (ver detectar_divergencia).
        custos = espelho.custos([i.codigo for i in itens])
        # O custo do ajuste sai do Cardex, um codigo por consulta: busca todos
        # em paralelo antes, senao o lote espera um por um.
        from . import logistica_cardex_service as cardex
        cardex.precarregar_custos_inventario([i.codigo for i in itens])
    feitos, abertos, erros = 0, [], []
    for item in itens:
        try:
            if acao == "liberar":
                liberar_recontagem(item, usuario)
            else:
                contou = item.contagem1_por if item.status == Item.STATUS_DIVERGENTE_1A else item.contagem2_por
                ajuste = seguir_para_ajuste(item, usuario, custos.get((item.codigo, item.endereco or "")), avisar=False)
                if ajuste:
                    abertos.append((ajuste, contou))
            feitos += 1
        except ValueError as exc:
            db.session.rollback()
            erros.append({"codigo": item.codigo, "endereco": item.endereco, "erro": str(exc)})
    if abertos:
        numeros = sorted({numero(i.distribuicao) for i in itens})
        _avisar_teams(abertos, f"Inventário cíclico {', '.join(numeros)} · enviado ao ajuste por {usuario} sem recontagem")
    return {"feitos": feitos, "ajustes": len(abertos), "erros": erros}


def registrar_contagem_gestor(item: Item, quantidade, usuario: str) -> bool:
    """3a contagem. Devolve True se bateu com o GRV."""
    if item.status != Item.STATUS_AGUARDA_3A or item.distribuicao.cancelado_em:
        raise ValueError("Este item não está aguardando a 3ª contagem.")
    qtd = _quantidade(quantidade)
    grv, custo, _ = _saldo_grv(item)
    bateu = _bate(qtd, grv)
    item.contagem3, item.contagem3_por, item.contagem3_em, item.grv3 = qtd, usuario, agora_br(), grv
    ajuste = _fechar(item, qtd, grv, custo, usuario, 3, bateu, validado_por=usuario,
                     validacao="3ª contagem confirmou a diferença")
    if ajuste:
        _avisar_teams([(ajuste, usuario)], f"Inventário cíclico {numero(item.distribuicao)}")
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
