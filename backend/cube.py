"""Precomputed additive cube over (date, hour, pickup zone) and rate recomputation.

Only additive quantities (counts, sums, occupied minutes) are stored. Rates are
recomputed from summed numerators/denominators for whatever period is selected,
so aggregation is consistent across time windows.
"""
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

DATA = Path(__file__).resolve().parents[1] / "data"
CUBE = DATA / "cube.parquet"
TRAFFIC = DATA / "traffic_hourly.parquet"
OD = DATA / "od.parquet"
SUMS = ["trips", "fare_sum", "tip_sum", "tolls_sum", "minutes_sum", "card_trips", "good_tip_trips"]


def _find(name: str) -> Path:
    # newest handoff wins (folder names sort by first month, not by recency)
    hits = sorted((DATA / "taxi_clean_data").glob(f"clean_handoff_*/{name}"), key=lambda p: p.stat().st_mtime)
    if not hits:
        raise FileNotFoundError(f"{name} not found; run scripts/fetch_data.py first")
    return hits[-1]


def build() -> None:
    parts, od_parts = [], []
    for batch in pq.ParquetFile(_find("taxi_trips.parquet")).iter_batches(batch_size=500_000):
        d = batch.to_pandas()
        pu = pd.to_datetime(d["tpep_pickup_datetime"])
        minutes = (pd.to_datetime(d["tpep_dropoff_datetime"]) - pu).dt.total_seconds() / 60
        card = d["payment_type"] == 1
        g = pd.DataFrame({
            "date": pu.dt.normalize(), "hour": pu.dt.hour.astype("int8"), "zone": d["PULocationID"],
            "trips": 1, "fare_sum": d["fare_amount"], "tip_sum": d["tip_amount"],
            "tolls_sum": d["tolls_amount"], "minutes_sum": minutes,
            "card_trips": card.astype(int),
            "good_tip_trips": (card & (d["tip_amount"] > 0.2 * d["fare_amount"])).astype(int),
        }).groupby(["date", "hour", "zone"], as_index=False).sum()
        parts.append(g)
        od_parts.append(pd.DataFrame({"date": pu.dt.normalize(), "hour": pu.dt.hour.astype("int8"),
                                      "pu": d["PULocationID"].astype("int16"), "do": d["DOLocationID"].astype("int16"),
                                      "trips": 1}).groupby(["date", "hour", "pu", "do"], as_index=False).sum())
    cube = pd.concat(parts).groupby(["date", "hour", "zone"], as_index=False).sum()
    DATA.mkdir(exist_ok=True)
    cube.to_parquet(CUBE, index=False)
    pd.concat(od_parts).groupby(["date", "hour", "pu", "do"], as_index=False).sum().to_parquet(OD, index=False)

    t = pd.read_parquet(_find("traffic_observations.parquet"),
                        columns=["speed", "travel_time", "data_as_of", "borough"])
    t["date"] = t["data_as_of"].dt.normalize()
    t["hour"] = t["data_as_of"].dt.hour
    t["borough"] = t["borough"].astype(str)
    t.groupby(["date", "hour", "borough"], as_index=False).agg(
        speed=("speed", "mean"), travel_time=("travel_time", "mean"), n=("speed", "size")
    ).to_parquet(TRAFFIC, index=False)


class Store:
    def __init__(self):
        if not CUBE.exists() or not TRAFFIC.exists() or not OD.exists():
            build()
        od = pd.read_parquet(OD)
        self.od_by_pu = {k: g.drop(columns="pu") for k, g in od.groupby("pu")}
        self.cube = pd.read_parquet(CUBE)
        self.traffic = pd.read_parquet(TRAFFIC)

    def meta(self) -> dict:
        c = self.cube
        return {"min_date": str(c.date.min().date()), "max_date": str(c.date.max().date()),
                "traffic_dates": sorted({str(d.date()) for d in self.traffic.date}),
                "trips": int(c.trips.sum())}

    def select(self, start, end, weekdays, hour_from, hour_to, *, all_hours=False, all_weekdays=False):
        c = self.cube
        m = (c.date >= pd.Timestamp(start)) & (c.date <= pd.Timestamp(end))
        if not all_weekdays:
            m &= c.date.dt.weekday.isin(weekdays)
        if not all_hours:
            m &= (c.hour >= hour_from) & (c.hour <= hour_to)
        return c[m]

    def n_days(self, start, end, weekdays) -> int:
        d = self.cube.date.drop_duplicates()
        d = d[(d >= pd.Timestamp(start)) & (d <= pd.Timestamp(end)) & d.dt.weekday.isin(weekdays)]
        return int(len(d))


