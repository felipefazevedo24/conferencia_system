"""Rotas da Qualidade: análise de certificados no recebimento.

Fluxo: quando a conferência de uma NF de fornecedor monitorado (Brasimet, Metal
Paulista ou Friese) é finalizada, gera-se uma pendência aqui. O analista de
qualidade anexa a foto do certificado e preenche os dados da análise.
"""
import os
from datetime import datetime
from io import BytesIO

from flask import (
    Blueprint,
    current_app,
    jsonify,
    render_template,
    request,
    send_file,
    session,
)
from werkzeug.utils import secure_filename

from ..auth import has_permission, is_admin_session, login_required, permission_required
from ..extensions import db
from ..models import QualidadeCertificado
from ..services.qualidade_service import nota_elegivel_para_qualidade, notas_qualidade_visiveis_map


qualidade_bp = Blueprint("qualidade", __name__)

PERM = "PAGE_QUALIDADE"
PERM_APROVAR = "PAGE_QUALIDADE_APROVAR"
UPLOAD_SUB = "qualidade_certificados"
ALLOWED_EXTS = {"jpg", "jpeg", "png", "webp", "pdf"}
RESULTADOS_VALIDOS = {"Conforme", "Não Conforme"}

STATUS_PENDENTE = "Pendente de análise"
STATUS_EMITIDO = "Laudo emitido"
STATUS_APROVADO = "Laudo aprovado"
STATUS_SLUG = {
    STATUS_PENDENTE: "pendente",
    STATUS_EMITIDO: "emitido",
    STATUS_APROVADO: "aprovado",
}


def _pode_aprovar() -> bool:
    try:
        return bool(has_permission(PERM_APROVAR)) or is_admin_session()
    except Exception:
        return is_admin_session()


def _registro_visivel_por_cfop(registro: QualidadeCertificado) -> bool:
    return nota_elegivel_para_qualidade(registro.numero_nota)


def _upload_dir() -> str:
    d = os.path.join(current_app.instance_path, UPLOAD_SUB)
    os.makedirs(d, exist_ok=True)
    return d


def _save_foto(key: str = "foto") -> str | None:
    f = request.files.get(key)
    if not f or not f.filename:
        return None
    nome = secure_filename(f.filename)
    ext = nome.rsplit(".", 1)[-1].lower() if "." in nome else ""
    if ext not in ALLOWED_EXTS:
        return None
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    final = f"{stamp}_{nome}"
    f.save(os.path.join(_upload_dir(), final))
    return final


def _serialize(registro: QualidadeCertificado) -> dict:
    pode_aprovar = _pode_aprovar()
    usuario_atual = (session.get("username") or "").strip().casefold()
    analista = (registro.analista or "").strip().casefold()
    mesmo_usuario_que_emitiu = bool(usuario_atual and analista and usuario_atual == analista)
    bloqueio_aprovacao_motivo = ""
    if mesmo_usuario_que_emitiu and registro.status == STATUS_EMITIDO:
        bloqueio_aprovacao_motivo = "Compliance (4 olhos): o emissor do laudo não pode ser o aprovador."
    return {
        "id": registro.id,
        "numero_nota": registro.numero_nota,
        "chave_acesso": registro.chave_acesso or "",
        "fornecedor": registro.fornecedor or "",
        "numero_orcamento": registro.numero_orcamento or "",
        "numero_certificado": registro.numero_certificado or "",
        "os": registro.os or "",
        "grid_os": registro.grid_os or registro.os or "",
        "grid_dureza": registro.grid_dureza or "",
        "grid_chd": registro.grid_chd or "",
        "grid_resultado": registro.grid_resultado or "",
        "sapatas_os": registro.sapatas_os or registro.os or "",
        "sapatas_dureza": registro.sapatas_dureza or "",
        "sapatas_chd": registro.sapatas_chd or "",
        "sapatas_resultado": registro.sapatas_resultado or "",
        "status": registro.status,
        "status_slug": STATUS_SLUG.get(registro.status, "pendente"),
        "analista": registro.analista or "",
        "aprovado_por": registro.aprovado_por or "",
        "tem_foto": bool(registro.foto_path),
        "criado_em": registro.criado_em.strftime("%d/%m/%Y %H:%M") if registro.criado_em else "",
        "analisado_em": registro.analisado_em.strftime("%d/%m/%Y %H:%M") if registro.analisado_em else "",
        "aprovado_em": registro.aprovado_em.strftime("%d/%m/%Y %H:%M") if registro.aprovado_em else "",
        # Regras de ação
        "pode_editar": registro.status in (STATUS_PENDENTE, STATUS_EMITIDO),
        "pode_excluir": registro.status == STATUS_EMITIDO,
        "pode_aprovar": pode_aprovar and registro.status == STATUS_EMITIDO and not mesmo_usuario_que_emitiu,
        "bloqueio_aprovacao_motivo": bloqueio_aprovacao_motivo,
        "tem_laudo": registro.status in (STATUS_EMITIDO, STATUS_APROVADO),
    }


