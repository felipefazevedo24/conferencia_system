"""Rotas da funcionalidade de Inventario Inicial da Logistica."""

from __future__ import annotations

import json
import os
from datetime import datetime
from io import BytesIO
from pathlib import Path

from flask import Blueprint, current_app, jsonify, render_template, request, session, send_file
from openpyxl import Workbook
import requests

from ..auth import has_permission, permission_required, permission_required_any
from ..extensions import db
from ..models import LogisticaInventarioAjuste, LogisticaInventarioInicial
from ..services.erp_estoque_service import (
    LocalizacaoEstoqueNaoEncontrada,
    atualizar_localizacao_estoque,
    buscar_estoque_grv,
    qtde_grv_para,
)
from ..services import logistica_inventario_ajuste_service as ajuste_svc


logistica_inventario_bp = Blueprint("logistica_inventario", __name__)

PERMISSION = "PAGE_LOGISTICA_INVENTARIO"
INVENTARIO_EXPORT_JSON_REL_PATH = "inventario_material_local.json"
UNIDADES_PADRAO = [
    "UN", "PC", "CX", "PCT", "RL", "KG", "G", "MG", "L", "ML", "M", "CM", "MM", "M2", "M3",
]


def _fmt_registro(row: LogisticaInventarioInicial, incluir_grv: bool = False) -> dict:
    dados = {
        "id": row.id,
        "local_codigo": row.local_codigo,
        "codigo_produto": row.codigo_produto,
        "unidade_medida": row.unidade_medida,
        "quantidade": row.quantidade,
        "lote": row.lote or "",
        "observacao": row.observacao or "",
        "criado_por": row.criado_por,
        "criado_em": row.criado_em.isoformat() if row.criado_em else None,
        "atualizado_em": row.atualizado_em.isoformat() if row.atualizado_em else None,
    }
    # So incluido quando explicitamente pedido (tela de consulta/revisao).
    # A tela de contagem (Novo Inventario) NUNCA pede isso, pra nao vesar a
    # conferencia com o saldo esperado - mesma logica da conferencia cega
    # usada no resto do sistema.
    #
    # O saldo do GRV usado aqui e' o SNAPSHOT gravado no momento da contagem
    # (ver criar_inventario_inicial), nao uma consulta em tempo real - senao
    # uma contagem que batia com o GRV na hora em que foi feita passaria a
    # aparecer como "divergente" so porque o estoque mudou depois (giro
    # normal), sem relacao nenhuma com erro de contagem.
    if incluir_grv:
        qtde_grv = row.qtde_grv_no_momento
        dados["qtde_grv"] = qtde_grv
        dados["grv_consultado_em"] = row.grv_consultado_em.isoformat() if row.grv_consultado_em else None
        dados["divergente"] = (qtde_grv is not None) and (abs(float(row.quantidade or 0) - qtde_grv) > ajuste_svc.TOLERANCIA_DIVERGENCIA)
    return dados


def _build_query(local: str = "", codigo: str = ""):
    query = LogisticaInventarioInicial.query
    if local:
        query = query.filter(LogisticaInventarioInicial.local_codigo.ilike(f"%{local}%"))
    if codigo:
        query = query.filter(LogisticaInventarioInicial.codigo_produto.ilike(f"%{codigo}%"))
    return query


def _token_integracao_inventario_recebido() -> str:
    token = str(request.headers.get("X-Integracao-Token") or "").strip()
    if token:
        return token
    auth = str(request.headers.get("Authorization") or "").strip()
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return str(request.args.get("token") or "").strip()


def _token_integracao_inventario_valido() -> bool:
    # Usa token proprio do inventario; se ausente, reaproveita token da integracao de expedicao.
    esperado = str(
        current_app.config.get("INVENTARIO_INTEGRACAO_TOKEN")
        or current_app.config.get("EXPEDICAO_INTEGRACAO_TOKEN")
        or ""
    ).strip()
    if not esperado:
        return True
    return _token_integracao_inventario_recebido() == esperado


