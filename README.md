# AgroCast – Backend Prototype (Step 1)

Predictive early-warning API for highland vegetable gluts. It forecasts each parcel's harvest date from
**Growing Degree Days** (real weather + forecast) and computes a rolling 7-day **Supply Congestion Index**
against a trading hub's capacity.

```
GDD_day = max((Tmax + Tmin)/2 - 4.4, 0)        harvest = first day cumulative GDD >= 1200   (cabbage)
CI_w    = sum(area_ha * 18 t/ha for parcels harvesting in window w) / weekly_hub_capacity
CI > 1.20  -> HIGH_GLUT   |   1.00 < CI <= 1.20 -> WARNING   |   else NORMAL
```

## Project layout
```
agrocast/
├── app/
│   ├── main.py                    # FastAPI app + startup (creates tables)
│   ├── config.py                  # Settings + CROP_PROFILES (T_base, target GDD, yield)
│   ├── database.py                # SQLModel engine / session dependency
│   ├── models/
│   │   ├── tables.py              # Parcel, DailyWeatherLog, TradingHubCapacity
│   │   └── schemas.py             # Pydantic request/response models
│   ├── services/
│   │   ├── weather_service.py     # Open-Meteo archive+forecast client, DB cache, offline fallback
│   │   ├── gdd_engine.py          # GDD accumulation & harvest-date projection (pure core + I/O wrapper)
│   │   ├── congestion_service.py  # Rolling-window CI + alerts (pure, no I/O)
│   │   └── ndvi_service.py        # OPTIONAL Google Earth Engine NDVI (decoupled; Null provider by default)
│   └── routers/
│       ├── parcels.py             # POST/GET /api/parcels, POST /api/parcels/{id}/harvest
│       └── congestion.py          # /api/hubs, GET /api/congestion-forecast
├── scripts/seed_data.py           # Mock hub + 20 Atok parcels
├── tests/                         # pytest suite (offline)
├── requirements.txt
└── .env.example
```

## Install & run
Requires Python 3.10+.
```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

python -m scripts.seed_data --reset            # seed (uses live Open-Meteo; falls back to synthetic if offline)
python -m scripts.seed_data --reset --offline  # or: never touch the network

uvicorn app.main:app --reload
```
Open **http://127.0.0.1:8000/docs** for the interactive Swagger UI.

Run the tests: `python -m pytest -q`

## Endpoints
| Method | Path | Purpose |
|---|---|---|
| POST | `/api/parcels` | Register a plot (lat/lon **or** GeoJSON polygon); harvest date auto-computed |
| GET | `/api/parcels` | List parcels with `progress_pct` (of 1200 GDD), `days_to_harvest`, `expected_tonnage` |
| POST | `/api/parcels/{id}/harvest` | Mark harvested (removes it from projected supply) |
| GET/POST | `/api/hubs` | List / create trading hubs |
| GET | `/api/congestion-forecast?hub_name=&horizon_days=14&refresh=false` | Daily sliding-window + weekly-bucket CI, alert status, summary |

```bash
curl -X POST localhost:8000/api/parcels -H 'Content-Type: application/json' -d '{
  "name":"Kuya Ben plot","owner":"Ben Molintas","area_hectares":1.5,
  "planting_date":"2026-07-01","latitude":16.58,"longitude":120.70}'
curl localhost:8000/api/congestion-forecast
```

## How the harvest date is estimated
1. Days from planting → ~7 days ago: **Open-Meteo archive** (confirmed ERA5).
2. Last week → +14 days: **Open-Meteo forecast**.
3. Beyond that: **climatology extrapolation** (mean GDD/day of the trailing 30 days).
`harvest_basis` on each parcel tells you which was used: `observed_forecast` (high confidence)
or `climatology` (lower confidence). Estimates are refreshed on `GET /api/parcels` and
`GET /api/congestion-forecast?refresh=true`.

Weather rows are cached in `DailyWeatherLog` (archive rows are permanent; forecast rows expire after 6 h),
so 20 parcels in the same area cost only a couple of API calls.

## Configuration (env vars, all optional – see `.env.example`)
`AGROCAST_DATABASE_URL`, `AGROCAST_WEATHER_MODE` (`live`|`synthetic`), `AGROCAST_GEE_ENABLED`, `AGROCAST_GEE_PROJECT`.
Crop parameters and alert thresholds live in `app/config.py` – add a crop by adding a `CropProfile`.

## Working with the GitHub repo (team workflow)
This project lives at `https://github.com/AgroCast-CAO/AgroCast`. A few notes before the steps below:
* `.venv/`, `*.db`, and `__pycache__/` are excluded via `.gitignore` — never commit these. Everyone creates
  their own local `.venv` and database; only the source code (`app/`, `tests/`, `scripts/`, config files) is shared.
