"""Rotas da Gestao de Frota: documentos, manutencao, abastecimento, multas, checklist."""
from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta

from flask import Blueprint, current_app, jsonify, request, session
from sqlalchemy import func, or_
from werkzeug.utils import secure_filename

from ..auth import permission_required
from ..extensions import db
from ..models import (
    AgendamentoMotorista,
    AgendamentoVeiculo,
    FrotaAbastecimento,
    FrotaChecklistDiario,
    FrotaDocumento,
    FrotaManutencao,
    FrotaMulta,
)

frota_bp = Blueprint("frota", __name__, url_prefix="/api/frota")

PERM = "PAGE_LOGISTICA_FROTA"
UPLOAD_SUB = "frota"
ALLOWED_EXTS = {"pdf", "jpg", "jpeg", "png", "webp"}


# ----------------------------------------------------------------------------- util
def _user():
    return session.get("username", "sistema")


def _parse_date(s):
    if not s:
        return None
    s = str(s).strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


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


def _parse_float(s, default=0.0):
    try:
        return float(str(s).replace(",", "."))
    except (TypeError, ValueError):
        return default


def _parse_int(s, default=None):
    try:
        return int(str(s).strip())
    except (TypeError, ValueError):
        return default


def _upload_dir():
    d = os.path.join(current_app.instance_path, UPLOAD_SUB)
    os.makedirs(d, exist_ok=True)
    return d


def _save_upload(key: str = "anexo") -> str | None:
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


def _veiculo_label(v: AgendamentoVeiculo | None) -> str:
    if not v:
        return "—"
    return f"{v.nome_exibicao}" + (f" ({v.placa})" if v.placa else "")


def _motorista_label(m: AgendamentoMotorista | None) -> str:
    return m.nome if m else "—"


