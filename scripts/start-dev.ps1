$repo = Split-Path -Parent $PSScriptRoot
$logs = Join-Path $env:TEMP "reelmaker-dev"
New-Item -ItemType Directory -Force -Path $logs | Out-Null

Start-Process cmd.exe -ArgumentList "/c uv run uvicorn app.main:app --host 127.0.0.1 --port 8000" `
    -WorkingDirectory (Join-Path $repo "backend") -WindowStyle Hidden `
    -RedirectStandardOutput (Join-Path $logs "backend.out.log") `
    -RedirectStandardError (Join-Path $logs "backend.err.log")

Start-Process cmd.exe -ArgumentList "/c npm run dev" `
    -WorkingDirectory (Join-Path $repo "frontend") -WindowStyle Hidden `
    -RedirectStandardOutput (Join-Path $logs "frontend.out.log") `
    -RedirectStandardError (Join-Path $logs "frontend.err.log")

Write-Host ""
Write-Host "ReelMaker is starting..."
Write-Host "  Web UI:      http://localhost:5173"
Write-Host "  Backend API: http://127.0.0.1:8000/api/health"
Write-Host "  Logs:        $logs"
Write-Host ""
Write-Host "Give it ~10 seconds, then open http://localhost:5173 in your browser."
