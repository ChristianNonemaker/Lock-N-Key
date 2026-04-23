# Odds Quota Accounting

Last reviewed: 2026-04-20

## Summary

Odds API usage is now append-only and persisted in `odds_api_usage`.
Each actual request attempt gets one row by app sport and provider sport key,
including failed HTTP responses and request errors.

## Important Files

- `dk_ncaab/db/models.py`: `OddsApiUsage` table.
- `dk_ncaab/db/migrations/versions/0002_odds_api_usage.py`: migration.
- `dk_ncaab/collectors/odds_api.py`: request recording, monthly summary,
  due-sport selection, budget reserve enforcement, and SQLite/Postgres-safe quote insert.
- `api/schemas.py` and `api/main.py`: `/status` budget fields.
- `ui/pages/pipeline_status.py`: budget display in the Streamlit status page.
- `tests/test_odds_quota.py`: no-network tests for cadence, reserve skips, usage rows, and summaries.

## Policy

- Defaults: 500 monthly requests, 50 reserved, 1 sport per run, 360 minutes
  between requests for the same sport.
- Default Odds API request attempts are capped at 1 through
  `odds_api.max_request_attempts` so a controlled one-shot cannot silently spend
  extra free-tier requests on transient retries.
- `collect_odds()` validates configured sports through the sport registry.
- Budget/cadence checks happen before any HTTP client is opened.
- A run can only request due sports, capped by `max_sports_per_run` and the
  remaining budget above reserve.
- HTTP 429 is no longer retried; 500/502/503 still use bounded backoff.
- `collect_odds()` can make more than one provider call if config allows more
  due sports, so docs should say quota-gated rather than "1 request".

## Status Fields

`GET /status` returns:

- `odds_api_monthly_budget`
- `odds_api_reserve_requests`
- `odds_api_requests_recorded_month`
- `odds_api_requests_used`
- `odds_api_requests_remaining`
- `odds_api_last_request_utc`
- `odds_api_requests_by_sport`

## Verification

Run:

```bash
pytest tests/test_odds_quota.py tests/test_sports_registry.py -v
pytest tests/ -v
```

No tests hit live provider APIs.

## Local MLB Smoke

On 2026-04-22, a local one-shot `python -m dk_ncaab collect-odds` ran with the
default MLB-only config. It inserted 136 DraftKings quote rows across 24 MLB
events and recorded one `baseball_mlb` usage row. Provider headers reported
3 used and 497 remaining.
