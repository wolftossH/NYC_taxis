"""Land outside NYC for the map backdrop: Census cartographic county boundaries (clipped to the shoreline),
cropped to the area around the taxi zones and cached as WGS84 GeoJSON. Anything not covered by land is water.

Stdlib only: the shapefile is read straight from the zip with struct, so no GIS dependency is needed.
"""
import io
import json
import struct
import zipfile
from pathlib import Path

import requests

URL = "https://www2.census.gov/geo/tiger/GENZ2023/shp/cb_2023_us_county_500k.zip"
CACHE = Path(__file__).resolve().parents[1] / "data" / "basemap.geojson"
NYC = {"36005", "36047", "36061", "36081", "36085"}  # drawn by the taxi zones themselves
BOX = (-74.45, 40.35, -73.50, 41.05)  # lon/lat crop, comfortably larger than the zones' extent


def _dbf(raw: bytes) -> list[dict]:
    n, hlen, rlen = struct.unpack("<IHH", raw[4:12])
    fields, off = [], 32
    while raw[off] != 0x0D:
        fields.append((raw[off:off + 11].split(b"\0")[0].decode(), raw[off + 16]))
        off += 32
    rows = []
    for i in range(n):
        rec, pos, row = raw[hlen + i * rlen: hlen + (i + 1) * rlen], 1, {}
        for name, size in fields:
            row[name] = rec[pos:pos + size].decode("latin-1").strip()
            pos += size
        rows.append(row)
    return rows


def _shp(raw: bytes):
    """Yield each polygon record as a list of rings (lists of [lon, lat])."""
    off = 100
    while off < len(raw):
        clen = struct.unpack(">i", raw[off + 4:off + 8])[0] * 2
        body, off = raw[off + 8:off + 8 + clen], off + 8 + clen
        if struct.unpack("<i", body[:4])[0] != 5:  # 5 = polygon
            yield []
            continue
        nparts, npts = struct.unpack("<ii", body[36:44])
        parts = list(struct.unpack(f"<{nparts}i", body[44:44 + 4 * nparts])) + [npts]
        xy = struct.unpack(f"<{2 * npts}d", body[44 + 4 * nparts:44 + 4 * nparts + 16 * npts])
        yield [[[xy[2 * j], xy[2 * j + 1]] for j in range(parts[k], parts[k + 1])] for k in range(nparts)]


def _clip(ring):
    """Sutherland-Hodgman clip of one ring to BOX."""
    x0, y0, x1, y1 = BOX
    edges = [(lambda p: p[0] >= x0, 0, x0), (lambda p: p[0] <= x1, 0, x1),
             (lambda p: p[1] >= y0, 1, y0), (lambda p: p[1] <= y1, 1, y1)]
    for inside, axis, v in edges:
        out = []
        for i, cur in enumerate(ring):
            prev = ring[i - 1]
            if inside(cur) != inside(prev):
                t = (v - prev[axis]) / (cur[axis] - prev[axis])
                out.append([prev[0] + t * (cur[0] - prev[0]), prev[1] + t * (cur[1] - prev[1])])
            if inside(cur):
                out.append(cur)
        ring = out
        if not ring:
            return []
    return [[round(x, 4), round(y, 4)] for x, y in ring] + ([[round(ring[0][0], 4), round(ring[0][1], 4)]])


def _clockwise(ring):  # shapefile outer rings are clockwise, holes counter-clockwise (same as d3-geo)
    return sum((b[0] - a[0]) * (b[1] + a[1]) for a, b in zip(ring, ring[1:])) > 0


def load_basemap() -> dict:
    if CACHE.exists():
        return json.loads(CACHE.read_text(encoding="utf8"))
    r = requests.get(URL, timeout=300)
    r.raise_for_status()
    z = zipfile.ZipFile(io.BytesIO(r.content))
    name = lambda ext: next(n for n in z.namelist() if n.endswith(ext))
    rows = _dbf(z.read(name(".dbf")))
    features = []
    for row, rings in zip(rows, _shp(z.read(name(".shp")))):
        if row["STATEFP"] not in ("34", "36", "09") or row["GEOID"] in NYC:
            continue
        polys = []
        for ring in rings:
            c = _clip(ring)
            if len(c) < 4:
                continue
            if _clockwise(c) or not polys:
                polys.append([c])
            else:
                polys[-1].append(c)
        if polys:
            features.append({"type": "Feature", "properties": {"name": row["NAME"], "state": row["STATEFP"]},
                             "geometry": {"type": "MultiPolygon", "coordinates": polys}})
    gj = {"type": "FeatureCollection", "features": features}
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(gj, separators=(",", ":")), encoding="utf8")
    return gj


if __name__ == "__main__":
    g = load_basemap()
    print(len(g["features"]), sorted(f["properties"]["name"] for f in g["features"]), CACHE.stat().st_size)
