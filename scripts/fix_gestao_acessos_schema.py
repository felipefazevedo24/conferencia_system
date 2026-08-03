"""Hotfix de emergencia: garante as colunas/tabela de gestao de acessos no banco
em uso pela aplicacao (MySQL em producao ou SQLite local), sem depender das
migrations legadas que podem falhar por estado parcial.

Uso no servidor (PythonAnywhere):

    cd /home/felipefazevedo/conferencia_system
    source /home/felipefazevedo/.virtualenvs/conferencia-env/bin/activate
    export SKIP_APP_BOOTSTRAP=1
    python scripts/fix_gestao_acessos_schema.py
    unset SKIP_APP_BOOTSTRAP

Depois: Reload no painel Web.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Garante que o bootstrap do app nao rode ao importar (evita consultar colunas
# que ainda nao existem).
os.environ.setdefault("SKIP_APP_BOOTSTRAP", "1")

from sqlalchemy import inspect, text  # noqa: E402

from conferencia_app import create_app  # noqa: E402
from conferencia_app.extensions import db  # noqa: E402


USUARIO_COLUNAS = [
    ("ativo", "TINYINT(1) NOT NULL DEFAULT 1", "BOOLEAN NOT NULL DEFAULT 1"),
    ("ultimo_login_em", "DATETIME NULL", "DATETIME"),
    ("convite_token_hash", "VARCHAR(64) NULL", "VARCHAR(64)"),
    ("convite_expires_at", "DATETIME NULL", "DATETIME"),
    ("convite_enviado_em", "DATETIME NULL", "DATETIME"),
    ("convite_aceito_em", "DATETIME NULL", "DATETIME"),
    ("forcar_troca_senha", "TINYINT(1) NOT NULL DEFAULT 0", "BOOLEAN NOT NULL DEFAULT 0"),
    ("criado_em", "DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP", "DATETIME"),
    ("criado_por", "VARCHAR(100) NULL", "VARCHAR(100)"),
    ("atualizado_em", "DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP", "DATETIME"),
    ("atualizado_por", "VARCHAR(100) NULL", "VARCHAR(100)"),
]


def _is_sqlite(bind) -> bool:
    return bind.dialect.name == "sqlite"


def main() -> int:
    app = create_app()
    with app.app_context():
        bind = db.engine
        inspector = inspect(bind)

        if not inspector.has_table("usuario"):
            print("ERRO: tabela 'usuario' nao existe no banco atual.")
            return 1

        existentes = {c["name"] for c in inspector.get_columns("usuario")}
        sqlite = _is_sqlite(bind)
        adicionadas = []

        with bind.begin() as conn:
            for nome, ddl_mysql, ddl_sqlite in USUARIO_COLUNAS:
                if nome in existentes:
                    continue
                ddl = ddl_sqlite if sqlite else ddl_mysql
                conn.execute(text(f"ALTER TABLE usuario ADD COLUMN {nome} {ddl}"))
                adicionadas.append(nome)

            if not inspector.has_table("usuario_gestao_auditoria"):
                if sqlite:
                    conn.execute(text(
                        """
                        CREATE TABLE usuario_gestao_auditoria (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            ator_username VARCHAR(100) NOT NULL,
                            alvo_username VARCHAR(80) NOT NULL,
                            acao VARCHAR(60) NOT NULL,
                            detalhes TEXT NULL,
                            ip_address VARCHAR(64) NULL,
                            criado_em DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                        )
                        """
                    ))
                else:
                    conn.execute(text(
                        """
                        CREATE TABLE usuario_gestao_auditoria (
                            id INT AUTO_INCREMENT PRIMARY KEY,
                            ator_username VARCHAR(100) NOT NULL,
                            alvo_username VARCHAR(80) NOT NULL,
                            acao VARCHAR(60) NOT NULL,
                            detalhes TEXT NULL,
                            ip_address VARCHAR(64) NULL,
                            criado_em DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                            INDEX ix_uga_ator (ator_username),
                            INDEX ix_uga_alvo (alvo_username),
                            INDEX ix_uga_acao (acao),
                            INDEX ix_uga_criado_em (criado_em)
                        )
                        """
                    ))
                print("Tabela 'usuario_gestao_auditoria' criada.")
            else:
                print("Tabela 'usuario_gestao_auditoria' ja existe.")

        if adicionadas:
            print("Colunas adicionadas em 'usuario':", ", ".join(adicionadas))
        else:
            print("Nenhuma coluna nova necessaria em 'usuario'.")

        print("OK: schema de gestao de acessos garantido.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
