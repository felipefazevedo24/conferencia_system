@echo off
setlocal
cd /d "%~dp0"

set APP_HOST=0.0.0.0
set APP_PORT=5000
set APP_THREADS=8

echo Verificando IPs IPv4 ativos deste PC:
for /f "tokens=2 delims=:" %%I in ('ipconfig ^| findstr /R /C:"IPv4"') do (
  echo  -%%I
)
echo.

echo Verificando regra de firewall para a porta %APP_PORT%...
netsh advfirewall firewall show rule name="Conferencia Tablets %APP_PORT%" >nul 2>nul
if errorlevel 1 (
  echo Regra nao encontrada. Tentando criar regra de entrada TCP %APP_PORT%...
  netsh advfirewall firewall add rule name="Conferencia Tablets %APP_PORT%" dir=in action=allow protocol=TCP localport=%APP_PORT% profile=any >nul 2>nul
  if errorlevel 1 (
    echo Nao foi possivel criar regra de firewall automaticamente.
    echo Execute este .bat como Administrador para liberar acesso externo.
  ) else (
    echo Regra de firewall criada com sucesso.
  )
) else (
  echo Regra de firewall ja existe.
)
echo.

for /f "tokens=5" %%p in ('netstat -ano ^| findstr LISTENING ^| findstr :5000') do (
  echo Encerrando processo antigo na porta 5000 ^(PID %%p^)...
  taskkill /PID %%p /F >nul 2>nul
)

if not exist ".venv\Scripts\python.exe" (
  echo Ambiente virtual nao encontrado em .venv
  pause
  exit /b 1
)

echo Instalando dependencias...
.venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 (
  echo Falha ao instalar dependencias.
  pause
  exit /b 1
)

echo Iniciando servidor para tablets na rede local...
echo.
echo Use no tablet: http://IP_DESTE_PC:%APP_PORT%
echo.
.venv\Scripts\python.exe serve_tablet.py

endlocal
