"""Nucleo nativo do modulo de Producao, usando o banco/bridge do Sync."""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
from typing import Any

from ..compras import queries
from ..compras.db import fetch_all, fetch_one


STATUS_LABELS = {
    "bloqueado": "Bloqueado",
    "concluido": "Concluido",
    "disponivel": "Disponivel",
    "montagem": "Em montagem",
    "fabricacao": "Em fabricacao",
    "nao_iniciado": "Nao iniciado",
}


def _iso(value: Any) -> str | None:
    return value.isoformat() if isinstance(value, (datetime, date)) else (str(value) if value else None)


def _texto(value: Any) -> str:
    return str(value or "").strip()


def buscar_os(termo: str, limite: int = 20) -> list[dict[str, Any]]:
    termo = _texto(termo)
    if not termo:
        return []
    rows = fetch_all(
        queries.SQL_PRODUCAO_BUSCAR_OS,
        {
            "cod_empresa": 1,
            "busca": f"%{termo}%",
            "termo": termo,
            "limite": max(1, min(int(limite or 20), 50)),
        },
    )
    return [_os_payload(row) for row in rows]


def listar_os_abertas(limite: int = 100) -> list[dict[str, Any]]:
    rows = fetch_all(
        queries.SQL_PRODUCAO_OS_ABERTAS,
        {"cod_empresa": 1, "limite": max(1, min(int(limite or 100), 200))},
    )
    return [_os_payload(row) for row in rows]


def obter_estrutura(numero_os: str) -> dict[str, Any]:
    ordem = fetch_one(
        queries.SQL_PRODUCAO_BUSCAR_OS,
        {"cod_empresa": 1, "busca": numero_os, "termo": numero_os, "limite": 1},
    )
    if not ordem:
        raise LookupError(f"OS {numero_os} nao encontrada.")
    itens = fetch_all(
        queries.SQL_PRODUCAO_ESTRUTURA_OS,
        {"cod_empresa": 1, "cod_os": ordem["codigo"]},
    )
    operacoes = fetch_all(
        queries.SQL_PRODUCAO_OPERACOES_OS,
        {"cod_empresa": 1, "cod_os": ordem["codigo"]},
    )
    payload = _estrutura_payload(ordem, itens, operacoes)
    payload["rncs"] = _rncs(ordem["codigo"])
    return payload


def obter_materiais(numero_os: str, aux_code: int) -> dict[str, Any]:
    ordem = _obter_ordem(numero_os)
    rows = fetch_all(
        queries.SQL_PRODUCAO_MATERIAIS_ITEM,
        {"cod_empresa": 1, "cod_os": ordem["codigo"], "cod_os_aux": aux_code},
    )
    return {
        "numero_os": numero_os,
        "aux_code": aux_code,
        "materiais": [
            {
                "id": _texto(row.get("line_id")),
                "codigo": _texto(row.get("cod_interno") or row.get("produto")),
                "descricao": _texto(row.get("produto")),
                "unidade": _texto(row.get("unidade")),
                "necessario": row.get("qtde") or 0,
                "utilizado": row.get("qtde_utilizada") or 0,
                "disponivel": row.get("qtde_disponivel"),
                "restante": max(float(row.get("qtde") or 0) - float(row.get("qtde_utilizada") or 0), 0),
            }
            for row in rows
        ],
    }


def obter_apontamentos(numero_os: str, aux_code: int) -> dict[str, Any]:
    ordem = _obter_ordem(numero_os)
    rows = fetch_all(
        queries.SQL_PRODUCAO_APONTAMENTOS_ITEM,
        {"cod_empresa": 1, "cod_os": ordem["codigo"], "cod_os_aux": aux_code},
    )
    return {"aux_code": aux_code, "atualizado_em": datetime.now().isoformat(), "apontamentos": [
        {"operacao": _texto(row.get("operation_code")), "sequencia": row.get("seq_processo_prod"), "operador": _texto(row.get("operator_name")), "inicio": _iso(row.get("started_at")), "maquina": _texto(row.get("machine")), "pausado": bool(row.get("paused"))}
        for row in rows
    ]}


def _obter_ordem(numero_os: str) -> dict[str, Any]:
    ordem = fetch_one(queries.SQL_PRODUCAO_BUSCAR_OS, {"cod_empresa": 1, "busca": numero_os, "termo": numero_os, "limite": 1})
    if not ordem:
        raise LookupError(f"OS {numero_os} nao encontrada.")
    return ordem


def _rncs(cod_os: int) -> list[dict[str, Any]]:
    rows = fetch_all(queries.SQL_PRODUCAO_RNCS_OS, {"cod_empresa": 1, "cod_os": cod_os})
    return [{"codigo": row.get("codigo"), "aux_code": row.get("cod_os_aux"), "titulo": _texto(row.get("titulo")), "status": _texto(row.get("status_rnc")), "fechada_em": _iso(row.get("dt_fechamento")), "aberta": not bool(row.get("dt_fechamento"))} for row in rows]


def _os_payload(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "numero": _texto(row.get("n_os")),
        "codigo": row.get("codigo"),
        "titulo": _texto(row.get("titulo")),
        "status_origem": _texto(row.get("status_servico")),
        "data_prevista": _iso(row.get("dt_prevista")),
        "desenho": _texto(row.get("n_desenho")),
        "classificacao": _texto(row.get("u_classificacao")),
    }


