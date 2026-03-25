"""
§15.2 — Game Detail page.

Tabs: Spread | Total | Moneyline

Each tab shows:
  - Pre-game line movement over time (live odds shown in a separate muted region)
  - OPEN and CLOSE markers clearly annotated
  - Implied probability over time
  - Snapshot comparison (OPEN/T60/T30/CLOSE)
  - Outcome + CLV metrics
"""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import httpx
import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

_ET = ZoneInfo("America/New_York")

_SIDE_COLORS = {
    "home": "#1f77b4",
    "away": "#ff7f0e",
    "over": "#2ca02c",
    "under": "#d62728",
}


def _utc_to_et(iso_str: str) -> str:
    try:
        dt = datetime.fromisoformat(iso_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        et = dt.astimezone(_ET)
        try:
            return et.strftime("%#I:%M %p ET")
        except ValueError:
            return et.strftime("%I:%M %p ET").lstrip("0")
    except Exception:
        return iso_str[:16]


def render(api_base: str) -> None:
    st.header("🔍 Game Detail")

    event_id = st.session_state.get("selected_event_id")
    manual_id = st.number_input("Event ID", value=event_id or 0, step=1, min_value=0)
    event_id = manual_id or event_id

    if not event_id:
        st.info("Select a game from the Game Browser or Team History, or enter an Event ID above.")
        return

    # Fetch data
    try:
        summary = httpx.get(f"{api_base}/game/{event_id}/summary", timeout=30).json()
        ts_data = httpx.get(f"{api_base}/game/{event_id}/timeseries", timeout=30).json()
        feat_data = httpx.get(f"{api_base}/game/{event_id}/features", timeout=30).json()
    except Exception as e:
        st.error(f"API error: {e}")
        return

    # Header
    home = summary["home_team"]["name"]
    away = summary["away_team"]["name"]
    st.subheader(f"{away} @ {home}")

    cols = st.columns(4)
    cols[0].metric("Status", summary["status"].upper())
    cols[1].metric("Tip", _utc_to_et(summary["start_time_utc"]))
    if summary.get("home_score") is not None:
        cols[2].metric("Score", f"{summary['away_score']} - {summary['home_score']}")
    if summary.get("kenpom_expected_spread") is not None:
        cols[3].metric("KenPom Spread", f"{summary['kenpom_expected_spread']:+.1f}")

    st.markdown("---")

    # Tabs
    tab_spread, tab_total, tab_ml = st.tabs(["📊 Spread", "📈 Total", "💰 Moneyline"])

    for tab, market in [(tab_spread, "spread"), (tab_total, "total"), (tab_ml, "moneyline")]:
        with tab:
            _render_market_tab(market, summary, ts_data, feat_data, api_base, event_id)


def _render_market_tab(
    market: str,
    summary: dict,
    ts_data: dict,
    feat_data: dict,
    api_base: str,
    event_id: int,
) -> None:
    """Render a single market tab with charts and snapshot table."""

    sides = ["home", "away"] if market in ("moneyline", "spread") else ["over", "under"]

    # ── Line movement chart ────────────────────────────────
    odds = [o for o in ts_data.get("odds", []) if o["market"] == market]
    if odds:
        df_odds = pd.DataFrame(odds)
        df_odds["collected_at_utc"] = pd.to_datetime(df_odds["collected_at_utc"])

        # Separate pre-game and live data
        pregame = df_odds[~df_odds["is_live"]].copy()
        live = df_odds[df_odds["is_live"]].copy()

        fig = make_subplots(
            rows=2, cols=1,
            shared_xaxes=True,
            subplot_titles=["Implied Probability", "Line / Price"],
            vertical_spacing=0.08,
        )

        # Add vertical line at game start
        start_time = pd.to_datetime(ts_data.get("start_time_utc"))
        if start_time:
            fig.add_vline(
                x=start_time, line_dash="dash", line_color="red", opacity=0.5,
                annotation_text="Tip-off",
                row=1, col=1,
            )
            fig.add_vline(
                x=start_time, line_dash="dash", line_color="red", opacity=0.5,
                row=2, col=1,
            )

        for side in sides:
            color = _SIDE_COLORS.get(side, "#888")

            # Pre-game data (solid lines — this is what matters)
            pre_side = pregame[pregame["side"] == side].sort_values("collected_at_utc")
            if not pre_side.empty:
                fig.add_trace(
                    go.Scatter(
                        x=pre_side["collected_at_utc"],
                        y=pre_side["implied_probability"],
                        name=f"{side} implied (pre-game)",
                        mode="lines+markers",
                        line=dict(color=color, width=2),
                        marker=dict(size=5),
                    ),
                    row=1, col=1,
                )

                y_col = "line" if market in ("spread", "total") else "price_american"
                fig.add_trace(
                    go.Scatter(
                        x=pre_side["collected_at_utc"],
                        y=pre_side[y_col],
                        name=f"{side} {'line' if market != 'moneyline' else 'price'} (pre-game)",
                        mode="lines+markers",
                        line=dict(color=color, width=2),
                        marker=dict(size=5),
                    ),
                    row=2, col=1,
                )

                # Mark OPEN and CLOSE
                first = pre_side.iloc[0]
                last = pre_side.iloc[-1]
                fig.add_annotation(
                    x=first["collected_at_utc"], y=first[y_col],
                    text="OPEN", showarrow=True, arrowhead=2,
                    row=2, col=1,
                )
                if len(pre_side) > 1:
                    fig.add_annotation(
                        x=last["collected_at_utc"], y=last[y_col],
                        text="CLOSE", showarrow=True, arrowhead=2,
                        row=2, col=1,
                    )

            # Live data (muted, dashed — shown for context only)
            live_side = live[live["side"] == side].sort_values("collected_at_utc")
            if not live_side.empty:
                fig.add_trace(
                    go.Scatter(
                        x=live_side["collected_at_utc"],
                        y=live_side["implied_probability"],
                        name=f"{side} (live)",
                        mode="lines",
                        line=dict(color=color, width=1, dash="dot"),
                        opacity=0.3,
                        showlegend=False,
                    ),
                    row=1, col=1,
                )

                y_col = "line" if market in ("spread", "total") else "price_american"
                fig.add_trace(
                    go.Scatter(
                        x=live_side["collected_at_utc"],
                        y=live_side[y_col],
                        name=f"{side} (live)",
                        mode="lines",
                        line=dict(color=color, width=1, dash="dot"),
                        opacity=0.3,
                        showlegend=False,
                    ),
                    row=2, col=1,
                )

        # KenPom reference line for spread
        if market == "spread" and summary.get("kenpom_expected_spread") is not None:
            kp = summary["kenpom_expected_spread"]
            fig.add_hline(
                y=kp, row=2, col=1,
                line_dash="dot", line_color="gray",
                annotation_text=f"KenPom: {kp:+.1f}",
            )

        fig.update_layout(height=500, showlegend=True, hovermode="x unified")
        fig.update_yaxes(title_text="Implied Prob", row=1, col=1)
        fig.update_yaxes(
            title_text="Line" if market != "moneyline" else "American Odds",
            row=2, col=1,
        )
        st.plotly_chart(fig, use_container_width=True)

        # Pre-game vs live count info
        pre_count = len(pregame[pregame["market"] == market])
        live_count = len(live[live["market"] == market])
        st.caption(f"📊 {pre_count} pre-game snapshots · {live_count} live snapshots (muted)")
    else:
        st.info(f"No odds data for {market}")

    # ── Splits chart ───────────────────────────────────────
    splits = [s for s in ts_data.get("splits", []) if s["market"] == market]
    if splits:
        df_splits = pd.DataFrame(splits)
        df_splits["collected_at_utc"] = pd.to_datetime(df_splits["collected_at_utc"])

        fig_sp = go.Figure()
        for side in sides:
            color = _SIDE_COLORS.get(side, "#888")
            side_sp = df_splits[df_splits["side"] == side].sort_values("collected_at_utc")
            if side_sp.empty:
                continue
            fig_sp.add_trace(go.Scatter(
                x=side_sp["collected_at_utc"],
                y=side_sp["bets_pct"],
                name=f"{side} bets%",
                mode="lines+markers",
                line=dict(color=color),
            ))
            fig_sp.add_trace(go.Scatter(
                x=side_sp["collected_at_utc"],
                y=side_sp["handle_pct"],
                name=f"{side} handle%",
                mode="lines+markers",
                line=dict(color=color, dash="dash"),
            ))

        fig_sp.add_hline(y=50, line_dash="dot", line_color="gray")
        fig_sp.update_layout(
            title="Public Betting Splits",
            yaxis_title="Percentage",
            height=350,
            hovermode="x unified",
        )
        st.plotly_chart(fig_sp, use_container_width=True)

    # ── Snapshot comparison table ──────────────────────────
    st.subheader("Snapshot Comparison (Pre-Game Only)")
    features = feat_data.get("features", [])
    market_feats = [f for f in features if f.get("market") == market]

    if market_feats:
        snap_rows = []
        for f in market_feats:
            snap_rows.append({
                "Side": f["side"],
                "Implied OPEN": _fmt(f.get("implied_OPEN")),
                "Implied T60": _fmt(f.get("implied_T60")),
                "Implied T30": _fmt(f.get("implied_T30")),
                "Implied CLOSE": _fmt(f.get("implied_CLOSE")),
                "Δ OPEN→T60": _fmt(f.get("d_implied_OPEN_T60")),
                "Δ T60→T30": _fmt(f.get("d_implied_T60_T30")),
                "Δ T30→CLOSE": _fmt(f.get("d_implied_T30_CLOSE")),
                "Late Steam": _fmt(f.get("late_steam")),
                "CLV (OPEN)": _fmt(f.get("clv_OPEN")),
                "CLV (T60)": _fmt(f.get("clv_T60")),
            })
        st.dataframe(pd.DataFrame(snap_rows), use_container_width=True, hide_index=True)

        # KenPom deviation (spread only)
        if market == "spread":
            st.subheader("KenPom Deviation Trajectory")
            dev_rows = []
            for f in market_feats:
                dev_rows.append({
                    "Side": f["side"],
                    "Dev OPEN": _fmt(f.get("spread_dev_OPEN")),
                    "Dev T60": _fmt(f.get("spread_dev_T60")),
                    "Dev T30": _fmt(f.get("spread_dev_T30")),
                    "Dev CLOSE": _fmt(f.get("spread_dev_CLOSE")),
                    "KenPom Spread": _fmt(f.get("kenpom_expected_spread")),
                    "AdjEM Diff": _fmt(f.get("adj_em_diff")),
                })
            st.dataframe(pd.DataFrame(dev_rows), use_container_width=True, hide_index=True)

        # Outcome + CLV
        if market_feats[0].get("home_win") is not None:
            st.subheader("Outcome & CLV")
            out_rows = []
            for f in market_feats:
                out_rows.append({
                    "Side": f["side"],
                    "Home Win": f.get("home_win"),
                    "Spread Cover": f.get("spread_cover"),
                    "Total Over": f.get("total_over"),
                    "CLV OPEN": _fmt(f.get("clv_OPEN")),
                    "CLV T60": _fmt(f.get("clv_T60")),
                    "CLV T30": _fmt(f.get("clv_T30")),
                    "Fair CLV OPEN": _fmt(f.get("clv_fair_OPEN")),
                    "Fair CLV T60": _fmt(f.get("clv_fair_T60")),
                })
            st.dataframe(pd.DataFrame(out_rows), use_container_width=True, hide_index=True)


def _fmt(val) -> str:
    if val is None:
        return "—"
    if isinstance(val, float):
        return f"{val:+.4f}"
    return str(val)
