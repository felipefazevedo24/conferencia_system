"""Rotas da Qualidade: análise de certificados no recebimento.

Fluxo: quando a conferência de uma NF de fornecedor monitorado (Brasimet, Metal
Paulista ou Friese) é finalizada, gera-se uma pendência aqui. O analista de
qualidade anexa a foto do certificado e preenche os dados da análise.
"""
import os
import json
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
from ..models import QualidadeCertificado, QualidadeCertificadoComponente
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

TIPOS_COMPONENTE = {"Grid", "Sapatas"}


def _componentes_legados(registro: QualidadeCertificado) -> list[dict]:
    componentes = []
    if registro.grid_resultado or registro.grid_dureza or registro.grid_chd or registro.grid_os or registro.grid_numero_certificado:
        componentes.append(
            {
                "tipo": "Grid",
                "os": registro.grid_os or registro.os_referencia or registro.os or "",
                "numero_certificado": registro.grid_numero_certificado or registro.numero_certificado or "",
                "dureza": registro.grid_dureza or "",
                "chd": registro.grid_chd or "",
                "resultado": registro.grid_resultado or "",
            }
        )
    if registro.sapatas_resultado or registro.sapatas_dureza or registro.sapatas_chd or registro.sapatas_os or registro.sapatas_numero_certificado:
        componentes.append(
            {
                "tipo": "Sapatas",
                "os": registro.sapatas_os or registro.os_referencia or registro.os or "",
                "numero_certificado": registro.sapatas_numero_certificado or registro.numero_certificado or "",
                "dureza": registro.sapatas_dureza or "",
                "chd": registro.sapatas_chd or "",
                "resultado": registro.sapatas_resultado or "",
            }
        )
    return componentes


def _componentes_do_registro(registro: QualidadeCertificado) -> list[dict]:
    if registro.componentes:
        return [
            {
                "id": comp.id,
                "tipo": comp.tipo or "Grid",
                "os": comp.os or "",
                "numero_certificado": comp.numero_certificado or "",
                "dureza": comp.dureza or "",
                "chd": comp.chd or "",
                "resultado": comp.resultado or "",
            }
            for comp in sorted(registro.componentes, key=lambda c: (c.ordem or 0, c.id or 0))
        ]
    return _componentes_legados(registro)


