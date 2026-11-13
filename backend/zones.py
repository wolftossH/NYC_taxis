"""Download and cache the NYC taxi-zone polygons (WGS84 GeoJSON, NYC Open Data)."""
import json
from pathlib import Path

import requests

URL = "https://data.cityofnewyork.us/api/geospatial/8meu-9t5y?method=export&format=GeoJSON"
CACHE = Path(__file__).resolve().parents[1] / "data" / "zones.geojson"


def _round(coords):
    if isinstance(coords[0], (int, float)):
        return [round(coords[0], 5), round(coords[1], 5)]
    return [_round(c) for c in coords]


def load_zones() -> dict:
    if CACHE.exists():
        return json.loads(CACHE.read_text(encoding="utf8"))
    raw = requests.get(URL, timeout=120)
    raw.raise_for_status()
    features = []
    for f in raw.json()["features"]:
        p = f["properties"]
        features.append({
            "type": "Feature",
            "properties": {"LocationID": int(p["locationid"]), "zone": p["zone"], "borough": p["borough"]},
            "geometry": {"type": f["geometry"]["type"], "coordinates": _round(f["geometry"]["coordinates"])},
        })
    gj = {"type": "FeatureCollection", "features": features}
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(gj, separators=(",", ":")), encoding="utf8")
    return gj
