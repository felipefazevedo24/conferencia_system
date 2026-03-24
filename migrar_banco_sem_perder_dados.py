import sqlite3
import shutil
import os

# Caminhos
OLD_DB = 'instance/conferencia.db'
NEW_DB = 'instance/conferencia_nova.db'

# 1. Copia o banco antigo para o novo
shutil.copyfile(OLD_DB, NEW_DB)

# 2. Conecta no novo banco e remove a tabela temporária
conn = sqlite3.connect(NEW_DB)
cursor = conn.cursor()
cursor.execute('DROP TABLE IF EXISTS _alembic_tmp_checklist_recebimento')
conn.commit()

# 3. Lista todas as tabelas para conferência
cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
tabelas = [row[0] for row in cursor.fetchall()]
print('Tabelas no novo banco:', tabelas)

conn.close()

print('Banco migrado para', NEW_DB)
print('Agora, aponte sua aplicação para o novo arquivo e rode: flask db upgrade')
print('Se tudo funcionar, pode renomear/conferir o novo arquivo como conferencia.db')
