# ────────────────────────────────────────────────────────────────
# DK NCAAB Daemon Wrapper for Task Scheduler
# ────────────────────────────────────────────────────────────────
# Waits for Docker Desktop to initialise before starting the daemon.
# Retries PostgreSQL connectivity so we never start the Python
# process until the database is actually accepting connections.
# ────────────────────────────────────────────────────────────────

$ErrorActionPreference = "Continue"
$ProjectRoot = "C:\Users\nonem\OneDrive\Desktop\Code\DK_Prediction"
$Python      = "C:\Users\nonem\OneDrive\Desktop\Code\.venv\Scripts\python.exe"
$LogDir      = "C:\Users\nonem\OneDrive\Desktop\Code\DK_Prediction\artifacts\logs"

# Ensure log directory exists
if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir -Force | Out-Null }

$timestamp = Get-Date -Format "yyyy-MM-dd_HH-mm-ss"
$logFile   = "$LogDir\daemon_$timestamp.log"

# Helper: append to log and console
function Log($msg) {
    $line = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') [run_daemon] $msg"
    Write-Host $line
    Add-Content -Path $logFile -Value $line
}

# ── 1. Wait for Docker Desktop to be ready (up to 3 minutes) ──
$maxWaitSec  = 180
$elapsedSec  = 0
$pollSec     = 5

Log "Waiting for Docker Desktop to initialise..."

while ($elapsedSec -lt $maxWaitSec) {
    $dockerOk = $false
    try {
        $info = docker info 2>&1
        if ($LASTEXITCODE -eq 0) { $dockerOk = $true }
    } catch {}

    if ($dockerOk) {
        Log "Docker Desktop is ready (waited ${elapsedSec}s)."
        break
    }
    Start-Sleep -Seconds $pollSec
    $elapsedSec += $pollSec
}

if ($elapsedSec -ge $maxWaitSec) {
    Log "ERROR: Docker Desktop did not start within ${maxWaitSec}s. Aborting."
    exit 1
}

# ── 2. Ensure the PostgreSQL container is running ──────────────
$container = docker ps --filter "name=dk_ncaab_pg" --format "{{.Names}}" 2>$null
if ($container -ne "dk_ncaab_pg") {
    Log "Starting PostgreSQL container dk_ncaab_pg..."
    docker start dk_ncaab_pg 2>$null
    if ($LASTEXITCODE -ne 0) {
        Log "ERROR: Could not start dk_ncaab_pg container."
        exit 1
    }
}

# ── 3. Wait until PostgreSQL is accepting connections (up to 60s)
$pgReady    = $false
$pgMaxWait  = 60
$pgElapsed  = 0

Log "Waiting for PostgreSQL to accept connections..."

while ($pgElapsed -lt $pgMaxWait) {
    try {
        $check = docker exec dk_ncaab_pg pg_isready -U dk 2>&1
        if ($LASTEXITCODE -eq 0) {
            $pgReady = $true
            break
        }
    } catch {}
    Start-Sleep -Seconds 3
    $pgElapsed += 3
}

if (-not $pgReady) {
    Log "ERROR: PostgreSQL not ready after ${pgMaxWait}s. Aborting."
    exit 1
}

Log "PostgreSQL is ready. Starting daemon..."

# ── 4. Run the daemon (blocks until shutdown) ─────────────────
Set-Location $ProjectRoot
& $Python -m dk_ncaab auto --budget 450 2>&1 | Tee-Object -FilePath $logFile -Append
