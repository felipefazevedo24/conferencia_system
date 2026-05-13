"""Rotas HTTP para envio de NF-e por e-mail ao destinatario."""
from __future__ import annotations

from datetime import datetime

from flask import Blueprint, current_app, jsonify, render_template, request, session
from sqlalchemy import desc

from ..auth import roles_required
from ..extensions import db
from ..models import EmailNFEnviado
from ..services.nfe_email_service import enviar_nfe_por_email, resolver_destinatario
from ..services.erp_nfe_emitidas_service import listar_nfes_emitidas_erp, normalizar_data_minima
from ..services.planilhas_cadastros import buscar_email_por_cnpj

nfe_email_bp = Blueprint("nfe_email", __name__)


@nfe_email_bp.route("/api/nfe/email/destinatario-sugerido", methods=["POST"])
@roles_required("Fiscal", "Admin", "Conferente")
def api_nfe_email_destinatario_sugerido():
    payload = request.get_json(silent=True) or {}
    numero_nf = str(payload.get("numero_nf") or "").strip()
    chave = str(payload.get("chave") or "").strip()
    override_email = str(payload.get("email") or "").strip() or None
    if not numero_nf and not chave:
        return jsonify({"error": "Informe numero_nf ou chave."}), 400
    dados = resolver_destinatario(numero_nf, chave=chave or None, override_email=override_email)
    dados["modo_teste"] = bool(current_app.config.get("NFE_EMAIL_MODO_TESTE", True))
    dados["destino_teste"] = current_app.config.get("NFE_EMAIL_TESTE_DESTINO") if dados["modo_teste"] else None
    return jsonify(dados)


@nfe_email_bp.route("/api/nfe/email/enviar", methods=["POST"])
@roles_required("Fiscal", "Admin")
def api_nfe_email_enviar():
    payload = request.get_json(silent=True) or {}
    numero_nf = str(payload.get("numero_nf") or "").strip()
    chave = str(payload.get("chave") or "").strip() or None
    override_email = str(payload.get("email") or "").strip() or None
    cc_raw = payload.get("cc") or []
    if isinstance(cc_raw, str):
        cc_emails = [e.strip() for e in cc_raw.split(",") if e.strip()]
    elif isinstance(cc_raw, list):
        cc_emails = [str(e).strip() for e in cc_raw if str(e).strip()]
    else:
        cc_emails = []
    if not numero_nf:
        return jsonify({"error": "numero_nf e obrigatorio."}), 400

    conferencia_id = payload.get("conferencia_id")
    faturamento_id = payload.get("faturamento_id")
    try:
        conferencia_id = int(conferencia_id) if conferencia_id else None
    except (TypeError, ValueError):
        conferencia_id = None
    try:
        faturamento_id = int(faturamento_id) if faturamento_id else None
    except (TypeError, ValueError):
        faturamento_id = None

    resultado = enviar_nfe_por_email(
        numero_nf=numero_nf,
        chave=chave,
        override_email=override_email,
        cc_emails=cc_emails or None,
        disparado_por=session.get("username", "sistema"),
        origem="Manual",
        conferencia_id=conferencia_id,
        faturamento_id=faturamento_id,
    )
    code = 200 if resultado.get("sucesso") else 400
    return jsonify(resultado), code


@nfe_email_bp.route("/api/nfe/email/historico", methods=["GET"])
@roles_required("Fiscal", "Admin", "Conferente")
def api_nfe_email_historico():
    numero = (request.args.get("numero_nf") or "").strip()
    status = (request.args.get("status") or "").strip()
    origem = (request.args.get("origem") or "").strip()
    limite = request.args.get("limit", type=int) or 200
    limite = max(1, min(limite, 500))

    q = EmailNFEnviado.query
    if numero:
        q = q.filter(EmailNFEnviado.numero_nf.ilike(f"%{numero}%"))
    if status:
        q = q.filter(EmailNFEnviado.status == status)
    if origem:
        q = q.filter(EmailNFEnviado.origem == origem)
    rows = q.order_by(desc(EmailNFEnviado.criado_em)).limit(limite).all()
    return jsonify([
        {
            "id": r.id,
            "numero_nf": r.numero_nf,
            "chave_acesso": r.chave_acesso,
            "destinatario_email": r.destinatario_email,
            "destinatario_nome": r.destinatario_nome,
            "destinatario_cnpj": r.destinatario_cnpj,
            "cc_emails": r.cc_emails,
            "assunto": r.assunto,
            "fonte_email": r.fonte_email,
            "origem": r.origem,
            "status": r.status,
            "tentativas": r.tentativas,
            "erro_mensagem": r.erro_mensagem,
            "anexou_xml": r.anexou_xml,
            "anexou_pdf": r.anexou_pdf,
            "disparado_por": r.disparado_por,
            "criado_em": r.criado_em.isoformat() if r.criado_em else None,
            "enviado_em": r.enviado_em.isoformat() if r.enviado_em else None,
        }
        for r in rows
    ])


