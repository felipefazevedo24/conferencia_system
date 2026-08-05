"""Integracao: puxa a foto do canhoto que o motorista tirou no app (parada de
ENTREGA de um romaneio CIF) e a anexa como comprovante de entrega no Registro
de Expedicao de cada NF do romaneio, finalizando-os.

O elo entre os dois mundos e:
    Romaneio CIF (Expedido) -> AgendamentoSolicitacao de ENTREGA (AutoCIF, com
    romaneio_id no payload_origem) -> ViagemParada (foto do canhoto em
    foto_paths). Ver solicitacao_logistica_cif_service.gerar_solicitacao_
    entrega_para_romaneio.

Tudo fica gravado no proprio Registro de Expedicao (mesmos campos de canhoto ja
usados pela anexacao manual), reaproveitando o mesmo arquivo fisico enviado pelo
motorista (referenciado por caminho absoluto, servido por _resolver_foto_
expedicao)."""
from __future__ import annotations

import json
import os
from datetime import datetime

from flask import current_app

from ..extensions import db
from ..models import (
    AgendamentoSolicitacao,
    ExpedicaoRomaneio,
    ViagemParada,
)

ORIGEM_MOTORISTA = "app do motorista"
# Resultados de parada que representam uma entrega concluida com sucesso.
_RESULTADOS_ENTREGUE = {"entregue", "concluida", "concluída", ""}


def _romaneio_e_nf_da_parada(parada: ViagemParada) -> tuple[ExpedicaoRomaneio | None, str | None]:
    """Resolve (romaneio CIF, numero_nf) de uma parada de entrega, via a
    solicitacao de entrega gerada automaticamente (payload_origem). Cada parada
    corresponde a UMA NF (payload.numero_nf); payloads antigos sem numero_nf
    devolvem numero_nf=None (fallback: finaliza todas as NFs do romaneio)."""
    if not parada or not parada.solicitacao_id:
        return None, None
    sol = db.session.get(AgendamentoSolicitacao, parada.solicitacao_id)
    if not sol or not sol.payload_origem:
        return None, None
    try:
        payload = json.loads(sol.payload_origem)
    except (ValueError, TypeError):
        return None, None
    if not isinstance(payload, dict):
        return None, None
    romaneio_id = payload.get("romaneio_id")
    numero_nf = str(payload.get("numero_nf") or "").strip() or None
    if not romaneio_id:
        return None, None
    try:
        romaneio = db.session.get(ExpedicaoRomaneio, int(romaneio_id))
    except (ValueError, TypeError):
        return None, None
    return romaneio, numero_nf


def _ultima_foto_abs(parada: ViagemParada) -> tuple[str | None, str | None]:
    """Nome do arquivo + caminho absoluto da ultima foto (canhoto) da parada."""
    if not parada or not parada.foto_paths:
        return None, None
    try:
        fotos = json.loads(parada.foto_paths)
    except (ValueError, TypeError):
        return None, None
    if not isinstance(fotos, list) or not fotos:
        return None, None
    rel = str(fotos[-1] or "").strip()
    if not rel:
        return None, None
    caminho = os.path.join(current_app.instance_path, rel)
    if not os.path.isfile(caminho):
        return None, None
    return os.path.basename(rel), caminho


