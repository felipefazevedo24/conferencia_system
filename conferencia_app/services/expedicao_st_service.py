"""Servico da Conferencia de Expedicao - aba Servico de Terceiro (ST).

A aba ST consome SOMENTE o endpoint HTTP externo (EXPEDICAO_ST_API_URL),
sincroniza para o banco local e aplica as regras de status da conferencia cega:

    Pendente de conferência -> Conferido/Ag. Fat -> Expedido

O endpoint retorna apenas a FILA de ST (itens pendentes de envio ao terceiro).
Cada linha representa um material a enviar, agrupavel pela ordem de compra.

Regras:
    * A conferencia continua CEGA: a quantidade esperada (qtde) nunca vai ao
      front-end; fica so no back-end para validar a contagem.
    * O numero da NF de envio e apenas informativo (nao tira a ordem da fila).
"""

import re
from datetime import datetime

import requests
from flask import current_app

from ..extensions import db
from ..models import ExpedicaoOrdemST, ExpedicaoOrdemSTItem


# Parenteses que carregam a quantidade em pecas (ex.: "(15PÇS - 45KG)").
# Precisam ser removidos da descricao para nao vazar a quantidade esperada na
# conferencia cega.
_RE_QTD_DESCRICAO = re.compile(r"\s*\([^)]*\d[^)]*P[CÇ][^)]*\)", re.IGNORECASE)
_RE_SEP_DUPLICADO = re.compile(r"\s*-\s*-\s*")
_RE_ESPACO = re.compile(r"\s{2,}")


def _limpar_descricao(texto: str) -> str:
    """Remove a quantidade em pecas embutida na descricao do item.

    A conferencia e cega: o conferente nao pode ver quantas pecas sao esperadas.
    Alguns cadastros trazem isso no proprio nome (ex.: "... (15PÇS - 45KG) ...").
    """
    if not texto:
        return texto
    limpo = _RE_QTD_DESCRICAO.sub("", texto)
    limpo = _RE_SEP_DUPLICADO.sub(" - ", limpo)
    limpo = _RE_ESPACO.sub(" ", limpo).strip()
    return limpo.strip(" -").strip() or texto


STATUS_PENDENTE = "Pendente de conferência"
STATUS_CONFERIDO = "Conferido/Ag. Fat"
STATUS_FATURADO = "Faturado"
# Faturado na origem SEM conferencia cega (trava a expedicao ate conferir).
STATUS_FATURADO_SEM_CONF = "Faturado sem conferência"
# A NF da ordem foi incluida em um romaneio de expedicao (Rascunho/Pronto).
# Espelha o fluxo FAT: enquanto estiver "Em Romaneio" a ordem nao aparece mais
# em "Faturado". So volta para Faturado se a NF for removida do romaneio.
STATUS_EM_ROMANEIO = "Em Romaneio"
STATUS_EXPEDIDO = "Expedido"
# Encerrada por um Admin SEM conferencia fisica. Ordem sai da fila de pendentes
# e NAO segue para o Registro de expedicao.
STATUS_FINALIZADO_SEM_CONF = "Finalizada sem conferência"

STATUS_SLUGS = {
    STATUS_PENDENTE: "pendente",
    STATUS_CONFERIDO: "conferido",
    "Conferido": "conferido",  # compat: valor antigo
    STATUS_FATURADO: "faturado",
    STATUS_FATURADO_SEM_CONF: "faturado_sem_conf",
    STATUS_EM_ROMANEIO: "romaneio",
    STATUS_EXPEDIDO: "expedido",
    STATUS_FINALIZADO_SEM_CONF: "finalizado_sem_conf",
}


def status_slug(status: str) -> str:
    return STATUS_SLUGS.get(status, "pendente")


def _parse_int(value, default=0):
    try:
        if value is None or str(value).strip() == "":
            return default
        return int(float(str(value).strip().replace(",", ".")))
    except (TypeError, ValueError):
        return default


def _parse_dt(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "").strip())
    except (TypeError, ValueError):
        return None


