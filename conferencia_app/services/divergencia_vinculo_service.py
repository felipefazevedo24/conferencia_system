"""Ajuste de vinculo NF x pedido de compra feito por Compras na tela de
aprovacao da divergencia (/aprovar-divergencia/<token>).

Modelo de dados (o mesmo do Auditor XML, pra um lado nao desfazer o outro):
  - item.pedido_compra: a MESMA string com todos os pedidos da NF ("12717,12720");
  - item.linha_po_vinculada: indice na lista COMBINADA das linhas desses
    pedidos, na ordem de buscar_linhas_pedido (pedidos ordenados).

Esse indice so tem sentido junto com o conjunto de pedidos: se o conjunto
muda, a mesma posicao aponta pra outra linha. Por isso o navegador nunca
manda indice - manda a chave estavel "OC#posicao" (posicao da linha dentro
da propria OC) e o indice e recalculado aqui contra o conjunto NOVO.
"""
from __future__ import annotations

import re

from ..extensions import db
from ..models import DivergenciaPedidoAprovacao, DivergenciaVinculoAjuste, ItemNota
from .pedidos_service import (
    PedidoERPIndisponivelError,
    _parse_lista_pedidos,
    buscar_linhas_pedido,
)

# item.pedido_compra e String(50).
_MAX_PEDIDOS_CHARS = 50


def itens_da_divergencia(registro: DivergenciaPedidoAprovacao) -> list[ItemNota]:
    """Itens da NF do registro. Numero de NF nao e unico entre fornecedores:
    filtra pelo CNPJ quando o registro tem (novos) e, nos antigos, pelo nome
    do fornecedor quando o numero estiver repetido entre emitentes."""
    query = ItemNota.query.filter_by(numero_nota=registro.numero_nota)
    cnpj = re.sub(r"\D", "", str(getattr(registro, "cnpj_emitente", "") or ""))
    if cnpj:
        return query.filter(ItemNota.cnpj_emitente == cnpj).order_by(ItemNota.id.asc()).all()
    itens = query.order_by(ItemNota.id.asc()).all()
    emitentes = {i.cnpj_emitente for i in itens}
    if len(emitentes) > 1 and registro.fornecedor:
        do_fornecedor = [i for i in itens if (i.fornecedor or "") == registro.fornecedor]
        if do_fornecedor:
            return do_fornecedor
    return itens


def pedidos_dos_itens(itens: list[ItemNota]) -> str:
    """Conjunto de pedidos da NF, normalizado e ordenado ("12717,12720")."""
    return ",".join(_parse_lista_pedidos(",".join(str(i.pedido_compra or "") for i in itens)))


def _chave_linhas(linhas_po: list[dict]) -> list[dict]:
    """Anota cada linha do pedido com a chave estavel "OC#posicao"."""
    contagem: dict[str, int] = {}
    saida = []
    for indice, linha in enumerate(linhas_po):
        oc = str(linha.get("pedido_compra") or linha.get("ordem_compra") or "").strip()
        contagem[oc] = contagem.get(oc, 0) + 1
        posicao = contagem[oc]
        saida.append(
            {
                "chave": f"{oc}#{posicao}",
                "indice": indice,
                "pedido": oc,
                "posicao": posicao,
                "codigo": str(linha.get("codigo_material") or "").strip(),
                "descricao": str(linha.get("descricao_material") or linha.get("descricao") or "").strip(),
                "qtd": linha.get("qtd"),
                "valor_unit": linha.get("valor_unit"),
            }
        )
    return saida


def linhas_para_selecao(pedidos: str) -> list[dict]:
    """Linhas de todos os pedidos, com chave estavel, pro seletor da tela.
    Levanta ValueError (mensagem pro usuario) se o ERP nao responder."""
    if not _parse_lista_pedidos(pedidos):
        return []
    try:
        return _chave_linhas(buscar_linhas_pedido(pedidos))
    except PedidoERPIndisponivelError as exc:
        raise ValueError(f"ERP indisponível para consultar o pedido: {exc}") from exc


def _rotulo(linha: dict | None) -> str:
    if not linha:
        return "sem vínculo (automático)"
    codigo = f" · {linha['codigo']}" if linha.get("codigo") else ""
    return f"OC {linha['pedido']} · linha {linha['posicao']}{codigo}"[:300]


