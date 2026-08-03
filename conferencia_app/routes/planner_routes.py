"""Modulo de Planejamento de Tarefas (Kanban estilo Trello, integrado ao Sync)."""

from __future__ import annotations

from datetime import date, datetime

from flask import Blueprint, jsonify, render_template, request, session

from ..auth import has_permission, is_admin_role, permission_required
from ..extensions import db
from ..models import (
    PlannerBoard,
    PlannerCard,
    PlannerCardComment,
    PlannerCardLabel,
    PlannerChecklistItem,
    PlannerColumn,
    PlannerLabel,
    Usuario,
)


planner_bp = Blueprint("planner", __name__)
PERM = "PAGE_PLANEJAMENTO_TAREFAS"
DEFAULT_BOARD_NAME = "Planejamento Sync"
PRIORIDADES_VALIDAS = {"Baixa", "Media", "Alta", "Critica"}
DEFAULT_COLUNAS = [
    {"titulo": "Backlog", "color": "#2563eb", "is_done": False},
    {"titulo": "Em andamento", "color": "#f59e0b", "is_done": False},
    {"titulo": "Revisao", "color": "#8b5cf6", "is_done": False},
    {"titulo": "Concluido", "color": "#10b981", "is_done": True},
]
DEFAULT_LABELS = [
    {"nome": "Bloqueio", "color": "#dc2626"},
    {"nome": "Melhoria", "color": "#2563eb"},
    {"nome": "Urgente", "color": "#d97706"},
    {"nome": "Cliente", "color": "#0d9488"},
]


def _user() -> str:
    return (session.get("username") or "sistema").strip() or "sistema"


def _parse_date(value: str | None) -> date | None:
    txt = str(value or "").strip()
    if not txt:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(txt, fmt).date()
        except ValueError:
            continue
    return None


def _parse_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    txt = str(value or "").strip().lower()
    return txt in {"1", "true", "sim", "yes", "on"}


def _admin_responsaveis() -> list[str]:
    users = Usuario.query.order_by(Usuario.username.asc()).all()
    nomes = []
    for user in users:
        if is_admin_role(user.role) or has_permission("PAGE_ADMIN_DASHBOARD", username=user.username, role=user.role):
            nomes.append(user.username)
    return sorted(set(nomes), key=lambda x: x.casefold())


def _seed_default_columns(board: PlannerBoard) -> None:
    now = datetime.now()
    for idx, col in enumerate(DEFAULT_COLUNAS):
        db.session.add(
            PlannerColumn(
                board_id=board.id,
                titulo=col["titulo"],
                color=col["color"],
                is_done=bool(col["is_done"]),
                order_index=idx,
                criado_por=_user(),
                criado_em=now,
                atualizado_em=now,
            )
        )


def _seed_default_labels(board: PlannerBoard) -> None:
    now = datetime.now()
    for idx, label in enumerate(DEFAULT_LABELS):
        db.session.add(
            PlannerLabel(
                board_id=board.id,
                nome=label["nome"],
                color=label["color"],
                order_index=idx,
                criado_por=_user(),
                criado_em=now,
            )
        )


def _get_or_create_board() -> PlannerBoard:
    board = PlannerBoard.query.filter_by(nome=DEFAULT_BOARD_NAME).first()
    if board:
        if not board.colunas:
            _seed_default_columns(board)
        if not board.labels:
            _seed_default_labels(board)
            board.atualizado_em = datetime.now()
            db.session.commit()
        return board

    board = PlannerBoard(
        nome=DEFAULT_BOARD_NAME,
        criado_por=_user(),
        criado_em=datetime.now(),
        atualizado_em=datetime.now(),
    )
    db.session.add(board)
    db.session.flush()
    _seed_default_columns(board)
    _seed_default_labels(board)
    db.session.commit()
    return board


def _serialize_label(item: PlannerLabel) -> dict:
    return {
        "id": item.id,
        "nome": item.nome,
        "color": item.color,
        "order_index": item.order_index,
    }


