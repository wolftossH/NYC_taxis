"""Load TLC months and DOT traffic days from the dashboard (one background job at a time).

Flow: check traffic days with DOT (empty days are skipped, the notebook aborts on them) -> run
scripts/fetch_data.py for ONLY the new months (cached raw files are reused; already-loaded months are not
re-cleaned, which keeps RAM low) -> stream-merge the current handoff's still-wanted months with the new batch into
clean_handoff_<first>_to_<last> -> cube.build() -> hand a fresh cube.Store to `on_ready` (hot swap, no restart).
On failure or cancel the current handoff is made newest again, so the old data stays loaded.
"""
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq
import requests

from . import cube

ROOT = Path(__file__).resolve().parents[1]
CLEAN = cube.DATA / "taxi_clean_data"
RAW_TLC = CLEAN / "raw" / "tlc"
RAW_TRAFFIC = CLEAN / "raw" / "traffic"
LOG = cube.DATA / "fetch_job.log"
TLC_URL = "https://d37ci6vzurychx.cloudfront.net/trip-data/yellow_tripdata_{}.parquet"
DOT_URL = "https://data.cityofnewyork.us/resource/i4gi-tjb9.json"  # as in taxi_pipeline/acquisition.py
MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
MAX_TRAFFIC_DAYS = 400
MB_PER_MONTH = 63  # raw TLC file; the cleaned copy is about the same again
STAGES = ["check", "fetch", "merge", "rebuild", "done"]

_lock = threading.Lock()
_job: dict = {"state": "idle"}
_proc: subprocess.Popen | None = None
_available: tuple[float, list] = (0.0, [])


class Cancelled(Exception):
    pass


def _nyc_yesterday() -> date:
    return datetime.now(ZoneInfo("America/New_York")).date() - timedelta(days=1)


