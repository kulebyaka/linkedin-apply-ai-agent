# Development server startup script for Windows PowerShell
# Kills previous instances and starts API + UI servers

$ErrorActionPreference = "SilentlyContinue"

Push-Location $PSScriptRoot\..

Write-Host "Stopping previous instances..." -ForegroundColor Yellow

# Kill processes on ports 8000, 5173-5176
@(8000, 5173, 5174, 5175, 5176) | ForEach-Object {
    $port = $_
    $conn = Get-NetTCPConnection -LocalPort $port -State Listen 2>$null
    if ($conn) {
        Stop-Process -Id $conn.OwningProcess -Force 2>$null
        Write-Host "  Killed process on port $port" -ForegroundColor Gray
    }
}

Start-Sleep -Seconds 1

Write-Host ""
Write-Host "=========================================" -ForegroundColor Cyan
Write-Host "  API: http://localhost:8000" -ForegroundColor Green
Write-Host "  UI:  http://localhost:5173" -ForegroundColor Green
Write-Host "=========================================" -ForegroundColor Cyan
Write-Host "Press Ctrl+C to stop both servers" -ForegroundColor Yellow
Write-Host ""

# Helper function to kill a process and all its children
function Stop-ProcessTree {
    param([int]$ParentId)

    # Get all child processes first (recursive)
    Get-CimInstance Win32_Process | Where-Object { $_.ParentProcessId -eq $ParentId } | ForEach-Object {
        Stop-ProcessTree -ParentId $_.ProcessId
    }

    # Then kill the parent
    Stop-Process -Id $ParentId -Force -ErrorAction SilentlyContinue
}

# Start servers as separate processes (not jobs) so we can track their PIDs
$apiProcess = Start-Process -FilePath ".\.venv\Scripts\python.exe" `
    -ArgumentList "-m", "uvicorn", "src.api.main:app", "--reload", "--host", "0.0.0.0", "--port", "8000" `
    -WorkingDirectory $PWD `
    -PassThru -NoNewWindow

$uiProcess = Start-Process -FilePath "cmd.exe" `
    -ArgumentList "/c", "npm", "run", "dev" `
    -WorkingDirectory "$PWD\ui" `
    -PassThru -NoNewWindow

Write-Host "API PID: $($apiProcess.Id), UI PID: $($uiProcess.Id)" -ForegroundColor Gray

# Wait for Ctrl+C
try {
    while ($true) {
        # Check if processes are still running
        if ($apiProcess.HasExited -and $uiProcess.HasExited) {
            Write-Host "Both servers have stopped" -ForegroundColor Red
            break
        }
        Start-Sleep -Seconds 1
    }
} finally {
    Write-Host "`nShutting down..." -ForegroundColor Yellow

    # Kill process trees (this kills node.exe children too)
    if (-not $apiProcess.HasExited) {
        Stop-ProcessTree -ParentId $apiProcess.Id
        Write-Host "  Stopped API server" -ForegroundColor Gray
    }
    if (-not $uiProcess.HasExited) {
        Stop-ProcessTree -ParentId $uiProcess.Id
        Write-Host "  Stopped UI server" -ForegroundColor Gray
    }

    # Safety net: kill any remaining processes on the ports
    @(8000, 5173, 5174, 5175, 5176) | ForEach-Object {
        $conn = Get-NetTCPConnection -LocalPort $_ -State Listen 2>$null
        if ($conn) {
            Stop-Process -Id $conn.OwningProcess -Force 2>$null
            Write-Host "  Killed remaining process on port $_" -ForegroundColor Gray
        }
    }

    Pop-Location
}
