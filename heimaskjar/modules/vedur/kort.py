#!/usr/bin/env python3
"""
kort.py – sækir HARMONIE IG spá frá DMI og teiknar kort af Íslandi.
(heimaskjar weather module. Daily use: python -m heimaskjar.modules.vedur; this file is the
 lower-level tool and can still be run on its own: python -m heimaskjar.modules.vedur.kort …)

Draws a 2x2 map (cloud cover, precipitation, 2 m temperature, 10 m wind)
for one forecast hour from DMI's open HARMONIE IG (Iceland–Greenland) data.

Usage:
  python -m heimaskjar.modules.vedur.kort                       # newest run, +6 h, writes kort.png
  python -m heimaskjar.modules.vedur.kort --step 12 -o kort12.png
  python -m heimaskjar.modules.vedur.kort --step 12 --area hofudborg -o rvk12.png
  python -m heimaskjar.modules.vedur.kort --grib file.grib2     # draw from a GRIB2 file you already have
  python -m heimaskjar.modules.vedur.kort --list                # just show what the API offers
  python -m heimaskjar.modules.vedur.kort --batch web/spa       # daily: 07-19 today, one PNG per panel + manifest.json

Needs: pip install -r requirements.txt
The detailed coastline (IS 50V, Náttúrufræðistofnun, CC BY 4.0) is downloaded once per
area into the cache folder; cartopy's coarser coastline is the fallback.
If DMI asks for an API key, set DMI_API_KEY in the environment.
"""
import argparse
import datetime as dt
import os
import re
import sys
from pathlib import Path

import numpy as np

BASE = os.environ.get("DMI_BASE", "https://opendataapi.dmi.dk")
COLLECTION = "harmonie_ig_sf"
# lon_min, lat_min, lon_max, lat_max
AREAS = {
    "island":    (-25.0, 63.1, -13.0, 66.7),
    "sudvestur": (-23.3, 63.65, -19.9, 64.75),
    "hofudborg": (-22.45, 63.93, -21.25, 64.33),
    # same height and centre, ~40 % wider (Reykjanesbær to Hveragerði): fills a 16:9 screen
    "hofudborg_vitt": (-22.69, 63.93, -21.01, 64.33),
}
# places marked on the map, (name, lat, lon, smallest area to show them in)
PLACES = [
    ("Reykjavík", 64.146, -21.94, "island"),
    ("Akureyri", 65.684, -18.09, "island"),
    ("Egilsstaðir", 65.265, -14.40, "island"),
    ("Ísafjörður", 66.075, -23.13, "island"),
    ("Keflavík", 64.004, -22.56, "sudvestur"),
    ("Selfoss", 63.933, -21.00, "sudvestur"),
    ("Akranes", 64.321, -22.07, "sudvestur"),
    ("Hafnarfjörður", 64.067, -21.95, "hofudborg"),
    ("Mosfellsbær", 64.167, -21.70, "hofudborg"),
    ("Esja", 64.243, -21.64, "hofudborg"),
    ("Kópavogur", 64.112, -21.89, "hofudborg"),
]


# ---------------------------------------------------------------- download ---
def _get(url, params=None, stream=False):
    import requests
    params = dict(params or {})
    key = os.environ.get("DMI_API_KEY")
    if key:
        params["api-key"] = key
    r = requests.get(url, params=params, timeout=120, stream=stream)
    if r.status_code in (401, 403):
        sys.exit(f"DMI refused the request ({r.status_code}). Set DMI_API_KEY.\n{r.text[:300]}")
    r.raise_for_status()
    return r


def _parse_time(s):
    if not s:
        return None
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))


def list_items(hours_back=12):
    """Return [(model_run, valid_time, href)] for recent files."""
    url = f"{BASE}/v1/forecastdata/collections/{COLLECTION}/items"
    since = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=hours_back))
    params = {"datetime": since.strftime("%Y-%m-%dT%H:%M:%SZ") + "/..", "limit": 1000}
    out, pages, seen = [], 0, 0
    while url and pages < 20:
        r = _get(url, params)
        try:
            js = r.json()
        except ValueError:
            sys.exit(f"Not JSON from {r.url}:\n{r.text[:500]}")
        if pages == 0 and os.environ.get("DMI_DEBUG"):
            import json
            print("Request:", r.url, file=sys.stderr)
            print("Top-level keys:", list(js), file=sys.stderr)
            feats = js.get("features", [])
            print(json.dumps(feats[0] if feats else js, indent=2)[:3000], file=sys.stderr)
        params = None                      # 'next' links carry their own query
        seen += len(js.get("features", []))
        for f in js.get("features", []):
            p = f.get("properties", {})
            run = _parse_time(p.get("modelRun"))
            valid = _parse_time(p.get("datetime"))
            if run is None:                # fall back to times in the id
                ts = re.findall(r"(\d{4}-\d{2}-\d{2}T\d{6}Z)", f.get("id", ""))
                if ts:
                    run = dt.datetime.strptime(ts[0], "%Y-%m-%dT%H%M%SZ").replace(tzinfo=dt.timezone.utc)
            assets = f.get("assets") or f.get("asset") or {}   # DMI uses "asset"
            href = next((a.get("href") for a in assets.values() if a.get("href")), None)
            if run and valid and href:
                out.append((run, valid, href))
        url = next((l["href"] for l in js.get("links", []) if l.get("rel") == "next"), None)
        pages += 1
    if not out:
        print(f"Got {seen} items from DMI but could not read run time, valid time "
              f"and file link from any of them. Run again with DMI_DEBUG=1.", file=sys.stderr)
    return sorted(set(out))


# ------------------------------------------------------ partial download ---
# A GRIB2 file is a row of independent messages, one field each. Every message starts
# with a 16-byte header holding its total length, followed by short sections that say
# which field it is. We read just those headers (HTTP byte ranges) and then download
# only the messages we draw, ~13 of ~90, instead of the whole 400 MB file.
#
# (discipline, category, number, surface type, level, accumulated/max/min?)
# surface types: 1 = ground/sea surface, 102 = height above sea, 103 = height above ground
WANTED = {
    (0, 6, 32, 103, 0, False),    # cc   total cloud cover
    (0, 6, 3, 103, 0, False),     # lcc  low cloud
    (0, 6, 4, 103, 0, False),     # mcc  medium cloud
    (0, 6, 5, 103, 0, False),     # hcc  high cloud
    (0, 1, 52, 1, 0, True),       # tp   total precipitation (accumulated)
    (0, 1, 128, 1, 0, True),      # titspf  solid precipitation (accumulated)
    (0, 0, 0, 103, 2, False),     # 2t
    (0, 2, 2, 103, 10, False),    # 10u
    (0, 2, 3, 103, 10, False),    # 10v
    (0, 2, 1, 103, 10, False),    # 10si
    (0, 3, 0, 102, 0, False),     # pres at sea level
    (2, 0, 0, 103, 0, False),     # land-sea mask
}