# ----------------------------------------------------------------------------- dashboard
@frota_bp.route("/dashboard", methods=["GET"])
@permission_required(PERM)
def dashboard():
    hoje = date.today()
    daqui_30 = hoje + timedelta(days=30)

    veiculos = AgendamentoVeiculo.query.filter_by(ativo=True).all()
    motoristas = AgendamentoMotorista.query.filter_by(ativo=True).all()

    # Documentos
    docs = FrotaDocumento.query.all()
    docs_vencidos = [d for d in docs if d.vencimento and d.vencimento < hoje]
    docs_vencendo = [d for d in docs if d.vencimento and hoje <= d.vencimento <= daqui_30]

    # Manutencoes agendadas / atrasadas
    manuts = FrotaManutencao.query.filter(FrotaManutencao.status != "Cancelada").all()
    manut_atrasada = [m for m in manuts if m.proxima_data and m.proxima_data < hoje and m.status == "Realizada"]
    manut_proxima = [m for m in manuts if m.proxima_data and hoje <= m.proxima_data <= daqui_30]

    # Abastecimento - consumo medio por veiculo (ultimos 90d)
    inicio = datetime.now() - timedelta(days=90)
    abast = (
        FrotaAbastecimento.query
        .filter(FrotaAbastecimento.data >= inicio)
        .order_by(FrotaAbastecimento.veiculo_id, FrotaAbastecimento.data.asc())
        .all()
    )
    consumo_por_veiculo: dict[int, dict] = {}
    por_veiculo: dict[int, list] = {}
    for a in abast:
        por_veiculo.setdefault(a.veiculo_id, []).append(a)
    for vid, regs in por_veiculo.items():
        total_km = 0.0
        total_litros = 0.0
        total_gasto = 0.0
        for i, r in enumerate(regs):
            total_litros += r.litros or 0
            total_gasto += r.valor_total or 0
            if i > 0:
                delta = (r.km_atual or 0) - (regs[i - 1].km_atual or 0)
                if 0 < delta < 5000:
                    total_km += delta
        consumo_por_veiculo[vid] = {
            "km": round(total_km, 1),
            "litros": round(total_litros, 2),
            "gasto": round(total_gasto, 2),
            "consumo_kml": round(total_km / total_litros, 2) if total_litros > 0 else None,
            "custo_km": round(total_gasto / total_km, 2) if total_km > 0 else None,
        }

    # Multas pendentes
    multas_pend = FrotaMulta.query.filter_by(status="Pendente").count()
    multas_valor = db.session.query(func.coalesce(func.sum(FrotaMulta.valor), 0)).filter_by(status="Pendente").scalar()

    # Checklists de hoje
    inicio_hoje = datetime.combine(hoje, datetime.min.time())
    fim_hoje = datetime.combine(hoje, datetime.max.time())
    checks_hoje = FrotaChecklistDiario.query.filter(
        FrotaChecklistDiario.data >= inicio_hoje,
        FrotaChecklistDiario.data <= fim_hoje,
    ).all()

    kpis = {
        "veiculos_ativos": len(veiculos),
        "motoristas_ativos": len(motoristas),
        "docs_vencidos": len(docs_vencidos),
        "docs_vencendo_30d": len(docs_vencendo),
        "manut_atrasadas": len(manut_atrasada),
        "manut_proximas_30d": len(manut_proxima),
        "multas_pendentes": multas_pend,
        "multas_valor_pendente": float(multas_valor or 0),
        "checklists_hoje": len(checks_hoje),
        "veiculos_sem_checklist_hoje": max(0, len(veiculos) - len({c.veiculo_id for c in checks_hoje})),
    }

    alertas = []
    for d in sorted(docs_vencidos + docs_vencendo, key=lambda x: x.vencimento or date.max):
        veiculo = AgendamentoVeiculo.query.get(d.veiculo_id) if d.veiculo_id else None
        motorista = AgendamentoMotorista.query.get(d.motorista_id) if d.motorista_id else None
        dias = (d.vencimento - hoje).days if d.vencimento else None
        alertas.append({
            "id": d.id,
            "categoria": "Documento",
            "severidade": "danger" if dias is not None and dias < 0 else ("warning" if dias is not None and dias <= 30 else "info"),
            "titulo": f"{d.tipo} {('vencido há ' + str(abs(dias)) + ' dias') if dias is not None and dias < 0 else (f'vence em {dias} dias' if dias is not None else '')}".strip(),
            "descricao": f"{_veiculo_label(veiculo) if veiculo else _motorista_label(motorista)} · doc nº {d.numero or '—'}",
            "data": d.vencimento.isoformat() if d.vencimento else None,
        })
    for m in sorted(manut_atrasada + manut_proxima, key=lambda x: x.proxima_data or date.max):
        veiculo = AgendamentoVeiculo.query.get(m.veiculo_id)
        dias = (m.proxima_data - hoje).days if m.proxima_data else None
        alertas.append({
            "id": m.id,
            "categoria": "Manutenção",
            "severidade": "danger" if dias is not None and dias < 0 else "warning",
            "titulo": f"{m.tipo} {('atrasada em ' + str(abs(dias)) + ' dias') if dias is not None and dias < 0 else (f'em {dias} dias' if dias is not None else '')}".strip(),
            "descricao": f"{_veiculo_label(veiculo)} · {m.descricao or ''}",
            "data": m.proxima_data.isoformat() if m.proxima_data else None,
        })

    frota = []
    for v in veiculos:
        frota.append({
            "id": v.id,
            "codigo": v.codigo,
            "nome_exibicao": v.nome_exibicao,
            "placa": v.placa,
            "consumo": consumo_por_veiculo.get(v.id, {"km": 0, "litros": 0, "gasto": 0, "consumo_kml": None, "custo_km": None}),
        })

    return jsonify({"kpis": kpis, "alertas": alertas[:20], "frota": frota})


# ----------------------------------------------------------------------------- serializers
def _doc_dict(d: FrotaDocumento) -> dict:
    veiculo = AgendamentoVeiculo.query.get(d.veiculo_id) if d.veiculo_id else None
    motorista = AgendamentoMotorista.query.get(d.motorista_id) if d.motorista_id else None
    hoje = date.today()
    dias = (d.vencimento - hoje).days if d.vencimento else None
    status = "OK"
    if dias is None:
        status = "Sem data"
    elif dias < 0:
        status = "Vencido"
    elif dias <= 30:
        status = "Vence em breve"
    return {
        "id": d.id,
        "tipo": d.tipo,
        "numero": d.numero,
        "emitido_em": d.emitido_em.isoformat() if d.emitido_em else None,
        "vencimento": d.vencimento.isoformat() if d.vencimento else None,
        "dias_para_vencer": dias,
        "status": status,
        "observacao": d.observacao,
        "anexo_path": d.anexo_path,
        "veiculo_id": d.veiculo_id,
        "motorista_id": d.motorista_id,
        "veiculo_label": _veiculo_label(veiculo),
        "motorista_label": _motorista_label(motorista),
        "criado_em": d.criado_em.isoformat() if d.criado_em else None,
    }