@qualidade_bp.route("/qualidade")
@permission_required(PERM)
def qualidade_page():
    return render_template(
        "qualidade.html",
        user=session["username"],
        user_role=session.get("role", ""),
        is_admin=is_admin_session(),
        pode_aprovar=_pode_aprovar(),
    )


@qualidade_bp.route("/api/qualidade/certificados")
@permission_required(PERM)
def api_listar_certificados():
    status = (request.args.get("status") or "").strip()
    query = QualidadeCertificado.query
    if status and status.lower() != "todos":
        query = query.filter_by(status=status)
    rows = query.order_by(QualidadeCertificado.criado_em.desc()).limit(500).all()
    vis_map = notas_qualidade_visiveis_map([r.numero_nota for r in rows])
    rows = [r for r in rows if vis_map.get(r.numero_nota, False)]

    # Métricas por status considerando somente NFs elegíveis pela regra de CFOP.
    metricas = {"pendente": 0, "emitido": 0, "aprovado": 0}
    metrica_rows = db.session.query(QualidadeCertificado.numero_nota, QualidadeCertificado.status).all()
    metrica_vis = notas_qualidade_visiveis_map([n for n, _ in metrica_rows])
    for numero_nota, st in metrica_rows:
        if not metrica_vis.get(numero_nota, False):
            continue
        slug = STATUS_SLUG.get(st)
        if slug:
            metricas[slug] += 1

    return jsonify({
        "rows": [_serialize(r) for r in rows],
        "metricas": metricas,
        "pode_aprovar": _pode_aprovar(),
    })


@qualidade_bp.route("/api/qualidade/certificados/<int:id>")
@permission_required(PERM)
def api_obter_certificado(id):
    registro = QualidadeCertificado.query.get_or_404(id)
    if not _registro_visivel_por_cfop(registro):
        return jsonify({"error": "NF fora do escopo de CFOP do módulo Qualidade."}), 404
    return jsonify(_serialize(registro))


@qualidade_bp.route("/api/qualidade/certificados/<int:id>/foto")
@permission_required(PERM)
def api_obter_foto(id):
    registro = QualidadeCertificado.query.get_or_404(id)
    if not _registro_visivel_por_cfop(registro):
        return jsonify({"error": "NF fora do escopo de CFOP do módulo Qualidade."}), 404
    if not registro.foto_path:
        return jsonify({"error": "Sem foto do certificado."}), 404
    caminho = os.path.join(_upload_dir(), registro.foto_path)
    if not os.path.isfile(caminho):
        return jsonify({"error": "Arquivo não encontrado."}), 404
    return send_file(caminho)


@qualidade_bp.route("/api/qualidade/certificados/<int:id>/analisar", methods=["POST"])
@permission_required(PERM)
def api_analisar_certificado(id):
    registro = QualidadeCertificado.query.get_or_404(id)
    if not _registro_visivel_por_cfop(registro):
        return jsonify({"error": "NF fora do escopo de CFOP do módulo Qualidade."}), 404
    user = session.get("username") or ""

    if registro.status not in (STATUS_PENDENTE, STATUS_EMITIDO):
        return jsonify({"error": "Este laudo já foi aprovado e não pode ser editado."}), 409

    # Aceita multipart (com foto) ou JSON (sem alterar foto).
    if request.content_type and "multipart/form-data" in request.content_type:
        dados = request.form
    else:
        dados = request.get_json(silent=True) or {}

    numero_certificado = (dados.get("numero_certificado") or "").strip()
    numero_orcamento = (dados.get("numero_orcamento") or "").strip()
    os_lote = (dados.get("os") or "").strip()
    grid_os = (dados.get("grid_os") or os_lote or "").strip()
    sapatas_os = (dados.get("sapatas_os") or os_lote or "").strip()
    grid_dureza = (dados.get("grid_dureza") or "").strip()
    grid_chd = (dados.get("grid_chd") or "").strip()
    grid_resultado = (dados.get("grid_resultado") or "").strip()
    sapatas_dureza = (dados.get("sapatas_dureza") or "").strip()
    sapatas_chd = (dados.get("sapatas_chd") or "").strip()
    sapatas_resultado = (dados.get("sapatas_resultado") or "").strip()

    grid_preenchido = bool(grid_resultado or grid_dureza or grid_chd)
    sapatas_preenchido = bool(sapatas_resultado or sapatas_dureza or sapatas_chd)

    if not grid_preenchido and not sapatas_preenchido:
        return jsonify({"error": "Informe pelo menos um componente (Grid ou Sapatas)."}), 400

    obrigatorios = {
        "Orçamento nº": numero_orcamento,
    }
    if grid_preenchido:
        if grid_resultado not in RESULTADOS_VALIDOS:
            return jsonify({"error": "Selecione o resultado do Grid: Conforme ou Não Conforme."}), 400
        obrigatorios["OS / Lote-CP (Grid)"] = grid_os
        obrigatorios["Dureza medida (Grid)"] = grid_dureza
        obrigatorios["CHD medida (Grid)"] = grid_chd
    if sapatas_preenchido:
        if sapatas_resultado not in RESULTADOS_VALIDOS:
            return jsonify({"error": "Selecione o resultado das Sapatas: Conforme ou Não Conforme."}), 400
        obrigatorios["OS / Lote-CP (Sapatas)"] = sapatas_os
        obrigatorios["Dureza medida (Sapatas)"] = sapatas_dureza
        obrigatorios["CHD medida (Sapatas)"] = sapatas_chd

    faltando = [rotulo for rotulo, valor in obrigatorios.items() if not valor]
    if faltando:
        return jsonify({"error": "Preencha os campos obrigatórios: " + ", ".join(faltando)}), 400

    # Foto é opcional (nice to have).
    nova_foto = _save_foto("foto")
    if nova_foto:
        registro.foto_path = nova_foto

    registro.numero_orcamento = numero_orcamento[:120]
    registro.numero_certificado = numero_certificado[:120] or None
    registro.grid_os = grid_os[:120] if grid_preenchido else None
    registro.sapatas_os = sapatas_os[:120] if sapatas_preenchido else None
    registro.os = (registro.grid_os or registro.sapatas_os or os_lote[:120] or None)
    registro.grid_dureza = grid_dureza[:120] if grid_preenchido else None
    registro.grid_chd = grid_chd[:120] if grid_preenchido else None
    registro.grid_resultado = grid_resultado if grid_preenchido else None
    registro.sapatas_dureza = sapatas_dureza[:120] if sapatas_preenchido else None
    registro.sapatas_chd = sapatas_chd[:120] if sapatas_preenchido else None
    registro.sapatas_resultado = sapatas_resultado if sapatas_preenchido else None
    registro.analista = user
    registro.status = STATUS_EMITIDO
    registro.analisado_em = datetime.now()

    db.session.commit()
    return jsonify({"sucesso": True, "msg": "Laudo emitido com sucesso.", "registro": _serialize(registro)})


