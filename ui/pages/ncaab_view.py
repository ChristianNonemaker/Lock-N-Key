"""
NCAAB-focused workspace page with separate Game View and Team View.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx
import pandas as pd
import streamlit as st

_ET = ZoneInfo("America/New_York")
_SPORT = "basketball_ncaab"


def _utc_to_et(iso_str: str) -> datetime:
    dt = datetime.fromisoformat(iso_str)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(_ET)


def _fmt_time(iso_str: str) -> str:
    et = _utc_to_et(iso_str)
    s = et.strftime("%I:%M %p")
    return s[1:] if s.startswith("0") else s


def _fmt_date(iso_str: str) -> str:
    et = _utc_to_et(iso_str)
    return et.strftime("%m/%d")


def _spread(val: float | None) -> str:
    if val is None:
        return "-"
    return f"{val:+g}" if val else "0"


def _get(api_base: str, path: str, params: dict | None = None) -> dict:
    resp = httpx.get(f"{api_base}{path}", params=params, timeout=30)
    resp.raise_for_status()
    return resp.json()


def _render_game_view(api_base: str) -> None:
    st.subheader("Game View")
    mode = st.selectbox(
        "Window",
        ["Upcoming 7 days", "Recent finals (last 30 days)"],
        key="ncaab_games_window",
        index=1,
    )
    if mode == "Upcoming 7 days":
        status_filter = st.selectbox("Status", ["upcoming", "all", "live", "final"], key="ncaab_games_status")
    else:
        status_filter = "final"
    team_filter = st.text_input("Team contains", placeholder="e.g. Duke", key="ncaab_games_team")

    base_params: dict = {"sport": _SPORT}
    if status_filter != "all":
        base_params["status"] = status_filter
    if team_filter:
        base_params["team"] = team_filter

    if mode == "Upcoming 7 days":
        days = [date.today() + timedelta(days=i) for i in range(7)]
    else:
        days = [date.today() - timedelta(days=i) for i in range(30)]

    any_games = False
    shown_days = 0
    for i, day in enumerate(days):
        params = {**base_params, "date": day.strftime("%Y-%m-%d")}
        try:
            data = _get(api_base, "/games", params=params)
        except Exception as e:
            st.error(f"Games API error: {e}")
            return

        games = data.get("games", [])
        if not games:
            continue

        any_games = True
        shown_days += 1
        with st.expander(f"{data['date']} ({len(games)} games)", expanded=(shown_days == 1)):
            rows = []
            for g in games:
                oline = g.get("open_lines") or {}
                cline = g.get("close_lines") or {}
                rows.append(
                    {
                        "Time (ET)": _fmt_time(g["start_time_utc"]),
                        "Away": g["away_team"]["name"],
                        "Home": g["home_team"]["name"],
                        "Status": g["status"],
                        "Score": (
                            f"{g['away_score']}-{g['home_score']}"
                            if g.get("away_score") is not None and g.get("home_score") is not None
                            else "-"
                        ),
                        "Close Spread": _spread(cline.get("spread")),
                        "Close Total": cline.get("total") if cline.get("total") is not None else "-",
                        "Close ML H": cline.get("ml_home") if cline.get("ml_home") is not None else "-",
                        "Close ML A": cline.get("ml_away") if cline.get("ml_away") is not None else "-",
                        "Open Spread": _spread(oline.get("spread")),
                        "event_id": g["event_id"],
                    }
                )

            df = pd.DataFrame(rows)
            st.dataframe(df.drop(columns=["event_id"]), use_container_width=True, hide_index=True)
            options = {
                f"{r['Away']} @ {r['Home']} ({r['Time (ET)']})": r["event_id"]
                for r in rows
            }
            selected = st.selectbox("Open in Game Detail", list(options.keys()), key=f"ncaab_day_pick_{i}")
            if selected:
                st.session_state["selected_event_id"] = options[selected]
                st.caption("Saved to Game Detail selection.")

    if not any_games:
        if mode == "Upcoming 7 days":
            st.info("No NCAAB games found in next 7 days for current filters.")
        else:
            st.info("No finalized NCAAB games found in the last 30 days for current filters.")


def _render_team_view(api_base: str) -> None:
    st.subheader("Team View")

    try:
        standings = _get(api_base, "/standings", params={"sport": _SPORT})
        status = _get(api_base, "/status")
    except Exception as e:
        st.error(f"Standings API error: {e}")
        return

    if int(status.get("odds_quotes", 0)) == 0:
        st.warning(
            "Historical scores are loaded, but odds line history is not populated yet. "
            "Spread/total/ML columns will be blank until odds collection runs over time."
        )

    rows = standings.get("rows", [])
    if not rows:
        st.info("No standings rows available yet.")
        return

    st.caption("Standings")
    standings_df = pd.DataFrame(rows)
    standings_df["Record"] = standings_df.apply(lambda r: f"{r['wins']}-{r['losses']}", axis=1)
    standings_df["ATS"] = standings_df.apply(
        lambda r: f"{r['ats_wins']}-{r['ats_losses']}-{r['ats_pushes']}", axis=1
    )
    standings_df["O/U"] = standings_df.apply(
        lambda r: f"{r['ou_overs']}-{r['ou_unders']}-{r['ou_pushes']}", axis=1
    )
    st.dataframe(
        standings_df[["team_name", "Record", "win_pct", "ATS", "O/U"]].rename(
            columns={"team_name": "Team", "win_pct": "Win%"}
        ),
        use_container_width=True,
        hide_index=True,
    )

    col1, col2 = st.columns(2)
    with col1:
        team_search = st.text_input("Find team", placeholder="e.g. Kansas", key="ncaab_team_search")
    with col2:
        team_options = {r["team_name"]: r["team_id"] for r in rows}
        selected_name = st.selectbox("Or pick from standings", list(team_options.keys()), key="ncaab_team_pick")

    team_id = team_options[selected_name]
    if team_search and len(team_search) >= 2:
        try:
            search_res = _get(api_base, "/teams", params={"q": team_search, "sport": _SPORT})
            match = search_res.get("teams", [])
            if match:
                team_id = int(match[0]["id"])
                selected_name = match[0]["name"]
        except Exception:
            pass

    try:
        history = _get(api_base, f"/teams/{team_id}/history")
    except Exception as e:
        st.error(f"Team history API error: {e}")
        return

    # Condensed team summary
    s1, s2, s3 = st.columns(3)
    s1.metric(f"{selected_name} SU", history.get("record", "-"))
    s2.metric("ATS", history.get("ats_record", "-"))
    s3.metric("O/U", history.get("ou_record", "-"))

    games = history.get("games", [])
    upcoming = [g for g in games if g.get("status") in ("upcoming", "live")]
    past = [g for g in games if g.get("status") == "final"]

    st.markdown("### Upcoming Games")
    if upcoming:
        up_rows = []
        for g in sorted(upcoming, key=lambda x: x["start_time_utc"]):
            up_rows.append(
                {
                    "Date": _fmt_date(g["start_time_utc"]),
                    "Time (ET)": _fmt_time(g["start_time_utc"]),
                    "Opponent": g["opponent"]["name"],
                    "Loc": "vs" if g.get("is_home") else "@",
                    "Close Spread": _spread(g.get("close_spread")),
                    "Close Total": g.get("close_total") if g.get("close_total") is not None else "-",
                    "Close ML": g.get("close_ml") if g.get("close_ml") is not None else "-",
                }
            )
        st.dataframe(pd.DataFrame(up_rows), use_container_width=True, hide_index=True)
    else:
        st.caption("No upcoming games found.")

    st.markdown("### Past Games")
    if past:
        past_rows = []
        for g in past:
            past_rows.append(
                {
                    "Date": _fmt_date(g["start_time_utc"]),
                    "Opponent": g["opponent"]["name"],
                    "Loc": "vs" if g.get("is_home") else "@",
                    "Score": f"{g.get('team_score', '-')}-{g.get('opp_score', '-')}",
                    "Result": "W" if g.get("won") else "L",
                    "Close Spread": _spread(g.get("close_spread")),
                    "Close Total": g.get("close_total") if g.get("close_total") is not None else "-",
                    "Close ML": g.get("close_ml") if g.get("close_ml") is not None else "-",
                    "ATS": g.get("spread_result") or "-",
                    "O/U": g.get("total_result") or "-",
                }
            )
        st.dataframe(pd.DataFrame(past_rows), use_container_width=True, hide_index=True)
    else:
        st.caption("No finalized games found.")


def render(api_base: str) -> None:
    st.header("🏀 NCAAB View")
    tab_games, tab_teams = st.tabs(["Game View", "Team View"])

    with tab_games:
        _render_game_view(api_base)

    with tab_teams:
        _render_team_view(api_base)