def _manut_dict(m: FrotaManutencao) -> dict:
    veiculo = AgendamentoVeiculo.query.get(m.veiculo_id)
    hoje = date.today()
    dias = (m.proxima_data - hoje).days if m.proxima_data else None
    return {
        "id": m.id,
        "veiculo_id": m.veiculo_id,
        "veiculo_label": _veiculo_label(veiculo),
        "tipo": m.tipo,
        "data": m.data.isoformat() if m.data else None,
        "km_atual": m.km_atual,
        "custo": m.custo,
        "fornecedor": m.fornecedor,
        "nota_fiscal": m.nota_fiscal,
        "descricao": m.descricao,
        "proxima_data": m.proxima_data.isoformat() if m.proxima_data else None,
        "proxima_km": m.proxima_km,
        "dias_para_proxima": dias,
        "status": m.status,
        "anexo_path": m.anexo_path,
    }


def _abast_dict(a: FrotaAbastecimento) -> dict:
    veiculo = AgendamentoVeiculo.query.get(a.veiculo_id)
    motorista = AgendamentoMotorista.query.get(a.motorista_id) if a.motorista_id else None
    return {
        "id": a.id,
        "veiculo_id": a.veiculo_id,
        "veiculo_label": _veiculo_label(veiculo),
        "motorista_id": a.motorista_id,
        "motorista_label": _motorista_label(motorista),
        "data": a.data.isoformat() if a.data else None,
        "km_atual": a.km_atual,
        "litros": a.litros,
        "valor_litro": a.valor_litro,
        "valor_total": a.valor_total,
        "combustivel": a.combustivel,
        "posto": a.posto,
        "tanque_cheio": a.tanque_cheio,
        "observacao": a.observacao,
    }


def _multa_dict(m: FrotaMulta) -> dict:
    veiculo = AgendamentoVeiculo.query.get(m.veiculo_id)
    motorista = AgendamentoMotorista.query.get(m.motorista_id) if m.motorista_id else None
    hoje = date.today()
    dias = (m.vencimento - hoje).days if m.vencimento else None
    return {
        "id": m.id,
        "veiculo_id": m.veiculo_id,
        "veiculo_label": _veiculo_label(veiculo),
        "motorista_id": m.motorista_id,
        "motorista_label": _motorista_label(motorista),
        "auto_infracao": m.auto_infracao,
        "data_infracao": m.data_infracao.isoformat() if m.data_infracao else None,
        "local": m.local,
        "descricao": m.descricao,
        "valor": m.valor,
        "pontos": m.pontos,
        "gravidade": m.gravidade,
        "vencimento": m.vencimento.isoformat() if m.vencimento else None,
        "dias_para_vencer": dias,
        "status": m.status,
        "anexo_path": m.anexo_path,
        "observacao": m.observacao,
    }


def _check_dict(c: FrotaChecklistDiario) -> dict:
    veiculo = AgendamentoVeiculo.query.get(c.veiculo_id)
    motorista = AgendamentoMotorista.query.get(c.motorista_id) if c.motorista_id else None
    try:
        itens = json.loads(c.itens_json) if c.itens_json else []
    except ValueError:
        itens = []
    try:
        fotos = json.loads(c.foto_paths) if c.foto_paths else []
    except ValueError:
        fotos = []
    return {
        "id": c.id,
        "veiculo_id": c.veiculo_id,
        "veiculo_label": _veiculo_label(veiculo),
        "motorista_id": c.motorista_id,
        "motorista_label": _motorista_label(motorista),
        "data": c.data.isoformat() if c.data else None,
        "km_atual": c.km_atual,
        "itens": itens,
        "status_geral": c.status_geral,
        "observacao": c.observacao,
        "fotos": fotos,
        "criado_por": c.criado_por,
    }


