"""
CLI entrypoint - run with: python -m dk_ncaab <command>

Commands:
  collect-odds      Run one quota-gated odds collection cycle
  collect-event-odds Collect bounded event-specific markets such as team totals and props
  collect-mlb-stats Collect MLB Stats API team/player logs (no odds quota)
  backfill-mlb-current-season Backfill MLB Stats API logs in bounded windows
  mlb-data-inventory Report local MLB data ranges, counts, and join gaps
  mlb-evidence-growth-log Append local MLB evidence growth/readiness snapshot
  import-mlb-player-ids Import Chadwick-style MLB player ID crosswalk CSV
  import-mlb-statcast-daily Import Baseball Savant/Statcast CSV daily features
  backfill-mlb-statcast-daily Download/import bounded Baseball Savant daily features
  collect-mlb-environment Collect MLB weather/wind context (no odds quota)
  collect-splits    Run one splits scrape cycle
  collect-results   Run one results collection cycle (1 API request)
  load-games        Load games from ESPN for a date (FREE)
  update-results    Update all pending games with scores from ESPN (FREE)
  backfill          Backfill N days of games + scores from ESPN (FREE)
  reconcile-mlb-events Dry-run or apply one-time MLB duplicate-event cleanup
  reconcile-event-odds-identities Dry-run or fill missing event-specific odds participant IDs
  pipeline          Full daily pipeline: load->odds->update-results->build
  auto              Smart auto-collector daemon (budget-aware)
  import-kenpom     Import KenPom ratings from CSV (§4)
  import-ap         Import AP rankings from CSV (§5)
  export-mlb-venue-metadata-template Export fillable MLB venue metadata CSV
  import-mlb-venue-metadata Import reviewed MLB venue metadata from CSV
  import-mlb-park-factors Import reviewed MLB park factors from CSV
  build-dataset     Build features and export to Parquet
  export-mlb-market-history Export settlement-aware MLB event-market history parquet
  train             Train prediction models on collected data
  report            Generate correlation report
  build-oof         Build local out-of-fold close-proxy artifacts
  oof-entry-ev      Build strict OOF entry-EV artifacts
  mlb-daily-research-cycle Print the bounded MLB dashboard refresh workflow
  backtest          Run backtest suite on historical data (§11)
  predict           Score upcoming games with trained models (§10.3)
  models            List saved trained models
  seed-teams        Seed the team_aliases table from the curated list
  scheduler         Start the legacy APScheduler daemon
  db-init           Create all tables directly (no Alembic)
"""

from __future__ import annotations

