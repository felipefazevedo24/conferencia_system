param(
    [ValidateSet("Homologacao", "Producao")]
    [string]$Ambiente = "Homologacao",
    [string]$Servidor = "",
    [string]$AgentId = ""
)

$ErrorActionPreference = "Stop"
$suffix = if ($Ambiente -eq "Producao") { "_PRODUCAO" } else { "" }
if ([string]::IsNullOrWhiteSpace($Servidor)) {
    $Servidor = if ($Ambiente -eq "Producao") { "https://sync.columbiamachine.com.br" } else { "https://homologacao.columbiamachine.com.br" }
}
if ([string]::IsNullOrWhiteSpace($AgentId)) {
    $AgentId = if ($Ambiente -eq "Producao") { "columbia-grv-prod-01" } else { "columbia-grv-hml-01" }
}
$secureToken = Read-Host "Token do agente RPA" -AsSecureString
$bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureToken)
try {
    $token = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
    if ([string]::IsNullOrWhiteSpace($token)) {
        throw "Token vazio."
    }
    [Environment]::SetEnvironmentVariable("RPA_AGENT_TOKEN$suffix", $token, "User")
    [Environment]::SetEnvironmentVariable("RPA_AGENT_SERVER_URL$suffix", $Servidor.TrimEnd('/'), "User")
    [Environment]::SetEnvironmentVariable("RPA_AGENT_ID$suffix", $AgentId, "User")
    Write-Host "Agente configurado para $Servidor com o identificador $AgentId." -ForegroundColor Green
} finally {
    if ($bstr -ne [IntPtr]::Zero) {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
    }
}