def _first(row: dict, *keys) -> str:
    for key in keys:
        val = str(row.get(key) or "").strip()
        if val:
            return val
    return ""


def _normalizar_linha(raw: dict) -> dict:
    """Converte uma linha do endpoint /expedicao_terceiro para o formato
    interno usado pela sincronizacao."""
    cod_os = str(raw.get("cod_os_completo") or "").strip()
    n_os = cod_os.split("/")[0].strip() if cod_os else ""

    nf = raw.get("numero_nf")
    numero_nf = ""
    if nf not in (None, "", 0, 0.0):
        try:
            numero_nf = str(int(float(nf)))
        except (TypeError, ValueError):
            numero_nf = str(nf).strip()

    return {
        "cod_ordem_compra": raw.get("cod_ordem_compra"),
        "fornecedor": raw.get("fornecedor"),
        "n_os": n_os,
        "cod_os_completo": cod_os,
        "cod_interno": raw.get("cod_interno"),
        # A descricao do item vem em "produto"; a limpeza da quantidade embutida
        # continua sendo aplicada na sincronizacao (conferencia cega).
        "item": raw.get("produto") or raw.get("descricao_os"),
        "qtde": raw.get("qtde"),
        "qtde_a_faturar": raw.get("qtde"),
        # NF de ENVIO: apenas informativa (nao tira a ordem da fila).
        "numero_nf": numero_nf,
        "unidade": raw.get("unidade"),
        "dt_prevista_entrega": raw.get("dt_prevista_entrega"),
        "dt_solicitacao": raw.get("data"),
        "qtde_retornada": raw.get("qtde_retornada"),
    }


def buscar_ordens_api(timeout: int | None = None) -> list:
    """Consulta o endpoint HTTP externo e retorna as linhas de material de ST
    ja normalizadas. NAO usa mais o banco do CPS/bridge."""
    url = str(current_app.config.get("EXPEDICAO_ST_API_URL", "") or "").strip()
    if not url:
        raise ValueError("API de expedicao ST nao configurada (EXPEDICAO_ST_API_URL).")
    timeout = timeout or int(current_app.config.get("EXPEDICAO_ST_API_TIMEOUT", 30))
    headers = {
        "ngrok-skip-browser-warning": "true",
        "User-Agent": "ColumbiaSync/1.0 (expedicao-conferencia-st)",
        "Accept": "application/json",
    }
    resp = requests.get(url, headers=headers, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, list):
        raise ValueError("Resposta inesperada da API de expedicao ST (esperado uma lista).")
    return [_normalizar_linha(row) for row in data if isinstance(row, dict)]


def _cod_ordem_compra(row: dict) -> str:
    return _first(
        row,
        "cod_ordem_compra", "ordem_compra", "cod_oc", "oc",
        "numero_ordem_compra", "num_ordem_compra",
    )


def _limpar_num_nf(valor) -> str:
    """Normaliza o numero da NF. Alguns endpoints entregam a NF como float
    (ex.: 1234.0); aqui removemos o ".0" convertendo para inteiro quando o
    valor for um numero inteiro. Valores nao-numericos (ex.: multiplas NFs
    separadas por virgula) sao mantidos como estao."""
    val = str(valor or "").strip()
    if not val:
        return ""
    try:
        numero = float(val)
    except (TypeError, ValueError):
        return val
    if numero.is_integer():
        return str(int(numero))
    return val


def _detectar_numero_nf(row: dict) -> str:
    for key in (
        "numero_nf", "num_nf", "nf", "nota_fiscal", "n_nf", "numero_nota",
        "nf_envio", "nf_retorno", "nf_envio_retorno",
    ):
        val = str(row.get(key) or "").strip()
        if val and val.lower() not in ("0", "none", "null"):
            return _limpar_num_nf(val)
    return ""


def _origem_indica_faturado(origem_status: str, numero_nf: str) -> bool:
    if numero_nf:
        return True
    st = str(origem_status or "").strip().lower()
    return st in ("faturado", "faturada", "expedido", "expedida")


