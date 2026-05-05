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


def _slate_intelligence(score: int, headline: str, evidence: str = "Research only") -> dict:
    return {
        "score": score,
        "tier": "review" if score >= 45 else "monitor",
        "headline": headline,
        "primary_action": "open_research" if score >= 45 else "monitor",
        "next_action_label": "Open line research" if score >= 45 else "Monitor",
        "reasons": ["current DK lines", "fresh odds", "market moved"],
        "gaps": ["strict OOF evidence missing"],
        "strongest_move_label": "North Carolina spread",
        "strongest_number_move": -1.0,
        "strongest_price_move_american": None,
        "split_pressure_label": "spread home",
        "split_gap": 0.13,
        "evidence_label": evidence,
        "signals": [
            {"label": "Freshness", "value": "8m fresh", "detail": "Current DK snapshot available"},
            {"label": "Strongest Move", "value": "North Carolina spread", "detail": "number -1"},
            {"label": "Split Pressure", "value": "13% gap", "detail": "spread home"},
            {"label": "Evidence", "value": evidence, "detail": "Open research before acting"},
        ],
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
    "slate_intelligence": _slate_intelligence(67, "Worth review"),
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
    "slate_intelligence": _slate_intelligence(72, "Open first", evidence="OOF: moneyline, total"),
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

MLB_MARKET_READINESS_RESPONSE = {
    "generated_at_utc": datetime.now(timezone.utc).isoformat(),
    "summary": {
        "sport": "baseball_mlb",
        "league_key": "mlb",
        "window_start_utc": _iso(0),
        "window_end_utc": _iso(23),
        "markets_ready": 3,
        "markets_thin": 2,
        "markets_collect_more": 2,
        "markets_missing_data": 0,
        "total_current_quoted_rows": 42,
        "total_oof_predicted_rows": 294,
        "artifact_generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "artifact_anchor": "T60",
        "artifact_path": "artifacts/entry_ev/oof/fixture/predictions.parquet",
    },
    "markets": [
        {
            "market": "moneyline",
            "label": "Moneyline",
            "market_type": "core",
            "verdict": "ready",
            "current_quoted_rows": 2,
            "current_quoted_events": 1,
            "settled_quoted_rows": 82,
            "settled_quoted_events": 41,
            "oof_predicted_rows": 82,
            "oof_recommended_rows": 29,
            "participant_quote_rows": 0,
            "participant_linked_rows": 0,
            "participant_link_rate": None,
            "stat_context_rows": 632,
            "stat_context_label": "team logs",
            "priority_score": 10,
            "next_action": "ready_for_review",
            "next_action_label": "Ready for review",
            "next_action_command": "python -m dk_ncaab oof-entry-ev --sport baseball_mlb --anchor T60",
            "next_action_reason": "Current lines, settled history, stats context, and strict OOF coverage are present.",
            "gaps": [],
            "notes": ["Current DraftKings lines are available."],
        },
        {
            "market": "team_totals",
            "label": "Team Totals",
            "market_type": "event_specific",
            "verdict": "thin",
            "current_quoted_rows": 4,
            "current_quoted_events": 1,
            "settled_quoted_rows": 4,
            "settled_quoted_events": 1,
            "oof_predicted_rows": 4,
            "oof_recommended_rows": 1,
            "participant_quote_rows": 8,
            "participant_linked_rows": 8,
            "participant_link_rate": 1.0,
            "stat_context_rows": 632,
            "stat_context_label": "team logs",
            "priority_score": 55,
            "next_action": "grow_settled_event_market_sample",
            "next_action_label": "Grow settled prop sample",
            "next_action_command": (
                "python -m dk_ncaab collect-event-odds --sport baseball_mlb "
                "--markets team_totals --max-events 3"
            ),
            "next_action_reason": "Strict OOF rows exist, but the sample is still thin for this market.",
            "gaps": ["thin_oof_sample"],
            "notes": ["Strict OOF evidence exists for this market."],
        },
        {
            "market": "batter_hits",
            "label": "Batter Hits",
            "market_type": "event_specific",
            "verdict": "ready",
            "current_quoted_rows": 18,
            "current_quoted_events": 1,
            "settled_quoted_rows": 36,
            "settled_quoted_events": 1,
            "oof_predicted_rows": 36,
            "oof_recommended_rows": 12,
            "participant_quote_rows": 54,
            "participant_linked_rows": 54,
            "participant_link_rate": 1.0,
            "stat_context_rows": 6373,
            "stat_context_label": "batter Statcast days",
            "priority_score": 10,
            "next_action": "ready_for_review",
            "next_action_label": "Ready for review",
            "next_action_command": "python -m dk_ncaab oof-entry-ev --sport baseball_mlb --anchor T60",
            "next_action_reason": "Current lines, settled history, stats context, and strict OOF coverage are present.",
            "gaps": [],
            "notes": ["Current DraftKings lines are available."],
        },
    ],
    "warnings": [],
}

MLB_EVIDENCE_GROWTH_RESPONSE = {
    "available": True,
    "generated_at_utc": datetime.now(timezone.utc).isoformat(),
    "label": "screenshot-fixture",
    "previous_generated_at_utc": None,
    "summary": {
        "event_specific_quotes": 218,
        "event_specific_pregame_events": 4,
        "unlinked_event_specific_player_quotes": 2,
        "unlinked_event_specific_team_quotes": 0,
        "total_oof_predicted_rows": 294,
        "top_next_action": "grow_settled_event_market_sample",
        "top_next_action_label": "Grow settled prop sample",
    },
    "priority_markets": [
        {
            "market": "team_totals",
            "label": "Team Totals",
            "verdict": "thin",
            "previous_verdict": None,
            "verdict_changed": False,
            "current_quoted_rows": 4,
            "current_quoted_rows_delta": 0,
            "settled_quoted_rows": 4,
            "settled_quoted_rows_delta": 0,
            "oof_predicted_rows": 4,
            "oof_predicted_rows_delta": 0,
            "priority_score": 55,
            "next_action": "grow_settled_event_market_sample",
            "next_action_label": "Grow settled prop sample",
            "next_action_command": (
                "python -m dk_ncaab collect-event-odds --sport baseball_mlb "
                "--markets team_totals --max-events 3"
            ),
            "next_action_reason": "Strict OOF rows exist, but the sample is still thin.",
            "gaps": ["thin_oof_sample"],
        }
    ],
    "markets": [
        {
            "market": "team_totals",
            "label": "Team Totals",
            "verdict": "thin",
            "previous_verdict": "thin",
            "verdict_changed": False,
            "current_quoted_rows": 4,
            "current_quoted_rows_delta": 0,
            "settled_quoted_rows": 4,
            "settled_quoted_rows_delta": 0,
            "oof_predicted_rows": 4,
            "oof_predicted_rows_delta": 0,
            "priority_score": 55,
            "next_action": "grow_settled_event_market_sample",
            "next_action_label": "Grow settled prop sample",
            "gaps": ["thin_oof_sample"],
        },
        {
            "market": "batter_hits",
            "label": "Batter Hits",
            "verdict": "ready",
            "previous_verdict": "thin",
            "verdict_changed": True,
            "current_quoted_rows": 54,
            "current_quoted_rows_delta": 18,
            "settled_quoted_rows": 36,
            "settled_quoted_rows_delta": 0,
            "oof_predicted_rows": 36,
            "oof_predicted_rows_delta": 0,
            "priority_score": 10,
            "next_action": "ready_for_review",
            "next_action_label": "Ready for review",
            "gaps": [],
        },
    ],
    "warnings": ["2 event-specific player quotes are not linked to local player identities."],
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
    "market_context": [],
    "team_trends": {},
    "starter_context": {},
    "environment_context": {
        "provider": None,
        "available": False,
        "venue_name": None,
        "roof_type": None,
        "park_factor_runs": None,
        "park_factor_hr": None,
        "note": "Environment context is not wired for this sport yet.",
    },
    "data_gaps": ["player_stats_provider_pending"],
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
            "recent_games": [
                {
                    "start_time_utc": _iso(1),
                    "opponent": {"name": "St. Louis Cardinals"},
                    "is_home": False,
                    "team_score": 5,
                    "opp_score": 3,
                    "won": True,
                    "close_ml": 118,
                    "close_spread": None,
                    "close_implied_team_total": 4.1,
                    "close_implied_opponent_total": 4.4,
                },
                {
                    "start_time_utc": _iso(2),
                    "opponent": {"name": "Cincinnati Reds"},
                    "is_home": True,
                    "team_score": 4,
                    "opp_score": 5,
                    "won": False,
                    "close_ml": -102,
                    "close_spread": None,
                    "close_implied_team_total": 4.3,
                    "close_implied_opponent_total": 4.2,
                },
                {
                    "start_time_utc": _iso(3),
                    "opponent": {"name": "Pittsburgh Pirates"},
                    "is_home": False,
                    "team_score": 6,
                    "opp_score": 2,
                    "won": True,
                    "close_ml": 124,
                    "close_spread": None,
                    "close_implied_team_total": 4.0,
                    "close_implied_opponent_total": 4.5,
                },
            ],
        },
        "home": {
            "team": {"id": 11, "name": "Chicago Cubs"},
            "record": "8-4",
            "ats_record": "0-0-0",
            "ou_record": "0-0-0",
            "recent_games": [
                {
                    "start_time_utc": _iso(1),
                    "opponent": {"name": "Houston Astros"},
                    "is_home": True,
                    "team_score": 6,
                    "opp_score": 4,
                    "won": True,
                    "close_ml": -135,
                    "close_spread": None,
                    "close_implied_team_total": 4.8,
                    "close_implied_opponent_total": 3.9,
                },
                {
                    "start_time_utc": _iso(2),
                    "opponent": {"name": "San Diego Padres"},
                    "is_home": False,
                    "team_score": 5,
                    "opp_score": 3,
                    "won": True,
                    "close_ml": -120,
                    "close_spread": None,
                    "close_implied_team_total": 4.6,
                    "close_implied_opponent_total": 4.0,
                },
                {
                    "start_time_utc": _iso(3),
                    "opponent": {"name": "Los Angeles Dodgers"},
                    "is_home": True,
                    "team_score": 4,
                    "opp_score": 5,
                    "won": False,
                    "close_ml": 102,
                    "close_spread": None,
                    "close_implied_team_total": 4.5,
                    "close_implied_opponent_total": 4.4,
                },
            ],
        },
    },
    "market_context": [
        {
            "market": "moneyline",
            "side": "home",
            "selection": "Chicago Cubs moneyline",
            "current_line": None,
            "current_price_american": -138,
            "open_line": None,
            "open_price_american": -125,
            "best_entry_anchor": "OPEN",
            "best_entry_line": None,
            "best_entry_price_american": -125,
            "implied_probability": 0.58,
            "price_move_implied_from_open": 0.018,
            "price_move_american_from_open": -13,
            "number_move_from_open": None,
            "latest_quote_utc": _iso(18),
            "is_live": False,
            "is_stale": False,
            "bets_pct": 54.0,
            "handle_pct": 66.0,
            "recent_record_last_n": "3-1",
            "recent_win_rate_last_n": 0.75,
            "avg_market_price_american_last_n": -118,
            "avg_market_implied_probability_last_n": 0.541,
            "current_implied_delta_vs_avg_last_n": 0.039,
            "recent_results_vs_market_prices": [
                {
                    "game_date_utc": _iso(1),
                    "label": "04-18",
                    "price_american": -135,
                    "implied_probability": 0.574,
                    "result": "W",
                },
                {
                    "game_date_utc": _iso(2),
                    "label": "04-19",
                    "price_american": -120,
                    "implied_probability": 0.545,
                    "result": "W",
                },
                {
                    "game_date_utc": _iso(3),
                    "label": "04-20",
                    "price_american": 102,
                    "implied_probability": 0.495,
                    "result": "L",
                },
            ],
            "signal_notes": ["handle_bet_split"],
        }
    ],
    "line_evidence_status": [
        {
            "focus_key": "moneyline:home:chicago-cubs-moneyline",
            "market": "moneyline",
            "side": "home",
            "participant_name": "Chicago Cubs moneyline",
            "current_line": None,
            "current_price_american": -138,
            "line_lifecycle_status": "current",
            "market_readiness_verdict": "ready",
            "settled_sample_size": 82,
            "posted_line_sample_size": 3,
            "oof_predicted_rows": 82,
            "oof_recommended_rows": 29,
            "evidence_tier": "validated_sample",
            "gaps": [],
        },
        {
            "focus_key": "team_totals:over:chicago-cubs",
            "market": "team_totals",
            "side": "over",
            "participant_name": "Chicago Cubs",
            "current_line": 4.7,
            "current_price_american": -118,
            "line_lifecycle_status": "current",
            "market_readiness_verdict": "thin",
            "settled_sample_size": 12,
            "posted_line_sample_size": 3,
            "oof_predicted_rows": 24,
            "oof_recommended_rows": 2,
            "evidence_tier": "thin_validated",
            "gaps": ["sample_size_thin"],
        },
        {
            "focus_key": "pitcher_strikeouts:over:chicago-starter",
            "market": "pitcher_strikeouts",
            "side": "over",
            "participant_name": "Chicago Starter",
            "current_line": 6.5,
            "current_price_american": -122,
            "line_lifecycle_status": "current",
            "market_readiness_verdict": "collect_more",
            "settled_sample_size": 4,
            "posted_line_sample_size": 1,
            "oof_predicted_rows": 4,
            "oof_recommended_rows": 1,
            "evidence_tier": "thin_validated",
            "gaps": ["sample_size_thin"],
        }
    ],
    "line_thesis": [
        {
            "focus_key": "pitcher_strikeouts:over:chicago-starter",
            "market": "pitcher_strikeouts",
            "side": "over",
            "participant_name": "Chicago Starter",
            "headline": (
                "Chicago Starter Pitcher Strikeouts over: Thin Validated readout "
                "for pitcher strikeouts at O 6.5 -122"
            ),
            "action_status": "thin validated",
            "line_quality_score": 82,
            "evidence_quality_score": 46,
            "current_summary": "O 6.5 -122",
            "movement_summary": "number +1, price -12, best entry OPEN",
            "history_summary": "1-0-0 vs today's line; 1-0-0 vs posted lines",
            "evidence_summary": "Evidence thin: 4 OOF rows, 4 settled rows",
            "risk_summary": "thin validated; thin posted-line history; sample_size_thin",
            "support_points": [
                "Current DraftKings line is stored locally",
                "Recent current-line record: 1-0-0",
                "Recent posted-line record: 1-0-0",
            ],
            "caution_points": [
                "Treat as sample-sensitive until settled market history grows",
                "Posted-line sample is still thin",
            ],
            "next_step": "Grow settled priced sample before promotion.",
        },
        {
            "focus_key": "team_totals:over:chicago-cubs",
            "market": "team_totals",
            "side": "over",
            "participant_name": "Chicago Cubs",
            "headline": (
                "Chicago Cubs team total over: Thin Validated readout "
                "for team total at O 4.7 -118"
            ),
            "action_status": "thin validated",
            "line_quality_score": 88,
            "evidence_quality_score": 52,
            "current_summary": "O 4.7 -118",
            "movement_summary": "number +0.2, price -8, best entry OPEN",
            "history_summary": "2-1-0 vs today's line; 2-1-0 vs posted lines",
            "evidence_summary": "Evidence thin: 24 OOF rows, 12 settled rows",
            "risk_summary": "thin validated; sample_size_thin",
            "support_points": [
                "Current DraftKings line is stored locally",
                "Recent current-line record: 2-1-0",
            ],
            "caution_points": ["Treat as sample-sensitive until settled market history grows"],
            "next_step": "Grow settled priced sample before promotion.",
        },
    ],
    "team_line_evidence": [
        {
            "team_name": "Milwaukee Brewers",
            "line_source": "draftkings_team_total_market",
            "open_team_total": 3.7,
            "current_team_total": 3.9,
            "best_entry_team_total": 3.7,
            "best_entry_anchor": "OPEN",
            "current_over_price_american": -112,
            "current_under_price_american": -108,
            "open_over_price_american": -110,
            "open_under_price_american": -110,
            "over_price_move_american_from_open": -2,
            "under_price_move_american_from_open": 2,
            "number_move_from_open": 0.2,
            "latest_quote_utc": _iso(18),
            "games_sampled": 3,
            "posted_line_games_sampled": 2,
            "record_vs_current_line_last_n": "2-1-0",
            "avg_margin_vs_current_line_last_n": 0.5,
            "recent_results_vs_market_lines": [
                {
                    "game_date_utc": _iso(1),
                    "label": "04-18",
                    "value": 5.0,
                    "line": 4.1,
                    "margin_vs_line": 0.9,
                    "result": "O",
                }
            ],
            "avg_runs_vs_close_implied_last_n": 0.8,
            "avg_allowed_vs_close_implied_last_n": -0.2,
        },
        {
            "team_name": "Chicago Cubs",
            "line_source": "draftkings_team_total_market",
            "open_team_total": 4.5,
            "current_team_total": 4.7,
            "best_entry_team_total": 4.5,
            "best_entry_anchor": "OPEN",
            "current_over_price_american": -118,
            "current_under_price_american": -102,
            "open_over_price_american": -110,
            "open_under_price_american": -110,
            "over_price_move_american_from_open": -8,
            "under_price_move_american_from_open": 8,
            "number_move_from_open": 0.2,
            "latest_quote_utc": _iso(18),
            "games_sampled": 3,
            "posted_line_games_sampled": 3,
            "record_vs_current_line_last_n": "2-1-0",
            "avg_margin_vs_current_line_last_n": 0.7,
            "recent_results_vs_market_lines": [
                {
                    "game_date_utc": _iso(1),
                    "label": "04-18",
                    "value": 6.0,
                    "line": 4.8,
                    "margin_vs_line": 1.2,
                    "result": "O",
                }
            ],
            "avg_runs_vs_close_implied_last_n": 0.4,
            "avg_allowed_vs_close_implied_last_n": 0.1,
        },
    ],
    "team_trends": {
        "away": {
            "team": {"id": 12, "name": "Milwaukee Brewers"},
            "games": 7,
            "win_pct": 0.571,
            "avg_runs_for_l5": 4.4,
            "avg_runs_against_l5": 3.8,
            "run_diff_l5": 0.6,
            "avg_bullpen_outs_l3": 10.0,
            "rest_days": 1,
            "last_game_utc": _iso(1),
            "note": None,
        },
        "home": {
            "team": {"id": 11, "name": "Chicago Cubs"},
            "games": 8,
            "win_pct": 0.625,
            "avg_runs_for_l5": 5.2,
            "avg_runs_against_l5": 4.0,
            "run_diff_l5": 1.2,
            "avg_bullpen_outs_l3": 8.0,
            "rest_days": 1,
            "last_game_utc": _iso(1),
            "note": None,
        },
    },
    "starter_context": {
        "away": {
            "team": {"id": 12, "name": "Milwaukee Brewers"},
            "player_id": 1201,
            "player_name": "Milwaukee Starter",
            "primary_position": "SP",
            "prior_starts": 2,
            "era_l3": 3.6,
            "whip_l3": 1.18,
            "k_bb_l3": 9,
            "avg_ip_l3": 5.2,
            "avg_pitches_l3": 88.0,
            "note": None,
        },
        "home": {
            "team": {"id": 11, "name": "Chicago Cubs"},
            "player_id": 1101,
            "player_name": "Chicago Starter",
            "primary_position": "SP",
            "prior_starts": 2,
            "era_l3": 2.7,
            "whip_l3": 1.05,
            "k_bb_l3": 11,
            "avg_ip_l3": 6.0,
            "avg_pitches_l3": 91.0,
            "note": None,
        },
    },
    "bullpen_usage": [
        {
            "team_name": "Milwaukee Brewers",
            "pitcher_name": "Brewers Reliever A",
            "outings_last_3d": 2,
            "outs_last_3d": 5,
            "pitches_last_3d": 32,
            "last_appearance_utc": _iso(4),
        },
        {
            "team_name": "Milwaukee Brewers",
            "pitcher_name": "Brewers Reliever B",
            "outings_last_3d": 1,
            "outs_last_3d": 3,
            "pitches_last_3d": 17,
            "last_appearance_utc": _iso(3),
        },
        {
            "team_name": "Chicago Cubs",
            "pitcher_name": "Cubs Reliever A",
            "outings_last_3d": 2,
            "outs_last_3d": 6,
            "pitches_last_3d": 29,
            "last_appearance_utc": _iso(4),
        },
        {
            "team_name": "Chicago Cubs",
            "pitcher_name": "Cubs Reliever B",
            "outings_last_3d": 1,
            "outs_last_3d": 4,
            "pitches_last_3d": 22,
            "last_appearance_utc": _iso(3),
        },
    ],
    "environment_context": {
        "provider": "nws_api",
        "available": True,
        "venue_name": "Wrigley Field",
        "roof_type": "open",
        "park_factor_runs": None,
        "park_factor_hr": None,
        "temperature_f": 61.0,
        "wind_mph": 15.0,
        "wind_direction": "NW",
        "wind_from_degrees": 315.0,
        "wind_to_center_alignment": 0.72,
        "wind_out_mph": 10.8,
        "wind_in_mph": 0.0,
        "crosswind_mph": 10.4,
        "field_wind_label": "blowing out",
        "precipitation_chance": 12.0,
        "conditions": "Mostly Clear",
        "forecast_for_utc": _iso(23),
        "collected_at_utc": _iso(18),
        "note": "Park-factor source pending review.",
    },
    "why_this_line": [
        {
            "factor": "market_pressure",
            "market_focus": "side",
            "lean": "home",
            "headline": "Chicago drawing firmer money than ticket share suggests",
        },
        {
            "factor": "starter_edge",
            "market_focus": "side",
            "lean": "home",
            "headline": "Chicago starter enters with the steadier recent run prevention",
        },
        {
            "factor": "run_environment",
            "market_focus": "game",
            "lean": "over",
            "headline": "Wrigley wind still matters even with a modest total",
        },
    ],
    "data_gaps": ["park_factor_source_pending"],
    "player_stats": [
        {
            "player_name": "Milwaukee Starter",
            "team_name": "Milwaukee Brewers",
            "position": "SP",
            "last_games": [{"date": "2026-04-18", "ip": 5.2, "er": 2, "h": 5, "bb": 1, "k": 6}],
            "note": None,
        }
    ],
    "player_stats_note": "Fixture data for screenshot verification.",
    "player_prop_insights": [
        {
            "market_key": "pitcher_strikeouts",
            "market_label": "Pitcher Strikeouts",
            "player_name": "Chicago Starter",
            "team_name": "Chicago Cubs",
            "line_source": "draftkings_event_market",
            "open_line": 5.5,
            "current_line": 6.5,
            "open_over_price_american": -110,
            "over_price_american": -122,
            "open_under_price_american": -110,
            "under_price_american": 100,
            "best_entry_anchor": "OPEN",
            "best_entry_line": 5.5,
            "best_entry_over_price_american": -110,
            "best_entry_under_price_american": -110,
            "number_move_from_open": 1.0,
            "over_price_move_american_from_open": -12,
            "under_price_move_american_from_open": 10,
            "latest_quote_utc": _iso(18),
            "games_sampled": 2,
            "posted_line_games_sampled": 1,
            "avg_last_n": 7.0,
            "hit_rate_over_last_n": 0.5,
            "hit_rate_under_last_n": 0.5,
            "recent_results_vs_market_lines": [
                {
                    "game_date_utc": _iso(1),
                    "label": "04-18",
                    "value": 7.0,
                    "line": 5.5,
                    "margin_vs_line": 1.5,
                    "result": "O",
                }
            ],
            "context_note": "Starter prop context from recent MLB logs.",
        }
    ],
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
        elif path == "/analysis/mlb/market-readiness":
            self._json(MLB_MARKET_READINESS_RESPONSE)
        elif path == "/analysis/mlb/evidence-growth/latest":
            self._json(MLB_EVIDENCE_GROWTH_RESPONSE)
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
            "mlb-focused-line": (
                f"http://127.0.0.1:{STREAMLIT_PORT}"
                "?page=Sportsbook%20Board&sport=baseball_mlb&mode=today"
                "&date=2026-04-21&event_id=202&focus_market=pitcher_strikeouts"
                "&focus_side=over&focus_key=pitcher_strikeouts:over:chicago-starter",
                "Line Thesis",
            ),
            "mlb-queue": (
                f"http://127.0.0.1:{STREAMLIT_PORT}"
                "?page=Sportsbook%20Board&sport=baseball_mlb&mode=today"
                "&date=2026-04-21&lens=queue",
                "A secondary lens that prioritizes current lines",
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
                    marker = page.get_by_text(wait_text).first
                    marker.wait_for(timeout=15000)
                    if scenario == "mlb-focused-line":
                        marker.scroll_into_view_if_needed(timeout=5000)
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
