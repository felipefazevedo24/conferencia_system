"""Inventario geral - regras da contagem (definidas pela Logistica em 15-19/10/2026).

  - Abertura: le a API do CPS e congela o retrato. Entram os produtos com
    saldo (sem lote: uma linha; com lote: uma linha por lote com saldo). O
    estoque fica parado durante o inventario, entao a 1a contagem e' estatica.
  - 1a contagem = saldo do sistema. 2a e 3a: cegas e de pessoas diferentes.
      2a = 1a           -> Confere com o sistema
      2a != 1a          -> pede a 3a
      3a = 1a           -> Confere com o sistema (a 2a e' ignorada)
      3a = 2a (!= 1a)   -> Divergencia confirmada (vale o contado)
      nenhuma bate      -> Sem correspondencia -> "limpar e recontar"
  - Achado no fisico sem saldo no sistema: adicionado com 1a contagem = 0.
  - Sem fluxo de gestor/Finance: finalizado, o resultado vai pro CPS pela
    API (/simular e depois /aplicar), e o retorno fica registrado.
"""
from __future__ import annotations

import json

from ..extensions import db
from ..models import (
    InventarioGeral as Inventario,
    InventarioGeralEnvio as Envio,
    InventarioGeralItem as Item,
    InventarioGeralProduto as Produto,
)
from ..tempo import agora_br
from . import inventario_cps_client as cps

TOLERANCIA = 0.0005  # contagens com casas decimais (kg, m)
MAX_PRODUTOS_POR_ENVIO = 500  # limite da API


def _txt(valor) -> str:
    return str(valor if valor is not None else "").strip()


def _igual(a, b) -> bool:
    return a is not None and b is not None and abs(float(a) - float(b)) <= TOLERANCIA


def _qtde(valor) -> float:
    texto = _txt(valor).replace(" ", "")
    if "," in texto:
        texto = texto.replace(".", "").replace(",", ".")
    try:
        numero = float(texto)
    except ValueError:
        raise ValueError("Informe a quantidade contada (número, pode ter casas decimais).")
    if numero < 0:
        raise ValueError("A quantidade não pode ser negativa.")
    return round(numero, 6)


def com_lote(tipo_controle) -> bool:
    return int(tipo_controle or 0) != 0


# ── Abertura ────────────────────────────────────────────────────────────
def inventario_ativo() -> Inventario | None:
    """O que ainda nao foi enviado ao ERP (so' um por vez - estoque parado)."""
    return (Inventario.query
            .filter(Inventario.status != Inventario.STATUS_ENVIADO)
            .order_by(Inventario.id.desc()).first())


def abrir(usuario: str, descricao: str = "") -> Inventario:
    if inventario_ativo():
        raise ValueError("Já existe um inventário geral em andamento - finalize e envie antes de abrir outro.")
    produtos = cps.listar_deposito()
    inv = Inventario(descricao=_txt(descricao)[:160] or None, aberto_por=usuario, aberto_em=agora_br())
    db.session.add(inv)
    db.session.flush()
    for p in produtos:
        cod = int(p["cod_produto"])
        base = {
            "cod_produto": cod,
            "codigo_interno": _txt(p.get("codigo_interno"))[:60],
            "descricao": _txt(p.get("produto"))[:300],
            "unidade": _txt(p.get("unidade"))[:20],
            "tipo_controle": int(p.get("tipo_controle") or 0),
        }
        total = float(p.get("qtde_total") or 0)
        db.session.add(Produto(inventario_id=inv.id, qtde_total=total, versao=_txt(p.get("versao"))[:80], **base))
        if total <= TOLERANCIA:
            continue
        lotes = [l for l in (p.get("lotes") or []) if float(l.get("qtde") or 0) > TOLERANCIA]
        if com_lote(base["tipo_controle"]) and lotes:
            for l in lotes:
                db.session.add(Item(inventario_id=inv.id, lote=_txt(l.get("nome"))[:120] or None,
                                    qtde_sistema=float(l["qtde"]), **base))
        else:
            db.session.add(Item(inventario_id=inv.id, qtde_sistema=total, **base))
    db.session.commit()
    return inv