def anexar_comprovante_da_parada(
    parada: ViagemParada,
    *,
    usuario: str = ORIGEM_MOTORISTA,
    commit: bool = True,
) -> dict:
    """Anexa a foto do canhoto de uma parada de ENTREGA ao comprovante de
    entrega do romaneio CIF vinculado, finalizando o Registro de Expedicao de
    cada NF ainda pendente. Best-effort: nunca levanta excecao."""
    resultado = {"vinculado": False, "nfs_finalizadas": 0, "romaneio": None}
    try:
        if not parada or parada.tipo != "ENTREGA":
            return resultado
        res = str(parada.resultado or "").strip().lower()
        if res not in _RESULTADOS_ENTREGUE:
            return resultado

        romaneio, numero_nf = _romaneio_e_nf_da_parada(parada)
        if not romaneio or romaneio.status != "Expedido":
            return resultado

        nome_arquivo, caminho = _ultima_foto_abs(parada)
        if not caminho:
            return resultado

        # Import tardio para evitar import circular (routes -> services -> routes).
        from ..routes.expedicao_romaneio_routes import _registro_conferencia_da_nf

        agora = datetime.now()
        total = 0
        # Cada parada corresponde a UMA NF (payload.numero_nf) -> finaliza so o
        # registro daquela NF. Payload antigo (sem numero_nf) finaliza todas.
        if numero_nf:
            nfs_alvo = [
                nf for nf in (romaneio.nfs or [])
                if str(nf.numero_nf or "").strip() == numero_nf
            ]
        else:
            nfs_alvo = list(romaneio.nfs or [])
        for nf in nfs_alvo:
            registro = _registro_conferencia_da_nf(nf.numero_nf)
            # So finaliza registros que ainda estao aguardando o comprovante.
            if not registro or registro.status != "Expedido" or registro.canhoto_file_name:
                continue
            registro.canhoto_file_name = nome_arquivo
            registro.canhoto_file_path = caminho
            registro.canhoto_uploaded_at = agora
            registro.canhoto_uploaded_by = usuario
            registro.status = "Finalizado"
            registro.finalizado_at = agora
            registro.finalizado_by = usuario
            registro.updated_at = agora
            total += 1

        if total and commit:
            db.session.commit()

        resultado.update({
            "vinculado": True,
            "nfs_finalizadas": total,
            "romaneio": romaneio.numero_romaneio,
        })
        return resultado
    except Exception:
        current_app.logger.exception(
            "Falha ao anexar comprovante do motorista (parada %s) ao romaneio.",
            getattr(parada, "id", None),
        )
        if commit:
            db.session.rollback()
        return resultado


def sincronizar_comprovantes_pendentes() -> dict:
    """Backfill: percorre os romaneios CIF Expedidos e, para cada NF ainda sem
    comprovante, procura a parada de entrega concluida (com foto do canhoto) da
    solicitacao daquela NF e anexa a foto. Retorna um resumo agregado."""
    from ..routes.expedicao_romaneio_routes import _info_comprovantes_romaneio
    from .solicitacao_logistica_cif_service import _buscar_entrega_de_nf

    romaneios = (
        ExpedicaoRomaneio.query
        .filter(
            ExpedicaoRomaneio.tipo_frete == "CIF",
            ExpedicaoRomaneio.status == "Expedido",
        )
        .all()
    )

    romaneios_atualizados = 0
    nfs_finalizadas = 0
    detalhes: list[str] = []
    for romaneio in romaneios:
        info, tem_pendencia = _info_comprovantes_romaneio(romaneio)
        if not tem_pendencia:
            continue
        total_rom = 0
        for numero_nf, dados in info.items():
            if not dados.get("canhoto_pendente"):
                continue
            solicitacao = _buscar_entrega_de_nf(romaneio.id, numero_nf)
            if not solicitacao:
                continue
            parada = (
                ViagemParada.query
                .filter(
                    ViagemParada.solicitacao_id == solicitacao.id,
                    ViagemParada.tipo == "ENTREGA",
                    ViagemParada.foto_paths.isnot(None),
                )
                .order_by(ViagemParada.id.desc())
                .first()
            )
            if not parada:
                continue
            res = anexar_comprovante_da_parada(parada, usuario="sincronização (canhoto do app)")
            if res.get("vinculado") and res.get("nfs_finalizadas"):
                total_rom += res["nfs_finalizadas"]
        if total_rom:
            romaneios_atualizados += 1
            nfs_finalizadas += total_rom
            detalhes.append(f"{romaneio.numero_romaneio}: {total_rom} NF(s)")

    return {
        "romaneios_atualizados": romaneios_atualizados,
        "nfs_finalizadas": nfs_finalizadas,
        "detalhes": detalhes,
    }


# Throttle em memoria para a sincronizacao automatica: evita rodar o backfill a
# cada request de listagem de romaneios (roda no maximo 1x a cada _INTERVALO_AUTO).
_INTERVALO_AUTO_SEG = 60.0
_ultima_sync_auto = 0.0


def sincronizar_automatico() -> dict | None:
    """Roda a sincronizacao dos canhotos do motorista de forma automatica e
    best-effort (chamada ao carregar a tela de romaneios). Nunca levanta
    excecao e respeita um intervalo minimo para nao pesar a listagem."""
    global _ultima_sync_auto
    try:
        import time

        agora = time.monotonic()
        if agora - _ultima_sync_auto < _INTERVALO_AUTO_SEG:
            return None
        _ultima_sync_auto = agora
        return sincronizar_comprovantes_pendentes()
    except Exception:
        current_app.logger.exception(
            "Falha na sincronizacao automatica dos canhotos do motorista."
        )
        return None
