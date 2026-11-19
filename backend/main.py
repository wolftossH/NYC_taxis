from datetime import date
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import cube, datajobs
from .basemap import load_basemap
from .zones import load_zones

app = FastAPI(title="NYC Taxi Dashboard")


@app.middleware("http")
async def revalidate(request, call_next):
    # Without this, browsers may reuse a stale app.js against a newer index.html after an edit.
    # no-cache still uses the ETag, so unchanged files come back as a cheap 304.
    response = await call_next(request)
    response.headers.setdefault("Cache-Control", "no-cache")
    return response
store = cube.Store()
zones = load_zones()
BOROUGH = {f["properties"]["LocationID"]: f["properties"]["borough"] for f in zones["features"]}
try:
    basemap = load_basemap()
except Exception as e:  # the backdrop is decoration: the map still works without it
    print(f"basemap unavailable: {e}")
    basemap = {"type": "FeatureCollection", "features": []}


def _wd(weekdays: str) -> list[int]:
    return [int(x) for x in weekdays.split(",") if x != ""]


# Time of day is a range of 15-minute slots (0 = 00:00-00:14, 95 = 23:45-23:59), inclusive.
# hour_from/hour_to are kept for old links: hour h covers slots 4h..4h+3.
SlotFrom = Query(None, ge=0, le=95)
SlotTo = Query(None, ge=0, le=95)
HourFrom = Query(None, ge=0, le=23)
HourTo = Query(None, ge=0, le=23)


def _slots(slot_from, slot_to, hour_from, hour_to) -> tuple[int, int]:
    a = slot_from if slot_from is not None else (hour_from * 4 if hour_from is not None else 0)
    b = slot_to if slot_to is not None else (hour_to * 4 + 3 if hour_to is not None else 95)
    return a, b


@app.get("/api/meta")
def meta():
    return store.meta()


@app.get("/api/zones")
def get_zones():
    return JSONResponse(zones)


@app.get("/api/basemap")
def get_basemap():
    return JSONResponse(basemap)


@app.get("/api/summary")
def summary(start: date, end: date, weekdays: str = "0,1,2,3,4,5,6",
            slot_from: int | None = SlotFrom, slot_to: int | None = SlotTo,
            hour_from: int | None = HourFrom, hour_to: int | None = HourTo,
            min_trips: int = Query(30, ge=0)):
    return cube.zone_summary(store, start, end, _wd(weekdays), *_slots(slot_from, slot_to, hour_from, hour_to),
                             min_trips)


@app.get("/api/timeseries")
def timeseries(start: date, end: date, weekdays: str = "0,1,2,3,4,5,6",
               slot_from: int | None = SlotFrom, slot_to: int | None = SlotTo,
               hour_from: int | None = HourFrom, hour_to: int | None = HourTo,
               zone: int | None = None):
    return cube.timeseries(store, start, end, _wd(weekdays), *_slots(slot_from, slot_to, hour_from, hour_to), zone,
                           BOROUGH.get(zone))


@app.get("/api/od")
def od(zone: int, start: date, end: date, weekdays: str = "0,1,2,3,4,5,6",
       slot_from: int | None = SlotFrom, slot_to: int | None = SlotTo,
       hour_from: int | None = HourFrom, hour_to: int | None = HourTo):
    return cube.destinations(store, zone, start, end, _wd(weekdays), *_slots(slot_from, slot_to, hour_from, hour_to))


# --- data panel: load TLC months / DOT traffic days from the UI (backend/datajobs.py) ---

class FetchRequest(BaseModel):
    months: list[str]
    traffic_from: str
    traffic_to: str


def _swap(new_store: cube.Store) -> None:
    global store  # routes read the module-level store, so the old data serves until this line
    store = new_store


@app.get("/api/data/status")
def data_status():
    m = store.meta()
    return datajobs.status({k: m[k] for k in ("months", "min_date", "max_date", "traffic_dates", "traffic_missing",
                                              "trips")})


@app.get("/api/data/available")
def data_available():
    return {"months": datajobs.available_months()}


@app.post("/api/data/fetch", status_code=202)
def data_fetch(req: FetchRequest):
    try:
        datajobs.start(req.months, req.traffic_from, req.traffic_to, store.meta(), _swap)
    except ValueError as e:
        raise HTTPException(422, str(e))
    except RuntimeError as e:
        raise HTTPException(409, str(e))
    return {"ok": True}


@app.post("/api/data/cancel")
def data_cancel():
    return {"cancelled": datajobs.cancel()}


app.mount("/", StaticFiles(directory=Path(__file__).resolve().parents[1] / "frontend", html=True),
          name="frontend")