@nfe_email_bp.route("/api/nfe/email/<int:log_id>/reenviar", methods=["POST"])
@roles_required("Fiscal", "Admin")
def api_nfe_email_reenviar(log_id: int):
    log = db.session.get(EmailNFEnviado, log_id)
    if not log:
        return jsonify({"error": "Registro nao encontrado."}), 404
    resultado = enviar_nfe_por_email(
        numero_nf=log.numero_nf,
        chave=log.chave_acesso,
        override_email=log.destinatario_email or None,
        cc_emails=[e.strip() for e in (log.cc_emails or "").split(",") if e.strip()] or None,
        disparado_por=session.get("username", "sistema"),
        origem="Reenvio",
        conferencia_id=log.conferencia_id,
        faturamento_id=log.faturamento_id,
    )
    code = 200 if resultado.get("sucesso") else 400
    return jsonify(resultado), code


@nfe_email_bp.route("/api/nfe/email/config", methods=["GET", "POST"])
@roles_required("Admin")
def api_nfe_email_config():
    from ..services.nfe_email_config_store import salvar_persistido

    if request.method == "POST":
        payload = request.get_json(silent=True) or {}
        parcial: dict = {}
        if "modo_teste" in payload:
            parcial["NFE_EMAIL_MODO_TESTE"] = bool(payload["modo_teste"])
        if "destino_teste" in payload:
            parcial["NFE_EMAIL_TESTE_DESTINO"] = str(payload["destino_teste"] or "").strip()
        if "auto_no_faturamento" in payload:
            parcial["NFE_EMAIL_AUTO_NO_FATURAMENTO"] = bool(payload["auto_no_faturamento"])
        if "auto_enabled" in payload:
            parcial["NFE_EMAIL_AUTO_ENABLED"] = bool(payload["auto_enabled"])
        if "auto_desde" in payload:
            parcial["NFE_EMAIL_AUTO_DESDE"] = normalizar_data_minima(str(payload["auto_desde"] or "").strip())
        if "cc" in payload:
            valor = payload["cc"]
            if isinstance(valor, list):
                valor = ", ".join([str(e).strip() for e in valor if str(e).strip()])
            parcial["NFE_EMAIL_CC"] = str(valor or "").strip()
        if "poll_intervalo" in payload:
            try:
                parcial["NFE_EMAIL_POLL_INTERVAL_SECONDS"] = int(payload["poll_intervalo"])
            except (TypeError, ValueError):
                pass
        if "cfops_especiais" in payload:
            valor = payload["cfops_especiais"]
            if isinstance(valor, list):
                valor = ", ".join([str(c).strip() for c in valor if str(c).strip()])
            # Mantem apenas digitos por CFOP (CFOPs sao 4 digitos)
            import re as _re
            cfops = [
                c for c in _re.split(r"[,;\s]+", str(valor or ""))
                if c and c.strip().isdigit()
            ]
            parcial["NFE_EMAIL_CFOPS_ESPECIAIS"] = ", ".join(cfops)
        if "destinatarios_especiais" in payload:
            valor = payload["destinatarios_especiais"]
            if isinstance(valor, list):
                valor = ", ".join([str(e).strip() for e in valor if str(e).strip()])
            parcial["NFE_EMAIL_DESTINATARIOS_ESPECIAIS"] = str(valor or "").strip()
        if "entrada_chapa_enabled" in payload:
            parcial["ENTRADA_CHAPA_EMAIL_ENABLED"] = bool(payload["entrada_chapa_enabled"])
        if "entrada_chapa_destinatarios" in payload:
            valor = payload["entrada_chapa_destinatarios"]
            if isinstance(valor, list):
                valor = ", ".join([str(e).strip() for e in valor if str(e).strip()])
            parcial["ENTRADA_CHAPA_EMAIL_DESTINATARIOS"] = str(valor or "").strip()
        if "entrada_chapa_cc" in payload:
            valor = payload["entrada_chapa_cc"]
            if isinstance(valor, list):
                valor = ", ".join([str(e).strip() for e in valor if str(e).strip()])
            parcial["ENTRADA_CHAPA_EMAIL_CC"] = str(valor or "").strip()
        if "entrada_chapa_cfops" in payload:
            valor = payload["entrada_chapa_cfops"]
            if isinstance(valor, list):
                valor = ", ".join([str(c).strip() for c in valor if str(c).strip()])
            parcial["ENTRADA_CHAPA_CFOPS"] = str(valor or "").strip()
        if "entrada_chapa_controles" in payload:
            valor = payload["entrada_chapa_controles"]
            if isinstance(valor, list):
                valor = ", ".join([str(c).strip() for c in valor if str(c).strip()])
            parcial["ENTRADA_CHAPA_CONTROLE_LOTE_VALORES"] = str(valor or "").strip()
        salvar_persistido(parcial)

        # Se ligou/desligou auto_enabled, garante que o scheduler esta na situacao correta
        from ..services.nfe_email_scheduler import iniciar_scheduler, parar_scheduler
        if current_app.config.get("NFE_EMAIL_AUTO_ENABLED"):
            iniciar_scheduler(current_app._get_current_object())
        else:
            parar_scheduler()

    # Lê sempre do disco para evitar divergência entre workers (cada worker tem seu
    # próprio app.config em memória; sem isso, o worker que não salvou devolve valores
    # desatualizados logo após um POST de outro worker).
    from ..services.nfe_email_config_store import carregar_persistido
    carregar_persistido()  # sincroniza app.config deste worker com o JSON em disco

    return jsonify({
        "modo_teste": bool(current_app.config.get("NFE_EMAIL_MODO_TESTE", False)),
        "destino_teste": current_app.config.get("NFE_EMAIL_TESTE_DESTINO"),
        "auto_no_faturamento": bool(current_app.config.get("NFE_EMAIL_AUTO_NO_FATURAMENTO", True)),
        "auto_enabled": bool(current_app.config.get("NFE_EMAIL_AUTO_ENABLED", True)),
        "auto_desde": normalizar_data_minima(current_app.config.get("NFE_EMAIL_AUTO_DESDE")),
        "cc": current_app.config.get("NFE_EMAIL_CC", ""),
        "poll_intervalo": int(current_app.config.get("NFE_EMAIL_POLL_INTERVAL_SECONDS", 300)),
        "cfops_especiais": current_app.config.get("NFE_EMAIL_CFOPS_ESPECIAIS", ""),
        "destinatarios_especiais": current_app.config.get("NFE_EMAIL_DESTINATARIOS_ESPECIAIS", ""),
        "entrada_chapa_enabled": bool(current_app.config.get("ENTRADA_CHAPA_EMAIL_ENABLED", True)),
        "entrada_chapa_destinatarios": current_app.config.get("ENTRADA_CHAPA_EMAIL_DESTINATARIOS", ""),
        "entrada_chapa_cc": current_app.config.get("ENTRADA_CHAPA_EMAIL_CC", ""),
        "entrada_chapa_cfops": current_app.config.get("ENTRADA_CHAPA_CFOPS", "1901,1915"),
        "entrada_chapa_controles": current_app.config.get("ENTRADA_CHAPA_CONTROLE_LOTE_VALORES", "1,3"),
    })


