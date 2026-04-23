"""
Team History page.

Select a team and see:
  - Overall record (W-L)
  - ATS record and O/U record
  - Full game log with open/close lines, results, and ATS outcomes
  - Line movement patterns
"""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import httpx
import streamlit as st
import pandas as pd

from dk_ncaab.config.sports import ui_sport_choices

_ET = ZoneInfo("America/New_York")


def _utc_to_et_date(iso_str: str) -> str:
    """Convert an ISO-8601 UTC timestamp to 'M/D' ET string."""
    try:
        dt = datetime.fromisoformat(iso_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        et = dt.astimezone(_ET)
        return et.strftime("%m/%d")
    except Exception:
        return iso_str[:10]


def _spread(val: float | None) -> str:
    if val is None:
        return "—"
    sign = "+" if val > 0 else ""
    return f"{sign}{val:g}"


def _ml(val: int | None) -> str:
    if val is None:
        return "—"
    return f"{val:+d}"


def _total(val: float | None) -> str:
    if val is None:
        return "—"
    return f"{val:g}"


def _result_emoji(won: bool | None) -> str:
    if won is None:
        return ""
    return "✅" if won else "❌"


def _ats_emoji(result: str | None) -> str:
    if result is None:
        return ""
    return {"W": "✅", "L": "❌", "P": "➖"}.get(result, "")


def _ou_emoji(result: str | None) -> str:
    if result is None:
        return ""
    return {"O": "⬆", "U": "⬇", "P": "➖"}.get(result, "")


def _last_n_summary(final_games: list[dict], n: int = 10) -> tuple[str, str, str]:
    recent = final_games[:n]
    if not recent:
        return "N/A", "N/A", "N/A"

    su_w = sum(1 for g in recent if g.get("won"))
    su = f"{su_w}-{len(recent) - su_w}"

    ats_w = sum(1 for g in recent if g.get("spread_result") == "W")
    ats_l = sum(1 for g in recent if g.get("spread_result") == "L")
    ats_p = sum(1 for g in recent if g.get("spread_result") == "P")
    ats = f"{ats_w}-{ats_l}-{ats_p}"

    ou_o = sum(1 for g in recent if g.get("total_result") == "O")
    ou_u = sum(1 for g in recent if g.get("total_result") == "U")
    ou_p = sum(1 for g in recent if g.get("total_result") == "P")
    ou = f"{ou_o}-{ou_u}-{ou_p}"

    return su, ats, ou


def render(api_base: str) -> None:
    st.header("🏀 Team History")

    sport_filter = st.selectbox(
        "Sport",
        ui_sport_choices(),
        format_func=lambda x: x[1],
    )

    # Team search
    search = st.text_input("Search for a team", placeholder="e.g. Duke, Gonzaga, Kansas")
    if not search or len(search) < 2:
        st.info("Enter at least 2 characters to search for a team.")
        return

    # Fetch matching teams
    try:
        resp = httpx.get(
            f"{api_base}/teams",
            params={"q": search, "sport": sport_filter[0]},
            timeout=10,
        )
        resp.raise_for_status()
        teams_data = resp.json()
    except Exception as e:
        st.error(f"API error: {e}")
        return

    teams = teams_data.get("teams", [])
    if not teams:
        st.warning(f'No teams found matching "{search}"')
        return

    # Team selector
    team_options = {t["name"]: t["id"] for t in teams}
    if len(teams) == 1:
        selected_name = teams[0]["name"]
    else:
        selected_name = st.selectbox("Select team", list(team_options.keys()))

    team_id = team_options[selected_name]

    # Fetch history
    try:
        resp = httpx.get(f"{api_base}/teams/{team_id}/history", timeout=30)
        resp.raise_for_status()
        history = resp.json()
    except Exception as e:
        st.error(f"API error: {e}")
        return

    # ── Summary metrics ─────────────────────────────────────
    st.subheader(f"{history['team']['name']}")

    m1, m2, m3 = st.columns(3)
    m1.metric("Record", history["record"])
    m2.metric("ATS Record", history["ats_record"])
    m3.metric("O/U Record", history["ou_record"])

    with st.expander("⚔️ Team Matchup Lab (H2H + form)", expanded=False):
        compare_search = st.text_input(
            "Compare against",
            placeholder="e.g. North Carolina",
            key="team_compare_search",
        )
        if compare_search and len(compare_search) >= 2:
            try:
                cmp_resp = httpx.get(
                    f"{api_base}/teams",
                    params={"q": compare_search, "sport": sport_filter[0]},
                    timeout=10,
                )
                cmp_resp.raise_for_status()
                cmp_teams = [
                    t for t in cmp_resp.json().get("teams", []) if int(t["id"]) != int(team_id)
                ]
            except Exception as e:
                st.error(f"Comparison lookup failed: {e}")
                cmp_teams = []

            if cmp_teams:
                cmp_options = {t["name"]: t["id"] for t in cmp_teams}
                cmp_name = st.selectbox("Opponent", list(cmp_options.keys()), key="team_compare_opp")
                opp_id = cmp_options[cmp_name]

                try:
                    opp_hist_resp = httpx.get(f"{api_base}/teams/{opp_id}/history", timeout=30)
                    opp_hist_resp.raise_for_status()
                    opp_history = opp_hist_resp.json()
                except Exception as e:
                    st.error(f"Opponent history error: {e}")
                    opp_history = None

                if opp_history:
                    left, right = st.columns(2)
                    with left:
                        st.markdown(f"**{history['team']['name']}**")
                        st.metric("Season SU", history["record"])
                        st.metric("Season ATS", history["ats_record"])
                        st.metric("Season O/U", history["ou_record"])
                    with right:
                        st.markdown(f"**{opp_history['team']['name']}**")
                        st.metric("Season SU", opp_history["record"])
                        st.metric("Season ATS", opp_history["ats_record"])
                        st.metric("Season O/U", opp_history["ou_record"])

                    team_final = [g for g in history.get("games", []) if g["status"] == "final"]
                    opp_final = [g for g in opp_history.get("games", []) if g["status"] == "final"]

                    lsu, lats, lou = _last_n_summary(team_final, n=10)
                    rsu, rats, rou = _last_n_summary(opp_final, n=10)
                    st.caption("Last 10 form")
                    f1, f2, f3 = st.columns(3)
                    f1.metric(f"{history['team']['name']} SU/ATS/O-U", f"{lsu} | {lats} | {lou}")
                    f2.metric(f"{opp_history['team']['name']} SU/ATS/O-U", f"{rsu} | {rats} | {rou}")

                    h2h_games = [
                        g for g in team_final
                        if int(g.get("opponent", {}).get("id", -1)) == int(opp_id)
                    ]
                    h2h_w = sum(1 for g in h2h_games if g.get("won"))
                    h2h_l = len(h2h_games) - h2h_w
                    h2h_ats_w = sum(1 for g in h2h_games if g.get("spread_result") == "W")
                    h2h_ats_l = sum(1 for g in h2h_games if g.get("spread_result") == "L")
                    h2h_ats_p = sum(1 for g in h2h_games if g.get("spread_result") == "P")
                    h2h_ou_o = sum(1 for g in h2h_games if g.get("total_result") == "O")
                    h2h_ou_u = sum(1 for g in h2h_games if g.get("total_result") == "U")
                    h2h_ou_p = sum(1 for g in h2h_games if g.get("total_result") == "P")

                    st.caption("Head-to-head (from selected team perspective)")
                    h1, h2, h3 = st.columns(3)
                    h1.metric("H2H SU", f"{h2h_w}-{h2h_l}")
                    h2.metric("H2H ATS", f"{h2h_ats_w}-{h2h_ats_l}-{h2h_ats_p}")
                    h3.metric("H2H O/U", f"{h2h_ou_o}-{h2h_ou_u}-{h2h_ou_p}")

                    if h2h_games:
                        st.caption(f"Last meeting: {_utc_to_et_date(h2h_games[0]['start_time_utc'])}")
            else:
                st.info("No comparison team found for that search.")

    st.markdown("---")

    # ── Game log ────────────────────────────────────────────
    games = history.get("games", [])
    if not games:
        st.info("No games found for this team.")
        return

    # Filter
    status_filter = st.selectbox("Show", ["All games", "Final only", "Upcoming only"], index=0)

    filtered = games
    if status_filter == "Final only":
        filtered = [g for g in games if g["status"] == "final"]
    elif status_filter == "Upcoming only":
        filtered = [g for g in games if g["status"] == "upcoming"]

    if not filtered:
        st.info("No games match the filter.")
        return

    rows = []
    for g in filtered:
        loc = "vs" if g["is_home"] else "@"
        opp = g["opponent"]["name"]

        # Score
        score = ""
        if g.get("team_score") is not None:
            score = f"{g['team_score']}-{g['opp_score']}"

        # Line movement
        spread_move = ""
        if g.get("open_spread") is not None and g.get("close_spread") is not None:
            diff = g["close_spread"] - g["open_spread"]
            if abs(diff) >= 0.5:
                spread_move = f"{'⬆' if diff > 0 else '⬇'}{abs(diff):g}"

        rows.append({
            "Date": _utc_to_et_date(g["start_time_utc"]),
            "": loc,
            "Opponent": opp,
            "Result": _result_emoji(g.get("won")),
            "Score": score,
            "Open": _spread(g.get("open_spread")),
            "Close": _spread(g.get("close_spread")),
            "Move": spread_move,
            "ATS": f"{_ats_emoji(g.get('spread_result'))} {g.get('spread_result', '')}".strip(),
            "Total": _total(g.get("close_total")),
            "O/U": f"{_ou_emoji(g.get('total_result'))} {g.get('total_result', '')}".strip(),
            "ML": _ml(g.get("close_ml")),
            "event_id": g["event_id"],
        })

    df = pd.DataFrame(rows)

    st.dataframe(
        df.drop(columns=["event_id"]),
        use_container_width=True,
        hide_index=True,
    )

    # ── Trends summary ──────────────────────────────────────
    final_games = [g for g in games if g["status"] == "final"]
    if final_games:
        with st.expander("📈 Trends & Insights"):
            # Recent form (last 10)
            recent = final_games[:10]
            recent_w = sum(1 for g in recent if g.get("won"))
            recent_ats = sum(1 for g in recent if g.get("spread_result") == "W")
            recent_ou_o = sum(1 for g in recent if g.get("total_result") == "O")

            t1, t2, t3 = st.columns(3)
            t1.metric("Last 10 SU", f"{recent_w}-{len(recent) - recent_w}")
            ats_total = sum(1 for g in recent if g.get("spread_result") in ("W", "L", "P"))
            t2.metric("Last 10 ATS", f"{recent_ats}-{ats_total - recent_ats}" if ats_total else "N/A")
            ou_total = sum(1 for g in recent if g.get("total_result") in ("O", "U", "P"))
            t3.metric("Last 10 O/U", f"{recent_ou_o} Over / {ou_total - recent_ou_o} Under" if ou_total else "N/A")

            # Home vs Away split
            home_games = [g for g in final_games if g.get("is_home")]
            away_games = [g for g in final_games if not g.get("is_home")]
            home_w = sum(1 for g in home_games if g.get("won"))
            away_w = sum(1 for g in away_games if g.get("won"))

            h1, h2 = st.columns(2)
            h1.metric("Home Record", f"{home_w}-{len(home_games) - home_w}" if home_games else "N/A")
            h2.metric("Away Record", f"{away_w}-{len(away_games) - away_w}" if away_games else "N/A")

            # Favorite vs Underdog
            fav_games = [g for g in final_games if g.get("close_spread") is not None and g["close_spread"] < 0]
            dog_games = [g for g in final_games if g.get("close_spread") is not None and g["close_spread"] > 0]
            fav_ats = sum(1 for g in fav_games if g.get("spread_result") == "W")
            dog_ats = sum(1 for g in dog_games if g.get("spread_result") == "W")

            f1, f2 = st.columns(2)
            f1.metric("As Favorite ATS", f"{fav_ats}-{len(fav_games) - fav_ats}" if fav_games else "N/A")
            f2.metric("As Underdog ATS", f"{dog_ats}-{len(dog_games) - dog_ats}" if dog_games else "N/A")

    # Game selection for detail
    st.markdown("---")
    game_options = {
        f"{_utc_to_et_date(g['start_time_utc'])} {'vs' if g['is_home'] else '@'} {g['opponent']['name']}": g["event_id"]
        for g in filtered
    }
    selected_game = st.selectbox("Select game for detail view", list(game_options.keys()))
    if selected_game:
        st.session_state["selected_event_id"] = game_options[selected_game]
        st.info(
            f"Event ID: {game_options[selected_game]} — "
            "switch to **Game Detail** in the sidebar."
        )
