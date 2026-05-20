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


@dataclass
class DownloadedDriveFile:
    data: bytes
    file_name: str
    mimetype: str


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


def extract_drive_file_id(url: str | None) -> str | None:
    raw = str(url or "").strip()
    if not raw:
        return None

    draft = decode_drive_rascunho(raw)
    if draft:
        return draft[0]

    parsed = urllib.parse.urlparse(raw)
    qs = urllib.parse.parse_qs(parsed.query)
    file_id = (qs.get("id") or [""])[0]
    if file_id:
        return file_id
    if "/file/d/" in parsed.path:
        return parsed.path.split("/file/d/", 1)[1].split("/", 1)[0] or None
    return None


def is_external_url(value: str | None) -> bool:
    raw = str(value or "").strip().lower()
    return raw.startswith("http://") or raw.startswith("https://")


def _drive_credentials():
    from google.auth.transport.requests import Request
    from google.auth.exceptions import RefreshError
    from google.oauth2.credentials import Credentials
    from google.oauth2 import service_account

    scopes = ["https://www.googleapis.com/auth/drive"]
    oauth_json = str(current_app.config.get("GOOGLE_DRIVE_OAUTH_TOKEN_JSON") or "").strip()
    oauth_file = str(current_app.config.get("GOOGLE_DRIVE_OAUTH_TOKEN_FILE") or "").strip()
    raw_json = str(current_app.config.get("GOOGLE_DRIVE_SERVICE_ACCOUNT_JSON") or "").strip()
    file_path = str(current_app.config.get("GOOGLE_DRIVE_SERVICE_ACCOUNT_FILE") or "").strip()

    if raw_json:
        info = json.loads(raw_json)
        return service_account.Credentials.from_service_account_info(info, scopes=scopes)
    if file_path:
        return service_account.Credentials.from_service_account_file(file_path, scopes=scopes)

    try:
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
    except RefreshError as exc:
        msg = str(exc)
        if "disabled_client" in msg:
            raise RuntimeError(
                "OAuth do Google Drive desativado no Google Cloud. Reative o OAuth client "
                "ou configure GOOGLE_DRIVE_SERVICE_ACCOUNT_JSON/FILE e compartilhe a pasta "
                "do Drive com o e-mail da service account."
            ) from exc
        raise

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
    try:
        created = service.files().create(
            body=metadata,
            media_body=media,
            fields="id,name,webViewLink",
            supportsAllDrives=True,
        ).execute()
    except Exception as exc:
        msg = str(exc)
        if "storageQuotaExceeded" in msg or "Service Accounts do not have storage quota" in msg:
            raise RuntimeError(
                "A service account consegue acessar a pasta, mas nao consegue enviar arquivos "
                "para uma pasta de Meu Drive porque service accounts nao possuem cota de "
                "armazenamento propria. Use um Drive compartilhado para essa pasta ou configure "
                "um OAuth ativo para uploads."
            ) from exc
        raise
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


def download_drive_url(url: str, default_name: str | None = None) -> DownloadedDriveFile:
    file_id = extract_drive_file_id(url)
    if not file_id:
        raise RuntimeError("Nao foi possivel identificar o arquivo no Drive.")

    service = _drive_service()
    metadata = service.files().get(
        fileId=file_id,
        fields="name,mimeType",
        supportsAllDrives=True,
    ).execute()
    data = service.files().get_media(
        fileId=file_id,
        supportsAllDrives=True,
    ).execute()
    return DownloadedDriveFile(
        data=data,
        file_name=metadata.get("name") or default_name or file_id,
        mimetype=metadata.get("mimeType") or "application/octet-stream",
    )


def delete_drive_url(url: str | None) -> None:
    file_id = extract_drive_file_id(url)
    if not file_id:
        return
    _drive_service().files().delete(fileId=file_id, supportsAllDrives=True).execute()
