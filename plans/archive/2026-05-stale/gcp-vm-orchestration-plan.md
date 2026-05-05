# GCP VM Integration + Agent Orchestration Plan (Finalized)

## 1) Fixed Deployment Profile
- Project: `odds-collector-prod`
- Region/Zone: `us-central1` / `us-central1-a`
- VM: `e2-micro`, Debian x86_64, OS Login + IAM SSH
- Access model: private website only, authenticated, HTTPS, no anonymous/public API
- Data store now: SQLite primary (simple + reliable), with migration path kept open for Postgres

## 2) Current-State to Target-State Direction
- Keep collector independent from website runtime.
- Shift scheduling to cron (every 5 minutes), using restart-safe one-shot commands.
- Keep VM as short-term buffer; move durable raw/backup artifacts to GCS.
- Use Secret Manager in production for API/auth/webhook secrets.

## 3) Storage + Retention Policy
- GCS raw snapshot retention: 14 days (`odds-collector-raw-us-central1`, `us-central1`).
- VM raw snapshot retention: 7 days.
- VM log retention: 7 days with rotation + compression.
- DB backup: daily (SQLite file-safe backup workflow).

## 4) Alerting Policy
- Channel: email to `nonemakerc05@gmail.com`.
- Trigger on: collector failure, no data >15 min, disk >80%, backup/sync failure, unexpected restart.

## 5) Execution Plan (Phased)

### Phase A — Scheduler + Runtime Reliability
- A1: Create cron orchestration scripts for 5-minute collector cycle.
- A2: Add lockfile/timeout guards to prevent overlapping runs.
- A3: Add reboot-safe cron install script + validation command.

### Phase B — Secret Manager + Config Boundaries
- B1: Add Secret Manager loader for production secrets.
- B2: Keep non-sensitive config in YAML (`settings.yaml`) and enforce separation.
- B3: Add startup preflight to fail fast on missing required secrets.

### Phase C — Retention, Backup, and Sync
- C1: Add prune job for raw/log retention windows.
- C2: Add daily SQLite backup + compressed raw bundle upload to GCS.
- C3: Add backup verification + alert on failure.

### Phase D — Private Authenticated Website
- D1: Add private web access layer (auth required before data views).
- D2: Keep API read-only and non-public; expose minimum routes only.
- D3: Add mobile/desktop responsive UX pass for core views.

## 6) Orchestrator/Subagent Workflow
- Orchestrator owns task slicing, dependency order, merge criteria, and respins.
- Stage gates: Scaffolding → Implementation → Quality → UI/UX (if UI touched).
- Every handoff must include: objective, files touched, validation run, unresolved risks.
- Prefer small parallel tasks; merge only after cross-agent consistency check.

## 7) First Two Sprints
- Sprint 1: Phase A + B skeleton (cron jobs, preflight, secret-loader scaffolding).
- Sprint 2: Phase C implementation (retention + backup + GCS offload + email alerts).
