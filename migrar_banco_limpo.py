import sqlite3
import os

OLD_DB = 'instance/conferencia.db'
NEW_DB = 'instance/conferencia_limpissima.db'

# Remove novo banco se já existir
if os.path.exists(NEW_DB):
    os.remove(NEW_DB)

# Conecta nos bancos
old_conn = sqlite3.connect(OLD_DB)
new_conn = sqlite3.connect(NEW_DB)
old_cur = old_conn.cursor()
new_cur = new_conn.cursor()

# Lista todas as tabelas válidas (exclui a temporária)
old_cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name != '_alembic_tmp_checklist_recebimento'")
tabelas = [row[0] for row in old_cur.fetchall()]

for tabela in tabelas:
    # Pega o schema da tabela
    old_cur.execute(f"SELECT sql FROM sqlite_master WHERE type='table' AND name='{tabela}'")
    schema = old_cur.fetchone()[0]
    new_cur.execute(schema)
    # Copia os dados
    old_cur.execute(f"SELECT * FROM {tabela}")
    linhas = old_cur.fetchall()
    if linhas:
        placeholders = ','.join(['?'] * len(linhas[0]))
        new_cur.executemany(f"INSERT INTO {tabela} VALUES ({placeholders})", linhas)
    print(f'Tabela {tabela} copiada.')

new_conn.commit()
old_conn.close()
new_conn.close()
print('Banco limpo criado em', NEW_DB)
print('Renomeie para conferencia.db e rode flask db upgrade!')
