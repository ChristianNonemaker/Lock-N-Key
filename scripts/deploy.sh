#!/usr/bin/env bash
# ────────────────────────────────────────────────────────────────
# DK NCAAB — Deploy / update to Oracle Cloud VM
# ────────────────────────────────────────────────────────────────
# Syncs the project to the VM and (re)starts all containers.
#
# Usage:
#   bash scripts/deploy.sh <vm-ip> [ssh-key-path]
#
# Examples:
#   bash scripts/deploy.sh 129.213.42.10
#   bash scripts/deploy.sh 129.213.42.10 ~/.ssh/oracle_key
# ────────────────────────────────────────────────────────────────
set -euo pipefail

VM_IP="${1:?Usage: deploy.sh <vm-ip> [ssh-key-path]}"
SSH_KEY="${2:-}"
VM_USER="ubuntu"
REMOTE_DIR="/home/$VM_USER/dk_ncaab"

SSH_OPTS="-o StrictHostKeyChecking=no"
if [[ -n "$SSH_KEY" ]]; then
    SSH_OPTS="$SSH_OPTS -i $SSH_KEY"
fi

echo "═══════════════════════════════════════════"
echo "  Deploying to $VM_USER@$VM_IP"
echo "═══════════════════════════════════════════"

# ── 1. Sync project files ──────────────────────────────────────
echo "Syncing files..."
rsync -avz --progress \
    -e "ssh $SSH_OPTS" \
    --exclude '.git' \
    --exclude '__pycache__' \
    --exclude '*.pyc' \
    --exclude '.venv' \
    --exclude 'artifacts/' \
    --exclude '.env' \
    --exclude 'node_modules' \
    --exclude '.mypy_cache' \
    --exclude '.ruff_cache' \
    ./ "$VM_USER@$VM_IP:$REMOTE_DIR/"

# ── 2. Build & restart ─────────────────────────────────────────
echo ""
echo "Building and starting containers..."
# shellcheck disable=SC2029
ssh $SSH_OPTS "$VM_USER@$VM_IP" bash -c "'
    cd $REMOTE_DIR
    if [ ! -f .env ]; then
        echo \"ERROR: .env not found on VM. Create it first:\"
        echo \"  ssh $VM_USER@$VM_IP\"
        echo \"  cd $REMOTE_DIR\"
        echo \"  cp .env.example .env && nano .env\"
        exit 1
    fi
    docker compose -f docker-compose.prod.yml build
    docker compose -f docker-compose.prod.yml up -d
    echo \"\"
    echo \"Containers:\"
    docker compose -f docker-compose.prod.yml ps
'"

echo ""
echo "═══════════════════════════════════════════"
echo "  Deploy complete!"
echo "  UI: http://$VM_IP:8501"
echo "═══════════════════════════════════════════"
