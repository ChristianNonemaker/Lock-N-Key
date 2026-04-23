# ── Deploy to VM from Windows (PowerShell) ──────────────────────
# Usage: .\scripts\deploy.ps1 -VmIp "YOUR_VM_IP" -SshKey "~\.ssh\oracle_key"
# ─────────────────────────────────────────────────────────────────

param(
    [Parameter(Mandatory)][string]$VmIp,
    [string]$SshKey = "",
    [string]$VmUser = "ubuntu",
    [string]$RemoteDir = "/home/ubuntu/dk_ncaab"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot  # one level up from scripts/

$sshArgs = @("-o", "StrictHostKeyChecking=no")
if ($SshKey) { $sshArgs += @("-i", $SshKey) }

Write-Host "═══════════════════════════════════════════"
Write-Host "  Deploying to $VmUser@$VmIp"
Write-Host "═══════════════════════════════════════════"

# ── 1. Create remote directory ──────────────────────────────────
Write-Host "Creating remote directory..."
ssh @sshArgs "$VmUser@$VmIp" "mkdir -p $RemoteDir"

# ── 2. Copy project files via scp ──────────────────────────────
# (Windows has scp built-in since Win10 1809)
Write-Host "Copying project files..."

# Files/dirs to copy
$items = @(
    "Dockerfile",
    "docker-compose.prod.yml",
    "pyproject.toml",
    "alembic.ini",
    ".env.cloud",
    ".env.example"
)

foreach ($item in $items) {
    $src = Join-Path $ProjectRoot $item
    if (Test-Path $src) {
        Write-Host "  $item"
        scp @sshArgs $src "${VmUser}@${VmIp}:${RemoteDir}/$item"
    }
}

# Copy directories recursively
$dirs = @("dk_ncaab", "api", "ui")
foreach ($dir in $dirs) {
    $src = Join-Path $ProjectRoot $dir
    if (Test-Path $src) {
        Write-Host "  $dir/"
        # scp -r copies directories
        scp @sshArgs -r $src "${VmUser}@${VmIp}:${RemoteDir}/"
    }
}

# ── 3. Build & start on VM ─────────────────────────────────────
Write-Host ""
Write-Host "Building and starting containers on VM..."
$remoteCmd = @"
cd $RemoteDir

# Rename .env.cloud to .env if no .env exists
if [ ! -f .env ] && [ -f .env.cloud ]; then
    cp .env.cloud .env
    echo 'Created .env from .env.cloud -- EDIT IT with your API key!'
    echo '  nano $RemoteDir/.env'
fi

if [ ! -f .env ]; then
    echo 'ERROR: No .env file. Create one:'
    echo '  cp .env.cloud .env && nano .env'
    exit 1
fi

docker compose -f docker-compose.prod.yml build
docker compose -f docker-compose.prod.yml up -d
echo ''
docker compose -f docker-compose.prod.yml ps
"@

ssh @sshArgs "$VmUser@$VmIp" $remoteCmd

Write-Host ""
Write-Host "═══════════════════════════════════════════"
Write-Host "  Deploy complete!"
Write-Host "  UI: http://${VmIp}:8501"
Write-Host "═══════════════════════════════════════════"
# LEGACY: Docker/Oracle deploy helper. Production uses cron + systemd +
# Tailscale Serve on the Debian VM; do not use this as the default deploy path.
