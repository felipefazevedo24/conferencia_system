"""Logistica > Inventario > Inventario geral.

Duas permissoes: CONTAGEM (operador - visao CEGA: o servidor nem manda o
saldo do sistema nem a contagem de outra pessoa) e GESTAO (abre, ve o
resultado completo, recontagem, finaliza e envia ao ERP). Regras em
services/inventario_geral_service.py.
"""
from __future__ import annotations

from io import BytesIO

from flask import Blueprint, jsonify, render_template, request, send_file, session
from openpyxl import Workbook

from ..auth import has_permission, permission_required, permission_required_any
from ..extensions import db
from ..models import InventarioGeralItem as Item
from ..services import inventario_cps_client as cps
from ..services import inventario_geral_service as svc

inventario_geral_bp = Blueprint("inventario_geral", __name__)

PERM_CONTAGEM = "PAGE_INVENTARIO_GERAL_CONTAGEM"
PERM_GESTAO = "PAGE_INVENTARIO_GERAL_GESTAO"


def _usuario() -> str:
    return session.get("username", "desconhecido")


def _dt(valor):
    return valor.strftime("%d/%m/%Y %H:%M") if valor else None


def _item_gestao(i: Item) -> dict:
    return {
        "id": i.id, "cod_produto": i.cod_produto, "codigo_interno": i.codigo_interno, "descricao": i.descricao,
        "unidade": i.unidade, "lote": i.lote, "com_lote": svc.com_lote(i.tipo_controle),
        "qtde_sistema": i.qtde_sistema, "adicionado_manual": i.adicionado_manual, "adicionado_por": i.adicionado_por,
        "contagem2": i.contagem2, "contagem2_por": i.contagem2_por, "contagem2_em": _dt(i.contagem2_em),
        "contagem3": i.contagem3, "contagem3_por": i.contagem3_por, "contagem3_em": _dt(i.contagem3_em),
        "recontagens": i.recontagens, "status": i.status, "qtde_final": i.qtde_final,
    }


def _item_cego(i: Item, usuario: str) -> dict:
    """Operador: so' o que identifica o item - nada de saldo nem contagem alheia."""
    return {
        "id": i.id, "codigo_interno": i.codigo_interno, "descricao": i.descricao, "unidade": i.unidade,
        "lote": i.lote, "com_lote": svc.com_lote(i.tipo_controle),
        "etapa": "2ª contagem" if i.status == Item.STATUS_PENDENTE else "3ª contagem",
        "pode_contar": not (i.status == Item.STATUS_AGUARDA_3A and (i.contagem2_por or "").upper() == usuario.upper()),
    }


@inventario_geral_bp.route("/logistica/inventario-geral")
@permission_required_any(PERM_CONTAGEM, PERM_GESTAO)
def page_inventario_geral():
    return render_template(
        "inventario_geral.html",
        user=session.get("username"),
        pode_gestao=has_permission(PERM_GESTAO),
        pode_contar=has_permission(PERM_CONTAGEM),
    )


@inventario_geral_bp.route("/api/inventario-geral", methods=["GET"])
@permission_required_any(PERM_CONTAGEM, PERM_GESTAO)
def api_estado():
    inv = svc.inventario_ativo()
    gestao = has_permission(PERM_GESTAO)
    if not inv:
        return jsonify({"inventario": None, "api_configurada": cps.configurada(), "gestao": gestao})
    cab = {
        "id": inv.id, "descricao": inv.descricao, "status": inv.status, "aberto_em": _dt(inv.aberto_em),
        "aberto_por": inv.aberto_por, "finalizado_em": _dt(inv.finalizado_em), "finalizado_por": inv.finalizado_por,
    }
    usuario = _usuario()
    if gestao:
        envios = [{"tipo": e.tipo, "referencia": e.referencia, "status": e.status, "http": e.http_status,
                   "enviado_em": _dt(e.enviado_em), "enviado_por": e.enviado_por, "resposta": e.resposta}
                  for e in inv.envios]
        return jsonify({"inventario": cab, "resumo": svc.resumo(inv), "gestao": True,
                        "api_configurada": cps.configurada(), "gravacao_configurada": cps.configurada(gravacao=True),
                        "itens": [_item_gestao(i) for i in inv.itens], "envios": envios})
    a_contar = [i for i in inv.itens if i.status in (Item.STATUS_PENDENTE, Item.STATUS_AGUARDA_3A)]
    return jsonify({"inventario": cab, "gestao": False, "itens": [_item_cego(i, usuario) for i in a_contar]})


def _resposta_item(item, mensagem):
    if has_permission(PERM_GESTAO):
        return jsonify({"message": mensagem, "item": _item_gestao(item)})
    # Operador nao fica sabendo se bateu ou nao (contagem cega).
    return jsonify({"message": "Contagem registrada.", "item": {"id": item.id}})


@inventario_geral_bp.route("/api/inventario-geral/abrir", methods=["POST"])
@permission_required(PERM_GESTAO)
def api_abrir():
    payload = request.get_json(silent=True) or {}
    try:
        inv = svc.abrir(_usuario(), payload.get("descricao") or "")
    except (ValueError, cps.ErroApiInventario) as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"message": f"Inventário geral aberto com {len(inv.itens)} linha(s) para contar.", "id": inv.id})