def _gerar_dados_material_local(rows: list[LogisticaInventarioInicial]) -> list[dict]:
    # Mantem apenas a versao mais recente por par (material, local).
    mais_recente_por_chave: dict[tuple[str, str], LogisticaInventarioInicial] = {}
    for row in rows:
        codigo = str(row.codigo_produto or "").strip().upper()
        local = str(row.local_codigo or "").strip().upper()
        if not codigo or not local:
            continue
        chave = (codigo, local)
        atual = mais_recente_por_chave.get(chave)
        if not atual or (row.atualizado_em or row.criado_em or datetime.min) > (atual.atualizado_em or atual.criado_em or datetime.min):
            mais_recente_por_chave[chave] = row

    saida = []
    for (codigo, local), row in sorted(mais_recente_por_chave.items()):
        saida.append(
            {
                "codigo_material": codigo,
                "local": local,
                "quantidade": float(row.quantidade or 0),
                "unidade": str(row.unidade_medida or "UN"),
                "atualizado_em": (row.atualizado_em or row.criado_em).isoformat() if (row.atualizado_em or row.criado_em) else None,
            }
        )
    return saida


def _montar_payload_material_local() -> dict:
    rows = (
        LogisticaInventarioInicial.query
        .order_by(LogisticaInventarioInicial.atualizado_em.desc(), LogisticaInventarioInicial.id.desc())
        .all()
    )
    dados = _gerar_dados_material_local(rows)
    return {
        "sucesso": True,
        "gerado_em": datetime.now().isoformat(),
        "total": len(dados),
        "dados": dados,
    }


def _salvar_snapshot_inventario_json(payload: dict) -> Path:
    static_dir = Path(__file__).resolve().parents[2] / "static"
    static_dir.mkdir(parents=True, exist_ok=True)
    out_path = static_dir / INVENTARIO_EXPORT_JSON_REL_PATH
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return out_path


def _enviar_para_grv(payload: dict) -> dict:
    api_url = str(
        os.environ.get("INVENTARIO_GRV_API_URL")
        or os.environ.get("ERP_LANCAMENTO_API_URL")
        or current_app.config.get("INVENTARIO_GRV_API_URL")
        or current_app.config.get("ERP_LANCAMENTO_API_URL")
        or ""
    ).strip()
    token = str(
        os.environ.get("INVENTARIO_GRV_API_TOKEN")
        or os.environ.get("ERP_LANCAMENTO_API_TOKEN")
        or current_app.config.get("INVENTARIO_GRV_API_TOKEN")
        or current_app.config.get("ERP_LANCAMENTO_API_TOKEN")
        or ""
    ).strip()
    timeout = int(
        os.environ.get("INVENTARIO_GRV_API_TIMEOUT")
        or os.environ.get("ERP_LANCAMENTO_API_TIMEOUT")
        or current_app.config.get("INVENTARIO_GRV_API_TIMEOUT")
        or current_app.config.get("ERP_LANCAMENTO_API_TIMEOUT")
        or 30
    )

    if not api_url:
        return {"enviado": False, "motivo": "INVENTARIO_GRV_API_URL nao configurada."}

    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "ngrok-skip-browser-warning": "true",
        "User-Agent": "ColumbiaSync/Inventario-GRV",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    resp = requests.post(api_url, headers=headers, json=payload, timeout=timeout)
    resp.raise_for_status()
    body = resp.json() if (resp.headers.get("content-type", "").lower().find("json") >= 0) else {}
    return {
        "enviado": True,
        "status_code": resp.status_code,
        "resposta": body if isinstance(body, dict) else {},
    }


@logistica_inventario_bp.route("/logistica/inventario")
@permission_required(PERMISSION)
def inventario_home_page():
    return render_template(
        "logistica_inventario_home.html",
        user=session["username"],
        user_role=session.get("role", ""),
    )


@logistica_inventario_bp.route("/logistica/inventario/novo")
@permission_required(PERMISSION)
def inventario_novo_page():
    return render_template(
        "logistica_inventario_inicial.html",
        user=session["username"],
        user_role=session.get("role", ""),
    )


@logistica_inventario_bp.route("/logistica/inventario/consulta")
@permission_required(PERMISSION)
def inventario_consulta_page():
    return render_template(
        "logistica_inventario_consulta.html",
        user=session["username"],
        user_role=session.get("role", ""),
    )


@logistica_inventario_bp.route("/logistica/inventario-inicial")
@permission_required(PERMISSION)
def inventario_inicial_legacy_redirect():
    return inventario_novo_page()


@logistica_inventario_bp.route("/api/logistica/inventario-inicial/unidades", methods=["GET"])
@permission_required(PERMISSION)
def listar_unidades_padrao():
    return jsonify({"unidades": UNIDADES_PADRAO})


