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
    ".pytest_tmp_local",
    ".pytest_tmp_local_run",
    "__pycache__",
    "codex_test_temp",
    "codex_pytest_tmp",
    "codex_pytest_tmp_app",
    "manual_test_runs",
    "runtime_admin_check",
    "test_tmp",
    "dist"
)

$excludePatterns = @(
    ".pytest_tmp*",
    "tmp*",
    "codex_pytest_tmp*"
)

$excludeFilePatterns = @(
    "*.pyc"
)

function Should-SkipDir([string]$name) {
    if ($excludeNames -contains $name) {
        return $true
    }

    foreach ($pattern in $excludePatterns) {
        if ($name -like $pattern) {
            return $true
        }
    }

    return $false
}

function Should-SkipFile([string]$name) {
    foreach ($pattern in $excludeFilePatterns) {
        if ($name -like $pattern) {
            return $true
        }
    }

    return $false
}

function Copy-Tree($source, $destination) {
    New-Item -ItemType Directory -Path $destination -Force | Out-Null

    try {
        $items = Get-ChildItem -LiteralPath $source -Force -ErrorAction Stop
    } catch {
        return
    }

    foreach ($item in $items) {
        if ($item.PSIsContainer) {
            if (Should-SkipDir $item.Name) {
                continue
            }

            Copy-Tree $item.FullName (Join-Path $destination $item.Name)
            continue
        }

        if (Should-SkipFile $item.Name) {
            continue
        }

        Copy-Item -LiteralPath $item.FullName -Destination (Join-Path $destination $item.Name) -Force
    }
}

if (Test-Path $stagingDir) {
    Remove-Item -LiteralPath $stagingDir -Recurse -Force
}

if (Test-Path $zipPath) {
    Remove-Item -LiteralPath $zipPath -Force
}

New-Item -ItemType Directory -Path $distDir -Force | Out-Null
New-Item -ItemType Directory -Path $stagingDir -Force | Out-Null

Copy-Tree $projectRoot $stagingDir

Compress-Archive -Path (Join-Path $stagingDir "*") -DestinationPath $zipPath -Force

Write-Host "Bundle criado em: $zipPath"
