# NCAAB Sportsbook Metrics Roadmap

## Goal
Turn the current dashboard into an NCAAB-first betting research tool with fast game discovery, team-vs-team context, and market-quality signals.

## Implemented In This Iteration
- Game discovery now supports grouped next-7-day view in collapsible day sections with empty days hidden.
- Team History now includes a Team Matchup Lab for team-vs-team comparison and H2H summary.

## Tier 1 Metrics (Always Visible)
- Team season record (SU), ATS record, O/U record.
- Last 10 SU, ATS, O/U form.
- Home/Away split record and ATS.
- Favorite/Underdog ATS split.
- H2H SU/ATS/O-U between selected teams.

## Tier 2 Metrics (Market Context)
- Open vs Close spread/total/ML with signed movement.
- Early vs late movement profile (OPEN->T60, T60->T30, T30->CLOSE).
- Volatility indicators: number of price changes, implied-probability stdev.
- Public splits: bets %, handle %, and divergence (handle-bets).

## Tier 3 Metrics (Model + Edge)
- Market implied vs model implied probability.
- Residual (model - market) and EV per side.
- CLV at decision timestamp (OPEN/T60/T30).
- Confidence and calibration summaries by market.

## Tier 4 Metrics (Strength + Schedule)
- KenPom: AdjO, AdjD, AdjEM, Tempo, SoS.
- KenPom expected spread vs market spread deviation.
- AP ranking context and ranked-vs-unranked indicators.
- Remaining schedule strength for next 5 games.
- Rest differential (days since last game).

## Data Gaps To Close
- Player-level metrics (usage/injury/rotation) are not currently ingested.
- Team box-score factors (eFG%, TO%, ORB%, FT rate) are not currently stored.

## Next Build Queue
1. Add API endpoint for direct H2H history and common-opponent comparison.
2. Add Team Comparison page with side-by-side cards and matchup radar chart.
3. Add NCAAB-only season explorer page with sortable tables for all teams.
4. Add schedule-strength and rest differential features to game cards.
5. Add box-score ingestion from ESPN completed-game feeds (team level first).
