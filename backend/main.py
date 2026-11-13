from datetime import date
from pathlib import Path

from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from . import cube
from .basemap import load_basemap
from .zones import load_zones

app = FastAPI(title="NYC Taxi Dashboard")
store = cube.Store()
zones = load_zones()
try:
    basemap = load_basemap()
except Exception as e:  # the backdrop is decoration: the map still works without it
    print(f"basemap unavailable: {e}")
    basemap = {"type": "FeatureCollection", "features": []}


def _wd(weekdays: str) -> list[int]:
    return [int(x) for x in weekdays.split(",") if x != ""]


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
            hour_from: int = Query(0, ge=0, le=23), hour_to: int = Query(23, ge=0, le=23),
            min_trips: int = Query(30, ge=0)):
    return cube.zone_summary(store, start, end, _wd(weekdays), hour_from, hour_to, min_trips)


@app.get("/api/timeseries")
def timeseries(start: date, end: date, weekdays: str = "0,1,2,3,4,5,6",
               hour_from: int = Query(0, ge=0, le=23), hour_to: int = Query(23, ge=0, le=23),
               zone: int | None = None):
    return cube.timeseries(store, start, end, _wd(weekdays), hour_from, hour_to, zone)


@app.get("/api/od")
def od(zone: int, start: date, end: date, weekdays: str = "0,1,2,3,4,5,6",
       hour_from: int = Query(0, ge=0, le=23), hour_to: int = Query(23, ge=0, le=23)):
    return cube.destinations(store, zone, start, end, _wd(weekdays), hour_from, hour_to)


app.mount("/", StaticFiles(directory=Path(__file__).resolve().parents[1] / "frontend", html=True),
          name="frontend")
