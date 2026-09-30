"""Central configuration + agronomic constants for AgroCast.

Everything tunable lives here so services stay free of magic numbers.
Values can be overridden with environment variables (see .env.example).
"""
from __future__ import annotations

import datetime as dt
import os
from dataclasses import dataclass, field
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class CropProfile:
    """Thermal-time parameters for one crop."""
    key: str
    display_name: str
    t_base_c: float          # base temperature below which no growth occurs
    target_gdd: float        # cumulative GDD required for harvest maturity
    yield_t_per_ha: float    # average marketable yield


# Add more crops here (e.g. carrots, potato) without touching any service code.
CROP_PROFILES: dict[str, CropProfile] = {
    "cabbage": CropProfile(
        key="cabbage",
        display_name="Highland Cabbage (Brassica oleracea)",
        t_base_c=4.4,
        target_gdd=1200.0,
        yield_t_per_ha=18.0,
    ),
}


def _env(name: str, default: str) -> str:
    return os.getenv(f"AGROCAST_{name}", default)


@dataclass(frozen=True)
class Settings:
    database_url: str = field(default_factory=lambda: _env("DATABASE_URL", "sqlite:///./agrocast.db"))

    # --- Weather pipeline ---
    weather_mode: str = field(default_factory=lambda: _env("WEATHER_MODE", "live"))  # live | synthetic
    weather_fallback_synthetic: bool = True     # use synthetic data if Open-Meteo is unreachable
    open_meteo_archive_url: str = "https://archive-api.open-meteo.com/v1/archive"
    open_meteo_forecast_url: str = "https://api.open-meteo.com/v1/forecast"
    archive_lag_days: int = 7                   # ERA5 archive lags real time by ~5 days; be safe
    forecast_horizon_days: int = 14             # Open-Meteo allows up to 16
    weather_cache_ttl_hours: int = 6            # how long cached *forecast* rows are trusted
    http_timeout_s: float = 20.0
    timezone: str = "Asia/Manila"

    # --- GDD engine ---
    climatology_window_days: int = 30           # trailing days used to extrapolate beyond the forecast
    fallback_daily_gdd: float = 10.0            # used only if no weather at all is available
    max_growth_days: int = 300                  # safety cap on the projection loop

    # --- Congestion index ---
    warning_threshold: float = 1.00
    high_glut_threshold: float = 1.20           # CI > 1.20 -> High Glut Alert

    # --- Optional Earth Observation module ---
    gee_enabled: bool = field(default_factory=lambda: _env("GEE_ENABLED", "false").lower() == "true")
    gee_project: str = field(default_factory=lambda: _env("GEE_PROJECT", ""))

    def today(self) -> dt.date:
        """'Today' in the Philippines (so a server in UTC doesn't roll the date early/late)."""
        return dt.datetime.now(ZoneInfo(self.timezone)).date()


settings = Settings()
