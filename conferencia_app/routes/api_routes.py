from datetime import datetime
import csv
import io
import re
import os
import shutil
import tempfile
from datetime import timedelta

import requests
from flask import Blueprint, Response, current_app, jsonify, request, send_file, session
from sqlalchemy import func
from werkzeug.security import generate_password_hash
from werkzeug.utils import secure_filename

from ..auth import login_required, roles_required
from ..extensions import db
from ..models import (
    ConferenciaLock,
    ExpedicaoConferencia,
    ExpedicaoConferenciaDecisao,
    ExpedicaoConferenciaItem,
    ExpedicaoEstorno,
    ExpedicaoFaturamento,
    ExpedicaoFaturamentoItem,
    ItemNota,
    ChecklistRecebimento,
    LocalizacaoArmazem,
    ItemWMS,
    LogAcessoAdministrativo,
    LogDivergencia,
    LogExclusaoNota,
    LogEstornoLancamento,
    LogManifestacaoDestinatario,
    LogReversaoConferencia,
    LogTentativaConferencia,
    SolicitacaoDevolucaoRecebimento,
    Usuario,
)
from ..services import WMSService
from ..schemas.api_schemas import (
    AprovarSolicitacaoDevolucaoSchema,
    ConfirmarLancamentoSchema,
    ConsysteDownloadSchema,
    DevolverMaterialSchema,
    ExcluirNotaPendenteSchema,
    EstornoLancamentoSchema,
    ManifestarDestinatarioSchema,
    NotaSchema,
    RegisterSchema,
    ResetNotaSchema,
    ValidarSchema,
)
from ..services.consyste_service import enviar_decisao_consyste, manifestar_destinatario_consyste
from ..services.expedicao_service import (
    list_conferencia_reports,
    parse_conferencia_report,
    resolve_report_image_path,
    validate_blind_conference,
)
from ..services.xml_service import process_xml_and_store


api_bp = Blueprint("api", __name__)
register_schema = RegisterSchema()
consyste_download_schema = ConsysteDownloadSchema()
validar_schema = ValidarSchema()
devolver_schema = DevolverMaterialSchema()
reset_schema = ResetNotaSchema()
confirmar_schema = ConfirmarLancamentoSchema()
estorno_lancamento_schema = EstornoLancamentoSchema()
manifestar_destinatario_schema = ManifestarDestinatarioSchema()
nota_schema = NotaSchema()
excluir_nota_schema = ExcluirNotaPendenteSchema()
aprovar_solicitacao_devolucao_schema = AprovarSolicitacaoDevolucaoSchema()

CFOPS_CONFERENCIA_PRINCIPAL = {"5124", "5125"}
CFOPS_EXCLUIR_SEM_CONFERENCIA = {"5902", "6902"}


def _normalize_external_payload(payload):
    if isinstance(payload, dict):
        return payload
    if payload is None:
        return {}
    return {"raw": str(payload)[:1000]}


def _get_db_file_path() -> str:
    uri = current_app.config.get("SQLALCHEMY_DATABASE_URI", "")
    if uri.startswith("sqlite:///"):
        return uri.replace("sqlite:///", "", 1)
    return ""


def _normalize_cfop(cfop: str) -> str:
    return re.sub(r"\D", "", str(cfop or ""))[:4]


def _filter_itens_para_conferencia(itens):
    itens = list(itens or [])
    tem_cfop_principal = any(_normalize_cfop(item.cfop) in CFOPS_CONFERENCIA_PRINCIPAL for item in itens)
    if not tem_cfop_principal:
        return itens
    return [item for item in itens if _normalize_cfop(item.cfop) not in CFOPS_EXCLUIR_SEM_CONFERENCIA]


def _compose_motivo_pendencia(item_id, motivos_itens, motivos_tipos, motivos_observacoes):
    item_key = str(item_id)
    motivo_completo = str(motivos_itens.get(item_key) or "").strip()
    if motivo_completo:
        return motivo_completo

    motivo_tipo = str(motivos_tipos.get(item_key) or "").strip()
    observacao = str(motivos_observacoes.get(item_key) or "").strip()
    if motivo_tipo and observacao:
        return f"{motivo_tipo}: {observacao}"
    return motivo_tipo or observacao


def _release_lock(numero_nota: str) -> None:
    ConferenciaLock.query.filter_by(numero_nota=str(numero_nota)).delete()


def _acquire_lock(numero_nota: str, usuario: str):
    now = datetime.now()
    lock_minutes = current_app.config.get("LOCK_TIMEOUT_MINUTES", 25)
    heartbeat_timeout = current_app.config.get("LOCK_HEARTBEAT_SECONDS", 120)
    lock = ConferenciaLock.query.filter_by(numero_nota=str(numero_nota)).first()

    heartbeat_age = (now - (lock.heartbeat_at or lock.lock_until)).total_seconds() if lock else 0
    lock_stale = lock and heartbeat_age > heartbeat_timeout

    if lock and lock.lock_until > now and lock.usuario != usuario and not lock_stale:
        return False, lock

    if not lock:
        lock = ConferenciaLock(numero_nota=str(numero_nota), usuario=usuario, lock_until=now, heartbeat_at=now)
        db.session.add(lock)

    lock.usuario = usuario
    lock.lock_until = now + timedelta(minutes=lock_minutes)
    lock.heartbeat_at = now
    db.session.commit()
    return True, lock


def _append_attempt_log(
    numero_nota,
    item,
    tentativa_numero,
    qtd_digitada,
    qtd_convertida,
    unidade_informada,
    fator_conversao,
    status_item,
    motivo,
    usuario,
):
    db.session.add(
        LogTentativaConferencia(
            numero_nota=str(numero_nota),
            item_id=item.id,
            tentativa_numero=tentativa_numero,
            qtd_esperada=item.qtd_real,
            qtd_digitada=qtd_digitada,
            qtd_convertida=qtd_convertida,
            unidade_informada=unidade_informada,
            fator_conversao=fator_conversao,
            status_item=status_item,
            motivo=motivo,
            usuario=usuario,
        )
    )


def _normalize_unidade_medida(unidade: str) -> str:
    return str(unidade or "").strip().upper()


def _get_tolerancia_quantidade(item: ItemNota) -> float:
    quantidade_esperada = float(item.qtd_real or 0)
    unidade = _normalize_unidade_medida(item.unidade_comercial)
    tolerancia = 0.0001
    if unidade in {"KG", "MM"}:
        tolerancia = max(abs(quantidade_esperada) * 0.02, tolerancia)
    return tolerancia


def _quantidade_esta_dentro_da_tolerancia(item: ItemNota, quantidade_convertida: float) -> bool:
    quantidade_esperada = float(item.qtd_real or 0)
    tolerancia = _get_tolerancia_quantidade(item)
    return abs(quantidade_convertida - quantidade_esperada) <= tolerancia


def _quantidade_divergente_mas_tolerada(item: ItemNota, quantidade_convertida: float) -> bool:
    unidade = _normalize_unidade_medida(item.unidade_comercial)
    if unidade not in {"KG", "MM"}:
        return False
    quantidade_esperada = float(item.qtd_real or 0)
    diferenca = abs(quantidade_convertida - quantidade_esperada)
    return 0.0001 < diferenca <= _get_tolerancia_quantidade(item)


def _summarize_divergencia_nota(numero_nota: str):
    tentativas = (
        LogTentativaConferencia.query.filter_by(numero_nota=str(numero_nota))
        .order_by(LogTentativaConferencia.tentativa_numero.asc(), LogTentativaConferencia.id.asc())
        .all()
    )
    if not tentativas:
        return {
            "divergencia": "Não",
            "divergencia_status": "Não",
            "tentativas_invalidas": 0,
            "detalhe_divergencia": "",
        }

    tentativas_com_divergencia = sorted(
        {
            int(t.tentativa_numero)
            for t in tentativas
            if str(t.status_item or "").upper() == "DIVERGÊNCIA"
        }
    )
    total_invalidas = len(tentativas_com_divergencia)
    if total_invalidas == 0:
        return {
            "divergencia": "Não",
            "divergencia_status": "Não",
            "tentativas_invalidas": 0,
            "detalhe_divergencia": "",
        }

    ultima_tentativa = max(int(t.tentativa_numero) for t in tentativas)
    divergencias_ultima = [
        t
        for t in tentativas
        if int(t.tentativa_numero) == ultima_tentativa and str(t.status_item or "").upper() == "DIVERGÊNCIA"
    ]

    if divergencias_ultima:
        itens_ultima = (
            ItemNota.query.filter(
                ItemNota.numero_nota == str(numero_nota),
                ItemNota.id.in_({int(t.item_id) for t in divergencias_ultima}),
            ).all()
        )
        descricao_por_id = {int(i.id): i.descricao for i in itens_ultima}
        detalhes = []
        for t in divergencias_ultima:
            desc = descricao_por_id.get(int(t.item_id), f"Item {t.item_id}")
            motivo = str(t.motivo or "Divergência").strip()
            detalhes.append(f"{desc} ({motivo})")
        return {
            "divergencia": "Sim",
            "divergencia_status": "Ativa",
            "tentativas_invalidas": total_invalidas,
            "detalhe_divergencia": ", ".join(detalhes),
        }

    return {
        "divergencia": "Não",
        "divergencia_status": "Resolvida",
        "tentativas_invalidas": total_invalidas,
        "detalhe_divergencia": f"Divergências resolvidas após {total_invalidas} tentativa(s) inválida(s).",
    }


def _compute_pending_priority(numero_nota, fornecedor):
    now = datetime.now()
    itens = _filter_itens_para_conferencia(ItemNota.query.filter_by(numero_nota=numero_nota, status="Pendente").all())
    if not itens:
        return None

    first = itens[0]
    log_liberacao = (
        LogAcessoAdministrativo.query.filter(
            LogAcessoAdministrativo.rota.contains("/api/admin/liberar_nota_conferencia"),
            LogAcessoAdministrativo.rota.contains(f"nota={numero_nota}"),
        )
        .order_by(LogAcessoAdministrativo.data.desc())
        .first()
    )
    data_liberacao = (log_liberacao.data if log_liberacao and log_liberacao.data else None) or first.data_importacao
    liberado_por = log_liberacao.usuario if log_liberacao else "---"

    horas = int(((now - first.data_importacao).total_seconds() / 3600) if first.data_importacao else 0)
    qtd_itens = len(itens)
    divergencias_30d = (
        LogDivergencia.query.filter_by(numero_nota=numero_nota)
        .filter(LogDivergencia.data_erro >= now - timedelta(days=30))
        .count()
    )
    score = min(horas, 72) + min(qtd_itens * 2, 30) + min(divergencias_30d * 5, 25)
    if any((i.qtd_real or 0) > 1000 for i in itens):
        score += 10

    if score <= 15:
        prioridade_nivel = 0
    elif score <= 30:
        prioridade_nivel = 1
    elif score <= 50:
        prioridade_nivel = 2
    elif score <= 75:
        prioridade_nivel = 3
    elif score <= 100:
        prioridade_nivel = 4
    else:
        prioridade_nivel = 5

    idade_label = "<1h" if horas < 1 else f"{horas}h"

    return {
        "numero": numero_nota,
        "fornecedor": fornecedor,
        "score": score,
        "prioridade_nivel": prioridade_nivel,
        "idade_horas": horas,
        "idade_label": idade_label,
        "itens": qtd_itens,
        "divergencias_30d": divergencias_30d,
        "data_liberacao": data_liberacao.strftime("%d/%m/%Y %H:%M") if data_liberacao else "---",
        "data_liberacao_iso": data_liberacao.isoformat() if data_liberacao else None,
        "liberado_por": liberado_por,
    }


def _match_filters(rec, status=None, fornecedor=None, conferente=None, revertido_por=None, data_ini=None, data_fim=None):
    if status and rec.get("status") != status:
        return False
    if fornecedor and fornecedor.lower() not in (rec.get("fornecedor") or "").lower():
        return False
    if conferente and conferente.lower() not in (rec.get("conferido_por") or "").lower():
        return False
    if revertido_por and revertido_por.lower() not in (rec.get("revertido_por") or "").lower():
        return False

    data_ref = rec.get("data_ref")
    if data_ini and data_ref and data_ref.date() < data_ini:
        return False
    if data_fim and data_ref and data_ref.date() > data_fim:
        return False
    return True


def _parse_date(value):
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except Exception:
        return None


def _parse_positive_int(value, default, min_value=1, max_value=2000):
    try:
        parsed = int(value)
        return min(max(parsed, min_value), max_value)
    except Exception:
        return default


def _resolve_nota_context(numero_nota=None, chave_acesso=None):
    numero = str(numero_nota or "").strip()
    chave = re.sub(r"\D", "", str(chave_acesso or "").strip())

    query = ItemNota.query
    itens = []
    if numero:
        itens = query.filter_by(numero_nota=numero).all()
    elif chave:
        itens = query.filter_by(chave_acesso=chave).all()

    if itens:
        numero = str(itens[0].numero_nota or "").strip()
        chave = re.sub(r"\D", "", str(itens[0].chave_acesso or chave).strip())

    return numero, chave, itens


def _fetch_consyste_document_bytes(chave_acesso, tipo_documento):
    tipo = str(tipo_documento or "xml").strip().lower()
    if tipo not in {"xml", "pdf"}:
        raise ValueError("Tipo de documento inválido. Use xml ou pdf.")

    chave = re.sub(r"\D", "", str(chave_acesso or "").strip())
    if len(chave) != 44:
        raise ValueError("Chave de acesso inválida para download.")

    token = current_app.config.get("CONSYSTE_TOKEN")
    url = f"{current_app.config['CONSYSTE_API_BASE']}/nfe/{chave}/download.{tipo}"
    resp = requests.get(url, headers={"X-Consyste-Auth-Token": token}, timeout=20)
    return resp, tipo


def _manifestar_destinatario(numero_nota: str, usuario: str, manifestacao: str, descricao_evento: str, justificativa: str | None = None):
    numero_resolvido, chave_resolvida, itens = _resolve_nota_context(numero_nota=numero_nota)
    if not numero_resolvido or not itens:
        return {"sucesso": False, "msg": "NF não encontrada para manifestação.", "status_code": 404}

    if not chave_resolvida:
        detalhe = "NF sem chave de acesso cadastrada para manifestação do destinatário."
        db.session.add(
            LogManifestacaoDestinatario(
                numero_nota=numero_resolvido,
                chave_acesso=None,
                manifestacao=manifestacao,
                status="Falha",
                detalhe=detalhe,
                usuario=usuario,
            )
        )
        db.session.commit()
        return {"sucesso": False, "msg": detalhe, "status_code": 400}

    try:
        ok, status_code, payload = manifestar_destinatario_consyste(
            chave_resolvida,
            manifestacao=manifestacao,
            justificativa=justificativa,
        )
        payload = _normalize_external_payload(payload)
        detalhe = f"{descricao_evento} enviada com sucesso à SEFAZ."
        if ok:
            protocolo_raw = (
                (payload or {}).get("nProt")
                or (payload or {}).get("protocolo")
                or (payload or {}).get("numero_protocolo")
                or (payload or {}).get("protocol")
            )
            if protocolo_raw:
                detalhe = f"{descricao_evento} enviada com sucesso à SEFAZ. Protocolo: {str(protocolo_raw)[:60]}"
        else:
            detalhe = (
                (payload or {}).get("error")
                or (payload or {}).get("motivo")
                or f"Falha ao enviar {descricao_evento.lower()}."
            )[:500]

        db.session.add(
            LogManifestacaoDestinatario(
                numero_nota=numero_resolvido,
                chave_acesso=chave_resolvida,
                manifestacao=manifestacao,
                status="Sucesso" if ok else "Falha",
                detalhe=detalhe,
                usuario=usuario,
            )
        )
        db.session.commit()
        return {
            "sucesso": ok,
            "msg": detalhe,
            "status_code": status_code,
            "payload": payload,
        }
    except Exception as exc:
        detalhe = str(exc)[:500]
        db.session.add(
            LogManifestacaoDestinatario(
                numero_nota=numero_resolvido,
                chave_acesso=chave_resolvida,
                manifestacao=manifestacao,
                status="Falha",
                detalhe=detalhe,
                usuario=usuario,
            )
        )
        db.session.commit()
        return {"sucesso": False, "msg": detalhe, "status_code": 500}


def _manifestar_confirmacao_operacao(numero_nota: str, usuario: str):
    return _manifestar_destinatario(
        numero_nota=numero_nota,
        usuario=usuario,
        manifestacao="confirmada",
        descricao_evento="Confirmação do Destinatário",
    )


def _manifestar_operacao_nao_realizada(numero_nota: str, usuario: str, justificativa: str):
    return _manifestar_destinatario(
        numero_nota=numero_nota,
        usuario=usuario,
        manifestacao="operacao_nao_realizada",
        descricao_evento="Operação não realizada",
        justificativa=justificativa,
    )


