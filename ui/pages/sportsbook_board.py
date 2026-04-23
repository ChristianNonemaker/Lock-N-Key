"""
Sportsbook-style research board.

This page borrows the sportsbook interaction shape: sport filters, game rows,
clickable line buttons, an expanded research panel, and a right-side slip.
It is a research UI only; it does not place wagers.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pandas as pd
import streamlit as st

from dk_ncaab.config.sports import ui_sport_choices

_ET = ZoneInfo("America/New_York")
_WATCHLIST_FILE = Path("artifacts/state/research_watchlist.json")

_MODES = [
    ("live", "Live"),
    ("today", "Today"),
    ("upcoming", "Upcoming"),
]


def _query_value(name: str) -> str | None:
    value = st.query_params.get(name)
    if isinstance(value, list):
        return value[0] if value else None
    return value


def _query_date(name: str) -> date:
    raw = _query_value(name)
    if not raw:
        return date.today()
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return date.today()


def _query_event_id() -> int | None:
    raw = _query_value("event_id")
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _sync_board_query_params(
    sport: str,
    mode: str,
    selected_date: date,
    event_id: int | None = None,
) -> None:
    st.query_params["sport"] = sport
    st.query_params["mode"] = mode
    st.query_params["date"] = selected_date.isoformat()
    if event_id:
        st.query_params["event_id"] = str(event_id)
    elif "event_id" in st.query_params:
        del st.query_params["event_id"]


def _utc_to_et(value: str | None, include_date: bool = False) -> str:
    if not value:
        return "-"
    try:
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        et = dt.astimezone(_ET)
        fmt = "%m/%d %#I:%M %p" if include_date else "%#I:%M %p"
        try:
            return et.strftime(fmt)
        except ValueError:
            fallback = et.strftime(fmt.replace("%#I", "%I"))
            return fallback.replace(" 0", " ")
    except Exception:
        return value[:16]


def _price(value: int | None) -> str:
    if value is None:
        return "-"
    return f"{value:+d}"


def _line_value(line: dict) -> str:
    market = line.get("market")
    raw = line.get("line")
    price = _price(line.get("price_american"))
    if market == "moneyline":
        return price
    if raw is None:
        return price
    prefix = "+" if market == "spread" and raw > 0 else ""
    if market == "total":
        total_prefix = "O" if line.get("side") == "over" else "U"
        return f"{total_prefix} {raw:g} {price}"
    return f"{prefix}{raw:g} {price}"


def _move(value: float | None, pct: bool = False) -> str:
    if value is None:
        return "-"
    if pct:
        return f"{value:+.1%}"
    return f"{value:+g}"


@st.cache_data(ttl=30, show_spinner=False)
def _fetch_board(api_base: str, sport: str, mode: str, selected_date: date) -> dict:
    params = {
        "sport": sport,
        "mode": mode,
        "date": selected_date.strftime("%Y-%m-%d"),
    }
    resp = httpx.get(f"{api_base}/board", params=params, timeout=30)
    resp.raise_for_status()
    return resp.json()


@st.cache_data(ttl=60, show_spinner=False)
def _fetch_research(api_base: str, event_id: int) -> dict:
    resp = httpx.get(f"{api_base}/events/{event_id}/research", timeout=45)
    resp.raise_for_status()
    return resp.json()


@st.cache_data(ttl=60, show_spinner=False)
def _fetch_entry_ev_summary(api_base: str) -> dict:
    resp = httpx.get(f"{api_base}/analysis/entry-ev/latest", timeout=15)
    resp.raise_for_status()
    return resp.json()


@st.cache_data(ttl=60, show_spinner=False)
def _fetch_mlb_readiness(api_base: str) -> dict:
    resp = httpx.get(
        f"{api_base}/analysis/mlb/readiness",
        params={"sport": "baseball_mlb", "days_back": 1, "days_forward": 7, "limit": 80},
        timeout=20,
    )
    resp.raise_for_status()
    return resp.json()


def _load_watchlist() -> list[dict]:
    if not _WATCHLIST_FILE.exists():
        return []
    try:
        data = json.loads(_WATCHLIST_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []
    return data if isinstance(data, list) else []


def _save_watchlist(slip: list[dict]) -> None:
    _WATCHLIST_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = _WATCHLIST_FILE.with_suffix(".tmp")
    tmp_path.write_text(
        json.dumps(slip, indent=2, sort_keys=True, default=str),
        encoding="utf-8",
    )
    tmp_path.replace(_WATCHLIST_FILE)


def _ensure_slip() -> list[dict]:
    if "research_slip" not in st.session_state:
        st.session_state["research_slip"] = _load_watchlist()
    return st.session_state["research_slip"]


def _add_to_slip(game: dict, line: dict) -> None:
    slip = _ensure_slip()
    key = f"{game['event_id']}:{line['market']}:{line['side']}"
    if any(item["key"] == key for item in slip):
        return
    slip.append(
        {
            "key": key,
            "event_id": game["event_id"],
            "game": f"{game['away_team']['name']} @ {game['home_team']['name']}",
            "market": line["market"],
            "side": line["side"],
            "label": line["label"],
            "value": _line_value(line),
            "age": game.get("odds_age_min"),
            "stale": bool(game.get("odds_stale")),
            "added_at_utc": datetime.now(timezone.utc).isoformat(),
            "note": "",
        }
    )
    _save_watchlist(slip)


def _render_slip() -> None:
    slip = _ensure_slip()
    st.subheader("Research Slip")
    st.caption("Selected lines for review. This does not place bets.")

    if not slip:
        st.info("Click a line to pin it here.")
        return

    for item in list(slip):
        stale_text = "stale" if item.get("stale") else "fresh"
        with st.container(border=True):
            st.markdown(f"**{item['label']}**")
            st.caption(item["game"])
            st.metric(item["market"].title(), item["value"])
            st.caption(f"Odds age: {item.get('age', '-')} min - {stale_text}")
            if item.get("added_at_utc"):
                st.caption(f"Added: {item['added_at_utc'][:16].replace('T', ' ')} UTC")
            if st.button("Remove", key=f"remove_{item['key']}"):
                slip.remove(item)
                _save_watchlist(slip)
                st.rerun()

    if st.button("Clear slip", use_container_width=True):
        st.session_state["research_slip"] = []
        _save_watchlist([])
        st.rerun()


def _line_for(game: dict, market: str, side: str) -> dict | None:
    for line in game.get("lines", []):
        if line["market"] == market and line["side"] == side:
            return line
    return None


def _line_button(game: dict, line: dict | None, label: str) -> None:
    if not line:
        st.button(
            f"{label}\n-",
            disabled=True,
            use_container_width=True,
            key=f"missing_{game['event_id']}_{label}",
        )
        return
    stale = " !" if line.get("is_stale") else ""
    button_label = f"{label}{stale}\n{_line_value(line)}"
    if st.button(
        button_label,
        key=f"slip_{game['event_id']}_{line['market']}_{line['side']}",
        use_container_width=True,
    ):
        _add_to_slip(game, line)
        st.toast("Added to Research Slip")


def _render_game_row(api_base: str, game: dict, sport: str, mode: str, selected_date: date) -> None:
    away = game["away_team"]["name"]
    home = game["home_team"]["name"]
    status = game["status"].upper()
    score = ""
    if game.get("home_score") is not None:
        score = f" - {game['away_score']}:{game['home_score']}"
    age = game.get("odds_age_min")
    freshness = "No odds" if age is None else f"{age}m"
    if game.get("odds_stale"):
        freshness += " stale"

    title = f"{_utc_to_et(game['start_time_utc'], include_date=True)} | {away} @ {home} | {status}{score}"
    expanded = st.session_state.get("board_research_event_id") == game["event_id"]
    with st.expander(title, expanded=expanded):
        if game.get("flags"):
            st.warning(" | ".join(game["flags"]))

        meta_cols = st.columns([1, 1, 1, 1])
        meta_cols[0].metric("Status", status)
        meta_cols[1].metric("Odds Age", freshness)
        meta_cols[2].metric("Markets", ", ".join(game.get("markets_available", [])) or "-")
        meta_cols[3].metric("Event ID", game["event_id"])

        st.caption("Click a line to pin it to the Research Slip.")
        odds_cols = st.columns(6)
        with odds_cols[0]:
            _line_button(game, _line_for(game, "spread", "away"), "Away Sprd")
        with odds_cols[1]:
            _line_button(game, _line_for(game, "spread", "home"), "Home Sprd")
        with odds_cols[2]:
            _line_button(game, _line_for(game, "total", "over"), "Over")
        with odds_cols[3]:
            _line_button(game, _line_for(game, "total", "under"), "Under")
        with odds_cols[4]:
            _line_button(game, _line_for(game, "moneyline", "away"), "Away ML")
        with odds_cols[5]:
            _line_button(game, _line_for(game, "moneyline", "home"), "Home ML")

        if st.button("Load research panel", key=f"load_research_{game['event_id']}"):
            st.session_state["board_research_event_id"] = game["event_id"]
            _sync_board_query_params(sport, mode, selected_date, game["event_id"])

        if st.session_state.get("board_research_event_id") == game["event_id"]:
            _render_research_panel(api_base, game["event_id"])


def _render_research_panel(api_base: str, event_id: int) -> None:
    try:
        research = _fetch_research(api_base, event_id)
    except Exception as exc:
        st.error(f"Research payload failed: {exc}")
        return

    warnings = research.get("warnings", [])
    if warnings:
        st.info(" | ".join(warnings))

    tab_overview, tab_lines, tab_teams, tab_players, tab_model = st.tabs(
        ["Overview", "Lines", "Team Metrics", "Players", "Model Data"]
    )

    with tab_overview:
        cols = st.columns(4)
        cols[0].metric("Status", research["status"].upper())
        cols[1].metric("Odds Age", f"{research.get('odds_age_min') or '-'} min")
        cols[2].metric("Markets", len({line["market"] for line in research.get("lines", [])}))
        score = "-"
        if research.get("home_score") is not None:
            score = f"{research['away_score']} - {research['home_score']}"
        cols[3].metric("Score", score)

        if research.get("split_summary"):
            split_rows = [
                {
                    "Market": s["market"],
                    "Side": s["side"],
                    "Bets %": s.get("bets_pct"),
                    "Handle %": s.get("handle_pct"),
                    "Collected": _utc_to_et(s.get("collected_at_utc"), include_date=True),
                }
                for s in research["split_summary"]
            ]
            st.dataframe(pd.DataFrame(split_rows), use_container_width=True, hide_index=True)
        else:
            st.caption("No public split rows are available for this game.")

    with tab_lines:
        line_rows = [
            {
                "Market": line["market"],
                "Side": line["side"],
                "Selection": line["label"],
                "Current": _line_value(line),
                "Open Line": line.get("open_line"),
                "Open Price": _price(line.get("open_price_american")),
                "Imp Move": _move(line.get("implied_move_from_open"), pct=True),
                "Line Move": _move(line.get("line_move_from_open")),
                "Age": _utc_to_et(line.get("collected_at_utc"), include_date=True),
                "Live": line.get("is_live"),
                "Stale": line.get("is_stale"),
            }
            for line in research.get("lines", [])
        ]
        if line_rows:
            st.dataframe(pd.DataFrame(line_rows), use_container_width=True, hide_index=True)
        else:
            st.warning("No line data available.")

    with tab_teams:
        for side in ["away", "home"]:
            metric_key = "away" if side == "away" else "home"
            metrics = research["team_metrics"][metric_key]
            st.markdown(f"**{metrics['team']['name']}**")
            cols = st.columns(3)
            cols[0].metric("Record", metrics["record"])
            cols[1].metric("ATS", metrics["ats_record"])
            cols[2].metric("O/U", metrics["ou_record"])
            rows = []
            for row in metrics.get("recent_games", []):
                rows.append(
                    {
                        "Date": _utc_to_et(row["start_time_utc"], include_date=True),
                        "Opponent": row["opponent"]["name"],
                        "H/A": "Home" if row["is_home"] else "Away",
                        "Status": row["status"],
                        "Score": (
                            f"{row['team_score']}-{row['opp_score']}"
                            if row.get("team_score") is not None
                            else "-"
                        ),
                        "Open Sprd": row.get("open_spread"),
                        "Close Sprd": row.get("close_spread"),
                        "ATS": row.get("spread_result") or "-",
                        "O/U": row.get("total_result") or "-",
                    }
                )
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    with tab_players:
        st.warning(research.get("player_stats_note", "Player stats source pending."))
        st.dataframe(
            pd.DataFrame(research.get("player_stats", [])),
            use_container_width=True,
            hide_index=True,
        )

    with tab_model:
        rows = []
        for feat in research.get("features", []):
            rows.append(
                {
                    "Market": feat.get("market"),
                    "Side": feat.get("side"),
                    "Imp OPEN": feat.get("implied_OPEN"),
                    "Imp T60": feat.get("implied_T60"),
                    "Imp T30": feat.get("implied_T30"),
                    "Imp CLOSE": feat.get("implied_CLOSE"),
                    "CLV OPEN": feat.get("clv_OPEN"),
                    "CLV T60": feat.get("clv_T60"),
                    "Late Steam": feat.get("late_steam"),
                    "EV": feat.get("model_expected_value"),
                }
            )
        if rows:
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
        else:
            st.info("No feature rows are available yet.")


def _starter_readiness_label(starter: dict | None) -> str:
    if not starter:
        return "missing"
    name = starter.get("player_name") or "starter"
    starts = starter.get("prior_starts", 0)
    return f"{name} ({starts} prior)"


def _render_mlb_readiness(readiness: dict) -> None:
    summary = readiness.get("summary", {})
    with st.container(border=True):
        st.markdown("**MLB Data Readiness**")
        st.caption(
            "Local-only checks for the MLB odds, schedule, result, team trend, and starter "
            "history needed before strict entry-EV can train."
        )
        cols = st.columns(5)
        cols[0].metric("Settled Trainable", summary.get("settled_trainable_events", 0))
        cols[1].metric("Pending Pregame", summary.get("pending_pregame_events", 0))
        cols[2].metric("Provider Mapped", summary.get("events_with_provider_key", 0))
        cols[3].metric("Team History", summary.get("events_with_both_team_history", 0))
        cols[4].metric("Starter History", summary.get("events_with_both_starter_history", 0))

        ready_count = summary.get("ready_after_settlement_events", 0)
        if ready_count:
            st.success(f"{ready_count} MLB games look ready to become modelable after settlement.")
        else:
            st.info("No MLB games are fully ready after settlement yet. The table shows the gaps.")

        for warning in readiness.get("warnings", []):
            st.warning(warning)

        rows = []
        for event in readiness.get("events", [])[:12]:
            rows.append(
                {
                    "Game": f"{event['away_team']['name']} @ {event['home_team']['name']}",
                    "Start": _utc_to_et(event.get("start_time_utc"), include_date=True),
                    "Status": event.get("status"),
                    "Pregame Odds": event.get("pregame_quote_count", 0),
                    "Team Logs": (
                        f"{event.get('away_team_logs_prior', 0)}/"
                        f"{event.get('home_team_logs_prior', 0)}"
                    ),
                    "Away Starter": _starter_readiness_label(event.get("away_starter")),
                    "Home Starter": _starter_readiness_label(event.get("home_starter")),
                    "Gaps": ", ".join(event.get("gaps", [])) or "none",
                }
            )
        if rows:
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)


def render(api_base: str) -> None:
    st.header("Sportsbook Board")
    st.caption("Private research board for lines, movement, freshness, and game context.")

    sports = ui_sport_choices()
    sport_keys = [key for key, _ in sports]
    query_sport = _query_value("sport")
    default_sport = query_sport if query_sport in sport_keys else sport_keys[0]

    mode_keys = [key for key, _ in _MODES]
    query_mode = _query_value("mode")
    default_mode = query_mode if query_mode in mode_keys else "today"

    query_event_id = _query_event_id()
    if query_event_id is not None:
        st.session_state["board_research_event_id"] = query_event_id

    control_cols = st.columns([1.4, 1.2, 1.2])
    with control_cols[0]:
        sport = st.radio(
            "Sport",
            sports,
            index=sport_keys.index(default_sport),
            format_func=lambda item: item[1],
            horizontal=True,
            key="board_sport",
        )[0]
    with control_cols[1]:
        mode = st.radio(
            "Board",
            _MODES,
            index=mode_keys.index(default_mode),
            format_func=lambda item: item[1],
            horizontal=True,
            key="board_mode",
        )[0]
    with control_cols[2]:
        selected_date = st.date_input("Date", value=_query_date("date"), key="board_date")

    selected_event_id = st.session_state.get("board_research_event_id")
    _sync_board_query_params(sport, mode, selected_date, selected_event_id)

    try:
        board = _fetch_board(api_base, sport, mode, selected_date)
    except Exception as exc:
        st.error(f"API error: {exc}")
        st.info("Start the API with: `uvicorn api.main:app --host 127.0.0.1 --port 8000`")
        return

    for warning in board.get("warnings", []):
        st.warning(warning)

    try:
        ev_summary = _fetch_entry_ev_summary(api_base)
    except Exception:
        ev_summary = {"available": False, "warnings": ["Entry-EV artifact status unavailable."]}

    if ev_summary.get("available"):
        with st.expander("Validated Entry-EV Artifact", expanded=False):
            cols = st.columns(4)
            cols[0].metric("Anchor", ev_summary.get("anchor") or "-")
            cols[1].metric("OOF Rows", ev_summary.get("rows_predicted", 0))
            cols[2].metric("Recommended", ev_summary.get("recommended_count", 0))
            cols[3].metric("ROI", f"{ev_summary.get('recommended_roi', 0.0):+.1%}")
            st.caption(
                "Evidence is from out-of-fold historical rows. Treat small samples as exploratory."
            )
            for warning in ev_summary.get("warnings", []):
                st.warning(warning)
    else:
        st.info(
            "No validated entry-EV artifact is available yet. "
            "Run `python -m dk_ncaab oof-entry-ev` after collecting settled odds with prices."
        )

    if sport == "baseball_mlb":
        try:
            _render_mlb_readiness(_fetch_mlb_readiness(api_base))
        except Exception as exc:
            st.warning(f"MLB readiness diagnostics unavailable: {exc}")

    main_col, slip_col = st.columns([3.2, 1.15], gap="large")
    with slip_col:
        _render_slip()

    with main_col:
        games = board.get("games", [])
        if selected_event_id and not any(game["event_id"] == selected_event_id for game in games):
            st.session_state.pop("board_research_event_id", None)
            selected_event_id = None
            _sync_board_query_params(sport, mode, selected_date, None)
        st.metric("Games", len(games))
        if not games:
            st.info("No games matched this board filter. Try Upcoming or another sport.")
            return

        for game in games:
            _render_game_row(api_base, game, sport, mode, selected_date)