def rates(df: pd.DataFrame, n_days) -> pd.DataFrame:
    """Combine summed quantities into rates. n_days may be a scalar or aligned Series."""
    out = df.copy()
    with np.errstate(divide="ignore", invalid="ignore"):
        out["pickups_per_day"] = out["trips"] / n_days
        out["rate"] = (out["fare_sum"] + out["tip_sum"]) / out["minutes_sum"]
        out["avg_duration"] = out["minutes_sum"] / out["trips"]
        out["tip_rate"] = 100 * out["good_tip_trips"] / out["card_trips"].replace(0, np.nan)
    return out.replace([np.inf, -np.inf], np.nan)


def zone_summary(store: Store, start, end, weekdays, hour_from, hour_to, min_trips) -> dict:
    sel = store.select(start, end, weekdays, hour_from, hour_to)
    n_days = max(store.n_days(start, end, weekdays), 1)
    z = rates(sel.groupby("zone")[SUMS].sum().reset_index(), n_days)
    z["eligible"] = (z.trips >= min_trips) & (z.minutes_sum > 0)
    e = z[z.eligible]
    z["attractiveness"] = np.nan
    z.loc[z.eligible, "attractiveness"] = 100 * (
        0.5 * e.pickups_per_day.rank(pct=True) + 0.5 * e.rate.rank(pct=True))
    # Pareto frontier: maximise both pickups_per_day and rate
    z["pareto"] = False
    best = -np.inf
    for i, r in e.sort_values(["pickups_per_day", "rate"], ascending=False).iterrows():
        if r.rate > best:
            z.loc[i, "pareto"] = True
            best = r.rate
    tot = rates(pd.DataFrame([sel[SUMS].sum()]), n_days).iloc[0]
    cols = ["zone", "trips", "pickups_per_day", "rate", "avg_duration", "tip_rate",
            "attractiveness", "eligible", "pareto"]
    totals = tot[["trips", "pickups_per_day", "rate", "avg_duration", "tip_rate"]].to_dict()
    return {"n_days": n_days, "totals": _clean(totals),
            "zones": [_clean(r) for r in z[cols].to_dict("records")]}


def timeseries(store: Store, start, end, weekdays, hour_from, hour_to, zone=None) -> dict:
    def pick(sel):
        return sel if zone is None else sel[sel.zone == zone]

    # by hour: date + weekday filters only, so the hour axis shows the whole day
    sel = pick(store.select(start, end, weekdays, hour_from, hour_to, all_hours=True))
    n_days = max(store.n_days(start, end, weekdays), 1)
    h = rates(sel.groupby("hour")[SUMS].sum().reset_index(), n_days)
    # by weekday: date + hour filters only
    sel2 = pick(store.select(start, end, weekdays, hour_from, hour_to, all_weekdays=True))
    sel2 = sel2.assign(weekday=sel2.date.dt.weekday)
    dates = store.cube.date.drop_duplicates()
    dates = dates[(dates >= pd.Timestamp(start)) & (dates <= pd.Timestamp(end))]
    per_wd = dates.dt.weekday.value_counts()
    w = sel2.groupby("weekday")[SUMS].sum().reset_index()
    w = rates(w, w.weekday.map(per_wd).clip(lower=1))
    tr = store.traffic.assign(sp=lambda x: x.speed * x.n).groupby("hour")[["sp", "n"]].sum()
    tr = (tr.sp / tr.n).rename("speed").reset_index()
    return {"by_hour": [_clean(r) for r in h.to_dict("records")],
            "by_weekday": [_clean(r) for r in w.to_dict("records")],
            "traffic_by_hour": [_clean(r) for r in tr.to_dict("records")]}


def _clean(d: dict) -> dict:
    out = {}
    for k, v in d.items():
        if isinstance(v, (np.bool_, bool)):
            out[k] = bool(v)
        elif isinstance(v, np.integer):
            out[k] = int(v)
        elif v is None or (isinstance(v, (float, np.floating)) and not np.isfinite(v)):
            out[k] = None
        elif isinstance(v, (float, np.floating)):
            out[k] = round(float(v), 4)
        else:
            out[k] = v
    return out


def destinations(store: Store, zone, start, end, weekdays, hour_from, hour_to, top=8) -> dict:
    """P(zone -> B) = trips A->B / all selected trips departing A."""
    g = store.od_by_pu.get(zone)
    if g is None:
        return {"total": 0, "top": [], "other": 0.0}
    m = (g.date >= pd.Timestamp(start)) & (g.date <= pd.Timestamp(end)) & g.date.dt.weekday.isin(weekdays)         & (g.hour >= hour_from) & (g.hour <= hour_to)
    by = g[m].groupby("do").trips.sum().sort_values(ascending=False)
    total = int(by.sum())
    if not total:
        return {"total": 0, "top": [], "other": 0.0}
    head = by.head(top)
    return {"total": total, "other": round(float(1 - head.sum() / total), 4),
            "top": [{"zone": int(k), "trips": int(v), "prob": round(float(v / total), 4)} for k, v in head.items()]}
