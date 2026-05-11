from __future__ import annotations

import io
import json
import mimetypes
import os
import urllib.parse
from dataclasses import dataclass
from typing import Any

from flask import current_app
from werkzeug.datastructures import FileStorage


DRIVE_PREFIX = "gdrive|"


@dataclass
class StoredPhoto:
    file_name: str
    file_path: str
    url: str
    provider: str = "local"
    file_id: str | None = None


def storage_mode() -> str:
    mode = str(current_app.config.get("EXPEDICAO_FOTOS_STORAGE") or "").strip().lower()
    if mode:
        return mode
    return "local"


def using_drive() -> bool:
    return storage_mode() in {"drive", "google_drive", "google-drive"}


def public_drive_url(file_id: str) -> str:
    return f"https://drive.google.com/thumbnail?id={urllib.parse.quote(file_id)}&sz=w1600"


def drive_view_url(file_id: str) -> str:
    return f"https://drive.google.com/file/d/{urllib.parse.quote(file_id)}/view"


def encode_drive_rascunho(file_id: str, file_name: str) -> str:
    return f"{DRIVE_PREFIX}{file_id}|{urllib.parse.quote(file_name)}"


def decode_drive_rascunho(raw: str) -> tuple[str, str] | None:
    value = str(raw or "")
    if not value.startswith(DRIVE_PREFIX):
        return None
    rest = value[len(DRIVE_PREFIX):]
    file_id, sep, encoded_name = rest.partition("|")
    if not file_id:
        return None
    return file_id, urllib.parse.unquote(encoded_name) if sep else file_id


def is_external_url(value: str | None) -> bool:
    raw = str(value or "").strip().lower()
    return raw.startswith("http://") or raw.startswith("https://")


def _drive_credentials():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google.oauth2 import service_account

    scopes = ["https://www.googleapis.com/auth/drive"]
    oauth_json = str(current_app.config.get("GOOGLE_DRIVE_OAUTH_TOKEN_JSON") or "").strip()
    oauth_file = str(current_app.config.get("GOOGLE_DRIVE_OAUTH_TOKEN_FILE") or "").strip()
    raw_json = str(current_app.config.get("GOOGLE_DRIVE_SERVICE_ACCOUNT_JSON") or "").strip()
    file_path = str(current_app.config.get("GOOGLE_DRIVE_SERVICE_ACCOUNT_FILE") or "").strip()

    if oauth_json:
        creds = Credentials.from_authorized_user_info(json.loads(oauth_json), scopes=scopes)
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
        return creds
    if oauth_file:
        creds = Credentials.from_authorized_user_file(oauth_file, scopes=scopes)
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
        return creds
    if raw_json:
        info = json.loads(raw_json)
        return service_account.Credentials.from_service_account_info(info, scopes=scopes)
    if file_path:
        return service_account.Credentials.from_service_account_file(file_path, scopes=scopes)
    raise RuntimeError(
        "Google Drive nao configurado: informe GOOGLE_DRIVE_OAUTH_TOKEN_JSON/FILE "
        "ou GOOGLE_DRIVE_SERVICE_ACCOUNT_JSON/FILE."
    )


def _drive_service():
    from googleapiclient.discovery import build

    return build("drive", "v3", credentials=_drive_credentials(), cache_discovery=False)


def upload_bytes_to_drive(data: bytes, file_name: str, mimetype: str | None = None) -> StoredPhoto:
    from googleapiclient.http import MediaIoBaseUpload

    folder_id = str(current_app.config.get("EXPEDICAO_GOOGLE_DRIVE_FOLDER_ID") or "").strip()
    if not folder_id:
        raise RuntimeError("EXPEDICAO_GOOGLE_DRIVE_FOLDER_ID nao configurado.")

    service = _drive_service()
    media = MediaIoBaseUpload(
        io.BytesIO(data),
        mimetype=mimetype or "application/octet-stream",
        resumable=False,
    )
    metadata: dict[str, Any] = {"name": file_name, "parents": [folder_id]}
    created = service.files().create(
        body=metadata,
        media_body=media,
        fields="id,name,webViewLink",
        supportsAllDrives=True,
    ).execute()
    file_id = created["id"]

    if str(current_app.config.get("EXPEDICAO_GOOGLE_DRIVE_PUBLIC", "1")).strip() not in {"0", "false", "False"}:
        service.permissions().create(
            fileId=file_id,
            body={"type": "anyone", "role": "reader"},
            fields="id",
            supportsAllDrives=True,
        ).execute()

    return StoredPhoto(
        file_name=created.get("name") or file_name,
        file_path=public_drive_url(file_id),
        url=public_drive_url(file_id),
        provider="drive",
        file_id=file_id,
    )


def upload_to_drive(file_storage: FileStorage, file_name: str) -> StoredPhoto:
    try:
        file_storage.stream.seek(0)
    except Exception:
        pass
    data = file_storage.read()
    if not data:
        raise RuntimeError("Arquivo vazio recebido para upload no Drive.")
    return upload_bytes_to_drive(data, file_name, file_storage.mimetype or "application/octet-stream")


def upload_path_to_drive(path: str, file_name: str | None = None) -> StoredPhoto:
    safe_path = os.path.abspath(path)
    with open(safe_path, "rb") as fh:
        data = fh.read()
    if not data:
        raise RuntimeError(f"Arquivo vazio: {path}")
    final_name = file_name or os.path.basename(path)
    mimetype = mimetypes.guess_type(final_name)[0] or "application/octet-stream"
    return upload_bytes_to_drive(data, final_name, mimetype)


def delete_drive_url(url: str | None) -> None:
    raw = str(url or "")
    if "drive.google.com" not in raw:
        return
    parsed = urllib.parse.urlparse(raw)
    qs = urllib.parse.parse_qs(parsed.query)
    file_id = (qs.get("id") or [""])[0]
    if not file_id and "/file/d/" in parsed.path:
        file_id = parsed.path.split("/file/d/", 1)[1].split("/", 1)[0]
    if not file_id:
        return
    _drive_service().files().delete(fileId=file_id, supportsAllDrives=True).execute()
