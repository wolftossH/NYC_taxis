# CLAUDE.md

NYC taxi demand dashboard ("Where to wait next"): helps yellow-cab drivers compare taxi zones by
historical demand, earning rate (fare + tip - tolls per minute), trip duration, card tipping and destinations, with Pareto analysis.
Team project; the spec is `Project document.pdf` (metric definitions in its Appendix B).

## Commands (Windows; use the venv)

```
:: TLC Jan-Sep 2026 + one traffic day (runs the notebook)
.venv\Scripts\python scripts\fetch_data.py 2026-01,2026-02,2026-03,2026-04,2026-05,2026-06,2026-07,2026-08,2026-09 2026-09-15

:: TLC Jan-Sep 2026 + traffic speeds for a range of days (FROM:TO, inclusive)
.venv\Scripts\python scripts\fetch_data.py 2026-01,2026-02,2026-03,2026-04,2026-05,2026-06,2026-07,2026-08,2026-09 2026-01-01:2026-09-30

:: Traffic speeds only: pass the taxi months already loaded (reused from cache) + the whole traffic range wanted
.venv\Scripts\python scripts\fetch_data.py 2026-06,2026-07,2026-08,2026-09 2026-06-01:2026-09-30

.venv\Scripts\python scripts\check_cube.py                           # cube totals vs raw parquet (must print OK)
.venv\Scripts\python -m uvicorn backend.main:app --port 8000         # API + static frontend
```

- Traffic ranges set the notebook's `TRAFFIC_MODE = "days"` and download one NYC DOT file per day into
  `data/taxi_clean_data/raw/traffic/` (~0.25 MB each, cached and reused). Aug 1 onwards is already cached.
- Traffic only: there is no "skip taxi" switch. Raw TLC files of the listed months are reused (no download), the taxi
  cleaning reruns, and only missing traffic days are downloaded. Always pass the full loaded month list and the full
  traffic range you want: the backend reads only the newest handoff folder, and that folder holds just the months and
  traffic days of the last run, so a shorter list shrinks the dashboard's data.
- System Python is `C:\HuyTrinh\Anaconda\python.exe`; `python` on PATH is the Store stub. Node is not installed.
- uvicorn runs without `--reload`: restart the server after any backend change. An old server still serves the new
  frontend from disk, so new endpoints just 404 (this caused the "map click does nothing" bug).
- Loaded data: Jan-Sep 2026 (31.6M trips, 273 days) + DOT traffic 2026-08-01 to 2026-11-15 (106 days; DOT has none
  for 10-12). `clean_handoff_2026-01_to_2026-09` was made by streaming the Jan-May and Jun-Sep handoffs' taxi_trips into
  one file (running all 9 months through the notebook at once risks running out of RAM). The backend reads the most recently written
  `data/taxi_clean_data/clean_handoff_*` folder. Delete `data/cube.parquet`, `data/od.parquet`,
  `data/traffic_hourly.parquet` and restart the server after fetching new data (rebuild takes ~40 s). `Store` also
  rebuilds by itself when the cached cube lacks any column in `cube.SUMS` or the traffic cache lacks `speed_sum`, so
  adding a cube column needs no manual cache delete. Don't start a second server (or `check_cube.py`) while one is
  rebuilding: it reads the half-written parquet and fails with "Parquet magic bytes not found".
- If port 8000 is already taken by the user's server, test on another port (`--port 8001`) rather than killing it.
- Free disk on C: is tight (~14 GB); each extra month is ~60 MB raw plus ~60 MB cleaned.
- `data/` is gitignored (raw + cleaned parquet, caches, zones GeoJSON).

## Layout

- `download_and_preprocess_taxi_(KV)_new.ipynb` - the notebook in use. Cells 0-16 download TLC + DOT data and clean it
  with an embedded, SHA-checked helper zip; `fetch_data.py` runs only those, with cell 2's settings overridden, and exits
  if cell 2 stops being the settings cell. Cells 17-28 are metric exploration (duration, profitability index, tip %,
  zone names, pickup activity, card tipping by PU/DO zone, OD probability) and the reference for metric definitions.
  Don't reimplement cleaning; keep cells 0-16 in place when editing the notebook.
