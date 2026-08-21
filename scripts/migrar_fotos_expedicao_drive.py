"""Migra fotos locais de expedicao para Google Drive.

Uso no PythonAnywhere:
    cd ~/conferencia_system
    source ~/.virtualenvs/conferencia-env/bin/activate
    python scripts/migrar_fotos_expedicao_drive.py

Por seguranca, o padrao NAO apaga arquivos locais. Depois de validar no Drive:
    python scripts/migrar_fotos_expedicao_drive.py --delete-local
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
aa
os.environ.setdefault("NFE_EMAIL_AUTO_ENABLED", "0")
os.environ.setdefault("ERP_SYNC_AUTO_ENABLED", "0")
os.environ.setdefault("ERP_LANCAMENTO_AUTO_ENABLED", "0")
os.environ.setdefault("FACILITIES_ALERTAS_ENABLED", "0")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from conferencia_app import create_app  # noqa: E402
from conferencia_app.extensions import db  # noqa: E402
from conferencia_app.models import (  # noqa: E402
    ExpedicaoConferenciaSimples,
    ExpedicaoConferenciaSimplesFoto,
    ExpedicaoFaturamentoItem,
)
from conferencia_app.services.expedicao_photo_storage import (  # noqa: E402
    is_external_url,
    upload_path_to_drive,
    using_drive,
)


def _resolver_local(instance_path: str, pasta: str, valor: str | None, nome: str | None = None) -> str | None:
    candidatos: list[str] = []
    for item in (valor, nome):
        raw = str(item or "").strip()
        if not raw or is_external_url(raw):
            continue
        candidatos.append(raw)
        if not os.path.isabs(raw):
            candidatos.append(os.path.join(instance_path, pasta, raw))

    for candidato in candidatos:
        if os.path.isfile(candidato):
            return candidato
    return None


def _migrar_foto_simples(app, delete_local: bool) -> tuple[int, int]:
    migradas = 0
    puladas = 0
    rows = ExpedicaoConferenciaSimplesFoto.query.order_by(ExpedicaoConferenciaSimplesFoto.id.asc()).all()
    for row in rows:
        if is_external_url(row.file_path):
            puladas += 1
            continue
        caminho = _resolver_local(app.instance_path, "expedicao_conferencia_simples", row.file_path, row.file_name)
        if not caminho:
            puladas += 1
            continue
        stored = upload_path_to_drive(caminho, row.file_name)
        row.file_path = stored.file_path
        row.file_name = stored.file_name
        db.session.add(row)
        db.session.commit()
        migradas += 1
        if delete_local:
            try:
                os.remove(caminho)
            except OSError:
                pass
        print(f"[ok] conferencia simples foto {row.id} -> Drive")
    return migradas, puladas


def _migrar_canhotos(app, delete_local: bool) -> tuple[int, int]:
    migradas = 0
    puladas = 0
    rows = ExpedicaoConferenciaSimples.query.filter(ExpedicaoConferenciaSimples.canhoto_file_path.isnot(None)).all()
    for row in rows:
        if is_external_url(row.canhoto_file_path):
            puladas += 1
            continue
        caminho = _resolver_local(
            app.instance_path,
            "expedicao_conferencia_simples",
            row.canhoto_file_path,
            row.canhoto_file_name,
        )
        if not caminho:
            puladas += 1
            continue
        stored = upload_path_to_drive(caminho, row.canhoto_file_name or os.path.basename(caminho))
        row.canhoto_file_path = stored.file_path
        row.canhoto_file_name = stored.file_name
        db.session.add(row)
        db.session.commit()
        migradas += 1
        if delete_local:
            try:
                os.remove(caminho)
            except OSError:
                pass
        print(f"[ok] canhoto conferencia simples {row.id} -> Drive")
    return migradas, puladas


def _migrar_faturamento(app, delete_local: bool) -> tuple[int, int]:
    migradas = 0
    puladas = 0
    rows = ExpedicaoFaturamentoItem.query.filter(ExpedicaoFaturamentoItem.foto_path.isnot(None)).all()
    for row in rows:
        if is_external_url(row.foto_path):
            puladas += 1
            continue
        caminho = _resolver_local(app.instance_path, "expedicao_fotos", row.foto_path, row.foto_path)
        if not caminho:
            puladas += 1
            continue
        stored = upload_path_to_drive(caminho, os.path.basename(caminho))
        row.foto_path = stored.file_path
        db.session.add(row)
        db.session.commit()
        migradas += 1
        if delete_local:
            try:
                os.remove(caminho)
            except OSError:
                pass
        print(f"[ok] faturamento item {row.id} -> Drive")
    return migradas, puladas


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url", default="", help="DATABASE_URL do app, se nao estiver exportado no console.")
    parser.add_argument("--folder-id", default="", help="ID da pasta do Google Drive.")
    parser.add_argument("--service-account-file", default="", help="Caminho do JSON da service account.")
    parser.add_argument("--oauth-token-file", default="", help="Caminho do token OAuth da conta do Drive.")
    parser.add_argument("--delete-local", action="store_true", help="Apaga o arquivo local depois de migrar com sucesso.")
    args = parser.parse_args()

    if args.database_url:
        os.environ["DATABASE_URL"] = args.database_url
    if args.folder_id:
        os.environ["EXPEDICAO_GOOGLE_DRIVE_FOLDER_ID"] = args.folder_id
    if args.service_account_file:
        os.environ["GOOGLE_DRIVE_SERVICE_ACCOUNT_FILE"] = args.service_account_file
    if args.oauth_token_file:
        os.environ["GOOGLE_DRIVE_OAUTH_TOKEN_FILE"] = args.oauth_token_file
    os.environ.setdefault("EXPEDICAO_FOTOS_STORAGE", "drive")

    app = create_app()
    with app.app_context():
        if not using_drive():
            print("EXPEDICAO_FOTOS_STORAGE precisa estar configurado como drive.", file=sys.stderr)
            return 2

        total_migradas = 0
        total_puladas = 0
        for func in (_migrar_foto_simples, _migrar_canhotos, _migrar_faturamento):
            migradas, puladas = func(app, args.delete_local)
            total_migradas += migradas
            total_puladas += puladas

        print(f"\nConcluido. Migradas: {total_migradas}. Puladas/ja migradas: {total_puladas}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