* Treat `main` as always-working. Do your changes on a separate branch and merge back via a Pull Request (PR)
  rather than committing straight to `main`, so a half-finished feature never breaks the whole team's copy.

### First time on a new machine (clone)
```bash
git clone https://github.com/AgroCast-CAO/AgroCast.git
cd AgroCast
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python -m pytest -q                # confirm everything works before you start changing anything
```

### Daily workflow
1. **Pull the latest changes** before you start working, so you're not building on stale code:
   ```bash
   git checkout main
   git pull
   ```
2. **Create a branch** for whatever you're working on (one branch per feature/fix):
   ```bash
   git checkout -b your-name/short-description     # e.g. izzy/hub-assignment
   ```
3. **Make your changes** in VS Code as normal.
4. **Check what changed** before committing:
   ```bash
   git status                        # which files changed
   git diff                          # the actual line-by-line changes
   ```
5. **Stage and commit**:
   ```bash
   git add .
   git commit -m "Short description of what you did"
   ```
   Commit often, in small logical chunks, rather than one giant commit at the end of the day.
6. **Push your branch** to GitHub:
   ```bash
   git push -u origin your-name/short-description
   ```
   (After the first push, later pushes on that branch just need `git push`.)
7. **Open a Pull Request**: go to the repo on GitHub, you'll see a banner offering to "Compare & pull request"
   for your branch — click it, add a short description of what changed and why, and submit.
8. **Get it reviewed and merged**: a teammate reviews the PR on GitHub, leaves comments if needed, and merges
   it into `main` once it looks good. GitHub's web UI handles the merge — you don't need any local commands for this part.
9. **Clean up** after your branch is merged:
   ```bash
   git checkout main
   git pull                          # bring the merged changes down
   git branch -d your-name/short-description
   ```

### Common situations
| Situation | What to do |
|---|---|
| `git pull` says "Your local changes would be overwritten" | Commit or stash your work first: `git stash`, then `git pull`, then `git stash pop` |
| Merge conflict when pulling or merging a PR | VS Code will mark the conflicting lines in the file with `<<<<<<<` / `=======` / `>>>>>>>` — edit the file to keep the correct version, then `git add .` and `git commit` |
| You committed something you shouldn't have (like a `.db` file) | `git rm --cached filename`, make sure it's in `.gitignore`, then commit again |
| You want to see who changed what | `git log --oneline` for a compact history, or check the "commits" tab on GitHub |
| A teammate needs access | Repo → **Settings → Collaborators → Add people**, invite by GitHub username or email |

### Adding new Python packages
If you `pip install` something new, add it to `requirements.txt` so teammates get it too:
```bash
pip freeze > requirements.txt      # regenerates the file from what's installed in your .venv
```
Review the diff before committing — `pip freeze` can pull in extra sub-dependencies you don't need to pin.

## Using PostgreSQL/PostGIS
1. `pip install psycopg2-binary`, then `AGROCAST_DATABASE_URL=postgresql+psycopg2://user:pass@localhost/agrocast`
2. Everything works as-is (lat/lon floats + GeoJSON text).
3. To get true spatial queries later: `pip install geoalchemy2`, `CREATE EXTENSION postgis;`, add a
   `geom = Column(Geometry("POLYGON", srid=4326))` to `Parcel`, and use Alembic for the migration.

## Optional: Google Earth Engine NDVI
`pip install earthengine-api && earthengine authenticate`, then set `AGROCAST_GEE_ENABLED=true` and
`AGROCAST_GEE_PROJECT=<project>`. Use `get_ndvi_provider().get_ndvi_series(lat, lon, start, end)`.
Without credentials you get a `NullNDVIProvider`, so the core system is unaffected.
(This module is untested here because it needs live GEE credentials — test it locally.)

## Assumptions & limitations (worth discussing in your capstone paper)
* **All parcels supply the selected hub** – there is no parcel→hub assignment yet.
* **Harvest is treated as a single day** (the day GDD hits 1200). Real harvests spread over ~1–2 weeks; a
  harvest-window spread (e.g. spread tonnage over ±N days) is an easy next improvement.
* **Overdue mature parcels are folded into "today"** and reported as `overdue_tons`.
* Open-Meteo is gridded (~10 km) and doesn't capture microclimate/elevation exactly; validate against a local
  weather station or PAGASA data. Cabbage `T_base`, 1200 GDD and 18 t/ha are your given parameters – calibrate with field data.
* `synthetic` weather is for development/tests only and is flagged via `used_synthetic_weather` internally; don't use it for real advisories.
* No authentication yet, and schema changes need Alembic migrations once you have real data.
