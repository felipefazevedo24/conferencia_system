"""Inventario geral - regras da contagem (definidas pela Logistica em 15-19/10/2026).

  - Abertura: le a API do CPS e congela o retrato. Entram os produtos com
    saldo (sem lote: uma linha; com lote: uma linha por lote com saldo). O
    estoque fica parado durante o inventario, entao a 1a contagem e' estatica.
  - 1a contagem = saldo do sistema. 2a e 3a: cegas e de pessoas diferentes,
    por LOCAL: o operador le local + item (+ lote) + quantidade, e a contagem
    do item e' a soma dos locais (o ERP nao tem saldo por local). A 2a e' uma
    varredura livre; o gestor encerra e o que nao bateu vai pra 3a, dirigida
    aos locais onde o item foi lido (21/10/2026).
      2a = 1a           -> Confere com o sistema
      2a != 1a          -> pede a 3a
      3a = 1a           -> Confere com o sistema (a 2a e' ignorada)
      3a = 2a (!= 1a)   -> Divergencia confirmada (vale o contado)
      nenhuma bate      -> Sem correspondencia -> "limpar e recontar"
  - Lido no fisico sem saldo no sistema: entra na hora com 1a contagem = 0.
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
    InventarioGeralLeitura as Leitura,
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


def _leituras(item: Item, rodada: int) -> list[Leitura]:
    """Leituras que valem pra rodada (as da recontagem atual do item)."""
    return [l for l in item.leituras if l.rodada == rodada and l.recontagem == (item.recontagens or 0)]


def _leitores(leituras) -> str:
    return ", ".join(sorted({l.lido_por for l in leituras}))[:100]


def _rodada_do_item(item: Item) -> int:
    if item.status == Item.STATUS_PENDENTE:
        return 2
    if item.status == Item.STATUS_AGUARDA_3A:
        return 3
    raise ValueError(f"Este item já foi apurado ({item.status}) - não aceita mais leitura. Fale com o gestor.")


def achar_produto(inv: Inventario, codigo) -> Produto | None:
    """Pelo codigo interno (o que esta' na etiqueta) ou pelo cod_produto do ERP."""
    codigo = _txt(codigo)
    if not codigo:
        return None
    produto = (Produto.query.filter(Produto.inventario_id == inv.id)
               .filter(db.func.upper(Produto.codigo_interno) == codigo.upper()).first())
    if not produto and codigo.isdigit():
        produto = Produto.query.filter_by(inventario_id=inv.id, cod_produto=int(codigo)).first()
    return produto


def registrar_leitura(inv: Inventario, local, codigo, lote, quantidade, usuario: str) -> Leitura:
    """Operador leu local + item (+ lote) + quantidade. Vale pra rodada em
    que o item esta' (2a se pendente, 3a se aguardando). Item/lote sem saldo
    no sistema entra na hora com 1a contagem = 0."""
    _exigir_aberto(inv)
    local = _txt(local).upper()[:80]
    if not local:
        raise ValueError("Leia ou digite o local.")
    produto = achar_produto(inv, codigo)
    if not produto:
        raise ValueError("Item não encontrado entre os produtos do ERP (código errado, inativo ou fora do depósito).")
    lote = _txt(lote)[:120] or None
    if com_lote(produto.tipo_controle):
        if not lote:
            raise ValueError("Este item é controlado por lote - informe o lote.")
    else:
        lote = None
    qtde = _qtde(quantidade)
    if qtde <= TOLERANCIA:
        raise ValueError("A quantidade precisa ser maior que zero.")

    item = next((i for i in Item.query.filter_by(inventario_id=inv.id, cod_produto=produto.cod_produto).all()
                 if (i.lote or "").upper() == (lote or "").upper()), None)
    if not item:
        item = Item(
            inventario_id=inv.id, cod_produto=produto.cod_produto, codigo_interno=produto.codigo_interno,
            descricao=produto.descricao, unidade=produto.unidade, tipo_controle=produto.tipo_controle, lote=lote,
            qtde_sistema=0, adicionado_manual=True, adicionado_por=usuario,
        )
        db.session.add(item)
        db.session.flush()
    rodada = _rodada_do_item(item)
    if rodada == 3 and any(l.lido_por.upper() == (usuario or "").upper() for l in _leituras(item, 2)):
        raise ValueError("A 3ª contagem deste item precisa ser feita por outra pessoa (você leu na 2ª).")
    leitura = Leitura(inventario_id=inv.id, item=item, rodada=rodada, recontagem=item.recontagens or 0,
                      local=local, quantidade=qtde, lido_por=usuario, lido_em=agora_br())
    db.session.add(leitura)
    db.session.commit()
    return leitura


def excluir_leitura(leitura: Leitura, usuario: str, gestor: bool = False) -> None:
    """Corrige leitura errada enquanto a rodada dela nao foi encerrada."""
    _exigir_aberto(leitura.item.inventario)
    if not gestor and leitura.lido_por.upper() != (usuario or "").upper():
        raise ValueError("Só quem fez a leitura pode excluí-la.")
    item = leitura.item
    aberta = (leitura.recontagem == (item.recontagens or 0)
              and ((leitura.rodada == 2 and item.status == Item.STATUS_PENDENTE)
                   or (leitura.rodada == 3 and item.status == Item.STATUS_AGUARDA_3A)))
    if not aberta:
        raise ValueError("A contagem desta leitura já foi encerrada - não dá mais para excluir.")
    db.session.delete(leitura)
    db.session.commit()


def encerrar_rodada(inv: Inventario, rodada: int, usuario: str) -> dict:
    """Gestor fecha a 2a ou a 3a: soma as leituras de todos os locais e aplica
    a regra. Item sem leitura na rodada conta como 0 (nao achado)."""
    _exigir_aberto(inv)
    agora = agora_br()
    apurados = {}
    if rodada == 2:
        for item in [i for i in inv.itens if i.status == Item.STATUS_PENDENTE]:
            leituras = _leituras(item, 2)
            item.contagem2 = round(sum(l.quantidade for l in leituras), 6)
            item.contagem2_por, item.contagem2_em = _leitores(leituras) or None, agora
            if _igual(item.contagem2, item.qtde_sistema):
                item.status, item.qtde_final = Item.STATUS_CONFERE, item.qtde_sistema
            else:
                item.status = Item.STATUS_AGUARDA_3A
            apurados[item.status] = apurados.get(item.status, 0) + 1
    elif rodada == 3:
        for item in [i for i in inv.itens if i.status == Item.STATUS_AGUARDA_3A]:
            leituras = _leituras(item, 3)
            item.contagem3 = round(sum(l.quantidade for l in leituras), 6)
            item.contagem3_por, item.contagem3_em = _leitores(leituras) or None, agora
            if _igual(item.contagem3, item.qtde_sistema):
                item.status, item.qtde_final = Item.STATUS_CONFERE, item.qtde_sistema
            elif _igual(item.contagem3, item.contagem2):
                item.status, item.qtde_final = Item.STATUS_DIVERGENCIA, item.contagem3
            else:
                item.status, item.qtde_final = Item.STATUS_SEM_CORRESPONDENCIA, None
            apurados[item.status] = apurados.get(item.status, 0) + 1
    else:
        raise ValueError("Rodada inválida.")
    if not apurados:
        raise ValueError(f"Não há item na {rodada}ª contagem para encerrar.")
    db.session.commit()
    return apurados


def recontar(item: Item, usuario: str) -> Item:
    """Sem correspondencia: limpa a 2a e a 3a e o item volta pra 2a contagem.
    As leituras antigas ficam (outra recontagem) so' pra mostrar os locais."""
    _exigir_aberto(item.inventario)
    if item.status != Item.STATUS_SEM_CORRESPONDENCIA:
        raise ValueError("Só dá pra limpar e recontar um item sem correspondência entre as contagens.")
    for campo in ("contagem2", "contagem2_por", "contagem2_em", "contagem3", "contagem3_por", "contagem3_em", "qtde_final"):
        setattr(item, campo, None)
    item.status = Item.STATUS_PENDENTE
    item.recontagens = (item.recontagens or 0) + 1
    db.session.commit()
    return item


def locais_anteriores(item: Item) -> list[str]:
    """Onde procurar na contagem dirigida (sem quantidade - contagem cega):
    3a -> locais da 2a; recontagem -> locais da tentativa anterior."""
    if item.status == Item.STATUS_AGUARDA_3A:
        leituras = _leituras(item, 2)
    else:
        anterior = (item.recontagens or 0) - 1
        leituras = [l for l in item.leituras if l.recontagem == anterior]
    return sorted({l.local for l in leituras})


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
