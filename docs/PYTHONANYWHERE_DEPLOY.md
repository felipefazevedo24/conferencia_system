# Deploy no PythonAnywhere

## Caminho mais simples

1. Gere o pacote de upload no seu Windows:
   `powershell -ExecutionPolicy Bypass -File .\scripts\build_pythonanywhere_bundle.ps1`
2. No PythonAnywhere, envie o arquivo `dist/pythonanywhere_bundle.zip`.
3. Extraia o zip em `/home/SEU_USUARIO/conferencia_system`.
4. Crie um web app Flask por `Manual configuration`.
5. Crie o virtualenv e instale as dependencias:
   ```bash
   mkvirtualenv --python=/usr/bin/python3.11 conferencia-env
   workon conferencia-env
   cd ~/conferencia_system
   pip install -r requirements.txt
   ```
6. No painel `Web`, aponte o `Virtualenv` para:
   `/home/SEU_USUARIO/.virtualenvs/conferencia-env`
7. No arquivo WSGI do PythonAnywhere, cole o conteudo de `deploy/pythonanywhere_wsgi.py`.
8. Troque no WSGI:
   `SEU_USUARIO`, `SECRET_KEY` e `CONSYSTE_TOKEN`.
9. Configure static files:
   URL: `/static/`
   Directory: `/home/SEU_USUARIO/conferencia_system/static`
10. Clique em `Reload`.

## Banco de dados

- Para subir rapido, use o `database.db` que ja vai no bundle.
- Se quiser MySQL depois, troque no WSGI de `DB_PATH` para `DATABASE_URL`.
- A config ja normaliza `mysql://` para `mysql+pymysql://`.

## Arquivos importantes

- O app principal fica em `wsgi.py`
- O template do WSGI do PythonAnywhere fica em `deploy/pythonanywhere_wsgi.py`
- O bundle inclui `database.db` e a pasta `instance/`

## Observacoes

- Seu app grava arquivos em `instance/`, entao essa pasta precisa subir junto.
- O caminho `Z:\PUBLICO\SNData\eReports` nao existe no PythonAnywhere; se alguma funcionalidade depender disso, ela vai precisar de um caminho alternativo por variavel de ambiente.
- Se o site subir vazio, confira se `database.db` foi enviado junto.
