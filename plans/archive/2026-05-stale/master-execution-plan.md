# Master Execution Plan

## Objective
Build a reliable private sportsbook ingestion + analysis platform that runs unattended on a low-resource VM for exactly four sports (NCAAF, NCAAB, NFL, MLB), and provides a polished desktop/mobile dashboard via Tailscale-only access.

## Current State
- VM connectivity is now working via Tailscale + SSH.
- Cron-based collection and Sprint 2 alerting/backup scripts are implemented in repo.
- GitHub repo is now the source of truth for VM sync (`git clone` / `git pull`).
- Primary near-term risk is operational consistency on VM (fresh clone state, secret presence, cron health validation).

## Delivery Strategy (Orchestrator + Subagents)
- Orchestrator owns sequencing, acceptance gates, and respins.
- Subagent roles:
  - Scaffolding: create files, taskboard updates, migration stubs.
  - Implementation: collector logic, ETL/model/schema/API/UI code.
  - Quality: tests, smoke checks, regression checks, release notes.
  - UI/UX: responsive improvements, layout polish, visual hierarchy.
- Stage gates for every task:
  - Scope freeze -> implement -> validate -> polish -> merge.

## Phase Plan

### Phase 0: Operational Baseline (Now)
- Goal: Make VM runbook deterministic and repeatable.
- Tasks:
  - Validate VM clone/bootstrap path from GitHub.
  - Validate secrets preflight + cron installation + health monitor.
  - Run one full collection cycle and verify logs, DB writes, and alert suppression.
- Acceptance:
  - `tailscale status`, `ssh`, cron entries, and monitor logs all healthy.

### Phase 1: Data Reliability Hardening
- Goal: Improve lineage and uptime confidence before feature expansion.
- Tasks:
  - Add ingestion run metadata table (start/end/status/rows inserted/errors).
  - Add collector idempotency checks and duplicate diagnostics report.
  - Add no-data and backup stale checks to API pipeline status endpoint.
- Acceptance:
  - Daily health summary visible in UI + API and recoverable failures are explicit.

### Phase 2: Multi-Sport Expansion Foundation
- Goal: Expand architecture from NCAAB-specific to four-sport ingestion.
- Tasks:
  - Add sport dimension in config and collector routing.
  - Refactor odds collector to iterate configured sports safely under quota policy.
  - Keep snapshots/ETL backward compatible for NCAAB while adding per-sport partitions.
- In-scope sports only: NCAAF, NCAAB, NFL, MLB.
- Acceptance:
  - NCAAB remains stable while each in-scope sport is ingesting end-to-end.

### Phase 3: Dashboard Productization (Private)
- Goal: Make dashboard excellent on laptop and phone while staying lightweight.
- Tasks:
  - Add responsive mode behavior across key pages.
  - Introduce fast summary cards + filter presets + compact mobile tables.
  - Add private operational page: collector freshness, disk usage, backup status.
- Acceptance:
  - <2s render for core pages on VM under normal load and mobile navigation is frictionless.

### Phase 4: Research Scale-Up
- Goal: Increase data usefulness for EV/backtest workflows.
- Tasks:
  - Add richer market movement features and confidence diagnostics.
  - Add per-sport backtest slicing and configurable strategy filters.
  - Add export pipeline for local long-term analysis snapshots.
- Acceptance:
  - Reproducible dataset build and cross-sport backtest outputs.

## Sprint Cadence (Rolling 2-Week)
- Week 1: Phase 0 complete + Phase 1 ingestion telemetry skeleton.
- Week 2: Phase 1 complete + Phase 2 scaffolding for NCAAF/NFL/MLB.
- Week 3: All in-scope sports in production + UI status upgrades.
- Week 4: Mobile-first dashboard pass + backtest enhancements.

## Immediate Sprint Backlog (Next Actions)
1. VM bootstrap verification pass using Git flow only.
2. Run and validate cron + monitor + backup scripts on VM.
3. Add ingestion run metadata schema + writer hooks.
4. Add API/UI pipeline health widget sourced from monitor outputs.
5. Finalize four-sport config and quota guardrails.

## Orchestrator Runbook Template
- Task ID:
- Goal:
- Scope in/out:
- Assigned subagent:
- Deliverables:
- Validation commands:
- Risks/rollback:
- Decision: merge / respin

## Validation Baseline
- Unit tests: `pytest tests/ -v`
- CLI smoke: `python -m dk_ncaab --help`
- Cron cycle smoke: `bash scripts/cron_collect_cycle.sh --project-dir ~/dk_ncaab --python-cmd ~/dk_ncaab/.venv/bin/python`
- Health smoke: `bash scripts/monitor_health.sh --project-dir ~/dk_ncaab --project-id odds-collector-prod`
- Backup smoke: `bash scripts/backup_sqlite_to_gcs.sh --dry-run --bucket odds-collector-raw-us-central1`

## Non-Negotiables
- Private access only (Tailscale).
- Collector must continue independent of web/UI availability.
- VM resources protected (no heavy background loops, no unnecessary services).
- Keep append-only lineage and retention policy enforcement.
- Keep VM limits within the free limit on Google Cloud, it is a current e2-micro 10GB.