import argparse
import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
log = logging.getLogger("dk_ncaab")


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="dk_ncaab",
        description="DraftKings NCAAB odds & splits research pipeline",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # ── Data collectors ─────────────────────────────────────────
    sub.add_parser("collect-odds", help="Run one quota-gated odds collection cycle")
    event_odds = sub.add_parser(
        "collect-event-odds",
        help="Collect bounded event-specific odds such as team totals and player props",
    )
    event_odds.add_argument("--sport", default="baseball_mlb")
    event_odds.add_argument("--max-events", type=int, default=1)
    event_odds.add_argument("--lookahead-hours", type=int, default=24)
    event_odds.add_argument("--stale-after-minutes", type=int, default=180)
    event_odds.add_argument(
        "--markets",
        help="Optional comma-separated provider market keys; defaults to the sport registry subset",
    )
    mlb = sub.add_parser(
        "collect-mlb-stats",
        help="Collect MLB Stats API team/player logs (no odds quota)",
    )
    mlb.add_argument("--start-date", help="Start date YYYY-MM-DD, defaults to today")
    mlb.add_argument("--end-date", help="End date YYYY-MM-DD")
    mlb.add_argument("--days", type=int, default=1, help="Window length if --end-date omitted")
    mlb.add_argument("--max-boxscores", type=int, help="Maximum boxscores to fetch this run")
    mlb.add_argument("--request-delay-sec", type=float, help="Delay between boxscore requests")
    mlb.add_argument(
        "--include-unfinal",
        action="store_true",
        help="Fetch boxscores for non-final games too",
    )
    mlb.add_argument(
        "--refetch-existing-boxscores",
        action="store_true",
        help="Fetch boxscores even when local team/player logs already exist",
    )
    mlb_backfill = sub.add_parser(
        "backfill-mlb-current-season",
        help="Backfill MLB Stats API logs in bounded current-season windows",
    )
    mlb_backfill.add_argument("--start-date", help="Start date YYYY-MM-DD, defaults to Apr 1")
    mlb_backfill.add_argument("--end-date", help="End date YYYY-MM-DD, defaults to today")
    mlb_backfill.add_argument("--window-days", type=int, default=3)
    mlb_backfill.add_argument("--max-boxscores-per-window", type=int)
    mlb_backfill.add_argument("--request-delay-sec", type=float)
    mlb_backfill.add_argument(
        "--include-unfinal",
        action="store_true",
        help="Fetch boxscores for non-final games too",
    )
    mlb_backfill.add_argument(
        "--refetch-existing-boxscores",
        action="store_true",
        help="Fetch boxscores even when local team/player logs already exist",
    )
    mlb_backfill.add_argument("--dry-run", action="store_true")
    inventory = sub.add_parser(
        "mlb-data-inventory",
        help="Report local MLB date ranges, counts, line history, and join gaps",
    )
    inventory.add_argument(
        "--out-dir",
        default="artifacts/inventory",
        help="Directory for mlb_data_inventory.json; use --no-write to skip",
    )
    inventory.add_argument("--no-write", action="store_true")
    growth = sub.add_parser(
        "mlb-evidence-growth-log",
        help="Append local MLB evidence growth and next-action snapshot",
    )
    growth.add_argument(
        "--out-dir",
        default="artifacts/evidence_growth",
        help="Directory for MLB evidence growth artifacts",
    )
    growth.add_argument("--label", help="Optional operator label for this snapshot")
    growth.add_argument("--no-write", action="store_true")
    mlb_env = sub.add_parser(
        "collect-mlb-environment",
        help="Collect MLB NWS weather/wind snapshots (no odds quota)",
    )
    mlb_env.add_argument("--max-events", type=int, help="Maximum upcoming events to inspect")
    mlb_env.add_argument("--request-delay-sec", type=float, help="Delay between NWS requests")
    sub.add_parser("collect-splits", help="Run one splits scrape cycle")
    sub.add_parser(
        "collect-results",
        help="Fetch scores from Odds API (1 API req) - prefer update-results",
    )

    # ── ESPN-based (FREE, unlimited) ────────────────────────────
    lg = sub.add_parser("load-games", help="Load games from ESPN (FREE)")
    lg.add_argument("--date", help="Date YYYY-MM-DD, defaults to today")
    lg.add_argument("--sport", help="Optional sport key, e.g. baseball_mlb")

    ur = sub.add_parser(
        "update-results",
        help="Update pending games with ESPN scores (FREE, no API cost)",
    )
    ur.add_argument("--sport", help="Optional sport key, e.g. baseball_mlb")

    bf = sub.add_parser("backfill", help="Backfill N days from ESPN (FREE)")
    bf.add_argument(
        "--days", type=int, default=60,
        help="Number of days to backfill (default 60)",
    )
    bf.add_argument("--sport", help="Optional sport key, e.g. baseball_mlb")

    reconcile = sub.add_parser(
        "reconcile-mlb-events",
        help="Dry-run or apply one-time MLB duplicate-event cleanup",
    )
    reconcile.add_argument(
        "--apply",
        action="store_true",
        help="Apply the reconciliation instead of reporting the plan only",
    )
    reconcile.add_argument(
        "--max-start-diff-minutes",
        type=int,
        default=90,
        help="Maximum start-time gap between duplicate candidates (default 90)",
    )
    reconcile.add_argument(
        "--limit",
        type=int,
        help="Optional limit on candidate groups to inspect/apply",
    )
    event_identity = sub.add_parser(
        "reconcile-event-odds-identities",
        help="Dry-run or fill missing event-specific odds participant IDs",
    )
    event_identity.add_argument("--sport", default="baseball_mlb")
    event_identity.add_argument("--limit", type=int)
    event_identity.add_argument(
        "--apply",
        action="store_true",
        help="Apply source-backed team/player links instead of reporting only",
    )

    # ── Pipeline (combines multiple steps) ──────────────────────
    pp = sub.add_parser(
        "pipeline",
        help="Full cycle: load-games -> collect-odds -> update-results -> build-dataset",
    )
    pp.add_argument(
        "--skip-odds", action="store_true",
        help="Skip the Odds API call (saves quota)",
    )

    # ── KenPom / AP imports ─────────────────────────────────────
    kp = sub.add_parser("import-kenpom", help="Import KenPom ratings from CSV")
    kp.add_argument("csv_path", help="Path to KenPom CSV file")
    kp.add_argument("--date", help="Rating date YYYY-MM-DD, defaults to today")

    ap = sub.add_parser("import-ap", help="Import AP rankings from CSV")
    ap.add_argument("csv_path", help="Path to AP rankings CSV file")
    ap.add_argument("--date", help="Poll date YYYY-MM-DD, defaults to today")

    venue_template = sub.add_parser(
        "export-mlb-venue-metadata-template",
        help="Export fillable MLB venue metadata CSV",
    )
    venue_template.add_argument("csv_path", help="Path to write venue metadata CSV")
    venue_import = sub.add_parser(
        "import-mlb-venue-metadata",
        help="Import reviewed MLB venue metadata from CSV",
    )
    venue_import.add_argument("csv_path", help="Path to venue metadata CSV file")
    player_ids = sub.add_parser(
        "import-mlb-player-ids",
        help="Import Chadwick-style MLB player ID crosswalk CSV",
    )
    player_ids.add_argument("csv_path", help="Path to player ID crosswalk CSV")
    player_ids.add_argument("--source", default="chadwick_register_csv")
    statcast_daily = sub.add_parser(
        "import-mlb-statcast-daily",
        help="Import Baseball Savant/Statcast CSV as daily player features",
    )
    statcast_daily.add_argument("csv_path", help="Path to Statcast CSV export")
    statcast_daily.add_argument("--source", default="baseball_savant_csv")
    statcast_daily.add_argument("--source-url", help="Optional source URL/reference")
    statcast_backfill = sub.add_parser(
        "backfill-mlb-statcast-daily",
        help="Download/import Baseball Savant Statcast CSVs in bounded windows",
    )
    statcast_backfill.add_argument("--start-date", required=True, help="Start date YYYY-MM-DD")
    statcast_backfill.add_argument("--end-date", required=True, help="End date YYYY-MM-DD")
    statcast_backfill.add_argument("--window-days", type=int, default=1)
    statcast_backfill.add_argument(
        "--out-dir",
        default="artifacts/raw/mlb/statcast",
        help="Raw CSV download directory",
    )
    statcast_backfill.add_argument("--request-delay-sec", type=float)
    statcast_backfill.add_argument(
        "--refetch-existing-downloads",
        action="store_true",
        help="Download again even when a raw CSV already exists",
    )
    statcast_backfill.add_argument("--dry-run", action="store_true")

    # ── Analysis ────────────────────────────────────────────────
    pf = sub.add_parser("import-mlb-park-factors", help="Import reviewed MLB park factors from CSV")
    pf.add_argument("csv_path", help="Path to park-factor CSV file")
    pf.add_argument("--source", default="manual_csv", help="Source label for imported rows")
    pf.add_argument("--source-url", help="Optional source URL/reference for imported rows")

    sub.add_parser("build-dataset", help="Build features -> Parquet")
    export_mlb_market_history = sub.add_parser(
        "export-mlb-market-history",
        help="Export settlement-aware MLB team-total and prop history to parquet",
    )
    export_mlb_market_history.add_argument(
        "--markets",
        help="Optional comma-separated MLB event-market keys to export",
    )
    export_mlb_market_history.add_argument(
        "--event-limit",
        type=int,
        help="Optional limit on most-recent quoted final MLB events to export",
    )
    export_mlb_market_history.add_argument(
        "--out-dir",
        default="artifacts/market_history/mlb",
        help="Artifact output directory",
    )
    sub.add_parser("train", help="Train prediction models on collected data")
    sub.add_parser("report", help="Generate correlation report")
    oof = sub.add_parser("build-oof", help="Build local OOF close-proxy artifacts")
    oof.add_argument(
        "--source",
        choices=["auto", "db", "latest-parquet"],
        default="auto",
        help="Feature source: DB first, DB only, or latest parquet",
    )
    oof.add_argument("--min-train-size", type=int, default=60)
    oof.add_argument("--min-predictions", type=int, default=20)
    ev_oof = sub.add_parser("oof-entry-ev", help="Build strict OOF entry-EV artifacts")
    ev_oof.add_argument("--input-parquet", help="Feature parquet path")
    ev_oof.add_argument("--from-db", action="store_true", help="Build features from local DB")
    ev_oof.add_argument("--anchor", choices=["OPEN", "T60", "T30"], default="T60")
    ev_oof.add_argument("--sport", default="basketball_ncaab")
    ev_oof.add_argument("--out-dir", default="artifacts/entry_ev/oof")
    ev_oof.add_argument("--min-train-size", type=int, default=60)
    ev_oof.add_argument("--ev-threshold-units", type=float, default=0.0)
    mlb_daily = sub.add_parser(
        "mlb-daily-research-cycle",
        help="Print a bounded MLB dashboard refresh workflow",
    )
    mlb_daily.add_argument("--date", help="Slate date YYYY-MM-DD, defaults to today")
    mlb_daily.add_argument("--settled-start-date", help="Recent final-game backfill start date")
    mlb_daily.add_argument("--settled-end-date", help="Recent final-game backfill end date")
    mlb_daily.add_argument("--statcast-start-date", help="Statcast backfill start date")
    mlb_daily.add_argument("--statcast-end-date", help="Statcast backfill end date")
    mlb_daily.add_argument("--event-odds-max-events", type=int, default=1)
    mlb_daily.add_argument(
        "--include-event-odds",
        action="store_true",
        help="Include the explicit Odds API event-odds step in the printed workflow",
    )
    sub.add_parser("backtest", help="Run backtest suite on collected data (§11)")
    sub.add_parser("predict", help="Score upcoming games with trained models")
    sub.add_parser("models", help="List saved trained models")

    # ── Automation ──────────────────────────────────────────────
    auto = sub.add_parser(
        "auto",
        help="Smart auto-collector: budget-aware odds + free ESPN on a schedule",
    )
    auto.add_argument(
        "--budget", type=int, default=450,
        help="Monthly Odds API call cap (default 450, reserves 50 for manual)",
    )
    auto.add_argument(
        "--once", action="store_true",
        help="Run one smart cycle and exit (good for Task Scheduler)",
    )

    # ── Infrastructure ──────────────────────────────────────────
    sub.add_parser("seed-teams", help="Seed NCAAB team aliases")
    sub.add_parser("scheduler", help="Start the legacy job scheduler")
    sub.add_parser("db-init", help="Create all tables (dev shortcut)")

    # ── Status ──────────────────────────────────────────────────
    sub.add_parser("status", help="Show DB counts + API budget")

    args = parser.parse_args()

    # ── Dispatch ────────────────────────────────────────────────

    if args.command == "collect-odds":
        from dk_ncaab.collectors.odds_api import collect_odds
        collect_odds()

    elif args.command == "collect-event-odds":
        from dk_ncaab.collectors.odds_event_markets import collect_event_odds_markets

        markets = None
        if args.markets:
            markets = [part.strip() for part in args.markets.split(",") if part.strip()]
        result = collect_event_odds_markets(
            sport_key=args.sport,
            max_events=args.max_events,
            lookahead_hours=args.lookahead_hours,
            stale_after_minutes=args.stale_after_minutes,
            markets=markets,
        )
        print(result)

    elif args.command == "collect-mlb-stats":
        from datetime import datetime as _dt
        from dk_ncaab.collectors.mlb_stats import collect_mlb_stats

        start = _dt.strptime(args.start_date, "%Y-%m-%d").date() if args.start_date else None
        end = _dt.strptime(args.end_date, "%Y-%m-%d").date() if args.end_date else None
        result = collect_mlb_stats(
            start_date=start,
            end_date=end,
            days=args.days,
            final_only=not args.include_unfinal,
            max_boxscores=args.max_boxscores,
            request_delay_sec=args.request_delay_sec,
            skip_existing_boxscores=not args.refetch_existing_boxscores,
        )
        print(result)

    elif args.command == "backfill-mlb-current-season":
        from datetime import datetime as _dt
        from dk_ncaab.collectors.mlb_backfill import backfill_current_mlb_stats

        start = _dt.strptime(args.start_date, "%Y-%m-%d").date() if args.start_date else None
        end = _dt.strptime(args.end_date, "%Y-%m-%d").date() if args.end_date else None
        result = backfill_current_mlb_stats(
            start_date=start,
            end_date=end,
            window_days=args.window_days,
            max_boxscores_per_window=args.max_boxscores_per_window,
            request_delay_sec=args.request_delay_sec,
            final_only=not args.include_unfinal,
            skip_existing_boxscores=not args.refetch_existing_boxscores,
            dry_run=args.dry_run,
        )
        for window in result.windows:
            print(window)
        print(f"totals={result.totals}")

    elif args.command == "mlb-data-inventory":
        from dk_ncaab.analysis.mlb_inventory import build_mlb_data_inventory

        result = build_mlb_data_inventory(out_dir=None if args.no_write else args.out_dir)
        print(f"MLB data inventory: {result.json_path or 'not written'}")
        for section, values in result.summary.items():
            if isinstance(values, dict):
                print(f"{section}: {values}")
            else:
                print(f"{section}: {values}")

    elif args.command == "mlb-evidence-growth-log":
        from dk_ncaab.analysis.mlb_evidence_growth import build_mlb_evidence_growth_log

        result = build_mlb_evidence_growth_log(
            out_dir=args.out_dir,
            label=args.label,
            write=not args.no_write,
        )
        print(f"MLB evidence growth: {result.get('latest_path') or 'not written'}")
        print(f"generated_at_utc: {result['generated_at_utc']}")
        print(f"previous_generated_at_utc: {result.get('previous_generated_at_utc') or '-'}")
        print(f"summary: {result['summary']}")
        for row in result.get("priority_markets", []):
            print(
                f"priority {row['priority_score']}: {row['market']} -> "
                f"{row['next_action_label']} ({row['next_action_reason']})"
            )

    elif args.command == "collect-mlb-environment":
        from dk_ncaab.collectors.mlb_environment import collect_mlb_environment

        result = collect_mlb_environment(
            max_events=args.max_events,
            request_delay_sec=args.request_delay_sec,
        )
        print(result)

    elif args.command == "collect-splits":
        from dk_ncaab.collectors.splits_dknetwork import collect_splits
        collect_splits()

    elif args.command == "collect-results":
        from dk_ncaab.collectors.results import collect_results
        collect_results()

    elif args.command == "load-games":
        from dk_ncaab.collectors.load_games import load_games_for_date
        target = None
        if args.date:
            from datetime import datetime as _dt, timezone as _tz
            target = _dt.strptime(args.date, "%Y-%m-%d").replace(tzinfo=_tz.utc)
        load_games_for_date(target, sport=args.sport)

    elif args.command == "update-results":
        from dk_ncaab.collectors.load_games import update_scores_espn
        update_scores_espn(sport=args.sport)

    elif args.command == "backfill":
        from dk_ncaab.collectors.load_games import backfill_espn
        backfill_espn(days=args.days, sport=args.sport)

    elif args.command == "reconcile-mlb-events":
        from dk_ncaab.db.event_reconciliation import reconcile_mlb_event_identity

        result = reconcile_mlb_event_identity(
            apply=args.apply,
            max_start_diff_minutes=args.max_start_diff_minutes,
            limit=args.limit,
        )
        print(result)

    elif args.command == "reconcile-event-odds-identities":
        from dk_ncaab.collectors.event_odds_identity import reconcile_event_odds_identities

        result = reconcile_event_odds_identities(
            sport_key=args.sport,
            apply=args.apply,
            limit=args.limit,
        )
        print(
            "Event odds identity reconciliation: "
            f"scanned={result.scanned} resolvable={result.resolvable} "
            f"updated={result.updated} unresolved={result.unresolved}"
        )
        for row in result.resolutions:
            target = row.resolved_player_name or row.resolved_player_id or row.resolved_team_id
            print(
                f"quote_id={row.quote_id} event_id={row.event_id} market={row.market_key} "
                f"participant={row.participant_name!r} -> {target} "
                f"method={row.method} applied={row.applied}"
            )

    elif args.command == "pipeline":
        _run_pipeline(skip_odds=args.skip_odds)

    elif args.command == "import-kenpom":
        from datetime import datetime
        from dk_ncaab.collectors.kenpom import import_kenpom_csv
        date = datetime.strptime(args.date, "%Y-%m-%d") if args.date else None
        count = import_kenpom_csv(args.csv_path, rating_date=date)
        log.info("Imported %d KenPom ratings", count)

    elif args.command == "import-ap":
        from datetime import datetime
        from dk_ncaab.collectors.ap_rankings import import_ap_csv
        date = datetime.strptime(args.date, "%Y-%m-%d") if args.date else None
        count = import_ap_csv(args.csv_path, poll_date=date)
        log.info("Imported %d AP rankings", count)

    elif args.command == "export-mlb-venue-metadata-template":
        from dk_ncaab.collectors.mlb_venue_metadata import (
            export_mlb_venue_metadata_template_csv,
        )

        result = export_mlb_venue_metadata_template_csv(args.csv_path)
        print(result)

    elif args.command == "import-mlb-venue-metadata":
        from dk_ncaab.collectors.mlb_venue_metadata import import_mlb_venue_metadata_csv

        result = import_mlb_venue_metadata_csv(args.csv_path)
        print(result)

    elif args.command == "import-mlb-player-ids":
        from dk_ncaab.collectors.mlb_identity import import_chadwick_player_ids_csv

        result = import_chadwick_player_ids_csv(args.csv_path, source=args.source)
        print(result)

    elif args.command == "import-mlb-statcast-daily":
        from dk_ncaab.collectors.mlb_statcast import import_statcast_daily_csv

        result = import_statcast_daily_csv(
            args.csv_path,
            source=args.source,
            source_url=args.source_url,
        )
        print(result)

    elif args.command == "backfill-mlb-statcast-daily":
        from datetime import datetime as _dt
        from dk_ncaab.collectors.mlb_statcast import backfill_statcast_daily

        result = backfill_statcast_daily(
            start_date=_dt.strptime(args.start_date, "%Y-%m-%d").date(),
            end_date=_dt.strptime(args.end_date, "%Y-%m-%d").date(),
            window_days=args.window_days,
            out_dir=args.out_dir,
            request_delay_sec=args.request_delay_sec,
            skip_existing_downloads=not args.refetch_existing_downloads,
            dry_run=args.dry_run,
        )
        for window in result.windows:
            print(window)
        print(f"totals={result.totals}")

    elif args.command == "import-mlb-park-factors":
        from dk_ncaab.collectors.mlb_park_factors import import_mlb_park_factors_csv

        result = import_mlb_park_factors_csv(
            args.csv_path,
            default_source=args.source,
            default_source_url=args.source_url,
        )
        print(result)

    elif args.command == "build-dataset":
        from dk_ncaab.analysis.dataset_build import run_dataset_build
        run_dataset_build()

    elif args.command == "export-mlb-market-history":
        from dk_ncaab.analysis.mlb_market_history import generate_mlb_market_history_artifact

        markets = None
        if args.markets:
            markets = [part.strip() for part in args.markets.split(",") if part.strip()]
        try:
            result = generate_mlb_market_history_artifact(
                market_keys=markets,
                event_limit=args.event_limit,
                out_dir=args.out_dir,
            )
        except ValueError as exc:
            log.error("MLB market-history artifact was not created: %s", exc)
            sys.exit(1)
        print(f"MLB market-history artifact: {result.run_dir}")
        print(f"  parquet: {result.parquet_path}")
        print(f"  rows_exported: {result.summary['rows_exported']}")
        print(f"  events_exported: {result.summary['events_exported']}")
        for market_key, count in result.summary.get("rows_by_market", {}).items():
            print(f"  {market_key}: {count}")

    elif args.command == "train":
        _run_train()

    elif args.command == "report":
        from dk_ncaab.analysis.dataset_build import build_dataset
        from dk_ncaab.analysis.correlation_report import generate_report
        df = build_dataset()
        if df.empty:
            log.error("No data — run collectors first")
            sys.exit(1)
        generate_report(df)

    elif args.command == "build-oof":
        from dk_ncaab.analysis.oof_artifacts import generate_oof_artifacts

        summary = generate_oof_artifacts(
            source=args.source,
            min_train_size=args.min_train_size,
            min_predictions=args.min_predictions,
        )
        print(f"OOF artifact summary: rows={summary.rows} events={summary.events}")
        for anchor in summary.anchors:
            print(
                f"  {anchor.anchor}: predictions={anchor.rows_with_prediction} "
                f"bets={anchor.n_bets} roi={anchor.total_roi:+.2%}"
            )
            for warning in anchor.warnings:
                print(f"    warning: {warning}")
        for warning in summary.warnings:
            print(f"  warning: {warning}")

    elif args.command == "oof-entry-ev":
        from dk_ncaab.analysis.oof_entry_ev import generate_oof_entry_ev

        try:
            result = generate_oof_entry_ev(
                input_parquet=args.input_parquet,
                from_db=args.from_db,
                anchor=args.anchor,
                sport=args.sport,
                out_dir=args.out_dir,
                min_train_size=args.min_train_size,
                ev_threshold_units=args.ev_threshold_units,
            )
        except ValueError as exc:
            log.error("OOF entry-EV artifact was not created: %s", exc)
            sys.exit(1)
        print(f"OOF entry-EV artifact: {result.run_dir}")
        print(f"  predictions: {result.predictions_path}")
        print(f"  rows_predicted: {result.summary['rows_predicted']}")
        print(f"  recommended_count: {result.summary['recommended_count']}")
        for warning in result.summary.get("warnings", []):
            print(f"  warning: {warning}")

    elif args.command == "mlb-daily-research-cycle":
        from datetime import date as _date
        from datetime import datetime as _dt

        from dk_ncaab.collectors.mlb_daily_workflow import (
            build_mlb_daily_research_steps,
            format_mlb_daily_research_steps,
        )

        def parse_date(value: str | None) -> _date | None:
            return _dt.strptime(value, "%Y-%m-%d").date() if value else None

        steps = build_mlb_daily_research_steps(
            slate_date=parse_date(args.date) or _date.today(),
            settled_start_date=parse_date(args.settled_start_date),
            settled_end_date=parse_date(args.settled_end_date),
            statcast_start_date=parse_date(args.statcast_start_date),
            statcast_end_date=parse_date(args.statcast_end_date),
            event_odds_max_events=args.event_odds_max_events,
            include_event_odds=args.include_event_odds,
        )
        print(format_mlb_daily_research_steps(steps))

    elif args.command == "backtest":
        _run_backtest()

    elif args.command == "predict":
        _run_predict()

    elif args.command == "models":
        _list_models()

    elif args.command == "auto":
        from dk_ncaab.jobs.auto_collect import main as auto_main
        auto_main(monthly_budget=args.budget, once=args.once)

    elif args.command == "seed-teams":
        from dk_ncaab.db.seed_teams import seed_teams
        seed_teams()

    elif args.command == "scheduler":
        from dk_ncaab.jobs.scheduler import main as sched_main
        sched_main()

    elif args.command == "db-init":
        from dk_ncaab.db.models import Base
        from dk_ncaab.db.session import get_engine
        engine = get_engine()
        Base.metadata.create_all(engine)
        log.info("All tables created.")

    elif args.command == "status":
        _show_status()


