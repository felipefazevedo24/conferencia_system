@echo off
setlocal
cd /d "%~dp0"

REM ============================================================
REM  Sobe a API bridge do ERP Lancamento + tunel ngrok (fixo)
REM  - Bridge (Flask)  : http://localhost:%ERP_BRIDGE_PORT%
REM  - ngrok           : https://copy-shadiness-justice.ngrok-free.dev
REM ============================================================

REM ---- Configuracao da bridge ------------------------------
set ERP_BRIDGE_PORT=8088
set ERP_BRIDGE_HOST=0.0.0.0
set ERP_BRIDGE_TOKEN=csync-erp-bridge-2026-7f4b9c2a91d84e0fa63d52c8

REM ---- Conexao Postgres do ERP -----------------------------
set ERP_LANCAMENTO_PG_HOST=10.250.100.251
set ERP_LANCAMENTO_PG_PORT=5432
set ERP_LANCAMENTO_PG_DB=CPS
set ERP_LANCAMENTO_PG_USER=DevLeitura
set ERP_LANCAMENTO_PG_PASSWORD=PZdyLt8i7A5@@
set ERP_LANCAMENTO_PG_TABLE=tcompras

REM ---- Dominio fixo do ngrok -------------------------------
set NGROK_DOMAIN=copy-shadiness-justice.ngrok-free.dev

REM ---- Ambiente virtual ------------------------------------
if not exist ".venv\Scripts\python.exe" (
  echo Ambiente virtual nao encontrado em .venv
  pause
  exit /b 1
)

REM ---- Verifica ngrok --------------------------------------
where ngrok >nul 2>nul
if errorlevel 1 (
  echo [ERRO] ngrok nao encontrado no PATH.
  echo        Instale em https://ngrok.com/download e adicione ao PATH.
  pause
  exit /b 1
)

REM ---- Encerra bridge antiga na porta, se houver -----------
for /f "tokens=5" %%p in ('netstat -ano ^| findstr LISTENING ^| findstr :%ERP_BRIDGE_PORT%') do (
  echo Encerrando processo antigo na porta %ERP_BRIDGE_PORT% ^(PID %%p^)...
  taskkill /PID %%p /F >nul 2>nul
)

echo.
echo Iniciando API bridge na porta %ERP_BRIDGE_PORT%...
start "ERP Bridge" cmd /k ".venv\Scripts\python.exe scripts\erp_lancamento_api_bridge.py"

echo Iniciando tunel ngrok em https://%NGROK_DOMAIN% ...
start "ngrok" cmd /k "ngrok http --url=%NGROK_DOMAIN% %ERP_BRIDGE_PORT%"

echo.
echo Bridge e ngrok iniciados em janelas separadas.
echo URL publica: https://%NGROK_DOMAIN%
echo.
endlocal
