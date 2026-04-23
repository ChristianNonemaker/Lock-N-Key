"""
Pipeline Status page — system health overview.
"""

from __future__ import annotations

import httpx
import streamlit as st
import pandas as pd


def render(api_base: str) -> None:
    st.header("⚙️ Pipeline Status")

    try:
        resp = httpx.get(f"{api_base}/status", timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        st.error(f"API error: {e}")
        st.info("Make sure the API is running: `uvicorn api.main:app --host 127.0.0.1 --port 8000`")
        return

    # Data inventory
    col1, col2, col3 = st.columns(3)

    with col1:
        st.subheader("📦 Data Inventory")
        st.metric("Teams", data["teams"])
        st.metric("Events (total)", data["events_total"])
        st.metric("Events (upcoming)", data["events_upcoming"])
        st.metric("Events (final)", data["events_final"])

    with col2:
        st.subheader("📊 Quote Data")
        st.metric("Odds Quotes (total)", data["odds_quotes"])
        st.metric("  ↳ Pre-game", data.get("odds_quotes_pregame", "—"))
        st.metric("  ↳ Live/In-game", data.get("odds_quotes_live", "—"))
        st.metric("Splits Quotes", data["splits_quotes"])
        st.metric("Results", data["results"])

    with col3:
        st.subheader("🎓 Ratings + Health")
        st.metric("KenPom Ratings", data["kenpom_ratings"])
        st.metric("AP Rankings", data["ap_rankings"])
        st.metric("Odds Age (min)", data.get("odds_data_age_min", "—"))

    st.markdown("### Collector Health")
    latest_quote = data.get("latest_odds_quote_utc")
    if data.get("odds_stale", True):
        st.error(
            "Odds feed is stale (>15m)"
            + (f". Latest quote: {latest_quote}" if latest_quote else ".")
        )
    else:
        st.success(
            "Odds feed is fresh"
            + (f". Latest quote: {latest_quote}" if latest_quote else ".")
        )

    sports = data.get("configured_sports", [])
    if sports:
        st.caption("Configured sports: " + ", ".join(sports))

    st.markdown("### Odds API Budget")
    budget_col1, budget_col2, budget_col3 = st.columns(3)
    with budget_col1:
        st.metric("Monthly budget", data.get("odds_api_monthly_budget", "—"))
    with budget_col2:
        st.metric("Requests used", data.get("odds_api_requests_used", "—"))
    with budget_col3:
        st.metric("Requests remaining", data.get("odds_api_requests_remaining", "—"))

    reserve = data.get("odds_api_reserve_requests")
    recorded = data.get("odds_api_requests_recorded_month")
    last_request = data.get("odds_api_last_request_utc")
    st.caption(
        f"Recorded this month: {recorded if recorded is not None else '—'}"
        + (f" · reserve: {reserve}" if reserve is not None else "")
        + (f" · last request: {last_request}" if last_request else "")
    )

    usage_by_sport = data.get("odds_api_requests_by_sport", {})
    if usage_by_sport:
        st.table(
            [
                {"sport": sport, "requests_this_month": count}
                for sport, count in sorted(usage_by_sport.items())
            ]
        )

    run_status = data.get("last_run_status")
    last_run_time = data.get("last_run_completed_utc")
    failed_24h = data.get("failed_runs_24h", 0)
    if run_status:
        st.caption(f"Last run: {run_status} @ {last_run_time}")
    st.caption(f"Failed/partial runs (24h): {failed_24h}")

    league_counts = data.get("odds_quotes_by_league", {})
    if league_counts:
        st.markdown("### Odds Quotes by League")
        st.table(
            [
                {"league": league, "odds_quotes": count}
                for league, count in sorted(league_counts.items())
            ]
        )

    try:
        runs_resp = httpx.get(f"{api_base}/runs", params={"limit": 10}, timeout=15)
        runs_resp.raise_for_status()
        runs = runs_resp.json()
    except Exception:
        runs = []

    if runs:
        st.markdown("### Recent Ingestion Runs")
        rows = []
        for run in runs:
            steps = run.get("steps", {})
            failed = [name for name, meta in steps.items() if int(meta.get("rc", 1)) != 0]
            rows.append(
                {
                    "run_id": run.get("run_id"),
                    "completed_at_utc": run.get("completed_at_utc"),
                    "status": run.get("status"),
                    "failed_steps": ", ".join(failed) if failed else "none",
                }
            )
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    st.markdown("---")

    # Training readiness
    trainable = data["trainable_events"]
    if trainable >= 50:
        st.success(f"✅ Training-ready: {trainable} trainable events")
    else:
        st.warning(
            f"⏳ {trainable}/50 trainable events — "
            f"need {50 - trainable} more for meaningful training."
        )
        st.markdown("""
        **Next steps:**
        1. `python -m dk_ncaab backfill --days 60` (FREE)
        2. `python -m dk_ncaab collect-odds` (quota-gated)
        3. Repeat `collect-odds` a few times/day for odds history
        4. `python -m dk_ncaab train` when ready
        """)

    # Quick actions info
    st.markdown("---")
    st.subheader("Quick Reference")
    st.code("""
# Daily pipeline (free + 1 API call)
python -m dk_ncaab pipeline

# Train models
python -m dk_ncaab train

# Score upcoming games
python -m dk_ncaab predict

# Auto-collection daemon
python -m dk_ncaab auto --budget 450
    """, language="bash")
