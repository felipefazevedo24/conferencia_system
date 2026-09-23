param(
    [ValidateSet("Homologacao", "Producao")]
    [string]$Ambiente = "Producao"
)

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$instance = $Ambiente.ToLowerInvariant()
$taskName = "Sync RPA Agent - $Ambiente"
$suffix = if ($Ambiente -eq "Producao") { "_PRODUCAO" } else { "" }
$serverUrl = [Environment]::GetEnvironmentVariable("RPA_AGENT_SERVER_URL$suffix", "User")
if ([string]::IsNullOrWhiteSpace($serverUrl)) { $serverUrl = if ($Ambiente -eq "Producao") { "https://sync.columbiamachine.com.br" } else { "https://homologacao.columbiamachine.com.br" } }
$agentId = [Environment]::GetEnvironmentVariable("RPA_AGENT_ID$suffix", "User")
if ([string]::IsNullOrWhiteSpace($agentId)) { $agentId = if ($Ambiente -eq "Producao") { "columbia-grv-prod-01" } else { "columbia-grv-hml-01" } }
$token = [Environment]::GetEnvironmentVariable("RPA_AGENT_TOKEN$suffix", "User")
$task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
$process = Get-CimInstance Win32_Process | Where-Object {
    $_.Name -eq "pythonw.exe" -and $_.CommandLine -match "rpa_agent\.py" -and $_.CommandLine -match "--instance $instance"
} | Select-Object -First 1
$remote = $null
if (-not [string]::IsNullOrWhiteSpace($token)) {
    try {
        $remote = Invoke-RestMethod "$serverUrl/api/rpa/agent/status" -Headers @{ Authorization = "Bearer $token"; "X-RPA-Agent-ID" = $agentId } -TimeoutSec 20
    } catch { $remoteError = $_.Exception.Message }
}
$logName = if ($Ambiente -eq "Producao") { "rpa_agent_producao.log" } else { "rpa_agent.log" }
Write-Host "Ambiente: $instance"
Write-Host "Servidor: $serverUrl"
Write-Host "Scheduled Task: $(if ($task) { $task.State } else { 'Nao instalada' })"
Write-Host "Processo: $(if ($process) { 'PID ' + $process.ProcessId } else { 'Nao encontrado' })"
Write-Host "Agent ID: $agentId"
Write-Host "Hostname: $(if ($remote.executor.hostname) { $remote.executor.hostname } else { 'Indisponivel' })"
Write-Host "Usuario Windows: $(if ($remote.executor.usuario) { $remote.executor.usuario } else { 'Indisponivel' })"
Write-Host "Versao: $(if ($remote.executor.versao) { $remote.executor.versao } else { 'Indisponivel' })"
Write-Host "Ultimo heartbeat: $(if ($remote.executor.ultima_comunicacao) { $remote.executor.ultima_comunicacao } else { 'Indisponivel' })"
Write-Host "Executor: $(if ($remote.executor_online) { 'Online' } else { 'Offline' })"
Write-Host "GRV: $(if ($remote.grv_disponivel) { 'Disponivel' } else { 'Indisponivel' })"
if ($remoteError) { Write-Host "Erro remoto: $remoteError" }
Write-Host "Log: $(Join-Path $ProjectRoot "logs\$logName")"