@logistica_inventario_bp.route("/api/logistica/inventario-inicial", methods=["GET"])
@permission_required(PERMISSION)
def listar_inventario_inicial():
    limite = request.args.get("limit", type=int) or 100
    limite = max(1, min(limite, 500))
    local = (request.args.get("local") or "").strip().lower()
    codigo = (request.args.get("codigo") or "").strip().lower()

    query = _build_query(local=local, codigo=codigo)

    rows = query.order_by(LogisticaInventarioInicial.criado_em.desc()).limit(limite).all()

    # A comparacao com o GRV so e incluida quando explicitamente pedida
    # (tela de consulta) - a tela de contagem nunca passa esse parametro,
    # entao nunca recebe o saldo esperado antes de fechar a contagem.
    # O valor usado e' o snapshot gravado no momento de cada contagem (ver
    # criar_inventario_inicial), nao uma consulta em tempo real - o
    # "forcar_grv"/cache do buscar_estoque_grv() nao se aplica mais aqui.
    incluir_grv = request.args.get("comparar_grv") == "1"

    resposta = {"registros": [_fmt_registro(row, incluir_grv) for row in rows]}
    if incluir_grv:
        # Quantas contagens retornadas nao conseguiram capturar o saldo do
        # GRV no momento em que foram feitas (API fora do ar naquela hora,
        # ou codigo nao encontrado no GRV) - a coluna "Qtd GRV" fica vazia
        # pra essas, sem tentar reconsultar agora.
        resposta["sem_grv_no_momento"] = sum(1 for row in rows if row.qtde_grv_no_momento is None)
    return jsonify(resposta)


@logistica_inventario_bp.route("/api/logistica/inventario-inicial/exportar", methods=["GET"])
@permission_required(PERMISSION)
def exportar_inventario_inicial_excel():
    local = (request.args.get("local") or "").strip().lower()
    codigo = (request.args.get("codigo") or "").strip().lower()
    comparar_grv = request.args.get("comparar_grv") == "1"

    query = _build_query(local=local, codigo=codigo)
    rows = query.order_by(LogisticaInventarioInicial.criado_em.desc()).all()

    wb = Workbook()
    ws = wb.active
    ws.title = "Inventario"

    headers = [
        "Data",
        "Local",
        "Codigo Produto",
        "Unidade",
        "Quantidade",
        "Lote",
        "Observacao",
        "Criado Por",
    ]
    if comparar_grv:
        headers += ["Qtde GRV", "Divergente"]
    ws.append(headers)

    for row in rows:
        linha = [
            row.criado_em.strftime("%d/%m/%Y %H:%M:%S") if row.criado_em else "",
            row.local_codigo,
            row.codigo_produto,
            row.unidade_medida,
            float(row.quantidade or 0),
            row.lote or "",
            row.observacao or "",
            row.criado_por,
        ]
        if comparar_grv:
            qtde_grv = row.qtde_grv_no_momento
            divergente = (qtde_grv is not None) and (abs(float(row.quantidade or 0) - qtde_grv) > ajuste_svc.TOLERANCIA_DIVERGENCIA)
            linha += [qtde_grv if qtde_grv is not None else "N/D", "SIM" if divergente else "NAO"]
        ws.append(linha)

    for col in ws.columns:
        max_len = 0
        col_letter = col[0].column_letter
        for cell in col:
            val = "" if cell.value is None else str(cell.value)
            max_len = max(max_len, len(val))
        ws.column_dimensions[col_letter].width = min(max(12, max_len + 2), 60)

    stream = BytesIO()
    wb.save(stream)
    stream.seek(0)

    nome_arquivo = f"inventario_logistica_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
    return send_file(
        stream,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name=nome_arquivo,
    )