- `download_and_preprocess_taxi_(KV).ipynb` - older copy with identical cells 0-16; no longer used by any script.
- `taxi_metrics.html` - standalone report of the notebook's metrics for Aug-Sep 2026 (data embedded, opens offline).
  Generated outside the repo; it does not update when the data changes.
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

- **Store additive quantities only, recompute rates.** The cube is (date, 15-minute slot, zone) with counts, sums and
  occupied minutes; `slot = hour*4 + minute//15` (0-95). Pickup columns are keyed by pickup time/zone; `dropoffs`,
  `do_card_trips` and `do_good_tip_trips` by dropoff time/zone (trips ending after the last loaded day are dropped). `rates()` divides summed numerators by
  summed denominators. Never average precomputed rates.
- Time-of-day filters are inclusive slot ranges (`slot_from`/`slot_to`); the API still accepts `hour_from`/`hour_to`.
- Metric definitions follow the PDF and the notebook: pickups/day = pickups / selected days; earning rate `rate` =
  profitability index = (fare + tip - tolls) / occupied minutes; tip propensity uses card trips only
  (`payment_type == 1`), by pickup zone (`tip_rate`) and by dropoff zone/time (`do_tip_rate`); avg card tip
  `card_tip_pct` = card tips / card fares; OD probability = A->B / all from A.
  Not in the PDF: dropoffs/day = dropoffs / selected days; pickups per dropoff = pickups / dropoffs.
- Where the notebook averages per-trip ratios (avg tip %, avg profitability per zone), the dashboard uses ratios of
  totals, so its numbers differ slightly from the notebook's (e.g. avg card tip 22.6% vs 24.9%). That is intended.
- OD probability: `/api/od` returns the top 10 destinations plus `all` (every destination). The map metric `od_prob`
  is frontend-only (not in `/api/summary`): it colours the map from `od.all` and needs a selected zone.
- Pareto frontier maximises pickups/day and earning rate among zones meeting `min_trips`; computed server-side.
- Traffic (`traffic_hourly.parquet`, despite the name) is additive per (date, 15-min slot, borough): `speed_sum`, `n`.
  The indexed chart uses only selected days that have both taxi and traffic data, for BOTH lines (`rate_by_slot` is
  recomputed over those days), plus the selected zone's borough. Traffic starts 2026-08-01 (none for 2026-10-12).
  When the selection has no traffic days it falls back to all traffic days and the UI must say it is not a same-day match.

## Frontend conventions

- Charts follow the dataviz skill: no dual-axis charts (index to a common base instead), one-hue sequential map ramp,
  categorical colours only from the validated slots (`--s1` blue, `--s2` orange), tooltips everywhere, table view.
- Visual identity: taxi-yellow header band and a dark "meter" stat bar are the only bold elements; Archivo
  (variable width) for all text; flat panels. Yellow is chrome only, never a data colour.
- All colours are CSS tokens in `style.css`, with dark values under both the media query and `[data-theme="dark"]`.
  Read them in JS via `css("--token")`; re-render on theme change.
- Insert data-derived text with `textContent` (the `el()` helper), not `innerHTML`.
- Deep link for testing: `/?zone=161&compare=236&t0=16:00&t1=20:00&metric=od_prob` (`t1` is exclusive; old
  `h0=16&h1=19` still works; `metric` is any key of `METRICS` in `app.js`).
- A new metric needs: a cube column in `SUMS` (if new data), a formula in `rates()`, the field in `zone_summary`'s
  `cols`/`totals`, an entry in `METRICS`, and the tooltip / compare-table / footer text. `check_cube.py` should check it
  against the raw parquet.

## Verifying UI changes

Headless Edge screenshots work (Edge enforces a ~500px minimum width, so test mobile at 500):

```
"/c/Program Files (x86)/Microsoft/Edge/Application/msedge.exe" --headless=new --disable-gpu \
  --window-size=1400,1900 --virtual-time-budget=20000 --screenshot="<abs path>.png" http://localhost:8000/
```

Add `--force-dark-mode` for dark mode. Bash heredocs with nested quotes are fragile here; write patch scripts to a
file instead.
