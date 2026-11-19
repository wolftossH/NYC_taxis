"""Run the preprocessing notebook for one or more TLC months plus DOT traffic speeds for one or more days.

Runs cells 0-16 (settings -> download -> clean/export) of download_and_preprocess_taxi_(KV)_new.ipynb with the
settings below overridden; its later cells are metric exploration and are skipped.

Usage: python scripts/fetch_data.py [YYYY-MM[,YYYY-MM...]] [YYYY-MM-DD traffic day | FROM:TO day range]
  e.g. python scripts/fetch_data.py 2026-01,2026-02,2026-03,2026-04,2026-05,2026-06,2026-07,2026-08,2026-09 2026-09-15
       traffic only (taxi months already loaded are reused from cache):
       python scripts/fetch_data.py 2026-06,2026-07,2026-08,2026-09 2026-06-01:2026-09-30
       python scripts/fetch_data.py 2026-01,2026-02 2026-08-01:2026-11-15   (every traffic day, inclusive)
       ranges/days can be comma-separated, e.g. 2026-08-01:2026-10-11,2026-10-13:2026-11-15
       (DOT has no observations for 2026-10-12; the notebook stops on an empty day)
Output: data/taxi_clean_data/clean_handoff_<months>/taxi_trips.parquet, traffic_observations.parquet
Cached raw downloads are reused. After a new run, delete data/cube.parquet, data/od.parquet and
data/traffic_hourly.parquet so the server rebuilds them from the newest handoff.
"""
import subprocess
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

import nbformat

ROOT = Path(__file__).resolve().parents[1]
NOTEBOOK = ROOT / "download_and_preprocess_taxi_(KV)_new.ipynb"
PIPELINE_CELLS = 17  # cells 0-16: settings (cell 2) -> download -> clean/export
months = sorted((sys.argv[1] if len(sys.argv) > 1 else "2026-09").split(","))
traffic_day = sys.argv[2] if len(sys.argv) > 2 else f"{months[-1]}-15"
if ":" in traffic_day or "," in traffic_day:  # comma-separated days and/or FROM:TO inclusive ranges
    days = []
    for part in traffic_day.split(","):
        lo, hi = (date.fromisoformat(x) for x in (part.split(":") * 2)[:2])
        days += [str(lo + timedelta(i)) for i in range((hi - lo).days + 1)]
    traffic = f"TRAFFIC_MODE = 'days'\nTRAFFIC_DAYS = {days!r}\n"
else:
    traffic = f"TRAFFIC_MODE = 'day'\nTRAFFIC_DAY = {traffic_day!r}\n"
data_dir = ROOT / "data" / "taxi_clean_data"

nb = nbformat.read(NOTEBOOK, as_version=4)
nb.cells = nb.cells[:PIPELINE_CELLS]  # later cells are metric exploration
# The working notebook is edited by the team; fail loudly if the settings cell moved instead of silently
# running with the notebook's own defaults.
if not ("MONTHS =" in nb.cells[2].source and "TRAFFIC_MODE =" in nb.cells[2].source):
    sys.exit(f"{NOTEBOOK.name}: cell 2 is no longer the settings cell; update PIPELINE_CELLS / the cell index here")
overrides = (
    "\n# --- overrides from scripts/fetch_data.py ---\n"
    f"MONTHS = {months!r}\n"
    f"DATA_DIRECTORY = Path({str(data_dir)!r})\n"
    + traffic +
    "CREATE_ZIP = False\n"
)
nb.cells[2].source += overrides
for c in nb.cells:
    if c.cell_type == "code":
        c.outputs = []

tmp = Path(tempfile.mkdtemp()) / "run.ipynb"
nbformat.write(nb, tmp)
subprocess.check_call([sys.executable, "-m", "nbconvert", "--to", "notebook", "--execute",
                       "--ExecutePreprocessor.timeout=3600", str(tmp), "--output", "run_out.ipynb"])
found = list(data_dir.rglob("*.parquet"))
print("Parquet files:", *found, sep="\n  ")