def _field_key(head):
    """Identify a GRIB2 message from its first bytes; None if the bytes are too few."""
    import struct
    discipline = head[6]
    pos = 16
    while pos + 5 <= len(head):
        length = struct.unpack(">I", head[pos:pos + 4])[0]
        number = head[pos + 4]
        if number == 4:
            if pos + 28 > len(head):
                return None
            sec = head[pos:pos + 28]
            template = struct.unpack(">H", sec[7:9])[0]
            cat, num = sec[9], sec[10]
            surface, scale = sec[22], struct.unpack(">b", sec[23:24])[0]
            value = struct.unpack(">I", sec[24:28])[0]
            level = value / 10 ** scale if value != 0xFFFFFFFF else 0
            stat = template in (8, 11, 12)          # time-accumulated / max / min
            return (discipline, cat, num, surface, int(round(level)), stat)
        if length < 5:
            return None
        pos += length
    return None


def grib_index(url, session, read=None, size=None):
    """List (offset, length, key) for every message in a remote GRIB2 file."""
    import struct
    read = read or (lambda off, n: session.get(
        url, headers={"Range": f"bytes={off}-{off + n - 1}"}, timeout=60).content)
    out, off = [], 0
    while size is None or off < size:   # past the end S3 answers with an error page, not ""
        head = read(off, min(1024, size - off) if size else 1024)
        if len(head) < 16:
            break
        if head[:4] != b"GRIB" or head[7] != 2:
            raise ValueError(f"no GRIB2 message at byte {off}")
        total = struct.unpack(">Q", head[8:16])[0]
        key = _field_key(head)
        if key is None:                              # unusually long headers: read more
            key = _field_key(read(off, 8192))
        out.append((off, total, key))
        off += total
    return out


def partial_download(url, dest, session=None, read=None):
    """Download only the WANTED messages of a remote GRIB2 file into dest."""
    import requests
    session = session or requests.Session()
    read_fn = read or (lambda off, n: session.get(
        url, headers={"Range": f"bytes={off}-{off + n - 1}"}, timeout=120).content)
    size = None
    if read is None:
        r = session.head(url, timeout=60, allow_redirects=True)
        r.raise_for_status()
        size = int(r.headers["Content-Length"])
    index = grib_index(url, session, read_fn, size)
    keep = [(o, n) for o, n, k in index if k in WANTED]
    missing = WANTED - {k for _, _, k in index}
    if missing:
        print(f"Note: {len(missing)} wanted field(s) not in {url.rsplit('/', 1)[-1]}: "
              f"{sorted(missing)}", file=sys.stderr)
    tmp = Path(str(dest) + ".part")
    with open(tmp, "wb") as fh:
        for o, n in keep:
            data = read_fn(o, n)
            if len(data) != n:
                raise IOError(f"short read at byte {o}: {len(data)} of {n}")
            fh.write(data)
    tmp.rename(dest)
    mb = sum(n for _, n in keep) / 1e6
    print(f"  {len(keep)} of {len(index)} fields, {mb:.0f} MB", file=sys.stderr)


def fetch_file(url, dest):
    """Fields we need only, via byte ranges; whole file if that fails."""
    try:
        partial_download(url, dest)
    except Exception as e:
        print(f"  partial download failed ({e}); fetching the whole file", file=sys.stderr)
        tmp = Path(str(dest) + ".part")
        with _get(url, stream=True) as r, open(tmp, "wb") as fh:
            for chunk in r.iter_content(1 << 20):
                fh.write(chunk)
        tmp.rename(dest)


def download(step, dest_dir, with_prev=True):
    """Download +step (and +step-1, for hourly precipitation). Returns (path, prev_path)."""
    items = list_items()
    if not items:
        sys.exit("No files found. Try --list, or check the collection name / API key.")
    runs = sorted({r for r, _, _ in items}, reverse=True)
    for run in runs:                       # newest run that already has this step
        if any(r == run and v == run + dt.timedelta(hours=step) for r, v, _ in items):
            break
    else:
        sys.exit(f"No run has a +{step} h file yet.")
    def fetch(st):
        hit = [h for r, v, h in items if r == run and v == run + dt.timedelta(hours=st)]
        if not hit:
            return None
        dest = Path(dest_dir) / f"ig_{run:%Y%m%d%H}_{st:03d}.grib2"
        if not dest.exists():
            print(f"Downloading run {run:%Y-%m-%d %H} UTC, +{st} h …", file=sys.stderr)
            fetch_file(hit[0], dest)
        return dest

    return fetch(step), (fetch(step - 1) if with_prev and step > 0 else None)


# ------------------------------------------------------------------ fields ---
def inventory(path):
    """Print every field in the file with its range, to see what DMI provides."""
    import eccodes
    with open(path, "rb") as fh:
        while (h := eccodes.codes_grib_new_from_file(fh)) is not None:
            k = lambda key: eccodes.codes_get(h, key) if eccodes.codes_is_defined(h, key) else "-"
            v = eccodes.codes_get_values(h)
            print(f"{k('shortName'):>8} {str(k('paramId')):>7} {k('typeOfLevel'):>18} {str(k('level')):>5} "
                  f"{k('stepType'):>6} {k('units'):>14}  min {v.min():10.4g}  max {v.max():10.4g}  "
                  f"{k('discipline')}-{k('parameterCategory')}-{k('parameterNumber')}  {k('name')}")
            eccodes.codes_release(h)


