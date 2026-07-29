"""Servico da Conferencia de Expedicao.

Busca as ordens de faturamento em uma API externa (JSON), sincroniza para o
banco local e aplica as regras de status:

    Pendente de conferência -> Conferido -> Faturado -> Expedido
"""

from datetime import datetime

import requests
from flask import current_app

from ..extensions import db
from ..models import ExpedicaoOrdemFat, ExpedicaoOrdemFatItem


STATUS_PENDENTE = "Pendente de conferência"
STATUS_CONFERIDO = "Conferido/Ag. Fat"
STATUS_FATURADO = "Faturado"
# Faturado na origem (NF emitida) SEM que a conferencia cega tenha sido feita.
# A ordem fica travada para expedicao ate ser conferida.
STATUS_FATURADO_SEM_CONF = "Faturado sem conferência"
# A NF da ordem foi incluida em um romaneio de expedicao (Rascunho/Pronto).
# Etapa unica e exclusiva: enquanto estiver "Em Romaneio" a ordem NAO aparece
# mais em "Faturado". So volta para Faturado se a NF for removida do romaneio
# (estorno explicito da etapa atual).
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


def buscar_ordens_api(timeout: int | None = None) -> list:
    """Consulta a API externa e retorna a lista bruta de linhas de item."""
    url = str(current_app.config.get("EXPEDICAO_FAT_API_URL", "") or "").strip()
    if not url:
        raise ValueError("API de expedicao nao configurada (EXPEDICAO_FAT_API_URL).")
    timeout = timeout or int(current_app.config.get("EXPEDICAO_FAT_API_TIMEOUT", 30))
    headers = {
        "ngrok-skip-browser-warning": "true",
        "User-Agent": "ColumbiaSync/1.0 (expedicao-conferencia)",
        "Accept": "application/json",
    }
    resp = requests.get(url, headers=headers, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, list):
        raise ValueError("Resposta inesperada da API de expedicao (esperado uma lista).")
    return data


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
    for key in ("numero_nf", "num_nf", "nf", "nota_fiscal", "n_nf", "numero_nota"):
        val = str(row.get(key) or "").strip()
        if val and val.lower() not in ("0", "none", "null"):
            return _limpar_num_nf(val)
    return ""


def _origem_indica_faturado(origem_status: str, numero_nf: str) -> bool:
    if numero_nf:
        return True
    st = str(origem_status or "").strip().lower()
    return st in ("faturado", "faturada", "expedido", "expedida")


