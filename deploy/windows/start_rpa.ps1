param([switch]$NoBrowser)

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$AgentScript = Join-Path $ProjectRoot "rpa_agent.py"
$venvName = @(".venv312", ".venv") | Where-Object {
    Test-Path -LiteralPath (Join-Path $ProjectRoot "$_\Scripts\python.exe")
} | Select-Object -First 1

if (-not $venvName) {
    throw "Nenhum ambiente Python do Sync foi encontrado (.venv312 ou .venv)."
}

$PythonExe = Join-Path $ProjectRoot "$venvName\Scripts\python.exe"
$currentAgents = @(Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" |
    Where-Object {
        $_.CommandLine -and
        $_.CommandLine.IndexOf($ProjectRoot, [StringComparison]::OrdinalIgnoreCase) -ge 0 -and
        $_.CommandLine.IndexOf("rpa_agent.py", [StringComparison]::OrdinalIgnoreCase) -ge 0
    })

if ($currentAgents.Count -gt 0) {
    Write-Host "Agente RPA do Sync ja esta ativo (PID $($currentAgents[0].ProcessId))." -ForegroundColor Green
    exit 0
}

$oldServers = @(Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" |
    Where-Object {
        $_.CommandLine -and
        $_.CommandLine.IndexOf($ProjectRoot, [StringComparison]::OrdinalIgnoreCase) -ge 0 -and
        $_.CommandLine.IndexOf("serve_tablet.py", [StringComparison]::OrdinalIgnoreCase) -ge 0
    })
foreach ($oldServer in $oldServers) {
    Write-Host "Encerrando servidor local substituido pelo agente (PID $($oldServer.ProcessId))..." -ForegroundColor Yellow
    Stop-Process -Id $oldServer.ProcessId
}

$agentToken = [Environment]::GetEnvironmentVariable("RPA_AGENT_TOKEN", "User")
if ([string]::IsNullOrWhiteSpace($agentToken)) {
    throw "RPA_AGENT_TOKEN nao esta configurado no usuario Windows. Execute configurar_agente_rpa.ps1."
}

$serverUrl = [Environment]::GetEnvironmentVariable("RPA_AGENT_SERVER_URL", "User")
if ([string]::IsNullOrWhiteSpace($serverUrl)) {
    $serverUrl = "https://homologacao.columbiamachine.com.br"
}
$agentId = [Environment]::GetEnvironmentVariable("RPA_AGENT_ID", "User")
if ([string]::IsNullOrWhiteSpace($agentId)) {
    $agentId = "columbia-grv-hml-01"
}

$env:RPA_AGENT_TOKEN = $agentToken
$env:RPA_AGENT_SERVER_URL = $serverUrl
$env:RPA_AGENT_ID = $agentId

Push-Location $ProjectRoot
try {
    & $PythonExe -c "import flask; import pandas; import requests; import sqlalchemy"
    if ($LASTEXITCODE -ne 0) {
        throw "O ambiente $venvName nao possui as dependencias do agente RPA."
    }

    Write-Host "========================================" -ForegroundColor DarkCyan
    Write-Host "SYNC - AGENTE WINDOWS DO RPA" -ForegroundColor Cyan
    Write-Host "========================================" -ForegroundColor DarkCyan
    Write-Host "Python: $PythonExe"
    Write-Host "Diretorio: $ProjectRoot"
    Write-Host "Entrypoint: $AgentScript"
    Write-Host "Servidor: $serverUrl"
    Write-Host "Agente: $agentId"
    Write-Host "Desktop: sessao interativa do usuario $env:USERNAME"
    Write-Host "========================================" -ForegroundColor DarkCyan

    & $PythonExe $AgentScript
    exit $LASTEXITCODE
} finally {
    Pop-Location
}
