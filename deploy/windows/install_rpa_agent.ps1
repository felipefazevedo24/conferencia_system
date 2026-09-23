param(
    [ValidateSet("Homologacao", "Producao")]
    [string]$Ambiente = "Producao",
    [string]$Usuario = ([Security.Principal.WindowsIdentity]::GetCurrent().Name)
)

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$AgentScript = Join-Path $ProjectRoot "rpa_agent.py"
$instance = $Ambiente.ToLowerInvariant()
$taskName = "Sync RPA Agent - $Ambiente"
$suffix = if ($Ambiente -eq "Producao") { "_PRODUCAO" } else { "" }
$tokenName = "RPA_AGENT_TOKEN$suffix"
$urlName = "RPA_AGENT_SERVER_URL$suffix"
$idName = "RPA_AGENT_ID$suffix"
$defaultUrl = if ($Ambiente -eq "Producao") { "https://sync.columbiamachine.com.br" } else { "https://homologacao.columbiamachine.com.br" }
$defaultId = if ($Ambiente -eq "Producao") { "columbia-grv-prod-01" } else { "columbia-grv-hml-01" }

$venv = @(".venv312", ".venv") | Where-Object {
    Test-Path -LiteralPath (Join-Path $ProjectRoot "$_\Scripts\pythonw.exe")
} | Select-Object -First 1
if (-not $venv) { throw "pythonw.exe nao encontrado em .venv312 ou .venv." }
$pythonw = Join-Path $ProjectRoot "$venv\Scripts\pythonw.exe"
if (-not (Test-Path -LiteralPath $AgentScript)) { throw "rpa_agent.py nao encontrado." }
New-Item -ItemType Directory -Path (Join-Path $ProjectRoot "logs") -Force | Out-Null

$token = [Environment]::GetEnvironmentVariable($tokenName, "User")
if ([string]::IsNullOrWhiteSpace($token)) { throw "$tokenName nao configurado no usuario Windows." }
$serverUrl = [Environment]::GetEnvironmentVariable($urlName, "User")
if ([string]::IsNullOrWhiteSpace($serverUrl)) { $serverUrl = $defaultUrl }
$agentId = [Environment]::GetEnvironmentVariable($idName, "User")
if ([string]::IsNullOrWhiteSpace($agentId)) { $agentId = $defaultId }

$legacyTask = if ($Ambiente -eq "Producao") { "Columbia_Apontamento_Agrupado_Web_Producao" } else { "Columbia_Apontamento_Agrupado_Web" }
if (Get-ScheduledTask -TaskName $legacyTask -ErrorAction SilentlyContinue) {
    Stop-ScheduledTask -TaskName $legacyTask -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $legacyTask -Confirm:$false
}
if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
    Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
}

$arguments = "`"$AgentScript`" --instance $instance"
$action = New-ScheduledTaskAction -Execute $pythonw -Argument $arguments -WorkingDirectory $ProjectRoot
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $Usuario
$principal = New-ScheduledTaskPrincipal -UserId $Usuario -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) -StartWhenAvailable -Hidden
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Description "Agente RPA do Sync em background ($Ambiente)" -Force | Out-Null
Start-ScheduledTask -TaskName $taskName

$deadline = (Get-Date).AddSeconds(30)
do {
    Start-Sleep -Seconds 2
    $process = Get-CimInstance Win32_Process | Where-Object {
        $_.Name -eq "pythonw.exe" -and $_.CommandLine -match "rpa_agent\.py" -and $_.CommandLine -match "--instance $instance"
    } | Select-Object -First 1
} until ($process -or (Get-Date) -ge $deadline)
if (-not $process) { throw "A tarefa foi criada, mas o processo pythonw nao iniciou." }

$headers = @{ Authorization = "Bearer $token"; "X-RPA-Agent-ID" = $agentId }
$heartbeat = $null
try {
    Start-Sleep -Seconds 4
    $heartbeat = Invoke-RestMethod "$serverUrl/api/rpa/agent/status" -Headers $headers -TimeoutSec 20
} catch {
    Write-Warning "Processo iniciado; o status remoto ainda nao respondeu: $($_.Exception.Message)"
}

Write-Host "Agente instalado em background." -ForegroundColor Green
Write-Host "Ambiente: $instance"
Write-Host "Tarefa: $taskName"
Write-Host "Usuario: $Usuario"
Write-Host "Python: $pythonw"
Write-Host "PID: $($process.ProcessId)"
Write-Host "Servidor: $serverUrl"
Write-Host "Agent ID: $agentId"
if ($heartbeat) {
    Write-Host "Executor online: $($heartbeat.executor_online)"
    Write-Host "GRV disponivel: $($heartbeat.grv_disponivel)"
}