def salvar_vinculos(
    registro: DivergenciaPedidoAprovacao,
    pedidos: str,
    vinculos: dict,
    usuario: str,
) -> dict:
    """Grava o conjunto de pedidos da NF e a linha de OC de cada item.

    vinculos: {item_id: "OC#posicao" | None}. None = deixa o Sync escolher.
    Itens fora do dicionario mantem a linha atual (traduzida pro conjunto novo).
    Retorna {"alteracoes": n}. Levanta ValueError com mensagem pro usuario.
    """
    if registro.status != "Pendente":
        raise ValueError(f"Esta divergência já foi {str(registro.status).lower()}; o vínculo não pode mais ser ajustado aqui.")

    lista_nova = _parse_lista_pedidos(pedidos)
    if not lista_nova:
        raise ValueError("Informe ao menos um pedido de compra.")
    pedidos_novos = ",".join(lista_nova)
    if len(pedidos_novos) > _MAX_PEDIDOS_CHARS:
        raise ValueError("Pedidos demais para uma NF (limite de 50 caracteres somando os números).")

    itens = itens_da_divergencia(registro)
    if not itens:
        raise ValueError("Itens da NF não encontrados.")
    itens_por_id = {i.id: i for i in itens}

    vinculos = {int(k): (str(v).strip() if v else None) for k, v in (vinculos or {}).items()}
    estranhos = set(vinculos) - set(itens_por_id)
    if estranhos:
        raise ValueError("Item informado não pertence a esta NF.")

    pedidos_antigos = pedidos_dos_itens(itens)
    linhas_novas = linhas_para_selecao(pedidos_novos)
    por_chave = {l["chave"]: l for l in linhas_novas}
    encontrados = {l["pedido"] for l in linhas_novas}
    faltando = [p for p in lista_nova if p not in encontrados]
    if faltando:
        raise ValueError(f"Pedido(s) sem linhas no ERP: {', '.join(faltando)}. Confira o número.")
    for chave in vinculos.values():
        if chave and chave not in por_chave:
            raise ValueError(f"Linha de pedido inválida: {chave}.")

    linhas_antigas = linhas_novas if pedidos_antigos == pedidos_novos else (
        linhas_para_selecao(pedidos_antigos) if pedidos_antigos else []
    )

    def _linha_antiga(item: ItemNota) -> dict | None:
        idx = item.linha_po_vinculada
        if isinstance(idx, int) and 0 <= idx < len(linhas_antigas):
            return linhas_antigas[idx]
        return None

    historico = []
    if pedidos_antigos != pedidos_novos:
        historico.append((None, None, pedidos_antigos or "nenhum", pedidos_novos))

    for item in itens:
        antes = _linha_antiga(item)
        if item.id in vinculos:
            depois = por_chave.get(vinculos[item.id]) if vinculos[item.id] else None
        else:
            # Nao mexido na tela: mantem a mesma linha (pela chave estavel) se
            # ela existir no conjunto novo; senao volta pro automatico.
            depois = por_chave.get(antes["chave"]) if antes else None

        item.pedido_compra = pedidos_novos
        item.linha_po_vinculada = depois["indice"] if depois else None
        # Mesmo cuidado do Auditor: codigo interno vai em codigo_grv (o XML fica
        # intacto) e nao troca o de linha ja lancada no GRV.
        if depois and depois.get("codigo") and item.status != "Lançado":
            item.codigo_grv = depois["codigo"][:80]

        if _rotulo(antes) != _rotulo(depois):
            historico.append((item.id, item.codigo, _rotulo(antes), _rotulo(depois)))

    registro.pedido_compra = pedidos_novos[:200]
    for item_id, codigo, de, para in historico:
        db.session.add(
            DivergenciaVinculoAjuste(
                divergencia_id=registro.id,
                numero_nota=registro.numero_nota,
                item_nota_id=item_id,
                item_codigo=(codigo or "")[:80] or None,
                de=str(de)[:300],
                para=str(para)[:300],
                usuario=str(usuario or "—")[:160],
            )
        )
    db.session.commit()
    return {"alteracoes": len(historico), "pedidos": pedidos_novos}


def historico_ajustes(registro: DivergenciaPedidoAprovacao) -> list[dict]:
    ajustes = (
        DivergenciaVinculoAjuste.query.filter_by(divergencia_id=registro.id)
        .order_by(DivergenciaVinculoAjuste.id.desc())
        .all()
    )
    return [
        {
            "item_codigo": a.item_codigo,
            "pedidos": a.item_nota_id is None,
            "de": a.de,
            "para": a.para,
            "usuario": a.usuario,
            "em": a.criado_em.strftime("%d/%m/%Y %H:%M") if a.criado_em else "",
        }
        for a in ajustes
    ]
