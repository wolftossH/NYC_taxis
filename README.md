# NYC Taxi Explorer ("Where to wait next")

A dashboard that helps yellow-cab drivers compare NYC taxi zones using historical TLC trip data: how many
pickups a zone gets, how much a driver earns per occupied minute, how long trips last, how often card
passengers tip, and where trips from a zone go. Zones that no other zone beats on both pickups and earnings are
flagged as Pareto-efficient.

The product spec is `Project document.pdf`. Metric definitions are in its Appendix B.

## What you get

- **Choropleth map** of taxi zones (D3), colored by the selected metric. Click a zone for details.
- **Pickups vs. rate scatter** (Observable Plot) with the Pareto frontier.
- **Time-of-day (15-minute), weekday and traffic charts** (Vega-Lite), including pickups vs. dropoffs per day.
- **Destination breakdown** for a pickup zone (origin-destination probability).
- **Filters** for date range, weekdays, time of day in 15-minute steps and a minimum trip count per zone.

## Setup, step by step

Run every command from the project root (the folder that contains `backend/` and `scripts/`). Steps 2 and 3
differ between Windows and macOS/Linux. After the environment is active, the commands are the same on both.

### Step 1. Check prerequisites

- Python 3 on your PATH (`python --version`).
- An internet connection. Step 5 downloads TLC trip data and NYC DOT traffic data, and Step 6 downloads zone
  polygons from NYC Open Data.

### Step 2. Create a virtual environment

```
# Windows
python -m venv .venv

# macOS / Linux
python3 -m venv .venv
```

### Step 3. Activate the virtual environment

```
# Windows (Command Prompt)
.venv\Scripts\activate.bat

# Windows (PowerShell)
.venv\Scripts\Activate.ps1

# macOS / Linux
source .venv/bin/activate
```

- Your prompt now starts with `(.venv)`. From here on, `python` and `pip` refer to the project environment.
- Activate it again in every new terminal before Steps 4-9.
- If PowerShell blocks the script, run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once and try again.
- Run `deactivate` to leave the environment.

### Step 4. Install dependencies

```
pip install -r requirements.txt
```

### Step 5. Download and clean a first month

The server needs at least one cleaned folder before it can start, so the first load is done on the command line.
Start small: one month is enough. You add more months and traffic days from the dashboard in Step 8.

```
python scripts/fetch_data.py 2026-09 2026-09-15
```

The general form is:

```
python scripts/fetch_data.py [MONTHS] [TRAFFIC-DAYS]
```

- `MONTHS`: one or more TLC months as `YYYY-MM`, separated by commas.
- `TRAFFIC-DAYS`: one NYC DOT day (`YYYY-MM-DD`) or a range `FROM:TO` with both days included.
- With no arguments it uses month `2026-09` and traffic day `2026-09-15`.
- The script runs the preprocessing notebook and writes a cleaned hand-off to
  `data/taxi_clean_data/clean_handoff_<months>/`.
- Raw downloads are cached and reused on the next run. TLC files go to `data/taxi_clean_data/raw/tlc/`, and
  traffic files go to `data/taxi_clean_data/raw/traffic/`, one file per day (about 0.25 MB each).

> **Always list every month and the whole traffic range you want.** The dashboard reads only the newest cleaned
> folder, and that folder holds only what the last run asked for. A shorter list shrinks the dashboard's data.
> Cached files make re-listing cheap.

**Example A: several months, traffic for one day**

TLC trips for January to September 2026, plus traffic speeds for 15 September:

```
python scripts/fetch_data.py 2026-01,2026-02,2026-03,2026-04,2026-05,2026-06,2026-07,2026-08,2026-09 2026-09-15
```

**Example B: several months, traffic for a range of days**

The same months, with traffic speeds for every day from 1 January to 30 September. Each day is a separate DOT
download:

```
python scripts/fetch_data.py 2026-01,2026-02,2026-03,2026-04,2026-05,2026-06,2026-07,2026-08,2026-09 2026-01-01:2026-09-30
```

**Example C: add traffic only, for months you already have**

There is no switch to skip taxi data. Instead, list the taxi months you already downloaded: their raw files come
from cache, and only the missing traffic days are downloaded. This adds June-September traffic to June-September
taxi data:

```
python scripts/fetch_data.py 2026-06,2026-07,2026-08,2026-09 2026-06-01:2026-09-30
```

Large lists like Examples A and B run every month through the notebook at once and need a lot of RAM. Prefer
loading one month here and adding the rest in Step 8, which cleans only new months and merges them.