# ── Contagem ────────────────────────────────────────────────────────────
def _exigir_aberto(inv: Inventario) -> None:
    if inv.status != Inventario.STATUS_ABERTO:
        raise ValueError(f"O inventário está '{inv.status}' - não aceita mais contagem.")


def registrar_contagem(item: Item, quantidade, usuario: str) -> Item:
    """2a ou 3a contagem, conforme o estado do item."""
    _exigir_aberto(item.inventario)
    qtde = _qtde(quantidade)
    agora = agora_br()
    if item.status == Item.STATUS_PENDENTE:
        item.contagem2, item.contagem2_por, item.contagem2_em = qtde, usuario, agora
        if _igual(qtde, item.qtde_sistema):
            item.status, item.qtde_final = Item.STATUS_CONFERE, item.qtde_sistema
        else:
            item.status = Item.STATUS_AGUARDA_3A
    elif item.status == Item.STATUS_AGUARDA_3A:
        if (item.contagem2_por or "").upper() == (usuario or "").upper():
            raise ValueError("A 3ª contagem precisa ser feita por outra pessoa (você fez a 2ª).")
        item.contagem3, item.contagem3_por, item.contagem3_em = qtde, usuario, agora
        if _igual(qtde, item.qtde_sistema):
            item.status, item.qtde_final = Item.STATUS_CONFERE, item.qtde_sistema
        elif _igual(qtde, item.contagem2):
            item.status, item.qtde_final = Item.STATUS_DIVERGENCIA, qtde
        else:
            item.status, item.qtde_final = Item.STATUS_SEM_CORRESPONDENCIA, None
    else:
        raise ValueError(f"Este item já está encerrado ({item.status}).")
    db.session.commit()
    return item


def recontar(item: Item, usuario: str) -> Item:
    """Sem correspondencia: limpa a 2a e a 3a e o item volta pra contagem."""
    _exigir_aberto(item.inventario)
    if item.status != Item.STATUS_SEM_CORRESPONDENCIA:
        raise ValueError("Só dá pra limpar e recontar um item sem correspondência entre as contagens.")
    for campo in ("contagem2", "contagem2_por", "contagem2_em", "contagem3", "contagem3_por", "contagem3_em", "qtde_final"):
        setattr(item, campo, None)
    item.status = Item.STATUS_PENDENTE
    item.recontagens = (item.recontagens or 0) + 1
    db.session.commit()
    return item


def buscar_produtos(inv: Inventario, termo: str, limite: int = 20) -> list[Produto]:
    """Pra adicionar item achado no fisico: busca no retrato da abertura."""
    termo = _txt(termo)
    if len(termo) < 2:
        return []
    like = f"%{termo}%"
    return (Produto.query.filter(Produto.inventario_id == inv.id)
            .filter(db.or_(Produto.codigo_interno.ilike(like), Produto.descricao.ilike(like)))
            .order_by(Produto.codigo_interno).limit(limite).all())


def adicionar_item(inv: Inventario, cod_produto, lote: str, usuario: str) -> Item:
    """Item (ou lote) achado no fisico sem saldo no sistema: 1a contagem = 0."""
    _exigir_aberto(inv)
    try:
        cod = int(cod_produto)
    except (TypeError, ValueError):
        raise ValueError("Selecione o produto.")
    produto = Produto.query.filter_by(inventario_id=inv.id, cod_produto=cod).first()
    if not produto:
        raise ValueError("Produto não está entre os elegíveis do ERP (inativo ou fora do inventário).")
    lote = _txt(lote)[:120] or None
    if com_lote(produto.tipo_controle) and not lote:
        raise ValueError("Este produto é controlado por lote - informe o lote encontrado.")
    if not com_lote(produto.tipo_controle):
        lote = None
    existente = Item.query.filter_by(inventario_id=inv.id, cod_produto=cod).all()
    if any((i.lote or "").upper() == (lote or "").upper() for i in existente):
        raise ValueError("Este item" + (f" / lote {lote}" if lote else "") + " já está no inventário - registre a contagem nele.")
    item = Item(
        inventario_id=inv.id, cod_produto=cod, codigo_interno=produto.codigo_interno, descricao=produto.descricao,
        unidade=produto.unidade, tipo_controle=produto.tipo_controle, lote=lote, qtde_sistema=0,
        adicionado_manual=True, adicionado_por=usuario,
    )
    db.session.add(item)
    db.session.commit()
    return item


