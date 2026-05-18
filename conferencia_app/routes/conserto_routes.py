from datetime import datetime

from flask import Blueprint, jsonify, render_template, request, session
from sqlalchemy import func

from ..auth import permission_required
from ..extensions import db
from ..models import ConsertoAuditoria, ConsertoBaixa, ConsertoEstoque, ItemNota
from ..services.conserto_service import ConsertoService

conserto_bp = Blueprint("conserto", __name__)
SYNC_JOBS = {}


def _parse_iso_date(value):
    raw = (value or "").strip()
    if not raw:
        raise ValueError("Data não informada.")
    if len(raw) == 10:
        return datetime.strptime(raw, "%Y-%m-%d")
    return datetime.fromisoformat(raw.replace("Z", ""))


def _contexto_tela(tab_ativa):
    return {"tab_ativa": tab_ativa}


def _mapa_numeros_por_chave(chaves):
    chaves_validas = [str(chave).strip() for chave in (chaves or []) if str(chave or "").strip()]
    if not chaves_validas:
        return {}

    rows = (
        db.session.query(ItemNota.chave_acesso, func.max(ItemNota.numero_nota))
        .filter(ItemNota.chave_acesso.in_(chaves_validas))
        .group_by(ItemNota.chave_acesso)
        .all()
    )
    return {str(chave or "").strip(): str(numero or "").strip() for chave, numero in rows}


@conserto_bp.route("/conserto", methods=["GET"])
@conserto_bp.route("/conserto/estoque", methods=["GET"])
@conserto_bp.route("/conserto/estoque_aberto", methods=["GET"])
@permission_required("PAGE_CONSERTO")
def tela_central_conserto():
    return render_template("conserto_central.html", **_contexto_tela("processos"))


@conserto_bp.route("/conserto/confirmacao_baixa", methods=["GET"])
@permission_required("PAGE_CONSERTO")
def tela_confirmacao_baixa():
    return render_template("conserto_central.html", **_contexto_tela("baixa"))


@conserto_bp.route("/conserto/auditoria_tela", methods=["GET"])
@permission_required("PAGE_CONSERTO")
def tela_auditoria_conserto():
    return render_template("conserto_central.html", **_contexto_tela("auditoria"))


@conserto_bp.route("/conserto/auditoria", methods=["GET"])
@permission_required("PAGE_CONSERTO")
def auditoria_conserto():
    auditorias = ConsertoAuditoria.query.order_by(ConsertoAuditoria.data_hora.desc()).limit(100).all()
    result = [
        {
            "id": auditoria.id,
            "acao": auditoria.acao,
            "referencia_id": auditoria.referencia_id,
            "referencia_tipo": auditoria.referencia_tipo,
            "usuario": auditoria.usuario,
            "data_hora": auditoria.data_hora.isoformat(),
            "detalhes": auditoria.detalhes,
        }
        for auditoria in auditorias
    ]
    return jsonify(result)


@conserto_bp.route("/conserto/processos", methods=["GET"])
@permission_required("PAGE_CONSERTO")
def processos_conserto():
    return jsonify(ConsertoService.listar_processos_conserto())