# ── Pipeline ────────────────────────────────────────────────────

def _run_pipeline(skip_odds: bool = False) -> None:
    """
    Full daily pipeline:
      1. load-games (ESPN, free) — seed today's games
      2. update-results (ESPN, free) — close out finished games w/ scores
      3. collect-odds (Odds API, quota-gated) — get DK lines for live games
      4. build-dataset — export features to Parquet
    """
    log.info("═" * 60)
    log.info("Starting daily pipeline")
    log.info("═" * 60)

    # Step 1: Load today's games from ESPN (FREE)
    log.info("── Step 1/4: Loading today's games from ESPN (free) ──")
    from dk_ncaab.collectors.load_games import load_games_for_date
    load_games_for_date()

    # Step 2: Update scores for any completed games (FREE)
    log.info("── Step 2/4: Updating scores from ESPN (free) ──")
    from dk_ncaab.collectors.load_games import update_scores_espn
    update_scores_espn()

    # Step 3: Collect odds from Odds API (quota-gated)
    if skip_odds:
        log.info("── Step 3/4: SKIPPED (--skip-odds) ──")
    else:
        log.info("── Step 3/4: Collecting DK odds (quota-gated) ──")
        from dk_ncaab.collectors.odds_api import collect_odds
        collect_odds()

    # Step 4: Build dataset
    log.info("── Step 4/4: Building dataset ──")
    from dk_ncaab.analysis.dataset_build import run_dataset_build
    run_dataset_build()

    log.info("═" * 60)
    log.info("Pipeline complete!")
    log.info("═" * 60)


