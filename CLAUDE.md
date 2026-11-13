# CLAUDE.md

NYC taxi demand dashboard ("Where to wait next"): helps yellow-cab drivers compare taxi zones by
historical demand, fare+tip rate, trip duration, card tipping and destinations, with Pareto analysis.
Team project; the spec is `Project document.pdf` (metric definitions in its Appendix B).

## Commands (Windows; use the venv)

```
.venv\Scripts\python scripts\fetch_data.py 2026-06,2026-07,2026-08,2026-09 2026-09-15   # runs the notebook
.venv\Scripts\python scripts\check_cube.py                           # cube totals vs raw parquet (must print OK)
.venv\Scripts\python -m uvicorn backend.main:app --port 8000         # API + static frontend
```

- System Python is `C:\HuyTrinh\Anaconda\python.exe`; `python` on PATH is the Store stub. Node is not installed.
- uvicorn runs without `--reload`: restart the server after any backend change. An old server still serves the new
  frontend from disk, so new endpoints just 404 (this caused the "map click does nothing" bug).
- Loaded data: Jun-Sep 2026 (13.7M trips, 122 days) + one traffic day. The backend reads the most recently written
  `data/taxi_clean_data/clean_handoff_*` folder. Delete `data/cube.parquet`, `data/od.parquet`,
  `data/traffic_hourly.parquet` and restart the server after fetching new data (rebuild takes ~35 s).
- Free disk on C: is tight (~14 GB); each extra month is ~60 MB raw plus ~60 MB cleaned.
- `data/` is gitignored (raw + cleaned parquet, caches, zones GeoJSON).

## Layout

- `download_and_preprocess_taxi_(KV).ipynb` - downloads TLC + DOT data and cleans it with an embedded, SHA-checked
  helper zip. Don't reimplement cleaning; `fetch_data.py` runs cells 0-16 with overridden settings.
- `backend/cube.py` - builds and queries the aggregates. `backend/main.py` - FastAPI routes
  (`/api/meta`, `/api/zones`, `/api/basemap`, `/api/summary`, `/api/timeseries`, `/api/od`) and serves `frontend/` at `/`.
- `backend/zones.py` - downloads/caches the zone polygons from NYC Open Data (WGS84).
- `backend/basemap.py` - map backdrop: land outside NYC from Census cartographic counties (shoreline-clipped), read with
  stdlib only and cached as `data/basemap.geojson` (`/api/basemap`). Water is just the map background. Delete the cache
  to rebuild; if the download fails the map still works with plain water.
- `frontend/` - static, no build step. D3 (choropleth), Observable Plot (Pareto scatter), Vega-Lite (time charts),
  loaded from pinned CDN versions.
- `taxi_data_model.html`, `taxi_database_design.html` - design docs for the data model / a Postgres schema.

## Rules that matter

- **Store additive quantities only, recompute rates.** The cube is (date, hour, pickup zone) with counts, sums and
  occupied minutes; `rates()` divides summed numerators by summed denominators. Never average precomputed rates.
- Metric definitions follow the PDF: pickups/day = pickups / selected days; fare+tip rate = (fare + tip) / occupied
  minutes (tolls excluded); tip propensity uses card trips only (`payment_type == 1`); OD probability = A->B / all from A.
- Pareto frontier maximises pickups/day and fare+tip rate among zones meeting `min_trips`; computed server-side.
- Traffic is one DOT day, independent of taxi dates. Say so in the UI; don't present it as a same-day join.

## Frontend conventions

- Charts follow the dataviz skill: no dual-axis charts (index to a common base instead), one-hue sequential map ramp,
  categorical colours only from the validated slots (`--s1` blue, `--s2` orange), tooltips everywhere, table view.
- Visual identity: taxi-yellow header band and a dark "meter" stat bar are the only bold elements; Archivo
  (variable width) for all text; flat panels. Yellow is chrome only, never a data colour.
- All colours are CSS tokens in `style.css`, with dark values under both the media query and `[data-theme="dark"]`.
  Read them in JS via `css("--token")`; re-render on theme change.
- Insert data-derived text with `textContent` (the `el()` helper), not `innerHTML`.
- Deep link for testing: `/?zone=161&compare=236&h0=16&h1=19`.

## Verifying UI changes

Headless Edge screenshots work (Edge enforces a ~500px minimum width, so test mobile at 500):

```
"/c/Program Files (x86)/Microsoft/Edge/Application/msedge.exe" --headless=new --disable-gpu \
  --window-size=1400,1900 --virtual-time-budget=20000 --screenshot="<abs path>.png" http://localhost:8000/
```

Add `--force-dark-mode` for dark mode. Bash heredocs with nested quotes are fragile here; write patch scripts to a
file instead.
