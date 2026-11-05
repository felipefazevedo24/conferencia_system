"""Espelho do estoque do GRV no banco do Sync (EstoqueGrvEspelho).

Baixar o estoque inteiro pela bridge leva tempo (21 mil linhas atravessando
ate' o PythonAnywhere; a consulta no CPS em si leva meio segundo). A tela le
o espelho e abre na hora; quando ele passa de IDADE_MAXIMA_MIN, a propria
tela pede a atualizacao por tras. A atualizacao compara com o que esta
gravado e so' mexe no que mudou: inclui o novo, altera o diferente e tira o
que sumiu do GRV.

O espelho serve para montar listas (familias, grupos, itens a distribuir).
Saldo para comparar contagem continua sendo consultado na hora.
"""
from __future__ import annotations

from datetime import timedelta

from ..extensions import db
from ..models import EstoqueGrvEspelho as Linha, EstoqueGrvEspelhoSync as Sync
from ..tempo import agora_br
from . import erp_estoque_service as erp

IDADE_MAXIMA_MIN = 10
# Atualizacao "em andamento" ha mais tempo que isso caiu no meio (worker
# reiniciado): deixa outra comecar.
ANDAMENTO_EXPIRA_MIN = 10
CAMPOS = ("descricao", "unidade", "familia", "grupo", "grupo_nome", "qtde_total",
          "qtde_disponivel", "qtde_reservada", "custo_medio", "tipo_controle")


def _texto(valor, limite: int) -> str:
    return str(valor or "").strip()[:limite]


def _num(valor):
    return float(valor) if valor is not None else None


def _igual(atual, novo) -> bool:
    # FLOAT do MySQL e' precisao simples: o lido de volta nao bate bit a bit
    # com o double do GRV, e sem tolerancia toda linha pareceria alterada.
    if isinstance(novo, float) or isinstance(atual, float):
        if atual is None or novo is None:
            return atual is None and novo is None
        return abs(float(atual) - float(novo)) <= 1e-6 * max(1.0, abs(float(novo)))
    return (atual or None) == (novo or None)


def _linhas_do_grv(estoque: dict) -> dict[tuple[str, str], dict]:
    """Uma linha por codigo + localizacao, no formato da tabela."""
    novas: dict[tuple[str, str], dict] = {}
    por_local = estoque.get("por_local") or {}
    for codigo, ag in (estoque.get("por_codigo") or {}).items():
        base = {
            "descricao": _texto(ag.get("item"), 300),
            "unidade": _texto(ag.get("unidade"), 20),
            "familia": _texto(ag.get("familia"), 160),
            "grupo": _texto(ag.get("grupo"), 20),
            "grupo_nome": _texto(ag.get("grupo_nome"), 160),
            "tipo_controle": _texto(ag.get("tipo_controle"), 40) or None,
        }
        locais = sorted(set(ag.get("localizacoes") or []))
        if not locais:
            novas[(codigo[:60], "")] = {
                **base,
                "qtde_total": float(ag.get("qtde_total") or 0),
                "qtde_disponivel": float(ag.get("qtde_disponivel") or 0),
                "qtde_reservada": float(ag.get("qtde_reservada") or 0),
                "custo_medio": _num(ag.get("custo_medio")),
            }
        for local in locais:
            bruto = por_local.get(f"{codigo}|{local}") or {}
            novas[(codigo[:60], local[:120])] = {
                **base,
                "qtde_total": float(bruto.get("qtde_total") or 0),
                "qtde_disponivel": float(bruto.get("qtde_disponivel") or 0),
                "qtde_reservada": float(bruto.get("qtde_reservada") or 0),
                "custo_medio": _num(bruto.get("custo_medio")),
            }
    return novas


def _ultima(status: str | None = None):
    q = Sync.query
    if status:
        q = q.filter(Sync.status == status)
    return q.order_by(Sync.id.desc()).first()


