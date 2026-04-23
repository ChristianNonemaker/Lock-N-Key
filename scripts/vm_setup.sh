#!/usr/bin/env bash
# ────────────────────────────────────────────────────────────────
# DK NCAAB — Oracle Cloud VM first-time setup
# ────────────────────────────────────────────────────────────────
# Run this on a fresh Ubuntu 22.04+ ARM (Ampere A1) instance.
#
#   ssh ubuntu@<your-vm-ip> 'bash -s' < scripts/vm_setup.sh
#
# Or copy to the VM first, then:
#   chmod +x vm_setup.sh && sudo ./vm_setup.sh
# ────────────────────────────────────────────────────────────────
set -euo pipefail

ALLOW_PUBLIC_UI="${DKNCAAB_ALLOW_PUBLIC_UI:-0}"

echo "══════════════════════════════════════════════"
echo "  DK NCAAB — VM Setup (Docker + Compose)"
echo "══════════════════════════════════════════════"

# ── 1. System updates ──────────────────────────────────────────
sudo apt-get update && sudo apt-get upgrade -y

# ── 2. Install Docker ──────────────────────────────────────────
if ! command -v docker &> /dev/null; then
    echo "Installing Docker..."
    curl -fsSL https://get.docker.com | sudo sh
    sudo usermod -aG docker "$USER"
    echo "  Docker installed. You may need to log out/in for group changes."
else
    echo "  Docker already installed."
fi

# Enable & start Docker
sudo systemctl enable docker
sudo systemctl start docker

# ── 3. Install Docker Compose plugin ───────────────────────────
if ! docker compose version &> /dev/null; then
    echo "Installing Docker Compose plugin..."
    sudo apt-get install -y docker-compose-plugin
else
    echo "  Docker Compose already installed."
fi

# ── 4. Create project directory ────────────────────────────────
PROJECT_DIR="$HOME/dk_ncaab"
mkdir -p "$PROJECT_DIR"
echo "  Project directory: $PROJECT_DIR"

# ── 5. Open firewall for Streamlit (port 8501) ─────────────────
# Oracle Cloud uses iptables by default on Ubuntu images
if [[ "$ALLOW_PUBLIC_UI" == "1" ]]; then
    if sudo iptables -L INPUT -n | grep -q 8501; then
        echo "  Port 8501 already open."
    else
        echo "Opening port 8501 because DKNCAAB_ALLOW_PUBLIC_UI=1..."
        sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 8501 -j ACCEPT
        sudo netfilter-persistent save 2>/dev/null || true
    fi
else
    echo "  Skipping public port 8501. Use Tailscale Serve for private UI access."
fi

echo ""
echo "══════════════════════════════════════════════"
echo "  Setup complete!"
echo ""
echo "  Next steps:"
echo "    1. Log out and back in (for docker group)"
echo "    2. Copy project files to $PROJECT_DIR/"
echo "    3. Create .env file with your API key"
echo "    4. Preferred production: install cron + systemd + Tailscale Serve"
echo "       Legacy only: docker compose -f docker-compose.prod.yml up -d"
echo "══════════════════════════════════════════════"
