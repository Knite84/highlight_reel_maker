param([string[]]$MediaRoot = @(), [string]$ExportRoot, [switch]$Backup)
$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$arguments = @('run', '--project', (Join-Path $repo 'backend'), 'python',
    (Join-Path $repo 'backend/tools/configure_docker.py'))
foreach ($folder in $MediaRoot) { $arguments += @('--media-root', $folder) }
if ($Backup) { $arguments += '--backup' }
if ($ExportRoot) { $arguments += @('--export-root', $ExportRoot) }
& uv @arguments
if ($LASTEXITCODE -ne 0) { throw 'Docker configuration failed' }