# ----------------------------------------------------------------------------- DOCUMENTOS
@frota_bp.route("/documentos", methods=["GET"])
@permission_required(PERM)
def listar_documentos():
    q = FrotaDocumento.query
    if request.args.get("veiculo_id"):
        q = q.filter_by(veiculo_id=_parse_int(request.args.get("veiculo_id")))
    if request.args.get("motorista_id"):
        q = q.filter_by(motorista_id=_parse_int(request.args.get("motorista_id")))
    if request.args.get("tipo"):
        q = q.filter_by(tipo=request.args.get("tipo"))
    regs = q.order_by(FrotaDocumento.vencimento.asc().nullslast()).all()
    return jsonify([_doc_dict(d) for d in regs])


@frota_bp.route("/documentos", methods=["POST"])
@permission_required(PERM)
def criar_documento():
    # aceita multipart (com anexo) ou json
    if request.content_type and "multipart" in request.content_type:
        payload = request.form.to_dict()
        anexo = _save_upload("anexo")
    else:
        payload = request.get_json(silent=True) or {}
        anexo = None

    tipo = str(payload.get("tipo") or "").strip().upper()
    if not tipo:
        return jsonify({"sucesso": False, "msg": "Tipo obrigatório."}), 400

    veiculo_id = _parse_int(payload.get("veiculo_id"))
    motorista_id = _parse_int(payload.get("motorista_id"))
    if not veiculo_id and not motorista_id:
        return jsonify({"sucesso": False, "msg": "Vincule ao veículo ou motorista."}), 400

    d = FrotaDocumento(
        tipo=tipo,
        numero=str(payload.get("numero") or "").strip() or None,
        emitido_em=_parse_date(payload.get("emitido_em")),
        vencimento=_parse_date(payload.get("vencimento")),
        observacao=str(payload.get("observacao") or "").strip() or None,
        anexo_path=anexo,
        veiculo_id=veiculo_id,
        motorista_id=motorista_id,
        criado_por=_user(),
    )
    db.session.add(d)
    db.session.commit()
    return jsonify({"sucesso": True, "documento": _doc_dict(d)})


@frota_bp.route("/documentos/<int:doc_id>", methods=["PATCH"])
@permission_required(PERM)
def editar_documento(doc_id: int):
    d = db.session.get(FrotaDocumento, doc_id)
    if not d:
        return jsonify({"sucesso": False, "msg": "Documento não encontrado."}), 404
    p = request.get_json(silent=True) or {}
    if "tipo" in p:
        d.tipo = str(p["tipo"]).strip().upper()
    if "numero" in p:
        d.numero = str(p["numero"] or "").strip() or None
    if "emitido_em" in p:
        d.emitido_em = _parse_date(p["emitido_em"])
    if "vencimento" in p:
        d.vencimento = _parse_date(p["vencimento"])
    if "observacao" in p:
        d.observacao = str(p["observacao"] or "").strip() or None
    d.atualizado_em = datetime.now()
    db.session.commit()
    return jsonify({"sucesso": True, "documento": _doc_dict(d)})


@frota_bp.route("/documentos/<int:doc_id>", methods=["DELETE"])
@permission_required(PERM)
def excluir_documento(doc_id: int):
    d = db.session.get(FrotaDocumento, doc_id)
    if not d:
        return jsonify({"sucesso": False, "msg": "Documento não encontrado."}), 404
    db.session.delete(d)
    db.session.commit()
    return jsonify({"sucesso": True})


# ----------------------------------------------------------------------------- MANUTENCOES
@frota_bp.route("/manutencoes", methods=["GET"])
@permission_required(PERM)
def listar_manutencoes():
    q = FrotaManutencao.query
    if request.args.get("veiculo_id"):
        q = q.filter_by(veiculo_id=_parse_int(request.args.get("veiculo_id")))
    if request.args.get("status"):
        q = q.filter_by(status=request.args.get("status"))
    regs = q.order_by(FrotaManutencao.data.desc()).all()
    return jsonify([_manut_dict(m) for m in regs])


