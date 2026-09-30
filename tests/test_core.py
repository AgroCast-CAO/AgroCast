import datetime as dt
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.models.schemas import AlertStatus
from app.services.congestion_service import build_forecast
from app.services.gdd_engine import compute_daily_gdd, project_from_series

TODAY = dt.date(2026, 9, 28)


# ---------------------------- GDD maths ----------------------------
def test_gdd_formula():
    assert compute_daily_gdd(20, 10, 4.4) == pytest.approx(10.6)
    assert compute_daily_gdd(6, 0, 4.4) == 0.0       # below base -> clamped at zero


def test_harvest_date_is_first_day_crossing_target():
    planting = TODAY - dt.timedelta(days=10)
    series = {planting + dt.timedelta(days=i): 10.0 for i in range(40)}   # flat 10 GDD/day
    proj = project_from_series(planting, series, TODAY, 1200, 10.0, max(series))
    # 1200 GDD at 10/day is reached on day index 119 (the 120th day) -> beyond the series => climatology
    assert proj.harvest_date == planting + dt.timedelta(days=119)
    assert proj.basis == "climatology"
    assert proj.accumulated_gdd == 110.0             # days 0..10 inclusive
    assert proj.progress_pct == pytest.approx(9.2, abs=0.1)


def test_already_mature_parcel_caps_progress_at_100():
    planting = TODAY - dt.timedelta(days=150)
    proj = project_from_series(planting, {}, TODAY, 1200, 10.0, None)
    assert proj.progress_pct == 100.0 and proj.days_to_harvest < 0


# ------------------------ Congestion index ------------------------
def _parcel(area, harvest_offset, status="growing"):
    return SimpleNamespace(crop_type="cabbage", area_hectares=area, status=status,
                           estimated_harvest_date=TODAY + dt.timedelta(days=harvest_offset))


def test_congestion_index_and_alert_threshold():
    # 20 ha * 18 t/ha = 360 t inside one week vs 300 t capacity -> CI 1.2 exactly (NOT > 1.2)
    fc = build_forecast([_parcel(20, 2)], hub_name="X", weekly_capacity_tons=300, today=TODAY)
    assert fc.daily[0].congestion_index == pytest.approx(1.2)
    assert fc.daily[0].alert_status == AlertStatus.WARNING
    # a bit more tips it over 1.2
    fc = build_forecast([_parcel(21, 2)], hub_name="X", weekly_capacity_tons=300, today=TODAY)
    assert fc.daily[0].alert_status == AlertStatus.HIGH_GLUT
    assert fc.summary.first_high_glut_date == TODAY


def test_harvested_and_overdue_handling():
    parcels = [_parcel(10, 3, status="harvested"), _parcel(10, -5)]
    fc = build_forecast(parcels, hub_name="X", weekly_capacity_tons=300, today=TODAY)
    assert fc.summary.overdue_tons == 180.0          # only the un-harvested overdue parcel counts
    assert fc.daily[0].harvest_tons_today == 180.0
    fc2 = build_forecast(parcels, hub_name="X", weekly_capacity_tons=300, today=TODAY, include_overdue=False)
    assert fc2.summary.active_parcels == 0


def test_weekly_buckets_do_not_overlap():
    fc = build_forecast([_parcel(1, 0), _parcel(1, 6), _parcel(1, 7)], hub_name="X",
                        weekly_capacity_tons=300, today=TODAY)
    assert [b.parcel_count for b in fc.weekly_buckets] == [2, 1]


# ----------------------------- API -----------------------------
@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:          # context manager triggers the startup (table creation)
        yield c


def test_end_to_end_flow(client):
    assert client.get("/health").json()["status"] == "ok"
    assert client.get("/api/congestion-forecast").status_code == 404      # no hub yet

    client.post("/api/hubs", json={"hub_name": "La Trinidad BAPTC", "weekly_absorption_limit_tons": 300})
    planting = (settings.today() - dt.timedelta(days=60)).isoformat()
    r = client.post("/api/parcels", json={
        "name": "Test plot", "owner": "Tester", "area_hectares": 2.5,
        "planting_date": planting, "latitude": 16.58, "longitude": 120.70})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["estimated_harvest_date"] and 0 < body["progress_pct"] < 100
    assert body["expected_tonnage"] == 45.0

    # polygon-only registration -> centroid derived
    poly = {"type": "Polygon", "coordinates": [[[120.69, 16.57], [120.71, 16.57], [120.71, 16.59], [120.69, 16.59], [120.69, 16.57]]]}
    r = client.post("/api/parcels", json={"name": "Poly", "owner": "T", "area_hectares": 1,
                                          "planting_date": planting, "geometry_geojson": poly})
    assert r.status_code == 201 and abs(r.json()["latitude"] - 16.58) < 0.01

    assert client.post("/api/parcels", json={"name": "x", "owner": "y", "crop_type": "durian", "area_hectares": 1,
                                             "planting_date": planting, "latitude": 16.5, "longitude": 120.7}).status_code == 422
    assert len(client.get("/api/parcels").json()) == 2

    fc = client.get("/api/congestion-forecast").json()
    assert len(fc["daily"]) == 14 and fc["hub_name"] == "La Trinidad BAPTC"


# --------------------- Open-Meteo client (mocked HTTP) ---------------------
def test_weather_service_live_path_with_mock_transport():
    import httpx
    from sqlmodel import Session, SQLModel, create_engine
    from sqlmodel.pool import StaticPool
    from app.services.weather_service import WeatherService

    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.host)
        p = request.url.params
        start, end = dt.date.fromisoformat(p["start_date"]), dt.date.fromisoformat(p["end_date"])
        days = [(start + dt.timedelta(days=i)).isoformat() for i in range((end - start).days + 1)]
        return httpx.Response(200, json={"daily": {
            "time": days,
            "temperature_2m_max": [22.0] * len(days),
            "temperature_2m_min": [10.0] * len(days)}})

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    from app.models import tables  # noqa: F401
    SQLModel.metadata.create_all(engine)
    client = httpx.Client(transport=httpx.MockTransport(handler))
    with Session(engine) as s:
        svc = WeatherService(s, mode="live", client=client)
        res = svc.get_daily_temperatures(16.58, 120.70, TODAY - dt.timedelta(days=20), TODAY + dt.timedelta(days=5), today=TODAY)
        assert not res.used_synthetic and len(res.temps) == 26
        assert {t.source for t in res.temps} == {"archive", "forecast"}
        assert sorted(set(calls)) == ["api.open-meteo.com", "archive-api.open-meteo.com"]
        n_first = len(calls)
        svc.get_daily_temperatures(16.58, 120.70, TODAY - dt.timedelta(days=20), TODAY + dt.timedelta(days=5), today=TODAY)
        assert len(calls) == n_first                     # second call served fully from the DB cache


def test_weather_falls_back_to_synthetic_when_api_down():
    import httpx
    from sqlmodel import Session, SQLModel, create_engine
    from sqlmodel.pool import StaticPool
    from app.services.weather_service import WeatherService

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    from app.models import tables  # noqa: F401
    SQLModel.metadata.create_all(engine)
    down = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(503)))
    with Session(engine) as s:
        res = WeatherService(s, mode="live", client=down).get_daily_temperatures(
            16.58, 120.70, TODAY - dt.timedelta(days=3), TODAY, today=TODAY)
        assert res.used_synthetic and len(res.temps) == 4
