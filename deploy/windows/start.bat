@echo off
REM Sobe o app Flask via waitress no Windows.
REM Usa o venv local. A DATABASE_URL e demais variaveis devem estar
REM definidas como variaveis de ambiente da maquina (System).

cd /d "%~dp0..\.."
set "GRV_WEB_RPA_ENABLED=1"
set "SYNC_LAUNCHER=deploy\windows\start.bat"
if exist ".venv312\Scripts\python.exe" (
    set "SYNC_VENV=.venv312"
) else (
    set "SYNC_VENV=.venv"
)
call "%SYNC_VENV%\Scripts\activate.bat"
if errorlevel 1 (
    echo [start] Falha ao ativar o ambiente %SYNC_VENV%
    exit /b 1
)

python -c "import pandas; import sqlalchemy"
if errorlevel 1 (
    echo [start] Ambiente incompativel com o backend do RPA. Reinstale requirements.txt em %SYNC_VENV%.
    exit /b 1
)

echo [start] Ambiente: %SYNC_VENV%
echo [start] Subindo waitress em 0.0.0.0:8000 com RPA do GRV habilitado
python -m waitress --host=0.0.0.0 --port=8000 wsgi:application