def _messages(path):
    """Yield (key tuple, values 2-D, handle info) for every field in a GRIB file."""
    import eccodes
    with open(path, "rb") as fh:
        while (h := eccodes.codes_grib_new_from_file(fh)) is not None:
            g = lambda k: eccodes.codes_get(h, k) if eccodes.codes_is_defined(h, k) else None
            ni, nj = g("Ni") or g("Nx"), g("Nj") or g("Ny")
            info = {k: g(k) for k in ("shortName", "typeOfLevel", "level", "discipline",
                                      "parameterCategory", "parameterNumber", "stepType",
                                      "dataDate", "dataTime", "endStep")}
            vals = eccodes.codes_get_values(h).reshape(nj, ni)
            if "latlon" not in _messages.__dict__:
                lat = eccodes.codes_get_array(h, "latitudes").reshape(nj, ni)
                lon = eccodes.codes_get_array(h, "longitudes").reshape(nj, ni)
                _messages.latlon = (lat, np.where(lon > 180, lon - 360, lon))
            eccodes.codes_release(h)
            yield info, vals


# Which GRIB messages hold what. Matched on (shortName, typeOfLevel, level);
# a tuple of three ints matches (discipline, parameterCategory, parameterNumber).
FIELDS = {
    "cc":     ("cc", "heightAboveGround", 0),       # total cloud cover, 0..1
    "lcc":    ("lcc", "heightAboveGround", 0),      # %, used for a sanity check
    "mcc":    ("mcc", "heightAboveGround", 0),
    "hcc":    ("hcc", "heightAboveGround", 0),
    "tp":     ("tp", "surface", 0),                 # accumulated since run start, mm
    "snow":   ("titspf", "surface", 0),             # accumulated solid precip, mm
    "t2":     ("2t", "heightAboveGround", 2),
    "u":      ("10u", "heightAboveGround", 10),
    "v":      ("10v", "heightAboveGround", 10),
    "wind":   ("10si", "heightAboveGround", 10),
    "gust":   ("max_i10fg", "heightAboveGround", 10),
    "msl":    ("pres", "heightAboveSea", 0),        # sea-level pressure in this file
    "lsm":    (2, 0, 0),                            # land-sea mask (no shortName)
}


def _pick(path):
    _messages.__dict__.pop("latlon", None)
    got, meta = {}, None
    for info, vals in _messages(path):
        meta = meta or info
        for name, key in FIELDS.items():
            if name in got:
                continue
            if isinstance(key[0], int):
                ok = (info["discipline"], info["parameterCategory"], info["parameterNumber"]) == key
            else:
                ok = (info["shortName"], info["typeOfLevel"], info["level"]) == key
            if ok:
                got[name] = vals.astype(float)
    lat, lon = _messages.latlon
    return got, meta, lat, lon


def read_fields(path, prev_path=None):
    """Read the fields for one forecast hour; prev_path (hour before) gives hourly precip."""
    got, meta, lat, lon = _pick(path)
    f = {"lat": lat, "lon": lon, "lsm": got.get("lsm")}

    cc = got.get("cc")
    parts = [got[k] / 100 for k in ("lcc", "mcc", "hcc") if k in got]
    if cc is not None and parts:
        # total cover can't be less than any single layer; if it often is, cc isn't total
        if np.mean(cc + 0.05 < np.max(parts, axis=0)) > 0.02:
            print("Note: 'cc' looks smaller than the layers; combining low/mid/high instead.",
                  file=sys.stderr)
            cc = None
    if cc is None and parts:
        cc = np.max(parts, axis=0)          # maximum overlap: conservative
    f["cloud"] = cc
    f["layers"] = {k: got[k] / 100 for k in ("lcc", "mcc", "hcc") if k in got}

    f["precip"] = f["snowfrac"] = None
    f["precip_note"] = ""
    if prev_path and "tp" in got:
        prev, *_ = _pick(prev_path)
        if "tp" in prev:
            f["precip"] = np.clip(got["tp"] - prev["tp"], 0, None)
            f["precip_note"] = "mm/klst"
            if "snow" in got and "snow" in prev:
                sn = np.clip(got["snow"] - prev["snow"], 0, None)
                f["snowfrac"] = np.where(f["precip"] > 0.05, sn / np.maximum(f["precip"], 1e-6), np.nan)
    elif "tp" in got:
        f["precip"], f["precip_note"] = got["tp"], "mm alls"   # no hour before: total so far

    f["temp"] = got["t2"] - 273.15 if "t2" in got else None
    f["u"], f["v"] = got.get("u"), got.get("v")
    f["wind"] = got.get("wind")
    if f["wind"] is None and f["u"] is not None:
        f["wind"] = np.hypot(f["u"], f["v"])
    f["gust"] = got.get("gust")
    f["msl"] = got["msl"] / 100 if "msl" in got else None

    run = dt.datetime.strptime(f"{meta['dataDate']}{meta['dataTime']:04d}", "%Y%m%d%H%M")
    f["run"], f["valid"] = run, run + dt.timedelta(hours=int(meta["endStep"] or 0))
    return f


# --------------------------------------------------------------- coastline ---
# IS 50V Strandlína © Náttúrufræðistofnun (formerly Landmælingar Íslands), CC BY 4.0
LMI_WFS = ["https://gis.lmi.is/geoserver/wfs", "https://gis.natt.is/geoserver/wfs"]
LMI_LAYER = "IS_50V:strandlina_flakar"


