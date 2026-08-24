"""Rotas de Gestao de Viagens - rastreamento consolidado por viagem."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import math
import re
from datetime import date, datetime, timedelta

from flask import Blueprint, current_app, jsonify, render_template, request, send_file, session, url_for
from sqlalchemy import func, or_, true
from werkzeug.utils import secure_filename

from ..auth import permission_required, permission_required_any
from ..extensions import db
from ..models import (
    AgendamentoMotorista,
    AgendamentoCliente,
    AgendamentoFornecedor,
    AgendamentoSolicitacao,
    AgendamentoVeiculo,
    FrotaAbastecimento,
    FrotaChecklistDiario,
    Viagem,
    ViagemEvento,
    ViagemParada,
    ViagemPosicao,
)
from ..services.agendamento_service import _geocode_endereco, montar_endereco_rota
from ..services.agendamento_service import sincronizar_motoristas_usuarios

viagem_bp = Blueprint("viagem", __name__, url_prefix="/api/viagem")

PERM = "PAGE_LOGISTICA_VIAGEM"
UPLOAD_SUB = "viagens"
ALLOWED_EXTS = {"pdf", "jpg", "jpeg", "png", "webp"}


def _user():
    return session.get("username", "sistema")


def _parse_dt(s):
    if not s:
        return None
    s = str(s).strip()
    for fmt in ("%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%d/%m/%Y %H:%M", "%d/%m/%Y"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def _parse_int(v, default=None):
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return default


def _parse_float(v, default=None):
    try:
        return float(str(v).replace(",", "."))
    except (TypeError, ValueError):
        return default


def _upload_dir():
    d = os.path.join(current_app.instance_path, UPLOAD_SUB)
    os.makedirs(d, exist_ok=True)
    return d


def _save_upload(key: str):
    f = request.files.get(key)
    if not f or not f.filename:
        return None
    nome = secure_filename(f.filename)
    ext = nome.rsplit(".", 1)[-1].lower() if "." in nome else ""
    if ext not in ALLOWED_EXTS:
        return None
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    final = f"{stamp}_{nome}"
    path = os.path.join(_upload_dir(), final)
    f.save(path)
    return os.path.join(UPLOAD_SUB, final).replace("\\", "/")


def _integrar_comprovante_entrega(parada) -> None:
    """Ao concluir uma parada de ENTREGA com foto do canhoto, anexa essa foto
    automaticamente como comprovante de entrega no romaneio CIF vinculado
    (Registro de Expedicao). Best-effort: nunca interrompe o fluxo da viagem."""
    try:
        from ..services.comprovante_entrega_motorista_service import (
            anexar_comprovante_da_parada,
        )

        anexar_comprovante_da_parada(parada)
    except Exception:
        current_app.logger.exception(
            "Falha ao integrar comprovante de entrega da parada %s ao romaneio.",
            getattr(parada, "id", None),
        )



def _proximo_codigo() -> str:
    ano = datetime.now().year
    prefix = f"VG-{ano}-"
    ultima = (
        Viagem.query.filter(Viagem.codigo.like(f"{prefix}%"))
        .order_by(Viagem.id.desc())
        .first()
    )
    if ultima and ultima.codigo:
        try:
            n = int(ultima.codigo.rsplit("-", 1)[-1]) + 1
        except ValueError:
            n = (Viagem.query.count() or 0) + 1
    else:
        n = 1
    return f"{prefix}{n:04d}"


def _token_motorista(vid: int) -> str:
    """Token HMAC curto usado no link do motorista (sem precisar de coluna no banco)."""
    secret = (current_app.config.get("SECRET_KEY") or "dev").encode("utf-8")
    msg = f"viagem:{vid}".encode("utf-8")
    return hmac.new(secret, msg, hashlib.sha256).hexdigest()[:16]


def _viagem_por_token(vid: int, token: str):
    if not token or not hmac.compare_digest(token, _token_motorista(vid)):
        return None
    return db.session.get(Viagem, vid)


def _token_painel_motorista(mid: int) -> str:
    """Token HMAC permanente do painel do motorista (link PWA)."""
    secret = (current_app.config.get("SECRET_KEY") or "dev").encode("utf-8")
    msg = f"painel_motorista:{mid}".encode("utf-8")
    return hmac.new(secret, msg, hashlib.sha256).hexdigest()[:20]


def _motorista_por_token(mid: int, token: str):
    if not token or not hmac.compare_digest(token, _token_painel_motorista(mid)):
        return None
    return db.session.get(AgendamentoMotorista, mid)


def _haversine_km(lat1, lon1, lat2, lon2):
    if None in (lat1, lon1, lat2, lon2):
        return 0.0
    r = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _intervalo_viagem(v: Viagem | None, *, saida=None, retorno=None) -> tuple[datetime | None, datetime | None]:
    inicio = saida if saida is not None else (v.saida_prevista if v else None)
    if not inicio:
        return None, None
    fim = retorno if retorno is not None else (v.retorno_previsto if v else None)
    if fim and fim > inicio:
        return inicio, fim
    return inicio, inicio + timedelta(minutes=int(current_app.config.get("VIAGEM_DURACAO_PADRAO_MINUTOS", 180)))


def _validar_conflito_recurso(
    *,
    veiculo_id: int | None,
    motorista_id: int | None,
    saida: datetime | None,
    retorno: datetime | None,
    viagem_id: int | None = None,
) -> str | None:
    inicio, fim = _intervalo_viagem(None, saida=saida, retorno=retorno)
    if not inicio or not fim:
        return None

    buffer_min = int(current_app.config.get("VIAGEM_CONFLITO_MINUTOS", 30))
    query = Viagem.query.filter(Viagem.status.in_(["Planejada", "EmAndamento"]))
    if viagem_id:
        query = query.filter(Viagem.id != viagem_id)
    if veiculo_id and motorista_id:
        query = query.filter((Viagem.veiculo_id == veiculo_id) | (Viagem.motorista_id == motorista_id))
    elif veiculo_id:
        query = query.filter(Viagem.veiculo_id == veiculo_id)
    elif motorista_id:
        query = query.filter(Viagem.motorista_id == motorista_id)
    else:
        return None

    for outra in query.all():
        outro_inicio, outro_fim = _intervalo_viagem(outra)
        if not outro_inicio or not outro_fim:
            continue
        sobrepoe = (inicio - timedelta(minutes=buffer_min)) < (outro_fim + timedelta(minutes=buffer_min)) and (
            fim + timedelta(minutes=buffer_min)
        ) > (outro_inicio - timedelta(minutes=buffer_min))
        if not sobrepoe:
            continue
        if veiculo_id and outra.veiculo_id == veiculo_id:
            return f"Veiculo ja reservado na viagem {outra.codigo or outra.id} para {outro_inicio.strftime('%d/%m/%Y %H:%M')}."
        if motorista_id and outra.motorista_id == motorista_id:
            nome = outra.motorista_nome or "Motorista"
            return f"{nome} ja esta em outra viagem ({outra.codigo or outra.id}) em {outro_inicio.strftime('%d/%m/%Y %H:%M')}."
    return None


def _veiculo_label(v):
    if not v:
        return "—"
    return f"{v.nome_exibicao}" + (f" ({v.placa})" if v.placa else "")


def _normalizar_placa(valor: str | None) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(valor or "").upper())


def _solicitacao_avulsa_aprovada(sol: AgendamentoSolicitacao | None) -> bool:
    if not sol:
        return False
    if str(sol.tipo or "").strip().upper() != "AVULSA":
        return True
    return str(sol.status or "").strip() == "Aprovada"


def _validar_aprovacao_avulsa(sol: AgendamentoSolicitacao | None) -> str | None:
    if _solicitacao_avulsa_aprovada(sol):
        return None
    return "Solicitação avulsa precisa de aprovação de Admin antes de ser alocada em viagem."


def _resolver_motorista_por_nome(nome: str | None) -> AgendamentoMotorista | None:
    nome_limpo = str(nome or "").strip()
    if not nome_limpo:
        return None
    exato = AgendamentoMotorista.query.filter(func.lower(AgendamentoMotorista.nome) == nome_limpo.lower()).first()
    if exato:
        return exato
    return (
        AgendamentoMotorista.query
        .filter(AgendamentoMotorista.nome.ilike(f"%{nome_limpo}%"))
        .order_by(AgendamentoMotorista.id.desc())
        .first()
    )


def garantir_viagem_automatica_romaneio_st(romaneio, *, usuario: str = "sistema") -> tuple[bool, int | None, str]:
    """Cria uma viagem avulsa automática para romaneio ST quando possível.

    Regras:
    - Só cria quando há ao menos uma NF ST (com ordem_compra) no romaneio.
    - Evita duplicidade usando marcador AUTO_ROMANEIO_ST:<id> em observacao.
    - Exige veículo interno casado pela placa do romaneio.
    """
    if not romaneio:
        return False, None, "Romaneio inválido."

    nfs = list(getattr(romaneio, "nfs", []) or [])
    if not any(str(getattr(nf, "ordem_compra", "") or "").strip() for nf in nfs):
        return False, None, "Romaneio não possui NF de serviço de terceiro."

    marcador = f"AUTO_ROMANEIO_ST:{int(romaneio.id)}"
    existente = (
        Viagem.query
        .filter(Viagem.observacao.ilike(f"%{marcador}%"))
        .filter(Viagem.status != "Cancelada")
        .order_by(Viagem.id.desc())
        .first()
    )
    if existente:
        return True, int(existente.id), "Viagem automática já existia."

    placa_romaneio = _normalizar_placa(getattr(romaneio, "placa", None))
    veiculo = None
    if placa_romaneio:
        veiculos = AgendamentoVeiculo.query.filter_by(ativo=True).all()
        for item in veiculos:
            if _normalizar_placa(item.placa) == placa_romaneio:
                veiculo = item
                break
    if not veiculo:
        return False, None, "Romaneio ST sem veículo interno compatível pela placa."

    motorista = _resolver_motorista_por_nome(getattr(romaneio, "motorista", None))
    saida = datetime.combine(getattr(romaneio, "data_romaneio", date.today()), datetime.min.time())
    saida = saida.replace(hour=8, minute=0, second=0, microsecond=0)
    retorno = saida + timedelta(minutes=int(current_app.config.get("VIAGEM_DURACAO_PADRAO_MINUTOS", 180)))

    responsavel = (
        str(getattr(romaneio, "motorista", "") or "").strip()
        or str(getattr(romaneio, "transportadora", "") or "").strip()
        or str(usuario or "sistema")
    )
    viagem = Viagem(
        codigo=_proximo_codigo(),
        veiculo_id=veiculo.id,
        motorista_id=(motorista.id if motorista else None),
        motorista_nome=(motorista.nome if motorista else None),
        tipo="ENTREGA",
        status="Planejada",
        titulo=f"Romaneio ST {romaneio.numero_romaneio}",
        observacao=f"{marcador} | Gerada automaticamente a partir do romaneio de serviço de terceiro.",
        saida_prevista=saida,
        retorno_previsto=retorno,
        origem_label="Expedição",
        criado_por=str(usuario or "sistema"),
        criado_em=datetime.now(),
        atualizado_em=datetime.now(),
        avulsa=True,
        funcionario_responsavel=responsavel[:160],
    )
    db.session.add(viagem)
    db.session.flush()
    _log_evento(
        viagem.id,
        "OBSERVACAO",
        "Viagem criada automaticamente",
        descricao=f"Gatilho automático do romaneio ST {romaneio.numero_romaneio}.",
        severidade="info",
    )
    db.session.commit()
    return True, int(viagem.id), "Viagem automática ST criada."


def _viagem_resumo_mapa(v: Viagem | None) -> dict | None:
    if not v:
        return None
    return {
        "id": v.id,
        "codigo": v.codigo,
        "status": v.status,
        "titulo": v.titulo,
        "motorista_nome": v.motorista_nome,
        "destino_label": v.destino_label,
        "saida_prevista": v.saida_prevista.isoformat() if v.saida_prevista else None,
        "saida_real": v.saida_real.isoformat() if v.saida_real else None,
        "retorno_previsto": v.retorno_previsto.isoformat() if v.retorno_previsto else None,
    }


def _checklist_liberacao(v: Viagem, paradas: list[ViagemParada] | None = None) -> dict:
    paradas = paradas if paradas is not None else ViagemParada.query.filter_by(viagem_id=v.id).order_by(ViagemParada.sequencia).all()
    motorista = AgendamentoMotorista.query.get(v.motorista_id) if v.motorista_id else None
    bloqueios: list[str] = []
    avisos: list[str] = []
    if not v.veiculo_id:
        bloqueios.append("Veiculo nao definido.")
    if v.avulsa:
        if not str(v.funcionario_responsavel or "").strip():
            bloqueios.append("Informe o funcionario responsavel pela viagem avulsa.")
    else:
        if not v.motorista_id:
            bloqueios.append("Motorista nao definido.")
        if not paradas:
            bloqueios.append("Adicione pelo menos uma parada.")
    if not v.saida_prevista:
        bloqueios.append("Informe a saida prevista.")
    for idx, parada in enumerate(paradas, start=1):
        destino = " ".join(filter(None, [parada.endereco, parada.cidade, parada.uf])).strip()
        if not destino:
            bloqueios.append(f"Parada {idx} sem endereco/cidade.")
        if parada.latitude is None or parada.longitude is None:
            avisos.append(f"Parada {idx} sem coordenadas para mapa/otimizacao.")
    if not v.retorno_previsto:
        avisos.append("Retorno previsto nao informado.")
    if motorista and not str(motorista.telefone or "").strip():
        avisos.append("Motorista sem telefone cadastrado para WhatsApp.")
    return {
        "ok": not bloqueios,
        "bloqueios": bloqueios,
        "avisos": avisos,
        "total": len(bloqueios) + len(avisos),
    }


def _alertas_operacionais(v: Viagem, ult: ViagemPosicao | None = None) -> list[dict]:
    alertas: list[dict] = []
    agora = datetime.now()
    if v.status == "Planejada":
        if v.saida_prevista and v.saida_prevista < agora and not v.liberada:
            alertas.append({"tipo": "danger", "msg": "Saida prevista vencida e viagem nao liberada."})
        elif v.saida_prevista and v.saida_prevista < agora:
            alertas.append({"tipo": "warn", "msg": "Saida prevista vencida."})
        if not v.liberada:
            alertas.append({"tipo": "info", "msg": "Ainda nao liberada ao motorista."})
    if v.status == "EmAndamento":
        if not ult:
            alertas.append({"tipo": "warn", "msg": "Sem ponto de GPS recebido."})
        else:
            min_sem_gps = int((agora - ult.registrado_em).total_seconds() // 60)
            if min_sem_gps > 15:
                alertas.append({"tipo": "danger", "msg": f"GPS sem atualizar ha {min_sem_gps} min."})
            elif min_sem_gps > 5:
                alertas.append({"tipo": "warn", "msg": f"GPS sem atualizar ha {min_sem_gps} min."})
    return alertas


def _sla_viagem(v: Viagem, ult: ViagemPosicao | None = None) -> dict:
    agora = datetime.now()
    base = v.saida_prevista
    if v.status == "EmAndamento":
        base = v.retorno_previsto
    if v.status in ("Concluida", "Cancelada") or not base:
        return {"nivel": "ok", "label": "Sem risco", "minutos": None, "prioridade": "Baixa"}

    minutos = int((base - agora).total_seconds() // 60)
    sem_gps_min = None
    if v.status == "EmAndamento" and ult:
        sem_gps_min = int((agora - ult.registrado_em).total_seconds() // 60)

    if minutos < 0 or (sem_gps_min is not None and sem_gps_min > 15):
        return {"nivel": "critico", "label": "CrÃ­tico", "minutos": minutos, "prioridade": "Critica"}
    if minutos <= 60 or (sem_gps_min is not None and sem_gps_min > 5):
        return {"nivel": "alto", "label": "Alta atenÃ§Ã£o", "minutos": minutos, "prioridade": "Alta"}
    if minutos <= 240:
        return {"nivel": "medio", "label": "Monitorar", "minutos": minutos, "prioridade": "Media"}
    return {"nivel": "ok", "label": "No prazo", "minutos": minutos, "prioridade": "Baixa"}


# --------------------------------------------------------------------------- serializers
def _viagem_dict(v: Viagem, detalhada: bool = False) -> dict:
    veiculo = AgendamentoVeiculo.query.get(v.veiculo_id)
    motorista = AgendamentoMotorista.query.get(v.motorista_id) if v.motorista_id else None
    qtd_paradas = ViagemParada.query.filter_by(viagem_id=v.id).count()
    qtd_pontos = ViagemPosicao.query.filter_by(viagem_id=v.id).count()
    qtd_eventos = ViagemEvento.query.filter_by(viagem_id=v.id).count()
    paradas_ok = ViagemParada.query.filter_by(viagem_id=v.id, status="Concluida").count()

    # tempo corrido
    inicio = v.saida_real or v.saida_prevista
    fim = v.retorno_real or (datetime.now() if v.status == "EmAndamento" else v.retorno_previsto)
    tempo_min = int((fim - inicio).total_seconds() // 60) if inicio and fim else 0

    # ultima posicao
    ult = (
        ViagemPosicao.query.filter_by(viagem_id=v.id)
        .order_by(ViagemPosicao.registrado_em.desc())
        .first()
    )
    alertas = _alertas_operacionais(v, ult)
    sla = _sla_viagem(v, ult)

    out = {
        "id": v.id,
        "codigo": v.codigo,
        "tipo": v.tipo,
        "status": v.status,
        "titulo": v.titulo,
        "observacao": v.observacao,
        "veiculo_id": v.veiculo_id,
        "veiculo_label": _veiculo_label(veiculo),
        "motorista_id": v.motorista_id,
        "motorista_nome": v.motorista_nome or (motorista.nome if motorista else None),
        "saida_prevista": v.saida_prevista.isoformat() if v.saida_prevista else None,
        "retorno_previsto": v.retorno_previsto.isoformat() if v.retorno_previsto else None,
        "saida_real": v.saida_real.isoformat() if v.saida_real else None,
        "retorno_real": v.retorno_real.isoformat() if v.retorno_real else None,
        "km_previsto": v.km_previsto,
        "km_inicial": v.km_inicial,
        "km_final": v.km_final,
        "km_percorrido": v.km_percorrido,
        "total_litros": v.total_litros,
        "total_gasto": v.total_gasto,
        "tempo_total_min": v.tempo_total_min or tempo_min,
        "origem_label": v.origem_label,
        "origem_lat": v.origem_lat,
        "origem_lng": v.origem_lng,
        "destino_label": v.destino_label,
        "destino_lat": v.destino_lat,
        "destino_lng": v.destino_lng,
        "qtd_paradas": qtd_paradas,
        "qtd_paradas_ok": paradas_ok,
        "qtd_pontos_gps": qtd_pontos,
        "qtd_eventos": qtd_eventos,
        "ultima_posicao": {
            "lat": ult.latitude,
            "lng": ult.longitude,
            "em": ult.registrado_em.isoformat(),
            "velocidade": ult.velocidade_kmh,
        } if ult else None,
        "criado_em": v.criado_em.isoformat() if v.criado_em else None,
        "criado_por": v.criado_por,
        "motivo_cancelamento": v.motivo_cancelamento,
        "liberada": bool(v.liberada),
        "liberada_em": v.liberada_em.isoformat() if v.liberada_em else None,
        "liberada_por": v.liberada_por,
        "destino_unico": bool(v.destino_unico),
        "avulsa": bool(v.avulsa),
        "funcionario_responsavel": v.funcionario_responsavel,
        "alertas": alertas,
        "sla": sla,
        "prioridade_operacional": sla["prioridade"],
    }
    if detalhada:
        try:
            out["motorista_link"] = url_for(
                "motorista_viagem.motorista_rastrear",
                vid=v.id, token=_token_motorista(v.id), _external=True,
            )
        except Exception:
            out["motorista_link"] = f"/motorista/viagem/{v.id}/{_token_motorista(v.id)}"
        paradas_regs = ViagemParada.query.filter_by(viagem_id=v.id).order_by(ViagemParada.sequencia).all()
        out["paradas"] = [_parada_dict(p) for p in paradas_regs]
        out["checklist_liberacao"] = _checklist_liberacao(v, paradas_regs)
        out["eventos"] = [_evento_dict(e) for e in ViagemEvento.query.filter_by(viagem_id=v.id).order_by(ViagemEvento.registrado_em).all()]
        out["posicoes"] = [
            {
                "lat": p.latitude, "lng": p.longitude,
                "vel": p.velocidade_kmh, "em": p.registrado_em.isoformat(),
                "ts": p.registrado_em.isoformat(),
                "origem": p.origem,
            }
            for p in ViagemPosicao.query.filter_by(viagem_id=v.id).order_by(ViagemPosicao.registrado_em).all()
        ]
        out["abastecimentos"] = [
            {
                "id": a.id, "data": a.data.isoformat(), "km_atual": a.km_atual,
                "litros": a.litros, "valor_total": a.valor_total, "posto": a.posto,
            }
            for a in FrotaAbastecimento.query.filter_by(viagem_id=v.id).order_by(FrotaAbastecimento.data).all()
        ]
        check = FrotaChecklistDiario.query.filter_by(viagem_id=v.id).first()
        if check:
            try:
                itens = json.loads(check.itens_json) if check.itens_json else []
            except ValueError:
                itens = []
            out["checklist"] = {
                "id": check.id, "status_geral": check.status_geral,
                "data": check.data.isoformat(), "km_atual": check.km_atual,
                "itens": itens, "observacao": check.observacao,
            }
        else:
            out["checklist"] = None
    return out


def _viagem_lista_dict(
    v: Viagem,
    *,
    veiculo: AgendamentoVeiculo | None = None,
    qtd_paradas: int = 0,
    qtd_paradas_ok: int = 0,
) -> dict:
    motorista = AgendamentoMotorista.query.get(v.motorista_id) if v.motorista_id else None
    return {
        "id": v.id,
        "codigo": v.codigo,
        "tipo": v.tipo,
        "status": v.status,
        "titulo": v.titulo,
        "observacao": v.observacao,
        "veiculo_id": v.veiculo_id,
        "veiculo_label": _veiculo_label(veiculo),
        "motorista_id": v.motorista_id,
        "motorista_nome": v.motorista_nome or (motorista.nome if motorista else None),
        "saida_prevista": v.saida_prevista.isoformat() if v.saida_prevista else None,
        "retorno_previsto": v.retorno_previsto.isoformat() if v.retorno_previsto else None,
        "saida_real": v.saida_real.isoformat() if v.saida_real else None,
        "retorno_real": v.retorno_real.isoformat() if v.retorno_real else None,
        "qtd_paradas": int(qtd_paradas or 0),
        "qtd_paradas_ok": int(qtd_paradas_ok or 0),
        "liberada": bool(v.liberada),
        "criado_em": v.criado_em.isoformat() if v.criado_em else None,
        "criado_por": v.criado_por,
    }


def _parada_dict(p: ViagemParada) -> dict:
    try:
        fotos = json.loads(p.foto_paths) if p.foto_paths else []
    except ValueError:
        fotos = []
    return {
        "id": p.id,
        "viagem_id": p.viagem_id,
        "sequencia": p.sequencia,
        "solicitacao_id": p.solicitacao_id,
        "tipo": p.tipo,
        "parceiro_nome": p.parceiro_nome,
        "endereco": p.endereco,
        "cidade": p.cidade,
        "uf": p.uf,
        "latitude": p.latitude,
        "longitude": p.longitude,
        "previsao_chegada": p.previsao_chegada.isoformat() if p.previsao_chegada else None,
        "chegada_real": p.chegada_real.isoformat() if p.chegada_real else None,
        "saida_real": p.saida_real.isoformat() if p.saida_real else None,
        "status": p.status,
        "resultado": p.resultado,
        "observacao": p.observacao,
        "assinatura_path": p.assinatura_path,
        "foto_paths": fotos,
    }


def _evento_dict(e: ViagemEvento) -> dict:
    return {
        "id": e.id,
        "tipo": e.tipo,
        "titulo": e.titulo,
        "descricao": e.descricao,
        "latitude": e.latitude,
        "longitude": e.longitude,
        "km": e.km,
        "foto_path": e.foto_path,
        "severidade": e.severidade,
        "parada_id": e.parada_id,
        "registrado_por": e.registrado_por,
        "registrado_em": e.registrado_em.isoformat(),
    }


def _log_evento(viagem_id, tipo, titulo, **kwargs):
    db.session.add(ViagemEvento(
        viagem_id=viagem_id,
        tipo=tipo,
        titulo=titulo,
        descricao=kwargs.get("descricao"),
        latitude=kwargs.get("latitude"),
        longitude=kwargs.get("longitude"),
        km=kwargs.get("km"),
        foto_path=kwargs.get("foto_path"),
        severidade=kwargs.get("severidade", "info"),
        parada_id=kwargs.get("parada_id"),
        registrado_por=_user(),
    ))


def _endereco_parada(parada: ViagemParada) -> str:
    return montar_endereco_rota({
        "logradouro": parada.endereco,
        "cidade": parada.cidade,
        "uf": parada.uf,
    })


def _enderecos_candidatos_parada(parada: ViagemParada) -> list[str]:
    endereco = str(parada.endereco or "").strip()
    cidade = str(parada.cidade or "").strip()
    uf = str(parada.uf or "").strip()
    candidatos = [
        _endereco_parada(parada),
        ", ".join(p for p in [endereco, cidade, uf, "Brasil"] if p),
        ", ".join(p for p in [endereco, cidade, "São Paulo", "Brasil"] if p and uf.upper() == "SP"),
        ", ".join(p for p in [parada.parceiro_nome, endereco, cidade, uf, "Brasil"] if p),
    ]
    vistos = set()
    saida = []
    for candidato in candidatos:
        candidato = " ".join(str(candidato or "").split()).strip(" ,")
        chave = candidato.lower()
        if candidato and chave not in vistos:
            vistos.add(chave)
            saida.append(candidato)
    return saida


def _geocodificar_parada(parada: ViagemParada) -> bool:
    if parada.latitude is not None and parada.longitude is not None:
        return False
    geo = None
    for endereco in _enderecos_candidatos_parada(parada):
        geo = _geocode_endereco(endereco)
        if geo:
            break
    if not geo:
        current_app.logger.warning(
            "Nao foi possivel geocodificar parada %s: %s",
            parada.id,
            " | ".join(_enderecos_candidatos_parada(parada)),
        )
        return False
    parada.latitude = geo.get("lat")
    parada.longitude = geo.get("lng")
    return parada.latitude is not None and parada.longitude is not None


def _geocodificar_paradas_sem_coord(paradas: list[ViagemParada]) -> int:
    atualizadas = 0
    for parada in paradas:
        if _geocodificar_parada(parada):
            atualizadas += 1
    return atualizadas


def _janela_inicio_minutos(texto: str | None) -> int | None:
    raw = str(texto or "").strip()
    if not raw:
        return None
    match = re.search(r"(\d{1,2})[:h](\d{2})?", raw.lower())
    if not match:
        return None
    hora = max(0, min(23, int(match.group(1) or 0)))
    minuto = int(match.group(2) or 0)
    minuto = max(0, min(59, minuto))
    return hora * 60 + minuto


def _janela_atendimento_parada(parada: ViagemParada) -> int | None:
    if parada.solicitacao_id:
        sol = db.session.get(AgendamentoSolicitacao, parada.solicitacao_id)
        if sol:
            model = AgendamentoFornecedor if sol.parceiro_tipo == "Fornecedor" else AgendamentoCliente
            cadastro = None
            if sol.parceiro_codigo:
                cadastro = model.query.filter_by(codigo=sol.parceiro_codigo).first()
            if not cadastro and sol.parceiro_documento:
                cadastro = model.query.filter_by(cnpj_cpf=sol.parceiro_documento).first()
            if not cadastro and sol.parceiro_nome:
                cadastro = model.query.filter(model.nome.ilike(f"%{sol.parceiro_nome}%")).first()
            janela = getattr(cadastro, "janela_atendimento", None) if cadastro else None
            return _janela_inicio_minutos(janela)
    return None


def _reordenar_paradas(viagem_id: int, ids: list[int], *, permitir_concluidas: bool = False) -> dict:
    paradas = ViagemParada.query.filter_by(viagem_id=viagem_id).order_by(ViagemParada.sequencia).all()
    por_id = {p.id: p for p in paradas}
    ids_limpos: list[int] = []
    for raw in ids:
        pid = _parse_int(raw)
        if pid and pid in por_id and pid not in ids_limpos:
            ids_limpos.append(pid)
    if not ids_limpos:
        return {"ok": False, "msg": "Informe a ordem das paradas."}

    bloqueadas = [p for p in paradas if p.status not in ("Pendente", "EmAndamento")]
    editaveis = [p for p in paradas if permitir_concluidas or p.status in ("Pendente", "EmAndamento")]
    editaveis_ids = {p.id for p in editaveis}
    ordem_editavel = [por_id[pid] for pid in ids_limpos if pid in editaveis_ids]
    ordem_editavel += [p for p in editaveis if p.id not in ids_limpos]

    seq = 1
    for parada in sorted(bloqueadas, key=lambda p: p.sequencia or 0):
        parada.sequencia = seq
        seq += 1
    for parada in ordem_editavel:
        parada.sequencia = seq
        seq += 1
    return {"ok": True, "paradas_ordenadas": len(ordem_editavel)}


def _sync_solicitacao_viagem(sol: AgendamentoSolicitacao | None, v: Viagem, status: str = "Alocada") -> None:
    """Mantem a solicitacao de transporte alinhada com a viagem consolidada."""
    if not sol:
        return
    status_atual = str(sol.status or "").strip()
    if status_atual in {"Concluida", "Cancelada"} and status != "Concluida":
        return
    motorista = AgendamentoMotorista.query.get(v.motorista_id) if v.motorista_id else None
    sol.veiculo_id = v.veiculo_id
    sol.motorista_id = v.motorista_id
    sol.motorista_nome = v.motorista_nome or (motorista.nome if motorista else None)
    sol.data_hora_saida_prevista = sol.data_hora_saida_prevista or v.saida_prevista
    sol.data_hora_retorno_prevista = sol.data_hora_retorno_prevista or v.retorno_previsto
    sol.alocado_por = sol.alocado_por or _user()
    sol.alocado_em = sol.alocado_em or datetime.now()
    sol.status = status
    sol.atualizado_em = datetime.now()


def _sync_solicitacoes_da_viagem(v: Viagem, status: str) -> None:
    paradas = ViagemParada.query.filter(
        ViagemParada.viagem_id == v.id,
        ViagemParada.solicitacao_id.isnot(None),
    ).all()
    for parada in paradas:
        if parada.status == "Nao_realizada" or (status == "Concluida" and parada.status != "Concluida"):
            _solicitacao_volta_pendente(db.session.get(AgendamentoSolicitacao, parada.solicitacao_id))
            continue
        _sync_solicitacao_viagem(db.session.get(AgendamentoSolicitacao, parada.solicitacao_id), v, status)


def _atualizar_tipo_viagem_por_paradas(v: Viagem | None) -> None:
    if not v:
        return
    tipos = {
        str(p.tipo or "").upper().strip()
        for p in ViagemParada.query.filter_by(viagem_id=v.id).all()
        if str(p.tipo or "").strip()
    }
    tipos_validos = {t for t in tipos if t in {"COLETA", "ENTREGA"}}
    if len(tipos_validos) >= 2:
        v.tipo = "MISTA"
    elif len(tipos_validos) == 1:
        v.tipo = list(tipos_validos)[0]


def _solicitacao_volta_pendente(sol: AgendamentoSolicitacao | None) -> None:
    if not sol or str(sol.status or "").strip() in {"Concluida", "Cancelada"}:
        return
    sol.status = "Pendente"
    sol.veiculo_id = None
    sol.motorista_id = None
    sol.motorista_nome = None
    sol.data_hora_saida_prevista = None
    sol.data_hora_retorno_prevista = None
    sol.alocado_por = None
    sol.alocado_em = None
    sol.atualizado_em = datetime.now()


def _filtro_solicitacao_visivel_viagem():
    return true()


# --------------------------------------------------------------------------- LISTAGEM / DASHBOARD
@viagem_bp.route("/dashboard", methods=["GET"])
@permission_required(PERM)
def dashboard():
    hoje = date.today()
    inicio = datetime.combine(hoje, datetime.min.time())
    fim = datetime.combine(hoje, datetime.max.time())

    em_andamento = Viagem.query.filter_by(status="EmAndamento").count()
    planejadas_hoje = Viagem.query.filter(
        Viagem.status == "Planejada",
        Viagem.saida_prevista >= inicio,
        Viagem.saida_prevista <= fim,
    ).count()
    concluidas_hoje = Viagem.query.filter(
        Viagem.status == "Concluida",
        Viagem.retorno_real >= inicio,
        Viagem.retorno_real <= fim,
    ).count()
    pendentes_liberacao = Viagem.query.filter(
        Viagem.status == "Planejada",
        Viagem.liberada == False,  # noqa: E712
    ).count()
    atrasadas = Viagem.query.filter(
        Viagem.status == "Planejada",
        Viagem.saida_prevista < datetime.now(),
    ).count()
    km_hoje = db.session.query(func.coalesce(func.sum(Viagem.km_percorrido), 0)).filter(
        Viagem.status == "Concluida",
        Viagem.retorno_real >= inicio,
        Viagem.retorno_real <= fim,
    ).scalar() or 0

    # Últimas viagens
    regs = Viagem.query.order_by(Viagem.criado_em.desc()).limit(30).all()

    return jsonify({
        "kpis": {
            "em_andamento": em_andamento,
            "planejadas_hoje": planejadas_hoje,
            "concluidas_hoje": concluidas_hoje,
            "km_hoje": round(float(km_hoje), 1),
            "pendentes_liberacao": pendentes_liberacao,
            "atrasadas": atrasadas,
        },
        "recentes": [_viagem_dict(v) for v in regs],
    })


@viagem_bp.route("/torre-controle", methods=["GET"])
@permission_required(PERM)
def torre_controle():
    agora = datetime.now()
    inicio = agora - timedelta(hours=12)
    fim = agora + timedelta(days=2)
    viagens = (
        Viagem.query
        .filter(Viagem.status.in_(["Planejada", "EmAndamento"]))
        .filter((Viagem.saida_prevista == None) | ((Viagem.saida_prevista >= inicio) & (Viagem.saida_prevista <= fim)))  # noqa: E711
        .order_by(Viagem.saida_prevista.asc().nullslast(), Viagem.criado_em.desc())
        .limit(120)
        .all()
    )
    items = []
    resumo = {"criticas": 0, "altas": 0, "sem_gps": 0, "pendentes_liberacao": 0, "em_rota": 0}
    for v in viagens:
        ult = (
            ViagemPosicao.query.filter_by(viagem_id=v.id)
            .order_by(ViagemPosicao.registrado_em.desc())
            .first()
        )
        sla = _sla_viagem(v, ult)
        alertas = _alertas_operacionais(v, ult)
        if sla["prioridade"] == "Critica":
            resumo["criticas"] += 1
        if sla["prioridade"] == "Alta":
            resumo["altas"] += 1
        if v.status == "EmAndamento":
            resumo["em_rota"] += 1
        if v.status == "Planejada" and not v.liberada:
            resumo["pendentes_liberacao"] += 1
        if any("GPS" in a.get("msg", "") for a in alertas):
            resumo["sem_gps"] += 1
        items.append({
            "id": v.id,
            "codigo": v.codigo,
            "status": v.status,
            "titulo": v.titulo,
            "veiculo": _veiculo_label(AgendamentoVeiculo.query.get(v.veiculo_id)),
            "motorista": v.motorista_nome or "",
            "saida_prevista": v.saida_prevista.isoformat() if v.saida_prevista else None,
            "retorno_previsto": v.retorno_previsto.isoformat() if v.retorno_previsto else None,
            "ultima_posicao": ult.registrado_em.isoformat() if ult else None,
            "sla": sla,
            "alertas": alertas,
        })
    prioridade_ordem = {"Critica": 0, "Alta": 1, "Media": 2, "Baixa": 3}
    items.sort(key=lambda i: (prioridade_ordem.get(i["sla"]["prioridade"], 9), i.get("saida_prevista") or "9999"))
    return jsonify({"resumo": resumo, "items": items[:40], "gerado_em": agora.isoformat()})


def dados_mapa_frota() -> dict:
    """Posicao atual de cada veiculo ativo (ultimo ping de GPS da viagem em
    andamento, com fallback para o rastreamento manual). Funcao pura (sem
    jsonify) para poder ser reaproveitada tanto pela rota autenticada
    /api/viagem/mapa-frota quanto pelo painel de TV publico."""
    try:
        from ..services import rastreamento_store

        rastreamento = rastreamento_store.estado_publico()
    except Exception as exc:
        current_app.logger.warning("Falha ao carregar estado de rastreamento: %s", exc)
        rastreamento = {"base": {}, "veiculos": []}

    posicoes_por_placa: dict[str, dict] = {}
    for item in rastreamento.get("veiculos") or []:
        placa_key = _normalizar_placa(item.get("placa"))
        if placa_key and item.get("latitude") is not None and item.get("longitude") is not None:
            posicoes_por_placa[placa_key] = item

    veiculos = (
        AgendamentoVeiculo.query
        .filter_by(ativo=True)
        .order_by(AgendamentoVeiculo.ordem_exibicao.asc(), AgendamentoVeiculo.nome_exibicao.asc())
        .all()
    )
    saida = []
    sem_posicao = 0
    for veiculo in veiculos:
        viagem = (
            Viagem.query
            .filter_by(veiculo_id=veiculo.id, status="EmAndamento")
            .order_by(Viagem.saida_real.desc(), Viagem.id.desc())
            .first()
        )
        ult = None
        if viagem:
            ult = (
                ViagemPosicao.query.filter_by(viagem_id=viagem.id)
                .order_by(ViagemPosicao.registrado_em.desc())
                .first()
            )

        latitude = ult.latitude if ult else None
        longitude = ult.longitude if ult else None
        atualizado_em = ult.registrado_em.isoformat() if ult else None
        velocidade = ult.velocidade_kmh if ult else None
        origem = ult.origem if ult else None

        rastreado = posicoes_por_placa.get(_normalizar_placa(veiculo.placa))
        if latitude is None and rastreado:
            latitude = rastreado.get("latitude")
            longitude = rastreado.get("longitude")
            atualizado_em = rastreado.get("atualizado_em")
            velocidade = rastreado.get("velocidade_kmh")
            origem = "rastreamento"

        if latitude is None or longitude is None:
            sem_posicao += 1
            continue

        saida.append({
            "veiculo_id": veiculo.id,
            "veiculo_label": _veiculo_label(veiculo),
            "placa": veiculo.placa,
            "latitude": latitude,
            "longitude": longitude,
            "atualizado_em": atualizado_em,
            "velocidade_kmh": velocidade,
            "origem": origem or "sem_viagem",
            "em_viagem": viagem is not None,
            "viagem": _viagem_resumo_mapa(viagem),
        })

    return {
        "base": rastreamento.get("base") or {},
        "veiculos": saida,
        "sem_posicao": sem_posicao,
        "gerado_em": datetime.now().isoformat(),
    }


@viagem_bp.route("/mapa-frota", methods=["GET"])
@permission_required_any(PERM, "PAGE_LOGISTICA_RASTREAMENTO")
def mapa_frota():
    return jsonify(dados_mapa_frota())


@viagem_bp.route("/agenda", methods=["GET"])
@permission_required(PERM)
def agenda_viagens():
    data_raw = (request.args.get("data") or "").strip()
    modo = (request.args.get("modo") or "dia").strip().lower()
    if modo not in {"dia", "semana"}:
        modo = "dia"
    try:
        base = datetime.strptime(data_raw, "%Y-%m-%d") if data_raw else datetime.now()
    except ValueError:
        base = datetime.now()
    inicio = base.replace(hour=0, minute=0, second=0, microsecond=0)
    fim = inicio + timedelta(days=7 if modo == "semana" else 1)
    viagens = (
        Viagem.query
        .filter(Viagem.status.in_(["Planejada", "EmAndamento"]))
        .filter(Viagem.saida_prevista.isnot(None))
        .filter(Viagem.saida_prevista >= inicio)
        .filter(Viagem.saida_prevista < fim)
        .order_by(Viagem.saida_prevista.asc())
        .all()
    )
    linhas: dict[str, dict] = {}
    for v in viagens:
        veiculo = AgendamentoVeiculo.query.get(v.veiculo_id)
        motorista = AgendamentoMotorista.query.get(v.motorista_id) if v.motorista_id else None
        for tipo, ref_id, label in (
            ("veiculo", v.veiculo_id, _veiculo_label(veiculo)),
            ("motorista", v.motorista_id, motorista.nome if motorista else "Sem motorista"),
        ):
            if not ref_id and tipo == "motorista":
                continue
            key = f"{tipo}:{ref_id or 'sem'}"
            linhas.setdefault(key, {"tipo": tipo, "id": ref_id, "label": label, "items": []})
            ini_v, fim_v = _intervalo_viagem(v)
            linhas[key]["items"].append({
                "id": v.id,
                "codigo": v.codigo,
                "titulo": v.titulo or v.codigo,
                "status": v.status,
                "inicio": ini_v.isoformat() if ini_v else None,
                "fim": fim_v.isoformat() if fim_v else None,
                "motorista": v.motorista_nome or (motorista.nome if motorista else ""),
                "veiculo": _veiculo_label(veiculo),
                "sla": _sla_viagem(v),
            })
    return jsonify({"inicio": inicio.isoformat(), "fim": fim.isoformat(), "modo": modo, "linhas": list(linhas.values())})


@viagem_bp.route("/lista", methods=["GET"])
@permission_required(PERM)
def listar():
    resumo = str(request.args.get("resumo") or "").strip().lower() in {"1", "true", "sim", "yes"}
    q = Viagem.query
    termo = str(request.args.get("q") or "").strip()
    status = request.args.get("status")
    if status:
        q = q.filter_by(status=status)
    if request.args.get("veiculo_id"):
        q = q.filter_by(veiculo_id=_parse_int(request.args.get("veiculo_id")))
    if request.args.get("motorista_id"):
        q = q.filter_by(motorista_id=_parse_int(request.args.get("motorista_id")))
    de_raw = request.args.get("de")
    ate_raw = request.args.get("ate")
    de = _parse_dt(de_raw)
    ate = _parse_dt(ate_raw)
    if ate and ate_raw and len(str(ate_raw).strip()) == 10:
        ate = ate + timedelta(days=1) - timedelta(seconds=1)
    data_base = func.coalesce(Viagem.saida_prevista, Viagem.criado_em)
    if de:
        q = q.filter(data_base >= de)
    if ate:
        q = q.filter(data_base <= ate)
    if termo:
        like = f"%{termo}%"
        viagem_ids_doc = (
            db.session.query(ViagemParada.viagem_id)
            .join(AgendamentoSolicitacao, AgendamentoSolicitacao.id == ViagemParada.solicitacao_id)
            .filter(
                or_(
                    AgendamentoSolicitacao.numero_oc.ilike(like),
                    AgendamentoSolicitacao.numero_nf.ilike(like),
                    AgendamentoSolicitacao.orcamento.ilike(like),
                    AgendamentoSolicitacao.documento_numero.ilike(like),
                    AgendamentoSolicitacao.codigo.ilike(like),
                    AgendamentoSolicitacao.parceiro_nome.ilike(like),
                )
            )
        )
        q = q.filter(
            or_(
                Viagem.codigo.ilike(like),
                Viagem.titulo.ilike(like),
                Viagem.observacao.ilike(like),
                Viagem.motorista_nome.ilike(like),
                Viagem.origem_label.ilike(like),
                Viagem.id.in_(viagem_ids_doc),
            )
        )
    regs = q.order_by(Viagem.criado_em.desc()).limit(300).all()
    if not resumo:
        return jsonify([_viagem_dict(v) for v in regs])

    ids = [int(v.id) for v in regs]
    qtd_paradas_por_viagem: dict[int, int] = {}
    qtd_paradas_ok_por_viagem: dict[int, int] = {}
    if ids:
        rows = (
            db.session.query(ViagemParada.viagem_id, func.count(ViagemParada.id))
            .filter(ViagemParada.viagem_id.in_(ids))
            .group_by(ViagemParada.viagem_id)
            .all()
        )
        qtd_paradas_por_viagem = {int(vid): int(qtd or 0) for vid, qtd in rows}
        rows_ok = (
            db.session.query(ViagemParada.viagem_id, func.count(ViagemParada.id))
            .filter(ViagemParada.viagem_id.in_(ids), ViagemParada.status == "Concluida")
            .group_by(ViagemParada.viagem_id)
            .all()
        )
        qtd_paradas_ok_por_viagem = {int(vid): int(qtd or 0) for vid, qtd in rows_ok}

    veiculo_ids = {int(v.veiculo_id) for v in regs if v.veiculo_id}
    veiculos = {}
    if veiculo_ids:
        veiculos = {int(v.id): v for v in AgendamentoVeiculo.query.filter(AgendamentoVeiculo.id.in_(veiculo_ids)).all()}

    return jsonify([
        _viagem_lista_dict(
            v,
            veiculo=veiculos.get(int(v.veiculo_id)) if v.veiculo_id else None,
            qtd_paradas=qtd_paradas_por_viagem.get(int(v.id), 0),
            qtd_paradas_ok=qtd_paradas_ok_por_viagem.get(int(v.id), 0),
        )
        for v in regs
    ])


@viagem_bp.route("/<int:vid>", methods=["GET"])
@permission_required(PERM)
def detalhe(vid: int):
    v = db.session.get(Viagem, vid)
    if not v:
        return jsonify({"sucesso": False, "msg": "Viagem não encontrada."}), 404
    return jsonify(_viagem_dict(v, detalhada=True))


# --------------------------------------------------------------------------- CRIAR / EDITAR / EXCLUIR
@viagem_bp.route("", methods=["POST"])
@permission_required(PERM)
def criar():
    return jsonify({"sucesso": False, "msg": "Criação manual de viagens foi descontinuada. Use a Central de Viagens."}), 410


@viagem_bp.route("/<int:vid>", methods=["PATCH"])
@permission_required(PERM)
def editar(vid: int):
    v = db.session.get(Viagem, vid)
    if not v:
        return jsonify({"sucesso": False, "msg": "Viagem não encontrada."}), 404
    if v.status in ("Concluida", "Cancelada"):
        return jsonify({"sucesso": False, "msg": "Viagem finalizada não pode ser editada."}), 400
    p = request.get_json(silent=True) or {}
    novo_tipo = str(p.get("tipo") or v.tipo or "MISTA").upper().strip()
    nova_observacao = str(p.get("observacao") if "observacao" in p else (v.observacao or "")).strip()
    if "tipo" in p and novo_tipo not in {"MISTA", "COLETA", "ENTREGA", "ALEATORIA"}:
        return jsonify({"sucesso": False, "msg": "Tipo de viagem invalido."}), 400
    if novo_tipo == "ALEATORIA" and not nova_observacao:
        return jsonify({"sucesso": False, "msg": "Descricao obrigatoria para viagem aleatoria."}), 400
    for campo in ("titulo", "observacao", "origem_label", "destino_label", "tipo"):
        if campo in p:
            valor = str(p[campo] or "").strip()
            if campo == "tipo":
                valor = valor.upper()
            setattr(v, campo, valor or None)
    if "motorista_id" in p:
        mid = _parse_int(p["motorista_id"])
        v.motorista_id = mid
        m = AgendamentoMotorista.query.get(mid) if mid else None
        v.motorista_nome = m.nome if m else None
    if "veiculo_id" in p:
        v.veiculo_id = _parse_int(p["veiculo_id"]) or v.veiculo_id
    for campo in ("saida_prevista", "retorno_previsto"):
        if campo in p:
            setattr(v, campo, _parse_dt(p[campo]))
    conflito = _validar_conflito_recurso(
        veiculo_id=v.veiculo_id,
        motorista_id=v.motorista_id,
        saida=v.saida_prevista,
        retorno=v.retorno_previsto,
        viagem_id=v.id,
    )
    if conflito:
        return jsonify({"sucesso": False, "msg": conflito}), 409
    if "km_previsto" in p:
        v.km_previsto = _parse_float(p["km_previsto"])
    for campo in ("origem_lat", "origem_lng", "destino_lat", "destino_lng"):
        if campo in p:
            setattr(v, campo, _parse_float(p[campo]))
    v.atualizado_em = datetime.now()
    if v.status in ("Planejada", "EmAndamento"):
        _sync_solicitacoes_da_viagem(v, "EmRota" if v.status == "EmAndamento" else "Alocada")
    _log_evento(
        v.id,
        "OBSERVACAO",
        "Dados da viagem atualizados",
        descricao=f"Alteração realizada por {_user()}.",
        severidade="info",
    )
    db.session.commit()
    return jsonify({"sucesso": True, "viagem": _viagem_dict(v, detalhada=True)})


@viagem_bp.route("/<int:vid>", methods=["DELETE"])
@permission_required(PERM, "Admin")
def excluir(vid: int):
    v = db.session.get(Viagem, vid)
    if not v:
        return jsonify({"sucesso": False, "msg": "Viagem não encontrada."}), 404
    if v.status == "EmAndamento":
        return jsonify({"sucesso": False, "msg": "Não é possível excluir viagem em andamento. Cancele primeiro."}), 400
    for parada in ViagemParada.query.filter_by(viagem_id=vid).all():
        if parada.solicitacao_id and v.status == "Planejada":
            _solicitacao_volta_pendente(db.session.get(AgendamentoSolicitacao, parada.solicitacao_id))
    ViagemEvento.query.filter_by(viagem_id=vid).delete()
    ViagemPosicao.query.filter_by(viagem_id=vid).delete()
    ViagemParada.query.filter_by(viagem_id=vid).delete()
    # Desvincular (não apagar) checklist/abastecimento
    FrotaAbastecimento.query.filter_by(viagem_id=vid).update({"viagem_id": None})
    FrotaChecklistDiario.query.filter_by(viagem_id=vid).update({"viagem_id": None})
    db.session.delete(v)
    db.session.commit()
    return jsonify({"sucesso": True})


# --------------------------------------------------------------------------- AÇÕES DE EXECUÇÃO
@viagem_bp.route("/<int:vid>/iniciar", methods=["POST"])
@permission_required(PERM)
def iniciar(vid: int):
    v = db.session.get(Viagem, vid)
    if not v:
        return jsonify({"sucesso": False, "msg": "Viagem não encontrada."}), 404
    if v.status != "Planejada":
        return jsonify({"sucesso": False, "msg": f"Viagem não pode ser iniciada (status {v.status})."}), 400
    p = request.get_json(silent=True) or {}
    v.saida_real = datetime.now()
    v.km_inicial = _parse_int(p.get("km_inicial"))
    v.status = "EmAndamento"
    v.iniciado_por = _user()
    _sync_solicitacoes_da_viagem(v, "EmRota")
    lat = _parse_float(p.get("latitude"))
    lng = _parse_float(p.get("longitude"))
    if lat is not None and lng is not None:
        db.session.add(ViagemPosicao(
            viagem_id=v.id, latitude=lat, longitude=lng,
            origem="motorista_app", registrado_em=datetime.now(),
        ))
    _log_evento(v.id, "INICIO", "Viagem iniciada",
                descricao=f"KM inicial: {v.km_inicial or '—'}",
                latitude=lat, longitude=lng, km=v.km_inicial, severidade="success")
    db.session.commit()
    return jsonify({"sucesso": True, "viagem": _viagem_dict(v, detalhada=True)})


@viagem_bp.route("/<int:vid>/concluir", methods=["POST"])
@permission_required(PERM)
def concluir(vid: int):
    v = db.session.get(Viagem, vid)
    if not v:
        return jsonify({"sucesso": False, "msg": "Viagem não encontrada."}), 404
    if v.status != "EmAndamento":
        return jsonify({"sucesso": False, "msg": f"Viagem não está em andamento (status {v.status})."}), 400
    p = request.get_json(silent=True) or {}
    v.retorno_real = datetime.now()
    v.km_final = _parse_int(p.get("km_final"))
    v.status = "Concluida"
    v.concluido_por = _user()
    _sync_solicitacoes_da_viagem(v, "Concluida")

    # KM percorrido
    if v.km_inicial is not None and v.km_final is not None and v.km_final >= v.km_inicial:
        v.km_percorrido = float(v.km_final - v.km_inicial)
    else:
        # fallback: somatório das distâncias entre pontos GPS consecutivos
        pts = ViagemPosicao.query.filter_by(viagem_id=vid).order_by(ViagemPosicao.registrado_em).all()
        km = 0.0
        for i in range(1, len(pts)):
            km += _haversine_km(pts[i-1].latitude, pts[i-1].longitude, pts[i].latitude, pts[i].longitude)
        v.km_percorrido = round(km, 2)

    # Totais de abastecimento da viagem
    abs_tot = db.session.query(
        func.coalesce(func.sum(FrotaAbastecimento.litros), 0),
        func.coalesce(func.sum(FrotaAbastecimento.valor_total), 0),
    ).filter(FrotaAbastecimento.viagem_id == vid).first()
    v.total_litros = float(abs_tot[0] or 0)
    v.total_gasto = float(abs_tot[1] or 0)

    if v.saida_real and v.retorno_real:
        v.tempo_total_min = int((v.retorno_real - v.saida_real).total_seconds() // 60)

    lat = _parse_float(p.get("latitude"))
    lng = _parse_float(p.get("longitude"))
    if lat is not None and lng is not None:
        db.session.add(ViagemPosicao(
            viagem_id=v.id, latitude=lat, longitude=lng,
            origem="motorista_app", registrado_em=datetime.now(),
        ))
    _log_evento(v.id, "FIM", "Viagem concluída",
                descricao=f"KM final: {v.km_final or '—'} · percorrido: {v.km_percorrido or 0:.1f} km",
                latitude=lat, longitude=lng, km=v.km_final, severidade="success")
    db.session.commit()
    return jsonify({"sucesso": True, "viagem": _viagem_dict(v, detalhada=True)})


@viagem_bp.route("/<int:vid>/cancelar", methods=["POST"])
@permission_required(PERM)
def cancelar(vid: int):
    v = db.session.get(Viagem, vid)
    if not v:
        return jsonify({"sucesso": False, "msg": "Viagem não encontrada."}), 404
    if v.status in ("Concluida", "Cancelada"):
        return jsonify({"sucesso": False, "msg": "Viagem já finalizada."}), 400
    p = request.get_json(silent=True) or {}
    motivo = str(p.get("motivo") or "").strip()
    if not motivo:
        return jsonify({"sucesso": False, "msg": "Motivo obrigatório."}), 400
    v.status = "Cancelada"
    v.cancelado_por = _user()
    v.motivo_cancelamento = motivo
    for parada in ViagemParada.query.filter_by(viagem_id=vid).all():
        if parada.solicitacao_id:
            _solicitacao_volta_pendente(db.session.get(AgendamentoSolicitacao, parada.solicitacao_id))
    _log_evento(v.id, "FIM", "Viagem cancelada", descricao=motivo, severidade="danger")
    db.session.commit()
    return jsonify({"sucesso": True, "viagem": _viagem_dict(v, detalhada=True)})


# --------------------------------------------------------------------------- PARADAS
@viagem_bp.route("/<int:vid>/paradas", methods=["POST"])
@permission_required(PERM)
def criar_parada(vid: int):
    v = db.session.get(Viagem, vid)
    if not v:
        return jsonify({"sucesso": False, "msg": "Viagem não encontrada."}), 404
    p = request.get_json(silent=True) or {}
    if v.status in ("Concluida", "Cancelada"):
        return jsonify({"sucesso": False, "msg": "Viagem finalizada nao pode receber paradas."}), 400
    ultima = db.session.query(func.coalesce(func.max(ViagemParada.sequencia), 0)).filter_by(viagem_id=vid).scalar() or 0
    parada = ViagemParada(
        viagem_id=vid,
        sequencia=ultima + 1,
        solicitacao_id=_parse_int(p.get("solicitacao_id")),
        tipo=str(p.get("tipo") or "ENTREGA").upper(),
        parceiro_nome=str(p.get("parceiro_nome") or "").strip() or None,
        endereco=str(p.get("endereco") or "").strip() or None,
        cidade=str(p.get("cidade") or "").strip() or None,
        uf=str(p.get("uf") or "").strip() or None,
        latitude=_parse_float(p.get("latitude")),
        longitude=_parse_float(p.get("longitude")),
        previsao_chegada=_parse_dt(p.get("previsao_chegada")),
    )
    db.session.add(parada)
    db.session.flush()
    if parada.solicitacao_id:
        _sync_solicitacao_viagem(db.session.get(AgendamentoSolicitacao, parada.solicitacao_id), v, "EmRota" if v.status == "EmAndamento" else "Alocada")
    _log_evento(vid, "PARADA_EXTRA", f"Parada adicionada: {parada.parceiro_nome or parada.endereco or parada.tipo}", parada_id=parada.id)
    reord = None
    db.session.commit()
    return jsonify({"sucesso": True, "parada": _parada_dict(parada), "otimizacao": reord})


@viagem_bp.route("/paradas/<int:pid>/chegar", methods=["POST"])
@permission_required(PERM)
def parada_chegar(pid: int):
    parada = db.session.get(ViagemParada, pid)
    if not parada:
        return jsonify({"sucesso": False, "msg": "Parada não encontrada."}), 404
    p = request.get_json(silent=True) or {}
    parada.chegada_real = datetime.now()
    parada.status = "EmAndamento"
    if parada.solicitacao_id:
        sol = db.session.get(AgendamentoSolicitacao, parada.solicitacao_id)
        viagem = db.session.get(Viagem, parada.viagem_id)
        if viagem:
            _sync_solicitacao_viagem(sol, viagem, "EmRota")
    lat = _parse_float(p.get("latitude"))
    lng = _parse_float(p.get("longitude"))
    if lat is not None and lng is not None:
        db.session.add(ViagemPosicao(
            viagem_id=parada.viagem_id, latitude=lat, longitude=lng,
            origem="motorista_app", registrado_em=datetime.now(),
        ))
    _log_evento(parada.viagem_id, "CHEGADA",
                f"Chegada em {parada.parceiro_nome or parada.endereco or 'parada'}",
                parada_id=pid, latitude=lat, longitude=lng, severidade="info")
    db.session.commit()
    return jsonify({"sucesso": True, "parada": _parada_dict(parada)})


@viagem_bp.route("/paradas/<int:pid>/concluir", methods=["POST"])
@permission_required(PERM)
def parada_concluir(pid: int):
    parada = db.session.get(ViagemParada, pid)
    if not parada:
        return jsonify({"sucesso": False, "msg": "Parada não encontrada."}), 404
    if request.content_type and "multipart" in request.content_type:
        p = request.form.to_dict()
        foto = _save_upload("foto")
    else:
        p = request.get_json(silent=True) or {}
        foto = None
    parada.saida_real = datetime.now()
    parada.status = "Concluida"
    if parada.solicitacao_id:
        sol = db.session.get(AgendamentoSolicitacao, parada.solicitacao_id)
        viagem = db.session.get(Viagem, parada.viagem_id)
        if viagem:
            _sync_solicitacao_viagem(sol, viagem, "Concluida")
    parada.resultado = str(p.get("resultado") or "").strip() or None
    parada.observacao = str(p.get("observacao") or "").strip() or parada.observacao
    if foto:
        try:
            atual = json.loads(parada.foto_paths) if parada.foto_paths else []
        except ValueError:
            atual = []
        atual.append(foto)
        parada.foto_paths = json.dumps(atual, ensure_ascii=False)
    lat = _parse_float(p.get("latitude"))
    lng = _parse_float(p.get("longitude"))
    if lat is not None and lng is not None:
        db.session.add(ViagemPosicao(
            viagem_id=parada.viagem_id, latitude=lat, longitude=lng,
            origem="motorista_app", registrado_em=datetime.now(),
        ))
    _log_evento(parada.viagem_id, "SAIDA_PARADA",
                f"Saída de {parada.parceiro_nome or 'parada'} · {parada.resultado or 'Concluída'}",
                descricao=parada.observacao, parada_id=pid,
                latitude=lat, longitude=lng, foto_path=foto, severidade="success")
    db.session.commit()
    _integrar_comprovante_entrega(parada)
    return jsonify({"sucesso": True, "parada": _parada_dict(parada)})


@viagem_bp.route("/paradas/<int:pid>/foto/<int:idx>", methods=["GET"])
@permission_required(PERM)
def parada_foto(pid: int, idx: int):
    """Serve a foto do canhoto (ou outra evidência) anexada na conclusão da parada."""
    parada = db.session.get(ViagemParada, pid)
    if not parada or not parada.foto_paths:
        return ("Foto não encontrada.", 404)
    try:
        fotos = json.loads(parada.foto_paths)
    except ValueError:
        fotos = []
    if idx < 0 or idx >= len(fotos):
        return ("Foto não encontrada.", 404)
    caminho = os.path.join(current_app.instance_path, fotos[idx])
    if not os.path.isfile(caminho):
        return ("Arquivo não encontrado.", 404)
    return send_file(caminho)


@viagem_bp.route("/paradas/<int:pid>/nao-realizada", methods=["POST"])
@permission_required(PERM)
def parada_nao_realizada(pid: int):
    parada = db.session.get(ViagemParada, pid)
    if not parada:
        return jsonify({"sucesso": False, "msg": "Parada não encontrada."}), 404
    p = request.get_json(silent=True) or {}
    motivo = str(p.get("motivo") or "").strip()
    if not motivo:
        return jsonify({"sucesso": False, "msg": "Motivo obrigatório."}), 400
    parada.status = "Nao_realizada"
    parada.resultado = str(p.get("resultado") or "Recusado").strip()
    parada.observacao = motivo
    if parada.solicitacao_id:
        sol = db.session.get(AgendamentoSolicitacao, parada.solicitacao_id)
        _solicitacao_volta_pendente(sol)
    _log_evento(parada.viagem_id, "OCORRENCIA",
                f"Parada NÃO realizada: {parada.parceiro_nome or parada.endereco}",
                descricao=motivo, parada_id=pid, severidade="warning")
    db.session.commit()
    return jsonify({"sucesso": True, "parada": _parada_dict(parada)})


@viagem_bp.route("/paradas/<int:pid>", methods=["DELETE"])
@permission_required(PERM)
def parada_excluir(pid: int):
    parada = db.session.get(ViagemParada, pid)
    if not parada:
        return jsonify({"sucesso": False, "msg": "Parada não encontrada."}), 404
    vid = parada.viagem_id
    v = db.session.get(Viagem, vid)
    if v and v.status in ("Concluida", "Cancelada"):
        return jsonify({"sucesso": False, "msg": "Viagem finalizada nao pode ser alterada."}), 400
    if v and v.status == "EmAndamento" and parada.status != "Pendente":
        return jsonify({"sucesso": False, "msg": "Em viagem iniciada, remova apenas paradas ainda pendentes."}), 400
    revogou = False
    solicitacao_id = parada.solicitacao_id
    db.session.delete(parada)
    if solicitacao_id and v and v.status in ("Planejada", "EmAndamento"):
        _solicitacao_volta_pendente(db.session.get(AgendamentoSolicitacao, solicitacao_id))
    # Se a viagem estava liberada para o motorista, revoga automaticamente
    if v and v.liberada and v.status == "Planejada":
        v.liberada = False
        v.liberada_em = None
        v.liberada_por = None
        revogou = True
        _log_evento(
            vid, "OBSERVACAO", "Liberação revogada automaticamente",
            descricao=f"Parada removida pelo gestor ({_user()}). Motorista notificado a aguardar nova liberação.",
            severidade="warning",
        )
    db.session.commit()
    return jsonify({"sucesso": True, "viagem_id": vid, "liberacao_revogada": revogou})


@viagem_bp.route("/paradas/<int:pid>/realocar", methods=["POST"])
@permission_required(PERM)
def parada_realocar(pid: int):
    parada = db.session.get(ViagemParada, pid)
    if not parada:
        return jsonify({"sucesso": False, "msg": "Parada não encontrada."}), 404
    if not parada.solicitacao_id:
        return jsonify({"sucesso": False, "msg": "Somente paradas vinculadas a solicitação podem ser realocadas."}), 409

    origem = db.session.get(Viagem, parada.viagem_id)
    if not origem:
        return jsonify({"sucesso": False, "msg": "Viagem de origem não encontrada."}), 404
    if origem.status in {"Concluida", "Cancelada"}:
        return jsonify({"sucesso": False, "msg": "Viagem finalizada não pode ser alterada."}), 409

    if parada.status != "Pendente":
        return jsonify({"sucesso": False, "msg": "Só é possível realocar paradas pendentes."}), 409

    payload = request.get_json(silent=True) or {}
    destino_id = _parse_int(payload.get("viagem_destino_id"))
    if not destino_id:
        return jsonify({"sucesso": False, "msg": "Informe a viagem de destino."}), 400

    destino = db.session.get(Viagem, destino_id)
    if not destino:
        return jsonify({"sucesso": False, "msg": "Viagem de destino não encontrada."}), 404
    if destino.status in {"Concluida", "Cancelada"}:
        return jsonify({"sucesso": False, "msg": "Viagem de destino finalizada não pode receber paradas."}), 409
    if int(destino.id) == int(origem.id):
        return jsonify({"sucesso": True, "msg": "Parada já está na viagem informada.", "viagem_id": destino.id})

    if destino.status != "Planejada":
        return jsonify({"sucesso": False, "msg": "Realocação permitida apenas para viagens de destino Planejadas."}), 409

    sol = db.session.get(AgendamentoSolicitacao, parada.solicitacao_id)
    if not sol:
        return jsonify({"sucesso": False, "msg": "Solicitação da parada não encontrada."}), 404
    bloqueio_avulsa = _validar_aprovacao_avulsa(sol)
    if bloqueio_avulsa:
        return jsonify({"sucesso": False, "msg": bloqueio_avulsa}), 409

    if sol.veiculo_id and destino.veiculo_id and int(sol.veiculo_id) != int(destino.veiculo_id):
        return jsonify({"sucesso": False, "msg": "Solicitação está em veículo diferente da viagem de destino. Realoque antes de mover."}), 409
    if sol.motorista_id and destino.motorista_id and int(sol.motorista_id) != int(destino.motorista_id):
        return jsonify({"sucesso": False, "msg": "Solicitação está com motorista diferente da viagem de destino. Realoque antes de mover."}), 409

    duplicada = ViagemParada.query.filter(
        ViagemParada.viagem_id == destino.id,
        ViagemParada.solicitacao_id == parada.solicitacao_id,
        ViagemParada.id != parada.id,
    ).first()
    if duplicada:
        return jsonify({"sucesso": False, "msg": "Solicitação já existe na viagem de destino."}), 409

    liberacao_revogada_origem = False
    liberacao_revogada_destino = False

    origem_id = origem.id
    origem_codigo = origem.codigo or f"#{origem.id}"
    destino_codigo = destino.codigo or f"#{destino.id}"
    solicitacao_label = sol.codigo or sol.id

    paradas_origem = ViagemParada.query.filter_by(viagem_id=origem.id).order_by(ViagemParada.sequencia.asc()).all()
    for seq, p in enumerate([p for p in paradas_origem if p.id != parada.id], start=1):
        p.sequencia = seq

    proxima_seq_destino = (db.session.query(func.max(ViagemParada.sequencia)).filter(ViagemParada.viagem_id == destino.id).scalar() or 0) + 1
    parada.viagem_id = destino.id
    parada.sequencia = proxima_seq_destino

    _sync_solicitacao_viagem(sol, destino, "EmRota" if destino.status == "EmAndamento" else "Alocada")
    _atualizar_tipo_viagem_por_paradas(origem)
    _atualizar_tipo_viagem_por_paradas(destino)

    if origem.liberada and origem.status == "Planejada":
        origem.liberada = False
        origem.liberada_em = None
        origem.liberada_por = None
        liberacao_revogada_origem = True

    if destino.liberada and destino.status == "Planejada":
        destino.liberada = False
        destino.liberada_em = None
        destino.liberada_por = None
        liberacao_revogada_destino = True

    origem.atualizado_em = datetime.now()
    destino.atualizado_em = datetime.now()

    _log_evento(
        origem.id,
        "OBSERVACAO",
        "Parada realocada para outra viagem",
        descricao=f"Solicitação {solicitacao_label} movida para {destino_codigo} por {_user()}.",
        parada_id=pid,
        severidade="warning",
    )
    _log_evento(
        destino.id,
        "PARADA_EXTRA",
        "Parada realocada de outra viagem",
        descricao=f"Solicitação {solicitacao_label} movida de {origem_codigo} por {_user()}.",
        parada_id=pid,
        severidade="info",
    )
    if liberacao_revogada_origem:
        _log_evento(
            origem.id,
            "OBSERVACAO",
            "Liberação revogada automaticamente",
            descricao="A viagem foi alterada e precisa ser liberada novamente para o motorista.",
            severidade="warning",
        )
    if liberacao_revogada_destino:
        _log_evento(
            destino.id,
            "OBSERVACAO",
            "Liberação revogada automaticamente",
            descricao="A viagem foi alterada e precisa ser liberada novamente para o motorista.",
            severidade="warning",
        )

    db.session.commit()
    return jsonify({
        "sucesso": True,
        "msg": "Parada realocada com sucesso.",
        "parada_id": pid,
        "viagem_origem_id": origem_id,
        "viagem_destino_id": destino.id,
        "liberacao_revogada_origem": liberacao_revogada_origem,
        "liberacao_revogada_destino": liberacao_revogada_destino,
    })


# --------------------------------------------------------------------------- LIBERAR / REVOGAR VIAGEM PARA O MOTORISTA
@viagem_bp.route("/<int:vid>/liberar", methods=["POST"])
@permission_required(PERM)
def liberar_viagem(vid: int):
    v = db.session.get(Viagem, vid)
    if not v:
        return jsonify({"sucesso": False, "msg": "Viagem não encontrada."}), 404
    if v.status in ("Concluida", "Cancelada"):
        return jsonify({"sucesso": False, "msg": "Viagem já encerrada."}), 400
    if not v.motorista_id:
        return jsonify({"sucesso": False, "msg": "Aloque um motorista antes de liberar."}), 400
    paradas_regs = ViagemParada.query.filter_by(viagem_id=vid).order_by(ViagemParada.sequencia).all()
    paradas = len(paradas_regs)
    checklist = _checklist_liberacao(v, paradas_regs)
    if not checklist["ok"]:
        return jsonify({
            "sucesso": False,
            "msg": "Corrija o checklist antes de liberar: " + " ".join(checklist["bloqueios"]),
            "checklist": checklist,
        }), 400
    body = request.get_json(silent=True) or {}
    destino_unico = bool(body.get("destino_unico")) if paradas == 1 else False
    v.liberada = True
    v.liberada_em = datetime.now()
    v.liberada_por = _user()
    v.destino_unico = destino_unico
    _log_evento(
        vid, "OBSERVACAO",
        "Viagem liberada para motorista" + (" (destino único)" if destino_unico else ""),
        descricao=f"{paradas} parada(s). Gestor: {_user()}.",
        severidade="info",
    )
    db.session.commit()
    return jsonify({"sucesso": True, "viagem": _viagem_dict(v)})


@viagem_bp.route("/<int:vid>/revogar-liberacao", methods=["POST"])
@permission_required(PERM)
def revogar_liberacao_viagem(vid: int):
    v = db.session.get(Viagem, vid)
    if not v:
        return jsonify({"sucesso": False, "msg": "Viagem não encontrada."}), 404
    if not v.liberada:
        return jsonify({"sucesso": False, "msg": "Viagem não está liberada."}), 400
    v.liberada = False
    v.liberada_em = None
    v.liberada_por = None
    _log_evento(
        vid, "OBSERVACAO", "Liberação revogada",
        descricao=f"Gestor {_user()} revogou a liberação. Motorista não verá mais a viagem até nova liberação.",
        severidade="warning",
    )
    db.session.commit()
    return jsonify({"sucesso": True, "viagem": _viagem_dict(v)})


# --------------------------------------------------------------------------- FLUXO UNIFICADO: a partir de uma SOLICITAÇÃO
def _parada_dict_from_solicitacao(sid: int, sequencia: int = 1):
    s = db.session.get(AgendamentoSolicitacao, sid)
    if not s:
        return None
    endereco = ", ".join(filter(None, [s.logradouro, s.numero, s.bairro]))
    tipo = s.tipo.upper() if s.tipo else "ENTREGA"
    return ViagemParada(
        sequencia=sequencia,
        solicitacao_id=s.id,
        tipo=tipo,
        parceiro_nome=s.parceiro_nome,
        endereco=endereco or None,
        cidade=s.cidade,
        uf=s.uf,
        latitude=s.destino_latitude,
        longitude=s.destino_longitude,
    )


@viagem_bp.route("/nova-de-solicitacao/<int:sid>", methods=["POST"])
@permission_required(PERM)
def nova_viagem_de_solicitacao(sid: int):
    sol = db.session.get(AgendamentoSolicitacao, sid)
    if not sol:
        return jsonify({"sucesso": False, "msg": "Solicitação não encontrada."}), 404
    if str(sol.status or "").strip() in {"Concluida", "Cancelada"}:
        return jsonify({"sucesso": False, "msg": "Solicitação finalizada não pode virar viagem."}), 409
    bloqueio_avulsa = _validar_aprovacao_avulsa(sol)
    if bloqueio_avulsa:
        return jsonify({"sucesso": False, "msg": bloqueio_avulsa}), 409
    if not sol.veiculo_id:
        return jsonify({"sucesso": False, "msg": "Alocar veículo na solicitação antes de montar a viagem."}), 400

    conflito_em_aberta = (
        db.session.query(ViagemParada.id, Viagem.id, Viagem.codigo)
        .join(Viagem, Viagem.id == ViagemParada.viagem_id)
        .filter(ViagemParada.solicitacao_id == sid)
        .filter(Viagem.status != "Cancelada")
        .first()
    )
    if conflito_em_aberta:
        _, vid_exist, cod_exist = conflito_em_aberta
        return jsonify(
            {
                "sucesso": False,
                "msg": f"Solicitação já está vinculada à viagem {cod_exist or ('#' + str(vid_exist))}.",
                "viagem_id": vid_exist,
            }
        ), 409

    conflito = _validar_conflito_recurso(
        veiculo_id=sol.veiculo_id,
        motorista_id=sol.motorista_id,
        saida=sol.data_hora_saida_prevista,
        retorno=sol.data_hora_retorno_prevista,
    )
    if conflito:
        return jsonify({"sucesso": False, "msg": conflito}), 409

    motorista = AgendamentoMotorista.query.get(sol.motorista_id) if sol.motorista_id else None
    viagem = Viagem(
        codigo=_proximo_codigo(),
        veiculo_id=sol.veiculo_id,
        motorista_id=sol.motorista_id,
        motorista_nome=sol.motorista_nome or (motorista.nome if motorista else None),
        tipo=(str(sol.tipo or "").upper().strip() or "MISTA"),
        status="Planejada",
        titulo=f"Rota {str(sol.codigo or ('#' + str(sol.id))).strip()}",
        saida_prevista=sol.data_hora_saida_prevista,
        retorno_previsto=sol.data_hora_retorno_prevista,
        origem_label=str(sol.departamento_solicitante or "Logística").strip() or "Logística",
        criado_por=_user(),
        criado_em=datetime.now(),
        atualizado_em=datetime.now(),
    )
    db.session.add(viagem)
    db.session.flush()

    parada = _parada_dict_from_solicitacao(sol.id, sequencia=1)
    if not parada:
        db.session.rollback()
        return jsonify({"sucesso": False, "msg": "Não foi possível gerar a parada da solicitação."}), 409
    parada.viagem_id = viagem.id
    db.session.add(parada)

    _sync_solicitacao_viagem(sol, viagem, "Alocada")
    _log_evento(
        viagem.id,
        "OBSERVACAO",
        "Viagem criada a partir da Central",
        descricao=f"Solicitação {sol.codigo or sol.id} vinculada na criação.",
        severidade="info",
    )
    db.session.commit()
    return jsonify({"sucesso": True, "viagem": _viagem_dict(viagem, detalhada=True)})


@viagem_bp.route("/nova-avulsa-de-solicitacao/<int:sid>", methods=["POST"])
@permission_required(PERM)
def nova_viagem_avulsa_de_solicitacao(sid: int):
    sol = db.session.get(AgendamentoSolicitacao, sid)
    if not sol:
        return jsonify({"sucesso": False, "msg": "Solicitação não encontrada."}), 404
    if str(sol.tipo or "").strip().upper() != "AVULSA":
        return jsonify({"sucesso": False, "msg": "Esta ação é exclusiva para solicitação AVULSA."}), 409
    if str(sol.status or "").strip() in {"Concluida", "Cancelada"}:
        return jsonify({"sucesso": False, "msg": "Solicitação finalizada não pode virar viagem."}), 409
    bloqueio_avulsa = _validar_aprovacao_avulsa(sol)
    if bloqueio_avulsa:
        return jsonify({"sucesso": False, "msg": bloqueio_avulsa}), 409
    if not sol.veiculo_id:
        return jsonify({"sucesso": False, "msg": "Alocar veículo na solicitação antes de montar a viagem avulsa."}), 400

    motorista = AgendamentoMotorista.query.get(sol.motorista_id) if sol.motorista_id else None
    viagem = Viagem(
        codigo=_proximo_codigo(),
        veiculo_id=sol.veiculo_id,
        motorista_id=sol.motorista_id,
        motorista_nome=sol.motorista_nome or (motorista.nome if motorista else None),
        tipo="ALEATORIA",
        status="Planejada",
        titulo=f"Viagem avulsa {str(sol.codigo or ('#' + str(sol.id))).strip()}",
        saida_prevista=sol.data_hora_saida_prevista,
        retorno_previsto=sol.data_hora_retorno_prevista,
        origem_label=str(sol.departamento_solicitante or "Logística").strip() or "Logística",
        observacao=str(sol.observacoes_solicitante or "").strip() or "Viagem avulsa criada a partir de solicitação.",
        criado_por=_user(),
        criado_em=datetime.now(),
        atualizado_em=datetime.now(),
        avulsa=True,
        funcionario_responsavel=(str(sol.solicitante or "").strip() or str(sol.parceiro_nome or "").strip() or _user())[:160],
    )
    db.session.add(viagem)
    db.session.flush()

    _sync_solicitacao_viagem(sol, viagem, "Alocada")
    _log_evento(
        viagem.id,
        "OBSERVACAO",
        "Viagem avulsa criada a partir de solicitação",
        descricao=f"Solicitação {sol.codigo or sol.id} aprovada e convertida em viagem avulsa.",
        severidade="info",
    )
    db.session.commit()
    return jsonify({"sucesso": True, "viagem": _viagem_dict(viagem, detalhada=True)})


@viagem_bp.route("/nova-avulsa-manual", methods=["POST"])
@permission_required(PERM)
def nova_viagem_avulsa_manual():
    payload = request.get_json(silent=True) or {}

    veiculo_id = _parse_int(payload.get("veiculo_id"))
    if not veiculo_id:
        return jsonify({"sucesso": False, "msg": "Selecione um veículo para a viagem avulsa."}), 400
    veiculo = AgendamentoVeiculo.query.get(veiculo_id)
    if not veiculo:
        return jsonify({"sucesso": False, "msg": "Veículo não encontrado."}), 404

    funcionario = str(payload.get("funcionario_responsavel") or "").strip()
    if not funcionario:
        return jsonify({"sucesso": False, "msg": "Informe o funcionário responsável."}), 400

    saida = _parse_dt(payload.get("saida_prevista"))
    if not saida:
        return jsonify({"sucesso": False, "msg": "Informe a saída prevista da viagem avulsa."}), 400

    retorno = _parse_dt(payload.get("retorno_previsto"))
    if retorno and retorno <= saida:
        return jsonify({"sucesso": False, "msg": "Retorno previsto deve ser maior que a saída."}), 400
    if not retorno:
        retorno = saida + timedelta(minutes=int(current_app.config.get("VIAGEM_DURACAO_PADRAO_MINUTOS", 180)))

    motorista_id = _parse_int(payload.get("motorista_id"))
    motorista = AgendamentoMotorista.query.get(motorista_id) if motorista_id else None

    conflito = _validar_conflito_recurso(
        veiculo_id=veiculo.id,
        motorista_id=(motorista.id if motorista else None),
        saida=saida,
        retorno=retorno,
    )
    if conflito:
        return jsonify({"sucesso": False, "msg": conflito}), 409

    observacao = str(payload.get("observacao") or "").strip()
    origem_label = str(payload.get("origem_label") or "Logística").strip() or "Logística"

    viagem = Viagem(
        codigo=_proximo_codigo(),
        veiculo_id=veiculo.id,
        motorista_id=(motorista.id if motorista else None),
        motorista_nome=(motorista.nome if motorista else None),
        tipo="ALEATORIA",
        status="Planejada",
        titulo=f"Viagem avulsa {funcionario}",
        observacao=observacao or "Viagem avulsa criada na Central de Viagens.",
        saida_prevista=saida,
        retorno_previsto=retorno,
        origem_label=origem_label,
        criado_por=_user(),
        criado_em=datetime.now(),
        atualizado_em=datetime.now(),
        avulsa=True,
        funcionario_responsavel=funcionario[:160],
    )
    db.session.add(viagem)
    db.session.flush()

    _log_evento(
        viagem.id,
        "OBSERVACAO",
        "Viagem avulsa criada na Central",
        descricao=f"Responsável: {funcionario}.",
        severidade="info",
    )

    db.session.commit()
    return jsonify({"sucesso": True, "viagem": _viagem_dict(viagem, detalhada=True)})


@viagem_bp.route("/<int:vid>/anexar-solicitacao/<int:sid>", methods=["POST"])
@permission_required(PERM)
def anexar_solicitacao_viagem(vid: int, sid: int):
    v = db.session.get(Viagem, vid)
    if not v:
        return jsonify({"sucesso": False, "msg": "Viagem não encontrada."}), 404
    if v.status in {"Concluida", "Cancelada"}:
        return jsonify({"sucesso": False, "msg": "Viagem finalizada não pode receber novas paradas."}), 409

    sol = db.session.get(AgendamentoSolicitacao, sid)
    if not sol:
        return jsonify({"sucesso": False, "msg": "Solicitação não encontrada."}), 404
    if str(sol.status or "").strip() in {"Concluida", "Cancelada"}:
        return jsonify({"sucesso": False, "msg": "Solicitação finalizada não pode ser anexada."}), 409
    bloqueio_avulsa = _validar_aprovacao_avulsa(sol)
    if bloqueio_avulsa:
        return jsonify({"sucesso": False, "msg": bloqueio_avulsa}), 409

    vinculo = (
        db.session.query(ViagemParada.id, ViagemParada.viagem_id, Viagem.codigo)
        .join(Viagem, Viagem.id == ViagemParada.viagem_id)
        .filter(ViagemParada.solicitacao_id == sid)
        .filter(Viagem.status != "Cancelada")
        .first()
    )
    if vinculo:
        _, viagem_atual_id, viagem_atual_codigo = vinculo
        if int(viagem_atual_id) == int(v.id):
            return jsonify({"sucesso": True, "msg": "Solicitação já estava nesta viagem.", "viagem": _viagem_dict(v, detalhada=True)})
        return jsonify(
            {
                "sucesso": False,
                "msg": f"Solicitação já está vinculada à viagem {viagem_atual_codigo or ('#' + str(viagem_atual_id))}.",
                "viagem_id": viagem_atual_id,
            }
        ), 409

    if sol.veiculo_id and int(sol.veiculo_id) != int(v.veiculo_id):
        return jsonify({"sucesso": False, "msg": "Solicitação está alocada em outro veículo. Realoque antes de anexar."}), 409
    if sol.motorista_id and v.motorista_id and int(sol.motorista_id) != int(v.motorista_id):
        return jsonify({"sucesso": False, "msg": "Solicitação está alocada para outro motorista. Realoque antes de anexar."}), 409

    if not v.motorista_id and sol.motorista_id:
        v.motorista_id = sol.motorista_id
        v.motorista_nome = sol.motorista_nome
    if not v.saida_prevista and sol.data_hora_saida_prevista:
        v.saida_prevista = sol.data_hora_saida_prevista
    if not v.retorno_previsto and sol.data_hora_retorno_prevista:
        v.retorno_previsto = sol.data_hora_retorno_prevista

    tipos_existentes = {
        str(p.tipo or "").upper().strip()
        for p in ViagemParada.query.filter_by(viagem_id=v.id).all()
        if str(p.tipo or "").strip()
    }
    tipo_sol = str(sol.tipo or "").upper().strip()
    if tipo_sol:
        tipos_existentes.add(tipo_sol)
    tipos_validos = {t for t in tipos_existentes if t in {"COLETA", "ENTREGA"}}
    if len(tipos_validos) >= 2:
        v.tipo = "MISTA"
    elif len(tipos_validos) == 1:
        v.tipo = list(tipos_validos)[0]

    seq = (db.session.query(func.max(ViagemParada.sequencia)).filter(ViagemParada.viagem_id == v.id).scalar() or 0) + 1
    parada = _parada_dict_from_solicitacao(sol.id, sequencia=seq)
    if not parada:
        return jsonify({"sucesso": False, "msg": "Não foi possível gerar a parada da solicitação."}), 409
    parada.viagem_id = v.id
    db.session.add(parada)

    _sync_solicitacao_viagem(sol, v, "EmRota" if v.status == "EmAndamento" else "Alocada")

    liberacao_revogada = False
    if v.liberada and v.status == "Planejada":
        v.liberada = False
        v.liberada_em = None
        v.liberada_por = None
        liberacao_revogada = True

    v.atualizado_em = datetime.now()
    _log_evento(
        v.id,
        "PARADA_EXTRA",
        "Solicitação anexada à viagem",
        descricao=f"Solicitação {sol.codigo or sol.id} anexada pelo gestor {_user()}.",
        parada_id=getattr(parada, "id", None),
        severidade="info",
    )
    if liberacao_revogada:
        _log_evento(
            v.id,
            "OBSERVACAO",
            "Liberação revogada automaticamente",
            descricao="A viagem foi alterada e precisa ser liberada novamente para o motorista.",
            severidade="warning",
        )

    db.session.commit()
    return jsonify({"sucesso": True, "viagem": _viagem_dict(v, detalhada=True), "liberacao_revogada": liberacao_revogada})


@viagem_bp.route("/montar-com-solicitacoes", methods=["POST"])
@permission_required(PERM)
def montar_viagem_com_solicitacoes():
    payload = request.get_json(silent=True) or {}
    ids_raw = payload.get("ids") if isinstance(payload.get("ids"), list) else []
    ids: list[int] = []
    for item in ids_raw:
        sid = _parse_int(item)
        if sid and sid > 0 and sid not in ids:
            ids.append(sid)
    if not ids:
        return jsonify({"sucesso": False, "msg": "Informe as solicitações para montar a viagem."}), 400

    modo = str(payload.get("modo") or "nova").strip().lower()
    if modo not in {"nova", "anexar"}:
        return jsonify({"sucesso": False, "msg": "Modo inválido. Use 'nova' ou 'anexar'."}), 400

    rows = AgendamentoSolicitacao.query.filter(AgendamentoSolicitacao.id.in_(ids)).all()
    por_id = {int(r.id): r for r in rows}
    missing = [sid for sid in ids if sid not in por_id]
    if missing:
        return jsonify({"sucesso": False, "msg": f"Solicitações não encontradas: {', '.join(str(x) for x in missing)}."}), 404

    solicitacoes = [por_id[sid] for sid in ids]
    for sol in solicitacoes:
        if str(sol.status or "").strip() in {"Concluida", "Cancelada"}:
            return jsonify({"sucesso": False, "msg": f"Solicitação {sol.codigo or sol.id} já está finalizada."}), 409
        bloqueio_avulsa = _validar_aprovacao_avulsa(sol)
        if bloqueio_avulsa:
            return jsonify({"sucesso": False, "msg": f"Solicitação {sol.codigo or sol.id}: {bloqueio_avulsa}"}), 409
        if not sol.veiculo_id or not sol.motorista_id:
            return jsonify({"sucesso": False, "msg": f"Defina veículo e motorista da solicitação {sol.codigo or sol.id} antes de montar viagem."}), 409

    veiculos = {int(sol.veiculo_id) for sol in solicitacoes if sol.veiculo_id}
    motoristas = {int(sol.motorista_id) for sol in solicitacoes if sol.motorista_id}
    if len(veiculos) != 1 or len(motoristas) != 1:
        return jsonify({"sucesso": False, "msg": "Todas as solicitações precisam estar alocadas no mesmo veículo e motorista."}), 409

    viagem_alvo = None
    if modo == "anexar":
        viagem_id = _parse_int(payload.get("viagem_id"))
        if not viagem_id:
            return jsonify({"sucesso": False, "msg": "Informe a viagem para anexar."}), 400
        viagem_alvo = db.session.get(Viagem, viagem_id)
        if not viagem_alvo:
            return jsonify({"sucesso": False, "msg": "Viagem não encontrada."}), 404
        if viagem_alvo.status in {"Concluida", "Cancelada"}:
            return jsonify({"sucesso": False, "msg": "Viagem finalizada não pode receber novas paradas."}), 409

    vinculos = (
        db.session.query(ViagemParada.solicitacao_id, ViagemParada.viagem_id, Viagem.codigo)
        .join(Viagem, Viagem.id == ViagemParada.viagem_id)
        .filter(ViagemParada.solicitacao_id.in_(ids))
        .filter(Viagem.status != "Cancelada")
        .all()
    )
    vinculo_por_solicitacao = {int(sid): (int(vid), str(cod or "").strip()) for sid, vid, cod in vinculos}

    if modo == "nova" and vinculo_por_solicitacao:
        sid_conf = next(iter(vinculo_por_solicitacao.keys()))
        vid_conf, cod_conf = vinculo_por_solicitacao[sid_conf]
        return jsonify({
            "sucesso": False,
            "msg": f"Solicitação já vinculada à viagem {cod_conf or ('#' + str(vid_conf))}.",
            "viagem_id": vid_conf,
        }), 409

    if modo == "anexar" and viagem_alvo:
        for sid, (vid_conf, cod_conf) in vinculo_por_solicitacao.items():
            if int(vid_conf) != int(viagem_alvo.id):
                return jsonify({
                    "sucesso": False,
                    "msg": f"Solicitação já vinculada à viagem {cod_conf or ('#' + str(vid_conf))}.",
                    "viagem_id": vid_conf,
                }), 409

    try:
        if modo == "nova":
            base = solicitacoes[0]
            conflito = _validar_conflito_recurso(
                veiculo_id=base.veiculo_id,
                motorista_id=base.motorista_id,
                saida=base.data_hora_saida_prevista,
                retorno=base.data_hora_retorno_prevista,
            )
            if conflito:
                return jsonify({"sucesso": False, "msg": conflito}), 409

            motorista = AgendamentoMotorista.query.get(base.motorista_id) if base.motorista_id else None
            viagem_alvo = Viagem(
                codigo=_proximo_codigo(),
                veiculo_id=base.veiculo_id,
                motorista_id=base.motorista_id,
                motorista_nome=base.motorista_nome or (motorista.nome if motorista else None),
                tipo=(str(base.tipo or "").upper().strip() or "MISTA"),
                status="Planejada",
                titulo=f"Rota {str(base.codigo or ('#' + str(base.id))).strip()}",
                saida_prevista=base.data_hora_saida_prevista,
                retorno_previsto=base.data_hora_retorno_prevista,
                origem_label=str(base.departamento_solicitante or "Logística").strip() or "Logística",
                criado_por=_user(),
                criado_em=datetime.now(),
                atualizado_em=datetime.now(),
            )
            db.session.add(viagem_alvo)
            db.session.flush()

        if not viagem_alvo:
            return jsonify({"sucesso": False, "msg": "Não foi possível determinar a viagem alvo."}), 400

        if int(viagem_alvo.veiculo_id or 0) != int(next(iter(veiculos))):
            return jsonify({"sucesso": False, "msg": "Solicitações estão em veículo diferente da viagem selecionada."}), 409
        if int(viagem_alvo.motorista_id or 0) != int(next(iter(motoristas))):
            return jsonify({"sucesso": False, "msg": "Solicitações estão com motorista diferente da viagem selecionada."}), 409

        seq = (db.session.query(func.max(ViagemParada.sequencia)).filter(ViagemParada.viagem_id == viagem_alvo.id).scalar() or 0) + 1
        tipos_existentes = {
            str(p.tipo or "").upper().strip()
            for p in ViagemParada.query.filter_by(viagem_id=viagem_alvo.id).all()
            if str(p.tipo or "").strip()
        }

        anexadas = 0
        for sol in solicitacoes:
            # Quando ja estiver anexada na mesma viagem, apenas sincroniza e segue.
            vinculo_mesma = vinculo_por_solicitacao.get(int(sol.id))
            if vinculo_mesma and int(vinculo_mesma[0]) == int(viagem_alvo.id):
                _sync_solicitacao_viagem(sol, viagem_alvo, "EmRota" if viagem_alvo.status == "EmAndamento" else "Alocada")
                continue

            parada = _parada_dict_from_solicitacao(sol.id, sequencia=seq)
            if not parada:
                db.session.rollback()
                return jsonify({"sucesso": False, "msg": f"Não foi possível gerar parada para a solicitação {sol.codigo or sol.id}."}), 409
            parada.viagem_id = viagem_alvo.id
            db.session.add(parada)
            seq += 1
            tipos_existentes.add(str(sol.tipo or "").upper().strip())

            _sync_solicitacao_viagem(sol, viagem_alvo, "EmRota" if viagem_alvo.status == "EmAndamento" else "Alocada")
            anexadas += 1

        tipos_validos = {t for t in tipos_existentes if t in {"COLETA", "ENTREGA"}}
        if len(tipos_validos) >= 2:
            viagem_alvo.tipo = "MISTA"
        elif len(tipos_validos) == 1:
            viagem_alvo.tipo = list(tipos_validos)[0]

        liberacao_revogada = False
        if viagem_alvo.liberada and viagem_alvo.status == "Planejada":
            viagem_alvo.liberada = False
            viagem_alvo.liberada_em = None
            viagem_alvo.liberada_por = None
            liberacao_revogada = True

        viagem_alvo.atualizado_em = datetime.now()
        _log_evento(
            viagem_alvo.id,
            "PARADA_EXTRA",
            "Solicitações montadas em lote",
            descricao=f"{anexadas} solicitação(ões) montadas pelo gestor {_user()}.",
            severidade="info",
        )
        if liberacao_revogada:
            _log_evento(
                viagem_alvo.id,
                "OBSERVACAO",
                "Liberação revogada automaticamente",
                descricao="A viagem foi alterada e precisa ser liberada novamente para o motorista.",
                severidade="warning",
            )

        db.session.commit()
        return jsonify({
            "sucesso": True,
            "viagem": _viagem_dict(viagem_alvo, detalhada=True),
            "anexadas": anexadas,
            "liberacao_revogada": liberacao_revogada,
        })
    except Exception:
        db.session.rollback()
        raise


@viagem_bp.route("/planejadas-do-motorista/<int:mid>", methods=["GET"])
@permission_required(PERM)
def viagens_planejadas_do_motorista(mid: int):
    """Lista viagens Planejadas do motorista — usado para 'anexar solicitação a viagem existente'."""
    rows = (
        Viagem.query
        .filter(Viagem.motorista_id == mid, Viagem.status == "Planejada")
        .order_by(Viagem.saida_prevista.asc())
        .all()
    )
    return jsonify({"viagens": [_viagem_dict(v) for v in rows]})


# --------------------------------------------------------------------------- VIAGENS DO MOTORISTA (novo endpoint substituindo legacy)
@viagem_bp.route("/motorista/minhas", methods=["GET"])
def motorista_minhas_viagens():
    """Retorna viagens LIBERADAS do motorista logado (via usuario_username).

    Substitui o endpoint legacy /api/logistica/motorista/minhas-viagens que usava
    AgendamentoSolicitacao isoladas. Agora entrega agrupado por Viagem multi-parada.
    """
    from flask import session
    username = session.get("username", "")
    if not username:
        return jsonify({"viagens": [], "motorista": None, "erro": "Não autenticado."}), 401
    mot = AgendamentoMotorista.query.filter_by(usuario_username=username).first()
    if not mot:
        return jsonify({"viagens": [], "motorista": None})
    rows = (
        Viagem.query
        .filter(
            Viagem.motorista_id == mot.id,
            Viagem.status.in_(["Planejada", "EmAndamento"]),
        )
        .order_by(Viagem.saida_prevista.asc())
        .all()
    )
    # Também viagens já concluídas recentes do motorista (últimas 20)
    concluidas = (
        Viagem.query
        .filter(
            Viagem.motorista_id == mot.id,
            Viagem.status == "Concluida",
        )
        .order_by(Viagem.retorno_real.desc())
        .limit(20)
        .all()
    )
    return jsonify({
        "motorista": {"id": mot.id, "nome": mot.nome, "usuario": mot.usuario_username},
        "viagens": [_viagem_dict(v, detalhada=True) for v in rows],
        "historico": [_viagem_dict(v) for v in concluidas],
    })


# --------------------------------------------------------------------------- LINK DO PAINEL DO MOTORISTA (gestor)
@viagem_bp.route("/motorista/<int:mid>/painel-link", methods=["GET"])
@permission_required(PERM)
def motorista_painel_link(mid: int):
    mot = db.session.get(AgendamentoMotorista, mid)
    if not mot:
        return jsonify({"sucesso": False, "msg": "Motorista não encontrado."}), 404
    token = _token_painel_motorista(mid)
    path = f"/motorista/painel/{mid}/{token}"
    link_externo = url_for("motorista_viagem.motorista_painel_publico", mid=mid, token=token, _external=True)
    texto_wa = (
        f"Olá {mot.nome}! Aqui está seu painel de viagens.\n"
        f"Abra este link no celular, toque em ⋮ e 'Adicionar à tela inicial' para virar um app:\n"
        f"{link_externo}"
    )
    wa_link = None
    if mot.telefone:
        # remove tudo que não é dígito
        fone = "".join(c for c in (mot.telefone or "") if c.isdigit())
        if fone:
            if not fone.startswith("55"):
                fone = "55" + fone
            from urllib.parse import quote
            wa_link = f"https://wa.me/{fone}?text={quote(texto_wa)}"
    qr_link = f"https://api.qrserver.com/v1/create-qr-code/?size=300x300&data={link_externo}"
    return jsonify({
        "sucesso": True,
        "motorista": {"id": mot.id, "nome": mot.nome, "telefone": mot.telefone},
        "path": path,
        "url": link_externo,
        "whatsapp": wa_link,
        "qr": qr_link,
        "texto_sugerido": texto_wa,
    })



@viagem_bp.route("/<int:vid>/posicao", methods=["POST"])
@permission_required(PERM)
def registrar_posicao(vid: int):
    v = db.session.get(Viagem, vid)
    if not v:
        return jsonify({"sucesso": False, "msg": "Viagem não encontrada."}), 404
    if v.status != "EmAndamento":
        return jsonify({"sucesso": False, "msg": "Viagem não está em andamento."}), 400
    p = request.get_json(silent=True) or {}
    lat = _parse_float(p.get("latitude"))
    lng = _parse_float(p.get("longitude"))
    if lat is None or lng is None:
        return jsonify({"sucesso": False, "msg": "Lat/Lng obrigatórios."}), 400
    pos = ViagemPosicao(
        viagem_id=vid,
        latitude=lat, longitude=lng,
        velocidade_kmh=_parse_float(p.get("velocidade_kmh")),
        rumo=_parse_float(p.get("rumo")),
        precisao_m=_parse_float(p.get("precisao_m")),
        bateria_pct=_parse_int(p.get("bateria_pct")),
        origem=str(p.get("origem") or "motorista_app").strip(),
    )
    db.session.add(pos)
    db.session.commit()
    return jsonify({"sucesso": True, "id": pos.id})


# --------------------------------------------------------------------------- EVENTOS / OCORRÊNCIAS
@viagem_bp.route("/<int:vid>/eventos", methods=["POST"])
@permission_required(PERM)
def registrar_evento(vid: int):
    v = db.session.get(Viagem, vid)
    if not v:
        return jsonify({"sucesso": False, "msg": "Viagem não encontrada."}), 404
    if request.content_type and "multipart" in request.content_type:
        p = request.form.to_dict()
        foto = _save_upload("foto")
    else:
        p = request.get_json(silent=True) or {}
        foto = None
    titulo = str(p.get("titulo") or "").strip()
    if not titulo:
        return jsonify({"sucesso": False, "msg": "Título obrigatório."}), 400
    tipo = str(p.get("tipo") or "OBSERVACAO").upper().strip()
    severidade = str(p.get("severidade") or "info").strip()
    e = ViagemEvento(
        viagem_id=vid,
        parada_id=_parse_int(p.get("parada_id")),
        tipo=tipo,
        titulo=titulo,
        descricao=str(p.get("descricao") or "").strip() or None,
        latitude=_parse_float(p.get("latitude")),
        longitude=_parse_float(p.get("longitude")),
        km=_parse_int(p.get("km")),
        foto_path=foto,
        severidade=severidade,
        registrado_por=_user(),
    )
    db.session.add(e)
    db.session.commit()
    return jsonify({"sucesso": True, "evento": _evento_dict(e)})


# --------------------------------------------------------------------------- AUXILIARES (cadastros + solicitações pendentes)
@viagem_bp.route("/auxiliares", methods=["GET"])
@permission_required(PERM)
def auxiliares():
    sincronizar_motoristas_usuarios(commit=True)
    veiculos = AgendamentoVeiculo.query.filter_by(ativo=True).order_by(AgendamentoVeiculo.ordem_exibicao).all()
    motoristas = AgendamentoMotorista.query.filter_by(ativo=True).order_by(AgendamentoMotorista.nome).all()
    sols = (
        AgendamentoSolicitacao.query
        .filter(AgendamentoSolicitacao.status.in_(["Pendente", "Alocada", "Aprovada"]))
        .filter(_filtro_solicitacao_visivel_viagem())
        .order_by(
            AgendamentoSolicitacao.prazo_limite.is_(None),
            AgendamentoSolicitacao.prazo_limite.asc(),
            AgendamentoSolicitacao.criado_em.desc(),
        )
        .limit(150).all()
    )
    ja_usadas = {
        r[0] for r in db.session.query(ViagemParada.solicitacao_id)
        .join(Viagem, Viagem.id == ViagemParada.viagem_id)
        .filter(ViagemParada.solicitacao_id.isnot(None))
        .filter(Viagem.status != "Cancelada")
        .all()
    }
    sols = [s for s in sols if s.id not in ja_usadas]
    return jsonify({
        "veiculos": [{"id": v.id, "label": _veiculo_label(v), "placa": v.placa} for v in veiculos],
        "motoristas": [{"id": m.id, "label": m.nome} for m in motoristas],
        "solicitacoes": [{
            "id": s.id,
            "codigo": s.codigo,
            "tipo": s.tipo,
            "status": s.status,
            "parceiro_nome": s.parceiro_nome,
            "cidade": s.cidade,
            "uf": s.uf,
            "prazo_limite": s.prazo_limite.isoformat() if s.prazo_limite else None,
            "documento_numero": s.documento_numero,
        } for s in sols],
    })


# --------------------------------------------------------------------------- ROTAS PLANEJADAS (integracao com Gestao de Rotas)
@viagem_bp.route("/rotas-planejadas", methods=["GET"])
@permission_required(PERM)
def rotas_planejadas():
    """Retorna veículos/data com solicitações alocadas (Gestão de Rotas) prontas para virar Viagem.

    Params:
      - data (YYYY-MM-DD) — default = hoje
      - veiculo_id (opcional) — filtra 1 veículo específico
    """
    data_str = (request.args.get("data") or "").strip()
    try:
        dia = datetime.strptime(data_str, "%Y-%m-%d").date() if data_str else date.today()
    except ValueError:
        dia = date.today()
    veiculo_id = _parse_int(request.args.get("veiculo_id"))

    ini = datetime.combine(dia, datetime.min.time())
    fim = ini + timedelta(days=1)

    q = (AgendamentoSolicitacao.query
         .filter(AgendamentoSolicitacao.veiculo_id.isnot(None))
         .filter(AgendamentoSolicitacao.status.in_(["Alocada", "Aprovada", "Pendente"]))
            .filter(_filtro_solicitacao_visivel_viagem())
         .filter(AgendamentoSolicitacao.data_hora_saida_prevista >= ini)
         .filter(AgendamentoSolicitacao.data_hora_saida_prevista < fim))
    if veiculo_id:
        q = q.filter(AgendamentoSolicitacao.veiculo_id == veiculo_id)

    # Solicitações já atreladas a uma Viagem não-cancelada devem ser excluídas
    ja_usadas = {
        r[0] for r in db.session.query(ViagemParada.solicitacao_id)
        .join(Viagem, Viagem.id == ViagemParada.viagem_id)
        .filter(ViagemParada.solicitacao_id.isnot(None))
        .filter(Viagem.status != "Cancelada")
        .all()
    }

    grupos: dict[int, dict] = {}
    for s in q.order_by(AgendamentoSolicitacao.data_hora_saida_prevista.asc()).all():
        if s.id in ja_usadas:
            continue
        vid = s.veiculo_id
        if vid not in grupos:
            veic = AgendamentoVeiculo.query.get(vid)
            mot = AgendamentoMotorista.query.get(s.motorista_id) if s.motorista_id else None
            grupos[vid] = {
                "veiculo_id": vid,
                "veiculo_label": _veiculo_label(veic),
                "placa": veic.placa if veic else None,
                "motorista_id": s.motorista_id,
                "motorista_nome": s.motorista_nome or (mot.nome if mot else None),
                "saida_prevista": s.data_hora_saida_prevista.isoformat() if s.data_hora_saida_prevista else None,
                "retorno_previsto": s.data_hora_retorno_prevista.isoformat() if s.data_hora_retorno_prevista else None,
                "solicitacoes": [],
            }
        grupos[vid]["solicitacoes"].append({
            "id": s.id,
            "codigo": s.codigo,
            "tipo": s.tipo,
            "status": s.status,
            "parceiro_nome": s.parceiro_nome,
            "cidade": s.cidade,
            "uf": s.uf,
            "previsao": s.data_hora_saida_prevista.isoformat() if s.data_hora_saida_prevista else None,
            "documento_numero": s.documento_numero,
        })

    return jsonify({"data": dia.isoformat(), "rotas": list(grupos.values())})


@viagem_bp.route("/importar-rota", methods=["POST"])
@permission_required(PERM)
def importar_rota():
    return jsonify({"sucesso": False, "msg": "Criação manual de viagens foi descontinuada. Use a Central de Viagens."}), 410


# --------------------------------------------------------------------------- OTIMIZADOR (Nearest Neighbor) - uso interno + endpoint
def _otimizar_paradas_viagem(vid: int) -> dict:
    """Reordena paradas pendentes por proximidade (Nearest Neighbor).

    Retorna dict com chaves: ok(bool), msg, paradas_ordenadas, distancia_km, motivo(se ok=False).
    NÃO faz commit — chamador decide.
    """
    v = db.session.get(Viagem, vid)
    if not v:
        return {"ok": False, "msg": "Viagem não encontrada.", "motivo": "nao_encontrada"}

    todas = ViagemParada.query.filter_by(viagem_id=vid).order_by(ViagemParada.sequencia).all()
    if len(todas) < 2:
        return {"ok": False, "msg": "Menos de 2 paradas.", "motivo": "insuficiente"}

    concluidas = [p for p in todas if p.status in ("Concluida", "Nao_realizada", "Cancelada")]
    pendentes = [p for p in todas if p.status in ("Pendente", "EmAndamento")]
    if not pendentes:
        return {"ok": False, "msg": "Nenhuma parada pendente.", "motivo": "sem_pendentes"}

    sem_coord = [p for p in pendentes if p.latitude is None or p.longitude is None]
    if sem_coord:
        _geocodificar_paradas_sem_coord(sem_coord)
        sem_coord = [p for p in pendentes if p.latitude is None or p.longitude is None]
    if sem_coord:
        nomes = ", ".join([p.parceiro_nome or f"Parada #{p.id}" for p in sem_coord[:3]])
        return {
            "ok": False,
            "msg": f"{len(sem_coord)} parada(s) sem coordenadas: {nomes}.",
            "motivo": "sem_coordenadas",
        }

    if concluidas and concluidas[-1].latitude is not None:
        ref_lat, ref_lng = concluidas[-1].latitude, concluidas[-1].longitude
    elif v.origem_lat is not None and v.origem_lng is not None:
        ref_lat, ref_lng = v.origem_lat, v.origem_lng
    else:
        ref_lat, ref_lng = pendentes[0].latitude, pendentes[0].longitude

    restantes = list(pendentes)
    janelas = {p.id: _janela_atendimento_parada(p) for p in restantes}
    ordenadas: list[ViagemParada] = []
    total_km = 0.0
    cur_lat, cur_lng = ref_lat, ref_lng
    while restantes:
        restantes.sort(key=lambda p: (janelas.get(p.id) is None, janelas.get(p.id) or 9999, _haversine_km(cur_lat, cur_lng, p.latitude, p.longitude)))
        prox = restantes.pop(0)
        total_km += _haversine_km(cur_lat, cur_lng, prox.latitude, prox.longitude)
        ordenadas.append(prox)
        cur_lat, cur_lng = prox.latitude, prox.longitude

    seq = 1
    for p in concluidas:
        p.sequencia = seq
        seq += 1
    for p in ordenadas:
        p.sequencia = seq
        seq += 1

    return {
        "ok": True,
        "msg": f"Rota otimizada. Distância estimada: {total_km:.1f} km.",
        "paradas_ordenadas": len(ordenadas),
        "distancia_km": round(total_km, 2),
    }


@viagem_bp.route("/<int:vid>/otimizar-rota", methods=["POST"])
@permission_required(PERM)
def otimizar_rota(vid: int):
    r = _otimizar_paradas_viagem(vid)
    if not r["ok"]:
        return jsonify({"sucesso": False, "msg": r["msg"]}), 400
    _log_evento(
        vid, "OBSERVACAO", "Rota otimizada automaticamente",
        descricao=f"{r['paradas_ordenadas']} parada(s) reordenada(s) por proximidade. Distância estimada: {r['distancia_km']} km.",
        severidade="info",
    )
    db.session.commit()
    return jsonify({"sucesso": True, **{k: v for k, v in r.items() if k != "ok"}})


@viagem_bp.route("/<int:vid>/geocodificar-paradas", methods=["POST"])
@permission_required(PERM)
def geocodificar_paradas_viagem(vid: int):
    v = db.session.get(Viagem, vid)
    if not v:
        return jsonify({"sucesso": False, "msg": "Viagem nao encontrada."}), 404
    paradas = ViagemParada.query.filter_by(viagem_id=vid).order_by(ViagemParada.sequencia).all()
    sem_coord = [p for p in paradas if p.latitude is None or p.longitude is None]
    atualizadas = _geocodificar_paradas_sem_coord(sem_coord)
    if atualizadas:
        _log_evento(
            vid,
            "OBSERVACAO",
            "Coordenadas atualizadas",
            descricao=f"{atualizadas} parada(s) receberam coordenadas pelo endereco.",
            severidade="info",
        )
        db.session.commit()
    ainda_sem = [
        {"id": p.id, "sequencia": p.sequencia, "endereco": _endereco_parada(p)}
        for p in paradas
        if p.latitude is None or p.longitude is None
    ]
    return jsonify({
        "sucesso": True,
        "atualizadas": atualizadas,
        "sem_coordenadas": ainda_sem,
    })


@viagem_bp.route("/<int:vid>/paradas/reordenar", methods=["POST"])
@permission_required(PERM)
def reordenar_paradas_gestor(vid: int):
    v = db.session.get(Viagem, vid)
    if not v:
        return jsonify({"sucesso": False, "msg": "Viagem nao encontrada."}), 404
    if v.status in ("Concluida", "Cancelada"):
        return jsonify({"sucesso": False, "msg": "Viagem finalizada nao pode ser reordenada."}), 400
    body = request.get_json(silent=True) or {}
    r = _reordenar_paradas(vid, body.get("ordem") or [], permitir_concluidas=True)
    if not r["ok"]:
        return jsonify({"sucesso": False, "msg": r["msg"]}), 400
    _log_evento(
        vid,
        "OBSERVACAO",
        "Rota reordenada",
        descricao=f"Gestor {_user()} reorganizou {r['paradas_ordenadas']} parada(s).",
        severidade="info",
    )
    db.session.commit()
    return jsonify({"sucesso": True, "viagem": _viagem_dict(v, detalhada=True)})




# --------------------------------------------------------------------------- ACESSO PUBLICO DO MOTORISTA (via token, sem login)
motorista_bp = Blueprint("motorista_viagem", __name__)


# ---- Download do app nativo (Android) do motorista ----
@motorista_bp.route("/motorista/app", methods=["GET"])
def motorista_app_download():
    """Página pública com o link para baixar o APK do app do motorista e o
    passo a passo de instalação. Sem login — o motorista abre pelo celular."""
    return render_template("motorista_app_download.html")


# ---- Painel permanente do motorista (link PWA compartilhável) ----
@motorista_bp.route("/motorista/painel/<int:mid>/<token>", methods=["GET"])
def motorista_painel_publico(mid: int, token: str):
    mot = _motorista_por_token(mid, token)
    if not mot:
        return ("Link inválido.", 404)
    return render_template("motorista_painel_publico.html", motorista=mot, token=token)


@motorista_bp.route("/motorista/painel/<int:mid>/<token>/viagens", methods=["GET"])
def motorista_painel_viagens(mid: int, token: str):
    mot = _motorista_por_token(mid, token)
    if not mot:
        return jsonify({"erro": "Token inválido."}), 403
    ativas = (
        Viagem.query
        .filter(
            Viagem.motorista_id == mid,
            Viagem.liberada == True,  # noqa: E712
            Viagem.status.in_(["Planejada", "EmAndamento"]),
        )
        .order_by(Viagem.saida_prevista.asc())
        .all()
    )
    concluidas = (
        Viagem.query
        .filter(Viagem.motorista_id == mid, Viagem.status == "Concluida")
        .order_by(Viagem.retorno_real.desc())
        .limit(20)
        .all()
    )
    return jsonify({
        "motorista": {"id": mot.id, "nome": mot.nome},
        "viagens": [_viagem_dict(v, detalhada=True) for v in ativas],
        "historico": [_viagem_dict(v) for v in concluidas],
    })


@motorista_bp.route("/motorista/viagem/<int:vid>/<token>", methods=["GET"])
def motorista_rastrear(vid: int, token: str):
    v = _viagem_por_token(vid, token)
    if not v:
        return ("Link inválido ou expirado.", 404)
    veiculo = AgendamentoVeiculo.query.get(v.veiculo_id)
    return render_template(
        "motorista_rastrear.html",
        viagem=v,
        veiculo_label=_veiculo_label(veiculo),
        token=token,
    )


@motorista_bp.route("/motorista/viagem/<int:vid>/<token>/iniciar", methods=["POST"])
def motorista_iniciar_publico(vid: int, token: str):
    v = _viagem_por_token(vid, token)
    if not v:
        return jsonify({"sucesso": False, "msg": "Link invalido."}), 404
    if v.status in ("Concluida", "Cancelada"):
        return jsonify({"sucesso": False, "msg": f"Viagem {v.status}. Acao nao permitida."}), 400
    if v.status == "Planejada":
        if not v.liberada:
            return jsonify({"sucesso": False, "msg": "A viagem ainda nao foi liberada pela logistica."}), 403
        v.saida_real = datetime.now()
        v.status = "EmAndamento"
        v.iniciado_por = v.motorista_nome or "motorista"
        _sync_solicitacoes_da_viagem(v, "EmRota")
        data = request.get_json(silent=True) or {}
        km_ini = _parse_int(data.get("km_inicial"))
        if km_ini is not None:
            v.km_inicial = km_ini
        lat = _parse_float(data.get("latitude"))
        lng = _parse_float(data.get("longitude"))
        if lat is not None and lng is not None:
            db.session.add(ViagemPosicao(
                viagem_id=v.id,
                latitude=lat,
                longitude=lng,
                precisao_m=_parse_float(data.get("precisao_m")),
                origem="motorista_app",
                registrado_em=datetime.now(),
            ))
        _log_evento(
            v.id,
            "INICIO",
            "Viagem iniciada pelo motorista",
            descricao=f"Inicio registrado no app do motorista. KM inicial: {v.km_inicial or '—'}.",
            latitude=lat,
            longitude=lng,
            km=km_ini,
            severidade="success",
        )
        db.session.commit()
    return jsonify({"sucesso": True, "status": v.status})


@motorista_bp.route("/motorista/viagem/<int:vid>/<token>/ping", methods=["POST"])
def motorista_ping(vid: int, token: str):
    v = _viagem_por_token(vid, token)
    if not v:
        return jsonify({"sucesso": False, "msg": "Link inválido."}), 404
    if v.status != "EmAndamento":
        return jsonify({"sucesso": False, "status": v.status, "msg": "Viagem não está em andamento."}), 200
    p = request.get_json(silent=True) or {}
    lat = _parse_float(p.get("latitude"))
    lng = _parse_float(p.get("longitude"))
    if lat is None or lng is None:
        return jsonify({"sucesso": False, "msg": "Lat/Lng obrigatórios."}), 400
    pos = ViagemPosicao(
        viagem_id=vid,
        latitude=lat, longitude=lng,
        velocidade_kmh=_parse_float(p.get("velocidade_kmh")),
        rumo=_parse_float(p.get("rumo")),
        precisao_m=_parse_float(p.get("precisao_m")),
        bateria_pct=_parse_int(p.get("bateria_pct")),
        origem="motorista_app",
    )
    db.session.add(pos)
    db.session.commit()
    return jsonify({"sucesso": True, "status": v.status, "id": pos.id})


@motorista_bp.route("/motorista/viagem/<int:vid>/<token>/concluir", methods=["POST"])
def motorista_concluir_publico(vid: int, token: str):
    v = _viagem_por_token(vid, token)
    if not v:
        return jsonify({"sucesso": False, "msg": "Link invalido."}), 404
    if v.status not in ("EmAndamento", "Planejada"):
        return jsonify({"sucesso": False, "msg": f"Viagem nao pode ser concluida (status {v.status})."}), 400
    data = request.get_json(silent=True) or {}
    km_fin = _parse_int(data.get("km_final"))
    v.km_final = km_fin
    v.retorno_real = datetime.now()
    v.status = "Concluida"
    v.concluido_por = v.motorista_nome or "motorista"
    _sync_solicitacoes_da_viagem(v, "Concluida")

    if v.km_inicial is not None and v.km_final is not None and v.km_final >= v.km_inicial:
        v.km_percorrido = float(v.km_final - v.km_inicial)
    else:
        pts = ViagemPosicao.query.filter_by(viagem_id=vid).order_by(ViagemPosicao.registrado_em).all()
        km = 0.0
        for i in range(1, len(pts)):
            km += _haversine_km(pts[i - 1].latitude, pts[i - 1].longitude, pts[i].latitude, pts[i].longitude)
        v.km_percorrido = round(km, 2)

    abs_tot = db.session.query(
        func.coalesce(func.sum(FrotaAbastecimento.litros), 0),
        func.coalesce(func.sum(FrotaAbastecimento.valor_total), 0),
    ).filter(FrotaAbastecimento.viagem_id == vid).first()
    v.total_litros = float(abs_tot[0] or 0)
    v.total_gasto = float(abs_tot[1] or 0)
    if v.saida_real and v.retorno_real:
        v.tempo_total_min = int((v.retorno_real - v.saida_real).total_seconds() // 60)

    lat = _parse_float(data.get("latitude"))
    lng = _parse_float(data.get("longitude"))
    if lat is not None and lng is not None:
        db.session.add(ViagemPosicao(
            viagem_id=v.id, latitude=lat, longitude=lng,
            origem="motorista_app", registrado_em=datetime.now(),
        ))
    _log_evento(v.id, "FIM", "Viagem concluida pelo motorista",
                descricao=f"KM final: {v.km_final or '—'} · percorrido: {v.km_percorrido or 0:.1f} km",
                latitude=lat, longitude=lng, km=v.km_final, severidade="success")
    db.session.commit()
    return jsonify({"sucesso": True, "status": v.status})


@motorista_bp.route("/motorista/viagem/<int:vid>/<token>/status", methods=["GET"])
def motorista_status(vid: int, token: str):
    v = _viagem_por_token(vid, token)
    if not v:
        return jsonify({"sucesso": False, "msg": "Link inválido."}), 404
    return jsonify({"sucesso": True, "status": v.status, "codigo": v.codigo, "titulo": v.titulo})


@motorista_bp.route("/motorista/viagem/<int:vid>/<token>/paradas", methods=["GET"])
def motorista_paradas(vid: int, token: str):
    """Lista paradas ordenadas por sequencia para o app do motorista."""
    v = _viagem_por_token(vid, token)
    if not v:
        return jsonify({"sucesso": False, "msg": "Link inválido."}), 404
    paradas = ViagemParada.query.filter_by(viagem_id=vid).order_by(ViagemParada.sequencia).all()
    lista = []
    for p in paradas:
        lista.append({
            "id": p.id,
            "sequencia": p.sequencia,
            "tipo": p.tipo,
            "parceiro_nome": p.parceiro_nome,
            "endereco": p.endereco,
            "cidade": p.cidade,
            "uf": p.uf,
            "latitude": p.latitude,
            "longitude": p.longitude,
            "previsao_chegada": p.previsao_chegada.isoformat() if p.previsao_chegada else None,
            "chegada_real": p.chegada_real.isoformat() if p.chegada_real else None,
            "status": p.status,
            "observacao": p.observacao,
        })
    return jsonify({"sucesso": True, "viagem_status": v.status, "paradas": lista})


@motorista_bp.route("/motorista/viagem/<int:vid>/<token>/paradas/reordenar", methods=["POST"])
def motorista_reordenar_paradas(vid: int, token: str):
    v = _viagem_por_token(vid, token)
    if not v:
        return jsonify({"sucesso": False, "msg": "Link invalido."}), 404
    if v.status in ("Concluida", "Cancelada"):
        return jsonify({"sucesso": False, "msg": "Viagem finalizada nao pode ser reordenada."}), 400
    data = request.get_json(silent=True) or {}
    r = _reordenar_paradas(vid, data.get("ordem") or [], permitir_concluidas=False)
    if not r["ok"]:
        return jsonify({"sucesso": False, "msg": r["msg"]}), 400
    _log_evento(
        vid,
        "OBSERVACAO",
        "Rota reorganizada pelo motorista",
        descricao=f"{r['paradas_ordenadas']} parada(s) pendente(s) reordenada(s).",
        severidade="info",
    )
    db.session.commit()
    return jsonify({"sucesso": True, "paradas_ordenadas": r["paradas_ordenadas"]})


@motorista_bp.route("/motorista/viagem/<int:vid>/<token>/parada/<int:pid>/chegar", methods=["POST"])
def motorista_chegar_parada(vid: int, token: str, pid: int):
    v = _viagem_por_token(vid, token)
    if not v:
        return jsonify({"sucesso": False, "msg": "Link inválido."}), 404
    if v.status != "EmAndamento":
        return jsonify({"sucesso": False, "msg": f"Viagem {v.status}. Ação não permitida."}), 400
    p = ViagemParada.query.filter_by(id=pid, viagem_id=vid).first()
    if not p:
        return jsonify({"sucesso": False, "msg": "Parada não encontrada."}), 404
    if p.status == "Concluida":
        return jsonify({"sucesso": True, "msg": "Parada já concluída."})
    data = request.get_json(silent=True) or {}
    p.chegada_real = datetime.now()
    p.status = "EmAndamento"
    _log_evento(
        vid,
        "CHEGADA",
        f"Chegada em {p.parceiro_nome or p.endereco or 'parada'}",
        descricao=f"Motorista registrou chegada (seq. {p.sequencia}).",
        latitude=_parse_float(data.get("latitude")),
        longitude=_parse_float(data.get("longitude")),
        parada_id=p.id,
        severidade="success",
    )
    db.session.commit()
    return jsonify({"sucesso": True, "parada_id": p.id, "status": p.status})


@motorista_bp.route("/motorista/viagem/<int:vid>/<token>/parada/<int:pid>/concluir", methods=["POST"])
def motorista_concluir_parada(vid: int, token: str, pid: int):
    v = _viagem_por_token(vid, token)
    if not v:
        return jsonify({"sucesso": False, "msg": "Link inválido."}), 404
    if v.status != "EmAndamento":
        return jsonify({"sucesso": False, "msg": f"Viagem {v.status}. Ação não permitida."}), 400
    p = ViagemParada.query.filter_by(id=pid, viagem_id=vid).first()
    if not p:
        return jsonify({"sucesso": False, "msg": "Parada não encontrada."}), 404
    if request.content_type and "multipart" in request.content_type:
        data = request.form
        foto = _save_upload("foto")
    else:
        data = request.get_json(silent=True) or {}
        foto = None
    p.resultado = (data.get("resultado") or ("Entregue" if p.tipo == "ENTREGA" else "Coletado")).strip()
    obs = (data.get("observacao") or "").strip()
    nao_realizada = p.resultado in {"Recusado", "AusenciaRecebedor", "NaoRealizada"}
    if nao_realizada and not obs:
        return jsonify({"sucesso": False, "msg": "Informe a justificativa da coleta/entrega não realizada."}), 400
    # Foto do canhoto é obrigatória ao concluir uma ENTREGA com sucesso
    # (não se aplica quando o resultado é recusa/ausência/não realizada).
    if p.tipo == "ENTREGA" and not nao_realizada and not foto and not p.foto_paths:
        return jsonify({"sucesso": False, "msg": "Anexe a foto do canhoto para concluir a entrega."}), 400
    p.saida_real = datetime.now()
    if not p.chegada_real:
        p.chegada_real = p.saida_real
    if obs:
        p.observacao = obs
    p.status = "Nao_realizada" if nao_realizada else "Concluida"
    if foto:
        try:
            atual = json.loads(p.foto_paths) if p.foto_paths else []
        except ValueError:
            atual = []
        atual.append(foto)
        p.foto_paths = json.dumps(atual, ensure_ascii=False)
    if nao_realizada and p.solicitacao_id:
        _solicitacao_volta_pendente(db.session.get(AgendamentoSolicitacao, p.solicitacao_id))
    elif p.solicitacao_id:
        _sync_solicitacao_viagem(db.session.get(AgendamentoSolicitacao, p.solicitacao_id), v, "Concluida")
    _log_evento(
        vid,
        "OCORRENCIA" if nao_realizada else "SAIDA_PARADA",
        f"Parada concluída: {p.parceiro_nome or 'parada'}",
        descricao=f"Resultado: {p.resultado}. {obs}" if obs else f"Resultado: {p.resultado}",
        latitude=_parse_float(data.get("latitude")),
        longitude=_parse_float(data.get("longitude")),
        parada_id=p.id,
        foto_path=foto,
        severidade="warning" if nao_realizada else "success",
    )
    db.session.commit()
    _integrar_comprovante_entrega(p)
    return jsonify({"sucesso": True, "parada_id": p.id, "status": p.status})