@frota_bp.route("/manutencoes", methods=["POST"])
@permission_required(PERM)
def criar_manutencao():
    if request.content_type and "multipart" in request.content_type:
        p = request.form.to_dict()
        anexo = _save_upload("anexo")
    else:
        p = request.get_json(silent=True) or {}
        anexo = None

    veiculo_id = _parse_int(p.get("veiculo_id"))
    if not veiculo_id:
        return jsonify({"sucesso": False, "msg": "Veículo obrigatório."}), 400
    descricao = str(p.get("descricao") or "").strip()
    if not descricao:
        return jsonify({"sucesso": False, "msg": "Descrição obrigatória."}), 400

    m = FrotaManutencao(
        veiculo_id=veiculo_id,
        tipo=str(p.get("tipo") or "OUTRO").strip().upper(),
        data=_parse_date(p.get("data")) or date.today(),
        km_atual=_parse_int(p.get("km_atual")),
        custo=_parse_float(p.get("custo")),
        fornecedor=str(p.get("fornecedor") or "").strip() or None,
        nota_fiscal=str(p.get("nota_fiscal") or "").strip() or None,
        descricao=descricao,
        proxima_data=_parse_date(p.get("proxima_data")),
        proxima_km=_parse_int(p.get("proxima_km")),
        status=str(p.get("status") or "Realizada").strip(),
        anexo_path=anexo,
        criado_por=_user(),
    )
    db.session.add(m)
    db.session.commit()
    return jsonify({"sucesso": True, "manutencao": _manut_dict(m)})


@frota_bp.route("/manutencoes/<int:mid>", methods=["PATCH"])
@permission_required(PERM)
def editar_manutencao(mid: int):
    m = db.session.get(FrotaManutencao, mid)
    if not m:
        return jsonify({"sucesso": False, "msg": "Não encontrada."}), 404
    p = request.get_json(silent=True) or {}
    for campo in ("tipo", "descricao", "fornecedor", "nota_fiscal", "status"):
        if campo in p:
            setattr(m, campo, (str(p[campo] or "").strip() or None))
    if "data" in p:
        m.data = _parse_date(p["data"]) or m.data
    if "proxima_data" in p:
        m.proxima_data = _parse_date(p["proxima_data"])
    if "km_atual" in p:
        m.km_atual = _parse_int(p["km_atual"])
    if "proxima_km" in p:
        m.proxima_km = _parse_int(p["proxima_km"])
    if "custo" in p:
        m.custo = _parse_float(p["custo"])
    db.session.commit()
    return jsonify({"sucesso": True, "manutencao": _manut_dict(m)})


@frota_bp.route("/manutencoes/<int:mid>", methods=["DELETE"])
@permission_required(PERM)
def excluir_manutencao(mid: int):
    m = db.session.get(FrotaManutencao, mid)
    if not m:
        return jsonify({"sucesso": False, "msg": "Não encontrada."}), 404
    db.session.delete(m)
    db.session.commit()
    return jsonify({"sucesso": True})


# ----------------------------------------------------------------------------- ABASTECIMENTOS
@frota_bp.route("/abastecimentos", methods=["GET"])
@permission_required(PERM)
def listar_abastecimentos():
    q = FrotaAbastecimento.query
    if request.args.get("veiculo_id"):
        q = q.filter_by(veiculo_id=_parse_int(request.args.get("veiculo_id")))
    regs = q.order_by(FrotaAbastecimento.data.desc()).limit(500).all()
    return jsonify([_abast_dict(a) for a in regs])


@frota_bp.route("/abastecimentos", methods=["POST"])
@permission_required(PERM)
def criar_abastecimento():
    p = request.get_json(silent=True) or {}
    veiculo_id = _parse_int(p.get("veiculo_id"))
    if not veiculo_id:
        return jsonify({"sucesso": False, "msg": "Veículo obrigatório."}), 400
    litros = _parse_float(p.get("litros"))
    km = _parse_int(p.get("km_atual"))
    if litros <= 0 or km is None:
        return jsonify({"sucesso": False, "msg": "KM e litros obrigatórios."}), 400
    valor_total = _parse_float(p.get("valor_total"))
    valor_litro = _parse_float(p.get("valor_litro"))
    if valor_total > 0 and valor_litro <= 0 and litros > 0:
        valor_litro = round(valor_total / litros, 3)
    if valor_litro > 0 and valor_total <= 0:
        valor_total = round(valor_litro * litros, 2)

    a = FrotaAbastecimento(
        veiculo_id=veiculo_id,
        motorista_id=_parse_int(p.get("motorista_id")),
        data=_parse_dt(p.get("data")) or datetime.now(),
        km_atual=km,
        litros=litros,
        valor_litro=valor_litro,
        valor_total=valor_total,
        combustivel=str(p.get("combustivel") or "Diesel").strip(),
        posto=str(p.get("posto") or "").strip() or None,
        tanque_cheio=bool(p.get("tanque_cheio", True)),
        observacao=str(p.get("observacao") or "").strip() or None,
        criado_por=_user(),
    )
    db.session.add(a)
    db.session.commit()
    return jsonify({"sucesso": True, "abastecimento": _abast_dict(a)})