def sincronizar_ordens(timeout: int | None = None) -> dict:
    """Busca a API e atualiza as ordens locais. Retorna um resumo da operacao."""
    linhas = buscar_ordens_api(timeout=timeout)

    grupos: dict[int, list] = {}
    for row in linhas:
        cod = row.get("cod_ordem_fat")
        if cod is None:
            continue
        try:
            cod_int = int(cod)
        except (TypeError, ValueError):
            continue
        grupos.setdefault(cod_int, []).append(row)

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
        origem_status = str(head.get("status") or "").strip()

        ordem = ExpedicaoOrdemFat.query.filter_by(cod_ordem_fat=cod).first()
        if not ordem:
            ordem = ExpedicaoOrdemFat(cod_ordem_fat=cod, status=STATUS_PENDENTE)
            db.session.add(ordem)
            db.session.flush()
            ordem.codigo_interno = f"OF-{ordem.id:06d}"
            criadas += 1
        else:
            atualizadas += 1

        ordem.cliente = str(head.get("cliente") or "").strip() or ordem.cliente
        ordem.orcamento = str(head.get("orcamento") or "").strip() or ordem.orcamento
        ordem.pedido = str(head.get("pedido") or "").strip() or ordem.pedido
        ordem.liberado_faturar = str(
            head.get("liberado_faturar") or ordem.liberado_faturar or "nao"
        ).strip()
        ordem.origem_status = origem_status or ordem.origem_status
        dt = _parse_dt(head.get("dt_solicitacao_fat"))
        if dt:
            ordem.dt_solicitacao_fat = dt
        dt_prev = _parse_dt(head.get("dt_previsao_entrega"))
        if dt_prev:
            ordem.dt_previsao_entrega = dt_prev

        # Enquanto ainda pendente de conferencia, mantem o snapshot dos itens
        # sincronizado com a origem. Apos conferido, preserva o que foi contado.
        #
        # IMPORTANTE: atualiza em vez de apagar-e-recriar. O poll automatico
        # (expedicao_sync_scheduler, a cada poucos minutos) chama esta funcao
        # continuamente; se ela recriasse as linhas a cada ciclo, o id de cada
        # item mudava no meio de uma conferencia em andamento. O conferente
        # preenchia as quantidades usando os ids antigos (ja carregados no
        # navegador), o sync rodava em paralelo e trocava os ids, e ao
        # finalizar TODOS os itens apareciam como "sem quantidade informada"
        # mesmo com os campos preenchidos certinho na tela. Casar por linha
        # (posicao) preserva o id de quem continua na mesma posicao.
        if ordem.status == STATUS_PENDENTE:
            existentes = {
                it.linha: it
                for it in ExpedicaoOrdemFatItem.query.filter_by(ordem_id=ordem.id).all()
            }
            linhas_atuais = set()
            for idx, r in enumerate(rows):
                linhas_atuais.add(idx)
                cod_interno = str(r.get("cod_interno") or "").strip()
                nome_item = str(r.get("item") or "").strip()
                n_os = str(r.get("n_os") or "").strip()
                qtde = _parse_int(r.get("qtde_a_faturar"))
                item_db = existentes.get(idx)
                if item_db:
                    item_db.cod_interno = cod_interno
                    item_db.item = nome_item
                    item_db.n_os = n_os
                    item_db.qtde_a_faturar = qtde
                else:
                    db.session.add(ExpedicaoOrdemFatItem(
                        ordem_id=ordem.id,
                        linha=idx,
                        cod_interno=cod_interno,
                        item=nome_item,
                        n_os=n_os,
                        qtde_a_faturar=qtde,
                    ))
            for linha, item_db in existentes.items():
                if linha not in linhas_atuais:
                    db.session.delete(item_db)

        # Deteccao de faturado (NF preenchida na origem).
        #  - Se ja estava Conferido -> Faturado (fluxo normal).
        #  - Se ainda estava Pendente (NF emitida sem conferencia) ->
        #    "Faturado sem conferência": exige a conferencia cega antes de
        #    liberar a expedicao.
        if ordem.status in (STATUS_PENDENTE, STATUS_CONFERIDO):
            if _origem_indica_faturado(origem_status, numero_nf):
                if numero_nf:
                    ordem.numero_nf = numero_nf
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
    """Marca como Expedido as ordens cujo numero_nf coincida com a(s) NF(s)
    informadas na finalizacao do Registro de expedicao."""
    if not numero_nf:
        return 0
    bruto = str(numero_nf).replace(";", ",").replace("/", ",")
    nfs = [n.strip() for n in bruto.split(",") if n.strip()]
    if not nfs:
        return 0

    ordens = (
        ExpedicaoOrdemFat.query
        .filter(ExpedicaoOrdemFat.numero_nf.in_(nfs))
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
    """Move a(s) ordem(ns) de Faturado para 'Em Romaneio' quando a NF entra em
    um romaneio de expedicao. Fluxo progressivo e exclusivo: a ordem sai da
    etapa "Faturado" assim que passa a compor um romaneio."""
    nfs = _nfs_da_string(numero_nf)
    if not nfs:
        return 0

    ordens = ExpedicaoOrdemFat.query.filter(ExpedicaoOrdemFat.numero_nf.in_(nfs)).all()
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
    """Estorna a(s) ordem(ns) de 'Em Romaneio' de volta para Faturado — usado
    quando a NF e removida do romaneio ou o romaneio (em Rascunho) e deletado.
    Essa e a UNICA forma de a ordem retornar a etapa anterior."""
    nfs = _nfs_da_string(numero_nf)
    if not nfs:
        return 0

    ordens = ExpedicaoOrdemFat.query.filter(ExpedicaoOrdemFat.numero_nf.in_(nfs)).all()
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
    """Estorna a(s) ordem(ns) de Expedido de volta para 'Em Romaneio' — usado
    quando o romaneio expedido e estornado (Expedido -> Pronto)."""
    nfs = _nfs_da_string(numero_nf)
    if not nfs:
        return 0

    ordens = ExpedicaoOrdemFat.query.filter(ExpedicaoOrdemFat.numero_nf.in_(nfs)).all()
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
