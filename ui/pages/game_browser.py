"""
§15.1 — Game Browser page.

Clean, intuitive game grid showing:
  - Pre-game open and close lines (never live/in-game odds)
  - Line movement (open → close shift)
  - Score and result for completed games
  - Times in US/Eastern
  - Click to drill into game detail
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import httpx
import streamlit as st
import pandas as pd

_ET = ZoneInfo("America/New_York")


# ── Formatting helpers ──────────────────────────────────────────

def _utc_to_et(iso_str: str, include_date: bool = False) -> str:
    """Convert an ISO-8601 UTC timestamp to a short ET time string.

    The 12-hour clock: 12:00 PM = noon, 12:30 AM = half-past midnight.
    """
    try:
        dt = datetime.fromisoformat(iso_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        et = dt.astimezone(_ET)
        fmt = "%m/%d %I:%M %p" if include_date else "%I:%M %p"
        try:
            result = et.strftime(fmt.replace("%I", "%#I"))
        except ValueError:
            result = et.strftime(fmt)
            # Strip leading zero from hour ("07:00 PM" -> "7:00 PM")
            if result[0] == "0":
                result = result[1:]
        return result
    except Exception:
        return iso_str[:16]


def _spread(val: float | None) -> str:
    if val is None:
        return "—"
    sign = "+" if val > 0 else ""
    return f"{sign}{val:g}"


def _price(val: int | None) -> str:
    if val is None:
        return ""
    return f"({val:+d})"


def _ml(val: int | None) -> str:
    if val is None:
        return "—"
    return f"{val:+d}"


def _total(val: float | None) -> str:
    if val is None:
        return "—"
    return f"{val:g}"


def _movement(open_val: float | None, close_val: float | None) -> str:
    """Show line movement as a signed number (e.g. '+1.0' or '-0.5')."""
    if open_val is None or close_val is None:
        return ""
    diff = close_val - open_val
    if abs(diff) < 0.01:
        return ""
    return f"{diff:+g}"


# ── Page render ─────────────────────────────────────────────────

def render(api_base: str) -> None:
    st.header("📋 Game Browser")

    # Filters
    col1, col2, col3 = st.columns([2, 2, 2])
    with col1:
        selected_date = st.date_input("Date", value=date.today())
    with col2:
        team_filter = st.text_input("Team search", placeholder="e.g. Duke")
    with col3:
        status_filter = st.selectbox("Status", ["all", "upcoming", "live", "final"])

    date_str = selected_date.strftime("%Y-%m-%d")
    params: dict = {"date": date_str}
    if team_filter:
        params["team"] = team_filter
    if status_filter != "all":
        params["status"] = status_filter

    try:
        resp = httpx.get(f"{api_base}/games", params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        st.error(f"API error: {e}")
        st.info("Make sure the API is running: `uvicorn api.main:app --port 8000`")
        return

    games = data.get("games", [])
    if not games:
        st.info(f"No games found for {date_str}")
        return

    st.caption(f"{data['count']} games on {data['date']}  ·  all lines are pre-game  ·  times in ET")

    # Build display rows
    rows = []
    for g in games:
        status_icon = {"upcoming": "🟡", "live": "🟢", "final": "⚫"}.get(g["status"], "⚪")
        oline = g.get("open_lines") or {}
        cline = g.get("close_lines") or {}

        # Score column
        score = ""
        if g.get("home_score") is not None:
            score = f"{g['away_score']} - {g['home_score']}"

        # Spread: show close line with movement indicator
        open_sprd = oline.get("spread")
        close_sprd = cline.get("spread")
        spread_col = _spread(close_sprd) if close_sprd is not None else _spread(open_sprd)
        spread_price_col = _price(cline.get("spread_price")) if cline.get("spread_price") else _price(oline.get("spread_price"))

        # Total: show close with movement
        open_tot = oline.get("total")
        close_tot = cline.get("total")
        total_col = _total(close_tot) if close_tot is not None else _total(open_tot)

        # ML: show close
        ml_h = cline.get("ml_home") if cline.get("ml_home") is not None else oline.get("ml_home")
        ml_a = cline.get("ml_away") if cline.get("ml_away") is not None else oline.get("ml_away")

        rows.append({
            "": status_icon,
            "Time (ET)": _utc_to_et(g["start_time_utc"]),
            "Away": g["away_team"]["name"],
            "Home": g["home_team"]["name"],
            "Score": score,
            "Spread": f"{spread_col} {spread_price_col}".strip(),
            "Sprd Move": _movement(open_sprd, close_sprd),
            "Total": total_col,
            "Tot Move": _movement(open_tot, close_tot),
            "ML H": _ml(ml_h),
            "ML A": _ml(ml_a),
            "event_id": g["event_id"],
        })

    df = pd.DataFrame(rows)

    st.dataframe(
        df.drop(columns=["event_id"]),
        use_container_width=True,
        hide_index=True,
    )

    # Open/Close comparison (expandable)
    with st.expander("📊 Open → Close Line Movement Detail"):
        detail_rows = []
        for g in games:
            oline = g.get("open_lines") or {}
            cline = g.get("close_lines") or {}
            if not oline and not cline:
                continue

            away = g["away_team"]["name"]
            home = g["home_team"]["name"]

            detail_rows.append({
                "Game": f"{away} @ {home}",
                "Open Spread": _spread(oline.get("spread")),
                "Close Spread": _spread(cline.get("spread")),
                "Sprd Move": _movement(oline.get("spread"), cline.get("spread")),
                "Open Total": _total(oline.get("total")),
                "Close Total": _total(cline.get("total")),
                "Tot Move": _movement(oline.get("total"), cline.get("total")),
                "Open ML H": _ml(oline.get("ml_home")),
                "Close ML H": _ml(cline.get("ml_home")),
                "Open ML A": _ml(oline.get("ml_away")),
                "Close ML A": _ml(cline.get("ml_away")),
            })

        if detail_rows:
            st.dataframe(pd.DataFrame(detail_rows), use_container_width=True, hide_index=True)
        else:
            st.info("No pre-game odds data available for these games.")

    # Game selection
    st.markdown("---")
    st.subheader("Select a game for detail view")
    game_options = {
        f"{g['away_team']['name']} @ {g['home_team']['name']} ({_utc_to_et(g['start_time_utc'])})": g["event_id"]
        for g in games
    }
    selected = st.selectbox("Game", list(game_options.keys()))
    if selected:
        st.session_state["selected_event_id"] = game_options[selected]
        st.info(
            f"Event ID: {game_options[selected]} — "
            "switch to **Game Detail** in the sidebar to view."
        )