def _item_ou_404(item_id):
    item = db.session.get(Item, item_id)
    if not item:
        return None, (jsonify({"error": "Item não encontrado."}), 404)
    return item, None


@inventario_geral_bp.route("/api/inventario-geral/itens/<int:item_id>/contagem", methods=["POST"])
@permission_required(PERM_CONTAGEM)
def api_contar(item_id):
    item, erro = _item_ou_404(item_id)
    if erro:
        return erro
    try:
        svc.registrar_contagem(item, (request.get_json(silent=True) or {}).get("quantidade"), _usuario())
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return _resposta_item(item, f"Contagem registrada - {item.status}.")


@inventario_geral_bp.route("/api/inventario-geral/itens/<int:item_id>/recontar", methods=["POST"])
@permission_required(PERM_GESTAO)
def api_recontar(item_id):
    item, erro = _item_ou_404(item_id)
    if erro:
        return erro
    try:
        svc.recontar(item, _usuario())
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"message": "Contagens limpas - o item voltou para a 2ª contagem.", "item": _item_gestao(item)})


@inventario_geral_bp.route("/api/inventario-geral/produtos", methods=["GET"])
@permission_required_any(PERM_CONTAGEM, PERM_GESTAO)
def api_buscar_produtos():
    inv = svc.inventario_ativo()
    if not inv:
        return jsonify({"produtos": []})
    # Sem saldo na resposta: a busca tambem e' usada pelo operador.
    return jsonify({"produtos": [
        {"cod_produto": p.cod_produto, "codigo_interno": p.codigo_interno, "descricao": p.descricao,
         "unidade": p.unidade, "com_lote": svc.com_lote(p.tipo_controle)}
        for p in svc.buscar_produtos(inv, request.args.get("q") or "")
    ]})


@inventario_geral_bp.route("/api/inventario-geral/itens", methods=["POST"])
@permission_required(PERM_CONTAGEM)
def api_adicionar_item():
    inv = svc.inventario_ativo()
    if not inv:
        return jsonify({"error": "Nenhum inventário geral em andamento."}), 400
    payload = request.get_json(silent=True) or {}
    try:
        item = svc.adicionar_item(inv, payload.get("cod_produto"), payload.get("lote") or "", _usuario())
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return _resposta_item(item, "Item encontrado no físico adicionado ao inventário (sistema = 0).")


@inventario_geral_bp.route("/api/inventario-geral/finalizar", methods=["POST"])
@permission_required(PERM_GESTAO)
def api_finalizar():
    inv = svc.inventario_ativo()
    if not inv:
        return jsonify({"error": "Nenhum inventário geral em andamento."}), 400
    try:
        svc.finalizar(inv, _usuario())
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"message": "Inventário finalizado - confira o resultado e envie ao ERP."})


@inventario_geral_bp.route("/api/inventario-geral/simular", methods=["POST"])
@permission_required(PERM_GESTAO)
def api_simular():
    inv = svc.inventario_ativo()
    if not inv:
        return jsonify({"error": "Nenhum inventário geral em andamento."}), 400
    try:
        resultado = svc.simular_envio(inv, _usuario())
    except (ValueError, cps.ErroApiInventario) as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"message": "Simulação concluída (nada foi gravado no ERP).", **resultado})


@inventario_geral_bp.route("/api/inventario-geral/aplicar", methods=["POST"])
@permission_required(PERM_GESTAO)
def api_aplicar():
    inv = svc.inventario_ativo()
    if not inv:
        return jsonify({"error": "Nenhum inventário geral em andamento."}), 400
    try:
        resultado = svc.aplicar_envio(inv, _usuario())
    except (ValueError, cps.ErroApiInventario) as exc:
        return jsonify({"error": str(exc)}), 400
    mensagem = ("Inventário gravado no ERP." if resultado["concluido"]
                else "Parte do envio não foi aplicada - veja o retorno do ERP.")
    return jsonify({"message": mensagem, **resultado})


@inventario_geral_bp.route("/api/inventario-geral/exportar", methods=["GET"])
@permission_required(PERM_GESTAO)
def api_exportar():
    inv = svc.inventario_ativo()
    if not inv:
        return jsonify({"error": "Nenhum inventário geral em andamento."}), 400
    wb = Workbook()
    ws = wb.active
    ws.title = "Inventario geral"
    ws.append(["Código", "Descrição", "Unidade", "Lote", "1ª (sistema)", "2ª contagem", "2ª por",
               "3ª contagem", "3ª por", "Status", "Qtde final", "Diferença", "Adicionado no físico", "Recontagens"])
    for i in inv.itens:
        dif = (i.qtde_final - i.qtde_sistema) if i.qtde_final is not None else None
        ws.append([i.codigo_interno, i.descricao, i.unidade, i.lote or "", i.qtde_sistema, i.contagem2, i.contagem2_por,
                   i.contagem3, i.contagem3_por, i.status, i.qtde_final, dif, "Sim" if i.adicionado_manual else "",
                   i.recontagens])
    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    return send_file(buf, as_attachment=True, download_name=f"inventario_geral_{inv.id}.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