@nfe_email_bp.route("/api/nfe/email/scheduler/status", methods=["GET"])
@roles_required("Admin", "Fiscal")
def api_nfe_email_scheduler_status():
    from ..services.nfe_email_scheduler import status_scheduler
    return jsonify(status_scheduler())


@nfe_email_bp.route("/api/nfe/email/scheduler/run-now", methods=["POST"])
@roles_required("Admin")
def api_nfe_email_scheduler_run_now():
    from ..services.nfe_email_scheduler import executar_ciclo
    resumo = executar_ciclo(current_app._get_current_object())
    return jsonify(resumo)


@nfe_email_bp.route("/faturamento/emails-nfe", methods=["GET"])
@roles_required("Fiscal", "Admin", "Conferente")
def pagina_emails_nfe():
    return render_template("emails_nfe.html")


@nfe_email_bp.route("/api/nfe/email/emitidas", methods=["GET"])
@roles_required("Fiscal", "Admin", "Conferente")
def api_nfe_email_emitidas():
    """Lista NFs emitidas no ERP a partir de 13/05/2026.

    Para cada NF retorna numero, chave, destinatario, e-mail sugerido (planilha)
    e se ja foi enviado por e-mail. O XML NAO e baixado aqui (performance);
    isso acontece no clique de "Enviar".
    """
    data_inicial = (request.args.get("data_inicial") or "").strip()
    if not data_inicial:
        data_inicial = "2026-05-13"

    # Valida data
    try:
        datetime.strptime(data_inicial, "%Y-%m-%d")
    except ValueError:
        return jsonify({"error": "data_inicial invalida (use YYYY-MM-DD)."}), 400
    data_inicial = normalizar_data_minima(data_inicial)

    try:
        documentos = listar_nfes_emitidas_erp(data_inicial)
    except Exception as exc:
        current_app.logger.exception("Falha ao consultar NF-e emitidas no ERP")
        return jsonify({"error": f"Falha ao consultar ERP bridge: {exc}"}), 502

    # Historico ja registrado (para marcar "ja enviado")
    enviados = {
        row.numero_nf: row
        for row in (
            EmailNFEnviado.query
            .filter(EmailNFEnviado.status == "Enviado")
            .order_by(desc(EmailNFEnviado.enviado_em))
            .all()
        )
    }
    # NFs aguardando e-mail manual (origem Auto sem e-mail encontrado)
    pendentes_manual = {
        row.numero_nf: row
        for row in (
            EmailNFEnviado.query
            .filter(EmailNFEnviado.status == "AguardandoManual")
            .order_by(desc(EmailNFEnviado.criado_em))
            .all()
        )
    }

    resultado = []
    from ..services.planilhas_cadastros import _somente_digitos

    for doc in documentos:
        chave = str(doc.get("chave") or "").strip()
        numero = str(doc.get("numero") or "").strip()
        if not chave or not numero:
            continue
        emitido_em = doc.get("emitido_em") or ""
        if emitido_em and emitido_em[:10] < data_inicial:
            continue

        cnpj_dest = _somente_digitos(doc.get("dest_cnpj"))
        nome_dest = str(doc.get("dest_nome") or "").strip()

        sugestao_email = ""
        sugestao_fonte = ""
        if cnpj_dest:
            hit = buscar_email_por_cnpj(cnpj_dest)
            if hit.get("email"):
                sugestao_email = hit["email"]
                sugestao_fonte = "Planilha"

        envio = enviados.get(numero)
        aguardando = pendentes_manual.get(numero) if not envio else None
        resultado.append({
            "numero": numero,
            "chave": chave,
            "emitido_em": emitido_em,
            "valor_total": doc.get("valor"),
            "emit_nome": doc.get("emit_nome") or "COLUMBIA MACHINE BRASIL",
            "dest_nome": nome_dest,
            "dest_cnpj": cnpj_dest,
            "autorizada": bool(doc.get("autorizada")),
            "tem_xml": bool(doc.get("xml_len")),
            "tem_pdf": bool(doc.get("pdf_len")),
            "status_nfe": doc.get("nfe_desc_status") or doc.get("nfe_cod_status") or "",
            "email_sugerido": sugestao_email,
            "fonte_sugestao": sugestao_fonte,
            "ja_enviado": bool(envio),
            "aguardando_manual": bool(aguardando),
            "ultimo_envio_em": envio.enviado_em.isoformat() if (envio and envio.enviado_em) else None,
            "ultimo_envio_para": envio.destinatario_email if envio else None,
        })

    resultado.sort(key=lambda r: r["emitido_em"] or "", reverse=True)
    return jsonify({
        "data_inicial": data_inicial,
        "total": len(resultado),
        "notas": resultado,
    })