@logistica_inventario_bp.route("/api/logistica/inventario-inicial", methods=["POST"])
@permission_required(PERMISSION)
def criar_inventario_inicial():
    payload = request.get_json(silent=True) or {}

    local_codigo = str(payload.get("local_codigo") or "").strip().upper()
    codigo_produto = str(payload.get("codigo_produto") or "").strip().upper()
    unidade_medida = str(payload.get("unidade_medida") or "UN").strip().upper() or "UN"
    lote = str(payload.get("lote") or "").strip()
    observacao = str(payload.get("observacao") or "").strip()

    if not local_codigo:
        return jsonify({"error": "Local e obrigatorio."}), 400
    if not codigo_produto:
        return jsonify({"error": "Codigo do produto e obrigatorio."}), 400

    try:
        quantidade = float(str(payload.get("quantidade") or "0").replace(",", "."))
    except (TypeError, ValueError):
        return jsonify({"error": "Quantidade invalida."}), 400

    if quantidade <= 0:
        return jsonify({"error": "Quantidade deve ser maior que zero."}), 400

    row = LogisticaInventarioInicial(
        local_codigo=local_codigo,
        codigo_produto=codigo_produto,
        unidade_medida=unidade_medida[:20],
        quantidade=quantidade,
        lote=lote[:120] if lote else None,
        observacao=observacao[:800] if observacao else None,
        criado_por=session.get("username", "sistema"),
        atualizado_em=datetime.now(),
    )
    db.session.add(row)
    db.session.commit()

    # Sincroniza a localizacao no ERP (tproduto) - nao falha a contagem se
    # o ERP estiver fora do ar, so avisa: o registro do inventario ja foi
    # salvo com sucesso independente disso.
    localizacao_erp = {"sincronizado": False}
    try:
        localizacao_erp["resposta"] = atualizar_localizacao_estoque(codigo_produto, local_codigo)
        localizacao_erp["sincronizado"] = True
    except LocalizacaoEstoqueNaoEncontrada as exc:
        localizacao_erp["erro"] = str(exc)
    except Exception as exc:  # noqa: BLE001
        current_app.logger.warning(
            "Falha ao sincronizar localização de estoque no ERP (código=%s, local=%s): %s",
            codigo_produto, local_codigo, exc,
        )
        localizacao_erp["erro"] = str(exc)

    # Consulta o saldo do GRV UMA VEZ, no exato momento da contagem, e grava
    # esse snapshot no proprio registro (qtde_grv_no_momento/grv_consultado_em)
    # - a tela de consulta (Inventario Realizado) usa esse valor gravado em
    # vez de reconsultar o GRV em tempo real depois, senao uma contagem que
    # batia com o GRV na hora em que foi feita passaria a aparecer como
    # divergente so porque o estoque girou normalmente depois. Tambem
    # detecta divergencia e, se houver, abre automaticamente um ajuste no
    # Modulo 02 (Validacao) pro gestor revisar - nao falha a contagem se o
    # GRV estiver indisponivel, so fica sem snapshot e sem ajuste.
    ajuste_aberto = None
    try:
        estoque_grv = buscar_estoque_grv()
        qtde_grv = qtde_grv_para(row.codigo_produto, row.local_codigo, estoque_grv)
        row.qtde_grv_no_momento = qtde_grv
        row.grv_consultado_em = datetime.now()
        db.session.commit()
        ajuste = ajuste_svc.detectar_divergencia(row, qtde_grv)
        if ajuste:
            ajuste_aberto = {"id": ajuste.id, "diferenca": ajuste.diferenca}
    except Exception as exc:  # noqa: BLE001
        current_app.logger.warning(
            "Falha ao consultar o GRV pra gravar snapshot/detectar divergencia (código=%s, local=%s): %s",
            codigo_produto, local_codigo, exc,
        )

    return jsonify({
        "sucesso": True,
        "registro": _fmt_registro(row),
        "localizacao_erp": localizacao_erp,
        "ajuste_aberto": ajuste_aberto,
    }), 201


@logistica_inventario_bp.route("/api/integracao/inventario/material-local", methods=["GET"])
def inventario_material_local_integracao():
    if not _token_integracao_inventario_valido():
        return jsonify({"sucesso": False, "erro": "Token de integracao invalido."}), 401
    return jsonify(_montar_payload_material_local())


@logistica_inventario_bp.route("/api/logistica/inventario-inicial/sincronizar-grv", methods=["POST"])
@permission_required(PERMISSION)
def sincronizar_inventario_grv():
    payload = _montar_payload_material_local()
    _salvar_snapshot_inventario_json(payload)

    grv = {}
    try:
        grv = _enviar_para_grv(payload)
    except Exception as exc:
        grv = {"enviado": False, "erro": str(exc)}

    base_url = request.url_root.rstrip("/")
    return jsonify(
        {
            "sucesso": True,
            "total": payload.get("total", 0),
            "snapshot_url": f"{base_url}/static/{INVENTARIO_EXPORT_JSON_REL_PATH}",
            "integracao_url": f"{base_url}/api/integracao/inventario/material-local",
            "grv": grv,
        }
    )


