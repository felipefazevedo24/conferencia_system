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
from sqlalchemy import func, or_
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
    return jsonify([_viagem_dict(v) for v in regs])


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
    p = request.get_json(silent=True) or {}
    veiculo_id = _parse_int(p.get("veiculo_id"))
    if not veiculo_id:
        return jsonify({"sucesso": False, "msg": "Veículo obrigatório."}), 400
    motorista_id = _parse_int(p.get("motorista_id"))
    motorista = AgendamentoMotorista.query.get(motorista_id) if motorista_id else None
    tipo = str(p.get("tipo") or "MISTA").upper().strip()
    if tipo not in {"MISTA", "COLETA", "ENTREGA", "ALEATORIA"}:
        return jsonify({"sucesso": False, "msg": "Tipo de viagem invalido."}), 400
    observacao = str(p.get("observacao") or "").strip()
    if tipo == "ALEATORIA" and not observacao:
        return jsonify({"sucesso": False, "msg": "Descricao obrigatoria para viagem aleatoria."}), 400
    avulsa = bool(p.get("avulsa"))
    funcionario_responsavel = str(p.get("funcionario_responsavel") or "").strip()
    if avulsa and not funcionario_responsavel:
        return jsonify({"sucesso": False, "msg": "Informe o nome do funcionario que esta saindo com o veiculo."}), 400
    sol_ids = p.get("solicitacao_ids") or []
    paradas_livres = p.get("paradas") or []
    if avulsa:
        sol_ids = []
        paradas_livres = []
    elif tipo == "ALEATORIA":
        sol_ids = []
        paradas_livres = []
    elif not sol_ids and not paradas_livres:
        return jsonify({"sucesso": False, "msg": "Selecione pelo menos uma solicitacao pendente ou parada manual."}), 400
    if sol_ids and not motorista_id:
        return jsonify({"sucesso": False, "msg": "Selecione um motorista para assumir as solicitações da viagem."}), 400

    saida_prevista = _parse_dt(p.get("saida_prevista"))
    retorno_previsto = _parse_dt(p.get("retorno_previsto"))
    conflito = _validar_conflito_recurso(
        veiculo_id=veiculo_id,
        motorista_id=motorista_id,
        saida=saida_prevista,
        retorno=retorno_previsto,
    )
    if conflito:
        return jsonify({"sucesso": False, "msg": conflito}), 409

    v = Viagem(
        codigo=_proximo_codigo(),
        veiculo_id=veiculo_id,
        motorista_id=motorista_id,
        motorista_nome=(p.get("motorista_nome") or (motorista.nome if motorista else None) or (funcionario_responsavel if avulsa else None)),
        tipo=tipo,
        status="Planejada",
        titulo=str(p.get("titulo") or "").strip() or None,
        observacao=observacao or None,
        avulsa=avulsa,
        funcionario_responsavel=funcionario_responsavel or None,
        saida_prevista=saida_prevista,
        retorno_previsto=retorno_previsto,
        km_previsto=_parse_float(p.get("km_previsto")),
        origem_label=str(p.get("origem_label") or "").strip() or None,
        origem_lat=_parse_float(p.get("origem_lat")),
        origem_lng=_parse_float(p.get("origem_lng")),
        destino_label=str(p.get("destino_label") or "").strip() or None,
        destino_lat=_parse_float(p.get("destino_lat")),
        destino_lng=_parse_float(p.get("destino_lng")),
        criado_por=_user(),
    )
    db.session.add(v)
    db.session.flush()

    # Vincular solicitações existentes como paradas
    seq = 1
    for sid in sol_ids:
        sol = db.session.get(AgendamentoSolicitacao, int(sid))
        if not sol:
            continue
        endereco = ", ".join([x for x in [sol.logradouro, sol.numero, sol.bairro] if x])
        db.session.add(ViagemParada(
            viagem_id=v.id,
            sequencia=seq,
            solicitacao_id=sol.id,
            tipo=sol.tipo,
            parceiro_nome=sol.parceiro_nome,
            endereco=endereco or None,
            cidade=sol.cidade,
            uf=sol.uf,
            latitude=sol.destino_latitude or sol.origem_latitude,
            longitude=sol.destino_longitude or sol.origem_longitude,
            previsao_chegada=sol.data_hora_saida_prevista,
        ))
        _sync_solicitacao_viagem(sol, v, "Alocada")
        seq += 1

    # Paradas livres
    for par in paradas_livres:
        db.session.add(ViagemParada(
            viagem_id=v.id,
            sequencia=seq,
            tipo=str(par.get("tipo") or "ENTREGA").upper(),
            parceiro_nome=str(par.get("parceiro_nome") or "").strip() or None,
            endereco=str(par.get("endereco") or "").strip() or None,
            cidade=str(par.get("cidade") or "").strip() or None,
            uf=str(par.get("uf") or "").strip() or None,
            latitude=_parse_float(par.get("latitude")),
            longitude=_parse_float(par.get("longitude")),
            previsao_chegada=_parse_dt(par.get("previsao_chegada")),
        ))
        seq += 1

    _log_evento(v.id, "INICIO", f"Viagem {v.codigo} criada", descricao=f"Planejada por {_user()}", severidade="info")
    db.session.commit()
    return jsonify({"sucesso": True, "viagem": _viagem_dict(v, detalhada=True), "otimizacao": None})


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
    db.session.commit()
    return jsonify({"sucesso": True, "viagem": _viagem_dict(v, detalhada=True)})