def _month_range(lo: str, hi: str) -> list[str]:
    y, m = map(int, lo.split("-"))
    out = []
    while f"{y:04d}-{m:02d}" <= hi:
        out.append(f"{y:04d}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def cached_months() -> list[str]:
    return sorted(p.stem.removeprefix("yellow_tripdata_") for p in RAW_TLC.glob("yellow_tripdata_*.parquet"))


def cached_traffic_days() -> set[str]:
    return {p.stem.removeprefix("nyc_traffic_speeds_") for p in RAW_TRAFFIC.glob("nyc_traffic_speeds_*.parquet")}


def available_months() -> list[str]:
    """Published TLC yellow-taxi months since 2024-01 (HEAD per file, cached for an hour)."""
    global _available
    if time.time() - _available[0] < 3600 and _available[1]:
        return _available[1]
    months = _month_range("2024-01", date.today().strftime("%Y-%m"))
    cached = set(cached_months())

    def ok(m):
        if m in cached:
            return True
        try:
            return requests.head(TLC_URL.format(m), timeout=15, allow_redirects=True).status_code == 200
        except requests.RequestException:
            return False

    with ThreadPoolExecutor(8) as ex:
        found = [m for m, good in zip(months, ex.map(ok, months)) if good]
    _available = (time.time(), found)
    return found


def _ranges(days: list[str]) -> str:
    """['2026-08-01', '2026-08-02', '2026-08-04'] -> '2026-08-01:2026-08-02,2026-08-04' (fetch_data.py syntax)."""
    out, ds = [], [date.fromisoformat(d) for d in sorted(days)]
    i = 0
    while i < len(ds):
        j = i
        while j + 1 < len(ds) and ds[j + 1] - ds[j] == timedelta(days=1):
            j += 1
        out.append(str(ds[i]) if i == j else f"{ds[i]}:{ds[j]}")
        i = j + 1
    return ",".join(out)


def validate(months: list[str], traffic_from: str, traffic_to: str) -> tuple[list[str], list[str]]:
    months = sorted(set(months))
    if not months:
        raise ValueError("Select at least one TLC month.")
    bad = [m for m in months if not MONTH_RE.match(m)]
    if bad:
        raise ValueError(f"Not a YYYY-MM month: {bad[0]!r}")
    try:
        lo, hi = date.fromisoformat(traffic_from), date.fromisoformat(traffic_to)
    except (TypeError, ValueError):
        raise ValueError("Traffic dates must be YYYY-MM-DD.")
    if lo > hi:
        raise ValueError("Traffic 'from' is after 'to'.")
    if hi > _nyc_yesterday():
        raise ValueError(f"Traffic can run up to yesterday ({_nyc_yesterday()}); today's data is still incomplete.")
    n = (hi - lo).days + 1
    if n > MAX_TRAFFIC_DAYS:
        raise ValueError(f"At most {MAX_TRAFFIC_DAYS} traffic days per load ({n} selected).")
    return months, [str(lo + timedelta(i)) for i in range(n)]


def status(loaded: dict) -> dict:
    with _lock:
        job = dict(_job)
    if job.get("state") == "running":
        job["elapsed"] = round(time.time() - job["started"])
        p = job["progress"] = dict(job.get("progress", {}))
        if job.get("stage") == "fetch":  # count files as the notebook writes them
            p["tlc_done"] = len(set(job["new_months"]) & set(cached_months()))
            p["traffic_done"] = len(set(job["traffic_days"]) & cached_traffic_days())
    if LOG.exists() and job.get("state") in {"running", "error"}:
        lines = LOG.read_text(encoding="utf8", errors="replace").splitlines()
        job["log"] = [re.sub(r"\x1b\[[0-9;]*m", "", s) for s in lines[-40:]]
    free = shutil.disk_usage(cube.DATA).free
    return {"loaded": loaded, "job": job, "free_gb": round(free / 1e9, 1), "mb_per_month": MB_PER_MONTH,
            "cached_months": cached_months(), "cached_traffic_days": sorted(cached_traffic_days()),
            "max_traffic_day": str(_nyc_yesterday())}


def start(months: list[str], traffic_from: str, traffic_to: str, loaded: dict, on_ready) -> None:
    """`loaded` is Store.meta() of the data being served; `on_ready(store)` swaps in the rebuilt Store."""
    loaded_months, loaded_days = loaded["months"], loaded["traffic_dates"]
    months, days = validate(months, traffic_from, traffic_to)
    with _lock:
        if _job.get("state") == "running":
            raise RuntimeError("A data load is already running.")
        _job.clear()
        _job.update(state="running", stage="check", started=time.time(), months=months,
                    new_months=[m for m in months if m not in loaded_months],
                    traffic_days=days, skipped_days=[], progress={}, cancel=False)
    LOG.write_text("", encoding="utf8")
    threading.Thread(target=_run, args=(months, days, loaded_months, loaded_days, on_ready), daemon=True).start()


def cancel() -> bool:
    with _lock:
        if _job.get("state") != "running":
            return False
        _job["cancel"] = True
    _kill()
    return True


def _kill() -> None:
    p = _proc
    if p and p.poll() is None:
        if os.name == "nt":  # fetch_data -> nbconvert -> kernel: kill the whole tree
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(p.pid)], capture_output=True)
        else:
            os.killpg(p.pid, 9)


def _set(**kw) -> None:
    with _lock:
        if _job.get("cancel"):
            raise Cancelled()
        _job.update(**kw)


def _log(msg: str) -> None:
    with LOG.open("a", encoding="utf8") as f:
        f.write(f"[{time.strftime('%H:%M:%S')}] {msg}\n")


