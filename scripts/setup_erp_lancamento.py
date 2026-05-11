"""Cria/atualiza instance/erp_lancamento_config.json em producao.

Uso interativo (recomendado):
    python scripts/setup_erp_lancamento.py

Uso com argumentos:
    python scripts/setup_erp_lancamento.py \
        --host 10.250.100.251 --port 5432 --database CPS \
        --user DevLeitura --table tcompras --usuario ERP
    (a senha sera pedida via stdin para nao ficar no historico do shell)

Uso via API bridge (PythonAnywhere -> VM):
    python scripts/setup_erp_lancamento.py \
        --api-url https://erp-api.suaempresa.com \
        --api-token TOKEN_FORTE --usuario ERP

Variaveis de ambiente tambem sao aceitas como default:
    ERP_LANCAMENTO_PG_HOST, ERP_LANCAMENTO_PG_PORT,
    ERP_LANCAMENTO_PG_DB, ERP_LANCAMENTO_PG_USER,
    ERP_LANCAMENTO_PG_PASSWORD, ERP_LANCAMENTO_PG_TABLE,
    ERP_LANCAMENTO_API_URL, ERP_LANCAMENTO_API_TOKEN,
    ERP_LANCAMENTO_USUARIO

O arquivo e gravado em <repo>/instance/erp_lancamento_config.json e o
diretorio instance/ esta no .gitignore.
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
INSTANCE_DIR = REPO_ROOT / "instance"
TARGET_FILE = INSTANCE_DIR / "erp_lancamento_config.json"


def _prompt(label: str, default: str | None = None, secret: bool = False) -> str:
    sufixo = f" [{default}]" if default else ""
    while True:
        if secret:
            valor = getpass.getpass(f"{label}{sufixo}: ")
        else:
            valor = input(f"{label}{sufixo}: ").strip()
        if not valor and default is not None:
            return default
        if valor:
            return valor
        print("  -> valor obrigatorio.")


def main() -> int:
    parser = argparse.ArgumentParser(description="Configura credenciais do ERP Lancamento.")
    parser.add_argument("--host", default=os.environ.get("ERP_LANCAMENTO_PG_HOST"))
    parser.add_argument("--port", default=os.environ.get("ERP_LANCAMENTO_PG_PORT", "5432"))
    parser.add_argument("--database", default=os.environ.get("ERP_LANCAMENTO_PG_DB"))
    parser.add_argument("--user", default=os.environ.get("ERP_LANCAMENTO_PG_USER"))
    parser.add_argument("--password", default=os.environ.get("ERP_LANCAMENTO_PG_PASSWORD"))
    parser.add_argument("--table", default=os.environ.get("ERP_LANCAMENTO_PG_TABLE", "tcompras"))
    parser.add_argument("--api-url", default=os.environ.get("ERP_LANCAMENTO_API_URL"))
    parser.add_argument("--api-token", default=os.environ.get("ERP_LANCAMENTO_API_TOKEN"))
    parser.add_argument("--api-timeout", default=os.environ.get("ERP_LANCAMENTO_API_TIMEOUT", "30"))
    parser.add_argument("--usuario", default=os.environ.get("ERP_LANCAMENTO_USUARIO", "ERP"))
    parser.add_argument("--force", action="store_true", help="Sobrescreve sem perguntar.")
    parser.add_argument("--non-interactive", action="store_true", help="Falha se faltar argumento.")
    args = parser.parse_args()

    interativo = not args.non_interactive and sys.stdin.isatty()

    if interativo:
        print("Configuracao do ERP Lancamento (Postgres)")
        print(f"Arquivo destino: {TARGET_FILE}")
        if TARGET_FILE.exists() and not args.force:
            resp = input("Arquivo ja existe. Sobrescrever? [s/N]: ").strip().lower()
            if resp not in ("s", "sim", "y", "yes"):
                print("Abortado.")
                return 1
        usar_api = bool(args.api_url)
        if not usar_api:
            resp = input("Usar API bridge em vez de Postgres direto? [s/N]: ").strip().lower()
            usar_api = resp in ("s", "sim", "y", "yes")
        if usar_api:
            api_url = args.api_url or _prompt("API URL")
            api_token = args.api_token or _prompt("API token", secret=True)
            api_timeout = args.api_timeout or _prompt("API timeout", default="30")
            host = port = database = user = password = table = ""
        else:
            api_url = api_token = ""
            api_timeout = args.api_timeout or "30"
            host = args.host or _prompt("Host")
            port = args.port or _prompt("Port", default="5432")
            database = args.database or _prompt("Database")
            user = args.user or _prompt("User")
            password = args.password or _prompt("Password", secret=True)
            table = args.table or _prompt("Table", default="tcompras")
        usuario = args.usuario or _prompt("Usuario lancamento", default="ERP")
    else:
        if args.api_url:
            faltando = [nome for nome, val in [("--api-token", args.api_token)] if not val]
        else:
            faltando = [
                nome for nome, val in [
                    ("--host", args.host), ("--database", args.database),
                    ("--user", args.user), ("--password", args.password),
                ] if not val
            ]
        if faltando:
            print(f"Faltam argumentos obrigatorios: {', '.join(faltando)}", file=sys.stderr)
            return 2
        api_url = args.api_url or ""
        api_token = args.api_token or ""
        api_timeout = args.api_timeout or "30"
        host, database, user, password = args.host or "", args.database or "", args.user or "", args.password or ""
        port = args.port or "5432"
        table = args.table or "tcompras"
        usuario = args.usuario or "ERP"

    config = {
        "host": str(host).strip(),
        "port": int(str(port).strip() or 5432),
        "database": str(database).strip(),
        "user": str(user).strip(),
        "password": str(password),
        "table": str(table).strip() or "tcompras",
        "api_url": str(api_url).strip().rstrip("/"),
        "api_token": str(api_token),
        "api_timeout": int(str(api_timeout).strip() or 30),
        "usuario_lancamento": str(usuario).strip() or "ERP",
    }

    INSTANCE_DIR.mkdir(parents=True, exist_ok=True)
    TARGET_FILE.write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8")
    try:
        os.chmod(TARGET_FILE, 0o600)
    except Exception:
        pass

    seguro = {**config, "password": "***", "api_token": "***" if config.get("api_token") else ""}
    print("\nGravado com sucesso:")
    print(json.dumps(seguro, indent=2, ensure_ascii=False))
    print(f"\nLocal: {TARGET_FILE}")
    print("Reinicie o app (touch wsgi / restart) para aplicar.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
