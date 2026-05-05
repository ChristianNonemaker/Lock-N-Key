"""
§15.3 — Model Panel page.

- Predicted probability vs market implied
- Residual visualization
- EV at each timestamp
- Confidence display
"""

from __future__ import annotations

import httpx
import streamlit as st
import pandas as pd
import plotly.graph_objects as go

from dk_ncaab.config.sports import ui_sport_choices


def render(api_base: str) -> None:
    st.header("🧠 Model Panel")

    pick_col1, pick_col2 = st.columns(2)
    with pick_col1:
        sport_filter = st.selectbox(
            "Sport",
            ui_sport_choices(),
            format_func=lambda x: x[1],
            key="model_panel_sport",
        )
    with pick_col2:
        selected_date = st.date_input("Date", key="model_panel_date")

    try:
        games_resp = httpx.get(
            f"{api_base}/games",
            params={"date": selected_date.strftime("%Y-%m-%d"), "sport": sport_filter[0]},
            timeout=20,
        )
        games_resp.raise_for_status()
        games_data = games_resp.json().get("games", [])
    except Exception:
        games_data = []

    if games_data:
        options = {
            f"{g['away_team']['name']} @ {g['home_team']['name']}": g["event_id"]
            for g in games_data
        }
        selected_game = st.selectbox("Pick game", list(options.keys()), key="model_panel_picker")
        if selected_game:
            st.session_state["selected_event_id"] = options[selected_game]

    event_id = st.session_state.get("selected_event_id")
    manual_id = st.number_input("Event ID", value=event_id or 0, step=1, min_value=0)
    event_id = manual_id or event_id

    if not event_id:
        st.info("Select a game from the Game Browser, or enter an Event ID above.")
        return

    # Fetch model predictions and features
    try:
        model_resp = httpx.get(f"{api_base}/game/{event_id}/model", timeout=60).json()
        feat_resp = httpx.get(f"{api_base}/game/{event_id}/features", timeout=30).json()
        summary_resp = httpx.get(f"{api_base}/game/{event_id}/summary", timeout=30).json()
    except Exception as e:
        st.error(f"API error: {e}")
        return

    # Header
    home = summary_resp["home_team"]["name"]
    away = summary_resp["away_team"]["name"]
    st.subheader(f"{away} @ {home}")

    if model_resp.get("model_name"):
        st.caption(f"Model: `{model_resp['model_name']}`")

    signals = model_resp.get("signals", [])

    if not signals:
        st.info(
            "No model signals for this game. "
            "This may mean no trained model exists, or the game lacks sufficient feature data."
        )

        # Still show feature data
        _show_features(feat_resp)
        return

    # ── Signal cards ───────────────────────────────────────────
    st.subheader("Mispricing Signals")

    for sig in signals:
        direction = "🔴 FADE" if sig["residual"] > 0 else "🟢 BET"
        with st.container(border=True):
            cols = st.columns([1, 2, 1, 1, 1])
            cols[0].markdown(f"### {direction}")
            cols[1].metric("Market / Side", f"{sig['market']} — {sig['side']}")
            cols[2].metric("Market Implied", f"{sig['market_implied']:.4f}")
            cols[3].metric("Model Implied", f"{sig['model_implied']:.4f}")
            cols[4].metric("Z-Score", f"{sig['z_score']:+.2f}")

    # ── Market vs Model bar chart ──────────────────────────────
    st.subheader("Market vs Model Implied Probability")

    fig = go.Figure()

    labels = [f"{s['market']}\n{s['side']}" for s in signals]
    market_vals = [s["market_implied"] for s in signals]
    model_vals = [s["model_implied"] for s in signals]

    fig.add_trace(go.Bar(
        name="Market Implied", x=labels, y=market_vals,
        marker_color="#1f77b4",
    ))
    fig.add_trace(go.Bar(
        name="Model Implied", x=labels, y=model_vals,
        marker_color="#ff7f0e",
    ))
    fig.update_layout(
        barmode="group",
        yaxis_title="Implied Probability",
        height=400,
    )
    st.plotly_chart(fig, use_container_width=True)

    # ── Residual chart ─────────────────────────────────────────
    st.subheader("Residuals (Market − Model)")

    residuals = [s["residual"] for s in signals]
    colors = ["#d62728" if r > 0 else "#2ca02c" for r in residuals]

    fig_res = go.Figure(go.Bar(
        x=labels, y=residuals,
        marker_color=colors,
        text=[f"{r:+.4f}" for r in residuals],
        textposition="outside",
    ))
    fig_res.add_hline(y=0, line_dash="solid", line_color="gray")
    fig_res.update_layout(
        yaxis_title="Residual",
        height=350,
    )
    st.plotly_chart(fig_res, use_container_width=True)

    # ── EV at each timestamp ───────────────────────────────────
    st.subheader("Expected Value at Each Timestamp")
    features = feat_resp.get("features", [])
    if features:
        ev_rows = []
        for f in features:
            fair_close = f.get("fair_implied_CLOSE")
            for anchor in ["OPEN", "T60", "T30"]:
                fair_entry = f.get(f"fair_implied_{anchor}")
                if fair_entry is not None and fair_close is not None:
                    ev = fair_close - fair_entry
                    ev_rows.append({
                        "Market": f["market"],
                        "Side": f["side"],
                        "Entry": anchor,
                        "Fair Entry": f"{fair_entry:.4f}",
                        "Fair Close": f"{fair_close:.4f}",
                        "EV (close - entry)": f"{ev:+.4f}",
                    })
        if ev_rows:
            st.dataframe(pd.DataFrame(ev_rows), use_container_width=True, hide_index=True)

    # Features used by model
    feats_used = model_resp.get("features_used", [])
    if feats_used:
        with st.expander(f"Features used by model ({len(feats_used)} columns)"):
            st.code(", ".join(feats_used))

    _show_features(feat_resp)


def _show_features(feat_resp: dict) -> None:
    """Show raw feature table."""
    features = feat_resp.get("features", [])
    if not features:
        return

    with st.expander("Raw Feature Data"):
        df = pd.DataFrame(features)
        # Drop mostly-null columns for readability
        null_pct = df.isnull().mean()
        keep = null_pct[null_pct < 0.9].index.tolist()
        st.dataframe(df[keep], use_container_width=True, hide_index=True)
