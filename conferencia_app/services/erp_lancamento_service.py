"""Integracao automatica com ERP (Postgres tcompras) para lancamento de NF.

Periodicamente consulta a tabela `tcompras` no Postgres do ERP procurando
NFs que ja foram lancadas la (`n_nf` + `dt_nf`). Para cada match com itens
locais com status="Concluido" e sem `numero_lancamento`, atualiza para
status="Lancado" gravando o `codigo` retornado pelo ERP, exatamente como
no fluxo manual de lancamento.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from typing import Any

from flask import current_app

from ..extensions import db
from ..models import ItemNota

logger = logging.getLogger(__name__)


# Cache em memoria do resultado da ultima consulta ao ERP por NF.
# Estrutura: { numero_nota: {"motivo": str, "verificada_em": datetime} }
# Usado pela tela /lancamento (Documento de Entrada) para sinalizar de forma
# sutil que a NF foi consultada no ERP e nao foi possivel lancar.
_STATUS_CONSULTA: dict[str, dict[str, Any]] = {}


def obter_status_consulta(numero_nota: str) -> dict[str, Any] | None:
    """Retorna a ultima informacao de consulta ERP para a NF, ou None."""
    if not numero_nota:
        return None
    return _STATUS_CONSULTA.get(str(numero_nota).strip())


def _registrar_status(numero_nota: str, motivo: str) -> None:
    if not numero_nota:
        return
    _STATUS_CONSULTA[str(numero_nota).strip()] = {
        "motivo": motivo,
        "verificada_em": datetime.now(),
    }


def _limpar_status(numero_nota: str) -> None:
    _STATUS_CONSULTA.pop(str(numero_nota).strip(), None)


def _carregar_credenciais_arquivo() -> dict[str, Any]:
    """Le instance/erp_lancamento_config.json se existir.

    Estrutura esperada:
        {
          "host": "10.250.100.251",
          "port": 5432,
          "database": "CPS",
          "user": "DevLeitura",
          "password": "...",
          "table": "tcompras"
        }
    """
    try:
        path = os.path.join(current_app.instance_path, "erp_lancamento_config.json")
        if not os.path.exists(path):
            return {}
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f) or {}
        if not isinstance(data, dict):
            return {}
        return data
    except Exception:
        logger.exception("Falha ao ler erp_lancamento_config.json")
        return {}


def _resolver_config() -> dict[str, Any]:
    arquivo = _carregar_credenciais_arquivo()
    cfg = current_app.config
    return {
        "host": str(arquivo.get("host") or cfg.get("ERP_LANCAMENTO_PG_HOST") or "").strip(),
        "port": int(arquivo.get("port") or cfg.get("ERP_LANCAMENTO_PG_PORT") or 5432),
        "database": str(arquivo.get("database") or cfg.get("ERP_LANCAMENTO_PG_DB") or "").strip(),
        "user": str(arquivo.get("user") or cfg.get("ERP_LANCAMENTO_PG_USER") or "").strip(),
        "password": str(arquivo.get("password") or cfg.get("ERP_LANCAMENTO_PG_PASSWORD") or ""),
        "table": str(arquivo.get("table") or cfg.get("ERP_LANCAMENTO_PG_TABLE") or "tcompras").strip(),
        "usuario_lancamento": str(
            arquivo.get("usuario_lancamento")
            or cfg.get("ERP_LANCAMENTO_USUARIO")
            or "ERP"
        ).strip(),
    }


def _conectar(cfg: dict[str, Any]):
    import psycopg2  # type: ignore

    return psycopg2.connect(
        host=cfg["host"],
        port=cfg["port"],
        dbname=cfg["database"],
        user=cfg["user"],
        password=cfg["password"],
        connect_timeout=15,
    )


def _consultar_codigos_no_erp(cfg: dict[str, Any], chaves: list[tuple[str, datetime | None]]):
    """Recebe pares (n_nf, data_emissao_or_None) e retorna {n_nf: (codigo, dt_nf)}.

    Quando `data_emissao` vier preenchida, restringe pela data (match exato).
    Quando vier None (NFs antigas sem dhEmi), consulta apenas por n_nf e
    aceita o resultado somente quando houver UMA unica linha (evita ambiguidade
    se o mesmo numero existir para fornecedores diferentes).
    """
    if not chaves:
        return {}

    table = cfg["table"]
    if not table.replace("_", "").isalnum():
        raise ValueError(f"Nome de tabela invalido: {table}")

    resultados: dict[str, tuple[str, Any]] = {}
    sql_com_data = f"SELECT codigo, dt_nf FROM {table} WHERE n_nf = %s AND dt_nf::date = %s LIMIT 1"
    sql_sem_data = f"SELECT codigo, dt_nf FROM {table} WHERE n_nf = %s LIMIT 2"

    with _conectar(cfg) as conn:
        with conn.cursor() as cur:
            for n_nf, data_emi in chaves:
                try:
                    if data_emi is not None:
                        cur.execute(sql_com_data, (str(n_nf), data_emi.date()))
                        row = cur.fetchone()
                        if row and row[0] is not None:
                            resultados[str(n_nf)] = (str(row[0]).strip(), row[1])
                        else:
                            _registrar_status(str(n_nf), "Aguardando lançamento no ERP")
                    else:
                        cur.execute(sql_sem_data, (str(n_nf),))
                        rows = cur.fetchall()
                        if len(rows) == 1 and rows[0][0] is not None:
                            resultados[str(n_nf)] = (str(rows[0][0]).strip(), rows[0][1])
                        elif len(rows) > 1:
                            logger.warning(
                                "ERP Lancamento: NF %s tem %d matches em %s sem data_emissao local; pulando para evitar ambiguidade.",
                                n_nf, len(rows), table,
                            )
                            _registrar_status(
                                str(n_nf),
                                "Múltiplos lançamentos com esse número no ERP — confirme manualmente",
                            )
                        else:
                            _registrar_status(str(n_nf), "Aguardando lançamento no ERP")
                except Exception as exc:
                    logger.warning("Erro consultando ERP n_nf=%s dt=%s: %s", n_nf, data_emi, exc)
                    continue
    return resultados


def _aplicar_lancamento_local(
    numero_nota: str,
    codigo: str,
    usuario: str,
    dt_nf_erp: Any = None,
) -> int:
    """Replica exatamente o fluxo manual: status->Lancado, numero_lancamento=codigo.

    Faz tambem backfill de `data_emissao` quando estiver vazia e o ERP tiver
    retornado `dt_nf` (NFs importadas antes da feature).
    """
    update_values: dict[str, Any] = {
        "status": "Lançado",
        "usuario_lancamento": usuario,
        "data_lancamento": datetime.now(),
        "numero_lancamento": codigo,
    }
    rows = ItemNota.query.filter_by(numero_nota=numero_nota, status="Concluído").update(update_values)

    if dt_nf_erp is not None:
        try:
            dt_emissao = dt_nf_erp if isinstance(dt_nf_erp, datetime) else datetime.combine(dt_nf_erp, datetime.min.time())
            ItemNota.query.filter(
                ItemNota.numero_nota == numero_nota,
                ItemNota.data_emissao.is_(None),
            ).update({"data_emissao": dt_emissao})
        except Exception:
            logger.exception("Falha ao backfill data_emissao para NF %s", numero_nota)

    return int(rows or 0)


def _enfileirar_wms_se_disponivel(numero_nota: str, usuario: str) -> None:
    """Reaproveita a fila de integracao WMS usada no lancamento manual, se existir."""
    try:
        from ..routes.api_routes import _enfileirar_integracao_wms_nota_lancada  # type: ignore
    except Exception:
        return
    try:
        _enfileirar_integracao_wms_nota_lancada(numero_nota, usuario)
    except Exception:
        logger.exception("Falha ao enfileirar integracao WMS para NF %s", numero_nota)


def executar_ciclo() -> dict[str, Any]:
    """Executa um ciclo de consulta no ERP e aplica os lancamentos encontrados."""
    resumo: dict[str, Any] = {
        "configurado": False,
        "candidatas": 0,
        "consultadas": 0,
        "encontradas": 0,
        "lancadas": 0,
        "erros": 0,
        "mensagem": "",
    }

    cfg = _resolver_config()
    if not cfg["host"] or not cfg["database"] or not cfg["user"]:
        resumo["mensagem"] = "ERP_LANCAMENTO nao configurado (host/database/user)."
        return resumo
    resumo["configurado"] = True

    # Coleta NFs candidatas: status Concluido, sem numero_lancamento.
    # Inclui NFs sem data_emissao (importadas antes desta feature); para essas,
    # a consulta no ERP sera feita apenas por n_nf (com match unico exigido).
    candidatas = (
        db.session.query(ItemNota.numero_nota, ItemNota.data_emissao)
        .filter(
            ItemNota.status == "Concluído",
            (ItemNota.numero_lancamento.is_(None)) | (ItemNota.numero_lancamento == ""),
        )
        .distinct()
        .all()
    )

    resumo["candidatas"] = len(candidatas)
    if not candidatas:
        resumo["mensagem"] = "Nenhuma NF aguardando lancamento."
        return resumo

    # Deduplica por numero_nota (uma NF pode aparecer varias vezes, com
    # data_emissao preenchida em algumas linhas e None em outras).
    pares_map: dict[str, datetime | None] = {}
    for n, d in candidatas:
        if not n:
            continue
        chave = str(n).strip()
        if chave not in pares_map or (pares_map[chave] is None and d is not None):
            pares_map[chave] = d
    pares: list[tuple[str, datetime | None]] = list(pares_map.items())
    resumo["consultadas"] = len(pares)

    try:
        achados = _consultar_codigos_no_erp(cfg, pares)
    except Exception as exc:
        logger.exception("Erro ao consultar Postgres ERP: %s", exc)
        resumo["erros"] += 1
        resumo["mensagem"] = f"Erro de conexao com ERP: {exc}"
        return resumo

    resumo["encontradas"] = len(achados)
    if not achados:
        resumo["mensagem"] = "Nenhuma NF localizada na tcompras neste ciclo."
        return resumo

    usuario = cfg["usuario_lancamento"]
    total_lancadas = 0
    for n_nf, (codigo, dt_nf_erp) in achados.items():
        try:
            atualizadas = _aplicar_lancamento_local(n_nf, codigo, usuario, dt_nf_erp)
            if atualizadas > 0:
                total_lancadas += 1
                _limpar_status(n_nf)
                _enfileirar_wms_se_disponivel(n_nf, usuario)
                logger.info(
                    "ERP Lancamento: NF %s lancada automaticamente (codigo=%s, %d itens).",
                    n_nf,
                    codigo,
                    atualizadas,
                )
        except Exception:
            logger.exception("Falha ao aplicar lancamento da NF %s", n_nf)
            db.session.rollback()
            resumo["erros"] += 1

    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        logger.exception("Falha no commit final do ciclo ERP Lancamento")
        resumo["erros"] += 1

    resumo["lancadas"] = total_lancadas
    resumo["mensagem"] = f"{total_lancadas} NF(s) lancada(s) via ERP."
    return resumo
