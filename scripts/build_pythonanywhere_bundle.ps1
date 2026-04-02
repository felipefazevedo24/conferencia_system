$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$distDir = Join-Path $projectRoot "dist"
$stagingDir = Join-Path $distDir "pythonanywhere_bundle"
$zipPath = Join-Path $distDir "pythonanywhere_bundle.zip"

$excludeNames = @(
    ".git",
    ".venv",
    ".pytest_cache",
    ".pytest_tmp",
    "__pycache__",
    "codex_test_temp",
    "manual_test_runs",
    "test_tmp",
    "dist"
)

if (Test-Path $stagingDir) {
    Remove-Item -LiteralPath $stagingDir -Recurse -Force
}

if (Test-Path $zipPath) {
    Remove-Item -LiteralPath $zipPath -Force
}

New-Item -ItemType Directory -Path $distDir -Force | Out-Null
New-Item -ItemType Directory -Path $stagingDir -Force | Out-Null

Get-ChildItem -LiteralPath $projectRoot -Force | Where-Object {
    $excludeNames -notcontains $_.Name
} | ForEach-Object {
    Copy-Item -LiteralPath $_.FullName -Destination $stagingDir -Recurse -Force
}

Compress-Archive -Path (Join-Path $stagingDir "*") -DestinationPath $zipPath -Force

Write-Host "Bundle criado em: $zipPath"
