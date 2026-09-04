@echo off
REM ============================================================
REM  Copie este arquivo para:  instance\erp_bridge_secrets.bat
REM  (a pasta instance\ NAO vai pro git - segredos ficam locais)
REM
REM  Preencha com os MESMOS valores que estavam no
REM  start_erp_bridge_ngrok.bat (token da bridge e senha do Postgres).
REM ============================================================

REM Token que o app (Sync) manda no header Authorization: Bearer
REM Tem que ser IGUAL ao ERP_LANCAMENTO_API_TOKEN configurado no PythonAnywhere.
set ERP_BRIDGE_TOKEN=coloque-o-token-aqui

REM Senha do usuario de leitura do Postgres do ERP.
set ERP_LANCAMENTO_PG_PASSWORD=coloque-a-senha-aqui