def sincronizar_ordens(timeout: int | None = None, cod_empresa: int | None = None) -> dict:
    """Busca o endpoint HTTP e atualiza as ordens de ST locais. Retorna um resumo."""
    linhas = buscar_ordens_api(timeout=timeout)

    grupos: dict[str, list] = {}
    for row in linhas:
        cod = _cod_ordem_compra(row)
        if not cod:
            continue
        # Ignora linhas ja faturadas na origem (nao entram na fila de conferencia).
        grupos.setdefault(cod, []).append(row)

    criadas = 0
    atualizadas = 0
    faturadas = 0
    faturadas_sem_conf = 0
    agora = datetime.now()

    for cod, rows in grupos.items():
        head = rows[0]
        numero_nf = ""
        for r in rows:
            numero_nf = _detectar_numero_nf(r) or numero_nf
        origem_status = _first(head, "status", "origem_status")

        # OSs agregadas (distintas) para exibir no cabecalho
        os_set = []
        for r in rows:
            os_val = _first(r, "n_os", "os", "ordem_servico", "num_os")
            if os_val and os_val not in os_set:
                os_set.append(os_val)
        n_os_agg = ", ".join(os_set)

        ordem = ExpedicaoOrdemST.query.filter_by(cod_ordem_compra=cod).first()
        if not ordem:
            ordem = ExpedicaoOrdemST(cod_ordem_compra=cod, status=STATUS_PENDENTE)
            db.session.add(ordem)
            db.session.flush()
            criadas += 1
        else:
            atualizadas += 1

        ordem.fornecedor = _first(head, "fornecedor", "nome_fornecedor") or ordem.fornecedor
        ordem.n_os = n_os_agg or ordem.n_os
        ordem.origem_status = origem_status or ordem.origem_status
        dt = _parse_dt(head.get("dt_solicitacao") or head.get("dt_solicitacao_fat"))
        if dt:
            ordem.dt_solicitacao = dt
        dt_prev = _parse_dt(head.get("dt_prevista_entrega"))
        if dt_prev:
            ordem.dt_prevista_entrega = dt_prev

        # Enquanto pendente, mantem o snapshot dos itens sincronizado.
        if ordem.status == STATUS_PENDENTE:
            ExpedicaoOrdemSTItem.query.filter_by(ordem_id=ordem.id).delete()
            for idx, r in enumerate(rows):
                db.session.add(ExpedicaoOrdemSTItem(
                    ordem_id=ordem.id,
                    linha=idx,
                    cod_interno=_first(r, "cod_interno", "codigo_interno"),
                    item=_limpar_descricao(_first(r, "item", "descricao", "descricao_item")),
                    n_os=_first(r, "n_os", "os", "ordem_servico", "num_os"),
                    qtde_a_faturar=_parse_int(
                        r.get("qtde_a_faturar")
                        if r.get("qtde_a_faturar") is not None
                        else r.get("qtde")
                    ),
                ))

        # O endpoint /expedicao_terceiro retorna a FILA de ST. Guardamos a NF
        # para exibicao e para a baixa por expedicao (marcar_expedido_por_nf).
        if numero_nf:
            ordem.numero_nf = numero_nf

        # Deteccao de faturado (NF preenchida na origem), espelhando o fluxo FAT:
        #  - Se ja estava Conferido/Ag. Fat -> Faturado (fluxo normal).
        #  - Se ainda estava Pendente (NF emitida sem conferencia) ->
        #    "Faturado sem conferência": exige a conferencia cega antes de
        #    liberar a expedicao.
        if ordem.status in (STATUS_PENDENTE, STATUS_CONFERIDO):
            if _origem_indica_faturado(origem_status, numero_nf):
                ordem.faturado_at = agora
                if ordem.status == STATUS_CONFERIDO:
                    ordem.status = STATUS_FATURADO
                    faturadas += 1
                else:
                    ordem.status = STATUS_FATURADO_SEM_CONF
                    faturadas_sem_conf += 1

        ordem.updated_at = agora

    db.session.commit()
    return {
        "criadas": criadas,
        "atualizadas": atualizadas,
        "faturadas": faturadas,
        "faturadas_sem_conf": faturadas_sem_conf,
        "total_ordens": len(grupos),
    }