def lmi_coastline(bb, cache_dir):
    """Coastline rings (list of Nx2 lon/lat arrays) for the box, downloaded once and cached."""
    import json
    import requests
    pad = .3
    box = (bb[0] - pad, bb[1] - pad / 2, bb[2] + pad, bb[3] + pad / 2)
    fn = Path(cache_dir) / ("lmi_strandlina_" + "_".join(f"{v:.2f}" for v in box) + ".geojson")
    if not fn.exists():
        Path(cache_dir).mkdir(exist_ok=True)
        params = {"service": "WFS", "version": "1.0.0", "request": "GetFeature",
                  "typeName": LMI_LAYER, "outputFormat": "application/json",
                  "srsName": "EPSG:4326", "bbox": ",".join(map(str, box)) + ",EPSG:4326"}
        last = None
        for url in LMI_WFS:
            try:
                print(f"Downloading LMI coastline from {url} …", file=sys.stderr)
                r = requests.get(url, params=params, timeout=300)
                r.raise_for_status()
                js = r.json()
                if js.get("features"):
                    fn.write_text(json.dumps(js))
                    break
                last = f"no features from {url}"
            except Exception as e:          # try the next host
                last = f"{url}: {e}"
        else:
            print(f"Could not get the LMI coastline ({last}); using a coarser one.", file=sys.stderr)
            return None
    js = json.loads(fn.read_text())
    rings = []
    for feat in js["features"]:
        g = feat.get("geometry") or {}
        t, c = g.get("type"), g.get("coordinates", [])
        polys = [c] if t == "Polygon" else c if t == "MultiPolygon" else []
        lines = [c] if t == "LineString" else c if t == "MultiLineString" else []
        for p in polys:
            rings.extend(p)
        rings.extend(lines)
    rings = [np.asarray(r, dtype=float)[:, :2] for r in rings if len(r) > 1]
    if rings and np.abs(rings[0]).max() > 360:      # came back in ISN93 metres: convert
        from pyproj import Transformer
        tr = Transformer.from_crs(3057, 4326, always_xy=True)
        rings = [np.column_stack(tr.transform(r[:, 0], r[:, 1])) for r in rings]
    return rings


# --------------------------------------------------------------- elevation ---
# ÍslandsDEM © Náttúrufræðistofnun, CC BY 4.0 – download a GeoTIFF from
# https://ftp.natt.is/gisdata/raster/ and pass it with --dem (or set DEM_FILE).
CONTOUR_STEP = {"island": 400, "sudvestur": 200, "hofudborg": 100, "hofudborg_vitt": 100}  # m


def dem_for_area(dem_file, area, bb, cache_dir, max_px=700):
    """(lon, lat, height) arrays for the area, cut from the DEM once and cached."""
    fn = Path(cache_dir) / f"dem_{area}.npz"
    # remember the last --dem file, so areas added later get contours without it
    remembered = Path(cache_dir) / "dem_file.txt"
    if dem_file:
        Path(cache_dir).mkdir(exist_ok=True)
        remembered.write_text(str(Path(dem_file).resolve()))
    elif remembered.exists() and Path(remembered.read_text().strip()).exists():
        if not fn.exists():
            dem_file = remembered.read_text().strip()
    # the cache remembers which file it came from; a different --dem file rebuilds it
    src_id = f"{Path(dem_file).resolve()}|{Path(dem_file).stat().st_size}" if dem_file else None
    if fn.exists():
        d = np.load(fn)
        cached_id = str(d["source"]) if "source" in d else None
        if src_id is None or cached_id == src_id:
            return d["lon"], d["lat"], d["z"]
    if not dem_file:
        print(f"No elevation contours for '{area}': run once with --dem FILE.", file=sys.stderr)
        return None
    import rasterio
    from rasterio.enums import Resampling
    from rasterio.windows import from_bounds
    from pyproj import Transformer
    with rasterio.open(dem_file) as src:
        # the "shade"/"daylight" files are pictures (brightness 0-255), not heights
        if src.count != 1 or src.dtypes[0] in ("uint8", "int8"):
            sys.exit(f"{dem_file} doesn't look like an elevation file ({src.count} band(s), "
                     f"{src.dtypes[0]}). Use the '..._zmasl.tif' file, which holds heights in metres.")
        to_src = Transformer.from_crs(4326, src.crs, always_xy=True)
        pad = .1
        xs, ys = to_src.transform([bb[0] - pad, bb[2] + pad, bb[0] - pad, bb[2] + pad],
                                  [bb[1] - pad, bb[1] - pad, bb[3] + pad, bb[3] + pad])
        win = from_bounds(min(xs), min(ys), max(xs), max(ys), src.transform)
        win = win.intersection(rasterio.windows.Window(0, 0, src.width, src.height))
        scale = max(1, max(win.width, win.height) / max_px)   # read at reduced resolution
        shape = (max(2, int(win.height / scale)), max(2, int(win.width / scale)))
        print(f"Reading elevation for '{area}' from {dem_file} …", file=sys.stderr)
        z = src.read(1, window=win, out_shape=shape, resampling=Resampling.average,
                     masked=True).astype(float).filled(np.nan)
        tr = rasterio.windows.transform(win, src.transform) * rasterio.Affine.scale(
            win.width / shape[1], win.height / shape[0])
        cols, rows = np.meshgrid(np.arange(shape[1]) + .5, np.arange(shape[0]) + .5)
        x, y = tr * (cols, rows)
        lon, lat = Transformer.from_crs(src.crs, 4326, always_xy=True).transform(x, y)
    z[z < -100] = np.nan
    Path(cache_dir).mkdir(exist_ok=True)
    if np.nanmax(z) > 2500 or np.nanmin(z) < -200:
        print(f"Warning: heights range {np.nanmin(z):.0f}–{np.nanmax(z):.0f}; is this an "
              f"elevation file in metres?", file=sys.stderr)
    np.savez_compressed(fn, lon=lon, lat=lat, z=z, source=src_id)
    return lon, lat, z


