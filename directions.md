# directions.md

# Project: DraftKings NCAAB Positive EV Detection System

This document defines the full architecture, modeling philosophy, data requirements, and UI layer for a quantitative betting research system focused on NCAA Men’s College Basketball (NCAAB).

The intended reader is an LLM or engineer implementing the system.  
Clarity, alignment, and statistical rigor are prioritized over brevity.

---

# 0. Core Objective

The purpose of this system is:

To identify bets with positive expected value (EV) at the time the bet is placed.

This system must determine whether:

model_implied_probability > market_implied_probability

at a specific decision timestamp.

Decision timestamps may include:

- OPEN
- T-60 minutes
- T-30 minutes
- Near tip

The system is NOT primarily designed to beat the closing line.  
Beating the closing line (CLV) is a validation metric, not the objective.

Primary objective:
Sustainably positive long-term ROI.

---

# 1. What “Value” Means

A bet has value when:

EV = model_probability − market_implied_probability > 0

Value must be evaluated through:

1. Expected Value (Primary)
   - Computed at entry time.
   - Must exceed break-even probability.
   - Must generate positive ROI across large samples.

2. Closing Line Value (Secondary Validation)
   - Entry implied probability vs closing implied probability.
   - Positive average CLV supports model quality.
   - CLV is not the objective.

3. Structural Mispricing
   - Market deviation from strength baseline (KenPom).
   - Deviation behavior (convergence/divergence) over time.

Outcome performance determines profitability.

---

# 2. Core Research Questions

The system must be able to answer:

- How does OPEN differ from strength-based expectation?
- Does deviation from KenPom converge to CLOSE?
- Does early movement predict closing movement?
- Does early movement predict outcome?
- How do public splits interact with deviation?
- Can we predict CLOSE better than OPEN?
- Does predicted residual produce positive EV?
- Does positive EV produce positive ROI?

---

# 3. Markets and Timestamps

Markets:
- Moneyline
- Spread
- Total

Required Snapshots:

- OPEN = first observed quote
- T-60 = latest quote ≤ start_time − 60 minutes
- T-30 = latest quote ≤ start_time − 30 minutes
- CLOSE = last quote before tip

No forward-filling.
No post-tip data used in pre-tip evaluation.
No leakage permitted.

Backtests must simulate decisions using only data available at the timestamp.

---

# 4. Strength Model (KenPom Integration)

For each team and historical date (no lookahead bias):

Store:
- AdjO
- AdjD
- AdjEM
- Tempo
- Strength of Schedule

For each event:

AdjEM_diff = AdjEM_home − AdjEM_away  
KenPom_expected_spread ≈ AdjEM_diff + Home_Court_Adjustment  

Deviation metrics:

spread_dev_OPEN  = market_spread_OPEN  − KenPom_expected_spread  
spread_dev_T60   = market_spread_T60   − KenPom_expected_spread  
spread_dev_T30   = market_spread_T30   − KenPom_expected_spread  
spread_dev_CLOSE = market_spread_CLOSE − KenPom_expected_spread  

KenPom is a baseline prior.
It is not treated as ground truth.

---

# 5. Perception Bias Inputs

Store historical rankings:

- ap_rank_home
- ap_rank_away
- ap_rank_diff
- ranked_vs_unranked flag

Purpose:
Capture brand inflation and public bias.

---

# 6. Market Structure Features

Convert American odds to implied probability.
Compute vig and no-vig probabilities where possible.

Store:

- implied_OPEN
- implied_T60
- implied_T30
- implied_CLOSE

Movement features:

- d_implied_OPEN_T60
- d_implied_T60_T30
- d_implied_OPEN_CLOSE
- d_line_OPEN_T60
- d_line_T60_T30
- d_line_OPEN_CLOSE

Velocity:
movement magnitude divided by time interval.

Volatility:
- number_of_line_changes
- std deviation of implied probability pregame

Late steam indicator:
movement inside final 30 minutes.

---

# 7. Public Betting Features

At OPEN, T-60, T-30:

- bets_pct
- handle_pct
- handle_minus_bets

Derived interactions:

- deviation × public_extreme
- movement × public_extreme
- handle_minus_bets × deviation

Public splits are contextual amplifiers.

