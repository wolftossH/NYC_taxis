"""Sanity checks: cube totals must match the raw parquet, over whatever period is loaded."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pyarrow.parquet as pq

from backend import cube

s = cube.Store()
meta = s.meta()
print(meta)
lo, hi = meta["min_date"], meta["max_date"]
full = cube.zone_summary(s, lo, hi, list(range(7)), 0, 23, 30)
print("days", full["n_days"], "totals", full["totals"])
print("pareto zones", sum(z["pareto"] for z in full["zones"]), "of", len(full["zones"]))

# Stream the raw file in batches so the check works for multi-month data too.
n = fare_tip = minutes = 0
for batch in pq.ParquetFile(cube._find("taxi_trips.parquet")).iter_batches(
        batch_size=1_000_000, columns=["tpep_pickup_datetime", "tpep_dropoff_datetime", "fare_amount", "tip_amount"]):
    t = batch.to_pandas()
    n += len(t)
    fare_tip += t.fare_amount.sum() + t.tip_amount.sum()
    minutes += ((t.tpep_dropoff_datetime - t.tpep_pickup_datetime).dt.total_seconds() / 60).sum()
direct = fare_tip / minutes
assert n == s.cube.trips.sum() == full["totals"]["trips"], ("trip count mismatch", n, full["totals"]["trips"])
assert abs(direct - full["totals"]["rate"]) < 1e-3, (direct, full["totals"]["rate"])
od_total = sum(int(g.trips.sum()) for g in s.od_by_pu.values())
assert od_total == n, ("OD total mismatch", od_total, n)
print(f"OK: {n:,} trips; cube, OD table and rate match raw parquet; direct rate = {direct:.4f}")
