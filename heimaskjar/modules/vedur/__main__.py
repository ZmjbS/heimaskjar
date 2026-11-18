"""Daily weather job: python -m heimaskjar.modules.vedur [--config FILE]

Renders the forecast maps (hours from the settings) into <data_dir>/vedur/ – today's
before switch_hour (default 12), tomorrow's after it –
writes summary.json for the overview card, and refreshes <data_dir>/site.json.
"""
import argparse
import datetime as dt
import json
import sys

from heimaskjar.core import config
from heimaskjar.modules.vedur import kort


def summarise(manifest):
    """Numbers for the overview card, from the place's hourly forecast."""
    pt = manifest.get("point") or {}
    hours = pt.get("hours", [])
    vals = lambda k: [h[k] for h in hours if h.get(k) is not None]
    temps, rain, wind = vals("temp"), vals("precip"), vals("wind")
    snow = [h["precip"] * (h.get("snow") or 0) for h in hours if h.get("precip")]
    return {
        "module": "vedur",
        "title": "Veður",
        "updated": manifest["generated"],
        "run": manifest.get("run"),
        "place": pt.get("name"),
        "first_hour": manifest["hours"][0] if manifest["hours"] else None,
        "stats": {
            "temp_min": min(temps) if temps else None,
            "temp_max": max(temps) if temps else None,
            "precip_total": round(sum(rain), 1) if rain else None,
            "snow_total": round(sum(snow), 1) if snow else None,
            "wind_max": max(wind) if wind else None,
        },
        "hours": hours,
        # the overview shows a small copy of this panel for the current hour
        "thumbnail": {"area": next(iter(manifest["areas"]), None), "panel": "urkoma"},
        "credit": manifest.get("credit"),
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", help="settings file (default: see README)")
    ap.add_argument("--day", help="YYYY-MM-DD instead of today/tomorrow")
    ap.add_argument("--grib-dir", help=argparse.SUPPRESS)        # testing: local GRIB files
    a = ap.parse_args()

    cfg = config.load(a.config)
    m = cfg.module("vedur")
    if not m.get("enabled", True):
        print("Weather module is disabled in the settings.", file=sys.stderr)
        return
    first, last = map(int, str(m.get("hours", "7-19")).split("-"))
    areas = m.get("areas", ["hofudborg"])
    bad = [x for x in areas if x not in kort.AREAS]
    if bad:
        sys.exit(f"Unknown area(s) in settings: {bad}; choose from {list(kort.AREAS)}")
    out = cfg.data_dir / "vedur"
    cache = cfg.cache_dir / "vedur"
    cache.mkdir(parents=True, exist_ok=True)
    dem = cfg.path_of(m.get("dem_file"))
    relief = cfg.path_of(m.get("relief_file"))
    if a.day:
        day = dt.date.fromisoformat(a.day)
    else:
        # before switch_hour: today's forecast; from then on: tomorrow's (Iceland = UTC)
        now = dt.datetime.now(dt.timezone.utc)
        day = now.date() + dt.timedelta(days=int(now.hour >= int(m.get("switch_hour", 12))))
    print(f"Forecast for {day} ({first:02d}–{last:02d})", file=sys.stderr)

    kort.batch(str(out), str(cache), areas, first, last, day=day, grib_dir=a.grib_dir,
               point=m.get("place", {"name": "Reykjavík", "lat": 64.146, "lon": -21.94}),
               coast=m.get("coastline", True), clouds=m.get("clouds", "layers"),
               dem_file=str(dem) if dem else None, places=False,
               relief_file=str(relief) if relief else None,
               relief_strength=float(m.get("relief_strength", .6)),
               contours=m.get("contours", True))

    manifest = json.loads((out / "manifest.json").read_text())
    summary = out / "summary.json"
    summary.write_text(json.dumps(summarise(manifest), ensure_ascii=False, indent=1))
    config.write_site(cfg)
    print(f"Wrote {summary} and {cfg.data_dir / 'site.json'}", file=sys.stderr)


if __name__ == "__main__":
    main()