def relief_for_area(relief_file, area, bb, cache_dir, max_px=900):
    """Shaded relief for the area: (lon, lat, rgba 0..1, kind). kind is 'shade' (grey) or 'color'."""
    fn = Path(cache_dir) / f"relief_{area}.npz"
    src_id = f"{Path(relief_file).resolve()}|{Path(relief_file).stat().st_size}" if relief_file else None
    if not relief_file:
        return None
    if fn.exists():
        d = np.load(fn)
        if str(d["source"]) == src_id:
            return d["lon"], d["lat"], d["rgba"], str(d["kind"])
    import rasterio
    from rasterio.enums import ColorInterp, Resampling
    from rasterio.windows import from_bounds
    from pyproj import Transformer
    with rasterio.open(relief_file) as src:
        to_src = Transformer.from_crs(4326, src.crs, always_xy=True)
        pad = .1
        xs, ys = to_src.transform([bb[0] - pad, bb[2] + pad, bb[0] - pad, bb[2] + pad],
                                  [bb[1] - pad, bb[1] - pad, bb[3] + pad, bb[3] + pad])
        win = from_bounds(min(xs), min(ys), max(xs), max(ys), src.transform)
        win = win.intersection(rasterio.windows.Window(0, 0, src.width, src.height))
        scale = max(1, max(win.width, win.height) / max_px)
        shape = (max(2, int(win.height / scale)), max(2, int(win.width / scale)))
        print(f"Reading relief for '{area}' from {relief_file} "
              f"({src.count} band(s), {src.dtypes[0]}, {[c.name for c in src.colorinterp]}) …",
              file=sys.stderr)
        data = src.read(window=win, out_shape=(src.count, *shape), resampling=Resampling.average,
                        masked=True)
        mask = np.ma.getmaskarray(data).any(axis=0)
        data = data.filled(0).astype(float)
        palette = src.count == 1 and src.colorinterp[0] == ColorInterp.palette
        if palette:                          # one band of colour indexes: look them up
            cmap = src.colormap(1)
            lut = np.zeros((256, 4))
            for i, c in cmap.items():
                if i < 256:
                    lut[i] = np.array(c) / 255
            idx = np.clip(np.rint(src.read(1, window=win, out_shape=shape,
                                           resampling=Resampling.nearest)), 0, 255).astype(int)
            rgba, kind = lut[idx], "color"
        elif src.count >= 3:
            top = 255. if src.dtypes[0] == "uint8" else max(data[:3].max(), 1)
            rgb = np.moveaxis(data[:3], 0, -1) / top
            a = (data[3] / top) if src.count >= 4 else np.ones(shape)
            rgba, kind = np.dstack([rgb, a]), "color"
        else:
            g = data[0]
            lo, hi = np.percentile(g[~mask], [1, 99]) if (~mask).any() else (0, 1)
            g = np.clip((g - lo) / max(hi - lo, 1e-6), 0, 1)
            rgba, kind = np.dstack([g, g, g, np.ones(shape)]), "shade"
        rgba[mask, 3] = 0
        tr = rasterio.windows.transform(win, src.transform) * rasterio.Affine.scale(
            win.width / shape[1], win.height / shape[0])
        cols, rows = np.meshgrid(np.arange(shape[1]) + .5, np.arange(shape[0]) + .5)
        x, y = tr * (cols, rows)
        lon, lat = Transformer.from_crs(src.crs, 4326, always_xy=True).transform(x, y)
    Path(cache_dir).mkdir(exist_ok=True)
    np.savez_compressed(fn, lon=lon, lat=lat, rgba=rgba, kind=kind, source=src_id)
    return lon, lat, rgba, kind


def shade_overlay(rgba, strength):
    """Turn a grey hillshade into an overlay: shadows darken, sunlit slopes lighten, flat = clear."""
    g = rgba[..., 0]
    valid = rgba[..., 3] > 0
    flat = np.median(g[valid]) if valid.any() else .5
    d = g - flat
    out = np.zeros_like(rgba)
    out[..., :3] = (d > 0)[..., None]                 # white where lighter, black where darker
    out[..., 3] = np.clip(np.abs(d) / max(flat, 1 - flat) * strength, 0, 1) * valid
    return out


# -------------------------------------------------------------------- draw ---
PANEL_IDS = ["sky", "urkoma", "hiti", "vindur"]