def _serialize_comment(item: PlannerCardComment) -> dict:
    return {
        "id": item.id,
        "texto": item.texto,
        "criado_por": item.criado_por,
        "criado_em": item.criado_em.strftime("%d/%m/%Y %H:%M") if item.criado_em else "",
    }


def _serialize_checklist_item(item: PlannerChecklistItem) -> dict:
    return {
        "id": item.id,
        "texto": item.texto,
        "is_done": bool(item.is_done),
        "order_index": item.order_index,
    }


def _serialize_card(card: PlannerCard, done_column: bool) -> dict:
    hoje = date.today()
    overdue = bool(card.prazo and card.prazo < hoje and not done_column)
    checklist = sorted(card.checklist_itens, key=lambda r: (r.order_index, r.id))
    checklist_done = sum(1 for item in checklist if item.is_done)
    label_links = sorted(card.labels, key=lambda r: r.id)
    return {
        "id": card.id,
        "column_id": card.column_id,
        "titulo": card.titulo,
        "descricao": card.descricao or "",
        "prioridade": card.prioridade,
        "responsavel": card.responsavel or "",
        "prazo": card.prazo.strftime("%Y-%m-%d") if card.prazo else "",
        "order_index": card.order_index,
        "criado_por": card.criado_por,
        "atualizado_por": card.atualizado_por or "",
        "criado_em": card.criado_em.strftime("%d/%m/%Y %H:%M") if card.criado_em else "",
        "atualizado_em": card.atualizado_em.strftime("%d/%m/%Y %H:%M") if card.atualizado_em else "",
        "overdue": overdue,
        "labels": [_serialize_label(link.label) for link in label_links if link.label],
        "label_ids": [link.label_id for link in label_links],
        "comments": [_serialize_comment(c) for c in card.comentarios],
        "checklist": [_serialize_checklist_item(i) for i in checklist],
        "checklist_total": len(checklist),
        "checklist_done": checklist_done,
    }


def _serialize_column(col: PlannerColumn) -> dict:
    cards = sorted(col.cards, key=lambda c: (c.order_index, c.id))
    return {
        "id": col.id,
        "board_id": col.board_id,
        "titulo": col.titulo,
        "color": col.color,
        "is_done": bool(col.is_done),
        "order_index": col.order_index,
        "cards": [_serialize_card(c, bool(col.is_done)) for c in cards],
    }


def _compute_kpis(board: PlannerBoard) -> dict:
    today = date.today()
    total = 0
    done = 0
    overdue = 0
    doing = 0
    for col in board.colunas:
        col_cards = len(col.cards)
        total += col_cards
        if col.is_done:
            done += col_cards
            continue
        doing += col_cards
        overdue += sum(1 for c in col.cards if c.prazo and c.prazo < today)

    return {
        "total_cards": total,
        "done_cards": done,
        "open_cards": doing,
        "overdue_cards": overdue,
        "columns": len(board.colunas),
    }


def _reindex_cards(column_id: int) -> None:
    cards = (
        PlannerCard.query
        .filter_by(column_id=column_id)
        .order_by(PlannerCard.order_index.asc(), PlannerCard.id.asc())
        .all()
    )
    now = datetime.now()
    for idx, card in enumerate(cards):
        if card.order_index != idx:
            card.order_index = idx
            card.atualizado_em = now


def _sync_card_labels(card: PlannerCard, label_ids: list[int]) -> None:
    valid_ids = [int(v) for v in label_ids if str(v).strip().isdigit()]
    valid_set = set(valid_ids)

    existing = {row.label_id: row for row in card.labels}
    for label_id, row in list(existing.items()):
        if label_id not in valid_set:
            db.session.delete(row)

    to_add = [label_id for label_id in valid_ids if label_id not in existing]
    if to_add:
        labels = PlannerLabel.query.filter(PlannerLabel.id.in_(to_add)).all()
        for label in labels:
            db.session.add(PlannerCardLabel(card_id=card.id, label_id=label.id, criado_em=datetime.now()))


