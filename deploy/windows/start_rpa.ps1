param(
    [int]$Porta = 8795,
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$EntryPoint = Join-Path $ProjectRoot "serve_tablet.py"
$LocalUrl = "http://127.0.0.1:$Porta/producao/rpa-agrupamento"

$venvCandidates = @(".venv312", ".venv")
$venvName = $venvCandidates | Where-Object {
    Test-Path -LiteralPath (Join-Path $ProjectRoot "$_\Scripts\python.exe")
} | Select-Object -First 1

if (-not $venvName) {
    throw "Nenhum ambiente Python do Sync foi encontrado (.venv312 ou .venv)."
}

$PythonExe = Join-Path $ProjectRoot "$venvName\Scripts\python.exe"
$listener = Get-NetTCPConnection -LocalPort $Porta -State Listen -ErrorAction SilentlyContinue |
    Select-Object -First 1

if ($listener) {
    $existing = Get-CimInstance Win32_Process -Filter "ProcessId = $($listener.OwningProcess)"
    $command = [string]$existing.CommandLine
    $isCurrentSync =
        $command.IndexOf($ProjectRoot, [StringComparison]::OrdinalIgnoreCase) -ge 0 -and
        $command.IndexOf("serve_tablet.py", [StringComparison]::OrdinalIgnoreCase) -ge 0 -and
        $command.IndexOf("--habilitar-rpa", [StringComparison]::OrdinalIgnoreCase) -ge 0
    $isLegacyRpa =
        $command.IndexOf("RPA - Consulta no Banco de Dados - Agrupamento", [StringComparison]::OrdinalIgnoreCase) -ge 0 -and
        $command.IndexOf("servidor_web.py", [StringComparison]::OrdinalIgnoreCase) -ge 0

    if ($isCurrentSync) {
        Write-Host "Sync com RPA ja esta ativo em $LocalUrl (PID $($existing.ProcessId))." -ForegroundColor Green
        if (-not $NoBrowser) {
            Start-Process $LocalUrl
        }
        exit 0
    }

    if ($isLegacyRpa) {
        Write-Host "Encerrando instancia antiga do RPA na porta $Porta (PID $($existing.ProcessId))..." -ForegroundColor Yellow
        Stop-Process -Id $existing.ProcessId
        for ($attempt = 0; $attempt -lt 30; $attempt++) {
            Start-Sleep -Milliseconds 200
            if (-not (Get-NetTCPConnection -LocalPort $Porta -State Listen -ErrorAction SilentlyContinue)) {
                break
            }
        }
    } else {
        throw "A porta $Porta pertence a outro processo (PID $($existing.ProcessId)): $command"
    }
}

$env:GRV_WEB_RPA_ENABLED = "1"
$env:SYNC_LAUNCHER = "deploy\windows\start_rpa.ps1"
$env:APP_HOST = "127.0.0.1"
$env:APP_PORT = [string]$Porta
$env:APP_DEBUG = "false"

Push-Location $ProjectRoot
try {
    & $PythonExe -c "import pandas; import sqlalchemy; import waitress"
    if ($LASTEXITCODE -ne 0) {
        throw "O ambiente $venvName nao possui as dependencias do Sync/RPA."
    }

    Write-Host "========================================" -ForegroundColor DarkCyan
    Write-Host "SYNC - INICIALIZACAO LOCAL DO RPA" -ForegroundColor Cyan
    Write-Host "========================================" -ForegroundColor DarkCyan
    Write-Host "Python: $PythonExe"
    Write-Host "Diretorio: $ProjectRoot"
    Write-Host "Entrypoint: $EntryPoint --habilitar-rpa"
    Write-Host "GRV_WEB_RPA_ENABLED: $env:GRV_WEB_RPA_ENABLED"
    Write-Host "Host: $env:APP_HOST"
    Write-Host "Porta: $env:APP_PORT"
    Write-Host "URL local: $LocalUrl"
    Write-Host "========================================" -ForegroundColor DarkCyan

    if (-not $NoBrowser) {
        Start-Job -ScriptBlock {
            param($Url)
            for ($attempt = 0; $attempt -lt 40; $attempt++) {
                try {
                    Invoke-WebRequest -Uri $Url -TimeoutSec 1 -UseBasicParsing | Out-Null
                    break
                } catch {
                    if ($_.Exception.Response -and [int]$_.Exception.Response.StatusCode -in 200, 302, 401, 403) {
                        break
                    }
                }
                Start-Sleep -Milliseconds 250
            }
            Start-Process $Url
        } -ArgumentList $LocalUrl | Out-Null
    }

    & $PythonExe $EntryPoint --habilitar-rpa
    exit $LASTEXITCODE
} finally {
    Pop-Location
}
