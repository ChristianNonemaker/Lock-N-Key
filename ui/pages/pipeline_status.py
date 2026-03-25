"""
Pipeline Status page — system health overview.
"""

from __future__ import annotations

import httpx
import streamlit as st


def render(api_base: str) -> None:
    st.header("⚙️ Pipeline Status")

    try:
        resp = httpx.get(f"{api_base}/status", timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        st.error(f"API error: {e}")
        st.info("Make sure the API is running: `uvicorn api.main:app --port 8000`")
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
        st.subheader("🎓 Ratings & Rankings")
        st.metric("KenPom Ratings", data["kenpom_ratings"])
        st.metric("AP Rankings", data["ap_rankings"])

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
        2. `python -m dk_ncaab collect-odds` (1 API request)
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
