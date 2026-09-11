from __future__ import annotations

import re
import unicodedata
from pathlib import PurePosixPath
from urllib.parse import quote, urlencode

PREVIEW_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".bmp", ".webp", ".tif", ".tiff"}
RENDER_VERSION = "sync-first-page-rgba-v1"


class DocumentError(LookupError):
    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.status = status


def safe_filename(value) -> str:
    name = str(value or "documento").replace("\\", "/").rsplit("/", 1)[-1]
    return "".join(char for char in name if ord(char) >= 32 and ord(char) != 127)[:240] or "documento"


def normalize(value) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char))
    return " ".join(re.sub(r"[^A-Z0-9]+", " ", text.upper()).split())


def compact(value) -> str:
    return normalize(value).replace(" ", "")


def segment(context: dict) -> str:
    classification = normalize(context.get("segmento"))
    if classification.startswith("MOLD"):
        return "mold"
    return "cms" if classification.startswith("CMS") else "palletizer"


def tokens(value) -> set[str]:
    ignored = {"A", "ANEXO", "ARQUIVO", "DE", "DESENHO", "DO", "E", "N1", "OS", "PDF", "REV"}
    return {token for token in normalize(value).split() if len(token) >= 2 and not token.isdecimal() and token not in ignored}


def score(document: dict, context: dict) -> float:
    candidate = f"{PurePosixPath(document['filename']).stem} {document.get('description') or ''}"
    candidate_tokens = tokens(candidate)
    item_tokens = tokens(context.get("subtitulo"))
    title_tokens = tokens(context.get("titulo"))
    description = normalize(context.get("subtitulo"))
    drawing = compact(context.get("n_desenho"))
    return (
        10 * len(item_tokens & candidate_tokens) / max(1, len(item_tokens))
        + 2 * len(title_tokens & candidate_tokens) / max(1, len(title_tokens))
        + (2.5 if len(description) >= 5 and description in normalize(candidate) else 0)
        + (4 if drawing and drawing in compact(candidate) else 0)
        + {"drawing": 0.03, "image": 0.02, "attachment": 0.01}[document["source_kind"]]
    )


def select_primary(documents: list[dict], context: dict) -> dict | None:
    allowed = {"mold": {"drawing", "image", "attachment"}, "cms": {"attachment"}, "palletizer": {"drawing"}}[segment(context)]
    candidates = []
    for document in documents:
        candidate = {**document, "source_kind": document.get("source_kind", document.get("kind"))}
        if candidate["source_kind"] not in allowed or candidate.get("from_origin"):
            continue
        if PurePosixPath(candidate["filename"]).suffix.lower() not in PREVIEW_EXTENSIONS:
            continue
        candidates.append(candidate)
    return max(candidates, key=lambda candidate: (score(candidate, context), -int(candidate["id"])), default=None)


def resolve_sources(items: list[dict], documents: list[dict], order: dict) -> dict[int, dict | None]:
    by_id = {int(item["aux_code"]): item for item in items}
    contexts = {item_id: {**item, "segmento": order.get("u_classificacao"), "titulo": order.get("titulo")} for item_id, item in by_id.items()}
    direct = {item_id: select_primary([doc for doc in documents if doc["aux_code"] == item_id], contexts[item_id]) for item_id in by_id}
    resolved = {}

    def resolve(item_id: int, visiting: set[int]) -> dict | None:
        if item_id in resolved:
            return resolved[item_id]
        if item_id in visiting or item_id not in by_id:
            return None
        context = contexts[item_id]
        own = direct[item_id]
        if segment(context) in {"cms", "mold"} and own:
            return own
        if segment(context) == "cms":
            return None
        number = compact(context.get("n_desenho"))
        if own and (not number or number in compact(f"{own['filename']} {own.get('description') or ''}")):
            return own
        if number:
            for other_id in sorted(by_id):
                other = direct[other_id]
                if other_id != item_id and other and other["source_kind"] == "drawing" and compact(by_id[other_id].get("n_desenho")) == number:
                    return other
        parent = by_id[item_id].get("os_pai")
        inherited = resolve(int(parent), visiting | {item_id}) if parent is not None else None
        return inherited or own

    for item_id in sorted(by_id):
        resolved[item_id] = resolve(item_id, set())
    return resolved


def document_url(order_number: str, aux_code: int, kind: str, document_id: int, origin: bool = False) -> str:
    route = {"drawing": "drawings", "image": "images", "attachment": "attachments"}[kind]
    return f"/api/v1/orders/{quote(order_number, safe='')}/items/{aux_code}/{route}/{document_id}" + ("?origin=true" if origin else "")


def thumbnail_url(order_number: str, aux_code: int, primary: dict | None) -> str | None:
    if primary is None:
        return None
    revision = ":".join(str(value) for value in (
        primary["source_kind"], primary["id"], primary.get("content_revision") or "unknown",
        primary["size_bytes"], primary["filename"], primary["aux_code"],
    ))
    return f"/api/v1/orders/{quote(order_number, safe='')}/items/{aux_code}/thumbnail?" + urlencode({"v": RENDER_VERSION, "document": revision})