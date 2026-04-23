"""
DK NCAAB Research UI — Streamlit entrypoint.

Provides four views per §15:
  1. Game Browser  — filter by date/team, quick-glance EV signals
  2. Game Detail   — deep-dive: movement, splits, snapshots, outcomes
  3. Model Panel   — predicted probabilities, residuals, EV
  4. Backtest      — ROI/CLV/drawdown/Sharpe across strategies

Start:
    streamlit run ui/app.py --server.address 127.0.0.1
"""

import os
import sys
from pathlib import Path
import streamlit as st

# Ensure project root is importable when Streamlit runs ui/app.py.
_UI_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _UI_DIR.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

st.set_page_config(
        page_title="Lock-N-Key Sportsbook Lab",
    page_icon="🏀",
    layout="wide",
    initial_sidebar_state="collapsed",
)

API_BASE = os.getenv("API_BASE", "http://127.0.0.1:8000")

st.markdown(
        """
        <style>
            :root {
                --lnk-bg: #0b0f0d;
                --lnk-panel: #121816;
                --lnk-panel-2: #171f1b;
                --lnk-accent: #23c55e;
                --lnk-accent-2: #86efac;
                --lnk-text: #e5efe9;
                --lnk-muted: #9db0a7;
            }

            .stApp {
                background:
                    radial-gradient(circle at 85% 8%, rgba(35, 197, 94, 0.16) 0%, rgba(35, 197, 94, 0) 35%),
                    radial-gradient(circle at 10% 22%, rgba(74, 222, 128, 0.12) 0%, rgba(74, 222, 128, 0) 30%),
                    linear-gradient(160deg, #0a0f0d 0%, #101614 45%, #0f1412 100%);
                color: var(--lnk-text);
            }

            .main .block-container {
                padding-top: 1.1rem;
                padding-bottom: 2rem;
            }

            section[data-testid="stSidebar"] {
                background: linear-gradient(180deg, var(--lnk-panel) 0%, #0f1412 100%);
                border-right: 1px solid #22352c;
            }

            section[data-testid="stSidebar"] .stRadio > div {
                background: var(--lnk-panel-2);
                border: 1px solid #2b3d34;
                border-radius: 12px;
                padding: 0.25rem;
            }

            section[data-testid="stSidebar"] .stRadio label {
                border-radius: 10px;
            }

            section[data-testid="stSidebar"] .stRadio label:hover {
                background: #1d2924;
            }

            h1, h2, h3 {
                color: #f4faf6;
                letter-spacing: 0.1px;
            }

            .stMetric {
                background: linear-gradient(180deg, #131c18 0%, #111915 100%);
                border: 1px solid #294034;
                border-radius: 12px;
                padding: 0.45rem 0.65rem;
            }

            .stDataFrame {
                border: 1px solid #2b4035;
                border-radius: 12px;
            }

            .stAlert {
                border-radius: 12px;
            }

            @media (max-width: 900px) {
                .main .block-container {
                    padding-left: 0.8rem;
                    padding-right: 0.8rem;
                }
                section[data-testid="stSidebar"] {
                    min-width: 14rem;
                }
            }
        </style>
        """,
        unsafe_allow_html=True,
)

st.sidebar.title("🏀 Lock-N-Key")
st.sidebar.caption("Private Sportsbook Research")
st.sidebar.markdown(f"API: `{API_BASE}`")
st.sidebar.markdown("---")

_PAGES = [
    "Sportsbook Board",
    "NCAAB View",
    "Game Browser",
    "Team History",
    "Game Detail",
    "Model Panel",
    "Backtest Dashboard",
    "Pipeline Status",
]

_query_page = st.query_params.get("page")
if isinstance(_query_page, list):
    _query_page = _query_page[0] if _query_page else None
_page_index = _PAGES.index(_query_page) if _query_page in _PAGES else 0

page = st.sidebar.radio(
    "Navigation",
    _PAGES,
    index=_page_index,
)
st.query_params["page"] = page

if page == "Sportsbook Board":
    from ui.pages import sportsbook_board
    sportsbook_board.render(API_BASE)
elif page == "NCAAB View":
    from ui.pages import ncaab_view
    ncaab_view.render(API_BASE)
elif page == "Game Browser":
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
