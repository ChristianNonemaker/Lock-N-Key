# Private Odds Dashboard Roadmap

Last reviewed: 2026-04-23

## Product North Star

Build a private sportsbook research dashboard that helps one bettor:

1. find the most important games to inspect in under 30 seconds,
2. understand why a number is where it is in under 2 minutes,
3. know whether the screen is showing research context or validated edge evidence.

This product should behave like a serious odds workstation, not a generic sports stats site.

## Core Principles

- Treat the market timeline as the primary object.
- Keep DraftKings book-specific decisioning as the first truth.
- Separate research context from validated wagering evidence everywhere.
- Prefer one brutally honest sport with strong evidence over many shallow sports.
- Add new provider-backed schema only after identifier, cadence, and retention contracts are known.

## Core Dashboard Objects

### 1. Actionable Slate

The first screen should answer:

- what games matter right now,
- which markets are moving,
- where data is fresh or stale,
- which games deserve immediate review.

Each row should evolve toward:

- teams
- start time
- market freshness
- open -> current -> best entry anchor
- biggest number move
- biggest price move
- split divergence
- weather / venue flag when relevant
- short "why open this" reason

### 2. Line Lifecycle

The real core object is not just a game row. It is the line lifecycle for each market and side:

- open
- T60
- T30
- current
- close
- line move
- price move
- last update
- source / book

The UI should show this as one coherent market story instead of scattering it across separate tables.

### 3. Game Research Panel

Every game should have one synthesized decision panel before raw tables:

- market context
- movement narrative
- public split context
- weather / venue / park context
- team form
- starter or player context
- data gaps

Raw tables still matter, but they should sit underneath a bettor-readable summary.

### 4. Evidence Shelf

The product needs a hard split between:

- Research-only evidence
- Validated wagering evidence

Research-only includes:

- movement
- splits
- trends
- weather
- matchup context
- injuries / lineups / starters

Validated evidence includes:

- strict OOF fair price
- edge %
- EV units
- threshold provenance
- calibration
- sample size
- realized ROI by anchor

### 5. Research Slip And Ledger

The Research Slip should stay session-state only while the private ledger keeps durable history:

- pinned lines
- notes
- thesis
- observed move after review
- eventual outcome

This creates product memory without pretending to be an execution engine.

### 6. Trust Layer

Freshness, readiness, quota, provider gaps, and settlement coverage should remain visible, but secondary.
Trust should support the betting workflow, not overwhelm it.

## Data And Modeling North Star

The long-term model north star is entry-time expected value by market and side.

A promoted signal should require:

- anchor-specific American price
- entry-safe features
- out-of-fold probabilities
- settlement-aware EV math
- enough sample size and calibration to trust the threshold

Everything else is support for this goal, not a substitute for it.

## Required Data Contracts

### 1. Canonical Event Identity

- one row per real game
- stable provider keys
- no fragmented ESPN vs Odds API duplicates

### 2. Pregame Odds Timeline

- DraftKings book-specific history
- open / T60 / T30 / current / close
- exact timestamps
- exact-tip boundary policy

### 3. Settlement Contract

- official final result
- push / void / cancel / postpone / regrade semantics
- market-aware grading
- defensible completion timing

### 4. Entry-Safe Feature Contract

- only features available by the selected anchor
- no close-aware leakage
- no future-anchor leakage
- sport-aware feature gating

### 5. Research-vs-Prediction Contract

Every important field should be thought of as one of:

- descriptive_only
- entry_safe_predictive
- postgame_validation

### 6. Historical Context Contracts

- team logs
- player logs
- starters / lineups / injuries
- venue / park / weather
- public splits

These should inform research first, then prediction only after timestamp discipline is proven.

## Source Strategy

### Keep As Backbone

- ESPN for schedules and results
- The Odds API for DraftKings odds
- MLB Stats API for MLB team / player / starter context
- NWS + reviewed venue metadata + reviewed park factors for MLB environment
- KenPom + AP for NCAAB strength context