def resumo(inv: Inventario) -> dict:
    contagem = {s: 0 for s in (Item.STATUS_PENDENTE, Item.STATUS_AGUARDA_3A, Item.STATUS_CONFERE,
                               Item.STATUS_DIVERGENCIA, Item.STATUS_SEM_CORRESPONDENCIA)}
    for i in inv.itens:
        contagem[i.status] = contagem.get(i.status, 0) + 1
    contagem["total"] = len(inv.itens)
    contagem["abertos"] = contagem[Item.STATUS_PENDENTE] + contagem[Item.STATUS_AGUARDA_3A] + contagem[Item.STATUS_SEM_CORRESPONDENCIA]
    return contagem


def finalizar(inv: Inventario, usuario: str) -> Inventario:
    _exigir_aberto(inv)
    abertos = resumo(inv)["abertos"]
    if abertos:
        raise ValueError(f"Ainda há {abertos} item(ns) sem contagem encerrada (pendentes, aguardando 3ª ou sem correspondência).")
    inv.status = Inventario.STATUS_FINALIZADO
    inv.finalizado_em = agora_br()
    inv.finalizado_por = usuario
    db.session.commit()
    return inv


# ── Envio ao ERP (CPS) ─────────────────────────────────────────────────
def _payloads(inv: Inventario, atual: dict[int, dict], operador: str) -> tuple[list[dict], list[dict]]:
    """Agrupa as linhas por produto (lote: todos os lotes contados, total =
    soma). estoque_esperado/versao vem da leitura ATUAL (regra da API).
    Devolve (payloads, recusados_pelo_sync)."""
    por_produto: dict[int, list[Item]] = {}
    for item in inv.itens:
        por_produto.setdefault(item.cod_produto, []).append(item)
    linhas, recusados = [], []
    retrato = {p.cod_produto: p for p in Produto.query.filter_by(inventario_id=inv.id).all()}
    for cod, itens in sorted(por_produto.items()):
        estado = atual.get(cod)
        if not estado:
            recusados.append({"cod_produto": cod, "motivo": "Produto não voltou na leitura atual do ERP (inativo?)."})
            continue
        # O CPS recusa (409) produto cujo estoque geral nao bate com a soma
        # dos depositos - e um recusado derruba o lote de 500 inteiro. Visto
        # em 584 dos 2.051 itens com saldo na leitura de 19/10/2026: separa
        # aqui pra o resto seguir, e a lista vai pra revisao no CPS.
        geral = estado.get("estoque_produto")
        if geral is not None and not _igual(geral, estado.get("qtde_total")):
            recusados.append({"cod_produto": cod, "motivo": "Estoque geral do ERP difere da soma dos depósitos - revisar no CPS."})
            continue
        antes = retrato.get(cod)
        if antes and not _igual(antes.qtde_total, estado.get("qtde_total")):
            recusados.append({"cod_produto": cod, "motivo": "Saldo do ERP mudou desde a abertura - revise antes de enviar."})
            continue
        linha = {
            "cod_produto": cod,
            "estoque_esperado": estado.get("qtde_total"),
            "versao": estado.get("versao"),
            "qtde_inventariada": round(sum(i.qtde_final or 0 for i in itens), 6),
        }
        if com_lote(itens[0].tipo_controle):
            linha["lotes"] = [{"nome": i.lote, "qtde": round(i.qtde_final, 6)}
                              for i in itens if i.lote and (i.qtde_final or 0) > TOLERANCIA]
        linhas.append(linha)
    payloads = []
    for n, inicio in enumerate(range(0, len(linhas), MAX_PRODUTOS_POR_ENVIO), start=1):
        payloads.append({
            "referencia": f"SYNC-INVGERAL-{inv.id}-{n:02d}",
            "cod_empresa": cps.EMPRESA, "cod_deposito": cps.DEPOSITO, "operador": operador[:60],
            "itens": linhas[inicio:inicio + MAX_PRODUTOS_POR_ENVIO],
        })
    return payloads, recusados


