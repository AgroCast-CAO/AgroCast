"""Growing Degree Day (GDD) engine.

Plants mature by accumulated *thermal time*, not calendar days:

    GDD_day = max( (T_max + T_min) / 2 - T_base , 0 )

The engine walks day-by-day from the planting date, adding GDD from:
    1. real observed history   (Open-Meteo archive)
    2. the ~14-day forecast    (Open-Meteo forecast)
    3. a climatological extrapolation (mean GDD/day of the trailing 30 days) beyond that

...and reports the first date the running total reaches the crop's target (1200 for cabbage).

The maths lives in the pure function `project_from_series`, so it is unit-testable
without a database or network. `estimate_growth` is the thin I/O wrapper around it.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import TYPE_CHECKING, Mapping, Optional

from app.config import CROP_PROFILES, CropProfile, settings

if TYPE_CHECKING:  # avoid a circular import at runtime
    from app.services.weather_service import WeatherService


def compute_daily_gdd(t_max: float, t_min: float, t_base: float = 4.4) -> float:
    """Single-day GDD using the simple averaging method (never negative)."""
    return max((t_max + t_min) / 2.0 - t_base, 0.0)


def get_crop_profile(crop_type: str) -> CropProfile:
    try:
        return CROP_PROFILES[crop_type.lower()]
    except KeyError:
        raise ValueError(f"Unsupported crop_type '{crop_type}'. Supported: {sorted(CROP_PROFILES)}") from None


@dataclass(frozen=True)
class GrowthProjection:
    planting_date: dt.date
    target_gdd: float
    accumulated_gdd: float          # accumulated up to and including today
    progress_pct: float             # 0-100 (capped)
    harvest_date: Optional[dt.date]
    days_to_harvest: Optional[int]  # negative/0 if already mature
    basis: str                      # observed_forecast | climatology | unknown
    used_synthetic_weather: bool = False


def project_from_series(
    planting_date: dt.date,
    daily_gdd: Mapping[dt.date, float],
    today: dt.date,
    target_gdd: float,
    climatology_gdd: float,
    last_data_date: Optional[dt.date],
    max_days: int = 300,
) -> GrowthProjection:
    """Pure GDD accumulation. Days missing from `daily_gdd` use `climatology_gdd`."""
    cumulative = 0.0
    accumulated_today = 0.0
    harvest: Optional[dt.date] = None
    basis = "unknown"
    day = planting_date

    for _ in range(max_days):
        cumulative += daily_gdd.get(day, climatology_gdd)
        if day <= today:
            accumulated_today = cumulative
        if harvest is None and cumulative >= target_gdd:
            harvest = day
            basis = "observed_forecast" if (last_data_date and day <= last_data_date) else "climatology"
        # Stop once we have the harvest date AND have caught up to today's progress figure.
        if harvest is not None and day >= today:
            break
        day += dt.timedelta(days=1)

    return GrowthProjection(
        planting_date=planting_date,
        target_gdd=target_gdd,
        accumulated_gdd=round(accumulated_today, 1),
        progress_pct=round(min(100.0, accumulated_today / target_gdd * 100.0), 1),
        harvest_date=harvest,
        days_to_harvest=(harvest - today).days if harvest else None,
        basis=basis,
    )


def estimate_growth(
    weather: "WeatherService",
    *,
    planting_date: dt.date,
    latitude: float,
    longitude: float,
    crop_type: str = "cabbage",
    today: Optional[dt.date] = None,
) -> GrowthProjection:
    """Fetch weather for a parcel's location and project its harvest date."""
    crop = get_crop_profile(crop_type)
    today = today or settings.today()

    # Pull from planting date (or 30 days ago, for the climatology) through the forecast horizon.
    fetch_start = min(planting_date, today - dt.timedelta(days=settings.climatology_window_days))
    fetch_end = today + dt.timedelta(days=settings.forecast_horizon_days)
    result = weather.get_daily_temperatures(latitude, longitude, fetch_start, fetch_end, today=today)

    daily_gdd = {t.date: compute_daily_gdd(t.t_max, t.t_min, crop.t_base_c) for t in result.temps}

    # Climatology = mean GDD/day over the trailing window (observed days only, i.e. <= today)
    window_start = today - dt.timedelta(days=settings.climatology_window_days)
    recent = [g for d, g in daily_gdd.items() if window_start <= d <= today]
    climatology = sum(recent) / len(recent) if recent else settings.fallback_daily_gdd

    projection = project_from_series(
        planting_date=planting_date,
        daily_gdd=daily_gdd,
        today=today,
        target_gdd=crop.target_gdd,
        climatology_gdd=climatology,
        last_data_date=max(daily_gdd) if daily_gdd else None,
        max_days=settings.max_growth_days,
    )
    return GrowthProjection(**{**projection.__dict__, "used_synthetic_weather": result.used_synthetic})