def _estrutura_payload(ordem: dict[str, Any], itens: list[dict[str, Any]], operacoes: list[dict[str, Any]]) -> dict[str, Any]:
    filhos: dict[int, list[int]] = defaultdict(list)
    itens_por_id = {int(item["aux_code"]): item for item in itens}
    for item in itens:
        parent = item.get("os_pai")
        if parent is not None and int(parent) in itens_por_id:
            filhos[int(parent)].append(int(item["aux_code"]))
    roots = [item_id for item_id, item in itens_por_id.items() if item.get("os_pai") is None or int(item.get("os_pai") or 0) not in itens_por_id]
    ops_por_item: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for operation in operacoes:
        ops_por_item[int(operation["cod_os_aux"])].append(operation)

    states: dict[int, str] = {}
    visiting: set[int] = set()

    def derive(item_id: int) -> str:
        if item_id in states:
            return states[item_id]
        if item_id in visiting:
            states[item_id] = "bloqueado"
            return states[item_id]
        visiting.add(item_id)
        item_ops = ops_por_item.get(item_id, [])
        finished = sum(bool(op.get("finalizado") or op.get("concluido") or op.get("dt_finalizacao")) for op in item_ops)
        started = sum(bool(op.get("data_inicio") or op.get("pcp_dt_primeiro_apont") or op.get("hs_realizadas")) for op in item_ops)
        children = [derive(child_id) for child_id in filhos.get(item_id, [])]
        has_assembly = any("MONTAGEM" in _texto(op.get("tiposervico")).upper().split() for op in item_ops)
        if item_ops and finished == len(item_ops):
            state = "concluido" if has_assembly else "disponivel"
        elif any(bool(op.get("processo_travado")) for op in item_ops):
            state = "bloqueado"
        elif started:
            state = "montagem" if has_assembly else "fabricacao"
        elif has_assembly and children and all(child in {"disponivel", "concluido"} for child in children):
            state = "disponivel"
        elif item_ops:
            state = "nao_iniciado"
        else:
            state = "disponivel" if _texto(item.get("status")).upper() in {"CONCLUIDO", "FINALIZADO"} else "nao_iniciado"
        visiting.remove(item_id)
        states[item_id] = state
        return state

    nodes = []
    for item_id, item in itens_por_id.items():
        state = derive(item_id)
        item_operations = ops_por_item.get(item_id, [])
        parent_id = item.get("os_pai") if item.get("os_pai") in itens_por_id else None
        nodes.append({
            "id": str(item_id),
            "aux_code": item_id,
            "codigo": _texto(item.get("cod_os_completo")),
            "descricao": _texto(item.get("subtitulo")),
            "desenho": _texto(item.get("n_desenho")),
            "revisao": _texto(item.get("revisao_desenho")),
            "posicao": _texto(item.get("posicao_desenho")),
            "quantidade": item.get("qtde_pecas") or 0,
            "parent_id": str(parent_id) if parent_id is not None else None,
            "child_ids": [str(child_id) for child_id in filhos.get(item_id, [])],
            "estado": state,
            "estado_label": STATUS_LABELS[state],
            "estado_motivo": _motivo(state, item_operations),
            "operacoes_total": len(item_operations),
            "operacoes_concluidas": sum(bool(op.get("finalizado") or op.get("concluido") or op.get("dt_finalizacao")) for op in item_operations),
            "operacoes": [_operacao_payload(op) for op in item_operations],
            "data_prevista": _iso(item.get("dt_prevista")),
            "status_origem": _texto(item.get("status")),
        })
    total = sum(len(ops) for ops in ops_por_item.values())
    concluidas = sum(bool(op.get("finalizado") or op.get("concluido") or op.get("dt_finalizacao")) for op in operacoes)
    return {
        "ordem": _os_payload(ordem),
        "raizes": [str(root_id) for root_id in roots],
        "nos": nodes,
        "progresso": round((concluidas / total) * 100, 1) if total else 0,
        "operacoes_concluidas": concluidas,
        "operacoes_total": total,
        "bloqueados": sum(state == "bloqueado" for state in states.values()),
    }


def _operacao_payload(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "codigo": _texto(row.get("codigo")),
        "nome": _texto(row.get("tiposervico")),
        "sequencia": row.get("seq"),
        "finalizada": bool(row.get("finalizado") or row.get("concluido") or row.get("dt_finalizacao")),
        "travada": bool(row.get("processo_travado")),
        "inicio": _iso(row.get("data_inicio")),
        "fim": _iso(row.get("dt_finalizacao")),
        "maquina": _texto(row.get("maquina_real") or row.get("maquina")),
    }


def _motivo(state: str, operations: list[dict[str, Any]]) -> str:
    if state == "bloqueado":
        return "Processo produtivo travado ou ciclo invalido"
    if state == "concluido":
        return "Operacoes de producao e montagem concluidas"
    if state == "disponivel":
        return "Item disponivel para a proxima etapa"
    if state == "montagem":
        return "Operacao de montagem em andamento"
    if state == "fabricacao":
        return "Operacao produtiva em andamento"
    return "Processo cadastrado, mas ainda nao iniciado"
