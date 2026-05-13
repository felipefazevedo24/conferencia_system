"""Executa um ciclo do envio automatico de NF-e emitida.

Uso em tarefa agendada:
    python scripts/run_nfe_email_once.py
"""
from __future__ import annotations

import json
import sys

from conferencia_app import create_app
from conferencia_app.services.nfe_email_scheduler import executar_ciclo


def main() -> int:
    app = create_app()
    resumo = executar_ciclo(app)
    print(json.dumps(resumo, ensure_ascii=False, indent=2, default=str))
    return 0 if resumo.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