@conserto_bp.route("/conserto/baixas_pendentes", methods=["GET"])
@permission_required("PAGE_CONSERTO")
def baixas_pendentes():
    items = (
        db.session.query(ConsertoBaixa, ConsertoEstoque)
        .join(ConsertoEstoque, ConsertoBaixa.conserto_estoque_id == ConsertoEstoque.id)
        .filter(ConsertoBaixa.status_baixa.in_(["Pendente de confirmacao", "Pendente de confirmação"]))
        .order_by(ConsertoBaixa.id.desc())
        .all()
    )

    chaves = [estoque.chave_nf_remessa for _, estoque in items] + [baixa.chave_nf_retorno for baixa, _ in items]
    mapa_numeros = _mapa_numeros_por_chave(chaves)

    pendentes = [
        {
            "baixa_id": baixa.id,
            "tipo_controle": estoque.tipo_controle or ConsertoService.TIPO_CONTROLE_PADRAO,
            "tipo_operacao": estoque.tipo_operacao or "Conserto",
            "cfop_remessa": estoque.cfop_remessa,
            "cfop_retorno": baixa.cfop_retorno,
            "chave_nf_remessa": estoque.chave_nf_remessa,
            "numero_nf_remessa": estoque.numero_nf_remessa or mapa_numeros.get(estoque.chave_nf_remessa, ""),
            "chave_nf_retorno": baixa.chave_nf_retorno,
            "numero_nf_retorno": baixa.numero_nf_retorno or mapa_numeros.get(baixa.chave_nf_retorno, ""),
            "produto_codigo": estoque.produto_codigo,
            "produto_descricao": estoque.produto_descricao,
            "quantidade_baixada": baixa.quantidade_baixada,
            "tipo_vinculo": baixa.tipo_vinculo,
            "status_baixa": baixa.status_baixa,
            "observacoes": baixa.observacoes,
        }
        for baixa, estoque in items
    ]
    return jsonify(pendentes)


@conserto_bp.route("/conserto/saldos_abertos", methods=["GET"])
@permission_required("PAGE_CONSERTO")
def saldos_abertos():
    saldos = (
        ConsertoEstoque.query.filter(ConsertoEstoque.quantidade_saldo > 0)
        .order_by(ConsertoEstoque.data_emissao.desc(), ConsertoEstoque.id.desc())
        .all()
    )
    mapa_numeros = _mapa_numeros_por_chave([saldo.chave_nf_remessa for saldo in saldos])
    result = [
        {
            "id": saldo.id,
            "tipo_controle": saldo.tipo_controle or ConsertoService.TIPO_CONTROLE_PADRAO,
            "tipo_operacao": saldo.tipo_operacao or "Conserto",
            "cfop_remessa": saldo.cfop_remessa,
            "chave_nf_remessa": saldo.chave_nf_remessa,
            "numero_nf_remessa": saldo.numero_nf_remessa or mapa_numeros.get(saldo.chave_nf_remessa, ""),
            "fornecedor_cnpj": saldo.fornecedor_cnpj,
            "fornecedor_nome": saldo.fornecedor_nome,
            "produto_codigo": saldo.produto_codigo,
            "produto_descricao": saldo.produto_descricao,
            "quantidade_enviada": saldo.quantidade_enviada,
            "quantidade_saldo": saldo.quantidade_saldo,
            "status": saldo.status,
            "data_emissao": saldo.data_emissao.isoformat(),
        }
        for saldo in saldos
    ]
    return jsonify(result)


@conserto_bp.route("/conserto/historico_estoque", methods=["GET"])
@permission_required("PAGE_CONSERTO")
def historico_estoque():
    historico = ConsertoService.listar_historico_estoque()
    mapa_numeros = _mapa_numeros_por_chave([item["chave_nf_remessa"] for item in historico])
    result = []
    for item in historico:
        payload = dict(item)
        payload["numero_nf_remessa"] = item.get("numero_nf_remessa") or mapa_numeros.get(item["chave_nf_remessa"], "")
        result.append(payload)
    return jsonify(result)


@conserto_bp.route("/conserto/relatorio_estoque", methods=["GET"])
@permission_required("PAGE_CONSERTO")
def relatorio_estoque():
    return jsonify(ConsertoService.montar_relatorio_estoque())


@conserto_bp.route("/conserto/sincronizar_notas", methods=["POST"])
@permission_required("PAGE_CONSERTO")
def sincronizar_notas():
    usuario = session.get("username", "sistema")
    data_inicial = datetime(datetime.now().year, 3, 1)
    logs = [f"Sincronizacao iniciada a partir de {data_inicial.strftime('%Y-%m-%d')}."]
    try:
        resumo = ConsertoService.sincronizar_notas_fiscais(
            usuario,
            data_inicial=data_inicial,
            debug_callback=lambda msg: logs.append(msg),
        )
        return jsonify({"resumo": resumo, "logs": logs, "msg": "Sincronizacao concluida."}), 200
    except Exception as exc:
        db.session.rollback()
        logs.append(f"Erro: {str(exc)}")
        return jsonify({"error": str(exc), "logs": logs}), 500