def _check_days(days: list[str]) -> list[str]:
    """Return requested days that DOT has no rows for (only uncached days are asked)."""
    have = cached_traffic_days()
    todo = [d for d in days if d not in have]

    def empty(d):
        nxt = date.fromisoformat(d) + timedelta(days=1)
        where = f"data_as_of >= '{d}T00:00:00' AND data_as_of < '{nxt}T00:00:00'"
        for attempt in range(3):
            try:
                r = requests.get(DOT_URL, params={"$select": "count(*)", "$where": where}, timeout=60)
                r.raise_for_status()
                return int(r.json()[0]["count"]) == 0
            except (requests.RequestException, KeyError, IndexError, ValueError):
                time.sleep(2 * (attempt + 1))
        return False  # unknown: let the download try (and retry) it

    with ThreadPoolExecutor(6) as ex:
        return [d for d, e in zip(todo, ex.map(empty, todo)) if e]


def _fetch(months: list[str], days: list[str]) -> Path:
    """Run fetch_data.py (up to 3 attempts; cached downloads make retries resume). Returns the batch folder."""
    global _proc
    before = {p.name: p.stat().st_mtime for p in CLEAN.glob("clean_handoff_*")}
    cmd = [sys.executable, str(ROOT / "scripts" / "fetch_data.py"), ",".join(months), _ranges(days)]
    for attempt in range(1, 4):
        _log(f"fetch attempt {attempt}: months {','.join(months)}; {len(days)} traffic days")
        with LOG.open("a", encoding="utf8") as f:
            kw = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}
            _proc = subprocess.Popen(cmd, cwd=ROOT, stdout=f, stderr=subprocess.STDOUT, **kw)
            code = _proc.wait()
        _set()  # raises if cancelled meanwhile
        if code == 0:
            break
        if attempt == 3:
            raise RuntimeError("Download/cleaning failed 3 times; see the log.")
    new = [p for p in CLEAN.glob("clean_handoff_*")
           if p.is_dir() and (p.name not in before or p.stat().st_mtime > before[p.name])
           and (p / "taxi_trips.parquet").exists()]
    if not new:
        raise RuntimeError("fetch_data.py finished but wrote no handoff folder.")
    return max(new, key=lambda p: p.stat().st_mtime)


def _merge(current: Path | None, batch: Path | None, months: list[str], batch_months: list[str]) -> Path:
    """Stream the wanted months of `current` plus `batch_months` of `batch` into one handoff folder."""
    name = f"clean_handoff_{months[0]}_to_{months[-1]}" if len(months) > 1 else f"clean_handoff_{months[0]}"
    out = CLEAN / name
    tmp = CLEAN / (name + ".tmp")
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir()
    keep_current = [m for m in months if m not in batch_months]
    sources = [(s, ms) for s, ms in ((current, keep_current), (batch, batch_months)) if s is not None and ms]
    schema = pq.read_schema((sources[0][0] if sources else current) / "taxi_trips.parquet").remove_metadata()
    n = 0
    with pq.ParquetWriter(tmp / "taxi_trips.parquet", schema) as w:
        for src, ms in sources:
            want = pa.array([int(m[:4]) * 100 + int(m[5:]) for m in ms], pa.int64())
            for b in pq.ParquetFile(src / "taxi_trips.parquet").iter_batches(batch_size=500_000):
                _set()
                # cleaning keeps trips that start inside their source month, so pickup month = source month.
                # year*100+month, not strftime: strftime needs a tz database that Windows pyarrow lacks.
                t = b.column("tpep_pickup_datetime")
                ym = pc.add(pc.multiply(pc.year(t), 100), pc.month(t)).cast(pa.int64())
                b = b.filter(pc.is_in(ym, value_set=want))
                if b.num_rows:
                    w.write_batch(b.replace_schema_metadata(None).cast(schema))
                    n += b.num_rows
    shutil.copy2((batch or current) / "traffic_observations.parquet", tmp / "traffic_observations.parquet")
    (tmp / "README.md").write_text(
        f"Merged by the dashboard data panel: months {', '.join(months)}; {n:,} trips.\n"
        f"Kept from {current.name if current else '-'}: {', '.join(keep_current) or '-'}; "
        f"new from {batch.name if batch else '-'}: {', '.join(batch_months) or '-'}.\n", encoding="utf8")
    if out.exists():
        old = CLEAN / (name + ".old")
        shutil.rmtree(old, ignore_errors=True)
        out.rename(old)
        tmp.rename(out)
        shutil.rmtree(old, ignore_errors=True)
    else:
        tmp.rename(out)
    for f in out.glob("*.parquet"):  # newest handoff wins in cube._find
        os.utime(f)
    _log(f"merged {n:,} trips into {out.name}")
    return out


