"""Logistica > Inventario > aba Inventario ciclico (dirigido).

Duas permissoes: CONTAGEM (conferente ve so' os itens distribuidos para ele -
codigo, descricao, endereco e unidade; o servidor nao manda saldo nem diz se
bateu) e GESTAO (distribui, acompanha, libera a 2a e lanca a 3a). Regras em
services/inventario_ciclico_service.py.
"""
from __future__ import annotations

from flask import Blueprint, jsonify, redirect, request, session

from ..auth import get_base_role_permissions, is_admin_role, permission_required, permission_required_any
from ..extensions import db
from ..models import InventarioCiclicoDistribuicao as Distribuicao, InventarioCiclicoItem as Item, PermissaoAcesso, Usuario
from ..services import erp_estoque_service as erp
from ..services import inventario_ciclico_service as svc

inventario_ciclico_bp = Blueprint("inventario_ciclico", __name__)

PERM_CONTAGEM = "PAGE_INVENTARIO_CICLICO_CONTAGEM"
PERM_GESTAO = "PAGE_INVENTARIO_CICLICO_GESTAO"


def _usuario() -> str:
    return session.get("username", "desconhecido")


def _dt(valor):
    return valor.strftime("%d/%m/%Y %H:%M") if valor else None


def _item_conferente(i: Item) -> dict:
    return {
        "id": i.id, "numero": svc.numero(i.distribuicao), "codigo": i.codigo, "descricao": i.descricao,
        "unidade": i.unidade, "endereco": i.endereco, "rodada": svc.STATUS_CONFERENTE.get(i.status),
    }


def _item_gestao(i: Item) -> dict:
    return {
        "id": i.id, "numero": svc.numero(i.distribuicao), "conferente": i.distribuicao.conferente,
        "codigo": i.codigo, "descricao": i.descricao, "unidade": i.unidade, "endereco": i.endereco, "status": i.status,
        "contagem1": i.contagem1, "contagem1_por": i.contagem1_por, "contagem1_em": _dt(i.contagem1_em), "grv1": i.grv1,
        "recontagem_liberada_por": i.recontagem_liberada_por, "recontagem_liberada_em": _dt(i.recontagem_liberada_em),
        "contagem2": i.contagem2, "contagem2_por": i.contagem2_por, "contagem2_em": _dt(i.contagem2_em), "grv2": i.grv2,
        "contagem3": i.contagem3, "contagem3_por": i.contagem3_por, "contagem3_em": _dt(i.contagem3_em), "grv3": i.grv3,
        "ajuste_id": i.ajuste_id,
    }


def _distribuicao(d: Distribuicao) -> dict:
    if d.criterio == "codigo":
        filtro = ", ".join((d.codigos or "").split())
    else:
        filtro = " / ".join(x for x in (d.familia, d.grupo) if x)
    return {
        "id": d.id, "numero": svc.numero(d), "criterio": svc.CRITERIOS.get(d.criterio, d.criterio), "filtro": filtro,
        "conferente": d.conferente, "criado_por": d.criado_por, "criado_em": _dt(d.criado_em),
        "cancelado_por": d.cancelado_por, "cancelado_em": _dt(d.cancelado_em), "resumo": svc.resumo(d),
    }


def _conferentes() -> list[str]:
    """Usuarios ativos com a permissao de contar. Mesma regra do
    auth._resolve_permissions (admin > usuario > cargo > base), mas com as
    excecoes lidas uma vez so': has_permission por usuario fazia ate' 3
    consultas para cada um e deixava a tela lenta."""
    excecoes = {
        (r.scope_type, r.scope_id): bool(r.allow)
        for r in PermissaoAcesso.query.filter_by(permission_key=PERM_CONTAGEM).all()
    }
    nomes = []
    for u in Usuario.query.filter_by(ativo=True).order_by(Usuario.username).all():
        role = str(u.role or "").strip()
        if is_admin_role(role):
            pode = True
        else:
            pode = get_base_role_permissions(role).get(PERM_CONTAGEM, False)
            pode = excecoes.get(("ROLE", role), pode)
            pode = excecoes.get(("USER", u.username), pode)
        if pode:
            nomes.append(u.username)
    return nomes


# A tela e' a aba "Inventario ciclico" do Inventario; a URL antiga so' redireciona.
@inventario_ciclico_bp.route("/logistica/inventario-ciclico")
@permission_required_any(PERM_CONTAGEM, PERM_GESTAO)
def page_inventario_ciclico():
    return redirect("/logistica/inventario?aba=ciclico")


# ── Conferente ──
@inventario_ciclico_bp.route("/api/inventario-ciclico/minhas", methods=["GET"])
@permission_required(PERM_CONTAGEM)
def api_minhas():
    return jsonify({"itens": [_item_conferente(i) for i in svc.itens_do_conferente(_usuario())]})


