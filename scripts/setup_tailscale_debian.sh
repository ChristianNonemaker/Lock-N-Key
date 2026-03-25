#!/usr/bin/env bash
set -euo pipefail

# Debian-compatible Tailscale setup for private-only access.
# Usage:
#   bash scripts/setup_tailscale_debian.sh --hostname odds-vm

HOSTNAME_OVERRIDE="odds-vm"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --hostname)
      HOSTNAME_OVERRIDE="$2"
      shift 2
      ;;
    --help|-h)
      echo "Usage: $0 [--hostname <name>]"
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

if ! command -v tailscale >/dev/null 2>&1; then
  curl -fsSL https://tailscale.com/install.sh | sh
fi

sudo systemctl enable tailscaled
sudo systemctl start tailscaled

sudo tailscale up --ssh --hostname "$HOSTNAME_OVERRIDE"

echo "Tailscale status:"
tailscale status
