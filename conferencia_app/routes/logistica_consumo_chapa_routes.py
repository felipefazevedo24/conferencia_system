"""Rotas do modulo de Consumo de Chapa (Nesting) da Logistica.

Upload manual do relatorio de Nesting exportado pela maquina de corte
(HTML, FastReport) - o Sync roda na nuvem e nao enxerga a rede local onde
a maquina salva o export. Workflow: Nesting -> Concluido."""
from __future__ import annotations

from flask import Blueprint, jsonify, render_template, request, session

from ..auth import permission_required
from ..extensions import db
from ..models import LogisticaConsumoChapaNesting
from ..services import logistica_consumo_chapa_service as svc

logistica_consumo_chapa_bp = Blueprint("logistica_consumo_chapa", __name__)

PERMISSION = "PAGE_LOGISTICA_CONSUMO_CHAPA"


def _fmt_peca(p) -> dict:
    return {
        "id": p.id,
        "peca_numero": p.peca_numero,
        "nome_peca": p.nome_peca,
        "qtd_requerida": p.qtd_requerida,
        "qtd_arranjada": p.qtd_arranjada,
        "peso_liquido_kg": p.peso_liquido_kg,
        "prox_operacao": p.prox_operacao,
        "cliente": p.cliente,
        "os_orcamento": p.os_orcamento,
        "os_numero": p.os_numero,
    }


def _fmt_nesting(n, com_pecas: bool = False) -> dict:
    dados = {
        "id": n.id,
        "numero_programa": n.numero_programa,
        "pagina_atual": n.pagina_atual,
        "pagina_total": n.pagina_total,
        "programador": n.programador,
        "maquina": n.maquina,
        "data_corte": n.data_corte.isoformat() if n.data_corte else None,
        "hora_corte": n.hora_corte,
        "tempo_corte": n.tempo_corte,
        "material": n.material,
        "codigo_material": n.codigo_material,
        "espessura_mm": n.espessura_mm,
        "nome_tarefa": n.nome_tarefa,
        "qtde_chapas": n.qtde_chapas,
        "peso_sucata_kg": n.peso_sucata_kg,
        "peso_pecas_kg": n.peso_pecas_kg,
        "peso_retalho_kg": n.peso_retalho_kg,
        "peso_total_kg": n.peso_total_kg,
        "aproveitamento_pct": n.aproveitamento_pct,
        "retalho_pct": n.retalho_pct,
        "sucata_pct": n.sucata_pct,
        "status": n.status,
        "status_slug": svc.status_slug(n.status),
        "arquivo_origem": n.arquivo_origem,
        "criado_em": n.criado_em.strftime("%d/%m/%Y %H:%M") if n.criado_em else None,
        "criado_por": n.criado_por,
        "concluido_em": n.concluido_em.strftime("%d/%m/%Y %H:%M") if n.concluido_em else None,
        "concluido_por": n.concluido_por,
        "qtd_pecas": len(n.pecas),
    }
    if com_pecas:
        dados["pecas"] = [_fmt_peca(p) for p in n.pecas]
    return dados


@logistica_consumo_chapa_bp.route("/logistica/consumo-chapa")
@permission_required(PERMISSION)
def consumo_chapa_page():
    return render_template(
        "logistica_consumo_chapa.html",
        user=session["username"],
        user_role=session.get("role", ""),
    )


@logistica_consumo_chapa_bp.route("/api/logistica/consumo-chapa", methods=["GET"])
@permission_required(PERMISSION)
def api_listar_consumo_chapa():
    status = request.args.get("status") or None
    busca = request.args.get("busca") or ""
    nestings = svc.listar_nestings(status=status, busca=busca)
    return jsonify({"nestings": [_fmt_nesting(n) for n in nestings]})


@logistica_consumo_chapa_bp.route("/api/logistica/consumo-chapa/<int:nesting_id>", methods=["GET"])
@permission_required(PERMISSION)
def api_detalhe_consumo_chapa(nesting_id):
    nesting = db.session.get(LogisticaConsumoChapaNesting, nesting_id)
    if not nesting:
        return jsonify({"error": "Nesting não encontrado."}), 404
    return jsonify({"nesting": _fmt_nesting(nesting, com_pecas=True)})


@logistica_consumo_chapa_bp.route("/api/logistica/consumo-chapa/importar", methods=["POST"])
@permission_required(PERMISSION)
def api_importar_consumo_chapa():
    arquivo = request.files.get("arquivo")
    if not arquivo or not arquivo.filename:
        return jsonify({"error": "Nenhum arquivo recebido."}), 400
    usuario = session.get("username", "desconhecido")
    try:
        conteudo = arquivo.read().decode("utf-8", errors="replace")
        resumo = svc.importar_relatorio(conteudo, arquivo.filename, usuario)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"error": f"Falha ao importar o relatório: {exc}"}), 502

    return jsonify({
        "message": (
            f"{resumo['total']} Nesting(s) processado(s) — "
            f"{resumo['criados']} novo(s), {resumo['atualizados']} atualizado(s)."
        ),
        "criados": resumo["criados"],
        "atualizados": resumo["atualizados"],
        "nestings": [_fmt_nesting(n) for n in resumo["nestings"]],
    })


@logistica_consumo_chapa_bp.route("/api/logistica/consumo-chapa/<int:nesting_id>/concluir", methods=["POST"])
@permission_required(PERMISSION)
def api_concluir_consumo_chapa(nesting_id):
    nesting = db.session.get(LogisticaConsumoChapaNesting, nesting_id)
    if not nesting:
        return jsonify({"error": "Nesting não encontrado."}), 404
    try:
        nesting = svc.concluir_nesting(nesting, session.get("username", "desconhecido"))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"message": "Nesting concluído.", "nesting": _fmt_nesting(nesting)})


@logistica_consumo_chapa_bp.route("/api/logistica/consumo-chapa/<int:nesting_id>/estornar", methods=["POST"])
@permission_required(PERMISSION)
def api_estornar_consumo_chapa(nesting_id):
    nesting = db.session.get(LogisticaConsumoChapaNesting, nesting_id)
    if not nesting:
        return jsonify({"error": "Nesting não encontrado."}), 404
    try:
        nesting = svc.estornar_nesting(nesting)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"message": "Nesting estornado para 'Nesting'.", "nesting": _fmt_nesting(nesting)})