@inventario_ciclico_bp.route("/api/inventario-ciclico/itens/<int:item_id>/contagem", methods=["POST"])
@permission_required(PERM_CONTAGEM)
def api_contar(item_id: int):
    item = db.session.get(Item, item_id)
    if not item:
        return jsonify({"error": "Item não encontrado."}), 404
    try:
        svc.registrar_contagem_conferente(item, (request.get_json(silent=True) or {}).get("quantidade"), _usuario())
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"message": "Contagem registrada."})


# ── Gestao ──
@inventario_ciclico_bp.route("/api/inventario-ciclico/opcoes", methods=["GET"])
@permission_required(PERM_GESTAO)
def api_opcoes():
    try:
        familias = svc.opcoes_familias(erp.buscar_estoque_grv())
        grv_ok = True
    except Exception:  # noqa: BLE001 - GRV fora nao derruba a tela
        familias, grv_ok = {}, False
    return jsonify({"familias": familias, "grv_ok": grv_ok, "conferentes": _conferentes()})


@inventario_ciclico_bp.route("/api/inventario-ciclico/distribuicoes", methods=["GET"])
@permission_required(PERM_GESTAO)
def api_distribuicoes():
    dists = Distribuicao.query.order_by(Distribuicao.id.desc()).limit(200).all()
    pendencias = (
        Item.query.join(Distribuicao)
        .filter(Distribuicao.cancelado_em.is_(None),
                Item.status.in_([Item.STATUS_DIVERGENTE_1A, Item.STATUS_AGUARDA_3A]))
        .order_by(Item.id)
        .all()
    )
    return jsonify({
        "distribuicoes": [_distribuicao(d) for d in dists],
        "pendencias": [_item_gestao(i) for i in pendencias],
    })


@inventario_ciclico_bp.route("/api/inventario-ciclico/previa", methods=["POST"])
@permission_required(PERM_GESTAO)
def api_previa():
    p = request.get_json(silent=True) or {}
    try:
        return jsonify(svc.previa(p.get("criterio"), p.get("familia"), p.get("grupo"), p.get("codigos")))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


@inventario_ciclico_bp.route("/api/inventario-ciclico/distribuicoes", methods=["POST"])
@permission_required(PERM_GESTAO)
def api_distribuir():
    p = request.get_json(silent=True) or {}
    conferente = str(p.get("conferente") or "").strip()
    if conferente not in _conferentes():
        return jsonify({"error": "Escolha um conferente com acesso ao inventário cíclico."}), 400
    try:
        res = svc.distribuir(p.get("criterio"), p.get("familia"), p.get("grupo"), p.get("codigos"),
                             conferente, _usuario(), p.get("itens"))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    res["distribuicao"] = _distribuicao(res["distribuicao"])
    return jsonify(res)


@inventario_ciclico_bp.route("/api/inventario-ciclico/distribuicoes/<int:dist_id>", methods=["GET"])
@permission_required(PERM_GESTAO)
def api_distribuicao(dist_id: int):
    dist = db.session.get(Distribuicao, dist_id)
    if not dist:
        return jsonify({"error": "Distribuição não encontrada."}), 404
    return jsonify({"distribuicao": _distribuicao(dist), "itens": [_item_gestao(i) for i in dist.itens]})


@inventario_ciclico_bp.route("/api/inventario-ciclico/distribuicoes/<int:dist_id>/cancelar", methods=["POST"])
@permission_required(PERM_GESTAO)
def api_cancelar(dist_id: int):
    dist = db.session.get(Distribuicao, dist_id)
    if not dist:
        return jsonify({"error": "Distribuição não encontrada."}), 404
    try:
        n = svc.cancelar_distribuicao(dist, _usuario())
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"message": f"Distribuição cancelada. {n} item(ns) em aberto saíram da lista do conferente."})


@inventario_ciclico_bp.route("/api/inventario-ciclico/itens/<int:item_id>/liberar-recontagem", methods=["POST"])
@permission_required(PERM_GESTAO)
def api_liberar(item_id: int):
    item = db.session.get(Item, item_id)
    if not item:
        return jsonify({"error": "Item não encontrado."}), 404
    try:
        svc.liberar_recontagem(item, _usuario())
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"message": f"2ª contagem liberada para {item.distribuicao.conferente}."})


@inventario_ciclico_bp.route("/api/inventario-ciclico/itens/<int:item_id>/terceira", methods=["POST"])
@permission_required(PERM_GESTAO)
def api_terceira(item_id: int):
    item = db.session.get(Item, item_id)
    if not item:
        return jsonify({"error": "Item não encontrado."}), 404
    try:
        bateu = svc.registrar_contagem_gestor(item, (request.get_json(silent=True) or {}).get("quantidade"), _usuario())
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    if bateu:
        return jsonify({"message": "Confere com o GRV. Item fechado."})
    if item.ajuste_id:
        return jsonify({"message": "Divergência confirmada. Ajuste aberto no Ajuste de Estoque."})
    return jsonify({"message": "Divergência confirmada. Já havia ajuste aberto para este código e endereço."})