def _armazenar_nota_no_wms(numero_nota: str, usuario: str):
    """
    Armazena todos os itens de uma nota no WMS automaticamente quando a nota é lançada.
    Se não houver localização automática disponível, registra o item sem localização
    e retorna aviso.
    
    Args:
        numero_nota: Número da nota fiscal
        usuario: Usuário fazendo o lançamento
        
    Returns:
        {
            'sucesso': bool,
            'itens_armazenados': int,
            'itens_sem_localizacao': int,
            'mensagem': str
        }
    """
    try:
        # Obtém todos os itens da nota
        itens = ItemNota.query.filter_by(numero_nota=numero_nota, status='Lançado').all()
        
        if not itens:
            return {
                'sucesso': True,
                'itens_armazenados': 0,
                'itens_sem_localizacao': 0,
                'mensagem': 'Nenhum item para armazenar'
            }
        
        itens_armazenados = 0
        itens_sem_localizacao = 0
        
        for item in itens:
            try:
                codigo_item = item.codigo
                qtd = item.qtd_real or 0
                
                # Tenta encontrar localização automática
                localizacao = WMSService.requisitar_localizacao_automatica(
                    codigo_item=codigo_item,
                    qtd=qtd,
                    usuario=usuario
                )
                
                if localizacao:
                    # Armazena com localização automática
                    WMSService.armazenar_item_nota(
                        numero_nota=numero_nota,
                        codigo_item=codigo_item,
                        localizacao_id=localizacao.id,
                        usuario=usuario,
                        qtd_recebida=qtd
                    )
                    itens_armazenados += 1
                else:
                    # Armazena sem localização (pendente de alocação manual)
                    WMSService.armazenar_item_nota(
                        numero_nota=numero_nota,
                        codigo_item=codigo_item,
                        localizacao_id=None,
                        usuario=usuario,
                        qtd_recebida=qtd
                    )
                    itens_sem_localizacao += 1
                    
            except Exception as e:
                current_app.logger.warning(f"Erro ao armazenar item {codigo_item} da nota {numero_nota}: {str(e)}")
                # Continua processando outros itens mesmo se um falhar
                continue
        
        total = itens_armazenados + itens_sem_localizacao
        mensagem = f"WMS: {itens_armazenados}/{total} itens armazenados"
        if itens_sem_localizacao > 0:
            mensagem += f" ({itens_sem_localizacao} sem localização automática)"
        
        return {
            'sucesso': True,
            'itens_armazenados': itens_armazenados,
            'itens_sem_localizacao': itens_sem_localizacao,
            'mensagem': mensagem
        }
        
    except Exception as e:
        current_app.logger.error(f"Erro ao integrar WMS para nota {numero_nota}: {str(e)}")
        return {
            'sucesso': False,
            'itens_armazenados': 0,
            'itens_sem_localizacao': 0,
            'mensagem': f'Erro na integração WMS: {str(e)}'
        }


def _admin_access_query_from_request():
    q_usuario = (request.args.get("usuario") or "").strip().lower()
    q_rota = (request.args.get("rota") or "").strip().lower()
    q_metodo = (request.args.get("metodo") or "").strip().upper()
    data_ini = _parse_date(request.args.get("data_ini"))
    data_fim = _parse_date(request.args.get("data_fim"))

    query = LogAcessoAdministrativo.query
    if q_usuario:
        query = query.filter(func.lower(LogAcessoAdministrativo.usuario).contains(q_usuario))
    if q_rota:
        query = query.filter(func.lower(LogAcessoAdministrativo.rota).contains(q_rota))
    if q_metodo:
        query = query.filter(LogAcessoAdministrativo.metodo == q_metodo)
    if data_ini:
        start_dt = datetime.combine(data_ini, datetime.min.time())
        query = query.filter(LogAcessoAdministrativo.data >= start_dt)
    if data_fim:
        end_dt = datetime.combine(data_fim, datetime.max.time())
        query = query.filter(LogAcessoAdministrativo.data <= end_dt)

    return query


def _serialize_admin_access_log(log):
    return {
        "usuario": log.usuario,
        "rota": log.rota,
        "metodo": log.metodo,
        "data": log.data.strftime("%d/%m/%Y %H:%M:%S") if log.data else "---",
    }


def _build_historico_records(
    search_nota=None,
    status_filter=None,
    fornecedor=None,
    conferente=None,
    revertido_por=None,
    data_ini=None,
    data_fim=None,
):
    notas = [n[0] for n in db.session.query(ItemNota.numero_nota).distinct().all()]
    notas_ativas = {str(n) for n in notas}
    lista = []
    termo = (search_nota or "").strip()

    for numero_nota in notas:
        if termo and termo not in str(numero_nota):
            continue

        itens = ItemNota.query.filter_by(numero_nota=numero_nota).all()
        if not itens:
            continue

        statuses = {item.status for item in itens}
        status_nota = (
            "Lançado"
            if "Lançado" in statuses
            else "Concluído"
            if "Concluído" in statuses
            else "Devolvido"
            if "Devolvido" in statuses
            else "Pendente"
        )

        inicios = [item.inicio_conferencia for item in itens if item.inicio_conferencia]
        fins = [item.fim_conferencia for item in itens if item.fim_conferencia]
        tempo_conf = (
            f"{int((max(fins) - min(inicios)).total_seconds() / 60)} min"
            if (inicios and fins)
            else "---"
        )

        resumo_divergencia = _summarize_divergencia_nota(numero_nota)
        logs_reversao = (
            LogReversaoConferencia.query.filter_by(numero_nota=numero_nota)
            .order_by(LogReversaoConferencia.data_reversao.desc())
            .all()
        )
        logs_estorno_lancamento = (
            LogEstornoLancamento.query.filter_by(numero_nota=numero_nota)
            .order_by(LogEstornoLancamento.data_estorno.desc())
            .all()
        )
        eventos_ajuste = []
        for ev in logs_reversao:
            eventos_ajuste.append(
                {
                    "tipo": "Ajuste",
                    "usuario": ev.usuario_reversao,
                    "motivo": ev.motivo,
                    "data": ev.data_reversao,
                }
            )
        for ev in logs_estorno_lancamento:
            eventos_ajuste.append(
                {
                    "tipo": "Ajuste",
                    "usuario": ev.usuario_estorno,
                    "motivo": ev.motivo,
                    "data": ev.data_estorno,
                }
            )
        ultimo_ajuste = max(eventos_ajuste, key=lambda x: x["data"]) if eventos_ajuste else None
        lancado_por = next((item.usuario_lancamento for item in itens if item.usuario_lancamento), "---")
        codigo_lancamento = next((item.numero_lancamento for item in itens if item.numero_lancamento), "---")
        data_ref = max(
            [
                d
                for d in [
                    next((i.data_importacao for i in itens if i.data_importacao), None),
                    next((i.fim_conferencia for i in itens if i.fim_conferencia), None),
                    next((i.data_lancamento for i in itens if i.data_lancamento), None),
                    ultimo_ajuste["data"] if ultimo_ajuste else None,
                ]
                if d is not None
            ],
            default=None,
        )

        rec = {
            "nota": numero_nota,
            "fornecedor": itens[0].fornecedor,
            "status": status_nota,
            "conferido_por": next(
                (item.usuario_conferencia for item in itens if item.usuario_conferencia),
                "---",
            ),
            "tempo_conf": tempo_conf,
            "divergencia": resumo_divergencia["divergencia"],
            "divergencia_status": resumo_divergencia["divergencia_status"],
            "tentativas": resumo_divergencia["tentativas_invalidas"],
            "lancado_por": lancado_por,
            "codigo_lancamento": codigo_lancamento,
            "detalhe_divergencia": resumo_divergencia["detalhe_divergencia"],
            "alterado_por": ultimo_ajuste["usuario"] if ultimo_ajuste else "---",
            "motivo_alteracao": ultimo_ajuste["motivo"] if ultimo_ajuste else "---",
            "data_alteracao": ultimo_ajuste["data"].strftime("%d/%m/%Y %H:%M") if ultimo_ajuste else "---",
            "qtd_ajustes": len(eventos_ajuste),
            "tipo_ajuste": ultimo_ajuste["tipo"] if ultimo_ajuste else "---",
            # Compatibilidade com telas/exports existentes
            "revertido_por": ultimo_ajuste["usuario"] if ultimo_ajuste else "---",
            "motivo_reversao": ultimo_ajuste["motivo"] if ultimo_ajuste else "---",
            "data_reversao": ultimo_ajuste["data"].strftime("%d/%m/%Y %H:%M") if ultimo_ajuste else "---",
            "qtd_estornos": len(eventos_ajuste),
            "documentos_disponiveis": any(str(item.chave_acesso or "").strip() for item in itens),
            "data_ref": data_ref,
        }
        if _match_filters(rec, status_filter, fornecedor, conferente, revertido_por, data_ini, data_fim):
            lista.append(rec)

    exclusoes_query = LogExclusaoNota.query
    if termo:
        exclusoes_query = exclusoes_query.filter_by(numero_nota=termo)

    for log_exc in exclusoes_query.order_by(LogExclusaoNota.data_exclusao.desc()).all():
        numero_nota = str(log_exc.numero_nota)
        if numero_nota in notas_ativas:
            continue

        rec = {
            "nota": numero_nota,
            "fornecedor": log_exc.fornecedor or "---",
            "status": "Excluída",
            "conferido_por": "---",
            "tempo_conf": "---",
            "divergencia": "Não",
            "tentativas": 0,
            "lancado_por": "---",
            "codigo_lancamento": "---",
            "detalhe_divergencia": "",
            "alterado_por": log_exc.usuario_exclusao,
            "motivo_alteracao": f"Exclusão de NF pendente: {log_exc.motivo}",
            "data_alteracao": log_exc.data_exclusao.strftime("%d/%m/%Y %H:%M"),
            "qtd_ajustes": 1,
            "tipo_ajuste": "Ajuste",
            "revertido_por": log_exc.usuario_exclusao,
            "motivo_reversao": f"Exclusão de NF pendente: {log_exc.motivo}",
            "data_reversao": log_exc.data_exclusao.strftime("%d/%m/%Y %H:%M"),
            "qtd_estornos": 1,
            "documentos_disponiveis": False,
            "data_ref": log_exc.data_exclusao,
        }
        if _match_filters(rec, status_filter, fornecedor, conferente, revertido_por, data_ini, data_fim):
            lista.append(rec)

    lista.sort(key=lambda x: x.get("data_ref") or datetime.min, reverse=True)

    for rec in lista:
        rec.pop("data_ref", None)
    return lista


def _build_estornos_records(search_nota=None):
    eventos = []
    termo = (search_nota or "").strip()

    query_reversao = LogReversaoConferencia.query
    query_estorno_lanc = LogEstornoLancamento.query
    query_exclusao = LogExclusaoNota.query
    if termo:
        query_reversao = query_reversao.filter_by(numero_nota=termo)
        query_estorno_lanc = query_estorno_lanc.filter_by(numero_nota=termo)
        query_exclusao = query_exclusao.filter_by(numero_nota=termo)

    reversoes_conf = query_reversao.all()
    estornos_lanc = query_estorno_lanc.all()
    exclusoes = query_exclusao.all()

    for item in reversoes_conf:
        eventos.append(
            {
                "nota": item.numero_nota,
                "tipo": "Ajuste",
                "usuario": item.usuario_reversao,
                "motivo": item.motivo,
                "data": item.data_reversao,
                "data_fmt": item.data_reversao.strftime("%d/%m/%Y %H:%M"),
            }
        )

    for item in estornos_lanc:
        eventos.append(
            {
                "nota": item.numero_nota,
                "tipo": "Ajuste",
                "usuario": item.usuario_estorno,
                "motivo": item.motivo,
                "data": item.data_estorno,
                "data_fmt": item.data_estorno.strftime("%d/%m/%Y %H:%M"),
            }
        )

    for item in exclusoes:
        eventos.append(
            {
                "nota": item.numero_nota,
                "tipo": "Ajuste",
                "usuario": item.usuario_exclusao,
                "motivo": item.motivo,
                "data": item.data_exclusao,
                "data_fmt": item.data_exclusao.strftime("%d/%m/%Y %H:%M"),
            }
        )

    eventos.sort(key=lambda e: e["data"], reverse=True)
    for e in eventos:
        del e["data"]
    return eventos


def _build_notas_liberadas_records(search_nota=None):
    termo = str(search_nota or "").strip()
    registros = []
    notas = (
        db.session.query(ItemNota.numero_nota)
        .filter(ItemNota.status.in_(["Concluído", "Lançado"]))
        .distinct()
        .all()
    )

    for row in notas:
        numero_nota = str(row[0] or "").strip()
        if not numero_nota:
            continue

        itens = ItemNota.query.filter_by(numero_nota=numero_nota).all()
        if not itens:
            continue

        if termo:
            fornecedor_ref = str(itens[0].fornecedor or "").strip().lower()
            if termo.lower() not in numero_nota.lower() and termo.lower() not in fornecedor_ref:
                continue

        statuses = {str(item.status or "").strip() for item in itens}
        status_atual = (
            "Lançado"
            if "Lançado" in statuses
            else "Concluído"
            if "Concluído" in statuses
            else "Devolvido"
            if "Devolvido" in statuses
            else "Pendente"
        )
        fim_conferencia = max((item.fim_conferencia for item in itens if item.fim_conferencia), default=None)
        data_lancamento = max((item.data_lancamento for item in itens if item.data_lancamento), default=None)
        data_importacao = max((item.data_importacao for item in itens if item.data_importacao), default=None)
        ultimo_estorno_lancamento = (
            LogEstornoLancamento.query.filter_by(numero_nota=numero_nota)
            .order_by(LogEstornoLancamento.data_estorno.desc())
            .first()
        )
        ultima_reversao_conferencia = (
            LogReversaoConferencia.query.filter_by(numero_nota=numero_nota)
            .order_by(LogReversaoConferencia.data_reversao.desc())
            .first()
        )
        data_referencia = max(
            [
                data
                for data in [
                    fim_conferencia,
                    data_lancamento,
                    data_importacao,
                    ultimo_estorno_lancamento.data_estorno if ultimo_estorno_lancamento else None,
                    ultima_reversao_conferencia.data_reversao if ultima_reversao_conferencia else None,
                ]
                if data is not None
            ],
            default=None,
        )
        liberado_por = next((item.usuario_conferencia for item in itens if item.usuario_conferencia), None)
        if not liberado_por:
            liberado_por = next((item.usuario_lancamento for item in itens if item.usuario_lancamento), "---")

        total_itens = len(itens)
        total_quantidade = sum(float(item.qtd_real or 0) for item in itens)
        liberacoes = max(1, LogEstornoLancamento.query.filter_by(numero_nota=numero_nota).count())
        resumo_divergencia = _summarize_divergencia_nota(numero_nota)
        motivo_liberacao = (
            resumo_divergencia["detalhe_divergencia"]
            if resumo_divergencia["divergencia"] == "Sim" and resumo_divergencia["detalhe_divergencia"]
            else "Conferência concluída e liberada para o fluxo fiscal."
        )

        registros.append(
            {
                "numero": numero_nota,
                "fornecedor": itens[0].fornecedor or "---",
                "status_atual": status_atual,
                "liberado_por": liberado_por,
                "motivo": motivo_liberacao,
                "data_liberacao": data_referencia.strftime("%d/%m/%Y %H:%M") if data_referencia else "---",
                "ordem_evento": data_referencia.isoformat() if data_referencia else None,
                "total_itens": total_itens,
                "total_quantidade": total_quantidade,
                "liberacoes": liberacoes,
                "documentos_disponiveis": any(str(item.chave_acesso or "").strip() for item in itens),
                "data_ref": data_referencia or datetime.min,
            }
        )

    registros.sort(key=lambda item: item.get("data_ref") or datetime.min, reverse=True)
    for registro in registros:
        registro.pop("data_ref", None)
    return registros


@api_bp.route("/api/registrar", methods=["POST"])
@roles_required("Admin")
def registrar():
    data = register_schema.load(request.json or {})
    username = data.get("username")

    if Usuario.query.filter_by(username=username).first():
        return jsonify({"sucesso": False, "msg": "Usuário já existe"}), 400

    novo = Usuario(
        username=username,
        password=generate_password_hash(data.get("password")),
        role=data.get("role"),
    )
    db.session.add(novo)
    db.session.commit()
    return jsonify({"sucesso": True})


@api_bp.route("/api/listar_usuarios")
@roles_required("Admin")
def listar_usuarios():
    usuarios = Usuario.query.all()
    return jsonify([{"username": u.username, "role": u.role} for u in usuarios])


