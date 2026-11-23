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
import re
from datetime import datetime

from ..extensions import db
from ..models import (
    InventarioGeral as Inventario,
    InventarioGeralEnvio as Envio,
    InventarioGeralItem as Item,
    InventarioGeralLeitura as Leitura,
    InventarioGeralProduto as Produto,
)
from ..tempo import agora_br
from ..models import InventarioGeralResultado as Resultado
from ..models import InventarioGeralCancelamento as Cancelamento
from ..models import InventarioGeralRecusa as Recusa
from ..models import InventarioGeralDistribuicao as Distribuicao
from ..models import InventarioGeralDistribuicaoItem as DistItem
from ..models import InventarioGeralRetratoErp as RetratoErp
from . import erp_estoque_service as erp
from . import estoque_espelho_service as espelho
from . import inventario_ciclico_service as ciclico
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
FECHADOS = (Inventario.STATUS_ENCERRADO, Inventario.STATUS_CANCELADO)


def inventario_ativo() -> Inventario | None:
    """O que ainda nao foi encerrado nem cancelado (so' um por vez - estoque parado)."""
    return (Inventario.query
            .filter(Inventario.status.notin_(FECHADOS))
            .order_by(Inventario.id.desc()).first())


def numeros() -> dict[int, str]:
    """IG-AAAA-NNNN pela ordem de abertura no ano. Calculado, nao gravado:
    inventario geral nao se exclui, entao a sequencia nao muda."""
    resultado, por_ano = {}, {}
    for inv_id, aberto_em in db.session.query(Inventario.id, Inventario.aberto_em).order_by(Inventario.id):
        ano = (aberto_em or datetime.now()).year
        por_ano[ano] = por_ano.get(ano, 0) + 1
        resultado[inv_id] = f"IG-{ano}-{por_ano[ano]:04d}"
    return resultado


def numero(inv: Inventario) -> str:
    return numeros().get(inv.id, f"IG-{inv.id}")


def numero_confere(inv: Inventario, digitado) -> bool:
    """O operador informa o numero do inventario aberto na Gestao: vale o
    numero inteiro (IG-2026-0003) ou so' a sequencia (0003 / 3)."""
    digitado = _txt(digitado).upper().replace(" ", "")
    if not digitado:
        return False
    certo = numero(inv)
    if digitado == certo.upper():
        return True
    return digitado.isdigit() and int(digitado) == int(certo.rsplit("-", 1)[-1])


def abrir(usuario: str, descricao: str = "") -> Inventario:
    if inventario_ativo():
        raise ValueError("Já existe um inventário geral em andamento - encerre ou cancele antes de abrir outro.")
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
    _gravar_inconsistentes(inv, produtos)
    db.session.commit()
    return inv


def _gravar_inconsistentes(inv: Inventario, produtos: list[dict]) -> RetratoErp:
    cods = sorted(int(p["cod_produto"]) for p in produtos if _inconsistente(p))
    retrato = RetratoErp(inventario_id=inv.id, inconsistentes=json.dumps(cods), lido_em=agora_br())
    db.session.add(retrato)
    return retrato


def _inconsistente(estado: dict) -> bool:
    geral = estado.get("estoque_produto")
    return geral is not None and not _igual(geral, estado.get("qtde_total"))


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
    leitura = _nova_leitura(inv, local, codigo, lote, quantidade, usuario)
    db.session.commit()
    return leitura


def registrar_leituras(inv: Inventario, local, codigo, lotes, usuario: str) -> list[Leitura]:
    """Varios lotes do mesmo item no mesmo local, numa leitura so' (como no
    inventario rotativo). Cada lote vira uma leitura propria; tudo ou nada."""
    lotes = [l for l in (lotes or []) if isinstance(l, dict)]
    if not lotes:
        raise ValueError("Informe ao menos um lote.")
    nomes = [_txt(l.get("lote")).upper() for l in lotes]
    repetido = next((n for n in nomes if n and nomes.count(n) > 1), None)
    if repetido:
        raise ValueError(f"O lote {repetido} foi informado mais de uma vez - some as quantidades numa linha só.")
    try:
        leituras = [_nova_leitura(inv, local, codigo, l.get("lote"), l.get("quantidade"), usuario) for l in lotes]
    except ValueError:
        db.session.rollback()
        raise
    db.session.commit()
    return leituras


