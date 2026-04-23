"""Capture populated Sportsbook Board screenshots with a local mock API.

This script intentionally avoids live ESPN, Odds API, and Action Network calls.
It starts a tiny localhost HTTP server with fixture `/board` and research
payloads, then runs Streamlit against that server and captures desktop/mobile
screenshots through Playwright.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[1]
SCREENSHOT_DIR = ROOT / "artifacts" / "screenshots"
MOCK_PORT = 8765
STREAMLIT_PORT = 8509


def _iso(hour: int) -> str:
    return datetime(2026, 4, 21, hour, 30, tzinfo=timezone.utc).isoformat()


def _line(market: str, side: str, label: str, line: float | None, price: int) -> dict:
    return {
        "market": market,
        "side": side,
        "label": label,
        "team_name": label.rsplit(" ", 1)[0] if market != "total" else None,
        "line": line,
        "price_american": price,
        "implied_probability": 0.5238,
        "collected_at_utc": _iso(18),
        "is_live": False,
        "is_stale": False,
        "open_line": (line + 1.0) if isinstance(line, float) else None,
        "open_price_american": -110,
        "implied_move_from_open": 0.018,
        "line_move_from_open": -1.0 if isinstance(line, float) else None,
    }


BOARD_GAME = {
    "event_id": 101,
    "sport": "basketball_ncaab",
    "league_key": "ncaab",
    "start_time_utc": _iso(23),
    "status": "upcoming",
    "home_team": {"id": 1, "name": "Duke"},
    "away_team": {"id": 2, "name": "North Carolina"},
    "home_score": None,
    "away_score": None,
    "latest_quote_utc": _iso(18),
    "odds_age_min": 8,
    "odds_stale": False,
    "lines": [
        _line("spread", "away", "North Carolina spread", 4.5, -108),
        _line("spread", "home", "Duke spread", -4.5, -112),
        _line("total", "over", "Over", 148.5, -110),
        _line("total", "under", "Under", 148.5, -110),
        _line("moneyline", "away", "North Carolina moneyline", None, 164),
        _line("moneyline", "home", "Duke moneyline", None, -195),
    ],
    "split_summary": [
        {
            "market": "spread",
            "side": "home",
            "bets_pct": 61.0,
            "handle_pct": 48.0,
            "collected_at_utc": _iso(18),
        }
    ],
    "markets_available": ["moneyline", "spread", "total"],
    "flags": [],
}

BOARD_RESPONSE = {
    "generated_at_utc": datetime.now(timezone.utc).isoformat(),
    "sport": "basketball_ncaab",
    "mode": "today",
    "date": "2026-04-21",
    "count": 1,
    "games": [BOARD_GAME],
    "configured_sports": [
        "basketball_ncaab",
        "americanfootball_ncaaf",
        "americanfootball_nfl",
        "baseball_mlb",
    ],
    "warnings": [],
}

MLB_BOARD_GAME = {
    "event_id": 202,
    "sport": "baseball_mlb",
    "league_key": "mlb",
    "start_time_utc": _iso(23),
    "status": "upcoming",
    "home_team": {"id": 11, "name": "Chicago Cubs"},
    "away_team": {"id": 12, "name": "Milwaukee Brewers"},
    "home_score": None,
    "away_score": None,
    "latest_quote_utc": _iso(18),
    "odds_age_min": 8,
    "odds_stale": False,
    "lines": [
        _line("moneyline", "away", "Milwaukee Brewers moneyline", None, 118),
        _line("moneyline", "home", "Chicago Cubs moneyline", None, -138),
        _line("total", "over", "Over", 8.5, -105),
        _line("total", "under", "Under", 8.5, -115),
    ],
    "split_summary": [],
    "markets_available": ["moneyline", "total"],
    "flags": ["No public splits"],
}

MLB_BOARD_RESPONSE = {
    **BOARD_RESPONSE,
    "sport": "baseball_mlb",
    "count": 1,
    "games": [MLB_BOARD_GAME],
    "warnings": ["Multi-sport lines are available only where collectors have matching data."],
}

MLB_READINESS_RESPONSE = {
    "generated_at_utc": datetime.now(timezone.utc).isoformat(),
    "summary": {
        "sport": "baseball_mlb",
        "league_key": "mlb",
        "window_start_utc": _iso(0),
        "window_end_utc": _iso(23),
        "visible_events": 1,
        "events_with_provider_key": 1,
        "events_with_pregame_odds": 1,
        "events_with_both_probable_starters": 1,
        "events_with_both_team_history": 1,
        "events_with_both_starter_history": 1,
        "pending_pregame_events": 1,
        "settled_trainable_events": 0,
        "ready_after_settlement_events": 1,
    },
    "events": [
        {
            "event_id": 202,
            "start_time_utc": _iso(23),
            "status": "upcoming",
            "home_team": {"id": 11, "name": "Chicago Cubs"},
            "away_team": {"id": 12, "name": "Milwaukee Brewers"},
            "has_provider_key": True,
            "pregame_quote_count": 4,
            "has_pregame_odds": True,
            "home_team_logs_prior": 8,
            "away_team_logs_prior": 7,
            "home_starter": {
                "team_id": 11,
                "player_id": 1101,
                "player_name": "Chicago Starter",
                "prior_starts": 2,
            },
            "away_starter": {
                "team_id": 12,
                "player_id": 1201,
                "player_name": "Milwaukee Starter",
                "prior_starts": 2,
            },
            "both_probable_starters": True,
            "both_team_history": True,
            "both_starter_history": True,
            "ready_after_settlement": True,
            "gaps": ["awaiting_settlement"],
        }
    ],
    "warnings": ["No settled MLB events with pregame odds are modelable yet."],
}

RESEARCH_RESPONSE = {
    **BOARD_GAME,
    "snapshots": {},
    "features": [
        {
            "market": "spread",
            "side": "home",
            "implied_OPEN": 0.50,
            "implied_T60": 0.52,
            "implied_T30": 0.53,
            "implied_CLOSE": 0.54,
            "clv_OPEN": 0.04,
            "clv_T60": 0.02,
            "late_steam": 0.01,
            "model_expected_value": 0.03,
        }
    ],
    "team_metrics": {
        "away": {
            "team": {"id": 2, "name": "North Carolina"},
            "record": "17-8",
            "ats_record": "13-12-0",
            "ou_record": "11-14-0",
            "recent_games": [],
        },
        "home": {
            "team": {"id": 1, "name": "Duke"},
            "record": "21-4",
            "ats_record": "15-10-0",
            "ou_record": "12-13-0",
            "recent_games": [],
        },
    },
    "player_stats": [{"player_name": "Player stats source pending", "note": "Provider pending"}],
    "player_stats_note": "Player stat provider pending.",
    "warnings": ["Fixture data for screenshot verification."],
}

MLB_RESEARCH_RESPONSE = {
    **MLB_BOARD_GAME,
    "snapshots": {},
    "features": [],
    "team_metrics": {
        "away": {
            "team": {"id": 12, "name": "Milwaukee Brewers"},
            "record": "7-5",
            "ats_record": "0-0-0",
            "ou_record": "0-0-0",
            "recent_games": [],
        },
        "home": {
            "team": {"id": 11, "name": "Chicago Cubs"},
            "record": "8-4",
            "ats_record": "0-0-0",
            "ou_record": "0-0-0",
            "recent_games": [],
        },
    },
    "player_stats": [{"player_name": "MLB player logs available", "note": "Fixture data"}],
    "player_stats_note": "Fixture data for screenshot verification.",
    "warnings": ["Fixture data for screenshot verification."],
}


class MockApiHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)
        if path == "/board":
            if query.get("sport", ["basketball_ncaab"])[0] == "baseball_mlb":
                self._json(MLB_BOARD_RESPONSE)
            else:
                self._json(BOARD_RESPONSE)
        elif path == "/events/101/research":
            self._json(RESEARCH_RESPONSE)
        elif path == "/events/202/research":
            self._json(MLB_RESEARCH_RESPONSE)
        elif path == "/events/research":
            self._json(
                {
                    "generated_at_utc": datetime.now(timezone.utc).isoformat(),
                    "count": 1,
                    "events": [RESEARCH_RESPONSE],
                    "warnings": [],
                }
            )
        elif path == "/analysis/entry-ev/latest":
            self._json(
                {
                    "available": False,
                    "warnings": ["No OOF entry-EV artifact found in screenshot fixture."],
                }
            )
        elif path == "/analysis/mlb/readiness":
            self._json(MLB_READINESS_RESPONSE)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format: str, *args: object) -> None:
        return

    def _json(self, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _wait_for_url(url: str, timeout: float = 30.0) -> None:
    import urllib.request

    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1):
                return
        except Exception:
            time.sleep(0.4)
    raise RuntimeError(f"Timed out waiting for {url}")


def main() -> int:
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    mock_server = ThreadingHTTPServer(("127.0.0.1", MOCK_PORT), MockApiHandler)
    env = os.environ.copy()
    env["API_BASE"] = f"http://127.0.0.1:{MOCK_PORT}"

    streamlit = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "streamlit",
            "run",
            str(ROOT / "ui" / "app.py"),
            "--server.address",
            "127.0.0.1",
            "--server.port",
            str(STREAMLIT_PORT),
            "--server.headless",
            "true",
        ],
        cwd=ROOT,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    try:
        import threading

        thread = threading.Thread(target=mock_server.serve_forever, daemon=True)
        thread.start()
        _wait_for_url(f"http://127.0.0.1:{MOCK_PORT}/board")
        _wait_for_url(f"http://127.0.0.1:{STREAMLIT_PORT}")

        scenarios = {
            "populated": (
                f"http://127.0.0.1:{STREAMLIT_PORT}"
                "?page=Sportsbook%20Board&sport=basketball_ncaab&mode=today"
                "&date=2026-04-21&event_id=101",
                "North Carolina @ Duke",
            ),
            "mlb-readiness": (
                f"http://127.0.0.1:{STREAMLIT_PORT}"
                "?page=Sportsbook%20Board&sport=baseball_mlb&mode=today"
                "&date=2026-04-21&event_id=202",
                "MLB Data Readiness",
            ),
        }
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            for scenario, (url, wait_text) in scenarios.items():
                for name, viewport in {
                    "desktop": {"width": 1440, "height": 1100},
                    "mobile": {"width": 390, "height": 1100},
                }.items():
                    page = browser.new_page(viewport=viewport)
                    page.goto(url, wait_until="networkidle")
                    page.get_by_text(wait_text).wait_for(timeout=15000)
                    page.screenshot(
                        path=SCREENSHOT_DIR / f"sportsbook-board-{scenario}-{name}.png",
                        full_page=True,
                    )
                    page.close()
            browser.close()
    finally:
        mock_server.shutdown()
        streamlit.terminate()
        try:
            streamlit.wait(timeout=10)
        except subprocess.TimeoutExpired:
            streamlit.kill()

    print(f"Screenshots written to {SCREENSHOT_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
