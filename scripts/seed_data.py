"""Seed the database with a mock hub and 20 staggered cabbage parcels in Atok, Benguet.

Usage (from the project root):
    python -m scripts.seed_data --reset            # wipe + reseed, live Open-Meteo weather
    python -m scripts.seed_data --reset --offline  # never touch the network (synthetic weather)

Planting dates are set RELATIVE TO TODAY so the demo always produces a glut spike:
    * Cohort A (12 parcels): planted within a ~6-day span  -> synchronized planting, the "problem"
    * Cohort B ( 5 parcels): planted a week or so later      -> a second, smaller wave
    * Cohort C ( 3 parcels): planted much later              -> harvests well outside the window
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # allow running as a plain script

parser = argparse.ArgumentParser(description="Seed AgroCast with mock data")
parser.add_argument("--reset", action="store_true", help="drop and recreate all tables first")
parser.add_argument("--offline", action="store_true", help="use synthetic weather (no network)")
args = parser.parse_args()
if args.offline:
    os.environ["AGROCAST_WEATHER_MODE"] = "synthetic"   # must be set BEFORE importing app.config

from sqlmodel import Session, SQLModel, select  # noqa: E402

from app.config import settings  # noqa: E402
from app.database import engine, init_db  # noqa: E402
from app.models.tables import Parcel, TradingHubCapacity  # noqa: E402
from app.routers.parcels import refresh_parcel  # noqa: E402
from app.services.congestion_service import build_forecast  # noqa: E402
from app.services.weather_service import WeatherService  # noqa: E402

CENTER_LAT, CENTER_LON = 16.58, 120.70
OWNERS = ["Juan Dela Cruz", "Maria Bagwan", "Pedro Lacsamana", "Rosa Sagsagen", "Andres Palaan",
          "Lorna Cayat", "Domingo Awingan", "Elena Pilando", "Carlos Bayanes", "Tessie Molintas"]
BARANGAYS = ["Paoay", "Abiang", "Caliking", "Cattubo", "Naguey", "Pasdong"]

# (days before today the crop was planted, how many parcels) -- see module docstring
COHORTS = [((94, 97), 12), ((84, 88), 5), ((55, 65), 3)]


def main() -> None:
    rng = random.Random(42)              # deterministic demo data
    today = settings.today()
    if args.reset:
        SQLModel.metadata.drop_all(engine)
    init_db()

    with Session(engine) as session:
        if session.exec(select(Parcel)).first():
            sys.exit("Database already has parcels. Re-run with --reset to reseed.")

        hub = TradingHubCapacity(hub_name="La Trinidad BAPTC", weekly_absorption_limit_tons=300.0)
        session.add(hub)

        weather = WeatherService(session)
        n = 0
        for (lo, hi), count in COHORTS:
            for _ in range(count):
                n += 1
                parcel = Parcel(
                    name=f"Atok Plot {n:02d} ({rng.choice(BARANGAYS)})",
                    owner=rng.choice(OWNERS),
                    crop_type="cabbage",
                    area_hectares=round(rng.uniform(0.8, 3.0), 2),
                    planting_date=today - dt.timedelta(days=rng.randint(lo, hi)),
                    latitude=round(CENTER_LAT + rng.uniform(-0.02, 0.02), 5),
                    longitude=round(CENTER_LON + rng.uniform(-0.02, 0.02), 5),
                )
                refresh_parcel(parcel, weather, today)   # computes harvest date + status via GDD
                session.add(parcel)
        session.commit()

        parcels = session.exec(select(Parcel)).all()
        forecast = build_forecast(parcels, hub_name=hub.hub_name,
                                  weekly_capacity_tons=hub.weekly_absorption_limit_tons, today=today)

    print(f"Seeded hub '{hub.hub_name}' ({hub.weekly_absorption_limit_tons:.0f} t/week) and {n} parcels "
          f"[weather mode: {settings.weather_mode}]\n")
    print(f"{'Date':<12}{'Harvest t':>10}{'Rolling 7d t':>14}{'CI':>7}  Status")
    for p in forecast.daily:
        bar = "#" * int(min(p.congestion_index, 2.0) * 10)
        print(f"{p.date!s:<12}{p.harvest_tons_today:>10.1f}{p.rolling_7d_tons:>14.1f}"
              f"{p.congestion_index:>7.2f}  {p.alert_status.value:<10}{bar}")
    s = forecast.summary
    print(f"\nPeak CI {s.peak_congestion_index:.2f} (window starting {s.peak_window_start}) | "
          f"{s.high_glut_days} high-glut day(s) | first alert: {s.first_high_glut_date}")


if __name__ == "__main__":
    main()