def _create_checklist_items(card: PlannerCard, texts: list[str]) -> None:
    now = datetime.now()
    for idx, raw in enumerate(texts):
        texto = str(raw or "").strip()
        if not texto:
            continue
        db.session.add(
            PlannerChecklistItem(
                card_id=card.id,
                texto=texto[:240],
                is_done=False,
                order_index=idx,
                criado_por=_user(),
                criado_em=now,
                atualizado_em=now,
            )
        )


@planner_bp.route("/planejamento")
@permission_required(PERM, "Admin")
def planejamento_page():
    return render_template(
        "planejamento.html",
        user=session.get("username", "Usuario"),
        user_role=session.get("role", ""),
    )


@planner_bp.route("/api/planejamento/board", methods=["GET"])
@permission_required(PERM, "Admin")
def api_get_board():
    board = _get_or_create_board()
    columns = sorted(board.colunas, key=lambda c: (c.order_index, c.id))
    labels = sorted(board.labels, key=lambda l: (l.order_index, l.id))
    return jsonify({
        "board": {
            "id": board.id,
            "nome": board.nome,
            "columns": [_serialize_column(c) for c in columns],
            "labels": [_serialize_label(l) for l in labels],
            "kpis": _compute_kpis(board),
            "responsaveis_admin": _admin_responsaveis(),
        }
    })


@planner_bp.route("/api/planejamento/labels", methods=["POST"])
@permission_required(PERM, "Admin")
def api_create_label():
    payload = request.get_json(silent=True) or {}
    nome = str(payload.get("nome") or "").strip()
    if not nome:
        return jsonify({"error": "Nome da etiqueta é obrigatório."}), 400

    board = _get_or_create_board()
    max_order = db.session.query(db.func.max(PlannerLabel.order_index)).filter_by(board_id=board.id).scalar()
    next_order = int(max_order or 0) + 1 if max_order is not None else 0

    label = PlannerLabel(
        board_id=board.id,
        nome=nome[:60],
        color=(str(payload.get("color") or "#0f62c9")[:20] or "#0f62c9"),
        order_index=next_order,
        criado_por=_user(),
        criado_em=datetime.now(),
    )
    db.session.add(label)
    board.atualizado_em = datetime.now()
    db.session.commit()
    return jsonify({"sucesso": True, "label": _serialize_label(label)})


@planner_bp.route("/api/planejamento/labels/<int:label_id>", methods=["DELETE"])
@permission_required(PERM, "Admin")
def api_delete_label(label_id: int):
    label = PlannerLabel.query.get_or_404(label_id)
    PlannerCardLabel.query.filter_by(label_id=label.id).delete()
    db.session.delete(label)
    db.session.commit()
    return jsonify({"sucesso": True})


@planner_bp.route("/api/planejamento/columns", methods=["POST"])
@permission_required(PERM, "Admin")
def api_create_column():
    payload = request.get_json(silent=True) or {}
    titulo = str(payload.get("titulo") or "").strip()
    if not titulo:
        return jsonify({"error": "Titulo da coluna é obrigatório."}), 400

    board = _get_or_create_board()
    max_order = db.session.query(db.func.max(PlannerColumn.order_index)).filter_by(board_id=board.id).scalar()
    next_order = int(max_order or 0) + 1 if max_order is not None else 0

    col = PlannerColumn(
        board_id=board.id,
        titulo=titulo[:80],
        color=(str(payload.get("color") or "#0f62c9")[:20] or "#0f62c9"),
        is_done=_parse_bool(payload.get("is_done")),
        order_index=next_order,
        criado_por=_user(),
        criado_em=datetime.now(),
        atualizado_em=datetime.now(),
    )
    db.session.add(col)
    board.atualizado_em = datetime.now()
    db.session.commit()
    return jsonify({"sucesso": True, "column": _serialize_column(col)})


