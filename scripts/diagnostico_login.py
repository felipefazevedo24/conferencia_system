"""
Script de diagnóstico para problemas de login no PythonAnywhere.
Execute no console Bash do PythonAnywhere:
    cd ~/conferencia_system
    python scripts/diagnostico_login.py
"""
import os
import sys

# Garante que o projeto está no path
project_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_dir not in sys.path:
    sys.path.insert(0, project_dir)

# ============================================================
# FORCE DATABASE_URL aqui para PythonAnywhere
# ============================================================
PA_USER = "felipefazevedo"
PA_PASS = "columbiasync2026"
PA_HOST = f"{PA_USER}.mysql.pythonanywhere-services.com"
PA_DB   = f"{PA_USER}$sync"
os.environ["DATABASE_URL"] = f"mysql+pymysql://{PA_USER}:{PA_PASS}@{PA_HOST}/{PA_DB}"

DB_URL = os.environ.get("DATABASE_URL", "")
print(f"[1] DATABASE_URL configurada: {'SIM' if DB_URL else 'NÃO (usando SQLite)'}")
if DB_URL:
    safe = DB_URL.split("@")[-1] if "@" in DB_URL else DB_URL
    print(f"    Host/DB: {safe}")

from conferencia_app import create_app
app = create_app()

with app.app_context():
    from conferencia_app.extensions import db
    from conferencia_app.models import Usuario, ActiveSession

    uri = app.config["SQLALCHEMY_DATABASE_URI"]
    is_mysql = "mysql" in uri.lower()
    print(f"\n[2] Engine: {'MySQL' if is_mysql else 'SQLite'}")
    safe_uri = uri.split("@")[-1] if "@" in uri else uri
    print(f"    URI (host): {safe_uri}")

    # Testa conexão
    try:
        result = db.session.execute(db.text("SELECT 1")).scalar()
        print(f"\n[3] Conexão com banco: OK (SELECT 1 = {result})")
    except Exception as e:
        print(f"\n[3] ERRO de conexão: {e}")
        sys.exit(1)

    # Cria todas as tabelas que faltam
    print("\n[3b] Criando tabelas (se não existirem)...")
    db.create_all()
    print("     Tabelas OK")

    # Verifica tabelas
    try:
        inspector = db.inspect(db.engine)
        tables = inspector.get_table_names()
        print(f"\n[4] Tabelas encontradas ({len(tables)}): {', '.join(sorted(tables))}")
        if "usuario" not in tables:
            print("    Aviso: Tabela 'usuario' NÃO existe! Rode as migrações:")
            print("       flask db upgrade")
            sys.exit(1)
    except Exception as e:
        print(f"\n[4] Erro ao inspecionar tabelas: {e}")

    # Lista usuários
    users = Usuario.query.all()
    print(f"\n[5] Usuários cadastrados: {len(users)}")
    if not users:
        print("    Aviso: NENHUM usuário no banco! Esse é o problema.")
        print("    Criando usuário Admin padrão...")
        from werkzeug.security import generate_password_hash
        admin = Usuario(
            username="ADMIN",
            email="admin@sistema.local",
            password=generate_password_hash("admin123"),
            role="Admin",
        )
        db.session.add(admin)
        db.session.commit()
        print("    ✅ Usuário criado: ADMIN / admin123")
        print("    Aviso: TROQUE A SENHA após o primeiro login!")
    else:
        for u in users:
            tem_senha = "SIM" if u.password else "NÃO"
            hash_prefix = (u.password or "")[:20] + "..." if u.password else "-"
            print(f"    - {u.username} | role={u.role} | email={u.email or '-'} | senha={tem_senha} ({hash_prefix})")

        # Verifica se algum hash parece correto (werkzeug usa prefixos conhecidos)
        for u in users:
            if u.password and not (u.password.startswith("pbkdf2:") or u.password.startswith("scrypt:")):
                print(f"\n    Aviso: {u.username}: hash de senha não parece ser werkzeug!")
                print(f"       Valor: {u.password[:40]}...")
                print(f"       Pode ser senha em texto puro ou hash incompatível.")

    # Verifica sessões ativas
    try:
        active = ActiveSession.query.filter_by(is_active=True).count()
        total = ActiveSession.query.count()
        print(f"\n[6] Sessões: {active} ativas de {total} total")
    except Exception as e:
        print(f"\n[6] Erro ao checar sessões: {e}")

    print("\n--- Diagnóstico concluído ---")