def draw(f, out, area="island", coast=True, cache_dir="grib_cache", clouds="layers",
         dem_file=None, places=False, relief_file=None, relief_strength=.6, contours=True,
         split_dir=None, tag=""):
    """Draw the four panels: one 2x2 image at `out`, or with split_dir one PNG per panel
    named <panel>_<tag>.png. Returns the credit line for the data used."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.colors import BoundaryNorm, LinearSegmentedColormap, ListedColormap

    try:
        import cartopy.crs as ccrs
        import cartopy.feature as cfeature
        bb = AREAS[area]
        proj = ccrs.LambertConformal(central_longitude=(bb[0] + bb[2]) / 2,
                                     central_latitude=(bb[1] + bb[3]) / 2,
                                     standard_parallels=(64, 66))
        pc = ccrs.PlateCarree()
    except ImportError:
        ccrs = None
    bb = AREAS[area]

    # crop the grid to the area, with a margin of a few grid cells
    lon, lat = f["lon"], f["lat"]
    mx, my = (bb[2] - bb[0]) * .15 + .15, (bb[3] - bb[1]) * .15 + .06
    sub = (lon > bb[0] - mx) & (lon < bb[2] + mx) & (lat > bb[1] - my) & (lat < bb[3] + my)
    rows, cols = np.where(sub)
    r0, r1, c0, c1 = rows.min(), rows.max() + 1, cols.min(), cols.max() + 1
    crop = lambda a: None if a is None else a[r0:r1, c0:c1]
    lon, lat = crop(lon), crop(lat)

    def levels_style(colors, levels, extend):
        return dict(levels=levels, colors=colors, extend=extend)

    panels = [
        ("Ský eftir hæð" if clouds == "layers" and f.get("layers") else "Skýjahula", crop(f["cloud"]),
         dict(levels=np.linspace(0, 1, 9),
              cmap=LinearSegmentedColormap.from_list("sky", ["#2a6fb0", "#9fb9cf", "#e9edf0", "#ffffff"]),
              extend="neither"), "áttunduhlutar"),
        ("Úrkoma", crop(f["precip"]),
         levels_style(["#c6e8f7", "#9fd4f0", "#5fb0e3", "#2f8fd6", "#1d5fb0", "#5a3bb0", "#a42ca8",
                       "#e0287a"],
                      [.1, .2, .5, 1, 2, 3, 5, 7], "max"), f["precip_note"] or "mm"),
        ("Hiti í 2 m", crop(f["temp"]),
         dict(levels=np.arange(-20, 22, 2), cmap="RdYlBu_r", extend="both"), "°C"),
        ("Vindur í 10 m", crop(f["wind"]),
         levels_style(["#f3f7f2", "#cfe8c3", "#8fd17a", "#f2e35c", "#f5a43a", "#e8552c", "#b51f43", "#6d1a5a",
                       "#3e0f3a"],
                      [0, 3, 5, 8, 11, 14, 18, 24, 32], "max"), "m/s"),
    ]

    lmi = lmi_coastline(bb, cache_dir) if coast else None
    dem = dem_for_area(dem_file, area, bb, cache_dir) if contours else None
    relief = relief_for_area(relief_file, area, bb, cache_dir)
    weather_alpha = 1.0
    if relief is not None and relief[3] == "color":
        weather_alpha = 1 - .55 * relief_strength      # see the coloured relief through the weather
    credits = []                             # required by the CC BY licences
    if lmi:
        credits.append("Strandlína: IS 50V")
    if dem is not None:
        credits.append(f"hæðarlínur á {CONTOUR_STEP[area]} m: ÍslandsDEM")
    if relief is not None:
        credits.append("skygging: ÍslandsDEM")
    credit = ("; ".join(credits) + " – Náttúrufræðistofnun (CC BY 4.0)") if credits else ""
    order = list(AREAS)
    if not split_dir:
        fig = plt.figure(figsize=(14, 10.5), dpi=110)
    for i, (title, data, style, unit) in enumerate(panels, 1):
        cax = None
        if split_dir:                         # one figure per panel, laid out by hand
            fig = plt.figure(dpi=110)
            sub = (1, 1, 1)
        else:
            sub = (2, 2, i)
        if ccrs:
            ax = fig.add_subplot(*sub, projection=proj)
            ax.set_extent([bb[0], bb[2], bb[1], bb[3]], crs=pc)
            kw = dict(transform=pc)
            if split_dir:
                x0, x1, y0, y1 = ax.get_extent()
                asp = (x1 - x0) / (y1 - y0)
        else:
            ax = fig.add_subplot(*sub)
            ax.set_xlim(bb[0], bb[2]); ax.set_ylim(bb[1], bb[3])
            ax.set_aspect(1 / np.cos(np.radians((bb[1] + bb[3]) / 2)))
            kw = {}
            asp = (bb[2] - bb[0]) * np.cos(np.radians((bb[1] + bb[3]) / 2)) / (bb[3] - bb[1])
        if split_dir:
            # sizes in inches: same margins and same colour-bar strip on every panel and area,
            # map exactly the shape of the area so there's no empty space around it
            L, B, TOP, GAP, CB, TICKS, R, MAP_H = .05, .05, .30, .10, .16, .36, .03, 4.2
            map_w = MAP_H * asp
            W, H = L + map_w + GAP + CB + TICKS + R, B + MAP_H + TOP
            fig.set_size_inches(W, H)
            ax.set_position([L / W, B / H, map_w / W, MAP_H / H])
            cax = fig.add_axes([(L + map_w + GAP) / W, (B + .1) / H, CB / W, (MAP_H - .6) / H])
        ax.set_facecolor("#e4e8eb")
        if title == "Ský eftir hæð":
            draw_cloud_layers(fig, ax, lon, lat, {k: crop(v) for k, v in f["layers"].items()}, kw, cax)
        elif data is None:
            ax.text(.5, .5, "vantar í gögnum", transform=ax.transAxes, ha="center")
        else:
            m = ax.contourf(lon, lat, data, alpha=weather_alpha, zorder=2, **style, **kw)
            cb = colour_bar(fig, m, ax, cax, unit)
            if title == "Skýjahula":
                cb.set_ticks(np.linspace(0, 1, 9)); cb.set_ticklabels([str(k) for k in range(9)])
            if title == "Úrkoma":                  # 0,1 0,2 0,5 1 2 … with an Icelandic comma
                lv = style["levels"]
                cb.set_ticks(lv); cb.set_ticklabels([f"{v:g}".replace(".", ",") for v in lv])
        if title == "Úrkoma":
            if f.get("snowfrac") is not None:          # dots where most of it falls as snow
                sf = np.nan_to_num(crop(f["snowfrac"]))
                if (sf > .5).any():
                    ax.contourf(lon, lat, sf, levels=[.5, 2], colors="none", hatches=[".."], zorder=6, **kw)
                ax.text(.01, .01, "punktar: snjókoma", transform=ax.transAxes, fontsize=8)
            if f["msl"] is not None:
                step = 4 if area == "island" else 2
                cs = ax.contour(lon, lat, crop(f["msl"]), levels=np.arange(940, 1060, step),
                                colors="#333", linewidths=.7, zorder=6, **kw)
                ax.clabel(cs, fmt="%d", fontsize=7)
        if title.startswith("Vindur") and f["u"] is not None:
            # about 16 arrows across, whatever the area
            span = max(np.ptp(lon[(lat > bb[1]) & (lat < bb[3])]) if lon.size else 1, 1e-3)
            cells = max(lon.shape) * (bb[2] - bb[0]) / span
            s = max(1, int(round(cells / 16)))
            ax.quiver(lon[::s, ::s], lat[::s, ::s], crop(f["u"])[::s, ::s], crop(f["v"])[::s, ::s],
                      color="#222", scale=400, width=.0025, zorder=6, **kw)
        # coastline: LMI's if we have it, else cartopy's, else the model's own land-sea mask
        if lmi:
            from matplotlib.collections import LineCollection
            ax.add_collection(LineCollection(lmi, colors="#222", linewidths=.6, zorder=5, **kw))
        elif ccrs and coast:
            ax.add_feature(cfeature.COASTLINE.with_scale("10m"), linewidth=.8, edgecolor="#222")
        elif f["lsm"] is not None:
            ax.contour(lon, lat, crop(f["lsm"]), levels=[.5], colors="#222", linewidths=.8, **kw)
        if relief is not None:
            rlon, rlat, rgba, kind = relief
            if kind == "shade":                      # on top, so weather colours stay true
                ax.pcolormesh(rlon, rlat, shade_overlay(rgba, relief_strength),
                              shading="nearest", zorder=3, edgecolors="none", linewidth=0,
                              antialiased=False, rasterized=True, **kw)
            else:                                    # coloured relief underneath
                ax.pcolormesh(rlon, rlat, rgba, shading="nearest", zorder=1, edgecolors="none",
                              linewidth=0, antialiased=False, rasterized=True, **kw)
        if dem is not None:
            dlon, dlat, dz = dem
            step = CONTOUR_STEP[area]
            top = np.nanmax(dz) if np.isfinite(dz).any() else 0
            if top >= step:
                ax.contour(dlon, dlat, dz, levels=np.arange(step, top + step, step),
                           colors="#5a4632", linewidths=.45, alpha=.55, zorder=4, **kw)
        for name, plat, plon, minarea in (PLACES if places else []):
            inside = bb[0] < plon < bb[2] and bb[1] < plat < bb[3]
            if inside and order.index(minarea) <= order.index(area):
                ax.plot(plon, plat, "o", ms=3, color="#111", **kw)
                ax.text(plon, plat, " " + name, fontsize=8, va="bottom", color="#111", **kw)
        ax.set_title(title, fontsize=13, loc="left")
        if split_dir:
            fig.savefig(Path(split_dir) / f"{PANEL_IDS[i - 1]}_{tag}.png")
            plt.close(fig)

    if split_dir:
        return credit
    dagar = ["mán", "þri", "mið", "fim", "fös", "lau", "sun"]
    valid = f'{dagar[f["valid"].weekday()]} {f["valid"]:%d.%m. kl. %H:%M}'
    fig.suptitle(f"HARMONIE IG · gildir {valid} (UTC) · keyrsla {f['run']:%d.%m. %H} UTC · gögn: DMI",
                 fontsize=14, x=.02, ha="left")
    if credit:
        fig.text(.99, .005, credit, ha="right", va="bottom", fontsize=7, color="#555")
    fig.tight_layout(rect=(0, .01, 1, .96))
    fig.savefig(out)
    plt.close(fig)
    print(f"Wrote {out}", file=sys.stderr)
    return credit


CLOUD_LAYERS = [   # drawn bottom-up as seen from above: high first, low on top
    ("hcc", "Háský", "#8a8a8a"),
    ("mcc", "Miðský", "#3b7fd0"),
    ("lcc", "Lágský", "#d6453d"),
]


def colour_bar(fig, mappable, ax, cax, unit):
    """Colour bar in its own strip (cax) or beside the map; unit written above it."""
    if cax is not None:
        cb = fig.colorbar(mappable, cax=cax)
    else:
        cb = fig.colorbar(mappable, ax=ax, shrink=.82, pad=.02)
    cb.ax.tick_params(labelsize=9, length=3, pad=2)
    # unit above the bar, starting at its left edge so it never covers the map
    cb.ax.set_title(unit, fontsize=9, pad=12 if cb.extend != "neither" else 5, loc="left")
    return cb


def draw_cloud_layers(fig, ax, lon, lat, layers, kw, cax=None):
    """Low, mid and high cloud in their own colours; opacity shows how much of the sky."""
    from matplotlib.colors import to_rgb
    from matplotlib.patches import Patch
    levels = [.125, .375, .625, .875, 1.01]        # 1-2, 3-4, 5-6, 7-8 eighths
    alphas = [.18, .38, .58, .8]
    ax.set_facecolor("#ffffff")
    handles = []
    for key, name, color in CLOUD_LAYERS:
        if key not in layers:
            continue
        rgb = to_rgb(color)
        ax.contourf(lon, lat, np.clip(layers[key], 0, 1), levels=levels,
                    colors=[(*rgb, a) for a in alphas], zorder=2, **kw)
        handles.append(Patch(facecolor=(*rgb, .7), edgecolor="none", label=name))
    ax.legend(handles=handles, loc="upper left", fontsize=8, framealpha=.85)
    # key: how strong a colour means how many eighths of the sky
    from matplotlib.cm import ScalarMappable
    from matplotlib.colors import BoundaryNorm, ListedColormap
    key = ListedColormap([(.42, .42, .42, a) for a in [0] + alphas])
    sm = ScalarMappable(norm=BoundaryNorm([0, 1, 3, 5, 7, 8], key.N), cmap=key)
    cb = colour_bar(fig, sm, ax, cax, "/8")
    cb.set_ticks(range(9))


def style_kw(style):
    return {k: v for k, v in style.items() if k != "extend"}


# ------------------------------------------------------------------- batch ---
PANEL_TITLES = {"sky": "Ský", "urkoma": "Úrkoma", "hiti": "Hiti", "vindur": "Vindur"}


def point_values(f, ij):
    """The forecast at one grid cell, as plain numbers for summary.json."""
    i, j = ij
    v = lambda a: None if a is None or not np.isfinite(a[i, j]) else round(float(a[i, j]), 2)
    u, w = v(f["u"]), v(f["v"])
    layers = f.get("layers") or {}
    return {
        "temp": v(f["temp"]), "precip": v(f["precip"]), "snow": v(f.get("snowfrac")),
        "wind": v(f["wind"]),
        # direction the wind blows FROM, degrees (relative to the model grid; within a few °)
        "dir": None if u is None else round(float((270 - np.degrees(np.arctan2(w, u))) % 360)),
        "cloud": v(f["cloud"]),
        "low": v(layers.get("lcc")), "mid": v(layers.get("mcc")), "high": v(layers.get("hcc")),
    }


def batch(outdir, cache, areas, first=7, last=19, day=None, grib_dir=None, point=None, **draw_kw):
    """Render hours first..last of `day` (default today, UTC = Iceland time) for each area,
    one PNG per panel and hour, plus manifest.json; then swap the folder into place.
    point = {"name", "lat", "lon"} adds that place's hourly forecast to the manifest."""
    import json
    import shutil
    day = day or dt.datetime.now(dt.timezone.utc).date()
    t0 = dt.datetime.combine(day, dt.time(first), dt.timezone.utc)
    want = [t0 + dt.timedelta(hours=h) for h in range(-1, last - first + 1)]   # -1: for precip

    files = {}
    if grib_dir:                              # testing: files named <anything>_HH.grib2
        for v in want:
            hits = sorted(Path(grib_dir).glob(f"*_{v:%H}.grib2"))
            if hits:
                files[v] = hits[0]
        run = None
    else:
        items = list_items(hours_back=max(12, int((dt.datetime.now(dt.timezone.utc) - want[0])
                                                  .total_seconds() // 3600) + 2))
        runs = sorted({r for r, _, _ in items}, reverse=True)
        run = next((r for r in runs
                    if all(any(rr == r and vv == v for rr, vv, _ in items) for v in want)), None)
        if run is None:
            sys.exit(f"No model run covers {want[0]:%d.%m. %H}–{want[-1]:%H} UTC yet.")
        print(f"Using run {run:%Y-%m-%d %H} UTC", file=sys.stderr)
        Path(cache).mkdir(exist_ok=True)
        for v in want:
            href = next(h for rr, vv, h in items if rr == run and vv == v)
            step = int((v - run).total_seconds() // 3600)
            dest = Path(cache) / f"ig_{run:%Y%m%d%H}_{step:03d}.grib2"
            if not dest.exists():
                print(f"Downloading +{step} h (valid {v:%H}:00) …", file=sys.stderr)
                fetch_file(href, dest)
            files[v] = dest

    out = Path(outdir)
    new = out.with_name(out.name + ".new")
    shutil.rmtree(new, ignore_errors=True)
    new.mkdir(parents=True)
    manifest = {"generated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                "run": run.isoformat() if run else None, "areas": {}, "hours": [],
                "panels": [{"id": p, "title": PANEL_TITLES[p]} for p in PANEL_IDS]}
    credit = ""
    ij = None
    if point:
        manifest["point"] = dict(point, hours=[])
    for area in areas:
        (new / area).mkdir()
        for v in want[1:]:
            if v not in files:
                print(f"Missing data for {v:%H}:00, skipping", file=sys.stderr)
                continue
            f = read_fields(files[v], files.get(v - dt.timedelta(hours=1)))
            manifest["run"] = manifest["run"] or f["run"].replace(tzinfo=dt.timezone.utc).isoformat()
            credit = draw(f, None, area=area, cache_dir=cache, split_dir=new / area,
                          tag=f"{v:%H}", **draw_kw) or credit
            if area == areas[0]:
                manifest["hours"].append(v.isoformat())
                if point:
                    if ij is None:                    # nearest grid cell, found once
                        d2 = ((f["lat"] - point["lat"]) ** 2 +
                              ((f["lon"] - point["lon"]) * np.cos(np.radians(point["lat"]))) ** 2)
                        ij = np.unravel_index(np.argmin(d2), d2.shape)
                    manifest["point"]["hours"].append(dict(time=v.isoformat(), **point_values(f, ij)))
            print(f"  {area} {v:%H}:00 done", file=sys.stderr)
        manifest["areas"][area] = {"bbox": AREAS[area], "path": area,
                                   "credit": "Veðurspá: HARMONIE IG, DMI. " + (credit + "." if credit else "")}
        credit = ""
    manifest["credit"] = manifest["areas"][areas[0]]["credit"]
    (new / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1))

    old = out.with_name(out.name + ".old")    # swap in one step so the page never sees half
    shutil.rmtree(old, ignore_errors=True)
    if out.exists():
        out.rename(old)
    new.rename(out)
    shutil.rmtree(old, ignore_errors=True)
    print(f"Wrote {out}/ ({len(manifest['hours'])} hours × {len(areas)} area(s))", file=sys.stderr)

    if run:                                   # forecast files from other runs aren't needed
        for p in Path(cache).glob("ig_*.grib2"):
            if not p.name.startswith(f"ig_{run:%Y%m%d%H}_"):
                p.unlink()


# -------------------------------------------------------------------- main ---
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--step", type=int, default=6, help="forecast hour (default 6)")
    ap.add_argument("--grib", help="draw from this GRIB2 file instead of downloading")
    ap.add_argument("-o", "--out", default="kort.png")
    ap.add_argument("--cache", default="grib_cache")
    ap.add_argument("--list", action="store_true", help="list available runs/steps and exit")
    ap.add_argument("--area", choices=list(AREAS), default="island",
                    help="map area: island, sudvestur (southwest), hofudborg (capital area) "
                         "or hofudborg_vitt (capital area, wider)")
    ap.add_argument("--prev", help="with --grib: the file for the hour before, for hourly precipitation")
    ap.add_argument("--clouds", choices=["layers", "total"], default="layers",
                    help="cloud panel: low/mid/high in colours (default) or total cover")
    ap.add_argument("--dem", default=os.environ.get("DEM_FILE"),
                    help="ÍslandsDEM GeoTIFF for elevation contours (read once per area, then cached)")
    ap.add_argument("--relief", default=os.environ.get("RELIEF_FILE"),
                    help="ÍslandsDEM '_shade.tif' (grey, drawn over the weather) or "
                         "'_daylight.tif' (coloured, drawn under it)")
    ap.add_argument("--relief-strength", type=float, default=.6,
                    help="0..1, how strongly the relief shows (default 0.6)")
    ap.add_argument("--no-contours", action="store_true", help="skip elevation contours")
    ap.add_argument("--places", action="store_true", help="mark towns on the map")
    ap.add_argument("--no-coast", action="store_true")
    ap.add_argument("--inventory", action="store_true", help="list the fields in the GRIB file and exit")
    ap.add_argument("--batch", metavar="OUTDIR",
                    help="daily mode: render every hour of --hours, one PNG per panel, into OUTDIR")
    ap.add_argument("--hours", default="7-19", help="with --batch: first-last hour (default 7-19)")
    ap.add_argument("--areas", default="hofudborg",
                    help="with --batch: comma-separated areas (default hofudborg)")
    ap.add_argument("--grib-dir", help=argparse.SUPPRESS)   # testing: local files instead of DMI
    a = ap.parse_args()

    if a.batch:
        first, last = map(int, a.hours.split("-"))
        areas = [x.strip() for x in a.areas.split(",") if x.strip()]
        bad = [x for x in areas if x not in AREAS]
        if bad:
            sys.exit(f"Unknown area(s): {bad}; choose from {list(AREAS)}")
        batch(a.batch, a.cache, areas, first, last, grib_dir=a.grib_dir,
              coast=not a.no_coast, clouds=a.clouds, dem_file=a.dem, places=a.places,
              relief_file=a.relief, relief_strength=a.relief_strength,
              contours=not a.no_contours)
        return
    if a.list:
        for run, valid, href in list_items()[-40:]:
            print(f"run {run:%Y-%m-%d %H}Z  +{int((valid - run).total_seconds() // 3600):3d} h  {href}")
        return
    if a.grib:
        path, prev = Path(a.grib), (Path(a.prev) if a.prev else None)
    else:
        Path(a.cache).mkdir(exist_ok=True)
        path, prev = download(a.step, a.cache, with_prev=not a.inventory)
    if a.inventory:
        inventory(path)
        return
    draw(read_fields(path, prev), a.out, area=a.area, coast=not a.no_coast,
         cache_dir=a.cache, clouds=a.clouds, dem_file=a.dem, places=a.places,
         relief_file=a.relief, relief_strength=a.relief_strength, contours=not a.no_contours)


if __name__ == "__main__":
    main()