# ── Train ───────────────────────────────────────────────────────

def _run_train() -> None:
    """
    Build dataset and train all model tiers:
      1. Ridge baseline (closing-line prediction)
      2. LightGBM (closing-line prediction)
      3. Outcome model — LogReg (spread cover)
      4. Outcome model — LightGBM (spread cover)
    All trained models are persisted to artifacts/models/.
    """
    from dk_ncaab.analysis.dataset_build import build_dataset
    from dk_ncaab.analysis.models_close_predict import (
        train_ridge, train_lgbm, DEFAULT_FEATURES,
    )
    from dk_ncaab.analysis.model_store import save_model

    log.info("═" * 60)
    log.info("Building dataset for training …")
    log.info("═" * 60)

    df = build_dataset()
    if df.empty:
        log.error("No data to train on — run backfill + pipeline first")
        sys.exit(1)

    log.info("Dataset: %d rows × %d columns", len(df), len(df.columns))

    # Check we have enough completed events with results
    n_with_close = df["implied_CLOSE"].notna().sum() if "implied_CLOSE" in df.columns else 0
    log.info("Rows with CLOSE data: %d", n_with_close)

    if n_with_close < 50:
        log.warning(
            "Only %d rows with CLOSE data. Need ~100+ for meaningful "
            "training. Keep collecting odds data over the next few days!",
            n_with_close,
        )
        log.info(
            "TIP: The model needs multiple odds snapshots per event "
            "(OPEN, T60, T30, CLOSE) — collect odds a few times per day."
        )

    # Train what we can
    try:
        log.info("── Training Ridge baseline ──")
        ridge_result = train_ridge(df)
        log.info("  %s", ridge_result.summary())
        # Save Ridge model (re-train on full data for persistence)
        if ridge_result.folds:
            from sklearn.linear_model import Ridge as _Ridge
            from sklearn.preprocessing import StandardScaler as _SS
            feats = [c for c in DEFAULT_FEATURES if c in df.columns]
            sub = df[feats + ["implied_CLOSE"]].dropna()
            if len(sub) >= 20:
                sc = _SS()
                X = sc.fit_transform(sub[feats])
                m = _Ridge(alpha=1.0).fit(X, sub["implied_CLOSE"])
                save_model(m, "ridge_close", feats,
                           {"r2": ridge_result.mean_r2, "rmse": ridge_result.mean_rmse},
                           scaler=sc)
    except Exception as e:
        log.warning("Ridge training failed (not enough data yet): %s", e)

    try:
        log.info("── Training LightGBM ──")
        lgbm_result = train_lgbm(df)
        log.info("  %s", lgbm_result.summary())
        # Save LightGBM (re-train on full data)
        if lgbm_result.folds:
            import lightgbm as lgb
            feats = [c for c in DEFAULT_FEATURES if c in df.columns]
            sub = df[feats + ["implied_CLOSE"]].dropna()
            if len(sub) >= 50:
                m = lgb.LGBMRegressor(
                    n_estimators=500, learning_rate=0.05,
                    num_leaves=31, verbose=-1,
                )
                m.fit(sub[feats], sub["implied_CLOSE"])
                save_model(m, "lgbm_close", feats,
                           {"r2": lgbm_result.mean_r2, "rmse": lgbm_result.mean_rmse})
    except Exception as e:
        log.warning("LightGBM training failed (not enough data yet): %s", e)

    try:
        from dk_ncaab.analysis.models_outcome import (
            train_outcome_model, train_outcome_lgbm, OUTCOME_FEATURES,
        )
        log.info("── Training Outcome model — LogReg (spread cover) ──")
        outcome = train_outcome_model(df)
        log.info("  %s", outcome.summary())
    except Exception as e:
        log.warning("Outcome LogReg training failed (not enough data yet): %s", e)

    try:
        from dk_ncaab.analysis.models_outcome import (
            train_outcome_lgbm, OUTCOME_FEATURES,
        )
        log.info("── Training Outcome model — LightGBM (spread cover) ──")
        outcome_lgbm = train_outcome_lgbm(df)
        log.info("  %s", outcome_lgbm.summary())
        # Save outcome LGBM
        if outcome_lgbm.folds:
            import lightgbm as lgb
            feats = [c for c in OUTCOME_FEATURES if c in df.columns]
            sub = df[feats + ["spread_cover"]].dropna()
            if len(sub) >= 100:
                m = lgb.LGBMClassifier(
                    n_estimators=300, learning_rate=0.05,
                    num_leaves=31, verbose=-1,
                )
                m.fit(sub[feats], sub["spread_cover"])
                save_model(m, "lgbm_outcome", feats,
                           {"auc": outcome_lgbm.mean_auc,
                            "accuracy": outcome_lgbm.mean_accuracy})
    except Exception as e:
        log.warning("Outcome LightGBM training failed (not enough data yet): %s", e)

    log.info("═" * 60)
    log.info("Training complete!")
    log.info("═" * 60)


