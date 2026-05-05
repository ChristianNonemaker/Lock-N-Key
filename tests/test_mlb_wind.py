from __future__ import annotations

from dk_ncaab.collectors.mlb_wind import derive_field_wind, parse_wind_from_degrees


def test_parse_wind_from_degrees_accepts_cardinal_and_numeric_values():
    assert parse_wind_from_degrees("NW") == 315.0
    assert parse_wind_from_degrees("292.5") == 292.5
    assert parse_wind_from_degrees("variable") is None


def test_derive_field_wind_projects_compass_wind_to_field_relative_components():
    wind = derive_field_wind(
        wind_direction="NW",
        wind_mph=12.0,
        center_field_orientation_deg=135.0,
        roof_type="open",
        weather_exposure_rule="open_air",
    )

    assert wind.field_wind_label == "blowing out"
    assert wind.wind_from_degrees == 315.0
    assert wind.wind_to_center_alignment == 1.0
    assert wind.wind_out_mph == 12.0
    assert wind.wind_in_mph == 0.0
    assert wind.crosswind_mph == 0.0


def test_derive_field_wind_keeps_unknown_orientation_explicit():
    wind = derive_field_wind(
        wind_direction="NW",
        wind_mph=12.0,
        center_field_orientation_deg=None,
    )

    assert wind.field_wind_label == "orientation pending"
    assert wind.wind_out_mph is None
    assert wind.wind_in_mph is None
    assert wind.crosswind_mph is None


def test_derive_field_wind_suppresses_indoor_or_unreliable_wind():
    wind = derive_field_wind(
        wind_direction="NW",
        wind_mph=12.0,
        center_field_orientation_deg=135.0,
        roof_type="fixed",
        weather_exposure_rule="indoor",
        wind_reliable_flag=False,
    )

    assert wind.field_wind_label == "indoor/roof"
    assert wind.wind_out_mph is None