# ── Fluxo de ajuste de estoque (Modulos 02-04, itens divergentes) ─────────
PERMISSION_VALIDACAO = "PAGE_LOGISTICA_INVENTARIO_VALIDACAO"
PERMISSION_FINANCE = "PAGE_LOGISTICA_INVENTARIO_FINANCE"
PERMISSION_FISCAL = "PAGE_LOGISTICA_INVENTARIO_FISCAL"
PERMISSION_PULAR_ETAPA = "PAGE_LOGISTICA_INVENTARIO_PULAR_ETAPA"


def _fmt_ajuste(a) -> dict:
    return {
        "id": a.id,
        "codigo_produto": a.codigo_produto,
        "local_codigo": a.local_codigo,
        "unidade_medida": a.unidade_medida,
        "qtde_contada": a.qtde_contada,
        "qtde_estoque_no_momento": a.qtde_estoque_no_momento,
        "diferenca": a.diferenca,
        "status_modulo": a.status_modulo,
        "status_slug": a.status_slug,
        "criado_em": a.criado_em.strftime("%d/%m/%Y %H:%M") if a.criado_em else None,
        "gestor_justificativa": a.gestor_justificativa,
        "gestor_confirmado_em": a.gestor_confirmado_em.strftime("%d/%m/%Y %H:%M") if a.gestor_confirmado_em else None,
        "gestor_confirmado_por": a.gestor_confirmado_por,
        "finance_observacao": a.finance_observacao,
        "finance_concluido_em": a.finance_concluido_em.strftime("%d/%m/%Y %H:%M") if a.finance_concluido_em else None,
        "finance_concluido_por": a.finance_concluido_por,
        "fiscal_nf_numero": a.fiscal_nf_numero,
        "fiscal_concluido_em": a.fiscal_concluido_em.strftime("%d/%m/%Y %H:%M") if a.fiscal_concluido_em else None,
        "fiscal_concluido_por": a.fiscal_concluido_por,
    }


@logistica_inventario_bp.route("/logistica/inventario/ajustes")
@permission_required_any(PERMISSION, PERMISSION_VALIDACAO, PERMISSION_FINANCE, PERMISSION_FISCAL)
def inventario_ajustes_page():
    return render_template(
        "logistica_inventario_ajustes.html",
        user=session["username"],
        user_role=session.get("role", ""),
        pode_validar=has_permission(PERMISSION_VALIDACAO),
        pode_finance=has_permission(PERMISSION_FINANCE),
        pode_fiscal=has_permission(PERMISSION_FISCAL),
        pode_pular_etapa=has_permission(PERMISSION_PULAR_ETAPA),
    )


@logistica_inventario_bp.route("/api/logistica/inventario-ajustes", methods=["GET"])
@permission_required_any(PERMISSION, PERMISSION_VALIDACAO, PERMISSION_FINANCE, PERMISSION_FISCAL)
def api_listar_ajustes():
    status_modulo = request.args.get("status") or None
    ajustes = ajuste_svc.listar_ajustes(status_modulo=status_modulo)
    return jsonify({
        "ajustes": [_fmt_ajuste(a) for a in ajustes],
        "pode_pular_etapa": has_permission(PERMISSION_PULAR_ETAPA),
        "pode_validar": has_permission(PERMISSION_VALIDACAO),
        "pode_finance": has_permission(PERMISSION_FINANCE),
        "pode_fiscal": has_permission(PERMISSION_FISCAL),
    })


@logistica_inventario_bp.route("/api/logistica/inventario-ajustes/<int:ajuste_id>/confirmar", methods=["POST"])
@permission_required_any(PERMISSION, PERMISSION_VALIDACAO, PERMISSION_FINANCE, PERMISSION_FISCAL)
def api_confirmar_ajuste(ajuste_id):
    if not has_permission(PERMISSION_VALIDACAO):
        return jsonify({"error": "Você não tem permissão pra validar divergências - fale com a gerência."}), 403
    ajuste = LogisticaInventarioAjuste.query.get(ajuste_id)
    if not ajuste:
        return jsonify({"error": "Ajuste não encontrado."}), 404
    payload = request.get_json(silent=True) or {}
    try:
        ajuste = ajuste_svc.confirmar_divergencia(ajuste, session.get("username", "desconhecido"), payload.get("justificativa"))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"message": "Divergência confirmada e enviada pro Finance.", "ajuste": _fmt_ajuste(ajuste)})


