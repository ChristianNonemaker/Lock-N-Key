# ----------------------------------------------------------------
# DK NCAAB -- Windows Task Scheduler Setup
# ----------------------------------------------------------------
# With 40+ timed snapshot jobs, the APScheduler daemon handles all
# scheduling internally. Task Scheduler just ensures the daemon
# starts at boot and restarts if it crashes.
#
# Creates one scheduled task:
#   DK_NCAAB_Daemon -- starts the auto-collector daemon at login
#
# The daemon internally runs:
#   SAT:          15 snapshots (8am-10:30pm ET)
#   TUE/WED/THU:  12 snapshots each (9am-10:30pm ET)
#   MON/FRI/SUN:   5 snapshots each (5pm-9:30pm ET)
#   EVERY DAY:     9pm OPEN capture + 3am ESPN sweep (free)
#   Budget: ~280 API calls/month (of 500 free)
#
# Run this script ONCE as Administrator:
#   powershell -ExecutionPolicy Bypass -File setup_tasks.ps1
# ----------------------------------------------------------------

$ErrorActionPreference = "Stop"

$ProjectRoot = "C:\Users\nonem\OneDrive\Desktop\Code\DK_Prediction"
$Python      = "C:\Users\nonem\OneDrive\Desktop\Code\.venv\Scripts\python.exe"
$LogDir      = "$ProjectRoot\artifacts\logs"

# Ensure log directory exists
if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir -Force | Out-Null }

Write-Host "=============================================="
Write-Host "  DK NCAAB -- Task Scheduler Setup"
Write-Host "=============================================="
Write-Host ""

# ================================================================
# Remove old tasks if they exist (from previous setup versions)
# ================================================================
foreach ($oldTask in @("DK_NCAAB_Cycle", "DK_NCAAB_ESPN")) {
    if (Get-ScheduledTask -TaskName $oldTask -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $oldTask -Confirm:$false
        Write-Host "  Removed old task: $oldTask"
    }
}

# ================================================================
# Create the daemon wrapper script
# ================================================================
$wrapperPath = "$ProjectRoot\scripts\run_daemon.ps1"
$wrapperContent = @"
# Auto-generated daemon wrapper for Task Scheduler
# Starts the DK NCAAB auto-collector daemon with logging

`$ErrorActionPreference = "Continue"
`$ProjectRoot = "$ProjectRoot"
`$Python = "$Python"
`$LogDir = "$LogDir"

# Ensure Docker container is running
`$container = docker ps --filter "name=dk_ncaab_pg" --format "{{.Names}}" 2>`$null
if (-not `$container) {
    Write-Host "Starting Docker container dk_ncaab_pg..."
    docker start dk_ncaab_pg 2>`$null
    Start-Sleep -Seconds 5
}

# Start the daemon (blocks until shutdown)
`$timestamp = Get-Date -Format "yyyy-MM-dd_HH-mm-ss"
`$logFile = "`$LogDir\daemon_`$timestamp.log"

Set-Location `$ProjectRoot
& `$Python -m dk_ncaab auto --budget 450 2>&1 | Tee-Object -FilePath `$logFile
"@
Set-Content -Path $wrapperPath -Value $wrapperContent -Encoding UTF8
Write-Host "  Created daemon wrapper: $wrapperPath"

# ================================================================
# Task: DK_NCAAB_Daemon -- starts at login, restarts on failure
# ================================================================

Write-Host ""
Write-Host "Creating task: DK_NCAAB_Daemon..."

$daemonTrigger = New-ScheduledTaskTrigger -AtLogOn

$daemonAction = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-ExecutionPolicy Bypass -WindowStyle Hidden -File `"$wrapperPath`"" `
    -WorkingDirectory $ProjectRoot

$daemonSettings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RunOnlyIfNetworkAvailable `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 5) `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Days 365)

Register-ScheduledTask `
    -TaskName "DK_NCAAB_Daemon" `
    -Trigger $daemonTrigger `
    -Action $daemonAction `
    -Settings $daemonSettings `
    -Description "DK NCAAB: Auto-collector daemon with 40+ timed snapshots (APScheduler)" `
    -Force

Write-Host "  [OK] DK_NCAAB_Daemon created"

# ================================================================
# Summary
# ================================================================

Write-Host ""
Write-Host "======================================================"
Write-Host "Setup complete!"
Write-Host ""
Write-Host "  DK_NCAAB_Daemon"
Write-Host "    Starts at: user login"
Write-Host "    Restarts:  up to 3 times on failure (5 min delay)"
Write-Host "    Runs:      continuously via APScheduler"
Write-Host ""
Write-Host "  Internal schedule:"
Write-Host "    SAT:          15 snapshots (8am-10:30pm ET)"
Write-Host "    TUE/WED/THU:  12 snapshots each (9am-10:30pm ET)"
Write-Host "    MON/FRI/SUN:   5 snapshots each (5pm-9:30pm ET)"
Write-Host "    EVERY DAY:     9pm OPEN + 3am ESPN (free)"
Write-Host ""
Write-Host "  Budget: ~280 API calls/month of 500 free"
Write-Host "          ~220 remaining for manual snapshots"
Write-Host ""
Write-Host "Manage:"
Write-Host "  Start-ScheduledTask DK_NCAAB_Daemon     # start now"
Write-Host "  Stop-ScheduledTask DK_NCAAB_Daemon      # stop"
Write-Host "  Disable-ScheduledTask DK_NCAAB_Daemon   # pause"
Write-Host "  Unregister-ScheduledTask DK_NCAAB_Daemon # remove"
Write-Host ""
Write-Host "Or run directly in a terminal:"
Write-Host "  python -m dk_ncaab auto"
Write-Host ""
Write-Host "Logs: $LogDir"
Write-Host "======================================================"
