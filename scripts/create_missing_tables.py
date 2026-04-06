#!/usr/bin/env python3
"""Script para sincronizar schema basico no MySQL do PythonAnywhere.

Uso no Bash do PythonAnywhere:
    cd ~/conferencia_system
    python scripts/create_missing_tables.py

Observacao:
    O create_app() ja executa o bootstrap da aplicacao, que agora tambem
    corrige colunas legadas da expedicao simples durante a inicializacao.
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from conferencia_app import create_app
from conferencia_app.extensions import db
from sqlalchemy import inspect


CRITICAL_TABLES = {
    "expedicao_conferencia_simples": {
        "numero_nf",
        "nome_cliente",
        "cliente_origem",
        "nf_origem",
        "consyste_document_id",
        "consyste_chave",
        "transportadora",
        "placa",
        "motorista",
        "status",
        "created_at",
        "updated_at",
        "expedido_at",
        "expedido_by",
    },
    "expedicao_conferencia_simples_foto": {
        "conferencia_id",
        "file_name",
        "file_path",
        "created_at",
    },
    "expedicao_conferencia_simples_estorno": {
        "conferencia_id",
        "solicitante",
        "motivo",
        "status",
        "admin_usuario",
        "admin_observacao",
        "resolvido_at",
        "created_at",
    },
}


app = create_app()

with app.app_context():
    inspector = inspect(db.engine)
    existing = set(inspector.get_table_names())
    print(f"Tabelas existentes ({len(existing)}):")
    for t in sorted(existing):
        print(f"  - {t}")

    needed = set(db.metadata.tables.keys())
    missing = needed - existing
    if missing:
        print(f"\nTabelas faltando ({len(missing)}):")
        for t in sorted(missing):
            print(f"  + {t}")
        print("\nCriando tabelas...")
        db.create_all()
        print("Pronto! Tabelas criadas.")
    else:
        print("\nTodas as tabelas ja existem.")

    # Verificar novamente
    new_existing = set(inspect(db.engine).get_table_names())
    still_missing = needed - new_existing
    if still_missing:
        print(f"\nATENCAO: Ainda faltam: {still_missing}")
    else:
        print("Tudo certo!")

    print("\nConferencia rapida do schema da expedicao simples:")
    inspector = inspect(db.engine)
    for table_name, expected_columns in CRITICAL_TABLES.items():
        if table_name not in new_existing:
            print(f"  [ERRO] Tabela ausente: {table_name}")
            continue

        current_columns = {col["name"] for col in inspector.get_columns(table_name)}
        missing_columns = sorted(expected_columns - current_columns)
        if missing_columns:
            print(f"  [ERRO] {table_name} ainda sem colunas: {', '.join(missing_columns)}")
        else:
            print(f"  [OK] {table_name}")
