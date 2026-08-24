foreach ($port in 8000, 5173) {
    $listeners = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty OwningProcess -Unique
    foreach ($processId in $listeners) {
        if ($processId -eq $PID) { continue }
        Write-Host "Stopping port ${port} pid ${processId}"
        taskkill /PID $processId /T /F 2>$null | Out-Null
    }
}
Write-Host "ReelMaker stopped."