@qualidade_bp.route("/api/qualidade/certificados/<int:id>/excluir-laudo", methods=["POST"])
@permission_required(PERM)
def api_excluir_laudo(id):
    """Exclui o laudo emitido e devolve a NF para a fila de Pendente de análise."""
    registro = QualidadeCertificado.query.get_or_404(id)
    if not _registro_visivel_por_cfop(registro):
        return jsonify({"error": "NF fora do escopo de CFOP do módulo Qualidade."}), 404
    if registro.status != STATUS_EMITIDO:
        return jsonify({"error": "Só é possível excluir laudos que estejam em 'Laudo emitido'."}), 409

    registro.numero_certificado = None
    registro.numero_orcamento = None
    registro.os = None
    registro.grid_os = None
    registro.grid_dureza = registro.grid_chd = registro.grid_resultado = None
    registro.sapatas_os = None
    registro.sapatas_dureza = registro.sapatas_chd = registro.sapatas_resultado = None
    registro.foto_path = None
    registro.analista = None
    registro.analisado_em = None
    registro.aprovado_em = None
    registro.aprovado_por = None
    registro.status = STATUS_PENDENTE

    db.session.commit()
    return jsonify({"sucesso": True, "msg": "Laudo excluído. NF devolvida para Pendente de análise.", "registro": _serialize(registro)})


@qualidade_bp.route("/api/qualidade/certificados/<int:id>/aprovar", methods=["POST"])
@permission_required(PERM)
def api_aprovar_laudo(id):
    if not _pode_aprovar():
        return jsonify({"error": "Apenas supervisor/gerente pode aprovar o laudo."}), 403
    registro = QualidadeCertificado.query.get_or_404(id)
    if not _registro_visivel_por_cfop(registro):
        return jsonify({"error": "NF fora do escopo de CFOP do módulo Qualidade."}), 404
    if registro.status != STATUS_EMITIDO:
        return jsonify({"error": "Só é possível aprovar laudos emitidos."}), 409

    aprovador = (session.get("username") or "").strip()
    if aprovador and (registro.analista or "").strip().casefold() == aprovador.casefold():
        return jsonify({
            "error": "Compliance (4 olhos): quem emitiu o laudo não pode aprovar o mesmo laudo."
        }), 409

    registro.status = STATUS_APROVADO
    registro.aprovado_por = aprovador
    registro.aprovado_em = datetime.now()
    db.session.commit()
    return jsonify({"sucesso": True, "msg": "Laudo aprovado.", "registro": _serialize(registro)})


@qualidade_bp.route("/api/qualidade/certificados/<int:id>/laudo.pdf")
@permission_required(PERM)
def api_laudo_pdf(id):
    from ..services.qualidade_laudo_pdf import gerar_laudo_pdf

    registro = QualidadeCertificado.query.get_or_404(id)
    if not _registro_visivel_por_cfop(registro):
        return jsonify({"error": "NF fora do escopo de CFOP do módulo Qualidade."}), 404
    if registro.status not in (STATUS_EMITIDO, STATUS_APROVADO):
        return jsonify({"error": "Emita o laudo antes de gerar o PDF."}), 400

    pdf_bytes = gerar_laudo_pdf(registro)
    nome = f"laudo_qualidade_NF_{registro.numero_nota}.pdf"
    return send_file(
        BytesIO(pdf_bytes),
        mimetype="application/pdf",
        as_attachment=False,
        download_name=nome,
    )

