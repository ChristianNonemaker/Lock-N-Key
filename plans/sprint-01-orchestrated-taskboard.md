# Sprint 01 Orchestrated Taskboard

## Goal
Make cron-based collector operation reboot-safe on GCP with Secret Manager-ready config boundaries.

## Task 1 — Cron runtime scaffolding
- Owner: Scaffolding -> Implementation -> Quality
- Scope:
  - Add `scripts/cron_collect_cycle.sh` (single cycle, restart-safe).
  - Add `scripts/install_cron_jobs.sh` (5-minute schedule + daily maintenance jobs).
- Acceptance:
  - No overlapping runs and clear run logs.

## Task 2 — Secret Manager preflight
- Owner: Scaffolding -> Implementation -> Quality
- Scope:
  - Add `scripts/preflight_secrets.sh` for required production secrets.
  - Add lightweight secret fetch helper for startup scripts.
- Acceptance:
  - Startup fails fast with actionable error output if secrets are missing.

## Task 3 — Retention + backup skeleton
- Owner: Implementation -> Quality
- Scope:
  - Add `scripts/prune_vm_data.sh` (raw/log retention targets).
  - Add `scripts/backup_sqlite_to_gcs.sh` (daily backup and upload skeleton).
- Acceptance:
  - Scripts support dry-run mode and return non-zero on failure.

## Quality Gate Commands
- `pytest tests/ -v`
- `python -m dk_ncaab --help`
- `bash scripts/cron_collect_cycle.sh --help` (or documented usage smoke test)

## Orchestrator Handoff Format (per task)
- Objective
- Files touched
- Validation run
- Risks/assumptions
- Ready for merge: yes/no
