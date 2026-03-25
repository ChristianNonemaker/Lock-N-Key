"""
DK NCAAB Research UI — Streamlit entrypoint.

Provides four views per §15:
  1. Game Browser  — filter by date/team, quick-glance EV signals
  2. Game Detail   — deep-dive: movement, splits, snapshots, outcomes
  3. Model Panel   — predicted probabilities, residuals, EV
  4. Backtest      — ROI/CLV/drawdown/Sharpe across strategies

Start:
    streamlit run ui/app.py
"""

import streamlit as st

st.set_page_config(
    page_title="DK NCAAB Research",
    page_icon="🏀",
    layout="wide",
    initial_sidebar_state="expanded",
)

API_BASE = "http://localhost:8000"

st.sidebar.title("🏀 DK NCAAB")
st.sidebar.markdown("---")

page = st.sidebar.radio(
    "Navigation",
    [
        "Game Browser",
        "Team History",
        "Game Detail",
        "Model Panel",
        "Backtest Dashboard",
        "Pipeline Status",
    ],
    index=0,
)

if page == "Game Browser":
    from ui.pages import game_browser
    game_browser.render(API_BASE)
elif page == "Team History":
    from ui.pages import team_history
    team_history.render(API_BASE)
elif page == "Game Detail":
    from ui.pages import game_detail
    game_detail.render(API_BASE)
elif page == "Model Panel":
    from ui.pages import model_panel
    model_panel.render(API_BASE)
elif page == "Backtest Dashboard":
    from ui.pages import backtest_dashboard
    backtest_dashboard.render(API_BASE)
elif page == "Pipeline Status":
    from ui.pages import pipeline_status
    pipeline_status.render(API_BASE)
