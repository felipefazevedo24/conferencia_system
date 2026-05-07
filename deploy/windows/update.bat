@echo off
REM Atualiza o projeto na VM: git pull + pip install + restart do servico/processo.
REM Roda este script com clique duplo ou via PowerShell:
REM   .\deploy\windows\update.bat

cd /d "%~dp0..\.."
echo.
echo === [1/4] Atualizando codigo via git ===
git pull
if errorlevel 1 (
    echo [update] git pull falhou. Abortando.
    pause
    exit /b 1
)

echo.
echo === [2/4] Atualizando dependencias Python ===
call .venv\Scripts\activate.bat
if errorlevel 1 (
    echo [update] Falha ao ativar venv .venv
    pause
    exit /b 1
)
python -m pip install --upgrade pip
pip install -r requirements.txt

echo.
echo === [3/4] Encerrando processos waitress (se houver) ===
REM Mata qualquer python.exe que esteja rodando waitress neste projeto.
for /f "tokens=2 delims=," %%P in ('tasklist /FI "IMAGENAME eq python.exe" /FO CSV /NH ^| findstr /R "[0-9]"') do (
    taskkill /F /PID %%~P >nul 2>&1
)

echo.
echo === [4/4] Reiniciando servico Windows (se existir) ===
sc query SyncConferencia >nul 2>&1
if %ERRORLEVEL%==0 (
    echo [update] Reiniciando servico SyncConferencia...
    sc stop SyncConferencia >nul 2>&1
    timeout /t 2 /nobreak >nul
    sc start SyncConferencia
) else (
    echo [update] Servico SyncConferencia nao encontrado.
    echo [update] Suba manualmente com: deploy\windows\start.bat
)

echo.
echo === Atualizacao concluida ===
pause