### Treat As Exploratory

- Action Network public betting splits

Useful, but still operationally brittle on the current VM.

### Keep Deferred Until Provider Contracts Are Chosen

- injuries
- lineups
- props
- richer player-provider schema
- soccer expansion beyond placeholder planning

## Phased Roadmap

## Phase 1: Honest Research Dashboard

Goal: best private board for reading a slate, not pretending to be an edge engine.

Build:

- slate-first dashboard hierarchy
- line lifecycle visible in board rows
- clear price move vs number move
- stronger market narrative in the research panel
- recommendation/research ledger
- stricter readiness and status language

Acceptance:

- the first screen tells a bettor what to inspect first
- no exploratory metric looks like a validated edge

## Phase 2: Trustworthy Market Spine

Goal: make the market timeline and event identity defensible.

Build:

- one-time duplicate-event reconciliation
- ESPN / Odds API provider-key hardening
- strict open / T60 / T30 / close lineage
- exact-tip anchor policy tests
- better settled-trainable and anchor-valid counts

Acceptance:

- status and readiness counts match what the strict EV path would truly allow

## Phase 3: MLB Evidence Base

Goal: first sport with enough real settled entry-price history to support honest model promotion.

Build:

- controlled MLB DraftKings collection
- enough settled events with anchor prices
- strict settlement coverage
- refreshed feature parquet from populated DB
- strict OOF entry-EV artifacts

Acceptance:

- the board can show validated MLB evidence without overclaiming

## Phase 4: Market Intelligence Engine

Goal: explain why the number moved.

Build:

- move classification: price-only vs number move
- key-number crossing
- stale hangs
- public-vs-handle divergence
- weather / starter / injury trigger notes
- book-specific vs consensus context where available

Acceptance:

- the board tells a coherent market story, not just raw values

## Phase 5: Sport-Specific Matchup Depth

Goal: deepen research context without losing discipline.

Build by sport:

- MLB: starters, team/player trends, weather, park, venue
- NCAAB: team factors, strength, rest, matchup metrics
- NFL / NCAAF: efficiency, injuries, QB depth, weather

Acceptance:

- deeper context improves research value without violating provider-decision rules

## Phase 6: Portfolio And Recommendation Memory

Goal: make the product usable as a daily decision tool.

Build:

- private recommendation ledger
- note-taking and postmortem tracking
- confidence and uncertainty
- sample-aware thresholds
- exposure and correlation awareness

Acceptance:

- the user can review, track, and learn from their own process over time

## Biggest Failure Modes

### 1. Leakage Dressed Up As Intelligence

- close-aware features in entry models
- actual weather treated as known pregame
- exact-tip ambiguity left unresolved

### 2. Dashboard Overclaiming

- research evidence shown like validated edge
- readiness counts sounding stronger than the strict OOF contract

### 3. Thin Historical Breadth

- too few settled anchor-priced rows
- unstable thresholds
- props attempted before sides and totals are trustworthy

### 4. Identity And Settlement Drift

- duplicate events
- wrong joins
- incomplete push / void / regrade handling

### 5. Feature Sprawl

- too many provider surfaces before timestamp and lineage discipline are clean

## Recommended Next 3 Sprints

### Sprint A

- reconcile existing duplicate MLB events locally
- tighten `/status` and MLB readiness language
- add line lifecycle fields to the board row design

### Sprint B

- add price-move vs number-move presentation
- create a synthesized research summary card for each game
- persist a private research ledger beyond the current slip

### Sprint C

- rebuild fresh parquet from the populated local DB
- measure how many MLB rows are truly anchor-valid and settled
- run strict `oof-entry-ev`
- only then expose validated MLB evidence on the board

## Strategic Rule

The winning path is not "collect every stat in existence."

The winning path is:

1. make the market spine correct,
2. make the dashboard elite at explaining the number,
3. earn the right to show validated edge only after the evidence base exists.