---

# 8. EV Computation

At each decision timestamp T:

EV_T = model_probability_T − market_implied_probability_T

Only place simulated bets when:

EV_T > statistically validated threshold

Threshold must be derived via walk-forward validation.

---

# 9. CLV Computation (Validation Only)

CLV_implied = implied_CLOSE − implied_entry  

CLV_spread must be direction-aware:
Positive value = favorable movement for the side bet.

Track:

- Average CLV
- CLV distribution
- CLV vs ROI correlation

CLV validates signal quality.
ROI determines profitability.

---

# 10. Outcome Labels

Store:

- ML win/loss
- Spread cover (entry-based)
- Total over/under result

Compute:

break_even_probability from odds  
realized_return per bet  
rolling ROI  

---

# 11. Modeling Framework

Primary modeling tasks:

1. Probability Estimation Model
   y = win/cover
   X = strength + deviation + movement + splits + perception

2. Closing Prediction Model
   y = implied_CLOSE
   X = OPEN + strength + movement + splits

3. Residual Model
   residual = model_implied − market_implied

Bet only when residual exceeds validated threshold.

Walk-forward validation required.

No hardcoded heuristics.

---

# 12. Backtesting Requirements

Backtests must:

- Respect timestamp integrity
- Avoid leakage
- Use walk-forward validation
- Report:
  - ROI
  - CLV
  - Drawdown
  - Sharpe-like metric
  - Stability across seasons

---

# 13. Visualization UI Requirement

Add a read-only interactive UI for:

- Data validation
- Market movement visualization
- Model inspection
- Strategy analysis

The UI is mandatory for system transparency.

---

# 14. UI Architecture

MVP stack:

- Backend: FastAPI (read-only API)
- Frontend: Streamlit
- Charts: Plotly
- Database: Postgres (read-only credentials)

UI must not mutate data.

---

# 15. Required UI Views

## 15.1 Game Browser

- Filter by date
- Search by team
- Display:
  - Spread_dev_OPEN
  - Public imbalance
  - Model EV at key timestamps

## 15.2 Game Detail View

Tabs:
- Spread
- Total
- Moneyline

Display:

- Line movement over time
- Implied probability over time
- Public splits over time
- KenPom expected spread vs market
- Snapshot comparison (OPEN/T60/T30/CLOSE)
- EV at each decision timestamp
- Outcome + realized return
- CLV metrics

## 15.3 Model Panel

- Predicted probability vs market implied
- Residual over time
- EV visualization
- Confidence intervals

## 15.4 Backtest Dashboard

- ROI over time
- CLV distribution
- EV vs ROI scatter
- Filter by:
  - deviation bins
  - public imbalance bins
  - ranked status

---

# 16. Backend API Endpoints

GET /games?date=YYYY-MM-DD  
GET /game/{event_id}/summary  
GET /game/{event_id}/timeseries  
GET /game/{event_id}/features  
GET /game/{event_id}/model  
GET /backtest/summary  

All endpoints read-only.

---

# 17. UI Execution Instructions

## Local Development

1. Ensure Postgres is running.
2. Start API server:

uvicorn api.main:app --reload --port 8000

3. Start UI:

streamlit run ui/app.py

4. Open browser:

UI: http://localhost:8501  
API docs: http://localhost:8000/docs  

---

## Optional Docker Deployment

Use docker-compose to launch:

- postgres
- api
- ui

Access:

UI: http://localhost:8501  
API: http://localhost:8000/docs  

---

# 18. Build Order

1. Database schema
2. Odds ingestion
3. Snapshot logic
4. KenPom ingestion
5. Rankings ingestion
6. Feature computation
7. EV computation
8. Modeling
9. Backtesting
10. FastAPI layer
11. Streamlit UI
12. Strategy validation

---

# 19. Success Criteria

The system succeeds if:

1. It consistently identifies bets where:
   model_probability > market_implied_probability at decision time.

2. It produces:
   - Positive long-term ROI
   - Stable performance across seasons
   - Controlled drawdowns

3. It shows:
   - Positive average CLV (supporting signal validity)

Primary success condition:

Sustainable identification of positive expected value bets at the time of entry, resulting in long-term profitability.

CLV is validation.
ROI is success.