@api_bp.route("/api/deletar_usuario/<username>", methods=["DELETE"])
@roles_required("Admin")
def deletar_usuario(username):
    if username == session.get("username"):
        return jsonify({"sucesso": False, "msg": "Erro: mesma conta"}), 400

    user = Usuario.query.filter_by(username=username).first()
    if user:
        db.session.delete(user)
        db.session.commit()
        return jsonify({"sucesso": True})
    return jsonify({"sucesso": False}), 404


@api_bp.route("/importar_xml", methods=["POST"])
@roles_required("Admin")
def importar_xml():
    arquivos = request.files.getlist("xml")
    contador = 0

    for file in arquivos:
        contador += process_xml_and_store(file.read(), session["username"], status_inicial="AguardandoLiberacao")

    db.session.commit()
    return jsonify({"msg": f"{contador} notas importadas!"})


@api_bp.route("/api/consyste/download", methods=["POST"])
@roles_required("Admin", "Portaria")
def consyste_download():
    data = consyste_download_schema.load(request.json or {})
    chave_bruta = data.get("chave", "").strip()
    chave = re.sub(r"\D", "", chave_bruta)

    if len(chave) != 44:
        return jsonify({"error": f"Chave inválida: possui {len(chave)} dígitos."}), 400

    token = current_app.config.get("CONSYSTE_TOKEN")
    url = f"{current_app.config['CONSYSTE_API_BASE']}/nfe/{chave}/download.xml"

    try:
        resp = requests.get(url, headers={"X-Consyste-Auth-Token": token}, timeout=15)
        if not resp.ok:
            return jsonify({"error": f"Consyste diz: {resp.text}"}), resp.status_code

        added = process_xml_and_store(resp.content, session["username"], status_inicial="AguardandoLiberacao")
        db.session.commit()
        return jsonify(
            {
                "msg": (
                    "XML importado com sucesso! NF aguardando liberação do admin para conferência."
                    if added
                    else "Nota já estava no banco"
                ),
            }
        )
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@api_bp.route("/api/consyste/documento")
@roles_required("Admin", "Fiscal", "Conferente")
def consyste_documento():
    tipo = request.args.get("tipo", "xml")
    numero_nota = request.args.get("nota")
    chave_param = request.args.get("chave")
    numero_resolvido, chave_resolvida, itens = _resolve_nota_context(numero_nota=numero_nota, chave_acesso=chave_param)

    if not chave_resolvida and chave_param:
        chave_resolvida = re.sub(r"\D", "", str(chave_param).strip())

    if numero_nota and not itens:
        return jsonify({"error": "NF não encontrada para download do documento."}), 404

    try:
        resp, tipo_resolvido = _fetch_consyste_document_bytes(chave_resolvida, tipo)
        if not resp.ok:
            return jsonify({"error": f"Consyste diz: {resp.text}"}), resp.status_code

        extensao = "pdf" if tipo_resolvido == "pdf" else "xml"
        mime = "application/pdf" if tipo_resolvido == "pdf" else "application/xml"
        nome_base = numero_resolvido or chave_resolvida or "documento_nf"
        return send_file(
            io.BytesIO(resp.content),
            mimetype=mime,
            as_attachment=True,
            download_name=f"NF_{nome_base}.{extensao}",
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@api_bp.route("/api/consyste/listar")
@roles_required("Admin", "Portaria")
def consyste_list():
    token = current_app.config.get("CONSYSTE_TOKEN")
    numero = (request.args.get("numero") or "").strip()
    chave = re.sub(r"\D", "", (request.args.get("chave") or "").strip())
    if not numero and not chave:
        return jsonify({"error": "Informe número da nota ou chave de acesso."}), 400

    headers = {
        "X-Consyste-Auth-Token": token,
        "Accept": "application/json",
    }

    def _normalizar(documentos):
        resultado = []
        for doc in documentos:
            resultado.append(
                {
                    "id": doc.get("id"),
                    "numero": str(doc.get("numero", "")),
                    "emissao": doc.get("emitido_em") or doc.get("emissao"),
                    "emitente_nome": doc.get("emit_nome") or doc.get("emitente_nome") or doc.get("dest_nome"),
                    "chave": doc.get("chave"),
                }
            )
        return resultado

    try:
        if chave:
            if len(chave) != 44:
                return jsonify({"error": f"Chave inválida: possui {len(chave)} dígitos."}), 400

            consulta_url = f"{current_app.config['CONSYSTE_API_BASE']}/nfe/{chave}"
            resp = requests.get(consulta_url, headers=headers, timeout=15)
            if resp.status_code == 200:
                dados = resp.json()
                doc = dados if isinstance(dados, dict) else {}
                return jsonify({"documentos": _normalizar([doc])})
            if resp.status_code == 404:
                return jsonify({"documentos": []})
            return jsonify({"error": f"Consyste diz: {resp.text}"}), resp.status_code

        # Busca por numero de NF-e na lista de recebidos/todos/emitidos.
        filtros = ["recebidos", "todos", "emitidos"]
        termos = [f"numero:{numero}", numero]
        for filtro in filtros:
            base_url = f"{current_app.config['CONSYSTE_API_BASE']}/nfe/lista/{filtro}"
            for termo in termos:
                params = {
                    "q": termo,
                    "campos": "id,chave,emitido_em,numero,emit_nome,dest_nome",
                }
                resp = requests.get(base_url, headers=headers, params=params, timeout=15)
                if resp.status_code != 200:
                    continue

                dados = resp.json() if resp.content else {}
                docs = dados.get("documentos", []) if isinstance(dados, dict) else []
                docs_filtrados = [d for d in docs if str(d.get("numero", "")).strip() == str(numero).strip()]
                if docs_filtrados:
                    return jsonify({"documentos": _normalizar(docs_filtrados)})

        return jsonify({"documentos": []})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@api_bp.route("/api/pendentes")
@roles_required("Conferente", "Admin", "Fiscal")
def listar_pendentes():
    notas = (
        db.session.query(ItemNota.numero_nota, ItemNota.fornecedor)
        .filter_by(status="Pendente")
        .distinct()
        .all()
    )
    return jsonify([{"numero": n[0], "fornecedor": n[1]} for n in notas])


@api_bp.route("/api/notas_aguardando_liberacao")
@roles_required("Admin")
def listar_notas_aguardando_liberacao():
    rows = (
        db.session.query(
            ItemNota.numero_nota,
            ItemNota.fornecedor,
            func.max(ItemNota.usuario_importacao),
            func.max(ItemNota.data_importacao),
        )
        .filter_by(status="AguardandoLiberacao")
        .group_by(ItemNota.numero_nota, ItemNota.fornecedor)
        .order_by(func.max(ItemNota.data_importacao).desc())
        .all()
    )

    return jsonify(
        [
            {
                "numero": r[0],
                "fornecedor": r[1],
                "importado_por": r[2] or "---",
                "data_importacao": r[3].strftime("%d/%m/%Y %H:%M") if r[3] else "---",
            }
            for r in rows
        ]
    )


@api_bp.route("/api/admin/liberar_nota_conferencia", methods=["POST"])
@roles_required("Admin")
def liberar_nota_conferencia():
    payload = nota_schema.load(request.json or {})
    numero_nota = str(payload.get("nota") or "").strip()
    if not numero_nota:
        return jsonify({"sucesso": False, "msg": "NF obrigatória."}), 400

    itens = ItemNota.query.filter_by(numero_nota=numero_nota).all()
    if not itens:
        return jsonify({"sucesso": False, "msg": "NF não encontrada."}), 404

    status_set = {str(i.status or "").strip() for i in itens}
    if status_set == {"AguardandoLiberacao"}:
        ItemNota.query.filter_by(numero_nota=numero_nota, status="AguardandoLiberacao").update({"status": "Pendente"})
        db.session.add(
            LogAcessoAdministrativo(
                usuario=session.get("username", "admin"),
                rota=f"/api/admin/liberar_nota_conferencia?nota={numero_nota}",
                metodo=request.method,
            )
        )
        db.session.commit()
        return jsonify({"sucesso": True, "msg": "NF liberada para conferência."})

    if "Pendente" in status_set:
        return jsonify({"sucesso": False, "msg": "NF já está liberada para conferência."}), 409

    return jsonify({"sucesso": False, "msg": "NF não está em estado aguardando liberação."}), 409


@api_bp.route("/api/expedicao/conferencia/relatorios")
@roles_required("Conferente", "Admin", "Fiscal")
def listar_relatorios_expedicao_conferencia():
    max_reports = _parse_positive_int(request.args.get("max_reports"), default=500, min_value=1, max_value=3000)
    base_dir = current_app.config.get("EXPEDICAO_REPORTS_DIR", r"Z:\PUBLICO\SNData\eReports")
    result = list_conferencia_reports(base_dir=base_dir, max_reports=max_reports)

    conferences = {
        c.report_file_name: c
        for c in ExpedicaoConferencia.query.all()
    }

    open_reports = []
    closed_reports = []
    for report in result.get("reports", []):
        conf = conferences.get(report.get("file_name"))
        status_conf = conf.status if conf else "Aberta"
        report["status_conferencia"] = status_conf
        report["conferencia_id"] = conf.id if conf else None
        report["closed_at"] = conf.closed_at.strftime("%d/%m/%Y %H:%M") if conf and conf.closed_at else None
        report["registro_pendente"] = False
        report["itens_pendentes_registro"] = 0
        report["solicitacao_pendencia_ativa"] = False
        if conf:
            itens = ExpedicaoConferenciaItem.query.filter_by(conferencia_id=conf.id).all()
            pendentes = [
                i
                for i in itens
                if (int(i.qtd_conferida or 0) > 0) and (int(i.qtd_faturada or 0) < int(i.qtd_conferida or 0))
            ]
            report["itens_pendentes_registro"] = len(pendentes)
            report["registro_pendente"] = len(pendentes) > 0
            solicitacao_ativa = (
                ExpedicaoConferenciaDecisao.query.filter_by(
                    conferencia_id=conf.id,
                    tipo="SolicitacaoPendencia",
                    ativa=True,
                )
                .order_by(ExpedicaoConferenciaDecisao.data.desc())
                .first()
            )
            report["solicitacao_pendencia_ativa"] = bool(solicitacao_ativa)
        if status_conf == "Fechada":
            closed_reports.append(report)
        else:
            open_reports.append(report)

    result["reports"] = open_reports
    result["reports_abertos"] = open_reports
    result["reports_fechados"] = closed_reports
    result["total_abertos"] = len(open_reports)
    result["total_fechados"] = len(closed_reports)
    return jsonify(result)


@api_bp.route("/api/expedicao/conferencia/relatorio")
@roles_required("Conferente", "Admin", "Fiscal")
def obter_relatorio_expedicao_conferencia():
    file_name = (request.args.get("file_name") or "").strip()
    if not file_name:
        return jsonify({"error": "file_name e obrigatorio."}), 400

    base_dir = current_app.config.get("EXPEDICAO_REPORTS_DIR", r"Z:\PUBLICO\SNData\eReports")
    try:
        is_admin = session.get("role") == "Admin"
        parsed = parse_conferencia_report(base_dir=base_dir, file_name=file_name)

        conferencia = ExpedicaoConferencia.query.filter_by(report_file_name=file_name).first()
        if not conferencia:
            conferencia = ExpedicaoConferencia(
                report_file_name=file_name,
                report_file_path=parsed.get("file_path", ""),
                status="Aberta",
                created_by=session.get("username", "desconhecido"),
                updated_at=datetime.now(),
            )
            db.session.add(conferencia)
            db.session.flush()

            for item in parsed.get("items", []):
                db.session.add(
                    ExpedicaoConferenciaItem(
                        conferencia_id=conferencia.id,
                        item_index=int(item.get("index", 0)),
                        codigo=str(item.get("nome_peca") or "---"),
                        nome_peca=str(item.get("nome_peca") or "---"),
                        dimensao=str(item.get("dimensao") or "---"),
                        os_numero=str(item.get("os") or "---"),
                        cliente=str(item.get("cliente") or "---"),
                        imagem=str(item.get("imagem") or ""),
                        qtd_html=int(item.get("qtd_esperada") or 0),
                        qtd_conferida=0,
                        qtd_faturada=0,
                        divergente=False,
                    )
                )
            db.session.commit()

        itens_db = (
            ExpedicaoConferenciaItem.query.filter_by(conferencia_id=conferencia.id)
            .order_by(ExpedicaoConferenciaItem.item_index.asc())
            .all()
        )

        items_payload = []
        for item in itens_db:
            payload_item = {
                "id": item.id,
                "index": item.item_index,
                "codigo": item.codigo,
                "nome_peca": item.nome_peca,
                "dimensao": item.dimensao,
                "os": item.os_numero,
                "cliente": item.cliente,
                "qtd_conferida": item.qtd_conferida,
                "qtd_faturada": item.qtd_faturada,
                "qtd_restante": max((item.qtd_html or 0) - (item.qtd_faturada or 0), 0),
                "divergente": bool(item.divergente),
                "imagem": item.imagem,
            }
            if is_admin:
                payload_item["qtd_esperada"] = item.qtd_html
            items_payload.append(payload_item)

        registro_pendente = any(
            (int(i.qtd_conferida or 0) > 0) and (int(i.qtd_faturada or 0) < int(i.qtd_html or 0))
            for i in itens_db
        )

        faturamentos = (
            ExpedicaoFaturamento.query.filter_by(conferencia_id=conferencia.id, ativo=True)
            .order_by(ExpedicaoFaturamento.data.desc())
            .all()
        )

        ultima_decisao = (
            ExpedicaoConferenciaDecisao.query.filter_by(conferencia_id=conferencia.id, ativa=True)
            .order_by(ExpedicaoConferenciaDecisao.data.desc())
            .first()
        )

        nfs_vinculadas = sorted({f.numero_nf for f in faturamentos if f.numero_nf})

        return jsonify(
            {
                "conferencia_id": conferencia.id,
                "status": conferencia.status,
                "can_authorize_pendencia": session.get("role") == "Admin",
                "file_name": conferencia.report_file_name,
                "file_path": conferencia.report_file_path,
                "image_folder": parsed.get("image_folder"),
                "items": items_payload,
                "total_items": len(items_payload),
                "registro_pendente": registro_pendente,
                "decisao": {
                    "tipo": ultima_decisao.tipo,
                    "motivo": ultima_decisao.motivo,
                    "usuario": ultima_decisao.usuario,
                    "data": ultima_decisao.data.strftime("%d/%m/%Y %H:%M") if ultima_decisao.data else "---",
                }
                if ultima_decisao
                else None,
                "nfs_vinculadas": nfs_vinculadas,
                "faturamentos": [
                    {
                        "id": f.id,
                        "numero_nf": f.numero_nf,
                        "tipo": f.tipo,
                        "usuario": f.usuario,
                        "data": f.data.strftime("%d/%m/%Y %H:%M") if f.data else "---",
                    }
                    for f in faturamentos
                ],
                "solicitacao_pendencia_ativa": bool(
                    ExpedicaoConferenciaDecisao.query.filter_by(
                        conferencia_id=conferencia.id,
                        tipo="SolicitacaoPendencia",
                        ativa=True,
                    ).first()
                ),
            }
        )
    except FileNotFoundError as exc:
        return jsonify({"error": str(exc)}), 404
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@api_bp.route("/api/expedicao/conferencia/imagem")
@roles_required("Conferente", "Admin", "Fiscal")
def obter_imagem_expedicao_conferencia():
    file_name = (request.args.get("file_name") or "").strip()
    image_name = (request.args.get("image_name") or "").strip()
    if not file_name or not image_name:
        return jsonify({"error": "file_name e image_name sao obrigatorios."}), 400

    base_dir = current_app.config.get("EXPEDICAO_REPORTS_DIR", r"Z:\PUBLICO\SNData\eReports")
    try:
        image_path = resolve_report_image_path(base_dir=base_dir, file_name=file_name, image_name=image_name)
        return send_file(image_path)
    except FileNotFoundError as exc:
        return jsonify({"error": str(exc)}), 404
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@api_bp.route("/api/expedicao/conferencia/validar", methods=["POST"])
@roles_required("Conferente", "Admin", "Fiscal")
def validar_expedicao_conferencia():
    payload = request.get_json(silent=True) or {}
    file_name = str(payload.get("file_name") or "").strip()
    contagens = payload.get("contagens") or {}

    if not file_name:
        return jsonify({"error": "file_name e obrigatorio."}), 400
    if not isinstance(contagens, dict):
        return jsonify({"error": "contagens deve ser um objeto."}), 400

    base_dir = current_app.config.get("EXPEDICAO_REPORTS_DIR", r"Z:\PUBLICO\SNData\eReports")
    try:
        is_admin = session.get("role") == "Admin"
        conferencia = ExpedicaoConferencia.query.filter_by(report_file_name=file_name).first()
        if not conferencia:
            return jsonify({"error": "Conferencia nao inicializada. Abra o relatorio primeiro."}), 404

        itens_db = (
            ExpedicaoConferenciaItem.query.filter_by(conferencia_id=conferencia.id)
            .order_by(ExpedicaoConferenciaItem.item_index.asc())
            .all()
        )
        ja_tem_qtd_gravada = any(int(item.qtd_conferida or 0) > 0 for item in itens_db)
        if ja_tem_qtd_gravada and conferencia.status != "PendenteDecisao":
            return jsonify(
                {
                    "error": "Conferencia de quantidades ja foi gravada. Continue apenas com o registro de expedicao.",
                    "bloqueio_quantidade": True,
                }
            ), 409

        report_data = {
            "items": [
                {
                    "index": item.item_index,
                    "nome_peca": item.nome_peca,
                    "os": item.os_numero,
                    "cliente": item.cliente,
                    "qtd_esperada": item.qtd_html,
                }
                for item in itens_db
            ]
        }
        result = validate_blind_conference(report_data, contagens)

        by_index = {int(item.item_index): item for item in itens_db}
        for ok_item in result.get("ok", []):
            idx = int(ok_item.get("index", -1))
            if idx in by_index:
                by_index[idx].qtd_conferida = int(ok_item.get("qtd_auditada") or 0)
                by_index[idx].divergente = False

        for div_item in result.get("divergencias", []):
            idx = int(div_item.get("index", -1))
            if idx in by_index:
                by_index[idx].qtd_conferida = int(div_item.get("qtd_auditada") or 0)
                by_index[idx].divergente = True

        for pend_item in result.get("pendentes", []):
            idx = int(pend_item.get("index", -1))
            if idx in by_index:
                by_index[idx].qtd_conferida = 0
                by_index[idx].divergente = False

        tem_divergencia = bool(result.get("total_divergencias"))
        sem_conferencia_total = (
            int(result.get("total_ok") or 0) == 0
            and int(result.get("total_divergencias") or 0) == 0
            and int(result.get("total_pendentes") or 0) > 0
        )
        if tem_divergencia or sem_conferencia_total:
            conferencia.status = "PendenteDecisao"
            ExpedicaoConferenciaDecisao.query.filter_by(conferencia_id=conferencia.id, ativa=True).update({"ativa": False})
        elif conferencia.status != "Fechada":
            conferencia.status = "Aberta"

        conferencia.updated_at = datetime.now()
        db.session.commit()
        if not is_admin:
            for rec in result.get("ok", []):
                rec.pop("qtd_esperada", None)
            for rec in result.get("divergencias", []):
                rec.pop("qtd_esperada", None)
        result["conferencia_id"] = conferencia.id
        result["requer_decisao"] = bool(tem_divergencia or sem_conferencia_total)
        result["bloqueio_sem_conferencia_total"] = bool(sem_conferencia_total)
        return jsonify(result)
    except FileNotFoundError as exc:
        return jsonify({"error": str(exc)}), 404
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@api_bp.route("/api/expedicao/faturamento/foto", methods=["POST"])
@roles_required("Conferente", "Admin", "Fiscal")
def upload_foto_expedicao():
    arquivo = request.files.get("arquivo")
    file_name = str(request.form.get("file_name") or "").strip()
    item_id = str(request.form.get("item_id") or "").strip()
    numero_nf = str(request.form.get("numero_nf") or "").strip()

    if not arquivo or not file_name or not item_id or not numero_nf:
        return jsonify({"error": "arquivo, file_name, item_id e numero_nf sao obrigatorios."}), 400

    base_folder = os.path.join(current_app.instance_path, "expedicao_fotos")
    os.makedirs(base_folder, exist_ok=True)

    extensao = os.path.splitext(secure_filename(arquivo.filename or "foto.jpg"))[1] or ".jpg"
    nome_final = secure_filename(f"{file_name}_{numero_nf}_{item_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}{extensao}")
    caminho = os.path.join(base_folder, nome_final)
    arquivo.save(caminho)
    return jsonify({"foto_path": nome_final})


@api_bp.route("/api/expedicao/faturamento", methods=["POST"])
@roles_required("Conferente", "Admin", "Fiscal")
def registrar_faturamento_expedicao():
    payload = request.get_json(silent=True) or {}
    file_name = str(payload.get("file_name") or "").strip()
    numero_nf = str(payload.get("numero_nf") or "").strip()
    tipo = str(payload.get("tipo") or "").strip().capitalize()
    transporte_tipo = str(payload.get("transporte_tipo") or "Proprio").strip().capitalize()
    transportadora = str(payload.get("transportadora") or "").strip()
    placa = str(payload.get("placa") or "").strip().upper()
    motorista = str(payload.get("motorista") or "").strip()
    observacao = str(payload.get("observacao") or "").strip()
    try:
        peso_bruto = float(payload.get("peso_bruto") or 0)
    except Exception:
        return jsonify({"error": "peso_bruto invalido."}), 400
    itens = payload.get("itens") or []

    if not file_name or not numero_nf:
        return jsonify({"error": "file_name e numero_nf sao obrigatorios."}), 400
    if tipo not in {"Parcial", "Total"}:
        return jsonify({"error": "tipo deve ser Parcial ou Total."}), 400
    if transporte_tipo not in {"Proprio", "Transportadora"}:
        return jsonify({"error": "transporte_tipo deve ser Proprio ou Transportadora."}), 400
    if transporte_tipo == "Transportadora" and not transportadora:
        return jsonify({"error": "Informe a transportadora ou selecione transporte proprio."}), 400
    placa = placa or "N/I"
    motorista = motorista or "N/I"
    if peso_bruto < 0:
        return jsonify({"error": "Peso bruto invalido."}), 400
    if not isinstance(itens, list) or not itens:
        return jsonify({"error": "itens obrigatorios."}), 400

    conferencia = ExpedicaoConferencia.query.filter_by(report_file_name=file_name).first()
    if not conferencia:
        return jsonify({"error": "Conferencia nao encontrada."}), 404

    if conferencia.status == "Fechada":
        return jsonify({"error": "Conferencia ja fechada."}), 409

    itens_db = {
        i.id: i
        for i in ExpedicaoConferenciaItem.query.filter_by(conferencia_id=conferencia.id).all()
    }

    possui_divergencia = any(bool(i.divergente) for i in itens_db.values())
    possui_sem_conferencia = any(int(i.qtd_conferida or 0) <= 0 for i in itens_db.values())
    decisao_ativa = (
        ExpedicaoConferenciaDecisao.query.filter_by(conferencia_id=conferencia.id, ativa=True)
        .order_by(ExpedicaoConferenciaDecisao.data.desc())
        .first()
    )
    pendencia_autorizada = bool(decisao_ativa and decisao_ativa.tipo == "Pendencia")
    if possui_divergencia and not pendencia_autorizada:
        return jsonify({"error": "Existe divergencia. Registre decisao de expedicao com pendencia antes de faturar."}), 409
    if possui_sem_conferencia and not pendencia_autorizada:
        return jsonify({"error": "Existem itens sem conferencia. Somente com decisao de pendencia autorizada por admin."}), 409

    selecao = []
    alertas_parciais = []
    for row in itens:
        item_id = int(row.get("item_id") or 0)
        qtd = int(row.get("qtd_enviada") or 0)
        foto_path = str(row.get("foto_path") or "").strip()
        if item_id <= 0 or qtd < 0:
            return jsonify({"error": "item_id/qtd_enviada invalidos."}), 400
        if item_id not in itens_db:
            return jsonify({"error": f"Item {item_id} nao pertence ao relatorio."}), 400
        if qtd == 0:
            continue

        item_db = itens_db[item_id]
        if int(item_db.qtd_conferida or 0) <= 0 and not pendencia_autorizada:
            return jsonify({"error": f"Item {item_id} ainda nao foi conferido para expedicao."}), 409

        limite_conferido = max((item_db.qtd_conferida or 0) - (item_db.qtd_faturada or 0), 0)
        restante = max((item_db.qtd_html or 0) - (item_db.qtd_faturada or 0), 0)
        limite_operacional = restante if pendencia_autorizada else limite_conferido
        if qtd > restante:
            return jsonify({"error": f"Qtd enviada maior que saldo do NESH para item {item_id}."}), 400
        if qtd > limite_operacional:
            return jsonify({"error": f"Qtd enviada maior que quantidade conferida restante para item {item_id}."}), 400
        if (not pendencia_autorizada) and 0 < qtd < limite_conferido:
            alertas_parciais.append(
                {
                    "item_id": item_id,
                    "qtd_conferida_restante": limite_conferido,
                    "qtd_enviada": qtd,
                }
            )
        selecao.append((item_db, qtd, foto_path))

    if not selecao:
        return jsonify({"error": "Nenhum item com quantidade enviada."}), 400

    if tipo == "Total":
        faltando = [i for i in itens_db.values() if max((i.qtd_html or 0) - (i.qtd_faturada or 0), 0) > 0]
        selected_ids = {x[0].id for x in selecao}
        if any(i.id not in selected_ids for i in faltando):
            return jsonify({"error": "Faturamento Total exige envio de todos os itens restantes."}), 400
        for i in faltando:
            qtd_sel = next((q for item_ref, q, _ in selecao if item_ref.id == i.id), 0)
            restante = max((i.qtd_html or 0) - (i.qtd_faturada or 0), 0)
            if qtd_sel != restante:
                return jsonify({"error": f"No Total, item {i.id} deve enviar exatamente {restante}."}), 400

    faturamento = ExpedicaoFaturamento(
        conferencia_id=conferencia.id,
        numero_nf=numero_nf,
        tipo=tipo,
        transporte_tipo=transporte_tipo,
        transportadora=transportadora or None,
        placa=placa,
        motorista=motorista,
        peso_bruto=peso_bruto,
        observacao=observacao or None,
        usuario=session.get("username", "desconhecido"),
    )
    db.session.add(faturamento)
    db.session.flush()

    for item_db, qtd, foto_path in selecao:
        item_db.qtd_faturada = int(item_db.qtd_faturada or 0) + int(qtd)
        db.session.add(
            ExpedicaoFaturamentoItem(
                faturamento_id=faturamento.id,
                conferencia_item_id=item_db.id,
                qtd_enviada=qtd,
                foto_path=foto_path,
            )
        )

    conferencia.updated_at = datetime.now()
    aberto = any(max((i.qtd_html or 0) - (i.qtd_faturada or 0), 0) > 0 for i in itens_db.values())
    if not aberto:
        conferencia.status = "Fechada"
        conferencia.closed_at = datetime.now()
        conferencia.closed_by = session.get("username", "desconhecido")

    db.session.commit()
    return jsonify(
        {
            "sucesso": True,
            "faturamento_id": faturamento.id,
            "status": conferencia.status,
            "alertas_parciais": alertas_parciais,
        }
    )


@api_bp.route("/api/expedicao/conferencia/decisao", methods=["POST"])
@roles_required("Conferente", "Admin", "Fiscal")
def registrar_decisao_expedicao_conferencia():
    payload = request.get_json(silent=True) or {}
    file_name = str(payload.get("file_name") or "").strip()
    tipo = str(payload.get("tipo") or "").strip().capitalize()
    motivo = str(payload.get("motivo") or "").strip()
    if not file_name or tipo not in {"Recontar", "Pendencia"} or not motivo:
        return jsonify({"error": "file_name, tipo(Recontar|Pendencia) e motivo sao obrigatorios."}), 400

    if tipo == "Pendencia" and session.get("role") != "Admin":
        return jsonify({"error": "Somente admin pode autorizar pendencia. Solicite no painel admin."}), 403

    conferencia = ExpedicaoConferencia.query.filter_by(report_file_name=file_name).first()
    if not conferencia:
        return jsonify({"error": "Conferencia nao encontrada."}), 404

    itens_db = ExpedicaoConferenciaItem.query.filter_by(conferencia_id=conferencia.id).all()
    possui_divergencia = any(bool(i.divergente) for i in itens_db)
    possui_sem_conferencia = any(int(i.qtd_conferida or 0) <= 0 for i in itens_db)
    if tipo == "Recontar" and not possui_divergencia:
        return jsonify({"error": "Nao ha divergencia ativa para registrar decisao de recontagem."}), 409
    if tipo == "Pendencia" and not possui_divergencia and not possui_sem_conferencia:
        return jsonify({"error": "Nao ha itens com divergencia ou sem conferencia para autorizar pendencia."}), 409

    ExpedicaoConferenciaDecisao.query.filter_by(conferencia_id=conferencia.id, ativa=True).update({"ativa": False})
    db.session.add(
        ExpedicaoConferenciaDecisao(
            conferencia_id=conferencia.id,
            tipo=tipo,
            motivo=motivo,
            usuario=session.get("username", "desconhecido"),
            ativa=True,
        )
    )

    if tipo == "Recontar":
        for item in itens_db:
            if item.divergente:
                item.divergente = False
                item.qtd_conferida = 0
        conferencia.status = "Aberta"
    else:
        conferencia.status = "Aberta"

    conferencia.updated_at = datetime.now()
    db.session.commit()
    return jsonify({"sucesso": True, "status": conferencia.status})


@api_bp.route("/api/expedicao/conferencia/solicitar_autorizacao", methods=["POST"])
@roles_required("Conferente", "Admin", "Fiscal")
def solicitar_autorizacao_expedicao_conferencia():
    payload = request.get_json(silent=True) or {}
    file_name = str(payload.get("file_name") or "").strip()
    motivo = str(payload.get("motivo") or "").strip()
    if not file_name or not motivo:
        return jsonify({"error": "file_name e motivo sao obrigatorios."}), 400

    conferencia = ExpedicaoConferencia.query.filter_by(report_file_name=file_name).first()
    if not conferencia:
        return jsonify({"error": "Conferencia nao encontrada."}), 404

    solicitacao_ativa = (
        ExpedicaoConferenciaDecisao.query.filter_by(
            conferencia_id=conferencia.id,
            tipo="SolicitacaoPendencia",
            ativa=True,
        )
        .order_by(ExpedicaoConferenciaDecisao.data.desc())
        .first()
    )
    if solicitacao_ativa:
        return jsonify({"error": "Ja existe solicitacao pendente para esta conferencia."}), 409

    db.session.add(
        ExpedicaoConferenciaDecisao(
            conferencia_id=conferencia.id,
            tipo="SolicitacaoPendencia",
            motivo=motivo,
            usuario=session.get("username", "desconhecido"),
            ativa=True,
        )
    )
    conferencia.status = "PendenteDecisao"
    conferencia.updated_at = datetime.now()
    db.session.commit()
    return jsonify({"sucesso": True, "status": conferencia.status})


@api_bp.route("/api/expedicao/romaneio")
@roles_required("Conferente", "Admin", "Fiscal")
def romaneio_expedicao():
    file_name = (request.args.get("file_name") or "").strip()
    if not file_name:
        return jsonify({"error": "file_name e obrigatorio."}), 400

    conferencia = ExpedicaoConferencia.query.filter_by(report_file_name=file_name).first()
    if not conferencia:
        return jsonify({"error": "Conferencia nao encontrada."}), 404

    itens = (
        ExpedicaoConferenciaItem.query.filter_by(conferencia_id=conferencia.id)
        .order_by(ExpedicaoConferenciaItem.item_index.asc())
        .all()
    )
    faturamentos = (
        ExpedicaoFaturamento.query.filter_by(conferencia_id=conferencia.id, ativo=True)
        .order_by(ExpedicaoFaturamento.data.asc())
        .all()
    )

    numeros_nf = sorted({f.numero_nf for f in faturamentos if f.numero_nf})
    linhas = []
    total_enviado = 0
    for item in itens:
        enviado = int(item.qtd_faturada or 0)
        if enviado <= 0:
            continue
        total_enviado += enviado
        linhas.append(
            {
                "codigo": item.codigo,
                "nome_peca": item.nome_peca,
                "os": item.os_numero,
                "cliente": item.cliente,
                "qtd_enviada": enviado,
                "qtd_nesh": int(item.qtd_html or 0),
            }
        )

    return jsonify(
        {
            "arquivo": conferencia.report_file_name,
            "status": conferencia.status,
            "gerado_em": datetime.now().strftime("%d/%m/%Y %H:%M"),
            "nfs": numeros_nf,
            "total_itens": len(linhas),
            "total_enviado": total_enviado,
            "faturamentos": [
                {
                    "numero_nf": f.numero_nf,
                    "tipo": f.tipo,
                    "transporte_tipo": f.transporte_tipo,
                    "transportadora": f.transportadora,
                    "placa": f.placa,
                    "motorista": f.motorista,
                    "peso_bruto": f.peso_bruto,
                    "observacao": f.observacao,
                    "data": f.data.strftime("%d/%m/%Y %H:%M") if f.data else "---",
                }
                for f in faturamentos
            ],
            "linhas": linhas,
        }
    )


@api_bp.route("/api/expedicao/romaneios")
@roles_required("Conferente", "Admin", "Fiscal")
def listar_romaneios_expedicao():
    numero_nf = (request.args.get("numero_nf") or "").strip()

    query = ExpedicaoFaturamento.query.filter_by(ativo=True)
    if numero_nf:
        query = query.filter_by(numero_nf=numero_nf)

    faturamentos = query.order_by(ExpedicaoFaturamento.data.desc()).limit(300).all()
    payload = []
    for fat in faturamentos:
        conferencia = ExpedicaoConferencia.query.get(fat.conferencia_id)
        if not conferencia:
            continue

        itens_fat = (
            ExpedicaoFaturamentoItem.query.filter_by(faturamento_id=fat.id, ativo=True)
            .order_by(ExpedicaoFaturamentoItem.id.asc())
            .all()
        )

        linhas = []
        total_enviado = 0
        for it in itens_fat:
            conf_item = ExpedicaoConferenciaItem.query.get(it.conferencia_item_id)
            if not conf_item:
                continue
            qtd = int(it.qtd_enviada or 0)
            total_enviado += qtd
            linhas.append(
                {
                    "codigo": conf_item.codigo,
                    "nome_peca": conf_item.nome_peca,
                    "os": conf_item.os_numero,
                    "cliente": conf_item.cliente,
                    "qtd_enviada": qtd,
                    "qtd_nesh": int(conf_item.qtd_html or 0),
                }
            )

        payload.append(
            {
                "faturamento_id": fat.id,
                "arquivo": conferencia.report_file_name,
                "numero_nf": fat.numero_nf,
                "tipo": fat.tipo,
                "transporte_tipo": fat.transporte_tipo,
                "transportadora": fat.transportadora,
                "placa": fat.placa,
                "motorista": fat.motorista,
                "peso_bruto": fat.peso_bruto,
                "observacao": fat.observacao,
                "status_conferencia": conferencia.status,
                "usuario": fat.usuario,
                "data": fat.data.strftime("%d/%m/%Y %H:%M") if fat.data else "---",
                "total_enviado": total_enviado,
                "linhas": linhas,
            }
        )

    return jsonify(payload)


@api_bp.route("/api/expedicao/vinculos")
@roles_required("Conferente", "Admin", "Fiscal")
def vinculos_html_nf_expedicao():
    file_name = (request.args.get("file_name") or "").strip()
    numero_nf = (request.args.get("numero_nf") or "").strip()

    query = ExpedicaoFaturamento.query.filter_by(ativo=True)
    if file_name:
        conferencia = ExpedicaoConferencia.query.filter_by(report_file_name=file_name).first()
        if not conferencia:
            return jsonify([])
        query = query.filter_by(conferencia_id=conferencia.id)
    if numero_nf:
        query = query.filter_by(numero_nf=numero_nf)

    rows = query.order_by(ExpedicaoFaturamento.data.desc()).limit(400).all()
    payload = []
    for fat in rows:
        conf = ExpedicaoConferencia.query.get(fat.conferencia_id)
        if not conf:
            continue
        payload.append(
            {
                "conferencia_id": conf.id,
                "arquivo_html": conf.report_file_name,
                "status_conferencia": conf.status,
                "numero_nf": fat.numero_nf,
                "tipo": fat.tipo,
                "usuario": fat.usuario,
                "data": fat.data.strftime("%d/%m/%Y %H:%M") if fat.data else "---",
            }
        )
    return jsonify(payload)


@api_bp.route("/api/expedicao/rastreabilidade")
@roles_required("Conferente", "Admin", "Fiscal")
def rastreabilidade_expedicao():
    q_os = (request.args.get("os") or "").strip().lower()
    q_codigo = (request.args.get("codigo") or "").strip().lower()
    q_cliente = (request.args.get("cliente") or "").strip().lower()

    query = ExpedicaoConferenciaItem.query.join(
        ExpedicaoConferencia,
        ExpedicaoConferencia.id == ExpedicaoConferenciaItem.conferencia_id,
    )
    if q_os:
        query = query.filter(func.lower(ExpedicaoConferenciaItem.os_numero).contains(q_os))
    if q_codigo:
        query = query.filter(func.lower(ExpedicaoConferenciaItem.codigo).contains(q_codigo))
    if q_cliente:
        query = query.filter(func.lower(ExpedicaoConferenciaItem.cliente).contains(q_cliente))

    is_admin = session.get("role") == "Admin"
    rows = query.order_by(ExpedicaoConferencia.updated_at.desc()).limit(300).all()
    payload = []
    for item in rows:
        conferencia = ExpedicaoConferencia.query.get(item.conferencia_id)
        row = {
            "conferencia_id": item.conferencia_id,
            "arquivo": conferencia.report_file_name if conferencia else "---",
            "status_conferencia": conferencia.status if conferencia else "---",
            "codigo": item.codigo,
            "nome_peca": item.nome_peca,
            "os": item.os_numero,
            "cliente": item.cliente,
            "qtd_conferida": item.qtd_conferida,
            "qtd_faturada": item.qtd_faturada,
            "qtd_restante": max((item.qtd_html or 0) - (item.qtd_faturada or 0), 0),
            "divergente": bool(item.divergente),
        }
        if is_admin:
            row["qtd_html"] = item.qtd_html
        payload.append(row)
    return jsonify(payload)


@api_bp.route("/api/admin/expedicao/estornar_total", methods=["POST"])
@roles_required("Admin")
def admin_estornar_total_expedicao():
    payload = request.get_json(silent=True) or {}
    conferencia_id = int(payload.get("conferencia_id") or 0)
    motivo = str(payload.get("motivo") or "").strip()
    if conferencia_id <= 0 or not motivo:
        return jsonify({"error": "conferencia_id e motivo sao obrigatorios."}), 400

    conferencia = ExpedicaoConferencia.query.get(conferencia_id)
    if not conferencia:
        return jsonify({"error": "Conferencia nao encontrada."}), 404

    faturamentos = ExpedicaoFaturamento.query.filter_by(conferencia_id=conferencia_id, ativo=True).all()
    if not faturamentos:
        return jsonify({"error": "Nao ha faturamento ativo para estornar."}), 409

    for fat in faturamentos:
        itens = ExpedicaoFaturamentoItem.query.filter_by(faturamento_id=fat.id, ativo=True).all()
        for it in itens:
            conf_item = ExpedicaoConferenciaItem.query.get(it.conferencia_item_id)
            if conf_item:
                conf_item.qtd_faturada = max((conf_item.qtd_faturada or 0) - int(it.qtd_enviada or 0), 0)
            it.ativo = False
            db.session.add(
                ExpedicaoEstorno(
                    conferencia_id=conferencia_id,
                    faturamento_id=fat.id,
                    conferencia_item_id=it.conferencia_item_id,
                    tipo="Total",
                    motivo=motivo,
                    usuario=session.get("username", "admin"),
                )
            )
        fat.ativo = False

    conferencia.status = "Aberta"
    conferencia.closed_at = None
    conferencia.closed_by = None
    conferencia.updated_at = datetime.now()
    db.session.commit()
    return jsonify({"sucesso": True})


@api_bp.route("/api/admin/expedicao/faturamentos")
@roles_required("Admin")
def admin_listar_faturamentos_expedicao():
    conferencia_id = int(request.args.get("conferencia_id") or 0)

    query = ExpedicaoFaturamento.query.filter_by(ativo=True)
    if conferencia_id > 0:
        query = query.filter_by(conferencia_id=conferencia_id)

    faturamentos = query.order_by(ExpedicaoFaturamento.data.desc()).limit(200).all()
    payload = []
    for fat in faturamentos:
        itens = (
            ExpedicaoFaturamentoItem.query.filter_by(faturamento_id=fat.id, ativo=True)
            .order_by(ExpedicaoFaturamentoItem.id.asc())
            .all()
        )
        payload.append(
            {
                "faturamento_id": fat.id,
                "conferencia_id": fat.conferencia_id,
                "numero_nf": fat.numero_nf,
                "tipo": fat.tipo,
                "usuario": fat.usuario,
                "data": fat.data.strftime("%d/%m/%Y %H:%M") if fat.data else "---",
                "itens": [
                    {
                        "faturamento_item_id": it.id,
                        "conferencia_item_id": it.conferencia_item_id,
                        "qtd_enviada": it.qtd_enviada,
                        "foto_path": it.foto_path,
                    }
                    for it in itens
                ],
            }
        )
    return jsonify(payload)


@api_bp.route("/api/admin/expedicao/solicitacoes_pendencia")
@roles_required("Admin")
def admin_listar_solicitacoes_pendencia_expedicao():
    rows = (
        ExpedicaoConferenciaDecisao.query.filter_by(tipo="SolicitacaoPendencia", ativa=True)
        .order_by(ExpedicaoConferenciaDecisao.data.desc())
        .limit(300)
        .all()
    )
    payload = []
    for row in rows:
        conferencia = ExpedicaoConferencia.query.get(row.conferencia_id)
        payload.append(
            {
                "solicitacao_id": row.id,
                "conferencia_id": row.conferencia_id,
                "arquivo": conferencia.report_file_name if conferencia else "---",
                "status_conferencia": conferencia.status if conferencia else "---",
                "solicitante": row.usuario,
                "motivo": row.motivo,
                "data": row.data.strftime("%d/%m/%Y %H:%M") if row.data else "---",
            }
        )
    return jsonify(payload)


@api_bp.route("/api/admin/expedicao/aprovar_pendencia", methods=["POST"])
@roles_required("Admin")
def admin_aprovar_pendencia_expedicao():
    payload = request.get_json(silent=True) or {}
    solicitacao_id = int(payload.get("solicitacao_id") or 0)
    observacao = str(payload.get("observacao") or "").strip()
    if solicitacao_id <= 0:
        return jsonify({"error": "solicitacao_id e obrigatorio."}), 400

    solicitacao = ExpedicaoConferenciaDecisao.query.filter_by(
        id=solicitacao_id,
        tipo="SolicitacaoPendencia",
        ativa=True,
    ).first()
    if not solicitacao:
        return jsonify({"error": "Solicitacao nao encontrada ou ja processada."}), 404

    conferencia = ExpedicaoConferencia.query.get(solicitacao.conferencia_id)
    if not conferencia:
        return jsonify({"error": "Conferencia nao encontrada."}), 404

    ExpedicaoConferenciaDecisao.query.filter_by(conferencia_id=conferencia.id, ativa=True).update({"ativa": False})
    db.session.add(
        ExpedicaoConferenciaDecisao(
            conferencia_id=conferencia.id,
            tipo="Pendencia",
            motivo=(
                f"Aprovacao admin via painel. Solicitacao: {solicitacao.motivo}"
                + (f" | Obs: {observacao}" if observacao else "")
            ),
            usuario=session.get("username", "admin"),
            ativa=True,
        )
    )
    conferencia.status = "Aberta"
    conferencia.updated_at = datetime.now()
    db.session.commit()
    return jsonify({"sucesso": True, "conferencia_id": conferencia.id})


@api_bp.route("/api/admin/expedicao/estornar_parcial", methods=["POST"])
@roles_required("Admin")
def admin_estornar_parcial_expedicao():
    payload = request.get_json(silent=True) or {}
    faturamento_item_ids = payload.get("faturamento_item_ids") or []
    motivo = str(payload.get("motivo") or "").strip()
    if not isinstance(faturamento_item_ids, list) or not faturamento_item_ids or not motivo:
        return jsonify({"error": "faturamento_item_ids e motivo sao obrigatorios."}), 400

    rows = ExpedicaoFaturamentoItem.query.filter(ExpedicaoFaturamentoItem.id.in_(faturamento_item_ids)).all()
    if not rows:
        return jsonify({"error": "Itens de faturamento nao encontrados."}), 404

    for row in rows:
        if not row.ativo:
            continue
        fat = ExpedicaoFaturamento.query.get(row.faturamento_id)
        if not fat:
            continue
        conf_item = ExpedicaoConferenciaItem.query.get(row.conferencia_item_id)
        if conf_item:
            conf_item.qtd_faturada = max((conf_item.qtd_faturada or 0) - int(row.qtd_enviada or 0), 0)
        row.ativo = False
        db.session.add(
            ExpedicaoEstorno(
                conferencia_id=fat.conferencia_id,
                faturamento_id=row.faturamento_id,
                conferencia_item_id=row.conferencia_item_id,
                tipo="Parcial",
                motivo=motivo,
                usuario=session.get("username", "admin"),
            )
        )

        ativos_restantes = ExpedicaoFaturamentoItem.query.filter_by(faturamento_id=fat.id, ativo=True).count()
        if ativos_restantes == 0:
            fat.ativo = False
        conferencia = ExpedicaoConferencia.query.get(fat.conferencia_id)
        if conferencia:
            conferencia.status = "Aberta"
            conferencia.closed_at = None
            conferencia.closed_by = None
            conferencia.updated_at = datetime.now()

    db.session.commit()
    return jsonify({"sucesso": True})


@api_bp.route("/api/pendentes_priorizadas")
@roles_required("Conferente", "Admin", "Fiscal")
def listar_pendentes_priorizadas():
    notas = (
        db.session.query(ItemNota.numero_nota, ItemNota.fornecedor)
        .filter_by(status="Pendente")
        .distinct()
        .all()
    )
    priorizadas = []
    for n in notas:
        item = _compute_pending_priority(n[0], n[1])
        if item:
            priorizadas.append(item)
    priorizadas.sort(key=lambda x: x["score"], reverse=True)
    return jsonify(priorizadas)


@api_bp.route("/api/excluir_nota_pendente", methods=["POST"])
@roles_required("Admin", "Fiscal")
def excluir_nota_pendente():
    payload = excluir_nota_schema.load(request.json or {})
    numero_nota = str(payload.get("nota")).strip()
    confirmacao_nota = str(payload.get("confirmacao_nota")).strip()
    motivo = payload.get("motivo").strip()

    if confirmacao_nota != numero_nota:
        return (
            jsonify(
                {
                    "sucesso": False,
                    "msg": "Confirmação inválida. Digite o mesmo número da NF para confirmar a exclusão.",
                }
            ),
            400,
        )

    itens = ItemNota.query.filter_by(numero_nota=numero_nota).all()
    if not itens:
        return jsonify({"sucesso": False, "msg": "Nota não encontrada."}), 404

    if any((item.status or "") != "Pendente" for item in itens):
        return (
            jsonify(
                {
                    "sucesso": False,
                    "msg": "Somente notas com status Pendente podem ser excluídas nesta tela.",
                }
            ),
            409,
        )

    db.session.add(
        LogExclusaoNota(
            numero_nota=numero_nota,
            fornecedor=itens[0].fornecedor,
            usuario_exclusao=session.get("username", "desconhecido"),
            motivo=motivo,
        )
    )

    ItemNota.query.filter_by(numero_nota=numero_nota).delete()
    LogDivergencia.query.filter_by(numero_nota=numero_nota).delete()
    LogReversaoConferencia.query.filter_by(numero_nota=numero_nota).delete()
    LogEstornoLancamento.query.filter_by(numero_nota=numero_nota).delete()
    _release_lock(numero_nota)
    db.session.add(
        LogAcessoAdministrativo(
            usuario=session.get("username", "desconhecido"),
            rota=f"/api/excluir_nota_pendente?nota={numero_nota}&motivo={motivo[:80]}",
            metodo=request.method,
        )
    )
    db.session.commit()
    return jsonify({"sucesso": True})


@api_bp.route("/api/itens/<nota>")
@roles_required("Conferente", "Admin", "Fiscal")
def buscar_itens(nota):
    ok_lock, lock = _acquire_lock(nota, session["username"])
    if not ok_lock:
        return (
            jsonify(
                {
                    "error": "Nota em conferência por outro usuário.",
                    "usuario": lock.usuario,
                    "expira_em": lock.lock_until.strftime("%H:%M:%S"),
                }
            ),
            423,
        )

    itens = _filter_itens_para_conferencia(ItemNota.query.filter_by(numero_nota=nota, status="Pendente").all())
    if not itens:
        _release_lock(nota)
        db.session.commit()
        return jsonify({"error": "NF sem itens elegíveis para conferência."}), 404
    for item in itens:
        if not item.inicio_conferencia:
            item.inicio_conferencia = datetime.now()
    db.session.commit()
    return jsonify(
        [
            {
                "id": item.id,
                "codigo": item.codigo,
                "descricao": item.descricao,
                "qtd_esperada": item.qtd_real,
                "unidade": item.unidade_comercial or "UN",
            }
            for item in itens
        ]
    )


@api_bp.route("/api/lock/release", methods=["POST"])
@roles_required("Conferente", "Admin", "Fiscal")
def release_lock():
    data = request.get_json(silent=True) or {}
    payload = nota_schema.load(data)
    _release_lock(str(payload.get("nota")))
    db.session.commit()
    return jsonify({"sucesso": True})


@api_bp.route("/api/lock/heartbeat", methods=["POST"])
@roles_required("Conferente", "Admin", "Fiscal")
def lock_heartbeat():
    data = request.get_json(silent=True) or {}
    payload = nota_schema.load(data)
    numero_nota = str(payload.get("nota"))
    lock = ConferenciaLock.query.filter_by(numero_nota=numero_nota).first()
    if not lock:
        return jsonify({"sucesso": False, "msg": "Lock não encontrado."}), 404
    if lock.usuario != session.get("username"):
        return jsonify({"sucesso": False, "msg": "Lock pertence a outro usuário."}), 409

    now = datetime.now()
    lock.lock_until = now + timedelta(minutes=current_app.config.get("LOCK_TIMEOUT_MINUTES", 25))
    lock.heartbeat_at = now
    db.session.commit()
    return jsonify({"sucesso": True})


@api_bp.route("/api/checklist/<nota>")
@roles_required("Conferente", "Admin", "Fiscal")
def obter_checklist(nota):
    checklist = ChecklistRecebimento.query.filter_by(numero_nota=str(nota)).first()
    if not checklist:
        return jsonify({"existe": False})
    return jsonify(
        {
            "existe": True,
            "usuario": checklist.usuario,
            "lacre_ok": bool(checklist.lacre_ok),
            "volumes_ok": bool(checklist.volumes_ok),
            "avaria_visual": bool(checklist.avaria_visual),
            "etiqueta_ok": bool(checklist.etiqueta_ok),
            "observacao": checklist.observacao or "",
            "data": checklist.data.strftime("%d/%m/%Y %H:%M") if checklist.data else "---",
        }
    )


@api_bp.route("/api/checklist", methods=["POST"])
@roles_required("Conferente", "Admin", "Fiscal")
def gravar_checklist():
    data = request.get_json(silent=True) or {}
    numero_nota = str(data.get("nota") or "").strip()
    if not numero_nota:
        return jsonify({"sucesso": False, "msg": "NF obrigatória."}), 400

    checklist = ChecklistRecebimento.query.filter_by(numero_nota=numero_nota).first()
    if not checklist:
        checklist = ChecklistRecebimento(numero_nota=numero_nota, usuario=session["username"])
        db.session.add(checklist)

    checklist.usuario = session["username"]
    checklist.lacre_ok = bool(data.get("lacre_ok"))
    checklist.volumes_ok = bool(data.get("volumes_ok"))
    checklist.avaria_visual = bool(data.get("avaria_visual"))
    checklist.etiqueta_ok = bool(data.get("etiqueta_ok"))
    checklist.observacao = str(data.get("observacao") or "").strip()[:500]
    checklist.data = datetime.now()
    db.session.commit()
    return jsonify({"sucesso": True})


@api_bp.route("/api/divergencia/evidencia", methods=["POST"])
@roles_required("Conferente", "Admin", "Fiscal")
def upload_evidencia_divergencia():
    file = request.files.get("arquivo")
    numero_nota = str(request.form.get("nota") or "").strip()
    item_id = str(request.form.get("item_id") or "").strip()

    if not file:
        return jsonify({"sucesso": False, "msg": "Arquivo não enviado."}), 400
    if not numero_nota or not item_id:
        return jsonify({"sucesso": False, "msg": "NF e item são obrigatórios."}), 400

    folder = os.path.join(current_app.instance_path, "evidencias")
    os.makedirs(folder, exist_ok=True)

    base_name = secure_filename(file.filename or "evidencia")
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    final_name = f"{numero_nota}_{item_id}_{timestamp}_{base_name}"
    file_path = os.path.join(folder, final_name)
    file.save(file_path)

    return jsonify({"sucesso": True, "arquivo": final_name})


@api_bp.route("/api/tentativas/<nota>")
@roles_required("Conferente", "Admin", "Fiscal")
def listar_tentativas_conferencia(nota):
    rows = (
        LogTentativaConferencia.query.filter_by(numero_nota=str(nota))
        .order_by(LogTentativaConferencia.tentativa_numero.asc(), LogTentativaConferencia.id.asc())
        .all()
    )
    return jsonify(
        [
            {
                "tentativa": r.tentativa_numero,
                "item_id": r.item_id,
                "qtd_esperada": r.qtd_esperada,
                "qtd_digitada": r.qtd_digitada,
                "qtd_convertida": r.qtd_convertida,
                "unidade": r.unidade_informada,
                "fator": r.fator_conversao,
                "status": r.status_item,
                "motivo": r.motivo,
                "usuario": r.usuario,
                "data": r.data.strftime("%d/%m/%Y %H:%M:%S") if r.data else "---",
            }
            for r in rows
        ]
    )


@api_bp.route("/validar", methods=["POST"])
@roles_required("Conferente", "Admin", "Fiscal")
def validar():
    user = session["username"]
    dados = validar_schema.load(request.json or {})
    numero_nota = str(dados.get("nota"))
    contagens = dados.get("contagens", {})
    motivos_itens = dados.get("motivos_itens", {})
    motivos_tipos = dados.get("motivos_tipos", {})
    motivos_observacoes = dados.get("motivos_observacoes", {})
    destinos_itens = dados.get("destinos_itens", {})
    evidencias_itens = dados.get("evidencias_itens", {})
    conversoes_itens = dados.get("conversoes_itens", {})
    checklist_payload = dados.get("checklist", {})
    forcar_pendencia = dados.get("forcar_pendencia") is True

    itens_db = _filter_itens_para_conferencia(ItemNota.query.filter_by(numero_nota=numero_nota, status="Pendente").all())
    if not itens_db:
        return jsonify({"sucesso": False, "msg": "NF sem itens pendentes para conferência."}), 404

    resultado_itens = []
    erros = []
    divergencias_ids = []
    tentativa_numero = (
        (LogTentativaConferencia.query.filter_by(numero_nota=numero_nota).order_by(LogTentativaConferencia.tentativa_numero.desc()).first() or LogTentativaConferencia(tentativa_numero=0)).tentativa_numero
        + 1
    )

    checklist = ChecklistRecebimento.query.filter_by(numero_nota=numero_nota).first()
    if not checklist and forcar_pendencia:
        if not checklist_payload:
            return jsonify({"sucesso": False, "msg": "Checklist inicial obrigatório para concluir recebimento."}), 400
        checklist = ChecklistRecebimento(numero_nota=numero_nota, usuario=user)
        db.session.add(checklist)

    if checklist_payload:
        if not checklist:
            checklist = ChecklistRecebimento(numero_nota=numero_nota, usuario=user)
            db.session.add(checklist)
        checklist.usuario = user
        checklist.lacre_ok = bool(checklist_payload.get("lacre_ok"))
        checklist.volumes_ok = bool(checklist_payload.get("volumes_ok"))
        checklist.avaria_visual = bool(checklist_payload.get("avaria_visual"))
        checklist.etiqueta_ok = bool(checklist_payload.get("etiqueta_ok"))
        checklist.observacao = str(checklist_payload.get("observacao") or "").strip()[:500]
        checklist.data = datetime.now()

    for item in itens_db:
        valor_bruto = contagens.get(str(item.id))
        if valor_bruto is None or str(valor_bruto).strip() == "":
            erros.append(item.descricao)
            divergencias_ids.append(str(item.id))
            resultado_itens.append(
                {
                    "id": item.id,
                    "descricao": item.descricao,
                    "status": "DIVERGÊNCIA",
                    "msg": "Quantidade não informada.",
                }
            )
            continue

        valor_digitado = str(valor_bruto).replace(",", ".")
        try:
            quantidade = float(valor_digitado)
            if quantidade < 0:
                erros.append(item.descricao)
                divergencias_ids.append(str(item.id))
                resultado_itens.append(
                    {
                        "id": item.id,
                        "descricao": item.descricao,
                        "status": "DIVERGÊNCIA",
                        "msg": "Quantidade negativa não permitida.",
                    }
                )
                continue

            conv_cfg = conversoes_itens.get(str(item.id), {}) if isinstance(conversoes_itens, dict) else {}
            fator = 1.0
            unidade_informada = ""
            if isinstance(conv_cfg, dict):
                unidade_informada = str(conv_cfg.get("unidade") or "").strip()[:20]
                try:
                    fator = float(str(conv_cfg.get("fator") or "1").replace(",", "."))
                except Exception:
                    fator = 1.0
            if fator <= 0:
                fator = 1.0

            quantidade_convertida = quantidade * fator

            if not _quantidade_esta_dentro_da_tolerancia(item, quantidade_convertida):
                msg_erro = _compose_motivo_pendencia(item.id, motivos_itens, motivos_tipos, motivos_observacoes) or "Divergência"
                unidade_item = _normalize_unidade_medida(item.unidade_comercial)
                msg_resultado = (
                    f"Quantidade fora da tolerância de 2% para {unidade_item}."
                    if unidade_item in {"KG", "MM"}
                    else "Quantidade divergente."
                )
                motivo_tipo = str(motivos_tipos.get(str(item.id)) or "Não classificado")[:80]
                destino_fisico = str(destinos_itens.get(str(item.id)) or "Aguardando decisão fiscal")[:80]
                evidencia = str(evidencias_itens.get(str(item.id)) or "")[:300]
                erros.append(item.descricao)
                divergencias_ids.append(str(item.id))
                db.session.add(
                    LogDivergencia(
                        numero_nota=numero_nota,
                        item_descricao=f"{item.descricao} (Motivo: {msg_erro})",
                        qtd_esperada=item.qtd_real,
                        qtd_contada=quantidade_convertida,
                        usuario_erro=user,
                        motivo_tipo=motivo_tipo,
                        destino_fisico=destino_fisico,
                        evidencia_path=evidencia,
                        tentativa_numero=tentativa_numero,
                    )
                )
                _append_attempt_log(
                    numero_nota,
                    item,
                    tentativa_numero,
                    quantidade,
                    quantidade_convertida,
                    unidade_informada,
                    fator,
                    "DIVERGÊNCIA",
                    msg_erro,
                    user,
                )
                resultado_itens.append(
                    {
                        "id": item.id,
                        "descricao": item.descricao,
                        "status": "DIVERGÊNCIA",
                        "msg": msg_resultado,
                    }
                )
            else:
                divergencia_tolerada = _quantidade_divergente_mas_tolerada(item, quantidade_convertida)
                msg_ok = (
                    f"Quantidade diferente da NF, mas dentro da tolerância de 2% para {_normalize_unidade_medida(item.unidade_comercial)}. Será informado como divergência de peso."
                    if divergencia_tolerada
                    else "Conferido."
                )
                _append_attempt_log(
                    numero_nota,
                    item,
                    tentativa_numero,
                    quantidade,
                    quantidade_convertida,
                    unidade_informada,
                    fator,
                    "OK",
                    msg_ok,
                    user,
                )
                resultado_itens.append(
                    {"id": item.id, "descricao": item.descricao, "status": "OK", "msg": msg_ok}
                )
        except Exception:
            erros.append(item.descricao)
            divergencias_ids.append(str(item.id))
            _append_attempt_log(
                numero_nota,
                item,
                tentativa_numero,
                None,
                None,
                "",
                1.0,
                "DIVERGÊNCIA",
                "Quantidade inválida",
                user,
            )
            resultado_itens.append(
                {
                    "id": item.id,
                    "descricao": item.descricao,
                    "status": "DIVERGÊNCIA",
                    "msg": "Quantidade inválida.",
                }
            )

    if forcar_pendencia and divergencias_ids:
        motivos_resolvidos = {
            item_id: _compose_motivo_pendencia(item_id, motivos_itens, motivos_tipos, motivos_observacoes)
            for item_id in divergencias_ids
        }
        motivos_faltantes = [
            item_id
            for item_id in divergencias_ids
            if len(str(motivos_resolvidos.get(item_id, "")).strip()) < 5
        ]
        if motivos_faltantes:
            return (
                jsonify(
                    {
                        "sucesso": False,
                        "msg": "Informe motivo com no mínimo 5 caracteres para cada divergência.",
                        "itens": resultado_itens,
                        "motivos_pendentes": motivos_faltantes,
                    }
                ),
                400,
            )
    else:
        motivos_resolvidos = {}

    if forcar_pendencia:
        ItemNota.query.filter_by(numero_nota=numero_nota, status="Pendente").update(
            {
                "status": "Concluído",
                "usuario_conferencia": user,
                "fim_conferencia": datetime.now(),
            }
        )

        decisao_consyste = "receber" if not motivos_resolvidos else "receber_com_pendencia"
        resumo_motivos = (
            "; ".join([f"{valor}" for _, valor in motivos_resolvidos.items()])
            if motivos_resolvidos
            else "Conferência Cega OK"
        )

        nota_db = ItemNota.query.filter_by(numero_nota=numero_nota).first()
        chave_api = nota_db.chave_acesso if nota_db else None
        _release_lock(numero_nota)
        db.session.commit()

        if chave_api:
            enviar_decisao_consyste(chave_api, decisao_consyste, resumo_motivos)
    else:
        db.session.commit()

    total_itens = len(resultado_itens)
    total_ok = len([i for i in resultado_itens if i.get("status") == "OK"])
    total_divergencias = total_itens - total_ok
    if total_divergencias == 0:
        # Limpa divergencias historicas da NF quando a recontagem fecha 100% correta.
        LogDivergencia.query.filter_by(numero_nota=numero_nota).delete()
        db.session.commit()
    destinos_resumo = sorted(
        {
            str(destinos_itens.get(str(item_id), "Aguardando decisão fiscal")).strip()
            for item_id in divergencias_ids
            if str(destinos_itens.get(str(item_id), "Aguardando decisão fiscal")).strip()
        }
    )

    payload = {
        "sucesso": not erros,
        "itens": resultado_itens,
        "resumo": {
            "nota": numero_nota,
            "total_itens": total_itens,
            "ok": total_ok,
            "divergencias": total_divergencias,
            "tentativa": tentativa_numero,
        },
    }

    if forcar_pendencia and total_divergencias > 0:
        payload["pendencia_confirmada"] = True
        payload["instrucoes_pendencia"] = {
            "titulo": "Recebimento com pendência registrado",
            "destinos": destinos_resumo,
            "passos": [
                "Identifique fisicamente os itens divergentes com etiqueta de pendência.",
                "Segregue os itens na área correspondente ao destino definido.",
                "Anexe evidências faltantes antes do fechamento do turno.",
                "Comunique fiscal/qualidade com o número da NF e os motivos registrados.",
                "Confirme no estoque físico que apenas os itens conformes seguiram para armazenagem.",
            ],
        }

    return jsonify(payload)


@api_bp.route("/api/devolver_material", methods=["POST"])
@roles_required("Admin")
def devolver_material():
    data = devolver_schema.load(request.json or {})
    numero_nota = data.get("nota")
    motivo = data.get("motivo")
    usuario = session.get("username", "admin")

    nota_db = ItemNota.query.filter_by(numero_nota=numero_nota).first()
    if nota_db and nota_db.chave_acesso:
        manifestacao_result = _manifestar_operacao_nao_realizada(numero_nota, usuario, motivo)
        if not manifestacao_result.get("sucesso"):
            return jsonify({"sucesso": False, "msg": manifestacao_result.get("msg")}), manifestacao_result.get("status_code") or 502

        ok, status_code, payload = enviar_decisao_consyste(nota_db.chave_acesso, "devolver", f"DEVOLUÇÃO: {motivo}")
        payload = _normalize_external_payload(payload)
        if not ok:
            detalhe = (
                (payload or {}).get("error")
                or (payload or {}).get("motivo")
                or (payload or {}).get("raw")
                or "Falha ao enviar devolução para o Consyste."
            )
            return jsonify({"sucesso": False, "msg": str(detalhe)[:500]}), status_code or 502

    ItemNota.query.filter_by(numero_nota=numero_nota).update({"status": "Devolvido"})
    _release_lock(numero_nota)
    db.session.commit()
    return jsonify({"sucesso": True})


@api_bp.route("/api/recebimento/solicitar_devolucao", methods=["POST"])
@roles_required("Conferente", "Admin", "Fiscal")
def solicitar_devolucao_recebimento():
    data = devolver_schema.load(request.json or {})
    numero_nota = str(data.get("nota"))
    motivo = str(data.get("motivo") or "").strip()
    usuario = session.get("username", "sistema")

    nota_db = ItemNota.query.filter_by(numero_nota=numero_nota).first()
    if not nota_db:
        return jsonify({"sucesso": False, "msg": "Nota não encontrada."}), 404

    if nota_db.status in {"Devolvido", "Lançado"}:
        return jsonify({"sucesso": False, "msg": f"NF já está em status {nota_db.status}."}), 409

    solicitacao_ativa = SolicitacaoDevolucaoRecebimento.query.filter_by(
        numero_nota=numero_nota,
        ativa=True,
        status="Pendente",
    ).first()
    if solicitacao_ativa:
        return jsonify({"sucesso": False, "msg": "Já existe solicitação de devolução aguardando aprovação admin para esta NF."}), 409

    SolicitacaoDevolucaoRecebimento.query.filter_by(numero_nota=numero_nota, ativa=True).update({"ativa": False})
    db.session.add(
        SolicitacaoDevolucaoRecebimento(
            numero_nota=numero_nota,
            fornecedor=nota_db.fornecedor,
            chave_acesso=nota_db.chave_acesso,
            usuario_solicitante=usuario,
            motivo=motivo,
            status="Pendente",
            ativa=True,
        )
    )
    ItemNota.query.filter_by(numero_nota=numero_nota).update({"status": "AguardandoDevolucao"})
    _release_lock(numero_nota)
    db.session.commit()

    return jsonify({"sucesso": True, "msg": "Solicitação enviada para aprovação admin."})


@api_bp.route("/api/admin/recebimento/solicitacoes_devolucao")
@roles_required("Admin")
def listar_solicitacoes_devolucao_recebimento():
    rows = (
        SolicitacaoDevolucaoRecebimento.query.filter_by(ativa=True, status="Pendente")
        .order_by(SolicitacaoDevolucaoRecebimento.data_solicitacao.desc())
        .all()
    )
    return jsonify(
        [
            {
                "id": row.id,
                "numero": row.numero_nota,
                "fornecedor": row.fornecedor,
                "solicitante": row.usuario_solicitante,
                "motivo": row.motivo,
                "data": row.data_solicitacao.strftime("%d/%m/%Y %H:%M:%S") if row.data_solicitacao else "---",
            }
            for row in rows
        ]
    )


@api_bp.route("/api/admin/recebimento/aprovar_devolucao", methods=["POST"])
@roles_required("Admin")
def aprovar_devolucao_recebimento():
    payload = aprovar_solicitacao_devolucao_schema.load(request.json or {})
    solicitacao_id = int(payload.get("solicitacao_id"))
    observacao_admin = str(payload.get("observacao_admin") or "").strip()
    usuario = session.get("username", "admin")

    solicitacao = SolicitacaoDevolucaoRecebimento.query.filter_by(
        id=solicitacao_id,
        ativa=True,
        status="Pendente",
    ).first()
    if not solicitacao:
        return jsonify({"sucesso": False, "msg": "Solicitação não encontrada ou já processada."}), 404

    nota_db = ItemNota.query.filter_by(numero_nota=solicitacao.numero_nota).first()
    if not nota_db:
        return jsonify({"sucesso": False, "msg": "NF vinculada à solicitação não encontrada."}), 404

    manifestacao_result = _manifestar_operacao_nao_realizada(
        solicitacao.numero_nota,
        usuario,
        solicitacao.motivo,
    )
    if not manifestacao_result.get("sucesso"):
        return (
            jsonify({"sucesso": False, "msg": manifestacao_result.get("msg")}),
            manifestacao_result.get("status_code") or 502,
        )

    if nota_db.chave_acesso:
        complemento = f" | Aprovação admin: {observacao_admin}" if observacao_admin else ""
        ok, status_code, payload = enviar_decisao_consyste(
            nota_db.chave_acesso,
            "devolver",
            f"DEVOLUÇÃO APROVADA | Solicitação: {solicitacao.motivo}{complemento}",
        )
        payload = _normalize_external_payload(payload)
        if not ok:
            detalhe = (
                (payload or {}).get("error")
                or (payload or {}).get("motivo")
                or (payload or {}).get("raw")
                or "Falha ao enviar devolução para o Consyste."
            )
            return jsonify({"sucesso": False, "msg": str(detalhe)[:500]}), status_code or 502

    ItemNota.query.filter_by(numero_nota=solicitacao.numero_nota).update({"status": "Devolvido"})
    solicitacao.status = "Aprovada"
    solicitacao.ativa = False
    solicitacao.usuario_aprovador = usuario
    solicitacao.observacao_admin = observacao_admin[:500]
    solicitacao.data_decisao = datetime.now()
    _release_lock(solicitacao.numero_nota)
    db.session.commit()

    return jsonify({"sucesso": True, "msg": "Devolução aprovada e enviada para Consyste."})


@api_bp.route("/api/admin/resetar_nota", methods=["POST"])
@roles_required("Admin", "Fiscal")
def resetar_nota_admin():
    data = reset_schema.load(request.json or {})
    numero_nota = data.get("nota")
    motivo = data.get("motivo")
    usuario = session["username"]
    nota_db = ItemNota.query.filter_by(numero_nota=numero_nota).first()

    if nota_db:
        possui_lancamento_ativo = (
            ItemNota.query.filter_by(numero_nota=numero_nota, status="Lançado").first() is not None
        )
        if possui_lancamento_ativo:
            return (
                jsonify(
                    {
                        "sucesso": False,
                        "msg": "Não é permitido reverter conferência de nota lançada. Estorne o lançamento fiscal primeiro.",
                    }
                ),
                409,
            )

        chave_acesso = nota_db.chave_acesso

        LogDivergencia.query.filter_by(numero_nota=numero_nota).delete()
        ItemNota.query.filter_by(numero_nota=numero_nota).update(
            {
                "status": "Pendente",
                "usuario_conferencia": None,
                "fim_conferencia": None,
                "inicio_conferencia": None,
                "numero_lancamento": None,
                "usuario_lancamento": None,
                "data_lancamento": None,
            }
        )

        db.session.add(
            LogReversaoConferencia(
                numero_nota=numero_nota,
                usuario_reversao=usuario,
                motivo=motivo,
            )
        )

        _release_lock(numero_nota)

        db.session.commit()

        # Nao bloqueia a tela por muito tempo: integracao externa best-effort apos commit local.
        if chave_acesso:
            enviar_decisao_consyste(
                chave_acesso,
                "desfazer",
                f"Reversao por {usuario}: {motivo}",
                timeout=3,
            )

        return jsonify({"sucesso": True})

    return jsonify({"sucesso": False, "msg": "Nota não encontrada"}), 404


@api_bp.route("/api/concluidas")
@roles_required("Fiscal", "Admin")
def listar_concluidas():
    notas_db = (
        db.session.query(ItemNota.numero_nota, ItemNota.fornecedor)
        .filter_by(status="Concluído")
        .distinct()
        .all()
    )
    lista = []

    for nota in notas_db:
        numero_nota = nota[0]
        resumo_divergencia = _summarize_divergencia_nota(numero_nota)
        motivo = resumo_divergencia["detalhe_divergencia"] if resumo_divergencia["divergencia"] == "Sim" else ""
        lista.append(
            {
                "numero": numero_nota,
                "fornecedor": nota[1],
                "motivo_pendencia": motivo,
                "divergencia_status": resumo_divergencia["divergencia_status"],
            }
        )

    return jsonify(lista)


@api_bp.route("/api/confirmar_lancamento", methods=["POST"])
@roles_required("Fiscal", "Admin")
def confirmar_lancamento():
    payload = confirmar_schema.load(request.json or {})
    numero_nota = str(payload.get("nota"))
    codigo = payload.get("codigo")
    manifestar_destinatario = bool(payload.get("manifestar_destinatario", True))

    ItemNota.query.filter_by(numero_nota=numero_nota, status="Concluído").update(
        {
            "status": "Lançado",
            "usuario_lancamento": session["username"],
            "data_lancamento": datetime.now(),
            "numero_lancamento": codigo,
        }
    )
    db.session.commit()

    manifestacao_result = None
    if manifestar_destinatario:
        manifestacao_result = _manifestar_confirmacao_operacao(numero_nota, session["username"])
        if not manifestacao_result.get("sucesso"):
            ItemNota.query.filter_by(numero_nota=numero_nota, status="Lançado").update(
                {
                    "status": "Concluído",
                    "usuario_lancamento": None,
                    "data_lancamento": None,
                    "numero_lancamento": None,
                }
            )
            db.session.commit()
            return (
                jsonify(
                    {
                        "sucesso": False,
                        "msg": manifestacao_result.get("msg") or "Falha ao manifestar destinatário.",
                        "manifestacao": manifestacao_result,
                    }
                ),
                manifestacao_result.get("status_code") or 502,
            )

    # Integração com WMS: armazenar itens automaticamente após lançamento bem-sucedido
    wms_result = _armazenar_nota_no_wms(numero_nota, session["username"])
    
    return jsonify(
        {
            "sucesso": True,
            "manifestacao": manifestacao_result,
            "wms": wms_result if wms_result.get('sucesso') else None,
            "aviso_wms": wms_result.get('mensagem') if not wms_result.get('sucesso') else None
        }
    )


@api_bp.route("/api/fiscal/manifestar_destinatario", methods=["POST"])
@roles_required("Fiscal", "Admin")
def manifestar_destinatario_fiscal():
    payload = manifestar_destinatario_schema.load(request.json or {})
    numero_nota = str(payload.get("nota") or "").strip()
    if not numero_nota:
        return jsonify({"sucesso": False, "msg": "NF obrigatória."}), 400

    possui_lancamento = ItemNota.query.filter_by(numero_nota=numero_nota, status="Lançado").first()
    if not possui_lancamento:
        return jsonify({"sucesso": False, "msg": "A confirmação da operação só pode ser enviada para NF lançada."}), 409

    resultado = _manifestar_confirmacao_operacao(numero_nota, session["username"])
    status_http = 200 if resultado.get("sucesso") else (resultado.get("status_code") or 500)
    return jsonify({"sucesso": bool(resultado.get("sucesso")), "msg": resultado.get("msg")}), status_http


@api_bp.route("/api/fiscal/estornar_lancamento", methods=["POST"])
@roles_required("Fiscal", "Admin")
def estornar_lancamento_fiscal():
    payload = estorno_lancamento_schema.load(request.json or {})
    numero_nota = str(payload.get("nota"))
    motivo = payload.get("motivo")
    usuario = session["username"]

    possui_lancamento = ItemNota.query.filter_by(numero_nota=numero_nota, status="Lançado").first()
    if not possui_lancamento:
        return jsonify({"sucesso": False, "msg": "Nota não está lançada para estorno."}), 404

    ItemNota.query.filter_by(numero_nota=numero_nota, status="Lançado").update(
        {
            "status": "Concluído",
            "numero_lancamento": None,
            "usuario_lancamento": None,
            "data_lancamento": None,
        }
    )
    db.session.add(
        LogEstornoLancamento(
            numero_nota=numero_nota,
            usuario_estorno=usuario,
            motivo=motivo,
        )
    )
    db.session.commit()
    return jsonify({"sucesso": True})


@api_bp.route("/api/notas_lancadas")
@roles_required("Fiscal", "Admin")
def listar_lancadas():
    notas = (
        db.session.query(
            ItemNota.numero_nota,
            ItemNota.fornecedor,
            ItemNota.numero_lancamento,
            ItemNota.usuario_lancamento,
        )
        .filter_by(status="Lançado")
        .distinct()
        .all()
    )
    return jsonify(
        [
            {
                "numero": nota[0],
                "fornecedor": nota[1],
                "codigo_erp": nota[2],
                "usuario": nota[3],
                "manifestacao": (
                    lambda log: {
                        "status": log.status,
                        "detalhe": log.detalhe,
                        "data": log.data.strftime("%d/%m/%Y %H:%M") if log.data else "---",
                    }
                    if log
                    else None
                )(
                    LogManifestacaoDestinatario.query.filter_by(numero_nota=nota[0])
                    .order_by(LogManifestacaoDestinatario.data.desc())
                    .first()
                ),
            }
            for nota in notas
        ]
    )


@api_bp.route("/api/detalhes_nf/<numero>")
@roles_required("Fiscal", "Admin")
def detalhes_nf(numero):
    itens = ItemNota.query.filter_by(numero_nota=numero).all()
    if not itens:
        return jsonify({"erro": "Nota não encontrada"}), 404

    lista_itens = [{"codigo": i.codigo, "desc": i.descricao, "qtd": i.qtd_real} for i in itens]
    return jsonify(
        {
            "numero": numero,
            "fornecedor": itens[0].fornecedor,
            "valor_total": itens[0].valor_total or "N/A",
            "impostos": itens[0].valor_imposto or "N/A",
            "status_atual": itens[0].status or "---",
            "documentos_disponiveis": any(str(i.chave_acesso or "").strip() for i in itens),
            "itens": lista_itens,
        }
    )


@api_bp.route("/api/fiscal/notas_liberadas")
@login_required
def listar_notas_liberadas_lancamento():
    return jsonify(_build_notas_liberadas_records(request.args.get("nota")))


@api_bp.route("/api/fiscal/notas_liberadas/<numero>")
@login_required
def detalhes_nota_liberada_lancamento(numero):
    itens = ItemNota.query.filter_by(numero_nota=str(numero)).all()
    if not itens:
        return jsonify({"erro": "Nota não encontrada"}), 404

    logs_estorno = (
        LogEstornoLancamento.query.filter_by(numero_nota=str(numero))
        .order_by(LogEstornoLancamento.data_estorno.desc())
        .all()
    )
    statuses = {str(item.status or "").strip() for item in itens}
    status_atual = (
        "Lançado"
        if "Lançado" in statuses
        else "Concluído"
        if "Concluído" in statuses
        else "Devolvido"
        if "Devolvido" in statuses
        else "Pendente"
    )
    fim_conferencia = max((item.fim_conferencia for item in itens if item.fim_conferencia), default=None)
    usuario_conferencia = next((item.usuario_conferencia for item in itens if item.usuario_conferencia), None)

    historico = []
    if fim_conferencia:
        historico.append(
            {
                "usuario": usuario_conferencia or "---",
                "motivo": "NF liberada para lançamento fiscal após conferência concluída.",
                "data": fim_conferencia.strftime("%d/%m/%Y %H:%M"),
            }
        )

    data_lancamento = max((item.data_lancamento for item in itens if item.data_lancamento), default=None)
    usuario_lancamento = next((item.usuario_lancamento for item in itens if item.usuario_lancamento), None)
    numero_lancamento = next((item.numero_lancamento for item in itens if item.numero_lancamento), None)
    if data_lancamento:
        historico.append(
            {
                "usuario": usuario_lancamento or "---",
                "motivo": f"Lançamento fiscal registrado no ERP {numero_lancamento or '---'}.",
                "data": data_lancamento.strftime("%d/%m/%Y %H:%M"),
            }
        )

    chave_acesso = next((str(item.chave_acesso or "").strip() for item in itens if item.chave_acesso), "")
    log_manifest = (
        LogManifestacaoDestinatario.query.filter_by(numero_nota=str(numero), status="Sucesso")
        .order_by(LogManifestacaoDestinatario.data.desc())
        .first()
    )
    manifestacao_sefaz = None
    if log_manifest:
        manifestacao_sefaz = {
            "operacao": "Confirmação do Destinatário",
            "status": log_manifest.status,
            "detalhe": log_manifest.detalhe or "---",
            "usuario": log_manifest.usuario,
            "data": log_manifest.data.strftime("%d/%m/%Y %H:%M") if log_manifest.data else "---",
        }
    return jsonify(
        {
            "numero": str(numero),
            "fornecedor": itens[0].fornecedor or "---",
            "status_atual": status_atual,
            "conferido_por": usuario_conferencia or "---",
            "data_conferencia": fim_conferencia.strftime("%d/%m/%Y %H:%M") if fim_conferencia else "---",
            "lancado_por": usuario_lancamento or "---",
            "data_lancamento": data_lancamento.strftime("%d/%m/%Y %H:%M") if data_lancamento else "---",
            "chave_acesso": chave_acesso,
            "manifestacao_sefaz": manifestacao_sefaz,
            "valor_total": itens[0].valor_total or "N/A",
            "impostos": itens[0].valor_imposto or "N/A",
            "documentos_disponiveis": any(str(i.chave_acesso or "").strip() for i in itens),
            "itens": [
                {
                    "codigo": item.codigo,
                    "descricao": item.descricao,
                    "qtd": item.qtd_real,
                    "unidade": item.unidade_comercial or "UN",
                }
                for item in itens
            ],
            "historico_liberacao": historico
            + [
                {
                    "usuario": log.usuario_estorno,
                    "motivo": f"Estorno do lançamento fiscal: {log.motivo}",
                    "data": log.data_estorno.strftime("%d/%m/%Y %H:%M") if log.data_estorno else "---",
                }
                for log in logs_estorno
            ],
        }
    )


@api_bp.route("/api/historico_completo")
@roles_required("Admin")
def api_historico():
    data_ini = _parse_date(request.args.get("data_ini"))
    data_fim = _parse_date(request.args.get("data_fim"))
    return jsonify(
        _build_historico_records(
            search_nota=request.args.get("nota"),
            status_filter=request.args.get("status"),
            fornecedor=request.args.get("fornecedor"),
            conferente=request.args.get("conferente"),
            revertido_por=request.args.get("revertido_por"),
            data_ini=data_ini,
            data_fim=data_fim,
        )
    )


@api_bp.route("/api/estornos_historico")
@roles_required("Admin")
def api_estornos_historico():
    return jsonify(_build_estornos_records(request.args.get("nota")))


@api_bp.route("/api/timeline/<nota>")
@roles_required("Admin")
def timeline_nota(nota):
    itens = ItemNota.query.filter_by(numero_nota=nota).all()
    eventos = []
    if itens:
        primeiro_import = next((i for i in itens if i.data_importacao), None)
        if primeiro_import:
            eventos.append(
                {
                    "data": primeiro_import.data_importacao,
                    "tipo": "Importação",
                    "descricao": f"Importada por {primeiro_import.usuario_importacao or '---'}",
                }
            )

        ini_conf = next((i for i in itens if i.inicio_conferencia), None)
        if ini_conf:
            eventos.append(
                {
                    "data": ini_conf.inicio_conferencia,
                    "tipo": "Conferência",
                    "descricao": "Início da conferência",
                }
            )

        fim_conf = next((i for i in itens if i.fim_conferencia), None)
        if fim_conf:
            eventos.append(
                {
                    "data": fim_conf.fim_conferencia,
                    "tipo": "Conferência",
                    "descricao": f"Finalizada por {fim_conf.usuario_conferencia or '---'}",
                }
            )

        for log in LogDivergencia.query.filter_by(numero_nota=nota).all():
            eventos.append(
                {
                    "data": log.data_erro,
                    "tipo": "Divergência",
                    "descricao": f"{log.item_descricao} - usuário {log.usuario_erro}",
                }
            )

        for log in LogReversaoConferencia.query.filter_by(numero_nota=nota).all():
            eventos.append(
                {
                    "data": log.data_reversao,
                    "tipo": "Estorno Conferência",
                    "descricao": f"{log.usuario_reversao}: {log.motivo}",
                }
            )

        for log in LogEstornoLancamento.query.filter_by(numero_nota=nota).all():
            eventos.append(
                {
                    "data": log.data_estorno,
                    "tipo": "Estorno Lançamento",
                    "descricao": f"{log.usuario_estorno}: {log.motivo}",
                }
            )

        for log in LogManifestacaoDestinatario.query.filter_by(numero_nota=nota).all():
            eventos.append(
                {
                    "data": log.data,
                    "tipo": f"Manifestação {log.manifestacao}",
                    "descricao": f"{log.usuario}: {log.status} - {log.detalhe or 'Sem detalhe'}",
                }
            )

        lanc = next((i for i in itens if i.data_lancamento), None)
        if lanc:
            eventos.append(
                {
                    "data": lanc.data_lancamento,
                    "tipo": "Lançamento",
                    "descricao": f"Lançada por {lanc.usuario_lancamento or '---'} ({lanc.numero_lancamento or '---'})",
                }
            )

    for log in LogExclusaoNota.query.filter_by(numero_nota=nota).all():
        eventos.append(
            {
                "data": log.data_exclusao,
                "tipo": "Exclusão",
                "descricao": f"{log.usuario_exclusao}: {log.motivo}",
            }
        )

    eventos = [e for e in eventos if e.get("data")]
    eventos.sort(key=lambda e: e["data"])
    return jsonify(
        [
            {
                "data": e["data"].strftime("%d/%m/%Y %H:%M"),
                "tipo": e["tipo"],
                "descricao": e["descricao"],
            }
            for e in eventos
        ]
    )


@api_bp.route("/api/sla_alertas")
@roles_required("Fiscal", "Admin")
def sla_alertas():
    data = _build_sla_dashboard_data()
    return jsonify({"pendentes": data["pendentes"], "fiscal": data["fiscal"]})


def _build_sla_dashboard_data():
    now = datetime.now()
    pendente_horas = _parse_positive_int(request.args.get("pendente_horas"), default=24, min_value=1, max_value=240)
    fiscal_horas = _parse_positive_int(request.args.get("fiscal_horas"), default=12, min_value=1, max_value=240)
    pendente_alerta = max(int(pendente_horas * 0.8), 1)
    fiscal_alerta = max(int(fiscal_horas * 0.8), 1)

    alertas_pendentes = []
    for n in db.session.query(ItemNota.numero_nota, ItemNota.fornecedor).filter_by(status="Pendente").distinct().all():
        item = ItemNota.query.filter_by(numero_nota=n[0]).first()
        if not item or not item.data_importacao:
            continue
        horas = (now - item.data_importacao).total_seconds() / 3600
        if horas >= pendente_horas:
            alertas_pendentes.append(
                {"nota": n[0], "fornecedor": n[1], "horas": int(horas), "risco": "critico"}
            )

    alertas_fiscal = []
    for n in db.session.query(ItemNota.numero_nota, ItemNota.fornecedor).filter_by(status="Concluído").distinct().all():
        item = ItemNota.query.filter_by(numero_nota=n[0]).first()
        if not item or not item.fim_conferencia:
            continue
        horas = (now - item.fim_conferencia).total_seconds() / 3600
        if horas >= fiscal_horas:
            alertas_fiscal.append(
                {"nota": n[0], "fornecedor": n[1], "horas": int(horas), "risco": "critico"}
            )

    alertas_pendentes.sort(key=lambda x: x["horas"], reverse=True)
    alertas_fiscal.sort(key=lambda x: x["horas"], reverse=True)

    em_risco = []
    for n in db.session.query(ItemNota.numero_nota, ItemNota.fornecedor).filter_by(status="Pendente").distinct().all():
        item = ItemNota.query.filter_by(numero_nota=n[0]).first()
        if not item or not item.data_importacao:
            continue
        horas = int((now - item.data_importacao).total_seconds() / 3600)
        if pendente_alerta <= horas < pendente_horas:
            em_risco.append(
                {
                    "nota": n[0],
                    "fornecedor": n[1],
                    "horas": horas,
                    "fase": "Conferência",
                    "limite": pendente_horas,
                }
            )

    for n in db.session.query(ItemNota.numero_nota, ItemNota.fornecedor).filter_by(status="Concluído").distinct().all():
        item = ItemNota.query.filter_by(numero_nota=n[0]).first()
        if not item or not item.fim_conferencia:
            continue
        horas = int((now - item.fim_conferencia).total_seconds() / 3600)
        if fiscal_alerta <= horas < fiscal_horas:
            em_risco.append(
                {
                    "nota": n[0],
                    "fornecedor": n[1],
                    "horas": horas,
                    "fase": "Fiscal",
                    "limite": fiscal_horas,
                }
            )
    em_risco.sort(key=lambda x: x["horas"], reverse=True)

    fornecedor_por_nota = {
        str(row[0]): row[1] or "---"
        for row in db.session.query(ItemNota.numero_nota, ItemNota.fornecedor).distinct().all()
    }
    limite_30d = now - timedelta(days=30)
    logs_30d = LogDivergencia.query.filter(LogDivergencia.data_erro >= limite_30d).all()
    agg_fornecedor = {}
    for log in logs_30d:
        fornecedor = fornecedor_por_nota.get(str(log.numero_nota), "---")
        if fornecedor not in agg_fornecedor:
            agg_fornecedor[fornecedor] = {"fornecedor": fornecedor, "divergencias": 0, "notas": set()}
        agg_fornecedor[fornecedor]["divergencias"] += 1
        agg_fornecedor[fornecedor]["notas"].add(str(log.numero_nota))

    fornecedores_criticos = sorted(
        [
            {
                "fornecedor": v["fornecedor"],
                "divergencias": v["divergencias"],
                "notas_impactadas": len(v["notas"]),
            }
            for v in agg_fornecedor.values()
        ],
        key=lambda x: (x["divergencias"], x["notas_impactadas"]),
        reverse=True,
    )[:8]

    return {
        "pendentes": alertas_pendentes[:15],
        "fiscal": alertas_fiscal[:15],
        "em_risco": em_risco[:15],
        "fornecedores_criticos": fornecedores_criticos,
        "resumo": {
            "pendente_horas": pendente_horas,
            "fiscal_horas": fiscal_horas,
            "pendentes_criticos": len(alertas_pendentes),
            "fiscal_criticos": len(alertas_fiscal),
            "notas_em_risco": len(em_risco),
        },
    }


@api_bp.route("/api/expedicao/dashboard")
@roles_required("Admin", "Fiscal")
def expedicao_dashboard():
    abertas = ExpedicaoConferencia.query.filter(ExpedicaoConferencia.status != "Fechada").count()
    fechadas = ExpedicaoConferencia.query.filter_by(status="Fechada").count()
    pendente_decisao = ExpedicaoConferencia.query.filter_by(status="PendenteDecisao").count()

    itens_divergentes = ExpedicaoConferenciaItem.query.filter_by(divergente=True).count()
    total_fat = ExpedicaoFaturamento.query.filter_by(ativo=True).count()
    total_parcial = ExpedicaoFaturamento.query.filter_by(ativo=True, tipo="Parcial").count()
    total_total = ExpedicaoFaturamento.query.filter_by(ativo=True, tipo="Total").count()

    top_clientes = (
        db.session.query(
            ExpedicaoConferenciaItem.cliente,
            func.sum(ExpedicaoConferenciaItem.qtd_faturada).label("qtd"),
        )
        .group_by(ExpedicaoConferenciaItem.cliente)
        .order_by(func.sum(ExpedicaoConferenciaItem.qtd_faturada).desc())
        .limit(5)
        .all()
    )

    return jsonify(
        {
            "abertas": abertas,
            "fechadas": fechadas,
            "pendente_decisao": pendente_decisao,
            "itens_divergentes": itens_divergentes,
            "faturamentos": {
                "total": total_fat,
                "parcial": total_parcial,
                "total_tipo": total_total,
            },
            "top_clientes": [
                {"cliente": (row[0] or "---"), "qtd_faturada": int(row[1] or 0)}
                for row in top_clientes
            ],
        }
    )


@api_bp.route("/api/sla_dashboard")
@roles_required("Fiscal", "Admin")
def sla_dashboard():
    return jsonify(_build_sla_dashboard_data())


@api_bp.route("/api/admin/backup_db")
@roles_required("Admin")
def backup_db():
    db_path = _get_db_file_path()
    if not db_path or not os.path.exists(db_path):
        return jsonify({"sucesso": False, "msg": "Banco não encontrado."}), 404

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return send_file(db_path, as_attachment=True, download_name=f"backup_conferencia_{ts}.db")


@api_bp.route("/api/admin/restore_db", methods=["POST"])
@roles_required("Admin")
def restore_db():
    file = request.files.get("backup_file")
    if not file:
        return jsonify({"sucesso": False, "msg": "Arquivo de backup não enviado."}), 400

    db_path = _get_db_file_path()
    if not db_path:
        return jsonify({"sucesso": False, "msg": "Banco não suportado para restore automático."}), 400

    temp_fd, temp_name = tempfile.mkstemp(suffix=".db")
    os.close(temp_fd)
    file.save(temp_name)

    try:
        db.session.remove()
        db.engine.dispose()
        shutil.copyfile(temp_name, db_path)
        return jsonify({"sucesso": True})
    except Exception as exc:
        return jsonify({"sucesso": False, "msg": str(exc)}), 500
    finally:
        try:
            os.remove(temp_name)
        except Exception:
            pass


@api_bp.route("/api/historico/export.csv")
@roles_required("Admin")
def exportar_historico_csv():
    records = _build_historico_records(
        search_nota=request.args.get("nota"),
        status_filter=request.args.get("status"),
        fornecedor=request.args.get("fornecedor"),
        conferente=request.args.get("conferente"),
        revertido_por=request.args.get("revertido_por"),
        data_ini=_parse_date(request.args.get("data_ini")),
        data_fim=_parse_date(request.args.get("data_fim")),
    )

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "nota",
            "fornecedor",
            "status",
            "conferido_por",
            "tempo_conf",
            "divergencia",
            "tentativas",
            "codigo_lancamento",
            "revertido_por",
            "motivo_reversao",
            "data_reversao",
            "detalhe_divergencia",
        ]
    )
    for r in records:
        writer.writerow(
            [
                r["nota"],
                r["fornecedor"],
                r["status"],
                r["conferido_por"],
                r["tempo_conf"],
                r["divergencia"],
                r["tentativas"],
                r["codigo_lancamento"],
                r["revertido_por"],
                r["motivo_reversao"],
                r["data_reversao"],
                r["detalhe_divergencia"],
            ]
        )

    content = output.getvalue()
    output.close()
    return Response(
        content,
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=historico_conferencia.csv"},
    )