If the server is already running when you fetch on the command line, follow "Load different months or traffic
days (command line)" under [Common tasks](#common-tasks) so the server rebuilds its aggregates.

### Step 6. Start the server

```
python -m uvicorn backend.main:app --port 8000
```

The first start takes longer. The backend builds `data/cube.parquet`, `data/od.parquet` and
`data/traffic_hourly.parquet`, and downloads zone polygons to `data/zones.geojson`.

### Step 7. Open the dashboard

Go to http://localhost:8000.

### Step 8. Load more months and traffic days from the dashboard

With the server running:

1. Click **Data** in the yellow header (or open http://localhost:8000/?panel=data).
2. Tick the taxi months you want shown. Ticked months are the result: tick to add a month, untick to remove one.
   Months with a dot are already downloaded; greyed-out months are not published by TLC yet.
3. Set the traffic range (from/to, up to yesterday). The presets fill in the loaded range, the taxi months' range
   or "up to yesterday".
4. Check the summary line (what will be added, removed and downloaded, and free disk), then click
   **Download and load**.
5. Wait for the panel to finish. It shows each stage (check traffic days, download and clean, merge, rebuild) with
   live counts and a log. You can close it while it runs; the header button reads "Data · loading…".

The dashboard keeps showing the current data until the new data is ready, then refreshes by itself. No server
restart is needed. Each new month adds about 120 MB on disk (raw plus cleaned), so watch the free-disk figure.

### Step 9 (optional). Verify the aggregates

In a second terminal, activate the environment (Step 3) and run:

```
python scripts/check_cube.py
```

It compares cube totals against the raw parquet and must print `OK`. Don't run it while a data load is rebuilding.

## Common tasks

Activate the environment first (Step 3) if this is a new terminal.

**Load data from the dashboard (easiest)**

Follow [Step 8](#step-8-load-more-months-and-traffic-days-from-the-dashboard). The same panel also removes months:
untick them and click **Download and load**.

What it does for you:

- Downloads and cleans **only new months** (through `scripts/fetch_data.py`) and merges them with the months
  already loaded, so nothing is re-cleaned and memory use stays low.
- Asks DOT which days have no traffic data and skips them (for example 2026-10-12), instead of failing.
- Retries the download up to 3 times; cached files make each retry resume.
- If anything fails or you cancel, the previous data stays loaded.
- After a successful load it deletes the older `clean_handoff_*` folders, so only the one in use is kept. Raw
  downloads in `raw/tlc` and `raw/traffic` are kept, so a removed month reloads without downloading again.

The command line below still works and does the same as the panel.

**Load different months or traffic days (command line)**

1. Stop the server (Ctrl+C).
2. Run Step 5 with every month and the whole traffic range you want (see the examples there).
3. Delete `data/cube.parquet`, `data/od.parquet` and `data/traffic_hourly.parquet`.
4. Start the server again (Step 6). It rebuilds the three files from the newest hand-off.

**After changing backend code**

Stop and restart uvicorn (Step 6). It runs without `--reload`, so changes are not picked up automatically.
Frontend files are static and only need a browser refresh.

## Project layout

| Path | Purpose |
|---|---|
| `download_and_preprocess_taxi_(KV)_new.ipynb` | Downloads TLC and DOT data and cleans it (cells 0-16), then explores the metrics (cells 17-28). `scripts/fetch_data.py` runs cells 0-16 with overridden settings. |
| `download_and_preprocess_taxi_(KV).ipynb` | Older copy of the notebook; cells 0-16 are identical. Not used by the scripts. |
| `scripts/fetch_data.py` | Runs the notebook for the chosen months and traffic day or range. |
| `scripts/check_cube.py` | Verifies the cube against the raw trips. |
| `backend/cube.py` | Builds the aggregates and recomputes rates for a selected period. |
| `backend/main.py` | FastAPI routes. Also serves `frontend/` at `/`. |
| `backend/zones.py` | Downloads and caches zone polygons (WGS84). |
| `backend/datajobs.py` | The dashboard's data panel: runs a background load (DOT day check, fetch, merge, rebuild) and swaps in the new data. |
| `frontend/` | Static HTML, JS and CSS with no build step. D3, Observable Plot and Vega-Lite load from pinned CDN versions. |
| `taxi_data_model.html`, `taxi_database_design.html` | Design documents for the data model and a Postgres schema. |
| `data/` | Gitignored: raw and cleaned parquet, aggregates, zone GeoJSON. |

## Data files

Everything lives under `data/` and is generated locally by Steps 5 and 6.

| File | Grain | Contents |
|---|---|---|
| `taxi_clean_data/raw/tlc/yellow_tripdata_<month>.parquet` | trip | Official TLC download, 21 columns. |
| `taxi_clean_data/raw/traffic/*.parquet` | link reading | Raw NYC DOT traffic speeds, one file per day (cached). |
| `taxi_clean_data/clean_handoff_<months>/taxi_trips.parquet` | trip | 10 columns, valid trips only. For 2026-09: 3,635,168 of 3,876,044 rows kept. |
| `taxi_clean_data/clean_handoff_<months>/traffic_observations.parquet` | link reading | link_id, speed, travel_time, data_as_of, borough, link_name, link_points. |
| `cube.parquet` | date x 15-min slot x zone | Additive sums used by the dashboard: pickup sums by pickup time and zone, `dropoffs` by dropoff time and zone. |
| `od.parquet` | date x 15-min slot x pickup x dropoff | Trip counts for destination probabilities. |
| `traffic_hourly.parquet` | date x 15-min slot x borough | Additive `speed_sum` and `n` (readings); mean speed = sum / n for any selection. |
| `zones.geojson` | zone | 263 zone polygons with LocationID, zone name and borough. |

Cleaning keeps trips that:

- start inside their source month
- have zone IDs 1-263
- last 1-240 minutes
- have a distance above 0 and up to 100 miles
- have finite, non-negative fare, tip, tolls and total amounts

## API

| Route | Returns |
|---|---|
| `GET /api/meta` | Available date range and settings. |
| `GET /api/zones` | Zone polygons. |
| `GET /api/summary` | Per-zone metrics and Pareto flags for `start`, `end`, `weekdays`, time window and `min_trips` (default 30). |
| `GET /api/timeseries` | 15-minute, weekday and traffic series for the same filters (`traffic`: speed and earning rate per slot on the selected days that have traffic data, limited to the zone's borough when `zone` is set). |
| `GET /api/od` | Destination breakdown for one pickup `zone`. |
| `GET /api/data/status` | Loaded months and traffic days, cached raw files, free disk and the state of the data-load job. |
| `GET /api/data/available` | TLC months published since 2024-01 (checked once an hour). |
| `POST /api/data/fetch` | Start a data load: `{"months": [...], "traffic_from": "YYYY-MM-DD", "traffic_to": "YYYY-MM-DD"}`. 202, 409 if one is running, 422 on bad input. |
| `POST /api/data/cancel` | Cancel the running data load. |

## Metrics

The cube stores only additive quantities per (date, 15-minute slot, zone): trips, fare, tip, tolls, occupied minutes,
card trips, good-tip trips, card fares and card tips (by pickup), and dropoffs, card trips and good-tip trips by dropoff time and zone. Rates are recomputed from summed numerators and denominators for the selected
period, never by averaging stored rates.

- **Pickups per day** = trips / selected days.
- **Earning rate (profitability index)** = (fare + tip − tolls) / occupied minutes.
- **Average duration** = occupied minutes / trips. This is trip time, not the time spent driving to a pickup.
- **Good-tip %** = card trips with tip above 20% of fare / card trips (`payment_type == 1` only). Shown by pickup zone and, separately, by dropoff zone (keyed by dropoff time).
- **Average card tip %** = card tips / card fares. A ratio of totals, so it differs slightly from the notebook's mean of per-trip tip percentages.
- **Attractiveness** = mean percentile rank of pickups per day and earning rate. Zones below the minimum trip count are excluded.
- **Pareto-efficient** = no other eligible zone is better on both pickups per day and earning rate.
- **Origin-destination probability** = trips from A to B / all trips from A (top 10 destinations shown).
- **Dropoffs per day** = trips ending in the zone during the selected time window / selected days.
- **Pickups per dropoff** = pickups / dropoffs. Above 1, more fares start in the zone than end there.

## Limitations

- Traffic speeds start on 2026-08-01 (no DOT data for 2026-10-12; 2026-10-13 is partial). The indexed chart compares the selected days that have both taxi and traffic data; when there are none it falls back to all traffic days, which compares daily shapes rather than the same days. There is no traffic-to-zone mapping, only a borough roll-up.
- Cash tips are unobserved, so tip metrics describe card trips only.
- Pickups are recorded service activity, not all passenger demand.
- The earning rate deducts tolls but not fuel or other costs, and ignores empty driving between fares.
- A single 15-minute window holds few trips; use the minimum trip count or a wider window when results look noisy.
