# data/

Everything in this folder except this README is gitignored. Each teammate pulls and cleans the data locally with
[`download_and_preprocess_taxi_(KV).ipynb`](../download_and_preprocess_taxi_(KV).ipynb) (in the repo root), and the
backend builds its aggregates from the result.

The notebook downloads NYC TLC yellow-taxi trips and one or more days of NYC DOT traffic speeds, cleans them with
bundled helper code (an embedded, SHA-256-checked zip, so don't reimplement or edit the cleaning), and writes a
"handoff" folder with two Parquet tables. The backend expects that folder under `data/taxi_clean_data/`.

## 1. Choose how to run the notebook

| Option | When to use |
| --- | --- |
| **A. Jupyter, local** | Normal case. Output lands straight in `data/`. |
| **B. Google Colab** | No local Python, or not enough disk to download. You copy the result into `data/` afterwards. |
| **C. `scripts/fetch_data.py`** | Same as A without opening Jupyter. It runs the notebook headless. |

All options run notebook cells 0-16 (settings -> download -> clean -> export). Cells 17 onwards are exploration
and are not needed for the dashboard.

## 2. Edit the settings cell (section "1. Settings")

| Setting | What to set | Notes |
| --- | --- | --- |
| `MONTHS` | e.g. `["2026-06", "2026-07", "2026-08", "2026-09"]` | Or `"latest"` with `RECENT_MONTH_COUNT = n` to take the newest n published months. Use an explicit list to make the run reproducible. |
| `DATA_DIRECTORY` | `Path("data/taxi_clean_data")` (local) | The default `Path.cwd() / "taxi_clean_data"` writes to the repo root, where the backend won't find it. |
| `TRAFFIC_MODE` | `"day"` | `"days"` with `TRAFFIC_DAYS = [...]` for several days, `"skip"` for no traffic, `"local"` with `TRAFFIC_FILE` for a saved file. |
| `TRAFFIC_DAY` | e.g. `"2026-09-15"` | Default is yesterday in NYC, which changes every run. Set a fixed date. |
| `EXISTING_TAXI_DIR` | `None` | Or a folder that already holds `yellow_tripdata_YYYY-MM.parquet` files, to skip downloads. |
| `INSTALL_DEPENDENCIES` | `False` if you use the repo venv | `True` pip-installs pandas, numpy, pyarrow and requests into the kernel. |
| `CREATE_ZIP` | `False` locally, `True` on Colab | The zip is only for moving the result between machines. |
| `USE_GOOGLE_DRIVE` | Colab only | `True` saves the output in your Drive (`GOOGLE_DRIVE_DIRECTORY`) so it survives a runtime reset. |

TLC publishes each month with a delay of about 2 months. If a selected month is not published yet, the download
cell stops with an error; it never falls back to other data. If the DOT request returns nothing for the traffic
day, pick another day or set `TRAFFIC_MODE = "skip"`.

## 3. Run it

### A. Jupyter, local

```
C:\HuyTrinh\Anaconda\python.exe -m venv .venv              # once; any Python 3.10+
.venv\Scripts\python -m pip install -r requirements.txt notebook
.venv\Scripts\python -m jupyter notebook                  # start from the repo root
```

Open the notebook, edit the settings cell as above, then run cells from the top through section
"8. Save and share the results".

### B. Google Colab

1. Upload the `.ipynb` to Colab (it is self-contained; no repo clone needed).
2. Edit the settings: keep `DATA_DIRECTORY` as is, set `CREATE_ZIP = True`, and either `USE_GOOGLE_DRIVE = True`
   or `DOWNLOAD_ZIP_IN_COLAB = True`.
3. Run cells through section 8 and download `clean_handoff_<months>.zip`.
4. Unzip it into `data/taxi_clean_data/`, so you get `data/taxi_clean_data/clean_handoff_<months>/taxi_trips.parquet`.

### C. Script

```
.venv\Scripts\python scripts\fetch_data.py 2026-06,2026-07,2026-08,2026-09 2026-09-15
```

Argument 1 is the comma-separated `MONTHS`, argument 2 the `TRAFFIC_DAY`. The script sets
`DATA_DIRECTORY = data/taxi_clean_data`, `TRAFFIC_MODE = "day"` and `CREATE_ZIP = False`, then executes cells 0-16.

## 4. Check the output

The run should end with "All handoff validation checks passed" and a table of row counts. You should now have:

```
data/taxi_clean_data/
  raw/tlc/yellow_tripdata_YYYY-MM.parquet          raw TLC downloads, reused on reruns (~60 MB/month)
  raw/traffic/nyc_traffic_speeds_YYYY-MM-DD.parquet raw DOT day(s)
  clean_handoff_<month>_<month>.../
    taxi_trips.parquet                             all selected months, cleaned (~60-70 MB/month)
    traffic_observations.parquet                   all selected traffic days
    README.md                                      cleaning rules for this run
    reports/                                       quality report and notebook_settings.json
    code/preprocessing_source.zip                  helper code used for this run
```

Rerunning with the same months replaces that handoff folder; copy it first if you want to keep the old one.

## 5. Load it into the dashboard

The backend reads the **most recently written** `clean_handoff_*` folder. Delete the cached aggregates so they are
rebuilt from it, start the server, and check the totals:

```
del data\cube.parquet data\od.parquet data\traffic_hourly.parquet
.venv\Scripts\python -m uvicorn backend.main:app --port 8000      # rebuild takes ~35 s on first start
.venv\Scripts\python scripts\check_cube.py                        # must print OK
```

If the server was already running, restart it. The backend also downloads the taxi zone polygons from NYC Open Data
into `data/zones.geojson` on first start.

## Disk space

Each extra month costs about 60 MB raw plus 60-70 MB cleaned, and each different month selection writes a new
`clean_handoff_*` folder. Old handoff folders and raw months you no longer need can be deleted; the backend only
reads the newest handoff.
