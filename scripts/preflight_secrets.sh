#!/usr/bin/env bash
set -euo pipefail

# === Purpose ===================================================
# Validate required production secrets are available in
# Google Cloud Secret Manager before collector/web startup.
# ==============================================================

PROJECT_ID="odds-collector-prod"
REQUIRED_SECRETS=("DKNCAAB_ODDS_API__KEY")
WITH_ALERTS=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --project-id)
      PROJECT_ID="$2"
      shift 2
      ;;
    --required-secret)
      REQUIRED_SECRETS+=("$2")
      shift 2
      ;;
    --with-alerts)
      WITH_ALERTS=1
      shift
      ;;
    --help|-h)
      echo "Usage: $0 [--project-id <gcp-project-id>] [--required-secret <name>]... [--with-alerts]"
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

if [[ "$WITH_ALERTS" -eq 1 ]]; then
  REQUIRED_SECRETS+=(
    "ALERT_SMTP_HOST"
    "ALERT_SMTP_USER"
    "ALERT_SMTP_PASS"
  )
fi

if ! command -v gcloud >/dev/null 2>&1; then
  echo "gcloud CLI not found" >&2
  exit 1
fi

MISSING=0
for secret_name in "${REQUIRED_SECRETS[@]}"; do
  if ! gcloud secrets versions access latest --secret="$secret_name" --project="$PROJECT_ID" >/dev/null 2>&1; then
    echo "Missing/unreadable secret: $secret_name" >&2
    MISSING=1
  fi
done

if [[ "$MISSING" -ne 0 ]]; then
  echo "Secret preflight failed" >&2
  exit 1
fi

echo "Secret preflight passed"
