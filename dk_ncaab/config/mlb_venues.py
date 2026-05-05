"""Curated MLB venue metadata for weather and park context.

Coordinates are intentionally stored as operational metadata, not betting
signals. Park-factor fields stay nullable until a reviewed source is chosen.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MlbVenueSpec:
    name: str
    latitude: float
    longitude: float
    roof_type: str | None = None
    notes: str | None = None
    park_factor_runs: float | None = None
    park_factor_hr: float | None = None
    orientation_deg: float | None = None
    weather_exposure_rule: str | None = "open_air"
    wind_reliable_flag: bool | None = True


_VENUES_BY_NAME: dict[str, MlbVenueSpec] = {
    "angel stadium": MlbVenueSpec("Angel Stadium", 33.8003, -117.8827, "open"),
    "angel stadium of anaheim": MlbVenueSpec("Angel Stadium", 33.8003, -117.8827, "open"),
    "american family field": MlbVenueSpec(
        "American Family Field",
        43.0280,
        -87.9712,
        "retractable",
        weather_exposure_rule="roof_unknown",
        wind_reliable_flag=False,
    ),
    "miller park": MlbVenueSpec(
        "American Family Field",
        43.0280,
        -87.9712,
        "retractable",
        weather_exposure_rule="roof_unknown",
        wind_reliable_flag=False,
    ),
    "busch stadium": MlbVenueSpec("Busch Stadium", 38.6226, -90.1928, "open"),
    "busch stadium iii": MlbVenueSpec("Busch Stadium", 38.6226, -90.1928, "open"),
    "camden yards": MlbVenueSpec("Oriole Park at Camden Yards", 39.2840, -76.6217, "open"),
    "chase field": MlbVenueSpec(
        "Chase Field",
        33.4455,
        -112.0667,
        "retractable",
        weather_exposure_rule="roof_unknown",
        wind_reliable_flag=False,
    ),
    "bank one ballpark": MlbVenueSpec(
        "Chase Field",
        33.4455,
        -112.0667,
        "retractable",
        weather_exposure_rule="roof_unknown",
        wind_reliable_flag=False,
    ),
    "the bob": MlbVenueSpec(
        "Chase Field",
        33.4455,
        -112.0667,
        "retractable",
        weather_exposure_rule="roof_unknown",
        wind_reliable_flag=False,
    ),
    "citi field": MlbVenueSpec("Citi Field", 40.7571, -73.8458, "open"),
    "citizens bank park": MlbVenueSpec("Citizens Bank Park", 39.9061, -75.1665, "open"),
    "coors field": MlbVenueSpec("Coors Field", 39.7559, -104.9942, "open"),
    "comerica park": MlbVenueSpec("Comerica Park", 42.3390, -83.0485, "open"),
    "daikin park": MlbVenueSpec(
        "Daikin Park",
        29.7573,
        -95.3555,
        "retractable",
        weather_exposure_rule="roof_unknown",
        wind_reliable_flag=False,
    ),
    "minute maid park": MlbVenueSpec(
        "Daikin Park",
        29.7573,
        -95.3555,
        "retractable",
        weather_exposure_rule="roof_unknown",
        wind_reliable_flag=False,
    ),
    "enron field": MlbVenueSpec(
        "Daikin Park",
        29.7573,
        -95.3555,
        "retractable",
        weather_exposure_rule="roof_unknown",
        wind_reliable_flag=False,
    ),
    "dodger stadium": MlbVenueSpec("Dodger Stadium", 34.0739, -118.2400, "open"),
    "uniqlo field at dodger stadium": MlbVenueSpec("Dodger Stadium", 34.0739, -118.2400, "open"),
    "fenway park": MlbVenueSpec("Fenway Park", 42.3467, -71.0972, "open"),
    "globe life field": MlbVenueSpec(
        "Globe Life Field",
        32.7473,
        -97.0842,
        "retractable",
        weather_exposure_rule="roof_unknown",
        wind_reliable_flag=False,
    ),
    "great american ball park": MlbVenueSpec("Great American Ball Park", 39.0979, -84.5082, "open"),
    "guaranteed rate field": MlbVenueSpec("Rate Field", 41.8300, -87.6339, "open"),
    "rate field": MlbVenueSpec("Rate Field", 41.8300, -87.6339, "open"),
    "u.s. cellular field": MlbVenueSpec("Rate Field", 41.8300, -87.6339, "open"),
    "new comiskey park": MlbVenueSpec("Rate Field", 41.8300, -87.6339, "open"),
    "kauffman stadium": MlbVenueSpec("Kauffman Stadium", 39.0517, -94.4803, "open"),
    "loandepot park": MlbVenueSpec(
        "loanDepot park",
        25.7781,
        -80.2197,
        "retractable",
        weather_exposure_rule="roof_unknown",
        wind_reliable_flag=False,
    ),
    "marlins park": MlbVenueSpec(
        "loanDepot park",
        25.7781,
        -80.2197,
        "retractable",
        weather_exposure_rule="roof_unknown",
        wind_reliable_flag=False,
    ),
    "nationals park": MlbVenueSpec("Nationals Park", 38.8730, -77.0074, "open"),
    "oracle park": MlbVenueSpec("Oracle Park", 37.7786, -122.3893, "open"),
    "pacific bell park": MlbVenueSpec("Oracle Park", 37.7786, -122.3893, "open"),
    "sbc park": MlbVenueSpec("Oracle Park", 37.7786, -122.3893, "open"),
    "at&t park": MlbVenueSpec("Oracle Park", 37.7786, -122.3893, "open"),
    "oriole park at camden yards": MlbVenueSpec("Oriole Park at Camden Yards", 39.2840, -76.6217, "open"),
    "petco park": MlbVenueSpec("Petco Park", 32.7073, -117.1566, "open"),
    "pnc park": MlbVenueSpec("PNC Park", 40.4469, -80.0057, "open"),
    "progressive field": MlbVenueSpec("Progressive Field", 41.4962, -81.6852, "open"),
    "jacobs field": MlbVenueSpec("Progressive Field", 41.4962, -81.6852, "open"),
    "rogers centre": MlbVenueSpec(
        "Rogers Centre",
        43.6414,
        -79.3894,
        "retractable",
        weather_exposure_rule="roof_unknown",
        wind_reliable_flag=False,
    ),
    "skydome": MlbVenueSpec(
        "Rogers Centre",
        43.6414,
        -79.3894,
        "retractable",
        weather_exposure_rule="roof_unknown",
        wind_reliable_flag=False,
    ),
    "safeco field": MlbVenueSpec(
        "T-Mobile Park",
        47.5914,
        -122.3325,
        "retractable",
        weather_exposure_rule="roof_unknown",
        wind_reliable_flag=False,
    ),
    "sutter health park": MlbVenueSpec(
        "Sutter Health Park",
        38.5804,
        -121.5139,
        "open",
        notes="Temporary Athletics venue; verify active venue from MLB schedule.",
    ),
    "t-mobile park": MlbVenueSpec(
        "T-Mobile Park",
        47.5914,
        -122.3325,
        "retractable",
        weather_exposure_rule="roof_unknown",
        wind_reliable_flag=False,
    ),
    "target field": MlbVenueSpec("Target Field", 44.9817, -93.2776, "open"),
    "tropicana field": MlbVenueSpec(
        "Tropicana Field",
        27.7682,
        -82.6534,
        "fixed",
        notes="Rays venue can vary by season; verify against schedule venue for recent seasons.",
        weather_exposure_rule="indoor",
        wind_reliable_flag=False,
    ),
    "the trop": MlbVenueSpec(
        "Tropicana Field",
        27.7682,
        -82.6534,
        "fixed",
        notes="Rays venue can vary by season; verify against schedule venue for recent seasons.",
        weather_exposure_rule="indoor",
        wind_reliable_flag=False,
    ),
    "truist park": MlbVenueSpec("Truist Park", 33.8908, -84.4678, "open"),
    "suntrust park": MlbVenueSpec("Truist Park", 33.8908, -84.4678, "open"),
    "wrigley field": MlbVenueSpec("Wrigley Field", 41.9484, -87.6553, "open"),
    "yankee stadium": MlbVenueSpec("Yankee Stadium", 40.8296, -73.9262, "open"),
}

_VENUE_BY_FANGRAPHS_TEAM: dict[str, str] = {
    "angels": "Angel Stadium",
    "orioles": "Oriole Park at Camden Yards",
    "red sox": "Fenway Park",
    "white sox": "Rate Field",
    "guardians": "Progressive Field",
    "tigers": "Comerica Park",
    "royals": "Kauffman Stadium",
    "twins": "Target Field",
    "yankees": "Yankee Stadium",
    "athletics": "Sutter Health Park",
    "mariners": "T-Mobile Park",
    "rays": "Tropicana Field",
    "rangers": "Globe Life Field",
    "blue jays": "Rogers Centre",
    "diamondbacks": "Chase Field",
    "braves": "Truist Park",
    "cubs": "Wrigley Field",
    "reds": "Great American Ball Park",
    "rockies": "Coors Field",
    "marlins": "loanDepot park",
    "astros": "Daikin Park",
    "dodgers": "Dodger Stadium",
    "brewers": "American Family Field",
    "nationals": "Nationals Park",
    "mets": "Citi Field",
    "phillies": "Citizens Bank Park",
    "pirates": "PNC Park",
    "cardinals": "Busch Stadium",
    "padres": "Petco Park",
    "giants": "Oracle Park",
}


def lookup_mlb_venue(name: str | None) -> MlbVenueSpec | None:
    if not name:
        return None
    return _VENUES_BY_NAME.get(name.strip().lower())


def lookup_mlb_venue_for_fangraphs_team(team_name: str | None) -> MlbVenueSpec | None:
    if not team_name:
        return None
    venue_name = _VENUE_BY_FANGRAPHS_TEAM.get(team_name.strip().lower())
    return lookup_mlb_venue(venue_name)


def curated_mlb_venues() -> list[MlbVenueSpec]:
    """Return unique curated venue specs in stable order."""
    seen: set[str] = set()
    rows: list[MlbVenueSpec] = []
    for spec in _VENUES_BY_NAME.values():
        key = spec.name.strip().lower()
        if key in seen:
            continue
        seen.add(key)
        rows.append(spec)
    rows.sort(key=lambda item: item.name)
    return rows
