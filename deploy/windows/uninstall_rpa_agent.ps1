param(
    [ValidateSet("Homologacao", "Producao")]
    [string]$Ambiente = "Producao"
)

$ErrorActionPreference = "Stop"
$taskName = "Sync RPA Agent - $Ambiente"
if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
    Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
    Write-Host "Tarefa removida: $taskName" -ForegroundColor Green
} else {
    Write-Host "Tarefa nao encontrada: $taskName"
}
Write-Host "Codigo, configuracao e logs foram preservados."
