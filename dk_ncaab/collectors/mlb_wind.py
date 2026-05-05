"""Field-relative MLB wind helpers."""

from __future__ import annotations

import math
from dataclasses import dataclass


_CARDINAL_DEGREES = {
    "N": 0.0,
    "NNE": 22.5,
    "NE": 45.0,
    "ENE": 67.5,
    "E": 90.0,
    "ESE": 112.5,
    "SE": 135.0,
    "SSE": 157.5,
    "S": 180.0,
    "SSW": 202.5,
    "SW": 225.0,
    "WSW": 247.5,
    "W": 270.0,
    "WNW": 292.5,
    "NW": 315.0,
    "NNW": 337.5,
}


@dataclass(frozen=True)
class FieldWind:
    wind_from_degrees: float | None
    wind_to_center_alignment: float | None
    wind_out_mph: float | None
    wind_in_mph: float | None
    crosswind_mph: float | None
    field_wind_label: str | None


def _wrap_degrees(value: float) -> float:
    return value % 360.0


def parse_wind_from_degrees(direction: str | None) -> float | None:
    """Parse NWS-style wind source direction into compass degrees."""
    if not direction:
        return None
    text = direction.strip().upper()
    if not text or text in {"VRB", "VARIABLE", "CALM", "UNKNOWN"}:
        return None
    if text.endswith("°"):
        text = text[:-1]
    try:
        return _wrap_degrees(float(text))
    except ValueError:
        return _CARDINAL_DEGREES.get(text)


def derive_field_wind(
    *,
    wind_direction: str | None,
    wind_mph: float | None,
    center_field_orientation_deg: float | None,
    roof_type: str | None = None,
    weather_exposure_rule: str | None = None,
    wind_reliable_flag: bool | None = True,
) -> FieldWind:
    """Translate compass wind into field-relative components.

    `wind_direction` is the direction the wind is blowing from. Orientation is
    the compass bearing from home plate toward center field.
    """
    source_deg = parse_wind_from_degrees(wind_direction)
    if wind_mph is None or source_deg is None:
        return FieldWind(source_deg, None, None, None, None, None)

    rule = (weather_exposure_rule or "").strip().lower()
    roof = (roof_type or "").strip().lower()
    if wind_reliable_flag is False or rule in {"ignore", "indoor"} or roof in {"fixed", "dome"}:
        return FieldWind(source_deg, None, None, None, None, "indoor/roof")

    if center_field_orientation_deg is None:
        return FieldWind(source_deg, None, None, None, None, "orientation pending")

    wind_to_deg = _wrap_degrees(source_deg + 180.0)
    delta = math.radians(_wrap_degrees(wind_to_deg - center_field_orientation_deg))
    alignment = math.cos(delta)
    cross = math.sin(delta)

    out_mph = max(0.0, wind_mph * alignment)
    in_mph = max(0.0, -wind_mph * alignment)
    cross_mph = abs(wind_mph * cross)

    if alignment >= 0.5:
        label = "blowing out"
    elif alignment <= -0.5:
        label = "blowing in"
    else:
        label = "crosswind"

    return FieldWind(
        wind_from_degrees=round(source_deg, 1),
        wind_to_center_alignment=round(alignment, 3),
        wind_out_mph=round(out_mph, 1),
        wind_in_mph=round(in_mph, 1),
        crosswind_mph=round(cross_mph, 1),
        field_wind_label=label,
    )