def _prune_handoffs(keep: Path) -> None:
    """Delete every finished handoff folder except `keep`. Raw downloads (raw/tlc, raw/traffic) are never touched,
    so any removed month reloads from cache without a download."""
    for p in CLEAN.glob("clean_handoff_*"):
        if p == keep or not p.is_dir() or p.name.endswith((".tmp", ".old")):
            continue
        mb = sum(f.stat().st_size for f in p.rglob("*") if f.is_file()) / 1e6
        shutil.rmtree(p, ignore_errors=True)
        _log(f"removed old handoff {p.name} ({mb:,.0f} MB)")


def _run(months, days, loaded_months, loaded_days, on_ready) -> None:
    current = None
    batch = None
    created_batch = False
    try:
        current = cube._find("taxi_trips.parquet").parent
    except FileNotFoundError:
        pass
    try:
        _log("checking traffic days with NYC DOT")
        skipped = _check_days(days)
        days = [d for d in days if d not in skipped]
        if not days:
            raise RuntimeError("DOT has no traffic data for any selected day.")
        _set(skipped_days=skipped, traffic_days=days, stage="fetch",
             progress={"tlc_total": len([m for m in months if m not in loaded_months]), "traffic_total": len(days)})
        if skipped:
            _log(f"no DOT data, skipped: {', '.join(skipped)}")

        new_months = [m for m in months if m not in loaded_months]
        need_traffic = current is None or set(days) != set(loaded_days)
        batch_months = new_months
        if new_months or need_traffic or current is None:
            # traffic only: clean one cached month just to get the traffic output, then drop its trips
            run_months = new_months or [next((m for m in months if m in cached_months()), months[0])]
            existing = {p.name for p in CLEAN.glob("clean_handoff_*")}
            batch = _fetch(run_months, days)
            created_batch = batch.name not in existing
        else:
            _log("no download needed: only removing months")
        _set(stage="merge")
        out = _merge(current, batch, months, batch_months if batch else [])
        if batch is not None and created_batch and batch != out:
            shutil.rmtree(batch, ignore_errors=True)
        batch = None
        _set(stage="rebuild")
        _log("rebuilding aggregates (cube, OD, traffic)")
        try:
            cube.build()
            store = cube.Store()
        except Exception:
            for f in (cube.CUBE, cube.OD, cube.TRAFFIC):  # half-written caches: force a rebuild on next start
                f.unlink(missing_ok=True)
            raise
        on_ready(store)
        _prune_handoffs(keep=out)  # only after the new data is live; on error the old folder is the fallback
        _log("done")
        with _lock:
            _job.update(state="done", stage="done", finished=time.time())
    except Exception as e:
        cancelled = isinstance(e, Cancelled)
        _log("cancelled" if cancelled else f"error: {e}")
        if batch is not None and created_batch:
            shutil.rmtree(batch, ignore_errors=True)
        for tmp in CLEAN.glob("clean_handoff_*.tmp"):  # merge interrupted part-way
            shutil.rmtree(tmp, ignore_errors=True)
        if current is not None and current.exists():  # keep serving / restarting on the old data
            for f in current.glob("*.parquet"):
                os.utime(f)
        with _lock:
            _job.update(state="cancelled" if cancelled else "error", error=None if cancelled else str(e),
                        finished=time.time())