@planner_bp.route("/api/planejamento/columns/reorder", methods=["POST"])
@permission_required(PERM, "Admin")
def api_reorder_columns():
    payload = request.get_json(silent=True) or {}
    ids = payload.get("column_ids") or []
    if not isinstance(ids, list) or not ids:
        return jsonify({"error": "Informe column_ids com a ordem desejada."}), 400

    board = _get_or_create_board()
    cols = PlannerColumn.query.filter(PlannerColumn.id.in_(ids), PlannerColumn.board_id == board.id).all()
    col_map = {c.id: c for c in cols}
    if len(col_map) != len(ids):
        return jsonify({"error": "Uma ou mais colunas não pertencem ao board."}), 400

    now = datetime.now()
    for idx, col_id in enumerate(ids):
        col = col_map[col_id]
        col.order_index = idx
        col.atualizado_em = now
    board.atualizado_em = now
    db.session.commit()
    return jsonify({"sucesso": True})


@planner_bp.route("/api/planejamento/columns/<int:column_id>", methods=["PATCH"])
@permission_required(PERM, "Admin")
def api_update_column(column_id: int):
    col = PlannerColumn.query.get_or_404(column_id)
    payload = request.get_json(silent=True) or {}

    titulo = payload.get("titulo")
    if titulo is not None:
        titulo = str(titulo).strip()
        if not titulo:
            return jsonify({"error": "Titulo da coluna é obrigatório."}), 400
        col.titulo = titulo[:80]

    if "color" in payload:
        color = str(payload.get("color") or "").strip()[:20]
        col.color = color or "#0f62c9"

    if "is_done" in payload:
        col.is_done = _parse_bool(payload.get("is_done"))
        if col.is_done:
            now = datetime.now()
            for card in col.cards:
                if card.concluido_em is None:
                    card.concluido_em = now
                card.atualizado_em = now

    col.atualizado_em = datetime.now()
    db.session.commit()
    return jsonify({"sucesso": True, "column": _serialize_column(col)})


@planner_bp.route("/api/planejamento/columns/<int:column_id>", methods=["DELETE"])
@permission_required(PERM, "Admin")
def api_delete_column(column_id: int):
    col = PlannerColumn.query.get_or_404(column_id)
    if col.cards:
        return jsonify({"error": "A coluna ainda possui cards. Mova ou exclua os cards antes."}), 409

    board_id = col.board_id
    db.session.delete(col)
    db.session.flush()

    cols = PlannerColumn.query.filter_by(board_id=board_id).order_by(PlannerColumn.order_index.asc(), PlannerColumn.id.asc()).all()
    now = datetime.now()
    for idx, row in enumerate(cols):
        row.order_index = idx
        row.atualizado_em = now
    db.session.commit()
    return jsonify({"sucesso": True})


@planner_bp.route("/api/planejamento/cards", methods=["POST"])
@permission_required(PERM, "Admin")
def api_create_card():
    payload = request.get_json(silent=True) or {}
    titulo = str(payload.get("titulo") or "").strip()
    column_id = payload.get("column_id")
    if not titulo:
        return jsonify({"error": "Titulo da tarefa é obrigatório."}), 400
    if not column_id:
        return jsonify({"error": "Coluna da tarefa é obrigatória."}), 400

    col = PlannerColumn.query.get_or_404(int(column_id))
    max_order = db.session.query(db.func.max(PlannerCard.order_index)).filter_by(column_id=col.id).scalar()
    next_order = int(max_order or 0) + 1 if max_order is not None else 0

    prioridade = str(payload.get("prioridade") or "Media").strip().title()
    if prioridade not in PRIORIDADES_VALIDAS:
        prioridade = "Media"

    card = PlannerCard(
        column_id=col.id,
        titulo=titulo[:180],
        descricao=str(payload.get("descricao") or "")[:8000],
        prioridade=prioridade,
        responsavel=str(payload.get("responsavel") or "")[:100] or None,
        prazo=_parse_date(payload.get("prazo")),
        order_index=next_order,
        criado_por=_user(),
        atualizado_por=_user(),
        criado_em=datetime.now(),
        atualizado_em=datetime.now(),
        concluido_em=datetime.now() if col.is_done else None,
    )
    db.session.add(card)
    db.session.flush()

    label_ids = payload.get("label_ids") or []
    if isinstance(label_ids, list):
        _sync_card_labels(card, label_ids)

    checklist = payload.get("checklist") or []
    if isinstance(checklist, list) and checklist:
        _create_checklist_items(card, checklist)

    db.session.commit()
    return jsonify({"sucesso": True, "card": _serialize_card(card, bool(col.is_done))})


