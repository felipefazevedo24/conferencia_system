param(
    [string]$Servidor = "https://homologacao.columbiamachine.com.br",
    [string]$AgentId = "columbia-grv-hml-01"
)

$ErrorActionPreference = "Stop"
$secureToken = Read-Host "Token do agente RPA" -AsSecureString
$bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureToken)
try {
    $token = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
    if ([string]::IsNullOrWhiteSpace($token)) {
        throw "Token vazio."
    }
    [Environment]::SetEnvironmentVariable("RPA_AGENT_TOKEN", $token, "User")
    [Environment]::SetEnvironmentVariable("RPA_AGENT_SERVER_URL", $Servidor.TrimEnd('/'), "User")
    [Environment]::SetEnvironmentVariable("RPA_AGENT_ID", $AgentId, "User")
    Write-Host "Agente configurado para $Servidor com o identificador $AgentId." -ForegroundColor Green
} finally {
    if ($bstr -ne [IntPtr]::Zero) {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
    }
}
