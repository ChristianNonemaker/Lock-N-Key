"""Registry for event-specific team totals and player props markets."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PropMarketSpec:
    sport_key: str
    provider: str
    provider_market_key: str
    market_key: str
    label: str
    entity_type: str  # team | player
    selection_type: str  # over_under | yes_no
    stat_key: str
    ui_enabled: bool = True
    collection_enabled: bool = True
    notes: str = ""


_PROP_MARKETS: tuple[PropMarketSpec, ...] = (
    PropMarketSpec(
        sport_key="baseball_mlb",
        provider="the_odds_api_event_odds",
        provider_market_key="team_totals",
        market_key="team_totals",
        label="Team Total",
        entity_type="team",
        selection_type="over_under",
        stat_key="runs_for",
        notes="Current line only. Historical settlement can be approximated from final team runs.",
    ),
    PropMarketSpec(
        sport_key="baseball_mlb",
        provider="the_odds_api_event_odds",
        provider_market_key="pitcher_strikeouts",
        market_key="pitcher_strikeouts",
        label="Pitcher Strikeouts",
        entity_type="player",
        selection_type="over_under",
        stat_key="pitching_strike_outs",
        notes="Best first MLB pitcher prop: clean stat mapping and strong local starter context.",
    ),
    PropMarketSpec(
        sport_key="baseball_mlb",
        provider="the_odds_api_event_odds",
        provider_market_key="batter_hits",
        market_key="batter_hits",
        label="Batter Hits",
        entity_type="player",
        selection_type="over_under",
        stat_key="hits",
        notes="Simple hitter prop with direct boxscore mapping.",
    ),
    PropMarketSpec(
        sport_key="baseball_mlb",
        provider="the_odds_api_event_odds",
        provider_market_key="batter_total_bases",
        market_key="batter_total_bases",
        label="Batter Total Bases",
        entity_type="player",
        selection_type="over_under",
        stat_key="total_bases",
        notes="High-signal hitter prop with direct derivation from local boxscores.",
    ),
)


def prop_market_specs_for_sport(
    sport_key: str,
    *,
    ui_enabled_only: bool = False,
    collection_enabled_only: bool = False,
) -> list[PropMarketSpec]:
    specs = [spec for spec in _PROP_MARKETS if spec.sport_key == sport_key]
    if ui_enabled_only:
        specs = [spec for spec in specs if spec.ui_enabled]
    if collection_enabled_only:
        specs = [spec for spec in specs if spec.collection_enabled]
    return specs


def prop_market_spec(sport_key: str, provider_market_key: str) -> PropMarketSpec | None:
    for spec in _PROP_MARKETS:
        if spec.sport_key == sport_key and spec.provider_market_key == provider_market_key:
            return spec
    return None


def provider_prop_market_keys_for_sport(sport_key: str) -> list[str]:
    return [spec.provider_market_key for spec in prop_market_specs_for_sport(
        sport_key,
        collection_enabled_only=True,
    )]
