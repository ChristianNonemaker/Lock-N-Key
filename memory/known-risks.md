# Known Risks

Last reviewed: 2026-05-05

## Highest Priority

1. VM is currently unreachable; deployment is blocked until Tailscale/VM health is restored.
2. Multiple orchestration paths can collide. Production is cron/systemd/Tailscale only.
3. Private hosting is perimeter-only; no app-level auth exists.
4. Strict OOF evidence is still sample-sensitive and currently negative ROI in the latest
   MLB artifact.
5. NBA and Soccer/EPL must stay disabled placeholders until provider contracts land.

## Data Correctness

- Preserve append-only odds, event odds, splits, raw payloads, and results.
- Exact-tip rows are not entry-safe.
- Event identity drift remains possible on environments that have not run reconciliation.
- Splits scraping is brittle and should remain off cron.
- Historical baseball stats are not historical betting lines.

## Product Risks

- Dashboard overclaiming is the easiest way to fool ourselves.
- Thin prop/team-total samples should remain labeled research-only or sample-sensitive.
- No saved recommendation/decision ledger existed historically; the new ledger is local
  and private, not wager placement.
- Mobile board flow needs screenshot review after each substantial change.

## Stop And Ask

- Before changing storage/backup policy.
- Before public ingress or app-level auth changes.
- Before increasing odds polling frequency.
- Before buying historical odds.
- Before enabling NBA/Soccer or new provider-backed schemas.
