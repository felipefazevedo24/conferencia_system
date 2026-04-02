# Migrar SQLite para MySQL no PythonAnywhere

## 1. Criar o banco MySQL

Na aba `Databases` do PythonAnywhere:

- defina a senha do MySQL, se ainda nao definiu
- crie um banco novo, por exemplo `sync`

O PythonAnywhere vai te mostrar algo nesse formato:

- host: `SEU_USUARIO.mysql.pythonanywhere-services.com`
- database: `SEU_USUARIO$sync`
- username: `SEU_USUARIO`

## 2. Rodar a migracao no Bash console

Ative o virtualenv e rode o script:

```bash
workon conferencia-env
cd ~/conferencia_system
python scripts/migrate_sqlite_to_mysql.py \
  --source 'sqlite:////home/SEU_USUARIO/conferencia_system/database.db' \
  --target 'mysql://SEU_USUARIO:SUA_SENHA@SEU_USUARIO.mysql.pythonanywhere-services.com/SEU_USUARIO$sync' \
  --replace
```

Se a senha tiver caractere especial, mantenha tudo entre aspas simples.

## 3. Apontar o app para MySQL

No arquivo WSGI do web app, troque:

```python
os.environ.setdefault("DB_PATH", f"{PA_PROJECT_DIR}/database.db")
```

por:

```python
os.environ.setdefault(
    "DATABASE_URL",
    "mysql://SEU_USUARIO:SUA_SENHA@SEU_USUARIO.mysql.pythonanywhere-services.com/SEU_USUARIO$sync",
)
```

Depois clique em `Reload`.

## 4. Validacao rapida

- abra o sistema
- faca login
- confira se as telas carregam dados antigos
- teste gravacao de um registro simples

## 5. Backup

Antes de trocar o app para MySQL, guarde uma copia do arquivo:

`/home/SEU_USUARIO/conferencia_system/database.db`

Assim, se precisar voltar, basta recolocar `DB_PATH` no WSGI e dar `Reload`.