@planner_bp.route("/api/planejamento/cards/<int:card_id>", methods=["PATCH"])
@permission_required(PERM, "Admin")
def api_update_card(card_id: int):
    card = PlannerCard.query.get_or_404(card_id)
    payload = request.get_json(silent=True) or {}

    if "titulo" in payload:
        titulo = str(payload.get("titulo") or "").strip()
        if not titulo:
            return jsonify({"error": "Titulo da tarefa é obrigatório."}), 400
        card.titulo = titulo[:180]

    if "descricao" in payload:
        card.descricao = str(payload.get("descricao") or "")[:8000]

    if "responsavel" in payload:
        card.responsavel = str(payload.get("responsavel") or "")[:100] or None

    if "prazo" in payload:
        card.prazo = _parse_date(payload.get("prazo"))

    if "prioridade" in payload:
        prioridade = str(payload.get("prioridade") or "Media").strip().title()
        card.prioridade = prioridade if prioridade in PRIORIDADES_VALIDAS else "Media"

    if "column_id" in payload:
        target_col = PlannerColumn.query.get_or_404(int(payload.get("column_id")))
        if target_col.id != card.column_id:
            source_col_id = card.column_id
            card.column_id = target_col.id
            max_order = db.session.query(db.func.max(PlannerCard.order_index)).filter_by(column_id=target_col.id).scalar()
            card.order_index = int(max_order or 0) + 1 if max_order is not None else 0
            _reindex_cards(source_col_id)
            card.concluido_em = datetime.now() if target_col.is_done else None

    if "label_ids" in payload and isinstance(payload.get("label_ids"), list):
        _sync_card_labels(card, payload.get("label_ids") or [])

    card.atualizado_por = _user()
    card.atualizado_em = datetime.now()
    db.session.commit()
    done_column = bool(PlannerColumn.query.get(card.column_id).is_done)
    return jsonify({"sucesso": True, "card": _serialize_card(card, done_column)})


@planner_bp.route("/api/planejamento/cards/<int:card_id>/move", methods=["POST"])
@permission_required(PERM, "Admin")
def api_move_card(card_id: int):
    payload = request.get_json(silent=True) or {}
    to_column_id = int(payload.get("to_column_id") or 0)
    position = payload.get("position")

    if not to_column_id:
        return jsonify({"error": "to_column_id é obrigatório."}), 400

    card = PlannerCard.query.get_or_404(card_id)
    from_col_id = card.column_id
    to_col = PlannerColumn.query.get_or_404(to_column_id)

    if from_col_id == to_col.id:
        cards = (
            PlannerCard.query
            .filter_by(column_id=to_col.id)
            .order_by(PlannerCard.order_index.asc(), PlannerCard.id.asc())
            .all()
        )
        cards = [c for c in cards if c.id != card.id]
    else:
        cards = (
            PlannerCard.query
            .filter_by(column_id=to_col.id)
            .order_by(PlannerCard.order_index.asc(), PlannerCard.id.asc())
            .all()
        )

    if position is None:
        position = len(cards)
    try:
        position = int(position)
    except (TypeError, ValueError):
        position = len(cards)
    position = max(0, min(position, len(cards)))

    if from_col_id != to_col.id:
        card.column_id = to_col.id

    cards.insert(position, card)
    now = datetime.now()
    for idx, row in enumerate(cards):
        row.order_index = idx
        row.atualizado_em = now
        row.atualizado_por = _user()

    if from_col_id != to_col.id:
        _reindex_cards(from_col_id)

    card.concluido_em = now if to_col.is_done else None
    db.session.commit()
    return jsonify({"sucesso": True})


