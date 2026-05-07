@echo off
REM Sobe o app Flask via waitress no Windows.
REM Usa o venv local. A DATABASE_URL e demais variaveis devem estar
REM definidas como variaveis de ambiente da maquina (System).

cd /d "%~dp0..\.."
call .venv\Scripts\activate.bat
if errorlevel 1 (
    echo [start] Falha ao ativar o venv .venv
    exit /b 1
)

echo [start] Subindo waitress em 0.0.0.0:8000
python -m waitress --host=0.0.0.0 --port=8000 wsgi:application
