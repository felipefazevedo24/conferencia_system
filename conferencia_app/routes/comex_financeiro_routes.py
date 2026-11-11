"""Comex > Gestao financeira: provisao de custo da importacao (na instrucao
de embarque) x realizado (DUIMP/DIR + valores digitados). O Comex edita; a
Controladoria consulta (PAGE_COMEX_FINANCEIRO_CONSULTA). Regras em
services/comex_provisao_service.py."""
from __future__ import annotations

import json
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from io import BytesIO

from flask import Blueprint, current_app, jsonify, render_template, request, send_file, session

from ..auth import has_permission, is_admin_session, permission_required, permission_required_any
from ..extensions import db
from ..models import ComexProcesso, ComexProvisao
from ..services import comex_custo_service as custo
from ..services import comex_provisao_relatorio as relatorio
from ..services import comex_provisao_service as svc
from ..services.smtp_service import enviar_mensagem_smtp
from .comex_routes import _remetente_comex

comex_financeiro_bp = Blueprint("comex_financeiro", __name__)

PERM_EDITAR = "PAGE_COMEX"
PERM_CONSULTA = "PAGE_COMEX_FINANCEIRO_CONSULTA"
_ANEXOS = {"documento": ("documento_nome", "documento_arquivo"), "icms_guia": ("icms_guia_nome", "icms_guia_arquivo"),
           "frete_fatura": ("frete_fatura_nome", "frete_fatura_arquivo"), "despesas": ("despesas_nome", "despesas_arquivo")}


def _usuario() -> str:
    return session.get("username", "desconhecido")


def _processo(processo_id: int) -> ComexProcesso | None:
    return db.session.get(ComexProcesso, processo_id)


def _provisao_payload(p: ComexProvisao) -> dict:
    return {
        "id": p.id, "versao": p.versao, "modelo": p.modelo, "incoterm": p.incoterm, "ptax": p.ptax,
        "impostos_brl": p.impostos_brl, "frete_brl": p.frete_brl, "despesas_brl": p.despesas_brl,
        "criado_em": p.criado_em.strftime("%d/%m/%Y %H:%M"), "criado_por": p.criado_por,
        "email_enviado": p.email_enviado, "email_erro": p.email_erro,
        "parametros": json.loads(p.parametros or "{}"),
    }


def _realizado_payload(processo: ComexProcesso) -> dict | None:
    r = processo.realizado
    if not r:
        return None
    dados = {c: getattr(r, c) for c in svc.CAMPOS_REALIZADO_NUM}
    dados.update({
        "documento_tipo": r.documento_tipo, "documento_numero": r.documento_numero, "referencia_importador": r.referencia_importador,
        "atualizado_em": r.atualizado_em.strftime("%d/%m/%Y %H:%M"), "atualizado_por": r.atualizado_por,
        "anexos": {k: getattr(r, nome) for k, (nome, _) in _ANEXOS.items() if getattr(r, nome)},
        "totais": svc.totais_realizado(r),
    })
    return dados


def _enviar_email_controladoria(p: ComexProvisao) -> None:
    proc = p.processo
    msg = MIMEMultipart("mixed")
    msg["Subject"] = f"Provisão de custo de importação – {proc.id_op} (versão {p.versao})"
    remetente, smtp_kwargs = _remetente_comex()
    msg["From"] = remetente
    msg["To"] = svc.EMAIL_CONTROLADORIA
    total = (p.impostos_brl or 0) + (p.frete_brl or 0) + (p.despesas_brl or 0)
    corpo = (
        f"<p>Prezados,</p><p>Segue a provisão de custo da importação <strong>{proc.id_op}</strong> "
        f"({proc.fornecedor or ''}), versão {p.versao}, gerada por {p.criado_por}.</p>"
        f"<ul><li>Impostos e taxas: {relatorio._brl(p.impostos_brl)}</li>"
        f"<li>Frete internacional: {relatorio._brl(p.frete_brl)}</li>"
        f"<li>Despesas de desembaraço: {relatorio._brl(p.despesas_brl)}</li>"
        f"<li><strong>Total: {relatorio._brl(total)}</strong></li></ul>"
        f"<p>Detalhe por item no PDF anexo.</p><p>Comex – Columbia Machine Brasil</p>"
    )
    msg.attach(MIMEText(corpo, "html", "utf-8"))
    parte = MIMEApplication(relatorio.gerar_pdf(p), _subtype="pdf")
    parte.add_header("Content-Disposition", "attachment", filename=f"provisao-{proc.id_op}-v{p.versao}.pdf")
    msg.attach(parte)
    enviar_mensagem_smtp(current_app, msg, **smtp_kwargs)


@comex_financeiro_bp.route("/comex/gestao-financeira")
@permission_required_any(PERM_EDITAR, PERM_CONSULTA)
def page_gestao_financeira():
    return render_template("comex_gestao_financeira.html", user=session.get("username"),
                           pode_editar=is_admin_session() or has_permission(PERM_EDITAR), incoterms=svc.INCOTERMS)


