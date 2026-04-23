# Agent Memory Index

Last reviewed: 2026-04-22

This folder holds compact repo memory for future agents. Read the relevant file before broad searches so the main context stays clean.

## Files

- `current-state.md`: simple summary of where the project is right now.
- `repo-map.md`: subsystem map, commands, and source-of-truth docs.
- `data-pipeline-and-ops.md`: collectors, free data sources, quotas, VM/private-hosting shape.
- `modeling-and-backtests.md`: datasets, modeling, EV/CLV/ROI, leakage risks.
- `ui-and-api.md`: FastAPI and Streamlit inventory, UX gaps, screenshot workflow.
- `known-risks.md`: short list of the most important risks and next decisions.
- `deep-dive-2026-04-20.md`: current senior-level audit across collectors,
  schema, API/UI, modeling, ops, validation, and multi-sport readiness.
- `sport-provider-registry.md`: sport/provider source of truth, defaults,
  eligibility rules, and registry validation notes.
- `odds-quota-accounting.md`: append-only Odds API usage accounting,
  cadence/budget gates, and `/status` budget fields.
- `espn-schedule-results.md`: no-network ESPN schedule/result contract tests
  for active sports and remaining fixture gaps.
- `entry-ev-modeling.md`: settlement-aware backtesting, entry-safe features,
  threshold calibration, and event-grouped OOF validation foundations.

## How To Use

1. Read `current-state.md` first for orientation.
2. Read only the focused file for the current task.
3. Verify stale facts with targeted `rg` or file reads before editing.
4. Add new durable findings here instead of forcing future agents to rediscover them.

## Memory Update Format

Use this shape for new memory files:

```markdown
# Topic

Last reviewed: YYYY-MM-DD

## Summary

## Important Files

## Known Risks

## Verification
```

Keep memory concise. Do not paste long logs, full command output, or raw payloads.