@conserto_bp.route("/conserto/sincronizar_notas/<job_id>", methods=["GET"])
@permission_required("PAGE_CONSERTO")
def status_sincronizacao_notas(job_id):
    job = SYNC_JOBS.get(str(job_id))
    if not job:
        return jsonify({"error": "Sincronizacao não encontrada."}), 404
    return jsonify(job), 200


@conserto_bp.route("/conserto/criar_saldo", methods=["POST"])
@permission_required("PAGE_CONSERTO")
def criar_saldo():
    data = request.get_json(silent=True) or {}
    try:
        saldo = ConsertoService.criar_saldo_remessa(
            chave_nf_remessa=data["chave_nf_remessa"],
            data_emissao=_parse_iso_date(data["data_emissao"]),
            fornecedor_cnpj=data["fornecedor_cnpj"],
            fornecedor_nome=data["fornecedor_nome"],
            produto_codigo=data["produto_codigo"],
            produto_descricao=data["produto_descricao"],
            quantidade=data["quantidade"],
            tipo_operacao=data.get("tipo_operacao"),
            tipo_controle=data.get("tipo_controle"),
            cfop_remessa=data.get("cfop_remessa"),
            usuario=session.get("username", "sistema"),
        )
        return jsonify({"id": saldo.id, "msg": "Saldo criado com sucesso"}), 201
    except (KeyError, ValueError) as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        db.session.rollback()
        return jsonify({"error": str(exc)}), 400


@conserto_bp.route("/conserto/vinculo_manual", methods=["POST"])
@permission_required("PAGE_CONSERTO")
def vinculo_manual():
    data = request.get_json(silent=True) or {}
    try:
        baixa = ConsertoService.vinculo_manual(
            conserto_estoque_id=data["conserto_estoque_id"],
            chave_nf_retorno=data["chave_nf_retorno"],
            data_nf_retorno=_parse_iso_date(data["data_nf_retorno"]),
            quantidade=data["quantidade"],
            usuario=session.get("username", "sistema"),
            observacoes=data.get("observacoes"),
            cfop_retorno=data.get("cfop_retorno"),
            numero_nf_retorno=data.get("numero_nf_retorno"),
        )
        return jsonify({"id": baixa.id, "msg": "Vinculo manual registrado"}), 200
    except (KeyError, ValueError) as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        db.session.rollback()
        return jsonify({"error": str(exc)}), 400


@conserto_bp.route("/conserto/consultar_retorno_manual", methods=["POST"])
@permission_required("PAGE_CONSERTO")
def consultar_retorno_manual():
    data = request.get_json(silent=True) or {}
    try:
        payload = ConsertoService.consultar_retorno_manual_por_numero(
            numero_nf_retorno=data["numero_nf_retorno"],
            conserto_estoque_id=data["conserto_estoque_id"],
        )
        return jsonify(payload), 200
    except (KeyError, ValueError) as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        db.session.rollback()
        return jsonify({"error": str(exc)}), 400


@conserto_bp.route("/conserto/confirmar_baixa", methods=["POST"])
@permission_required("PAGE_CONSERTO")
def confirmar_baixa():
    data = request.get_json(silent=True) or {}
    if "baixa_id" not in data:
        return jsonify({"error": "ID da baixa não fornecido"}), 400

    try:
        baixa = ConsertoService.confirmar_baixa(
            baixa_id=data["baixa_id"],
            usuario=session.get("username", "sistema"),
            observacoes=data.get("observacoes"),
        )
        if not baixa:
            return jsonify({"error": "Baixa não encontrada ou já confirmada"}), 404
        return jsonify({"id": baixa.id, "msg": "Baixa confirmada"}), 200
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        db.session.rollback()
        return jsonify({"error": str(exc)}), 400