@api_bp.route("/api/stats")
@login_required
def get_stats():
    estoque = db.session.query(ItemNota.numero_nota).filter_by(status="Pendente").distinct().count()
    fiscal = db.session.query(ItemNota.numero_nota).filter_by(status="Concluído").distinct().count()
    finalizado = db.session.query(ItemNota.numero_nota).filter_by(status="Lançado").distinct().count()
    excluidas = db.session.query(LogExclusaoNota.numero_nota).distinct().count()
    return jsonify(
        {
            "estoque": estoque,
            "fiscal": fiscal,
            "finalizado": finalizado,
            "excluidas": excluidas,
        }
    )


@api_bp.route("/api/health")
def healthcheck():
    try:
        db.session.execute(db.text("SELECT 1"))
        db_ok = True
    except Exception:
        db_ok = False

    return (
        jsonify(
            {
                "status": "ok" if db_ok else "degradado",
                "db": "ok" if db_ok else "erro",
                "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
        ),
        200 if db_ok else 503,
    )


@api_bp.route("/api/admin/acessos")
@roles_required("Admin")
def listar_acessos_admin():
    page = _parse_positive_int(request.args.get("page"), default=1, min_value=1, max_value=100000)
    per_page = _parse_positive_int(request.args.get("per_page"), default=50, min_value=10, max_value=200)

    query = _admin_access_query_from_request()
    total = query.count()
    pages = max((total + per_page - 1) // per_page, 1)
    if page > pages:
        page = pages

    logs = (
        query.order_by(LogAcessoAdministrativo.data.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )

    return jsonify(
        {
            "items": [_serialize_admin_access_log(log) for log in logs],
            "page": page,
            "per_page": per_page,
            "total": total,
            "pages": pages,
        }
    )


@api_bp.route("/api/admin/acessos/export.csv")
@roles_required("Admin")
def exportar_acessos_admin_csv():
    query = _admin_access_query_from_request()
    logs = query.order_by(LogAcessoAdministrativo.data.desc()).limit(20000).all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["data", "usuario", "metodo", "rota"])
    for log in logs:
        writer.writerow(
            [
                log.data.strftime("%d/%m/%Y %H:%M:%S") if log.data else "---",
                log.usuario,
                log.metodo,
                log.rota,
            ]
        )

    content = output.getvalue()
    output.close()
    return Response(
        content,
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=auditoria_acessos_admin.csv"},
    )


@api_bp.route("/api/admin/acessos/resumo")
@roles_required("Admin")
def resumo_acessos_admin():
    now = datetime.now()
    last_24h = now - timedelta(hours=24)
    last_7d = now - timedelta(days=7)

    total_24h = LogAcessoAdministrativo.query.filter(LogAcessoAdministrativo.data >= last_24h).count()
    total_7d = LogAcessoAdministrativo.query.filter(LogAcessoAdministrativo.data >= last_7d).count()

    top_usuarios_raw = (
        db.session.query(LogAcessoAdministrativo.usuario, func.count(LogAcessoAdministrativo.id).label("qtd"))
        .filter(LogAcessoAdministrativo.data >= last_7d)
        .group_by(LogAcessoAdministrativo.usuario)
        .order_by(func.count(LogAcessoAdministrativo.id).desc())
        .limit(5)
        .all()
    )

    top_rotas_raw = (
        db.session.query(LogAcessoAdministrativo.rota, func.count(LogAcessoAdministrativo.id).label("qtd"))
        .filter(LogAcessoAdministrativo.data >= last_7d)
        .group_by(LogAcessoAdministrativo.rota)
        .order_by(func.count(LogAcessoAdministrativo.id).desc())
        .limit(5)
        .all()
    )

    return jsonify(
        {
            "total_24h": total_24h,
            "total_7d": total_7d,
            "top_usuarios": [{"usuario": u, "qtd": qtd} for u, qtd in top_usuarios_raw],
            "top_rotas": [{"rota": r, "qtd": qtd} for r, qtd in top_rotas_raw],
        }
    )
