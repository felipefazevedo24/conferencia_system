"""Logistica > Inventario > Inventario geral.

Duas permissoes: CONTAGEM (operador le local + item + quantidade - visao
CEGA: o servidor nem manda o saldo do sistema nem a leitura de outra
pessoa) e GESTAO (abre, encerra a 2a/3a, ve o resultado por local,
recontagem, finaliza e envia ao ERP). Regras em
services/inventario_geral_service.py.
"""
from __future__ import annotations

import json
from io import BytesIO

from flask import Blueprint, jsonify, render_template, request, send_file, session
from openpyxl import Workbook

from ..auth import has_permission, permission_required, permission_required_any
from ..extensions import db
from ..models import InventarioGeral, InventarioGeralItem as Item, InventarioGeralLeitura as Leitura
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
        "leituras": [_leitura(l) for l in i.leituras if l.recontagem == (i.recontagens or 0)],
    }


def _leitura(l: Leitura) -> dict:
    return {"id": l.id, "rodada": l.rodada, "local": l.local, "quantidade": l.quantidade,
            "lido_por": l.lido_por, "lido_em": _dt(l.lido_em)}


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
        "id": inv.id, "numero": svc.numero(inv), "descricao": inv.descricao, "status": inv.status, "aberto_em": _dt(inv.aberto_em),
        "aberto_por": inv.aberto_por, "finalizado_em": _dt(inv.finalizado_em), "finalizado_por": inv.finalizado_por,
    }
    if gestao:
        envios = [{"tipo": e.tipo, "referencia": e.referencia, "status": e.status, "http": e.http_status,
                   "enviado_em": _dt(e.enviado_em), "enviado_por": e.enviado_por, "resposta": e.resposta}
                  for e in inv.envios]
        return jsonify({"inventario": cab, "resumo": svc.resumo(inv), "gestao": True,
                        "api_configurada": cps.configurada(), "gravacao_configurada": cps.configurada(gravacao=True),
                        "itens": [_item_gestao(i) for i in inv.itens], "envios": envios})
    return jsonify({"inventario": cab, "gestao": False})


@inventario_geral_bp.route("/api/inventario-geral/contagem", methods=["GET"])
@permission_required(PERM_CONTAGEM)
def api_contagem():
    """Aba Contar (operador e gestor que conta): CEGA - nada de saldo, nada
    de quantidade lida por outra pessoa."""
    inv = svc.inventario_ativo()
    if not inv:
        return jsonify({"inventario": None, "dirigidos": [], "minhas": []})
    usuario = _usuario().upper()
    dirigidos, minhas = [], []
    for i in inv.itens:
        if i.status == Item.STATUS_PENDENTE and i.recontagens:
            etapa = "Recontagem"
        elif i.status == Item.STATUS_AGUARDA_3A:
            etapa = "3ª contagem"
        else:
            etapa = None
        if etapa:
            fiz_2a = any(l.lido_por.upper() == usuario for l in i.leituras
                         if l.rodada == 2 and l.recontagem == (i.recontagens or 0))
            dirigidos.append({
                "codigo_interno": i.codigo_interno, "descricao": i.descricao, "unidade": i.unidade, "lote": i.lote,
                "etapa": etapa, "locais": svc.locais_anteriores(i),
                "pode_contar": not (etapa == "3ª contagem" and fiz_2a),
            })
        for l in i.leituras:
            if l.lido_por.upper() == usuario and l.recontagem == (i.recontagens or 0) and (
                    (l.rodada == 2 and i.status == Item.STATUS_PENDENTE)
                    or (l.rodada == 3 and i.status == Item.STATUS_AGUARDA_3A)):
                minhas.append({**_leitura(l), "codigo_interno": i.codigo_interno, "descricao": i.descricao,
                               "unidade": i.unidade, "lote": i.lote})
    minhas.sort(key=lambda m: m["id"], reverse=True)
    return jsonify({"inventario": {"id": inv.id, "status": inv.status}, "dirigidos": dirigidos, "minhas": minhas})


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


@inventario_geral_bp.route("/api/inventario-geral/produto", methods=["GET"])
@permission_required(PERM_CONTAGEM)
def api_produto():
    """Confere o codigo lido antes da quantidade (sem saldo na resposta)."""
    inv = svc.inventario_ativo()
    produto = svc.achar_produto(inv, request.args.get("codigo")) if inv else None
    if not produto:
        return jsonify({"error": "Item não encontrado entre os produtos do ERP."}), 404
    return jsonify({"codigo_interno": produto.codigo_interno, "descricao": produto.descricao,
                    "unidade": produto.unidade, "com_lote": svc.com_lote(produto.tipo_controle)})


@inventario_geral_bp.route("/api/inventario-geral/leituras", methods=["POST"])
@permission_required(PERM_CONTAGEM)
def api_registrar_leitura():
    inv = svc.inventario_ativo()
    if not inv:
        return jsonify({"error": "Nenhum inventário geral em andamento."}), 400
    payload = request.get_json(silent=True) or {}
    try:
        leitura = svc.registrar_leitura(inv, payload.get("local"), payload.get("codigo"), payload.get("lote"),
                                        payload.get("quantidade"), _usuario())
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    # Operador nao fica sabendo se bateu (contagem cega).
    return jsonify({"message": "Leitura registrada.", "leitura": _leitura(leitura)})


