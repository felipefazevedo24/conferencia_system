"""Colunas da expedição SEM conferência (Conferência de Expedição → romaneio).

O aplicativo já adiciona estas colunas sozinho no boot (conferencia_app/
bootstrap.py, os `_ensure_expedicao_*`), como sempre foi feito no módulo de
expedição. Este script existe para **verificar** isso em produção e para
consertar o caso em que o boot falhou em silêncio — os `_ensure_*` rodam
dentro de `try/except: pass`, então uma falha lá não aparece no log.

Use o mesmo DATABASE_URL do servidor:

    DATABASE_URL='<copiado do arquivo WSGI>' python scripts/aplicar_migracao_expedicao_sem_conferencia.py

`--check` apenas verifica, sem alterar nada. Não inicia Flask nem schedulers,
não apaga dado e pode rodar quantas vezes for preciso.
"""
import argparse
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Mesmas colunas (e mesmo SQL) dos `_ensure_*` do bootstrap.
COLUNAS = {
    "expedicao_ordem_fat": {
        "expedido_sem_conferencia": "BOOLEAN NOT NULL DEFAULT 0",
        "expedido_sem_conferencia_motivo": "VARCHAR(500)",
    },
    "expedicao_ordem_st": {
        "expedido_sem_conferencia": "BOOLEAN NOT NULL DEFAULT 0",
        "expedido_sem_conferencia_motivo": "VARCHAR(500)",
    },
    "expedicao_romaneio_nf": {
        "sem_conferencia": "BOOLEAN NOT NULL DEFAULT 0",
        "sem_conferencia_motivo": "VARCHAR(500)",
    },
    "expedicao_conferencia_simples": {
        "sem_conferencia_justificativa": "VARCHAR(500)",
    },
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if not (os.environ.get("DATABASE_URL") or os.environ.get("DB_PATH")):
        parser.error(
            "Defina DATABASE_URL (servidor) ou DB_PATH (SQLite) explicitamente. "
            "Use o mesmo banco do aplicativo."
        )

    from sqlalchemy import create_engine, inspect, text
    from conferencia_app.config import Config

    engine = create_engine(Config.SQLALCHEMY_DATABASE_URI, pool_pre_ping=True)
    try:
        inspector = inspect(engine)
        ausentes = []
        for tabela, colunas in COLUNAS.items():
            if not inspector.has_table(tabela):
                print(f"Tabela {tabela} ausente: banco incompleto, nada a fazer aqui.")
                return 1
            existentes = {c["name"] for c in inspector.get_columns(tabela)}
            for nome, tipo in colunas.items():
                if nome not in existentes:
                    ausentes.append((tabela, nome, tipo))

        if ausentes and not args.check:
            with engine.begin() as conn:
                for tabela, nome, tipo in ausentes:
                    conn.execute(text(f"ALTER TABLE {tabela} ADD COLUMN {nome} {tipo}"))
                    print(f"Adicionada {tabela}.{nome}")
            inspector = inspect(engine)
            ausentes = [
                (tabela, nome, tipo)
                for tabela, colunas in COLUNAS.items()
                for nome, tipo in colunas.items()
                if nome not in {c["name"] for c in inspector.get_columns(tabela)}
            ]

        if ausentes:
            print("Colunas ausentes: " + ", ".join(f"{t}.{n}" for t, n, _ in ausentes))
            return 1

        print("Expedição sem conferência: colunas disponíveis. Nenhum dado foi apagado.")
        return 0
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
