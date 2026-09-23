@echo off
cd /d "%~dp0"
call deploy\windows\start.bat
if errorlevel 1 (
    echo.
    echo Nao foi possivel iniciar o Sync com o RPA. Consulte a mensagem acima.
    pause
    exit /b 1
)
