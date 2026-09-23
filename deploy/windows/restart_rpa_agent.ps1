param(
    [ValidateSet("Homologacao", "Producao")]
    [string]$Ambiente = "Producao"
)

$ErrorActionPreference = "Stop"
$taskName = "Sync RPA Agent - $Ambiente"
if (-not (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue)) {
    throw "Tarefa nao instalada: $taskName"
}
Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
Start-Sleep -Seconds 2
Start-ScheduledTask -TaskName $taskName
Start-Sleep -Seconds 8
& (Join-Path $PSScriptRoot "status_rpa_agent.ps1") -Ambiente $Ambiente