def status() -> dict:
    ok = _ultima(Sync.STATUS_OK)
    ultima = _ultima()
    agora = agora_br()
    andamento = bool(
        ultima and ultima.status == Sync.STATUS_ANDAMENTO
        and ultima.iniciado_em > agora - timedelta(minutes=ANDAMENTO_EXPIRA_MIN)
    )
    idade = int((agora - ok.concluido_em).total_seconds() // 60) if ok and ok.concluido_em else None
    return {
        "atualizado_em": ok.concluido_em.strftime("%d/%m/%Y %H:%M") if ok and ok.concluido_em else None,
        "idade_min": idade,
        "vazio": ok is None,
        "desatualizado": ok is None or idade is None or idade >= IDADE_MAXIMA_MIN,
        "em_andamento": andamento,
        "erro": ultima.erro if ultima and ultima.status == Sync.STATUS_ERRO else None,
    }


def atualizar(usuario: str) -> dict:
    """Baixa o estoque do GRV e grava so' a diferenca. Se outra atualizacao
    ja estiver rodando, nao comeca outra."""
    if status()["em_andamento"]:
        return {"ja_em_andamento": True}
    sync = Sync(status=Sync.STATUS_ANDAMENTO, iniciado_em=agora_br(), iniciado_por=usuario)
    db.session.add(sync)
    db.session.commit()
    try:
        novas = _linhas_do_grv(erp.buscar_estoque_grv(forcar_atualizacao=True))
        agora = agora_br()
        atuais = {(r.codigo, r.localizacao): r for r in Linha.query.all()}
        inserir, alterar = [], []
        for chave, dados in novas.items():
            row = atuais.pop(chave, None)
            if row is None:
                inserir.append({"codigo": chave[0], "localizacao": chave[1], "atualizado_em": agora, **dados})
            elif not all(_igual(getattr(row, c), dados[c]) for c in CAMPOS):
                alterar.append({"id": row.id, "atualizado_em": agora, **dados})
        remover = [r.id for r in atuais.values()]
        for i in range(0, len(inserir), 1000):
            db.session.bulk_insert_mappings(Linha, inserir[i:i + 1000])
        for i in range(0, len(alterar), 1000):
            db.session.bulk_update_mappings(Linha, alterar[i:i + 1000])
        for i in range(0, len(remover), 1000):
            Linha.query.filter(Linha.id.in_(remover[i:i + 1000])).delete(synchronize_session=False)
        sync.status = Sync.STATUS_OK
        sync.linhas, sync.inseridas, sync.alteradas, sync.removidas = len(novas), len(inserir), len(alterar), len(remover)
        sync.concluido_em = agora_br()
        db.session.commit()
        return {"linhas": len(novas), "inseridas": len(inserir), "alteradas": len(alterar), "removidas": len(remover)}
    except Exception as exc:  # noqa: BLE001 - GRV fora nao pode deixar a atualizacao "em andamento" para sempre
        db.session.rollback()
        sync = db.session.get(Sync, sync.id)
        sync.status = Sync.STATUS_ERRO
        sync.erro = str(exc)[:500]
        sync.concluido_em = agora_br()
        db.session.commit()
        raise ValueError("Não deu para atualizar o estoque do GRV agora. Tente de novo em instantes.") from exc


def carregar() -> dict:
    """O espelho no mesmo formato do erp.buscar_estoque_grv. Vazio se nunca
    foi atualizado."""
    itens = [
        {
            "codigo_interno": r.codigo, "localizacao_estoque": r.localizacao, "item": r.descricao,
            "unidade": r.unidade, "familia": r.familia, "grupo": r.grupo, "grupo_nome": r.grupo_nome,
            "qtde_total": r.qtde_total, "qtde_disponivel": r.qtde_disponivel, "qtde_reservada": r.qtde_reservada,
            "custo_medio": r.custo_medio, "tipo_controle": r.tipo_controle,
        }
        for r in db.session.query(Linha).all()
    ]
    return erp._agregar_itens(itens)


def custos(codigos: list[str]) -> dict[tuple[str, str], float | None]:
    """{(codigo, localizacao): custo medio} do espelho, sem ir no GRV."""
    codigos = sorted({c for c in codigos if c})
    resultado = {}
    for i in range(0, len(codigos), 500):
        for r in Linha.query.filter(Linha.codigo.in_(codigos[i:i + 500])).all():
            resultado[(r.codigo, r.localizacao or "")] = r.custo_medio
    return resultado


def nomes_grupos() -> dict[tuple[str, str], str]:
    """{(familia, codigo do grupo): nome} para mostrar o grupo pelo nome."""
    rows = db.session.query(Linha.familia, Linha.grupo, Linha.grupo_nome).distinct().all()
    return {(f or "", g or ""): n for f, g, n in rows if n}
