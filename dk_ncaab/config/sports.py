"""Sport/provider registry for collection, API filtering, and UI eligibility."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class SportSpec:
    key: str
    label: str
    league_key: str
    league_name: str
    status: str
    schedule_source: str | None
    odds_api_sport: str | None
    results_source: str | None
    splits_source: str | None
    team_stats_source: str | None
    player_stats_source: str | None
    injuries_source: str | None
    props_source: str | None
    feature_enrichers: tuple[str, ...] = field(default_factory=tuple)
    espn_scoreboard_url: str | None = None
    espn_scoreboard_params: dict[str, str] = field(default_factory=dict)
    schedule_enabled_by_default: bool = False
    odds_enabled_by_default: bool = False
    odds_collectable: bool = False
    ui_enabled: bool = False
    notes: str = ""

    @property
    def schedule_collectable(self) -> bool:
        return self.schedule_enabled_by_default and bool(self.espn_scoreboard_url)


_SPORT_SPECS: tuple[SportSpec, ...] = (
    SportSpec(
        key="basketball_ncaab",
        label="NCAAB",
        league_key="ncaab",
        league_name="NCAA Men's Basketball",
        status="active",
        schedule_source="espn_scoreboard",
        odds_api_sport="basketball_ncaab",
        results_source="espn_scoreboard",
        splits_source="action_network_public_betting",
        team_stats_source="kenpom_manual,ap_rankings_manual",
        player_stats_source=None,
        injuries_source=None,
        props_source=None,
        feature_enrichers=("odds_snapshots", "action_network_splits", "kenpom", "ap_rankings"),
        espn_scoreboard_url=(
            "https://site.api.espn.com/apis/site/v2/sports/"
            "basketball/mens-college-basketball/scoreboard"
        ),
        espn_scoreboard_params={"groups": "50"},
        schedule_enabled_by_default=True,
        odds_collectable=True,
        ui_enabled=True,
        notes="Deepest current support; NCAAB-specific enrichers are active here only.",
    ),
    SportSpec(
        key="americanfootball_ncaaf",
        label="NCAAF",
        league_key="ncaaf",
        league_name="NCAA Football",
        status="active",
        schedule_source="espn_scoreboard",
        odds_api_sport="americanfootball_ncaaf",
        results_source="espn_scoreboard",
        splits_source=None,
        team_stats_source=None,
        player_stats_source=None,
        injuries_source=None,
        props_source=None,
        feature_enrichers=("odds_snapshots",),
        espn_scoreboard_url=(
            "https://site.api.espn.com/apis/site/v2/sports/"
            "football/college-football/scoreboard"
        ),
        espn_scoreboard_params={"groups": "80"},
        schedule_enabled_by_default=True,
        odds_collectable=True,
        ui_enabled=True,
        notes="Schedule/results and generic odds snapshots only until richer providers are chosen.",
    ),
    SportSpec(
        key="americanfootball_nfl",
        label="NFL",
        league_key="nfl",
        league_name="NFL",
        status="active",
        schedule_source="espn_scoreboard",
        odds_api_sport="americanfootball_nfl",
        results_source="espn_scoreboard",
        splits_source=None,
        team_stats_source=None,
        player_stats_source=None,
        injuries_source=None,
        props_source=None,
        feature_enrichers=("odds_snapshots",),
        espn_scoreboard_url=(
            "https://site.api.espn.com/apis/site/v2/sports/"
            "football/nfl/scoreboard"
        ),
        schedule_enabled_by_default=True,
        odds_collectable=True,
        ui_enabled=True,
        notes="Schedule/results and generic odds snapshots only until richer providers are chosen.",
    ),
    SportSpec(
        key="baseball_mlb",
        label="MLB",
        league_key="mlb",
        league_name="MLB",
        status="active",
        schedule_source="espn_scoreboard",
        odds_api_sport="baseball_mlb",
        results_source="espn_scoreboard",
        splits_source=None,
        team_stats_source="mlb_stats_api_boxscore",
        player_stats_source="mlb_stats_api_boxscore",
        injuries_source=None,
        props_source=None,
        feature_enrichers=("odds_snapshots", "mlb_stats"),
        espn_scoreboard_url=(
            "https://site.api.espn.com/apis/site/v2/sports/"
            "baseball/mlb/scoreboard"
        ),
        schedule_enabled_by_default=True,
        odds_enabled_by_default=True,
        odds_collectable=True,
        ui_enabled=True,
        notes="The current quota-safe default odds target; MLB Stats API backs team/player trends.",
    ),
    SportSpec(
        key="basketball_nba",
        label="NBA",
        league_key="nba",
        league_name="NBA",
        status="planned",
        schedule_source="espn_scoreboard",
        odds_api_sport="basketball_nba",
        results_source="espn_scoreboard",
        splits_source=None,
        team_stats_source=None,
        player_stats_source=None,
        injuries_source=None,
        props_source=None,
        feature_enrichers=(),
        espn_scoreboard_url=(
            "https://site.api.espn.com/apis/site/v2/sports/"
            "basketball/nba/scoreboard"
        ),
        notes="Provider contracts and tests must land before collection or UI eligibility.",
    ),
    SportSpec(
        key="soccer_epl",
        label="Soccer",
        league_key="epl",
        league_name="English Premier League",
        status="planned",
        schedule_source=None,
        odds_api_sport=None,
        results_source=None,
        splits_source=None,
        team_stats_source=None,
        player_stats_source=None,
        injuries_source=None,
        props_source=None,
        feature_enrichers=(),
        notes="Soccer has provider-specific league keys; verify ESPN and The Odds API mappings before enabling.",
    ),
)

SPORTS: dict[str, SportSpec] = {spec.key: spec for spec in _SPORT_SPECS}
_SPORT_BY_LEAGUE: dict[str, SportSpec] = {spec.league_key: spec for spec in _SPORT_SPECS}


def normalize_sport_keys(values: list[str] | tuple[str, ...]) -> list[str]:
    """Strip blanks and de-duplicate while preserving order."""
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in values:
        sport = raw.strip()
        if not sport or sport in seen:
            continue
        normalized.append(sport)
        seen.add(sport)
    return normalized


def get_sport(key: str) -> SportSpec:
    try:
        return SPORTS[key]
    except KeyError as exc:
        raise ValueError(f"Unsupported sport={key}") from exc


def all_sports(include_planned: bool = False) -> list[SportSpec]:
    if include_planned:
        return list(_SPORT_SPECS)
    return [spec for spec in _SPORT_SPECS if spec.status == "active"]


def default_schedule_sport_keys() -> list[str]:
    return [spec.key for spec in _SPORT_SPECS if spec.schedule_enabled_by_default]


def default_odds_sport_keys() -> list[str]:
    return [spec.key for spec in _SPORT_SPECS if spec.odds_enabled_by_default]


def odds_collectable_sport_keys() -> list[str]:
    return [spec.key for spec in _SPORT_SPECS if spec.odds_collectable]


def ui_sport_choices() -> list[tuple[str, str]]:
    return [(spec.key, spec.label) for spec in _SPORT_SPECS if spec.ui_enabled]


def ui_sport_keys() -> list[str]:
    return [spec.key for spec in _SPORT_SPECS if spec.ui_enabled]


def league_for_sport(key: str) -> tuple[str, str]:
    spec = get_sport(key)
    return spec.league_key, spec.league_name


def league_key_for_sport(key: str) -> str:
    return get_sport(key).league_key


def sport_for_league_key(league_key: str) -> str:
    try:
        return _SPORT_BY_LEAGUE[league_key].key
    except KeyError as exc:
        raise ValueError(f"Unsupported league_key={league_key}") from exc


def odds_api_sport_for(key: str) -> str:
    spec = get_sport(key)
    if not spec.odds_collectable or not spec.odds_api_sport:
        raise ValueError(f"Sport is not eligible for odds collection: {key}")
    return spec.odds_api_sport


def espn_scoreboard_url_for(key: str) -> str:
    spec = get_sport(key)
    if not spec.schedule_collectable or not spec.espn_scoreboard_url:
        raise ValueError(f"Sport is not eligible for ESPN schedule collection: {key}")
    return spec.espn_scoreboard_url


def espn_scoreboard_params_for(key: str, date_str: str) -> dict[str, str]:
    spec = get_sport(key)
    params = {"dates": date_str, "limit": "200"}
    params.update(spec.espn_scoreboard_params)
    return params


def validate_schedule_sports(values: list[str]) -> list[str]:
    sports = normalize_sport_keys(values)
    for sport in sports:
        espn_scoreboard_url_for(sport)
    return sports


def validate_odds_sports(values: list[str]) -> list[str]:
    sports = normalize_sport_keys(values)
    for sport in sports:
        odds_api_sport_for(sport)
    return sports


def feature_enrichers_for(key: str) -> tuple[str, ...]:
    return get_sport(key).feature_enrichers