@frota_bp.route("/abastecimentos/<int:aid>", methods=["DELETE"])
@permission_required(PERM)
def excluir_abastecimento(aid: int):
    a = db.session.get(FrotaAbastecimento, aid)
    if not a:
        return jsonify({"sucesso": False, "msg": "Não encontrado."}), 404
    db.session.delete(a)
    db.session.commit()
    return jsonify({"sucesso": True})


# ----------------------------------------------------------------------------- MULTAS
@frota_bp.route("/multas", methods=["GET"])
@permission_required(PERM)
def listar_multas():
    q = FrotaMulta.query
    if request.args.get("veiculo_id"):
        q = q.filter_by(veiculo_id=_parse_int(request.args.get("veiculo_id")))
    if request.args.get("status"):
        q = q.filter_by(status=request.args.get("status"))
    regs = q.order_by(FrotaMulta.data_infracao.desc()).all()
    return jsonify([_multa_dict(m) for m in regs])


@frota_bp.route("/multas", methods=["POST"])
@permission_required(PERM)
def criar_multa():
    if request.content_type and "multipart" in request.content_type:
        p = request.form.to_dict()
        anexo = _save_upload("anexo")
    else:
        p = request.get_json(silent=True) or {}
        anexo = None

    veiculo_id = _parse_int(p.get("veiculo_id"))
    if not veiculo_id:
        return jsonify({"sucesso": False, "msg": "Veículo obrigatório."}), 400
    descricao = str(p.get("descricao") or "").strip()
    if not descricao:
        return jsonify({"sucesso": False, "msg": "Descrição obrigatória."}), 400

    m = FrotaMulta(
        veiculo_id=veiculo_id,
        motorista_id=_parse_int(p.get("motorista_id")),
        auto_infracao=str(p.get("auto_infracao") or "").strip() or None,
        data_infracao=_parse_dt(p.get("data_infracao")) or datetime.now(),
        local=str(p.get("local") or "").strip() or None,
        descricao=descricao,
        valor=_parse_float(p.get("valor")),
        pontos=_parse_int(p.get("pontos"), 0) or 0,
        gravidade=str(p.get("gravidade") or "").strip() or None,
        vencimento=_parse_date(p.get("vencimento")),
        status=str(p.get("status") or "Pendente").strip(),
        observacao=str(p.get("observacao") or "").strip() or None,
        anexo_path=anexo,
        criado_por=_user(),
    )
    db.session.add(m)
    db.session.commit()
    return jsonify({"sucesso": True, "multa": _multa_dict(m)})


@frota_bp.route("/multas/<int:mid>", methods=["PATCH"])
@permission_required(PERM)
def editar_multa(mid: int):
    m = db.session.get(FrotaMulta, mid)
    if not m:
        return jsonify({"sucesso": False, "msg": "Não encontrada."}), 404
    p = request.get_json(silent=True) or {}
    for campo in ("status", "gravidade", "local", "descricao", "auto_infracao", "observacao"):
        if campo in p:
            setattr(m, campo, (str(p[campo] or "").strip() or None))
    if "valor" in p:
        m.valor = _parse_float(p["valor"])
    if "pontos" in p:
        m.pontos = _parse_int(p["pontos"], 0) or 0
    if "vencimento" in p:
        m.vencimento = _parse_date(p["vencimento"])
    if "data_infracao" in p:
        m.data_infracao = _parse_dt(p["data_infracao"]) or m.data_infracao
    db.session.commit()
    return jsonify({"sucesso": True, "multa": _multa_dict(m)})