def _registrar(inv, tipo, payload, usuario, status_http, resposta, status) -> Envio:
    envio = Envio(inventario_id=inv.id, tipo=tipo, referencia=payload["referencia"],
                  payload=json.dumps(payload, ensure_ascii=False), enviado_por=usuario,
                  http_status=status_http, status=status,
                  resposta=json.dumps(resposta, ensure_ascii=False) if not isinstance(resposta, str) else resposta)
    db.session.add(envio)
    return envio


def simular_envio(inv: Inventario, usuario: str) -> dict:
    """Le o ERP de novo, monta os lotes de 500 e chama /simular (nao grava)."""
    if inv.status != Inventario.STATUS_FINALIZADO:
        raise ValueError("Finalize o inventário (todas as contagens encerradas) antes de enviar ao ERP.")
    atual = {int(p["cod_produto"]): p for p in cps.listar_deposito()}
    payloads, recusados = _payloads(inv, atual, usuario)
    resultados = []
    for payload in payloads:
        try:
            status_http, resposta = cps.simular(payload)
            status = (resposta or {}).get("status") if isinstance(resposta, dict) else None
        except cps.ErroApiInventario as exc:
            status_http, resposta, status = exc.status, exc.corpo or str(exc), "ERRO"
        _registrar(inv, "simular", payload, usuario, status_http, resposta, status or "ERRO")
        resultados.append({"referencia": payload["referencia"], "produtos": len(payload["itens"]),
                           "status": status or "ERRO", "http": status_http, "resposta": resposta})
    db.session.commit()
    return {"simulacoes": resultados, "recusados_pelo_sync": recusados}


def aplicar_envio(inv: Inventario, usuario: str) -> dict:
    """Grava no CPS reenviando EXATAMENTE os payloads simulados por ultimo
    (mesma referencia e versao - se cair no meio, repetir nao duplica)."""
    if inv.status != Inventario.STATUS_FINALIZADO:
        raise ValueError("Só dá pra aplicar um inventário finalizado e ainda não enviado.")
    simulados: dict[str, Envio] = {}
    for envio in inv.envios:
        if envio.tipo == "simular":
            simulados[envio.referencia] = envio
    if not simulados:
        raise ValueError("Simule o envio antes de aplicar no ERP.")
    falhas = [r for r, e in simulados.items() if e.status != "SIMULADO_SEM_GRAVAR"]
    if falhas:
        raise ValueError(f"A simulação não passou em {len(falhas)} lote(s) de envio - revise o retorno antes de aplicar.")
    resultados, tudo_ok = [], True
    for referencia, envio in sorted(simulados.items()):
        payload = json.loads(envio.payload)
        try:
            status_http, resposta = cps.aplicar(payload)
            status = (resposta or {}).get("status") if isinstance(resposta, dict) else None
        except cps.ErroApiInventario as exc:
            status_http, resposta, status = exc.status, exc.corpo or str(exc), "ERRO"
        status = status or "ERRO"
        tudo_ok = tudo_ok and status == "APLICADO"
        _registrar(inv, "aplicar", payload, usuario, status_http, resposta, status)
        resultados.append({"referencia": referencia, "status": status, "http": status_http, "resposta": resposta})
    if tudo_ok:
        inv.status = Inventario.STATUS_ENVIADO
    db.session.commit()
    return {"aplicacoes": resultados, "concluido": tudo_ok}
