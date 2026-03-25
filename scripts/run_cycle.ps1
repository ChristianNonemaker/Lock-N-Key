# ────────────────────────────────────────────────────────────────
# DK NCAAB Pipeline — Single Smart Cycle
# ────────────────────────────────────────────────────────────────
# Run this via Windows Task Scheduler every 3-4 hours.
# It will:
#   1. Ensure Docker container dk_ncaab_pg is running
#   2. Run one smart pipeline cycle (ESPN + conditional odds)
#   3. Log output to artifacts/logs/
#
# Task Scheduler setup:
#   Trigger:  Every day, repeat every 3 hours for 24 hours
#   Action:   powershell.exe -ExecutionPolicy Bypass -File "run_cycle.ps1"
#   Start in: C:\Users\nonem\OneDrive\Desktop\Code\DK_Prediction
# ────────────────────────────────────────────────────────────────

$ErrorActionPreference = "Continue"

# Project paths
$ProjectRoot = "C:\Users\nonem\OneDrive\Desktop\Code\DK_Prediction"
$Python      = "C:\Users\nonem\OneDrive\Desktop\Code\.venv\Scripts\python.exe"
$LogDir      = "$ProjectRoot\artifacts\logs"
$Timestamp   = Get-Date -Format "yyyy-MM-dd_HH-mm"
$LogFile     = "$LogDir\cycle_$Timestamp.log"

# Ensure log directory exists
New-Item -ItemType Directory -Path $LogDir -Force | Out-Null

# Start transcript (logs all output)
Start-Transcript -Path $LogFile -Append

Write-Host "═══════════════════════════════════════════" 
Write-Host "DK NCAAB Smart Cycle — $(Get-Date)"
Write-Host "═══════════════════════════════════════════"

# ── 1. Ensure PostgreSQL container is running ───────────────────
$container = docker ps --filter "name=dk_ncaab_pg" --format "{{.Names}}" 2>$null
if ($container -ne "dk_ncaab_pg") {
    Write-Host "Starting PostgreSQL container..."
    docker start dk_ncaab_pg 2>$null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "ERROR: Could not start dk_ncaab_pg container"
        Stop-Transcript
        exit 1
    }
    # Wait for PostgreSQL to be ready
    Start-Sleep -Seconds 5
}
Write-Host "PostgreSQL container: running"

# ── 2. Run one smart cycle ──────────────────────────────────────
Set-Location $ProjectRoot
& $Python -m dk_ncaab auto --once --budget 450

Write-Host ""
Write-Host "Cycle exit code: $LASTEXITCODE"
Write-Host "Log: $LogFile"

Stop-Transcript

# ── 3. Clean up old logs (keep 7 days) ──────────────────────────
Get-ChildItem -Path $LogDir -Filter "cycle_*.log" |
    Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-7) } |
    Remove-Item -Force