# ── Backtest ────────────────────────────────────────────────────

def _run_backtest() -> None:
    """
    Run the full backtest suite on historical data (§11).
    Measures CLV, ROI, drawdown, and Sharpe at each entry anchor.
    Optionally runs model-driven strategy if a trained model exists.
    """
    from dk_ncaab.analysis.dataset_build import build_dataset
    from dk_ncaab.analysis.backtest import run_backtest_suite, backtest_model_clv

    log.info("═" * 60)
    log.info("Building dataset for backtest …")
    log.info("═" * 60)

    df = build_dataset()
    if df.empty:
        log.error("No data — run collectors + pipeline first")
        sys.exit(1)

    log.info("Dataset: %d rows", len(df))

    # Run baseline strategies
    results = run_backtest_suite(df)

    # Try model-driven backtest if a trained close-prediction model exists
    try:
        from dk_ncaab.analysis.model_store import get_latest_model, load_model
        import pandas as pd

        model_path = get_latest_model("lgbm_close")
        if model_path:
            bundle = load_model(model_path)
            model = bundle["model"]
            feats = bundle["features"]
            cols = [c for c in feats if c in df.columns]
            sub = df.dropna(subset=cols + ["implied_CLOSE"])
            if len(sub) >= 20:
                predicted = pd.Series(
                    model.predict(sub[cols]),
                    index=sub.index,
                )
                for anchor in ("T60", "T30"):
                    r = backtest_model_clv(sub, predicted, anchor=anchor, clv_threshold=0.01)
                    log.info(r.summary())
                    results.append(r)
        else:
            log.info("No trained close-prediction model found — skipping model backtest")
            log.info("Run 'python -m dk_ncaab train' first to enable model-driven backtest")
    except Exception as e:
        log.warning("Model backtest skipped: %s", e)

    log.info("═" * 60)
    log.info("Backtest complete — %d strategies evaluated", len(results))
    log.info("═" * 60)


