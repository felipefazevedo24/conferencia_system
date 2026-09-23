@echo off
REM Inicia a instancia local/interativa do Sync usada pelo RPA da M83.
cd /d "%~dp0..\.."
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_rpa.ps1" %*
