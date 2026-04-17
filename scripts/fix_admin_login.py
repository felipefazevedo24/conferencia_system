import os, sys
project_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_dir not in sys.path:
    sys.path.insert(0, project_dir)

PA_USER = "felipefazevedo"
PA_PASS = "columbiasync2026"
PA_HOST = f"{PA_USER}.mysql.pythonanywhere-services.com"
PA_DB   = f"{PA_USER}$sync"
os.environ["DATABASE_URL"] = f"mysql+pymysql://{PA_USER}:{PA_PASS}@{PA_HOST}/{PA_DB}"

from werkzeug.security import check_password_hash, generate_password_hash
from conferencia_app import create_app

app = create_app()
with app.app_context():
    from conferencia_app.models import Usuario
    from conferencia_app.extensions import db

    u = Usuario.query.filter_by(username="admin").first()
    if not u:
        u = Usuario.query.filter_by(username="ADMIN").first()

    if not u:
        print("Usuário admin não encontrado!")
        sys.exit(1)

    print(f"Usuário: {u.username}")
    print(f"Hash atual: {u.password[:30]}...")

    # Testa senha padrão
    try:
        ok = check_password_hash(u.password, "admin1234")
        print(f"Senha 'admin1234' funciona? {ok}")
    except Exception as e:
        print(f"ERRO ao verificar hash: {e}")
        ok = False

    if not ok:
        print("\nResetando senha para 'admin1234'...")
        u.password = generate_password_hash("admin1234")
        db.session.commit()
        print(f"Novo hash: {u.password[:30]}...")
        ok2 = check_password_hash(u.password, "admin1234")
        print(f"Verificação do novo hash: {ok2}")

    # Garante username maiúsculo
    if u.username != "ADMIN":
        print(f"\nCorrigindo username de '{u.username}' para 'ADMIN'...")
        u.username = "ADMIN"
        db.session.commit()

    print("\nPronto! Logue com: ADMIN / admin1234")
