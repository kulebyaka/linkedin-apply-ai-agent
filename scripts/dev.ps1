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

# Start servers in background jobs
$apiJob = Start-Job -ScriptBlock {
    Set-Location $using:PWD
    python -m uvicorn src.api.main:app --reload --host 0.0.0.0 --port 8000
}

$uiJob = Start-Job -ScriptBlock {
    Set-Location "$using:PWD\ui"
    npm run dev
}

# Stream output from both jobs
try {
    while ($true) {
        # Get and display output from both jobs
        Receive-Job -Job $apiJob, $uiJob 2>&1 | Write-Host

        # Check if jobs are still running
        if ($apiJob.State -eq "Failed" -or $uiJob.State -eq "Failed") {
            Write-Host "A server failed to start" -ForegroundColor Red
            break
        }

        Start-Sleep -Milliseconds 500
    }
} finally {
    Write-Host "`nShutting down..." -ForegroundColor Yellow
    Stop-Job -Job $apiJob, $uiJob 2>$null
    Remove-Job -Job $apiJob, $uiJob -Force 2>$null

    # Kill any remaining processes on the ports
    @(8000, 5173) | ForEach-Object {
        $conn = Get-NetTCPConnection -LocalPort $_ -State Listen 2>$null
        if ($conn) { Stop-Process -Id $conn.OwningProcess -Force 2>$null }
    }

    Pop-Location
}