@viagem_bp.route("/<int:vid>", methods=["DELETE"])
@permission_required(PERM)
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
    """Cria uma nova Viagem contendo esta única solicitação como 1ª parada.
    Requer que a solicitação já tenha veículo + motorista alocados."""
    s = db.session.get(AgendamentoSolicitacao, sid)
    if not s:
        return jsonify({"sucesso": False, "msg": "Solicitação não encontrada."}), 404
    if not s.veiculo_id or not s.motorista_id:
        return jsonify({"sucesso": False, "msg": "Aloque veículo E motorista antes de montar a viagem."}), 400
    # verifica se já existe uma viagem Planejada com esta solicitação
    ja = ViagemParada.query.filter_by(solicitacao_id=sid).first()
    if ja:
        return jsonify({"sucesso": False, "msg": f"Esta solicitação já está em uma viagem (Viagem #{ja.viagem_id}).", "viagem_id": ja.viagem_id}), 409
    v = Viagem(
        codigo=_proximo_codigo(),
        veiculo_id=s.veiculo_id,
        motorista_id=s.motorista_id,
        status="Planejada",
        saida_prevista=s.data_hora_saida_prevista,
        retorno_previsto=s.data_hora_retorno_prevista,
        criado_por=_user(),
    )
    db.session.add(v)
    db.session.flush()
    parada = _parada_dict_from_solicitacao(sid, 1)
    if parada:
        parada.viagem_id = v.id
        db.session.add(parada)
        _sync_solicitacao_viagem(s, v, "Alocada")
    _log_evento(v.id, "OBSERVACAO", "Viagem criada a partir de solicitação",
                descricao=f"Solicitação {s.codigo or '#'+str(s.id)} - {s.parceiro_nome}. Gestor: {_user()}.",
                severidade="info")
    db.session.commit()
    return jsonify({"sucesso": True, "viagem": _viagem_dict(v, detalhada=True)})


