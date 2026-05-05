---
name: sportsbook-ui-designer
description: 'Design and iterate the private sportsbook dashboard UI for this repo. Use when working on Streamlit or API-backed UI/UX, controls, responsive layouts, empty states, visual hierarchy, screenshots, data-access flows, or human-centered dashboard polish for betting lines, model signals, backtests, and pipeline status.'
---

# Sportsbook UI Designer

## Purpose

Create polished, fast, human-centered UI for a private sports betting research dashboard. Keep the experience useful for daily line review, data validation, signal inspection, and operational awareness on a low-resource VM.

## When to Use

- Working with `ui/app.py` or files in `ui/pages/`
- Updating Streamlit layout, tabs, or metrics
- Creating empty states (when odds or artifacts are missing)
- Debugging responsive behavior (mobile vs. desktop)

## Workflow

1. Read `memory/ui-and-api.md` and the specific `ui/pages/*` file or API endpoint involved.
2. Name the user task in plain language, such as "find today's games with line movement" or "understand why model signals are empty".
3. Define a small rubric before editing:
   - Primary action is obvious.
   - Filters match how a bettor thinks: sport, date, team, market, status, edge strength.
   - Empty states explain what is missing and how to fix it.
   - Desktop and mobile layouts remain readable.
   - Expensive API calls are cached or limited.
4. Capture baseline screenshots when the app can run locally. Use at least one desktop and one mobile viewport. Include populated and empty states when possible.
5. Change one page or flow at a time unless the user asks for a larger redesign.
6. Re-run the page, capture comparison screenshots, and check for overflow, clipped labels, hidden primary actions, slow rerenders, and state handoff regressions.
7. Report the changed files, validation, screenshots reviewed, and remaining UX risks.

## Design Rules

- Keep Streamlit unless the user explicitly asks for another frontend stack.
- Design for a private single-user dashboard, not a marketing site.
- Start with the usable experience. Do not add a landing page.
- Prefer clear hierarchy: summary, controls, key chart/table, drill-down.
- Avoid giant all-at-once tables. Use compact summaries, filters, tabs, and expanders.
- Make missing data states first-class: no model artifacts, no recent runs, no odds, no splits, no final results.
- Do not hide risk language. Distinguish EV, CLV, ROI, model residuals, and exploratory metrics clearly.
- Keep color meaningful. Do not rely on color alone for important states.
- Keep heavy assets and rerun-heavy interactions out of the dashboard. This must remain comfortable on a tiny VM.

## Screenshot Review Rubric

Ask these questions after every meaningful UI change:

- Can a first-time user tell where to start in 5 seconds?
- Can a returning user get to today's useful games quickly?
- Are controls grouped by the user's decision, not by implementation detail?
- Are long team names, odds labels, and table cells readable on mobile?
- Does the page still work when data or artifacts are missing?
- Does selecting a game carry the correct `event_id` into detail and model pages?
- Are API calls and dataframe renders acceptable for repeated use?