"""Precomputed additive cube over (date, 15-minute slot, zone) and rate recomputation.

slot = hour * 4 + minute // 15 (0-95). Pickups (trips and money sums) are keyed by pickup time and
pickup zone; `dropoffs` and the `do_*` card-tip counts are keyed by dropoff time and dropoff zone.

Only additive quantities (counts, sums, occupied minutes) are stored. Rates are
recomputed from summed numerators/denominators for whatever period is selected,
so aggregation is consistent across time windows.
"""
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

DATA = Path(__file__).resolve().parents[1] / "data"
CUBE = DATA / "cube.parquet"
TRAFFIC = DATA / "traffic_hourly.parquet"
OD = DATA / "od.parquet"
SOURCE = DATA / "cache_source.json"  # which handoff the three caches above were built from
SUMS = ["trips", "fare_sum", "tip_sum", "tolls_sum", "minutes_sum", "card_trips", "good_tip_trips",
        "card_fare_sum", "card_tip_sum", "dropoffs", "do_card_trips", "do_good_tip_trips"]
SLOTS = 96


def _slot(t: pd.Series) -> pd.Series:
    return (t.dt.hour * 4 + t.dt.minute // 15).astype("int8")


def _find(name: str) -> Path:
    # newest handoff wins (folder names sort by first month, not by recency)
    # .tmp/.old are the data panel's working folders (backend/datajobs.py), never a finished handoff
    hits = sorted((p for p in (DATA / "taxi_clean_data").glob(f"clean_handoff_*/{name}")
                   if not p.parent.name.endswith((".tmp", ".old"))), key=lambda p: p.stat().st_mtime)
    if not hits:
        raise FileNotFoundError(f"{name} not found; run scripts/fetch_data.py first")
    return hits[-1]


def build() -> None:
    parts, do_parts, od_parts = [], [], []
    src = _find("taxi_trips.parquet")
    for batch in pq.ParquetFile(src).iter_batches(batch_size=500_000):
        d = batch.to_pandas()
        pu = pd.to_datetime(d["tpep_pickup_datetime"])
        do = pd.to_datetime(d["tpep_dropoff_datetime"])
        minutes = (do - pu).dt.total_seconds() / 60
        card = d["payment_type"] == 1
        good_tip = card & (d["tip_amount"] > 0.2 * d["fare_amount"])
        slot = _slot(pu)
        g = pd.DataFrame({
            "date": pu.dt.normalize(), "slot": slot, "zone": d["PULocationID"],
            "trips": 1, "fare_sum": d["fare_amount"], "tip_sum": d["tip_amount"],
            "tolls_sum": d["tolls_amount"], "minutes_sum": minutes,
            "card_trips": card.astype(int),
            "good_tip_trips": good_tip.astype(int),
            "card_fare_sum": d["fare_amount"].where(card, 0.0), "card_tip_sum": d["tip_amount"].where(card, 0.0),
        }).groupby(["date", "slot", "zone"], as_index=False).sum()
        parts.append(g)
        do_parts.append(pd.DataFrame({"date": do.dt.normalize(), "slot": _slot(do), "zone": d["DOLocationID"],
                                      "dropoffs": 1, "do_card_trips": card.astype(int),
                                      "do_good_tip_trips": good_tip.astype(int)})
                        .groupby(["date", "slot", "zone"], as_index=False).sum())
        od_parts.append(pd.DataFrame({"date": pu.dt.normalize(), "slot": slot,
                                      "pu": d["PULocationID"].astype("int16"), "do": d["DOLocationID"].astype("int16"),
                                      "trips": 1}).groupby(["date", "slot", "pu", "do"], as_index=False).sum())
    keys = ["date", "slot", "zone"]
    pick = pd.concat(parts).groupby(keys, as_index=False).sum()
    drop = pd.concat(do_parts).groupby(keys, as_index=False).sum()
    # trips ending after midnight on the last day would add dropoff-only dates past the loaded period
    drop = drop[drop.date.between(pick.date.min(), pick.date.max())]
    cube = pick.merge(drop, on=keys, how="outer").fillna(0)
    for c in ["trips", "card_trips", "good_tip_trips", "dropoffs", "do_card_trips", "do_good_tip_trips"]:
        cube[c] = cube[c].astype("int64")
    cube["slot"] = cube["slot"].astype("int8")
    cube["zone"] = cube["zone"].astype("int16")
    DATA.mkdir(exist_ok=True)
    cube.to_parquet(CUBE, index=False)
    pd.concat(od_parts).groupby(["date", "slot", "pu", "do"], as_index=False).sum().to_parquet(OD, index=False)

    t = pd.read_parquet(_find("traffic_observations.parquet"),
                        columns=["speed", "travel_time", "data_as_of", "borough"])
    t = t[t.speed.notna()]
    t = t.assign(date=t["data_as_of"].dt.normalize(), slot=_slot(t["data_as_of"]), borough=t["borough"].astype(str))
    # additive: speed_sum / n is recomputed for whatever days, slots and borough are selected
    t.groupby(["date", "slot", "borough"], as_index=False).agg(
        speed_sum=("speed", "sum"), n=("speed", "size")
    ).to_parquet(TRAFFIC, index=False)
    # written last: if a build is interrupted, the record is missing/old and the next start rebuilds
    SOURCE.write_text(json.dumps(_source_id(src)), encoding="utf8")


def _source_id(trips: Path) -> dict:
    # name + file sizes, not mtimes: datajobs re-touches the old handoff after a failed load to keep it newest
    traffic = trips.parent / "traffic_observations.parquet"
    return {"handoff": trips.parent.name, "trips_bytes": trips.stat().st_size,
            "traffic_bytes": traffic.stat().st_size if traffic.exists() else 0}


def _cache_matches_handoff() -> bool:
    try:
        return json.loads(SOURCE.read_text(encoding="utf8")) == _source_id(_find("taxi_trips.parquet"))
    except (OSError, ValueError):
        return False


class Store:
    def __init__(self):
        stale = (CUBE.exists() and not {"date", "slot", "zone", *SUMS} <= set(pq.read_schema(CUBE).names)) or (
            TRAFFIC.exists() and "speed_sum" not in pq.read_schema(TRAFFIC).names)
        if stale or not CUBE.exists() or not TRAFFIC.exists() or not OD.exists() or not _cache_matches_handoff():
            build()  # missing, built before a column was added, or built from another/older handoff
        od = pd.read_parquet(OD)
        self.od_by_pu = {k: g.drop(columns="pu") for k, g in od.groupby("pu")}
        self.cube = pd.read_parquet(CUBE)
        self.traffic = pd.read_parquet(TRAFFIC)

    def meta(self) -> dict:
        c = self.cube
        days = pd.DatetimeIndex(c.date.unique())
        tdays = pd.DatetimeIndex(self.traffic.date.unique()).sort_values()
        gaps = (pd.date_range(tdays.min(), tdays.max()).difference(tdays) if len(tdays)
                else pd.DatetimeIndex([]))
        return {"min_date": str(days.min().date()), "max_date": str(days.max().date()),
                "months": sorted(set(days.strftime("%Y-%m"))),
                "traffic_dates": [str(d.date()) for d in tdays],
                "traffic_missing": [str(d.date()) for d in gaps],  # gaps inside the loaded traffic range
                "trips": int(c.trips.sum())}

    def select(self, start, end, weekdays, slot_from, slot_to, *, all_slots=False, all_weekdays=False):
        c = self.cube
        m = (c.date >= pd.Timestamp(start)) & (c.date <= pd.Timestamp(end))
        if not all_weekdays:
            m &= c.date.dt.weekday.isin(weekdays)
        if not all_slots:
            m &= (c.slot >= slot_from) & (c.slot <= slot_to)
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
        # profitability index: (fare + tip - tolls) per occupied minute
        out["rate"] = (out["fare_sum"] + out["tip_sum"] - out["tolls_sum"]) / out["minutes_sum"]
        out["avg_duration"] = out["minutes_sum"] / out["trips"]
        out["tip_rate"] = 100 * out["good_tip_trips"] / out["card_trips"].replace(0, np.nan)
        out["card_tip_pct"] = 100 * out["card_tip_sum"] / out["card_fare_sum"].replace(0, np.nan)
        out["do_tip_rate"] = 100 * out["do_good_tip_trips"] / out["do_card_trips"].replace(0, np.nan)
        out["dropoffs_per_day"] = out["dropoffs"] / n_days
        out["pickups_per_dropoff"] = out["trips"] / out["dropoffs"].replace(0, np.nan)
    return out.replace([np.inf, -np.inf], np.nan)


def zone_summary(store: Store, start, end, weekdays, slot_from, slot_to, min_trips) -> dict:
    sel = store.select(start, end, weekdays, slot_from, slot_to)
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
    cols = ["zone", "trips", "pickups_per_day", "rate", "avg_duration", "tip_rate", "card_tip_pct", "do_tip_rate",
            "dropoffs_per_day", "pickups_per_dropoff", "attractiveness", "eligible", "pareto"]
    totals = tot[["trips", "pickups_per_day", "rate", "avg_duration", "tip_rate", "card_tip_pct", "do_tip_rate",
                  "dropoffs_per_day"]].to_dict()
    return {"n_days": n_days, "totals": _clean(totals),
            "zones": [_clean(r) for r in z[cols].to_dict("records")]}


def traffic_by_slot(store: Store, start, end, weekdays, borough=None, sel=None) -> dict:
    """Mean DOT speed per 15-minute slot over the selected dates/weekdays, limited to `borough` when it has data.

    matched: the selection holds traffic days. Then `rate_by_slot` is the earning rate over exactly those
    days (from `sel`, the all-slot cube selection), so both indexed lines cover the same days. Otherwise
    traffic falls back to all loaded traffic days on the selected weekdays and the rate stays unrestricted."""
    t = store.traffic[store.traffic.date.dt.weekday.isin(weekdays)]
    in_range = t[(t.date >= pd.Timestamp(start)) & (t.date <= pd.Timestamp(end))]
    taxi_days = set(store.cube.date[(store.cube.date >= pd.Timestamp(start)) & (store.cube.date <= pd.Timestamp(end))
                                    & store.cube.date.dt.weekday.isin(weekdays)].unique())
    in_range = in_range[in_range.date.isin(taxi_days)]  # same days on both lines
    matched = not in_range.empty
    t = in_range if matched else t
    rate = []
    if matched and sel is not None:
        r = sel[sel.date.isin(set(in_range.date))].groupby("slot")[SUMS].sum()
        r = rates(r.reindex(range(SLOTS), fill_value=0).rename_axis("slot").reset_index(), 1)
        rate = [_clean(x) for x in r[["slot", "rate"]].to_dict("records")]
    if borough is not None and (t.borough == borough).any():
        t = t[t.borough == borough]
    else:
        borough = None
    g = t.groupby("slot")[["speed_sum", "n"]].sum()
    s = (g.speed_sum / g.n).rename("speed").reset_index()
    days = t.date.drop_duplicates()
    return {"by_slot": [_clean(r) for r in s.to_dict("records")], "rate_by_slot": rate,
            "matched": matched, "borough": borough, "n_days": int(len(days)), "n_taxi_days": len(taxi_days),
            "first": str(days.min().date()) if len(days) else None,
            "last": str(days.max().date()) if len(days) else None}


def timeseries(store: Store, start, end, weekdays, slot_from, slot_to, zone=None, borough=None) -> dict:
    def pick(sel):
        return sel if zone is None else sel[sel.zone == zone]

    # by slot: date + weekday filters only, so the time axis shows the whole day
    sel = pick(store.select(start, end, weekdays, slot_from, slot_to, all_slots=True))
    n_days = max(store.n_days(start, end, weekdays), 1)
    h = sel.groupby("slot")[SUMS].sum().reindex(range(SLOTS), fill_value=0).rename_axis("slot").reset_index()
    h = rates(h, n_days)
    # by weekday: date + time-of-day filters only
    sel2 = pick(store.select(start, end, weekdays, slot_from, slot_to, all_weekdays=True))
    sel2 = sel2.assign(weekday=sel2.date.dt.weekday)
    dates = store.cube.date.drop_duplicates()
    dates = dates[(dates >= pd.Timestamp(start)) & (dates <= pd.Timestamp(end))]
    per_wd = dates.dt.weekday.value_counts()
    w = sel2.groupby("weekday")[SUMS].sum().reset_index()
    w = rates(w, w.weekday.map(per_wd).clip(lower=1))
    return {"by_slot": [_clean(r) for r in h.to_dict("records")],
            "by_weekday": [_clean(r) for r in w.to_dict("records")],
            "traffic": traffic_by_slot(store, start, end, weekdays, borough, sel)}


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


def destinations(store: Store, zone, start, end, weekdays, slot_from, slot_to, top=10) -> dict:
    """P(zone -> B) = trips A->B / all selected trips departing A."""
    g = store.od_by_pu.get(zone)
    if g is None:
        return {"total": 0, "top": [], "other": 0.0}
    m = ((g.date >= pd.Timestamp(start)) & (g.date <= pd.Timestamp(end)) & g.date.dt.weekday.isin(weekdays)
         & (g.slot >= slot_from) & (g.slot <= slot_to))
    by = g[m].groupby("do").trips.sum().sort_values(ascending=False)
    total = int(by.sum())
    if not total:
        return {"total": 0, "top": [], "other": 0.0}
    head = by.head(top)
    return {"total": total, "other": round(float(1 - head.sum() / total), 4),
            "top": [{"zone": int(k), "trips": int(v), "prob": round(float(v / total), 4)} for k, v in head.items()],
            # every destination, for colouring the map by OD probability
            "all": [{"zone": int(k), "trips": int(v), "prob": round(float(v / total), 6)} for k, v in by.items()]}