@frota_bp.route("/multas/<int:mid>", methods=["DELETE"])
@permission_required(PERM)
def excluir_multa(mid: int):
    m = db.session.get(FrotaMulta, mid)
    if not m:
        return jsonify({"sucesso": False, "msg": "Não encontrada."}), 404
    db.session.delete(m)
    db.session.commit()
    return jsonify({"sucesso": True})


# ----------------------------------------------------------------------------- CHECKLIST
ITENS_CHECKLIST_PADRAO = [
    "Pneus", "Faróis", "Lanternas", "Freios", "Nível de óleo", "Nível de água",
    "Limpador de para-brisa", "Setas", "Estepe", "Triângulo/Macaco",
    "Documento do veículo", "CNH", "Extintor", "Cinto de segurança",
]


@frota_bp.route("/checklist/template", methods=["GET"])
@permission_required(PERM)
def checklist_template():
    return jsonify({"itens": ITENS_CHECKLIST_PADRAO})


@frota_bp.route("/checklist", methods=["GET"])
@permission_required(PERM)
def listar_checklist():
    q = FrotaChecklistDiario.query
    if request.args.get("veiculo_id"):
        q = q.filter_by(veiculo_id=_parse_int(request.args.get("veiculo_id")))
    d = _parse_date(request.args.get("data"))
    if d:
        inicio = datetime.combine(d, datetime.min.time())
        fim = datetime.combine(d, datetime.max.time())
        q = q.filter(FrotaChecklistDiario.data >= inicio, FrotaChecklistDiario.data <= fim)
    regs = q.order_by(FrotaChecklistDiario.data.desc()).limit(200).all()
    return jsonify([_check_dict(c) for c in regs])


@frota_bp.route("/checklist", methods=["POST"])
@permission_required(PERM)
def criar_checklist():
    p = request.get_json(silent=True) or {}
    veiculo_id = _parse_int(p.get("veiculo_id"))
    if not veiculo_id:
        return jsonify({"sucesso": False, "msg": "Veículo obrigatório."}), 400
    itens = p.get("itens") or []
    if not isinstance(itens, list) or not itens:
        return jsonify({"sucesso": False, "msg": "Itens do checklist obrigatórios."}), 400

    # calcula status geral
    n_nok = sum(1 for i in itens if str(i.get("status") or "").upper() == "NAO_OK")
    n_atn = sum(1 for i in itens if str(i.get("status") or "").upper() == "ATENCAO")
    status_geral = "BLOQUEADO" if n_nok > 0 else ("ATENCAO" if n_atn > 0 else "OK")

    c = FrotaChecklistDiario(
        veiculo_id=veiculo_id,
        motorista_id=_parse_int(p.get("motorista_id")),
        data=_parse_dt(p.get("data")) or datetime.now(),
        km_atual=_parse_int(p.get("km_atual")),
        itens_json=json.dumps(itens, ensure_ascii=False),
        status_geral=status_geral,
        observacao=str(p.get("observacao") or "").strip() or None,
        criado_por=_user(),
    )
    db.session.add(c)
    db.session.commit()
    return jsonify({"sucesso": True, "checklist": _check_dict(c)})


@frota_bp.route("/checklist/<int:cid>", methods=["DELETE"])
@permission_required(PERM)
def excluir_checklist(cid: int):
    c = db.session.get(FrotaChecklistDiario, cid)
    if not c:
        return jsonify({"sucesso": False, "msg": "Não encontrado."}), 404
    db.session.delete(c)
    db.session.commit()
    return jsonify({"sucesso": True})


# ----------------------------------------------------------------------------- CADASTROS (para selects)
@frota_bp.route("/cadastros", methods=["GET"])
@permission_required(PERM)
def cadastros():
    veiculos = AgendamentoVeiculo.query.filter_by(ativo=True).order_by(AgendamentoVeiculo.ordem_exibicao).all()
    motoristas = AgendamentoMotorista.query.filter_by(ativo=True).order_by(AgendamentoMotorista.nome).all()
    return jsonify({
        "veiculos": [{"id": v.id, "label": _veiculo_label(v), "placa": v.placa, "codigo": v.codigo} for v in veiculos],
        "motoristas": [{"id": m.id, "label": m.nome, "cnh": m.cnh} for m in motoristas],
    })
