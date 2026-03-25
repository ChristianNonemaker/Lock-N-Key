"""Check events that have odds and should be final by now."""
from dk_ncaab.db.session import SessionLocal
from dk_ncaab.db.models import Event, EventResult, OddsQuote
from datetime import datetime, timezone
from sqlalchemy import func

session = SessionLocal()
now = datetime.now(timezone.utc)

# Events with odds that started before now (should have finished)
past_with_odds = (
    session.query(Event)
    .join(OddsQuote, Event.id == OddsQuote.event_id)
    .filter(Event.start_time_utc < now)
    .order_by(Event.start_time_utc.desc())
    .all()
)

print(f"Events with odds that started before now: {len(past_with_odds)}")
for e in past_with_odds[:15]:
    result = e.result
    score = f"{result.home_score}-{result.away_score}" if result else "NO RESULT"
    home = e.home_team.name if e.home_team else "?"
    away = e.away_team.name if e.away_team else "?"
    nq = session.query(func.count()).filter(OddsQuote.event_id == e.id).scalar()
    print(f"  {e.start_time_utc.strftime('%m/%d %H:%M')} | {e.status:8s} | {home:25s} vs {away:25s} | {score} | {nq} quotes")

# Check: are there EventResults for these events even though status != 'final'?
past_ids = [e.id for e in past_with_odds]
if past_ids:
    results_for_past = (
        session.query(EventResult)
        .filter(EventResult.event_id.in_(past_ids))
        .count()
    )
    print(f"\nResults existing for these past odds-events: {results_for_past}")

# Check: last night's games (Feb 14) - do they have results?
from datetime import timedelta
feb14_start = datetime(2026, 2, 14, 22, 0, tzinfo=timezone.utc)
feb14_end = datetime(2026, 2, 15, 8, 0, tzinfo=timezone.utc)
last_night = (
    session.query(Event)
    .filter(Event.start_time_utc >= feb14_start, Event.start_time_utc <= feb14_end)
    .all()
)
print(f"\nGames from last night (Feb 14 evening): {len(last_night)}")
finals = sum(1 for e in last_night if e.status == 'final')
with_results = sum(1 for e in last_night if e.result is not None)
print(f"  Status=final: {finals}")
print(f"  With results: {with_results}")

session.close()
