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
full = cube.zone_summary(s, lo, hi, list(range(7)), 0, cube.SLOTS - 1, 30)
print("days", full["n_days"], "totals", full["totals"])
print("pareto zones", sum(z["pareto"] for z in full["zones"]), "of", len(full["zones"]))

# Stream the raw file in batches so the check works for multi-month data too.
n = net = minutes = card = card_fare = card_tip = 0
for batch in pq.ParquetFile(cube._find("taxi_trips.parquet")).iter_batches(
        batch_size=1_000_000, columns=["tpep_pickup_datetime", "tpep_dropoff_datetime", "fare_amount", "tip_amount", "tolls_amount",
                                          "payment_type"]):
    t = batch.to_pandas()
    n += len(t)
    net += t.fare_amount.sum() + t.tip_amount.sum() - t.tolls_amount.sum()
    c = t[t.payment_type == 1]
    card += len(c); card_fare += c.fare_amount.sum(); card_tip += c.tip_amount.sum()
    minutes += ((t.tpep_dropoff_datetime - t.tpep_pickup_datetime).dt.total_seconds() / 60).sum()
direct = net / minutes
assert s.cube.slot.between(0, cube.SLOTS - 1).all(), "slot out of range"
assert n == s.cube.trips.sum() == full["totals"]["trips"], ("trip count mismatch", n, full["totals"]["trips"])
# every trip ends once, minus the few that end after the last loaded day (dropped so dates stay in range)
drops = int(s.cube.dropoffs.sum())
assert 0 <= n - drops < 0.001 * n, ("dropoff count mismatch", drops, n)
assert (s.cube.groupby("date").trips.sum() > 0).all(), "dropoff-only date outside the pickup period"
assert abs(direct - full["totals"]["rate"]) < 1e-3, (direct, full["totals"]["rate"])
assert abs(100 * card_tip / card_fare - full["totals"]["card_tip_pct"]) < 1e-3, ("card tip % mismatch", card_tip / card_fare)
assert int(s.cube.card_trips.sum()) == card, ("card trip mismatch", card)
od_total = sum(int(g.trips.sum()) for g in s.od_by_pu.values())
assert od_total == n, ("OD total mismatch", od_total, n)
print(f"OK: {n:,} trips, {drops:,} dropoffs in period; cube, OD table and rate match raw parquet; direct rate = {direct:.4f}")
