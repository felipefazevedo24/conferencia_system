@echo off
setlocal
cd /d "%~dp0"

REM ============================================================
REM  Sobe a API bridge do ERP + Tailscale Funnel (URL fixa .ts.net)
REM  - Bridge (Flask) : http://localhost:%ERP_BRIDGE_PORT%
REM  - Funnel publico : https://<maquina>.<seu-tailnet>.ts.net
REM
REM  Pre-requisitos (uma vez so - ver docs/BRIDGE_ERP_ATUALIZACAO.md):
REM    1) Instalar o Tailscale e logar (tailscale up)
REM    2) Habilitar HTTPS + Funnel no admin (login.tailscale.com)
REM ============================================================

REM ---- Config NAO-secreta (pode versionar) -----------------
set ERP_BRIDGE_PORT=8088
set ERP_BRIDGE_HOST=0.0.0.0
set ERP_LANCAMENTO_PG_HOST=10.250.100.251
set ERP_LANCAMENTO_PG_PORT=5432
set ERP_LANCAMENTO_PG_DB=CPS
set ERP_LANCAMENTO_PG_USER=DevLeitura
set ERP_LANCAMENTO_PG_TABLE=tcompras

REM ---- Segredos: ficam FORA do git, em instance\ -----------
REM Crie instance\erp_bridge_secrets.bat a partir de
REM deploy\windows\erp_bridge_secrets.example.bat e preencha:
REM   set ERP_BRIDGE_TOKEN=...
REM   set ERP_LANCAMENTO_PG_PASSWORD=...
set "SECRETS=%~dp0instance\erp_bridge_secrets.bat"
if exist "%SECRETS%" (
  call "%SECRETS%"
) else (
  echo [ERRO] Faltando instance\erp_bridge_secrets.bat
  echo        Copie deploy\windows\erp_bridge_secrets.example.bat para
  echo        instance\erp_bridge_secrets.bat e preencha token e senha.
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo [ERRO] Ambiente virtual nao encontrado em .venv
  pause
  exit /b 1
)

where tailscale >nul 2>nul
if errorlevel 1 (
  echo [ERRO] tailscale nao encontrado no PATH. Instale o Tailscale antes.
  echo        https://tailscale.com/download/windows
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

echo Publicando no Tailscale Funnel (porta %ERP_BRIDGE_PORT%)...
tailscale funnel --bg %ERP_BRIDGE_PORT%
if errorlevel 1 (
  echo.
  echo [ATENCAO] O Funnel pode nao estar habilitado ainda para esta maquina.
  echo           Rode "tailscale funnel %ERP_BRIDGE_PORT%" uma vez e siga a URL
  echo           que ele mostrar para habilitar o Funnel no admin.
)

echo.
echo ==== URL publica (use no ERP_LANCAMENTO_API_URL do PythonAnywhere) ====
tailscale funnel status
echo.
echo Dica: essa URL .ts.net e FIXA - nao muda em reboot.
echo.
endlocal
