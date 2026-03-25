"""
Splits collector -- scrapes Action Network public betting splits.

Uses Playwright (headless Chromium) to render the JS-heavy page,
extracts bets% for spread per visible game (money% is PRO-paywalled),
then matches to existing events or stages in unmatched_splits.

Artifacts: raw HTML + PNG screenshot archived each run.

DOM reference (Action Network, validated Feb 2025):
  - Game rows: <tr> containing div.public-betting__game-info
  - Away team (listed first):  div.game-info__team--desktop > span
  - Home team (listed second): div.game-info__team--desktop > span
  - Bet % per side: span.highlight-text__children  ("40%", "60%")
  - Money %: LOCKED behind PRO paywall (skip)
  - Ad / video rows: <tr> with td[colspan]  (skip)
  - Header row: <tr> with <th> elements  (skip)
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from dk_ncaab.config.settings import get_settings
from dk_ncaab.db.session import SessionLocal
from dk_ncaab.db.models import SplitsQuote, SplitsRawPayload, UnmatchedSplit
from dk_ncaab.etl.normalize import match_event

log = logging.getLogger(__name__)

_PCT_RE = re.compile(r"(\d+)\s*%")


# ── Parsed row from the page ───────────────────────────────────

class _RawSplit:
    __slots__ = ("team_a", "team_b", "market", "side", "bets_pct", "handle_pct")

    def __init__(self, team_a: str, team_b: str, market: str, side: str,
                 bets_pct: float, handle_pct: float):
        self.team_a = team_a
        self.team_b = team_b
        self.market = market
        self.side = side
        self.bets_pct = bets_pct
        self.handle_pct = handle_pct


# ── Page interaction ────────────────────────────────────────────

def _scrape_splits_page() -> tuple[str, bytes, list[_RawSplit]]:
    """
    Launch headless browser, load splits page, extract data.
    Returns (html, screenshot_bytes, parsed_rows).

    DOM selectors target Action Network table-based public-betting layout.
    Updated Feb 2025 after DOM restructure.
    """
    from playwright.sync_api import sync_playwright

    cfg = get_settings().splits
    rows: list[_RawSplit] = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=cfg.headless)
        page = browser.new_page()
        page.goto(cfg.url, timeout=cfg.timeout_ms, wait_until="networkidle")

        # Extra wait for JS to hydrate bet-% widgets
        page.wait_for_timeout(4000)

        html = page.content()
        screenshot = page.screenshot(full_page=True)

        # ── Select game rows (skip header <th> rows and ad <td colspan> rows)
        all_trs = page.query_selector_all("tr")
        game_trs = []
        for tr in all_trs:
            # Skip header rows
            if tr.query_selector("th"):
                continue
            # Skip ad / video embed rows (td with colspan)
            if tr.query_selector("td[colspan]"):
                continue
            # Must contain game-info div to be a real game row
            if tr.query_selector("div.public-betting__game-info"):
                game_trs.append(tr)

        log.info("Found %d game rows on splits page", len(game_trs))

        for tr in game_trs:
            try:
                _parse_game_row(tr, rows)
            except Exception as e:
                log.debug("Row parse error: %s", e)

        browser.close()

    return html, screenshot, rows


def _parse_game_row(tr, rows: list[_RawSplit]) -> None:
    """
    Extract team names and spread bet-% from a single <tr> game row.

    DOM structure per row:
      td[0]: game-info  (time, teams with icons)
      td[1]: open spread  (two div.public-betting__open-cell)
      td[2]: best odds    (book-cell__odds)
      td[3]: % of Bets    (two span.highlight-text__children with "XX%")
      td[4]: % of Money   (LOCKED -- PRO paywall)
      td[5]: Diff
      td[6]: Total Bets
    """
    # ── Team names ──────────────────────────────────────────────
    # The desktop team name spans are the most reliable.
    # First team listed = away, second = home.
    team_spans = tr.query_selector_all("div.game-info__team--desktop span")
    # Filter out empty / rotation-number spans
    team_names = []
    for s in team_spans:
        txt = (s.inner_text() or "").strip()
        # Skip rotation numbers (pure digits) and empty
        if txt and not txt.isdigit():
            team_names.append(txt)

    if len(team_names) < 2:
        return

    # Deduplicate: some spans have duplicate class nesting
    # e.g. <span class="game-info__team--desktop">SC State</span>
    # inside <div class="game-info__team--desktop">
    seen: list[str] = []
    for n in team_names:
        if n not in seen:
            seen.append(n)
    team_names = seen

    if len(team_names) < 2:
        return

    team_a = team_names[0]  # away
    team_b = team_names[1]  # home

    # ── Bet percentages ─────────────────────────────────────────
    # Located in span.highlight-text__children, e.g. "40%", "60%"
    pct_spans = tr.query_selector_all("span.highlight-text__children")
    pct_values: list[float] = []
    for span in pct_spans:
        txt = (span.inner_text() or "").strip()
        m = _PCT_RE.search(txt)
        if m:
            pct_values.append(float(m.group(1)))

    if len(pct_values) < 2:
        # Some games might not have bet % loaded yet
        log.debug("No bet%% for %s vs %s", team_a, team_b)
        return

    # First two percentages are away/home spread bet %
    bets_away = pct_values[0]
    bets_home = pct_values[1]

    # Money % is paywalled -- store None (0.0 as sentinel)
    rows.append(_RawSplit(team_a, team_b, "spread", "away", bets_away, 0.0))
    rows.append(_RawSplit(team_a, team_b, "spread", "home", bets_home, 0.0))


# ── Archive artifacts ───────────────────────────────────────────

def _save_artifacts(html: str, screenshot: bytes, collected_at: datetime) -> str | None:
    """Save raw HTML and screenshot to disk. Return screenshot path."""
    cfg = get_settings().storage
    ts = collected_at.strftime("%Y%m%dT%H%M%S")

    html_dir = Path(cfg.raw_html_dir)
    html_dir.mkdir(parents=True, exist_ok=True)
    (html_dir / f"splits_{ts}.html").write_text(html, encoding="utf-8")

    ss_dir = Path(cfg.screenshots_dir)
    ss_dir.mkdir(parents=True, exist_ok=True)
    ss_path = ss_dir / f"splits_{ts}.png"
    ss_path.write_bytes(screenshot)
    return str(ss_path)


# ── Insert logic ────────────────────────────────────────────────

def _insert_splits(
    session: Session,
    parsed: list[_RawSplit],
    collected_at: datetime,
    html: str,
    screenshot_path: str | None,
) -> tuple[int, int]:
    """
    Match parsed rows to events, insert splits_quotes or unmatched_splits.
    Returns (matched_count, unmatched_count).
    """
    matched = 0
    unmatched = 0

    for row in parsed:
        event = match_event(
            session,
            row.team_a,
            row.team_b,
            # Splits page shows the full day's slate; use noon ET as center
            # with a 720-min (12h) window to cover all tip-off times.
            approx_start_utc=collected_at.replace(hour=17, minute=0, second=0),
            source="dknetwork",
            tolerance_min=720,
        )

        if event:
            session.add(SplitsQuote(
                event_id=event.id,
                market=row.market,
                side=row.side,
                bets_pct=row.bets_pct,
                handle_pct=row.handle_pct,
                collected_at_utc=collected_at,
                source="dknetwork",
            ))
            matched += 1
        else:
            session.add(UnmatchedSplit(
                collected_at_utc=collected_at,
                raw_team_a=row.team_a,
                raw_team_b=row.team_b,
                market=row.market,
                side=row.side,
                bets_pct=row.bets_pct,
                handle_pct=row.handle_pct,
                raw_text=f"{row.team_a} vs {row.team_b}",
                notes="auto-unmatched",
            ))
            unmatched += 1

    # Archive raw payload in DB
    session.add(SplitsRawPayload(
        collected_at_utc=collected_at,
        payload_html=html[:500_000],  # truncate very large pages
        screenshot_path=screenshot_path,
    ))

    return matched, unmatched


# ── Public entry point ──────────────────────────────────────────

def collect_splits() -> int:
    """
    Single scrape cycle: render page -> parse -> match -> insert -> archive.
    Returns number of matched split rows inserted.
    """
    log.info("Starting splits collection cycle")

    try:
        html, screenshot, parsed = _scrape_splits_page()
    except Exception as e:
        log.error("Splits scrape failed: %s", e)
        return 0

    # Canary check: if we got zero rows on a page that loaded, something broke
    if not parsed:
        log.warning("CANARY: splits parse yielded 0 rows -- possible DOM change")
        return 0

    collected_at = datetime.now(timezone.utc)
    screenshot_path = _save_artifacts(html, screenshot, collected_at)

    with SessionLocal() as session:
        matched, unmatched = _insert_splits(session, parsed, collected_at, html, screenshot_path)
        session.commit()

    log.info("Splits cycle complete: %d matched, %d unmatched", matched, unmatched)
    return matched
