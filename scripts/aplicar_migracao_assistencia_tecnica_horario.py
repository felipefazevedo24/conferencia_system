"""Colunas do redesenho de Assistência Técnica por horário comercial.

O aplicativo já adiciona estas colunas sozinho no boot (conferencia_app/
bootstrap.py, `_ensure_solicitacao_nf_columns` e `_ensure_expedicao_romaneio_
columns`), como sempre foi feito neste módulo. Este script existe para
**verificar** isso em produção e para consertar o caso em que o boot falhou
em silêncio — os `_ensure_*` rodam dentro de `try/except: pass`, então uma
falha lá não aparece no log.

Também corrige os dados de `tipo_operacao_nf` (dois tipos saem de circulação,
Garantia passa a exigir retorno) — o bootstrap já faz isso sozinho também
(`_ensure_tipo_operacao_nf_ajustes`), este script só confirma.

Use o mesmo DATABASE_URL do servidor:

    DATABASE_URL='<copiado do arquivo WSGI>' python scripts/aplicar_migracao_assistencia_tecnica_horario.py

`--check` apenas verifica, sem alterar nada. Não inicia Flask nem
schedulers, não apaga dado e pode rodar quantas vezes for preciso.
"""
import argparse
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

COLUNAS = {
    "solicitacao_nf": {
        "data_necessidade": "DATE",
        "romaneio_id": "INTEGER",
    },
    "expedicao_romaneio": {
        "origem_assistencia_tecnica": "BOOLEAN NOT NULL DEFAULT 0",
    },
}

TIPOS_DESATIVADOS = ("Remessa para Conserto", "Remessa de retorno de demonstração")


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

        # Dados de tipo_operacao_nf (so' se a tabela ja' existir - modulo
        # Assistencia Tecnica completo).
        if inspector.has_table("tipo_operacao_nf"):
            with engine.connect() as conn:
                lista = ", ".join(f"'{t}'" for t in TIPOS_DESATIVADOS)
                ativos_indevidos = conn.execute(text(
                    f"SELECT nome FROM tipo_operacao_nf WHERE nome IN ({lista}) AND ativo = 1"
                )).fetchall()
                garantia_sem_retorno = conn.execute(text(
                    "SELECT 1 FROM tipo_operacao_nf WHERE nome = 'Garantia' "
                    "AND requer_retorno_padrao = 0"
                )).fetchone()
            if ativos_indevidos and not args.check:
                with engine.begin() as conn:
                    conn.execute(text(
                        f"UPDATE tipo_operacao_nf SET ativo = 0 WHERE nome IN ({lista})"
                    ))
                print(f"Desativados: {', '.join(r[0] for r in ativos_indevidos)}")
                ativos_indevidos = []
            if garantia_sem_retorno and not args.check:
                with engine.begin() as conn:
                    conn.execute(text(
                        "UPDATE tipo_operacao_nf SET requer_retorno_padrao = 1 "
                        "WHERE nome = 'Garantia'"
                    ))
                print("Garantia agora exige retorno.")
                garantia_sem_retorno = None
            if ativos_indevidos:
                print("Ainda ativos (deveriam estar desativados): " +
                      ", ".join(r[0] for r in ativos_indevidos))
                return 1
            if garantia_sem_retorno:
                print("Garantia ainda está marcada como sem retorno.")
                return 1

        print("Assistência Técnica (horário comercial): colunas e dados corretos. "
              "Nenhum dado foi apagado.")
        return 0
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