@viagem_bp.route("/<int:vid>/anexar-solicitacao/<int:sid>", methods=["POST"])
@permission_required(PERM)
def anexar_solicitacao_viagem(vid: int, sid: int):
    """Anexa uma solicitação existente como nova parada da viagem."""
    v = db.session.get(Viagem, vid)
    s = db.session.get(AgendamentoSolicitacao, sid)
    if not v or not s:
        return jsonify({"sucesso": False, "msg": "Viagem ou solicitação não encontrada."}), 404
    if v.status not in ("Planejada", "EmAndamento"):
        return jsonify({"sucesso": False, "msg": "Viagem já encerrada."}), 400
    ja = ViagemParada.query.filter_by(solicitacao_id=sid).first()
    if ja:
        return jsonify({"sucesso": False, "msg": f"Solicitação já está na viagem #{ja.viagem_id}.", "viagem_id": ja.viagem_id}), 409
    ultima = db.session.query(func.coalesce(func.max(ViagemParada.sequencia), 0)).filter_by(viagem_id=vid).scalar() or 0
    parada = _parada_dict_from_solicitacao(sid, ultima + 1)
    if not parada:
        return jsonify({"sucesso": False, "msg": "Falha ao montar parada."}), 400
    parada.viagem_id = vid
    db.session.add(parada)
    db.session.flush()
    _sync_solicitacao_viagem(s, v, "EmRota" if v.status == "EmAndamento" else "Alocada")
    revogou = False
    # se estava liberada, revoga (o motorista precisa ver o novo plano antes)
    if v.liberada and v.status == "Planejada":
        v.liberada = False
        v.liberada_em = None
        v.liberada_por = None
        revogou = True
        _log_evento(vid, "OBSERVACAO", "Liberação revogada automaticamente",
                    descricao=f"Nova parada ({s.parceiro_nome}) anexada pelo gestor {_user()}.",
                    severidade="warning")
    _log_evento(vid, "PARADA_EXTRA",
                f"Solicitação anexada: {s.parceiro_nome}",
                parada_id=parada.id, severidade="info")
    db.session.commit()
    return jsonify({"sucesso": True, "viagem_id": vid, "liberacao_revogada": revogou})


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
            Viagem.liberada == True,  # noqa: E712
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
    """Cria uma Viagem 'Planejada' a partir de uma rota planejada (veículo + data).

    Body: { veiculo_id, data (YYYY-MM-DD), titulo?, observacao? }
    Usa todas as solicitações alocadas do dia para o veículo.
    """
    p = request.get_json(silent=True) or {}
    veiculo_id = _parse_int(p.get("veiculo_id"))
    if not veiculo_id:
        return jsonify({"sucesso": False, "msg": "Veículo obrigatório."}), 400
    data_str = (p.get("data") or "").strip()
    try:
        dia = datetime.strptime(data_str, "%Y-%m-%d").date() if data_str else date.today()
    except ValueError:
        return jsonify({"sucesso": False, "msg": "Data inválida."}), 400

    ini = datetime.combine(dia, datetime.min.time())
    fim = ini + timedelta(days=1)

    ja_usadas = {
        r[0] for r in db.session.query(ViagemParada.solicitacao_id)
        .join(Viagem, Viagem.id == ViagemParada.viagem_id)
        .filter(ViagemParada.solicitacao_id.isnot(None))
        .filter(Viagem.status != "Cancelada")
        .all()
    }
    sols = (AgendamentoSolicitacao.query
            .filter(AgendamentoSolicitacao.veiculo_id == veiculo_id)
            .filter(AgendamentoSolicitacao.status.in_(["Alocada", "Aprovada", "Pendente"]))
            .filter(AgendamentoSolicitacao.data_hora_saida_prevista >= ini)
            .filter(AgendamentoSolicitacao.data_hora_saida_prevista < fim)
            .order_by(AgendamentoSolicitacao.data_hora_saida_prevista.asc())
            .all())
    sols = [s for s in sols if s.id not in ja_usadas]
    if not sols:
        return jsonify({"sucesso": False, "msg": "Nenhuma solicitação alocada encontrada para esse veículo/data."}), 404

    primeira = sols[0]
    ultima = sols[-1]
    motorista = AgendamentoMotorista.query.get(primeira.motorista_id) if primeira.motorista_id else None
    tipos = {s.tipo for s in sols if s.tipo}
    if len(tipos) == 1:
        tipo_v = next(iter(tipos))
    else:
        tipo_v = "MISTA"
    saida_prevista = primeira.data_hora_saida_prevista
    retorno_previsto = ultima.data_hora_retorno_prevista or ultima.data_hora_saida_prevista
    conflito = _validar_conflito_recurso(
        veiculo_id=veiculo_id,
        motorista_id=primeira.motorista_id,
        saida=saida_prevista,
        retorno=retorno_previsto,
    )
    if conflito:
        return jsonify({"sucesso": False, "msg": conflito}), 409

    v = Viagem(
        codigo=_proximo_codigo(),
        veiculo_id=veiculo_id,
        motorista_id=primeira.motorista_id,
        motorista_nome=primeira.motorista_nome or (motorista.nome if motorista else None),
        tipo=tipo_v,
        status="Planejada",
        titulo=str(p.get("titulo") or f"Rota {dia.strftime('%d/%m/%Y')}").strip(),
        observacao=str(p.get("observacao") or "").strip() or None,
        saida_prevista=saida_prevista,
        retorno_previsto=retorno_previsto,
        origem_lat=primeira.origem_latitude,
        origem_lng=primeira.origem_longitude,
        destino_label=f"{ultima.parceiro_nome} — {ultima.cidade}/{ultima.uf}" if ultima.parceiro_nome else None,
        destino_lat=ultima.destino_latitude,
        destino_lng=ultima.destino_longitude,
        criado_por=_user(),
    )
    db.session.add(v)
    db.session.flush()

    seq = 1
    for s in sols:
        endereco = ", ".join([x for x in [s.logradouro, s.numero, s.bairro] if x])
        db.session.add(ViagemParada(
            viagem_id=v.id,
            sequencia=seq,
            solicitacao_id=s.id,
            tipo=s.tipo,
            parceiro_nome=s.parceiro_nome,
            endereco=endereco or None,
            cidade=s.cidade,
            uf=s.uf,
            latitude=s.destino_latitude or s.origem_latitude,
            longitude=s.destino_longitude or s.origem_longitude,
            previsao_chegada=s.data_hora_saida_prevista,
        ))
        _sync_solicitacao_viagem(s, v, "Alocada")
        seq += 1

    _log_evento(
        v.id,
        "INICIO",
        f"Viagem {v.codigo} importada da rota planejada",
        descricao=f"{len(sols)} parada(s) importada(s) de {dia.strftime('%d/%m/%Y')} — por {_user()}",
        severidade="info",
    )
    db.session.commit()
    return jsonify({
        "sucesso": True,
        "viagem": _viagem_dict(v, detalhada=True),
        "paradas_importadas": len(sols),
        "otimizacao": None,
    })


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

