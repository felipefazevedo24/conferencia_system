"""Gera token OAuth do Google Drive usando a conta dona do armazenamento.

Rode em uma maquina com navegador:
    python scripts/gerar_google_drive_oauth_token.py \
      --client-secret caminho/client_secret.json \
      --token-file google-drive-oauth-token.json

Depois envie o token gerado para o PythonAnywhere e configure:
    GOOGLE_DRIVE_OAUTH_TOKEN_FILE=/home/usuario/secrets/google-drive-oauth-token.json
"""
from __future__ import annotations

import argparse
from pathlib import Path


SCOPES = ["https://www.googleapis.com/auth/drive"]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--client-secret", required=True, help="JSON do OAuth Client baixado do Google Cloud.")
    parser.add_argument("--token-file", default="google-drive-oauth-token.json", help="Arquivo de saida do token.")
    args = parser.parse_args()

    from google_auth_oauthlib.flow import InstalledAppFlow

    flow = InstalledAppFlow.from_client_secrets_file(args.client_secret, SCOPES)
    creds = flow.run_local_server(port=0, access_type="offline", prompt="consent")

    token_path = Path(args.token_file).expanduser().resolve()
    token_path.write_text(creds.to_json(), encoding="utf-8")
    print(f"Token OAuth salvo em: {token_path}")
    print("Use esse arquivo no PythonAnywhere em GOOGLE_DRIVE_OAUTH_TOKEN_FILE.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
