$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
Push-Location $repo
try {
    & docker compose stop --timeout 45
    if ($LASTEXITCODE -ne 0) { throw 'ReelMaker stop failed; inspect Docker Desktop.' }
    Write-Host 'ReelMaker containers stopped; their published ports are released. Host files are retained.'
} finally { Pop-Location }