@inventario_geral_bp.route("/api/inventario-geral/leituras/<int:leitura_id>", methods=["DELETE"])
@permission_required_any(PERM_CONTAGEM, PERM_GESTAO)
def api_excluir_leitura(leitura_id):
    leitura = db.session.get(Leitura, leitura_id)
    if not leitura:
        return jsonify({"error": "Leitura não encontrada."}), 404
    try:
        svc.excluir_leitura(leitura, _usuario(), gestor=has_permission(PERM_GESTAO))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"message": "Leitura excluída."})


@inventario_geral_bp.route("/api/inventario-geral/encerrar/<int:rodada>", methods=["POST"])
@permission_required(PERM_GESTAO)
def api_encerrar_rodada(rodada):
    inv = svc.inventario_ativo()
    if not inv:
        return jsonify({"error": "Nenhum inventário geral em andamento."}), 400
    try:
        apurados = svc.encerrar_rodada(inv, rodada, _usuario())
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    detalhe = " · ".join(f"{st}: {n}" for st, n in apurados.items())
    return jsonify({"message": f"{rodada}ª contagem encerrada - {detalhe}.", "apurados": apurados})


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


@inventario_geral_bp.route("/api/inventario-geral/encerrar-inventario", methods=["POST"])
@permission_required(PERM_GESTAO)
def api_encerrar_inventario():
    inv = svc.inventario_ativo()
    if not inv:
        return jsonify({"error": "Nenhum inventário geral em andamento."}), 400
    try:
        svc.encerrar(inv, _usuario())
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"message": f"Inventário {svc.numero(inv)} encerrado - veja os indicadores na aba Gestão."})


@inventario_geral_bp.route("/api/inventario-geral/historico", methods=["GET"])
@permission_required(PERM_GESTAO)
def api_historico():
    numeros = svc.numeros()
    linhas = []
    for inv in svc.historico():
        r = inv.resultado
        linhas.append({
            "id": inv.id, "numero": numeros.get(inv.id), "descricao": inv.descricao,
            "aberto_em": _dt(inv.aberto_em), "aberto_por": inv.aberto_por,
            "encerrado_em": _dt(r.encerrado_em) if r else None, "encerrado_por": r.encerrado_por if r else None,
            "enviado_ao_erp": bool(r and r.enviado_ao_erp),
            "linhas_total": r.linhas_total if r else None, "linhas_contadas": r.linhas_contadas if r else None,
            "linhas_conferem": r.linhas_conferem if r else None, "linhas_sem_custo": r.linhas_sem_custo if r else None,
            "financeiro_inicial": r.financeiro_inicial if r else None, "financeiro_final": r.financeiro_final if r else None,
            "diferenca_absoluta": r.diferenca_absoluta if r else None,
            "acuracidade_itens": r.acuracidade_itens if r else None,
            "acuracidade_financeira": r.acuracidade_financeira if r else None,
        })
    return jsonify({"inventarios": linhas})


@inventario_geral_bp.route("/api/inventario-geral/exportar", methods=["GET"])
@permission_required(PERM_GESTAO)
def api_exportar():
    """Sem ?id: o inventario em andamento. Com ?id: um encerrado (com o
    financeiro calculado no encerramento)."""
    inv = db.session.get(InventarioGeral, request.args.get("id", type=int)) if request.args.get("id") else svc.inventario_ativo()
    if not inv:
        return jsonify({"error": "Nenhum inventário geral em andamento."}), 400
    custos = json.loads(inv.resultado.custos or "{}") if inv.resultado else {}
    wb = Workbook()
    ws = wb.active
    ws.title = "Inventario geral"
    ws.append(["Código", "Descrição", "Unidade", "Lote", "1ª (sistema)", "2ª contagem", "2ª por",
               "3ª contagem", "3ª por", "Status", "Qtde final", "Diferença", "Adicionado no físico", "Recontagens"]
              + (["Custo médio", "Valor sistema", "Valor final", "Diferença (R$)"] if inv.resultado else []))
    for i in inv.itens:
        dif = (i.qtde_final - i.qtde_sistema) if i.qtde_final is not None else None
        linha = [i.codigo_interno, i.descricao, i.unidade, i.lote or "", i.qtde_sistema, i.contagem2, i.contagem2_por,
                 i.contagem3, i.contagem3_por, i.status, i.qtde_final, dif, "Sim" if i.adicionado_manual else "",
                 i.recontagens]
        if inv.resultado:
            custo = custos.get((i.codigo_interno or "").upper())
            valores = [None, None, None] if custo is None or i.qtde_final is None else [
                round(i.qtde_sistema * custo, 2), round(i.qtde_final * custo, 2), round(dif * custo, 2)]
            linha += [custo] + valores
        ws.append(linha)
    ws = wb.create_sheet("Leituras por local")
    ws.append(["Código", "Descrição", "Lote", "Contagem", "Local", "Quantidade", "Lido por", "Lido em", "Recontagem"])
    for i in inv.itens:
        for l in i.leituras:
            ws.append([i.codigo_interno, i.descricao, i.lote or "", f"{l.rodada}ª", l.local, l.quantidade,
                       l.lido_por, _dt(l.lido_em), l.recontagem])
    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    return send_file(buf, as_attachment=True, download_name=f"inventario_geral_{svc.numero(inv)}.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