def _sincronizar_campos_legados(registro: QualidadeCertificado, componentes: list[dict]) -> None:
    grids = [c for c in componentes if c.get("tipo") == "Grid"]
    sapatas = [c for c in componentes if c.get("tipo") == "Sapatas"]

    primeiro_grid = grids[0] if grids else None
    primeiro_sapata = sapatas[0] if sapatas else None
    primeiro_cert = next((str(c.get("numero_certificado") or "").strip() for c in componentes if str(c.get("numero_certificado") or "").strip()), "")
    primeiro_os = next((str(c.get("os") or "").strip() for c in componentes if str(c.get("os") or "").strip()), "")

    registro.numero_certificado = primeiro_cert[:120] or None
    registro.os = primeiro_os[:120] or None

    registro.grid_os = (primeiro_grid or {}).get("os", "")[:120] or None
    registro.grid_numero_certificado = (primeiro_grid or {}).get("numero_certificado", "")[:120] or None
    registro.grid_dureza = (primeiro_grid or {}).get("dureza", "")[:120] or None
    registro.grid_chd = (primeiro_grid or {}).get("chd", "")[:120] or None
    registro.grid_resultado = (primeiro_grid or {}).get("resultado") or None

    registro.sapatas_os = (primeiro_sapata or {}).get("os", "")[:120] or None
    registro.sapatas_numero_certificado = (primeiro_sapata or {}).get("numero_certificado", "")[:120] or None
    registro.sapatas_dureza = (primeiro_sapata or {}).get("dureza", "")[:120] or None
    registro.sapatas_chd = (primeiro_sapata or {}).get("chd", "")[:120] or None
    registro.sapatas_resultado = (primeiro_sapata or {}).get("resultado") or None


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
    componentes = _componentes_do_registro(registro)
    return {
        "id": registro.id,
        "numero_nota": registro.numero_nota,
        "os_referencia": registro.os_referencia or registro.os or "",
        "chave_acesso": registro.chave_acesso or "",
        "fornecedor": registro.fornecedor or "",
        "numero_orcamento": registro.numero_orcamento or "",
        "numero_certificado": registro.numero_certificado or "",
        "os": registro.os or registro.os_referencia or "",
        "grid_os": registro.grid_os or registro.os_referencia or registro.os or "",
        "grid_numero_certificado": registro.grid_numero_certificado or registro.numero_certificado or "",
        "grid_dureza": registro.grid_dureza or "",
        "grid_chd": registro.grid_chd or "",
        "grid_resultado": registro.grid_resultado or "",
        "sapatas_os": registro.sapatas_os or registro.os_referencia or registro.os or "",
        "sapatas_numero_certificado": registro.sapatas_numero_certificado or registro.numero_certificado or "",
        "sapatas_dureza": registro.sapatas_dureza or "",
        "sapatas_chd": registro.sapatas_chd or "",
        "sapatas_resultado": registro.sapatas_resultado or "",
        "status": registro.status,
        "status_slug": STATUS_SLUG.get(registro.status, "pendente"),
        "componentes": componentes,
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
    rows = query.order_by(QualidadeCertificado.criado_em.desc(), QualidadeCertificado.os_referencia.asc()).limit(500).all()
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

    numero_orcamento = (dados.get("numero_orcamento") or "").strip()
    os_referencia = str(registro.os_referencia or registro.os or "").strip()
    componentes_raw = dados.get("componentes")
    if componentes_raw is None:
        componentes_raw = dados.get("componentes_json")

    componentes = []
    if isinstance(componentes_raw, str):
        try:
            componentes = json.loads(componentes_raw)
        except Exception:
            componentes = []
    elif isinstance(componentes_raw, list):
        componentes = componentes_raw

    if not componentes:
        componentes = _componentes_legados(registro)

    componentes_validos: list[dict] = []
    obrigatorios = {"Orçamento nº": numero_orcamento}
    for idx, item in enumerate(componentes, start=1):
        tipo = str((item or {}).get("tipo") or "").strip() or "Grid"
        numero_certificado = str((item or {}).get("numero_certificado") or "").strip()
        os_lote = str((item or {}).get("os") or "").strip()
        dureza = str((item or {}).get("dureza") or "").strip()
        chd = str((item or {}).get("chd") or "").strip()
        resultado = str((item or {}).get("resultado") or "").strip()

        preenchido = bool(numero_certificado or os_lote or dureza or chd or resultado)
        if not preenchido:
            continue
        if tipo not in TIPOS_COMPONENTE:
            return jsonify({"error": f"Tipo de componente inválido na linha {idx}."}), 400
        if resultado not in RESULTADOS_VALIDOS:
            return jsonify({"error": f"Selecione o resultado do componente {idx}: Conforme ou Não Conforme."}), 400
        if os_referencia and os_lote and os_lote != os_referencia:
            return jsonify({"error": f"O componente {idx} usa a OS '{os_lote}', mas este laudo é da OS '{os_referencia}'. Para OS diferente, use outro laudo."}), 400
        if os_referencia and not os_lote:
            os_lote = os_referencia

        rotulo = f"{tipo} {idx}"
        obrigatorios[f"N° do certificado ({rotulo})"] = numero_certificado
        obrigatorios[f"OS / Lote-CP ({rotulo})"] = os_lote
        obrigatorios[f"Dureza medida ({rotulo})"] = dureza
        obrigatorios[f"CHD medida ({rotulo})"] = chd
        componentes_validos.append(
            {
                "tipo": tipo,
                "numero_certificado": numero_certificado,
                "os": os_lote,
                "dureza": dureza,
                "chd": chd,
                "resultado": resultado,
            }
        )

    if not componentes_validos:
        return jsonify({"error": "Informe pelo menos um componente no laudo."}), 400

    if not os_referencia:
        os_referencia = str(componentes_validos[0].get("os") or "").strip()
        if os_referencia:
            registro.os_referencia = os_referencia[:120]

    for idx, comp in enumerate(componentes_validos, start=1):
        if os_referencia and str(comp.get("os") or "").strip() != os_referencia:
            return jsonify({"error": f"O componente {idx} pertence a outra OS. Cada laudo aceita apenas uma OS."}), 400

    faltando = [rotulo for rotulo, valor in obrigatorios.items() if not valor]
    if faltando:
        return jsonify({"error": "Preencha os campos obrigatórios: " + ", ".join(faltando)}), 400

    # Foto é opcional (nice to have).
    nova_foto = _save_foto("foto")
    if nova_foto:
        registro.foto_path = nova_foto

    registro.numero_orcamento = numero_orcamento[:120]
    registro.componentes.clear()
    for ordem, comp in enumerate(componentes_validos, start=1):
        registro.componentes.append(
            QualidadeCertificadoComponente(
                ordem=ordem,
                tipo=comp["tipo"],
                os=comp["os"][:120],
                numero_certificado=comp["numero_certificado"][:120],
                dureza=comp["dureza"][:120],
                chd=comp["chd"][:120],
                resultado=comp["resultado"],
            )
        )
    _sincronizar_campos_legados(registro, componentes_validos)
    registro.os_referencia = os_referencia[:120] if os_referencia else ""
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
    registro.os_referencia = registro.os_referencia or ""
    registro.os = None
    registro.componentes.clear()
    registro.grid_os = None
    registro.grid_numero_certificado = None
    registro.grid_dureza = registro.grid_chd = registro.grid_resultado = None
    registro.sapatas_os = None
    registro.sapatas_numero_certificado = None
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

