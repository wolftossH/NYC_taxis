# NYC Taxi Explorer ("Where to wait next")

A dashboard that helps yellow-cab drivers compare NYC taxi zones using historical TLC trip data: how many
pickups a zone gets, how much a driver earns per occupied minute, how long trips last, how often card
passengers tip, and where trips from a zone go. Zones that no other zone beats on both pickups and earnings are
flagged as Pareto-efficient.

The product spec is `Project document.pdf` (metric definitions are in its Appendix B).

## Features

- **Choropleth map** of taxi zones (D3), colored by the selected metric. Click a zone for details.
- **Pickups vs. rate scatter** (Observable Plot) with the Pareto frontier.
- **Hour, weekday and traffic charts** (Vega-Lite).
- **Destination breakdown** for a pickup zone (origin-destination probability).
- **Filters** for date range, weekdays, hour window and a minimum trip count per zone.

## Quick start (Windows)

```
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python scripts\fetch_data.py [YYYY-MM] [traffic-day]
.venv\Scripts\python -m uvicorn backend.main:app --port 8000
```

Then open http://localhost:8000.

- `fetch_data.py` runs the preprocessing notebook. Defaults are month `2026-09` and traffic day `2026-09-15`.
  Several months can be given comma-separated, e.g. `2026-06,2026-07,2026-08,2026-09 2026-09-15`.
  Raw downloads are cached and reused.
- On the first server start the backend builds `data/cube.parquet`, `data/od.parquet` and
  `data/traffic_hourly.parquet`, and downloads zone polygons from NYC Open Data to `data/zones.geojson`.
- After fetching new data, delete those three parquet files so the server rebuilds them from the newest hand-off.
- Restart uvicorn after any backend change. It runs without `--reload`.
- Optional check: `.venv\Scripts\python scripts\check_cube.py` compares cube totals against the raw parquet and must print `OK`.

## Project layout

| Path | Purpose |
|---|---|
| `download_and_preprocess_taxi_(KV).ipynb` | Downloads TLC and DOT data and cleans it. `scripts/fetch_data.py` runs cells 0-16 with overridden settings. |
| `scripts/fetch_data.py` | Runs the notebook for the chosen months and traffic day. |
| `scripts/check_cube.py` | Verifies the cube against the raw trips. |
| `backend/cube.py` | Builds the aggregates and recomputes rates for a selected period. |
| `backend/main.py` | FastAPI routes; also serves `frontend/` at `/`. |
| `backend/zones.py` | Downloads and caches zone polygons (WGS84). |
| `frontend/` | Static HTML, JS and CSS with no build step. D3, Observable Plot and Vega-Lite load from pinned CDN versions. |
| `taxi_data_model.html`, `taxi_database_design.html` | Design documents for the data model and a Postgres schema. |
| `data/` | Gitignored: raw and cleaned parquet, aggregates, zone GeoJSON. |

## Data

Everything lives under `data/` and is generated locally.

| File | Grain | Contents |
|---|---|---|
| `taxi_clean_data/raw/tlc/yellow_tripdata_<month>.parquet` | trip | Official TLC download, 21 columns. |
| `taxi_clean_data/raw/traffic/*.parquet` | link reading | Raw NYC DOT traffic speeds for the selected day. |
| `taxi_clean_data/clean_handoff_<months>/taxi_trips.parquet` | trip | 10 columns, valid trips only. For 2026-09: 3,635,168 of 3,876,044 rows kept. |
| `taxi_clean_data/clean_handoff_<months>/traffic_observations.parquet` | link reading | link_id, speed, travel_time, data_as_of, borough, link_name, link_points. |
| `cube.parquet` | date x hour x pickup zone | Additive sums used by the dashboard. |
| `od.parquet` | date x hour x pickup x dropoff | Trip counts for destination probabilities. |
| `traffic_hourly.parquet` | hour x borough | Mean speed and travel time for the traffic day. |
| `zones.geojson` | zone | 263 zone polygons with LocationID, zone name and borough. |

Cleaning keeps pickups inside their source month, zone IDs 1-263, durations of 1-240 minutes, distance above 0 and
up to 100 miles, and finite non-negative fare, tip, tolls and total amounts. Cash tips are not recorded by TLC.

## API

| Route | Returns |
|---|---|
| `GET /api/meta` | Available date range and settings. |
| `GET /api/zones` | Zone polygons. |
| `GET /api/summary` | Per-zone metrics and Pareto flags for `start`, `end`, `weekdays`, hour window and `min_trips` (default 30). |
| `GET /api/timeseries` | Hour, weekday and traffic series for the same filters. |
| `GET /api/od` | Destination breakdown for one pickup `zone`. |

## Metrics

The cube stores only additive quantities per (date, hour, pickup zone): trips, fare, tip, tolls, occupied minutes,
card trips and good-tip trips. Rates are recomputed from summed numerators and denominators for the selected
period, never by averaging stored rates.

- **Pickups per day** = trips / selected days.
- **Fare + tip rate** = (fare + tip) / occupied minutes. Tolls are excluded.
- **Average duration** = occupied minutes / trips. This is trip time, not the time spent driving to a pickup.
- **Good-tip %** = card trips with tip above 20% of fare / card trips (`payment_type == 1` only).
- **Attractiveness** = mean percentile rank of pickups per day and fare + tip rate. Zones below the minimum trip count are excluded.
- **Pareto-efficient** = no other eligible zone is better on both pickups per day and fare + tip rate.
- **Origin-destination probability** = trips from A to B / all trips from A.

## Limitations

- Traffic is a single DOT day, chosen independently of the taxi dates. It is not a same-day join, and there is no traffic-to-zone mapping, only a borough roll-up.
- Cash tips are unobserved, so tip metrics describe card trips only.
- Pickups are recorded service activity, not all passenger demand.
- Fare + tip per minute is a gross earnings proxy, not net profit.