@logistica_inventario_bp.route("/api/logistica/inventario-ajustes/<int:ajuste_id>/descartar", methods=["POST"])
@permission_required_any(PERMISSION, PERMISSION_VALIDACAO, PERMISSION_FINANCE, PERMISSION_FISCAL)
def api_descartar_ajuste(ajuste_id):
    if not has_permission(PERMISSION_VALIDACAO):
        return jsonify({"error": "Você não tem permissão pra validar divergências - fale com a gerência."}), 403
    ajuste = LogisticaInventarioAjuste.query.get(ajuste_id)
    if not ajuste:
        return jsonify({"error": "Ajuste não encontrado."}), 404
    payload = request.get_json(silent=True) or {}
    try:
        ajuste = ajuste_svc.descartar_divergencia(ajuste, session.get("username", "desconhecido"), payload.get("motivo"))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"message": "Diferença marcada como improcedente.", "ajuste": _fmt_ajuste(ajuste)})


@logistica_inventario_bp.route("/api/logistica/inventario-ajustes/<int:ajuste_id>/finance-concluir", methods=["POST"])
@permission_required_any(PERMISSION, PERMISSION_VALIDACAO, PERMISSION_FINANCE, PERMISSION_FISCAL)
def api_finance_concluir_ajuste(ajuste_id):
    if not has_permission(PERMISSION_FINANCE):
        return jsonify({"error": "Você não tem permissão de Finance nesse fluxo - fale com a gerência."}), 403
    ajuste = LogisticaInventarioAjuste.query.get(ajuste_id)
    if not ajuste:
        return jsonify({"error": "Ajuste não encontrado."}), 404
    payload = request.get_json(silent=True) or {}
    try:
        ajuste = ajuste_svc.concluir_finance(ajuste, session.get("username", "desconhecido"), payload.get("observacao"))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"message": "Ajuste de estoque confirmado. Liberado pro Fiscal.", "ajuste": _fmt_ajuste(ajuste)})


@logistica_inventario_bp.route("/api/logistica/inventario-ajustes/<int:ajuste_id>/fiscal-concluir", methods=["POST"])
@permission_required_any(PERMISSION, PERMISSION_VALIDACAO, PERMISSION_FINANCE, PERMISSION_FISCAL)
def api_fiscal_concluir_ajuste(ajuste_id):
    if not has_permission(PERMISSION_FISCAL):
        return jsonify({"error": "Você não tem permissão de Fiscal nesse fluxo - fale com a gerência."}), 403
    ajuste = LogisticaInventarioAjuste.query.get(ajuste_id)
    if not ajuste:
        return jsonify({"error": "Ajuste não encontrado."}), 404
    payload = request.get_json(silent=True) or {}
    try:
        ajuste = ajuste_svc.concluir_fiscal(ajuste, session.get("username", "desconhecido"), payload.get("nf_numero"))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"message": "NF de ajuste confirmada. Processo concluído.", "ajuste": _fmt_ajuste(ajuste)})


@logistica_inventario_bp.route("/api/logistica/inventario-ajustes/<int:ajuste_id>/pular-etapa", methods=["POST"])
@permission_required_any(PERMISSION, PERMISSION_VALIDACAO, PERMISSION_FINANCE, PERMISSION_FISCAL, PERMISSION_PULAR_ETAPA)
def api_pular_etapa_ajuste(ajuste_id):
    """"Pular Etapa" - avanço SEM validação nenhuma pra próxima etapa do
    ajuste, reservado pra casos excepcionais. Exige permissão extra de
    gerência (PAGE_LOGISTICA_INVENTARIO_PULAR_ETAPA), além do acesso normal
    ao módulo."""
    if not has_permission(PERMISSION_PULAR_ETAPA):
        return jsonify({"error": "Você não tem permissão pra usar o Pular Etapa - fale com a gerência."}), 403
    ajuste = LogisticaInventarioAjuste.query.get(ajuste_id)
    if not ajuste:
        return jsonify({"error": "Ajuste não encontrado."}), 404
    usuario = session.get("username", "desconhecido")
    try:
        ajuste = ajuste_svc.pular_etapa(ajuste, usuario)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"message": f"Etapa avançada para {ajuste.status_modulo}.", "ajuste": _fmt_ajuste(ajuste)})
