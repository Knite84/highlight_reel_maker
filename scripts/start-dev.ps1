param([switch]$SkipBuild)
$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
if (!(Test-Path -LiteralPath (Join-Path $repo 'compose.override.yaml'))) {
    throw 'Configure host folders first with scripts/configure-docker.ps1'
}
Push-Location $repo
try {
    $arguments = @('compose', 'up', '-d', '--wait', '--wait-timeout', '120')
    if (!$SkipBuild) { $arguments += '--build' }
    & docker @arguments
    if ($LASTEXITCODE -ne 0) { throw 'ReelMaker startup failed; inspect Docker Desktop logs.' }
    Write-Host 'ReelMaker is healthy. Manage it in Docker Desktop under reelmaker.'
    Write-Host 'Default URL: http://localhost:5173 (REELMAKER_PORT can override it).'
} finally { Pop-Location }
