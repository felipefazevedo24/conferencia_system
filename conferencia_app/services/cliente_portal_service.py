"""Links publicos assinados para portal de documentos do cliente."""

from __future__ import annotations

from flask import current_app
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer


SALT_NF_PORTAL = "columbia-sync-nf-portal-v1"


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt=SALT_NF_PORTAL)


def gerar_token_nf(numero_nf: str, chave: str = "", cnpj: str = "") -> str:
    payload = {
        "numero_nf": str(numero_nf or "").strip(),
        "chave": str(chave or "").strip(),
        "cnpj": str(cnpj or "").strip(),
    }
    return _serializer().dumps(payload)


def ler_token_nf(token: str) -> dict | None:
    max_age = int(current_app.config.get("PORTAL_CLIENTE_TOKEN_MAX_AGE_SECONDS", 31536000) or 31536000)
    try:
        data = _serializer().loads(str(token or ""), max_age=max_age)
    except (BadSignature, SignatureExpired):
        return None
    return data if isinstance(data, dict) else None


def portal_base_url() -> str:
    return str(current_app.config.get("PORTAL_CLIENTE_BASE_URL") or current_app.config.get("PUBLIC_BASE_URL") or "").strip().rstrip("/")