# ── Predict / Signal ────────────────────────────────────────────

def _run_predict() -> None:
    """
    Score upcoming games with trained models.
    Identifies mispricings and prints actionable signals (§10.3).
    """
    from dk_ncaab.analysis.model_store import get_latest_model, load_model
    from dk_ncaab.analysis.dataset_build import build_dataset
    from dk_ncaab.analysis.models_outcome import detect_mispricings
    from dk_ncaab.db.session import SessionLocal
    from dk_ncaab.db.models import Event, Team
    import pandas as pd

    # Find the best close-prediction model
    model_path = get_latest_model("lgbm_close")
    if not model_path:
        model_path = get_latest_model("ridge_close")
    if not model_path:
        log.error("No trained model found — run 'python -m dk_ncaab train' first")
        sys.exit(1)

    bundle = load_model(model_path)
    model = bundle["model"]
    feats = bundle["features"]
    scaler = bundle.get("scaler")

    log.info("Using model: %s", model_path.name)

    # Build features for upcoming/live events (not just final)
    with SessionLocal() as session:
        from sqlalchemy import select
        upcoming_ids = [
            r[0] for r in session.execute(
                select(Event.id).where(Event.status.in_(["upcoming", "live"]))
            )
        ]

    if not upcoming_ids:
        log.info("No upcoming games to score")
        return

    log.info("Scoring %d upcoming events", len(upcoming_ids))
    df = build_dataset(event_ids=upcoming_ids)
    if df.empty:
        log.info("No feature data for upcoming games (need odds snapshots)")
        return

    # Predict closing implied probability
    cols = [c for c in feats if c in df.columns]
    scoreable = df.dropna(subset=cols)
    if scoreable.empty:
        log.info("No rows with complete features to score")
        return

    X = scoreable[cols]
    if scaler:
        X = pd.DataFrame(scaler.transform(X), columns=cols, index=X.index)
    predicted_close = pd.Series(model.predict(X), index=scoreable.index)

    # Detect mispricings
    signals = detect_mispricings(scoreable, predicted_close, entry_anchor="T60", z_threshold=1.5)

    if not signals:
        log.info("No mispricings detected at z > 1.5")
        return

    # Resolve team names for display
    with SessionLocal() as session:
        event_teams: dict[int, tuple[str, str]] = {}
        for sig in signals:
            if sig.event_id not in event_teams:
                ev = session.get(Event, sig.event_id)
                if ev:
                    home = session.get(Team, ev.home_team_id)
                    away = session.get(Team, ev.away_team_id)
                    event_teams[sig.event_id] = (
                        home.name if home else "?",
                        away.name if away else "?",
                    )

    print()
    print("╔══════════════════════════════════════════════════════════╗")
    print("║           MISPRICING SIGNALS (z > 1.5)                  ║")
    print("╠══════════════════════════════════════════════════════════╣")
    for sig in signals:
        home_name, away_name = event_teams.get(sig.event_id, ("?", "?"))
        direction = "FADE" if sig.residual > 0 else "BET"
        print(
            f"║  {direction:4s}  {away_name} @ {home_name}"
        )
        print(
            f"║        {sig.market:10s} {sig.side:5s}  "
            f"mkt={sig.market_implied:.4f}  "
            f"model={sig.model_implied:.4f}  "
            f"z={sig.z_score:+.2f}"
        )
        print("║")
    print("╚══════════════════════════════════════════════════════════╝")
    print()