def marcar_expedido_por_nf(numero_nf, registro_id=None, usuario=None) -> int:
    """Marca como Expedido as ordens de ST cujo numero_nf coincida com a(s)
    NF(s) informadas na finalizacao do Registro de expedicao."""
    if not numero_nf:
        return 0
    bruto = str(numero_nf).replace(";", ",").replace("/", ",")
    nfs = [n.strip() for n in bruto.split(",") if n.strip()]
    if not nfs:
        return 0

    ordens = (
        ExpedicaoOrdemST.query
        .filter(ExpedicaoOrdemST.numero_nf.in_(nfs))
        .all()
    )
    afetadas = 0
    agora = datetime.now()
    for ordem in ordens:
        # Trava de conferencia: ordens faturadas SEM conferencia nao podem ser
        # expedidas ate que a conferencia cega seja realizada.
        if ordem.status == STATUS_FATURADO_SEM_CONF:
            continue
        if ordem.status != STATUS_EXPEDIDO:
            ordem.status = STATUS_EXPEDIDO
            ordem.expedido_at = agora
            ordem.expedido_by = usuario
            if registro_id:
                ordem.expedicao_registro_id = registro_id
            ordem.updated_at = agora
            afetadas += 1
    if afetadas:
        db.session.commit()
    return afetadas


def _nfs_da_string(numero_nf) -> list:
    if not numero_nf:
        return []
    bruto = str(numero_nf).replace(";", ",").replace("/", ",")
    return [n.strip() for n in bruto.split(",") if n.strip()]


def marcar_em_romaneio_por_nf(numero_nf) -> int:
    """Move a(s) ordem(ns) de ST de Faturado para 'Em Romaneio' quando a NF
    entra em um romaneio de expedicao. Espelha expedicao_fat_service."""
    nfs = _nfs_da_string(numero_nf)
    if not nfs:
        return 0

    ordens = ExpedicaoOrdemST.query.filter(ExpedicaoOrdemST.numero_nf.in_(nfs)).all()
    afetadas = 0
    agora = datetime.now()
    for ordem in ordens:
        if ordem.status == STATUS_FATURADO:
            ordem.status = STATUS_EM_ROMANEIO
            ordem.updated_at = agora
            afetadas += 1
    if afetadas:
        db.session.commit()
    return afetadas


def reverter_romaneio_por_nf(numero_nf) -> int:
    """Estorna a(s) ordem(ns) de ST de 'Em Romaneio' de volta para Faturado —
    usado quando a NF e removida do romaneio ou o romaneio (em Rascunho) e
    deletado."""
    nfs = _nfs_da_string(numero_nf)
    if not nfs:
        return 0

    ordens = ExpedicaoOrdemST.query.filter(ExpedicaoOrdemST.numero_nf.in_(nfs)).all()
    afetadas = 0
    agora = datetime.now()
    for ordem in ordens:
        if ordem.status == STATUS_EM_ROMANEIO:
            ordem.status = STATUS_FATURADO
            ordem.updated_at = agora
            afetadas += 1
    if afetadas:
        db.session.commit()
    return afetadas


def reverter_expedicao_por_nf(numero_nf) -> int:
    """Estorna a(s) ordem(ns) de ST de Expedido de volta para 'Em Romaneio' —
    usado quando o romaneio expedido e estornado (Expedido -> Pronto)."""
    nfs = _nfs_da_string(numero_nf)
    if not nfs:
        return 0

    ordens = ExpedicaoOrdemST.query.filter(ExpedicaoOrdemST.numero_nf.in_(nfs)).all()
    afetadas = 0
    agora = datetime.now()
    for ordem in ordens:
        if ordem.status == STATUS_EXPEDIDO:
            ordem.status = STATUS_EM_ROMANEIO
            ordem.expedido_at = None
            ordem.expedido_by = None
            ordem.expedicao_registro_id = None
            ordem.updated_at = agora
            afetadas += 1
    if afetadas:
        db.session.commit()
    return afetadas