@planner_bp.route("/api/planejamento/cards/<int:card_id>/comments", methods=["POST"])
@permission_required(PERM, "Admin")
def api_add_comment(card_id: int):
    card = PlannerCard.query.get_or_404(card_id)
    payload = request.get_json(silent=True) or {}
    texto = str(payload.get("texto") or "").strip()
    if not texto:
        return jsonify({"error": "Comentário não pode ficar vazio."}), 400

    comment = PlannerCardComment(
        card_id=card.id,
        texto=texto[:8000],
        criado_por=_user(),
        criado_em=datetime.now(),
    )
    db.session.add(comment)
    card.atualizado_por = _user()
    card.atualizado_em = datetime.now()
    db.session.commit()
    return jsonify({"sucesso": True, "comment": _serialize_comment(comment)})


@planner_bp.route("/api/planejamento/cards/<int:card_id>/checklist", methods=["POST"])
@permission_required(PERM, "Admin")
def api_add_checklist_item(card_id: int):
    card = PlannerCard.query.get_or_404(card_id)
    payload = request.get_json(silent=True) or {}
    texto = str(payload.get("texto") or "").strip()
    if not texto:
        return jsonify({"error": "Item do checklist não pode ficar vazio."}), 400

    max_order = db.session.query(db.func.max(PlannerChecklistItem.order_index)).filter_by(card_id=card.id).scalar()
    next_order = int(max_order or 0) + 1 if max_order is not None else 0

    item = PlannerChecklistItem(
        card_id=card.id,
        texto=texto[:240],
        is_done=False,
        order_index=next_order,
        criado_por=_user(),
        criado_em=datetime.now(),
        atualizado_em=datetime.now(),
    )
    db.session.add(item)
    card.atualizado_por = _user()
    card.atualizado_em = datetime.now()
    db.session.commit()
    return jsonify({"sucesso": True, "item": _serialize_checklist_item(item)})


@planner_bp.route("/api/planejamento/checklist/<int:item_id>", methods=["PATCH"])
@permission_required(PERM, "Admin")
def api_update_checklist_item(item_id: int):
    item = PlannerChecklistItem.query.get_or_404(item_id)
    payload = request.get_json(silent=True) or {}

    if "texto" in payload:
        texto = str(payload.get("texto") or "").strip()
        if not texto:
            return jsonify({"error": "Item do checklist não pode ficar vazio."}), 400
        item.texto = texto[:240]

    if "is_done" in payload:
        item.is_done = _parse_bool(payload.get("is_done"))

    item.atualizado_em = datetime.now()
    item.card.atualizado_por = _user()
    item.card.atualizado_em = datetime.now()
    db.session.commit()
    return jsonify({"sucesso": True, "item": _serialize_checklist_item(item)})


@planner_bp.route("/api/planejamento/checklist/<int:item_id>", methods=["DELETE"])
@permission_required(PERM, "Admin")
def api_delete_checklist_item(item_id: int):
    item = PlannerChecklistItem.query.get_or_404(item_id)
    card_id = item.card_id
    db.session.delete(item)
    db.session.flush()

    items = (
        PlannerChecklistItem.query
        .filter_by(card_id=card_id)
        .order_by(PlannerChecklistItem.order_index.asc(), PlannerChecklistItem.id.asc())
        .all()
    )
    now = datetime.now()
    for idx, row in enumerate(items):
        row.order_index = idx
        row.atualizado_em = now

    card = PlannerCard.query.get(card_id)
    if card:
        card.atualizado_por = _user()
        card.atualizado_em = now

    db.session.commit()
    return jsonify({"sucesso": True})


@planner_bp.route("/api/planejamento/cards/<int:card_id>", methods=["DELETE"])
@permission_required(PERM, "Admin")
def api_delete_card(card_id: int):
    card = PlannerCard.query.get_or_404(card_id)
    col_id = card.column_id
    db.session.delete(card)
    db.session.flush()
    _reindex_cards(col_id)
    db.session.commit()
    return jsonify({"sucesso": True})