@comex_financeiro_bp.route("/api/comex/financeiro")
@permission_required_any(PERM_EDITAR, PERM_CONSULTA)
def api_gestao():
    return jsonify({"processos": [svc.linha_gestao(p) for p in svc.processos_da_gestao()]})


@comex_financeiro_bp.route("/api/comex/processos/<int:processo_id>/financeiro")
@permission_required_any(PERM_EDITAR, PERM_CONSULTA)
def api_financeiro_processo(processo_id):
    processo = _processo(processo_id)
    if not processo:
        return jsonify({"error": "Processo não encontrado."}), 404
    cotacao = svc._cotacao_escolhida(processo)
    incoterm = (cotacao.incoterm or "").strip().upper() if cotacao else ""
    ultima = svc.vigente(processo)
    return jsonify({
        "linha": svc.linha_gestao(processo),
        "motivo": svc.motivo_sem_provisao(processo),
        "padroes": {**(json.loads(ultima.parametros) if ultima else {}),
                    "incoterm": ultima.incoterm if ultima else (incoterm if incoterm in svc.INCOTERMS else "")},
        "provisoes": [_provisao_payload(p) for p in reversed(processo.provisoes)],
        "realizado": _realizado_payload(processo),
    })


@comex_financeiro_bp.route("/api/comex/processos/<int:processo_id>/provisao/simular", methods=["POST"])
@permission_required(PERM_EDITAR)
def api_simular_provisao(processo_id):
    processo = _processo(processo_id)
    if not processo:
        return jsonify({"error": "Processo não encontrado."}), 404
    dados = request.get_json(silent=True) or {}
    if not dados.get("ptax"):
        ptax = custo.ptax_do_dia()
        dados["ptax"] = ptax["cotacao"] if ptax else None
    try:
        return jsonify({"ptax": dados["ptax"], "resultado": svc.calcular(processo, dados)})
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


@comex_financeiro_bp.route("/api/comex/processos/<int:processo_id>/provisao", methods=["POST"])
@permission_required(PERM_EDITAR)
def api_gravar_provisao(processo_id):
    processo = _processo(processo_id)
    if not processo:
        return jsonify({"error": "Processo não encontrado."}), 404
    try:
        p = svc.gravar(processo, request.get_json(silent=True) or {}, _usuario())
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    mensagem = f"Provisão versão {p.versao} gravada e enviada à Controladoria."
    try:
        _enviar_email_controladoria(p)
        p.email_enviado = True
    except Exception as exc:
        current_app.logger.exception("Comex: falha no e-mail da provisao %s", processo.id_op)
        p.email_enviado, p.email_erro = False, str(exc)[:500]
        mensagem = f"Provisão versão {p.versao} gravada, mas o e-mail à Controladoria NÃO foi enviado: {exc}"
    db.session.commit()
    return jsonify({"message": mensagem, "provisao": _provisao_payload(p)})


@comex_financeiro_bp.route("/api/comex/provisoes/<int:provisao_id>.<formato>")
@permission_required_any(PERM_EDITAR, PERM_CONSULTA)
def api_baixar_provisao(provisao_id, formato):
    p = db.session.get(ComexProvisao, provisao_id)
    if not p or formato not in ("pdf", "xlsx"):
        return jsonify({"error": "Provisão não encontrada."}), 404
    nome = f"provisao-{p.processo.id_op}-v{p.versao}.{formato}"
    if formato == "pdf":
        return send_file(BytesIO(relatorio.gerar_pdf(p)), mimetype="application/pdf", download_name=nome)
    return send_file(BytesIO(relatorio.gerar_xlsx(p)), as_attachment=True, download_name=nome,
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@comex_financeiro_bp.route("/api/comex/processos/<int:processo_id>/realizado", methods=["POST"])
@permission_required(PERM_EDITAR)
def api_salvar_realizado(processo_id):
    processo = _processo(processo_id)
    if not processo:
        return jsonify({"error": "Processo não encontrado."}), 404
    try:
        dados = json.loads(request.form.get("payload") or "{}")
    except ValueError:
        return jsonify({"error": "Dados inválidos."}), 400
    arquivos = {k: (f.filename, f.read()) for k, f in request.files.items() if k in _ANEXOS and f.filename}
    try:
        svc.salvar_realizado(processo, dados, arquivos, _usuario())
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"message": "Realizado salvo.", "realizado": _realizado_payload(processo), "linha": svc.linha_gestao(processo)})


@comex_financeiro_bp.route("/api/comex/processos/<int:processo_id>/realizado/<anexo>")
@permission_required_any(PERM_EDITAR, PERM_CONSULTA)
def api_baixar_anexo_realizado(processo_id, anexo):
    processo = _processo(processo_id)
    r = processo.realizado if processo else None
    if not r or anexo not in _ANEXOS or not getattr(r, _ANEXOS[anexo][1]):
        return jsonify({"error": "Anexo não encontrado."}), 404
    nome, arquivo = _ANEXOS[anexo]
    return send_file(BytesIO(getattr(r, arquivo)), download_name=getattr(r, nome), mimetype="application/octet-stream")