# ── Model listing ──────────────────────────────────────────────

def _list_models() -> None:
    """Print all saved models with their metrics."""
    from dk_ncaab.analysis.model_store import list_models

    models = list_models()
    if not models:
        print("No saved models found. Run 'python -m dk_ncaab train' first.")
        return

    print()
    print("╔══════════════════════════════════════════════════════════╗")
    print("║                   Saved Models                          ║")
    print("╠══════════════════════════════════════════════════════════╣")
    for m in models:
        status = "✅" if m.get("exists") else "❌"
        metrics_str = "  ".join(
            f"{k}={v:.4f}" for k, v in m.get("metrics", {}).items()
            if isinstance(v, (int, float)) and v is not None
        )
        print(f"║  {status} {m['name']:20s}  {m.get('saved_at', '?'):>17s}")
        print(f"║     {m.get('model_class', '?')}  |  {metrics_str}")
        print(f"║     features: {len(m.get('features', []))} cols")
        print("║")
    print("╚══════════════════════════════════════════════════════════╝")
    print()


# ── Status ──────────────────────────────────────────────────────

def _show_status() -> None:
    """Print DB counts and data readiness."""
    from dk_ncaab.db.session import SessionLocal
    from dk_ncaab.db.models import (
        Team, Event, EventResult, OddsQuote, SplitsQuote,
        KenPomRating, APRanking,
    )

    with SessionLocal() as session:
        teams = session.query(Team).count()
        events = session.query(Event).count()
        ev_upcoming = session.query(Event).filter_by(status="upcoming").count()
        ev_live = session.query(Event).filter_by(status="live").count()
        ev_final = session.query(Event).filter_by(status="final").count()
        results = session.query(EventResult).count()
        odds = session.query(OddsQuote).count()
        splits = session.query(SplitsQuote).count()
        kenpom = session.query(KenPomRating).count()
        ap = session.query(APRanking).count()

    print()
    print("+------------------------------------------+")
    print("|     DK NCAAB Pipeline Status             |")
    print("+------------------------------------------+")
    print(f"|  Teams:          {teams:>6}                  |")
    print(f"|  Events:         {events:>6}  (total)         |")
    print(f"|    upcoming:     {ev_upcoming:>6}                  |")
    print(f"|    live:         {ev_live:>6}                  |")
    print(f"|    final:        {ev_final:>6}                  |")
    print(f"|  Results:        {results:>6}  (scores)        |")
    print(f"|  Odds quotes:    {odds:>6}                  |")
    print(f"|  Splits quotes:  {splits:>6}                  |")
    print(f"|  KenPom ratings: {kenpom:>6}                  |")
    print(f"|  AP rankings:    {ap:>6}                  |")
    print("+------------------------------------------+")

    # Research readiness: settled events with at least one DraftKings pregame quote.
    with SessionLocal() as session:
        trainable = (
            session.query(Event.id)
            .join(EventResult, EventResult.event_id == Event.id)
            .join(OddsQuote, OddsQuote.event_id == Event.id)
            .filter(Event.status == "final")
            .filter(OddsQuote.book == "draftkings")
            .filter(OddsQuote.collected_at_utc < Event.start_time_utc)
            .distinct()
            .count()
        )
    status = "READY" if trainable >= 50 else f"Need {50 - trainable} more"

    print(f"|  Settled DK quoted: {trainable:>4}  {status:>12} |")
    print("+------------------------------------------+")
    print()

    if trainable < 50:
        print("Next steps to build strict entry-EV evidence:")
        print("  1. python -m dk_ncaab backfill --days 60   (FREE, ~30s)")
        print("  2. python -m dk_ncaab collect-odds          (quota-gated)")
        print("  3. Repeat collect-odds a few times/day for odds history")
        print("  4. python -m dk_ncaab train                 (when ready)")


if __name__ == "__main__":
    main()
