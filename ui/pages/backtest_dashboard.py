"""
§15.4 — Backtest Dashboard page.

- ROI over time
- CLV distribution
- EV vs ROI scatter
- Filter by deviation bins, public imbalance bins, ranked status
"""

from __future__ import annotations

import httpx
import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px


def render(api_base: str) -> None:
    st.header("📊 Backtest Dashboard")

    st.caption(
        "Runs the full backtest suite on historical data. "
        "This may take a moment for large datasets."
    )

    if st.button("Run Backtest", type="primary"):
        with st.spinner("Running backtest suite…"):
            try:
                resp = httpx.get(f"{api_base}/backtest/summary", timeout=120)
                resp.raise_for_status()
                data = resp.json()
                st.session_state["backtest_data"] = data
            except Exception as e:
                st.error(f"API error: {e}")
                return

    data = st.session_state.get("backtest_data")
    if not data:
        st.info("Click **Run Backtest** to generate results.")
        return

    strategies = data.get("strategies", [])
    if not strategies:
        st.warning("No backtest results — not enough historical data yet.")
        return

    st.success(f"Backtest complete: {data['n_events']} events, {len(strategies)} strategies")

    # ── Summary table ──────────────────────────────────────────
    st.subheader("Strategy Comparison")

    df = pd.DataFrame(strategies)
    display_df = df.copy()
    display_df["mean_clv"] = display_df["mean_clv"].map(lambda x: f"{x:+.4f}")
    display_df["total_roi"] = display_df["total_roi"].map(lambda x: f"{x:+.1%}")
    display_df["clv_positive_rate"] = display_df["clv_positive_rate"].map(lambda x: f"{x:.1%}")
    display_df["win_rate"] = display_df["win_rate"].map(
        lambda x: f"{x:.1%}" if x is not None else "—"
    )
    display_df["max_drawdown"] = display_df["max_drawdown"].map(lambda x: f"{x:+.1%}")
    display_df["sharpe_ratio"] = display_df["sharpe_ratio"].map(
        lambda x: f"{x:.2f}" if x is not None else "—"
    )

    st.dataframe(display_df, use_container_width=True, hide_index=True)

    # ── ROI comparison bar chart ───────────────────────────────
    st.subheader("ROI by Strategy")

    fig_roi = go.Figure(go.Bar(
        x=df["strategy"],
        y=df["total_roi"] * 100,
        marker_color=[
            "#2ca02c" if r > 0 else "#d62728"
            for r in df["total_roi"]
        ],
        text=[f"{r:+.1f}%" for r in df["total_roi"] * 100],
        textposition="outside",
    ))
    fig_roi.add_hline(y=0, line_dash="solid", line_color="gray")
    fig_roi.update_layout(
        yaxis_title="ROI (%)",
        height=400,
        xaxis_tickangle=-30,
    )
    st.plotly_chart(fig_roi, use_container_width=True)

    # ── CLV comparison ─────────────────────────────────────────
    st.subheader("Mean CLV by Strategy")

    fig_clv = go.Figure(go.Bar(
        x=df["strategy"],
        y=df["mean_clv"],
        marker_color=[
            "#1f77b4" if c > 0 else "#ff7f0e"
            for c in df["mean_clv"]
        ],
        text=[f"{c:+.4f}" for c in df["mean_clv"]],
        textposition="outside",
    ))
    fig_clv.add_hline(y=0, line_dash="solid", line_color="gray")
    fig_clv.update_layout(
        yaxis_title="Mean CLV",
        height=400,
        xaxis_tickangle=-30,
    )
    st.plotly_chart(fig_clv, use_container_width=True)

    # ── CLV vs ROI scatter ─────────────────────────────────────
    st.subheader("CLV vs ROI")

    fig_scatter = px.scatter(
        df,
        x="mean_clv",
        y="total_roi",
        text="strategy",
        size="n_bets",
        color="sharpe_ratio",
        color_continuous_scale="RdYlGn",
        labels={
            "mean_clv": "Mean CLV",
            "total_roi": "Total ROI",
            "sharpe_ratio": "Sharpe",
            "n_bets": "# Bets",
        },
    )
    fig_scatter.add_hline(y=0, line_dash="dot", line_color="gray")
    fig_scatter.add_vline(x=0, line_dash="dot", line_color="gray")
    fig_scatter.update_traces(textposition="top center")
    fig_scatter.update_layout(height=500)
    st.plotly_chart(fig_scatter, use_container_width=True)

    # ── Key metrics cards ──────────────────────────────────────
    st.subheader("Key Metrics")

    best_roi = df.loc[df["total_roi"].idxmax()]
    best_clv = df.loc[df["mean_clv"].idxmax()]
    best_sharpe_idx = df["sharpe_ratio"].dropna().idxmax() if df["sharpe_ratio"].notna().any() else None

    cols = st.columns(3)
    cols[0].metric(
        "Best ROI",
        f"{best_roi['total_roi']:+.1%}",
        delta=best_roi["strategy"],
    )
    cols[1].metric(
        "Best CLV",
        f"{best_clv['mean_clv']:+.4f}",
        delta=best_clv["strategy"],
    )
    if best_sharpe_idx is not None:
        best_sharpe = df.loc[best_sharpe_idx]
        cols[2].metric(
            "Best Sharpe",
            f"{best_sharpe['sharpe_ratio']:.2f}",
            delta=best_sharpe["strategy"],
        )