def _nova_leitura(inv: Inventario, local, codigo, lote, quantidade, usuario: str) -> Leitura:
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

    item = next((i for i in Item.query.filter_by(inventario_id=inv.id, cod_produto=produto.cod_produto).all()
                 if (i.lote or "").upper() == (lote or "").upper()), None)
    if not item and qtde <= TOLERANCIA:
        # Zero de item sem saldo criaria uma linha 0 x 0 "lida no fisico" so' de ruido.
        raise ValueError(f"{'Lote ' + lote if lote else 'Item'} sem saldo no sistema: não precisa registrar zero.")
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
def _payloads(inv: Inventario, inconsistentes: set[int], operador: str, rodada: int = 1,
              ja_aplicados: set[int] | None = None) -> tuple[list[dict], list[dict]]:
    """Agrupa as linhas por produto (lote: todos os lotes contados, total =
    soma). estoque_esperado/versao vem do PRINT da abertura: no inventario
    geral o estoque fica parado, entao o Sync so' pre-envia o que ja' tem,
    sem reler o ERP. Produto ja' gravado no ERP (rodada anterior) nao vai de
    novo. Devolve (payloads, recusados_pelo_sync)."""
    por_produto: dict[int, list[Item]] = {}
    for item in inv.itens:
        if item.cod_produto not in (ja_aplicados or set()):
            por_produto.setdefault(item.cod_produto, []).append(item)
    linhas, recusados = [], []
    retrato = {p.cod_produto: p for p in Produto.query.filter_by(inventario_id=inv.id).all()}
    for cod, itens in sorted(por_produto.items()):
        antes = retrato.get(cod)
        if not antes:
            recusados.append({"cod_produto": cod, "motivo": "Produto sem saldo/versão no print da abertura."})
            continue
        # O CPS recusa (409) produto cujo estoque geral nao bate com a soma
        # dos depositos - e um recusado derruba o lote de 500 inteiro. Visto
        # em 584 dos 2.051 itens com saldo na leitura de 19/10/2026: separa
        # aqui pra o resto seguir, e a lista vai pra revisao no CPS.
        if cod in inconsistentes:
            recusados.append({"cod_produto": cod, "motivo": "Estoque geral do ERP difere da soma dos depósitos - revisar no CPS."})
            continue
        linha = {
            "cod_produto": cod,
            "estoque_esperado": antes.qtde_total,
            "versao": antes.versao,
            "qtde_inventariada": round(sum(i.qtde_final or 0 for i in itens), 6),
        }
        if com_lote(itens[0].tipo_controle):
            linha["lotes"] = [{"nome": i.lote, "qtde": round(i.qtde_final, 6)}
                              for i in itens if i.lote and (i.qtde_final or 0) > TOLERANCIA]
        linhas.append(linha)
    payloads = []
    for n, inicio in enumerate(range(0, len(linhas), MAX_PRODUTOS_POR_ENVIO), start=1):
        payloads.append({
            "referencia": _referencia(inv, rodada, n),
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


# Cada "Simular" e' uma rodada; o Aplicar usa so' a rodada mais recente (uma
# simulacao refeita com menos lotes nao pode reaplicar um lote antigo). A 1a
# rodada mantem a referencia original; as seguintes levam -R<n>, pra nao
# reusar no CPS uma referencia ja' gravada.
_RE_RODADA = re.compile(r"-R(\d+)-\d+$")


def _referencia(inv: Inventario, rodada: int, lote: int) -> str:
    return f"SYNC-INVGERAL-{inv.id}-{lote:02d}" if rodada <= 1 else f"SYNC-INVGERAL-{inv.id}-R{rodada}-{lote:02d}"


def _rodada(referencia: str) -> int:
    m = _RE_RODADA.search(referencia or "")
    return int(m.group(1)) if m else 1


def rodada_atual(inv: Inventario) -> int | None:
    rodadas = [_rodada(e.referencia) for e in inv.envios if e.tipo == "simular"]
    return max(rodadas) if rodadas else None


def envios_da_rodada(inv: Inventario) -> dict[str, dict]:
    """{referencia: {"simulacao": Envio, "aplicacoes": [Envio...]}} da rodada atual."""
    rodada = rodada_atual(inv)
    lotes: dict[str, dict] = {}
    if rodada is None:
        return lotes
    for e in inv.envios:
        if _rodada(e.referencia) != rodada:
            continue
        lote = lotes.setdefault(e.referencia, {"simulacao": None, "aplicacoes": []})
        if e.tipo == "simular":
            lote["simulacao"] = e
        else:
            lote["aplicacoes"].append(e)
    return dict(sorted(lotes.items()))


def _lote_aplicado(lote: dict) -> bool:
    return any(a.status == "APLICADO" for a in lote["aplicacoes"])


def produtos_aplicados(inv: Inventario) -> set[int]:
    """cod_produto ja' gravados no CPS, em qualquer rodada."""
    feitos: set[int] = set()
    for e in inv.envios:
        if e.tipo == "aplicar" and e.status == "APLICADO":
            try:
                feitos.update(int(i["cod_produto"]) for i in json.loads(e.payload).get("itens") or [])
            except (ValueError, KeyError, TypeError):
                continue
    return feitos


def simular_envio(inv: Inventario, usuario: str) -> dict:
    """Monta os lotes de 500 com o print da abertura e chama /simular (nao grava)."""
    if inv.status != Inventario.STATUS_FINALIZADO:
        raise ValueError("Finalize o inventário (todas as contagens encerradas) antes de enviar ao ERP.")
    retrato_erp = RetratoErp.query.filter_by(inventario_id=inv.id).first()
    if not retrato_erp:
        # Inventario aberto antes do print guardar os inconsistentes: le o ERP
        # uma unica vez pra montar a lista; dai' em diante so' o print.
        retrato_erp = _gravar_inconsistentes(inv, cps.listar_deposito())
    inconsistentes = set(json.loads(retrato_erp.inconsistentes or "[]"))
    rodada = (rodada_atual(inv) or 0) + 1
    # Depois de uma aplicacao parcial, a rodada nova leva so' o que falta
    # gravar (com a versao atual - reaplicar o pacote velho daria 409 de novo).
    payloads, recusados = _payloads(inv, inconsistentes, usuario, rodada, produtos_aplicados(inv))
    # Retrato do que o Sync deixou de fora nesta rodada (aparece no painel de envio).
    Recusa.query.filter_by(inventario_id=inv.id).delete()
    retrato = {p.cod_produto: p for p in Produto.query.filter_by(inventario_id=inv.id).all()}
    for r in recusados:
        prod = retrato.get(r["cod_produto"])
        r["codigo_interno"] = prod.codigo_interno if prod else None
        db.session.add(Recusa(inventario_id=inv.id, cod_produto=r["cod_produto"], motivo=r["motivo"][:300],
                              codigo_interno=r["codigo_interno"], descricao=prod.descricao if prod else None,
                              simulado_em=agora_br()))
    if not payloads:
        # Marca a rodada mesmo sem lote: o Aplicar nao pode cair numa rodada antiga.
        vazio = {"referencia": _referencia(inv, rodada, 1), "itens": []}
        _registrar(inv, "simular", vazio, usuario, None, "Nenhum produto para enviar - todos ficaram fora (ver lista).",
                   "SEM_ITENS")
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
    lotes = envios_da_rodada(inv)
    if not lotes:
        raise ValueError("Simule o envio antes de aplicar no ERP.")
    if all(l["simulacao"].status == "SEM_ITENS" for l in lotes.values()):
        raise ValueError("A última simulação não teve nenhum produto para enviar - veja os itens que ficaram de fora.")
    falhas = [r for r, l in lotes.items() if l["simulacao"].status != "SIMULADO_SEM_GRAVAR"]
    if falhas:
        raise ValueError(f"A simulação não passou em {len(falhas)} lote(s) de envio - revise o retorno antes de aplicar.")
    resultados, tudo_ok = [], True
    for referencia, lote in lotes.items():
        if _lote_aplicado(lote):  # reenvio depois de falha parcial: so' os pendentes
            continue
        payload = json.loads(lote["simulacao"].payload)
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


# ── Encerramento e indicadores ─────────────────────────────────────────
def _custos_erp() -> dict:
    try:
        return erp.buscar_estoque_grv(forcar_atualizacao=True)
    except Exception as exc:  # bridge fora/sem config: nao encerra sem o financeiro
        raise ValueError(f"Não consegui ler o custo médio no ERP para calcular o financeiro ({exc}). Tente de novo.")


def encerrar(inv: Inventario, usuario: str) -> Resultado:
    """Fecha o inventario em qualquer fase (inclusive sem enviar ao ERP -
    inventario de teste). Congela custo e indicadores e libera abrir outro."""
    if inv.status in FECHADOS:
        raise ValueError(f"Este inventário já está {inv.status.lower()}.")
    estoque = _custos_erp()
    concluidas = [i for i in inv.itens if i.status in (Item.STATUS_CONFERE, Item.STATUS_DIVERGENCIA)]
    custos, inicial, final, dif_abs, sem_custo = {}, 0.0, 0.0, 0.0, 0
    for item in concluidas:
        codigo = (item.codigo_interno or "").upper()
        if codigo not in custos:
            custos[codigo] = erp.custo_medio_para(codigo, "", estoque)
        custo = custos[codigo]
        if custo is None:
            sem_custo += 1
            continue
        inicial += item.qtde_sistema * custo
        final += (item.qtde_final or 0) * custo
        dif_abs += abs((item.qtde_final or 0) - item.qtde_sistema) * custo
    conferem = sum(1 for i in concluidas if i.status == Item.STATUS_CONFERE)
    resultado = Resultado(
        inventario_id=inv.id, encerrado_em=agora_br(), encerrado_por=usuario,
        enviado_ao_erp=inv.status == Inventario.STATUS_ENVIADO,
        linhas_total=len(inv.itens), linhas_contadas=len(concluidas), linhas_conferem=conferem,
        linhas_sem_custo=sem_custo, financeiro_inicial=round(inicial, 2), financeiro_final=round(final, 2),
        diferenca_absoluta=round(dif_abs, 2),
        acuracidade_itens=round(conferem / len(concluidas) * 100, 2) if concluidas else None,
        acuracidade_financeira=round(max(0.0, 1 - dif_abs / inicial) * 100, 2) if inicial > TOLERANCIA else None,
        custos=json.dumps(custos, ensure_ascii=False),
    )
    db.session.add(resultado)
    inv.status = Inventario.STATUS_ENCERRADO
    db.session.commit()
    return resultado


def historico() -> list[Inventario]:
    """Tela de Gestao: todos, o em andamento no topo junto com os fechados."""
    return Inventario.query.order_by(Inventario.id.desc()).all()


def aplicado_no_erp(inv: Inventario) -> bool:
    """Algum lote de envio ja' foi gravado no CPS (mesmo que outro tenha
    falhado e o inventario continue Finalizado)."""
    return inv.status == Inventario.STATUS_ENVIADO or any(
        e.tipo == "aplicar" and e.status == "APLICADO" for e in inv.envios)


def cancelar(inv: Inventario, motivo, usuario: str) -> Cancelamento:
    """Desiste do inventario antes de aplicar no ERP. Fica no historico como
    Cancelado (numero, linhas e leituras mantidos) e libera abrir outro."""
    if inv.status in FECHADOS:
        raise ValueError(f"Este inventário já está {inv.status.lower()}.")
    if aplicado_no_erp(inv):
        raise ValueError("O resultado já foi aplicado no ERP - não dá mais para cancelar. Encerre o inventário.")
    motivo = _txt(motivo)
    if not motivo:
        raise ValueError("Informe o motivo do cancelamento.")
    cancelamento = Cancelamento(inventario_id=inv.id, motivo=motivo[:2000], cancelado_por=usuario,
                                cancelado_em=agora_br())
    db.session.add(cancelamento)
    inv.status = Inventario.STATUS_CANCELADO
    db.session.commit()
    return cancelamento


# ── Distribuicao (molde do ciclico; so' orienta) ─────────────────────
def _chave(codigo, endereco) -> tuple[str, str]:
    return _txt(codigo).upper(), _txt(endereco).upper()


def opcoes_distribuicao() -> dict:
    """Filtros sao os do ciclico, lidos do espelho do GRV (o CPS nao tem
    familia/grupo/endereco)."""
    estoque = espelho.carregar()
    return {"familias": ciclico.opcoes_familias(estoque), "enderecos": ciclico.opcoes_enderecos(estoque),
            "espelho": espelho.status()}


def _linhas_distribuidas(inv: Inventario) -> dict[tuple[str, str], str]:
    """{(codigo, endereco): conferente} das distribuicoes nao canceladas."""
    rows = (db.session.query(DistItem.codigo, DistItem.endereco, Distribuicao.conferente)
            .join(Distribuicao)
            .filter(Distribuicao.inventario_id == inv.id, Distribuicao.cancelado_em.is_(None)).all())
    return {_chave(c, e): conf for c, e, conf in rows}


def previa_distribuicao(inv: Inventario, criterio, familia, grupo, codigos_texto, endereco) -> dict:
    """Linhas do filtro; as que ja estao com alguem vem com o conferente e
    nao podem ser escolhidas de novo (cancele a distribuicao antes)."""
    criterio, familia, grupo, codigos, endereco = ciclico._validar_filtro(criterio, familia, grupo, codigos_texto, endereco)
    linhas, nao_encontrados = ciclico.montar_linhas(ciclico._estoque(), criterio, familia, grupo, codigos, endereco)
    ja = _linhas_distribuidas(inv)
    for l in linhas:
        l["conferente_atual"] = ja.get(_chave(l["codigo"], l["endereco"]))
    return {"linhas": linhas, "nao_encontrados": nao_encontrados}


def distribuir(inv: Inventario, criterio, familia, grupo, codigos_texto, endereco, conferente: str,
               usuario: str, selecionados) -> dict:
    """Distribui so' as linhas escolhidas na previa, refeitas aqui contra o
    espelho: linha que saiu do filtro ou ja foi para outro fica de fora."""
    _exigir_aberto(inv)
    criterio, familia, grupo, codigos, endereco = ciclico._validar_filtro(criterio, familia, grupo, codigos_texto, endereco)
    conferente = _txt(conferente)
    if not conferente:
        raise ValueError("Escolha o conferente.")
    escolhidas = {_chave(x.get("codigo"), x.get("endereco")) for x in (selecionados or []) if isinstance(x, dict)}
    if not escolhidas:
        raise ValueError("Selecione ao menos um item.")
    linhas, _ = ciclico.montar_linhas(ciclico._estoque(), criterio, familia, grupo, codigos, endereco)
    ja = _linhas_distribuidas(inv)
    novas = [l for l in linhas if _chave(l["codigo"], l["endereco"]) in escolhidas
             and _chave(l["codigo"], l["endereco"]) not in ja]
    if not novas:
        raise ValueError("Os itens selecionados não estão mais disponíveis: já foram para outro conferente ou saíram do filtro.")
    dist = Distribuicao(
        inventario_id=inv.id, criterio=criterio, conferente=conferente, criado_por=usuario, criado_em=agora_br(),
        familia=(familia or None) if criterio in {"familia", "familia_grupo"} else None,
        grupo=(grupo or None) if criterio in {"grupo", "familia_grupo"} else None,
        codigos="\n".join(codigos) if criterio == "codigo" else None,
        endereco=endereco[:120] if criterio == "endereco" else None,
    )
    for l in novas:
        dist.itens.append(DistItem(codigo=l["codigo"][:60], descricao=l["descricao"][:300],
                                   unidade=l["unidade"][:20], endereco=l["endereco"][:120]))
    db.session.add(dist)
    db.session.commit()
    return {"distribuicao": dist, "total": len(novas), "fora": len(escolhidas) - len(novas)}


def cancelar_distribuicao(dist: Distribuicao, usuario: str) -> None:
    """As linhas saem da lista do conferente e podem ser distribuidas de novo.
    Leitura ja feita nao muda."""
    _exigir_aberto(dist.inventario)
    if dist.cancelado_em:
        raise ValueError("Distribuição já cancelada.")
    dist.cancelado_em, dist.cancelado_por = agora_br(), usuario
    db.session.commit()


def distribuicoes(inv: Inventario) -> list[Distribuicao]:
    return Distribuicao.query.filter_by(inventario_id=inv.id).order_by(Distribuicao.id.desc()).all()


def numeros_distribuicao(inv: Inventario) -> dict[int, str]:
    """D01, D02... pela ordem dentro do inventario (calculado, nao gravado)."""
    ids = [i for (i,) in db.session.query(Distribuicao.id).filter_by(inventario_id=inv.id).order_by(Distribuicao.id)]
    return {i: f"D{n:02d}" for n, i in enumerate(ids, start=1)}


def locais_lidos(inv: Inventario, usuario: str | None = None) -> dict[str, set[str]]:
    """{codigo: {locais}} com leitura no inventario (de todos ou so' do usuario)."""
    q = (db.session.query(Item.codigo_interno, Leitura.local)
         .join(Leitura, Leitura.item_id == Item.id)
         .filter(Leitura.inventario_id == inv.id))
    if usuario:
        q = q.filter(db.func.upper(Leitura.lido_por) == usuario.upper())
    lidos: dict[str, set[str]] = {}
    for codigo, local in q.distinct():
        lidos.setdefault(_txt(codigo).upper(), set()).add(_txt(local).upper())
    return lidos


def linha_lida(linha: DistItem, lidos: dict[str, set[str]]) -> bool:
    """Lida = o codigo tem leitura no endereco (codigo sem endereco: em qualquer local)."""
    locais = lidos.get(_txt(linha.codigo).upper())
    return bool(locais) and (not linha.endereco or _txt(linha.endereco).upper() in locais)


def minha_lista(inv: Inventario, usuario: str) -> list[DistItem]:
    return (DistItem.query.join(Distribuicao)
            .filter(Distribuicao.inventario_id == inv.id, Distribuicao.cancelado_em.is_(None),
                    db.func.upper(Distribuicao.conferente) == (usuario or "").upper())
            .order_by(DistItem.endereco, DistItem.codigo).all())
