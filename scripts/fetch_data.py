"""Run the preprocessing notebook for one or more TLC months plus one DOT traffic day.

Usage: python scripts/fetch_data.py [YYYY-MM[,YYYY-MM...]] [YYYY-MM-DD traffic day]
  e.g. python scripts/fetch_data.py 2026-06,2026-07,2026-08,2026-09 2026-09-15
Output: data/taxi_clean_data/clean_handoff_<months>/taxi_trips.parquet, traffic_observations.parquet
Cached raw downloads are reused. After a new run, delete data/cube.parquet, data/od.parquet and
data/traffic_hourly.parquet so the server rebuilds them from the newest handoff.
"""
import subprocess
import sys
import tempfile
from pathlib import Path

import nbformat

ROOT = Path(__file__).resolve().parents[1]
NOTEBOOK = ROOT / "download_and_preprocess_taxi_(KV).ipynb"
months = sorted((sys.argv[1] if len(sys.argv) > 1 else "2026-09").split(","))
traffic_day = sys.argv[2] if len(sys.argv) > 2 else f"{months[-1]}-15"
data_dir = ROOT / "data" / "taxi_clean_data"

nb = nbformat.read(NOTEBOOK, as_version=4)
nb.cells = nb.cells[:17]  # settings -> download -> clean/export; later cells are exploration
overrides = (
    "\n# --- overrides from scripts/fetch_data.py ---\n"
    f"MONTHS = {months!r}\n"
    f"DATA_DIRECTORY = Path({str(data_dir)!r})\n"
    f"TRAFFIC_MODE = 'day'\nTRAFFIC_DAY = {traffic_day!r}\n"
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
