"""City Scan lookups for the app: reads prebuilt packs (packs/<slug>.json.gz), never runs the model or the network.

Each function returns (status, payload). A payload with an "error" key is a plain-text message for the client.
"""
import functools
import gzip
import json
import re
from pathlib import Path

import market_share

PACKS = Path(__file__).parent / "packs"
SLUG = re.compile(r"[a-z0-9-]{1,40}")
TOP_DEFAULT, TOP_MAX = 10, 25


def slugify(city):
    return re.sub(r"[^a-z0-9]+", "-", city.strip().lower()).strip("-")


def available():
    return sorted(p.name[:-len(".json.gz")] for p in PACKS.glob("*.json.gz"))


@functools.lru_cache(maxsize=8)
def _load(slug):
    with gzip.open(PACKS / f"{slug}.json.gz", "rt", encoding="utf-8") as f:
        return json.load(f)


def pack(city):
    """The pack for a user-typed city name, or None. Only slugs of files that exist are ever opened."""
    slug = slugify(city)
    return _load(slug) if SLUG.fullmatch(slug) and slug in available() else None


def _no_pack(city):
    return 404, {"error": f"No city scan is available for {city!r}. Available: {', '.join(available())}."}


def _thin(p):
    q = p["quality"]
    return {"city": p["city"]["name"], "data_thin": True, "reason": q["reason"],
            "data_confidence_food": q["data_confidence_food"], "threshold": q["threshold"],
            "threshold_note": q["threshold_note"], "attribution": p["attribution"]}


def scan(city, concept, top=None):
    if not city or not concept:
        return 400, {"error": "city and concept are required"}
    p = pack(city[:120])
    if p is None:
        return _no_pack(city[:120])
    if not p["quality"]["gate_passed"]:
        thin = _thin(p)
        center = (p.get("city") or {}).get("center") or []
        if len(center) == 2:
            thin["market_share"] = market_share.estimate(
                city, center[0], center[1], display_name=(p.get("city") or {}).get("display_name") or "",
                gate_passed=False)
        return 200, thin
    try:
        top = TOP_DEFAULT if top in (None, "") else max(1, min(TOP_MAX, int(top)))
    except ValueError:
        return 400, {"error": "top must be a whole number"}
    want = concept[:300].strip().lower()
    s = next((s for s in p["scans"] if want in (s["concept"].lower(), s["label"].lower())), None)
    if s is None:
        names = ", ".join(s["concept"] for s in p["scans"])
        return 404, {"error": f"{concept[:300]!r} is not scanned for {p['city']['name']} yet. Scanned: {names}."}
    return 200, {"city": p["city"]["name"], "data_thin": False, "concept": s["concept"], "label": s["label"],
                 "model": {k: s[k] for k in ("fitted", "n_presences", "auc_cv", "auc_ci95", "ranking")
                           if s.get(k) is not None},
                 "top": s["top"][:top], "limits": s["limits"], "built_at": p["built_at"],
                 "attribution": p["attribution"]}


def cell_of(grid, lat, lon):
    s, w, n, e = grid["bbox"]
    if not (s <= lat <= n and w <= lon <= e):
        return None
    r = min(int((lat - s) / grid["dlat"]), grid["rows"] - 1)
    c = min(int((lon - w) / grid["dlon"]), grid["cols"] - 1)
    return r * grid["cols"] + c


def viable(row):
    """A ceiling at or below zero means no rent reaches the survival target here: say so, never show a negative rent."""
    if row["rent_max"] > 0:
        r80, ci = row.get("rent_max_80"), row.get("ci90")
        return row | {"viable": True, "ci90": ci and [max(ci[0], 0.0), ci[1]],
                      "rent_max_80": r80 if r80 is not None and r80 > 0 else None}
    return row | {"viable": False, "rent_max": None, "ci90": None, "rent_max_80": None,
                  "note": "Not viable at any rent here: below the city's 3-year survival rate even rent-free."}


def _with_share(payload, city, lat, lon, p):
    payload["market_share"] = market_share.estimate(
        city, lat, lon, display_name=(p.get("city") or {}).get("display_name") or "",
        gate_passed=bool((p.get("quality") or {}).get("gate_passed")))
    return payload


def unit(city, lat, lon):
    if not city or lat in (None, "") or lon in (None, ""):
        return 400, {"error": "city, lat and lon are required"}
    try:
        lat, lon = float(lat), float(lon)
    except ValueError:
        return 400, {"error": "lat and lon must be numbers"}
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return 400, {"error": "lat or lon out of range"}
    p = pack(city[:120])
    if p is None:
        return _no_pack(city[:120])
    if not p["quality"]["gate_passed"]:
        return 200, _with_share(_thin(p), city, lat, lon, p)
    j = cell_of(p["grid"], lat, lon)
    if j is None or j not in p["study"]:
        return 400, {"error": f"That point is outside the scanned area of {p['city']['name']}."}
    out = {"city": p["city"]["name"], "data_thin": False, "point": {"lat": lat, "lon": lon}, "cell": j,
           "built_at": p["built_at"], "attribution": p["attribution"]}
    units = p.get("units")
    if not units:
        return 200, _with_share(out | {"calibrated": False, "rent_ceiling": None,
                                       "note": "Rent ceiling not calibrated for this city yet."}, city, lat, lon, p)
    rows = [viable(r) for r in units["cells"].get(str(j), [])]
    if not rows:
        return 200, _with_share(out | {"calibrated": True, "rent_ceiling": None,
                                       "note": "No rent ceiling was computed for this cell."}, city, lat, lon, p)
    src = units.get("survival_source")
    note = ("Highest monthly rent at which each tenant type still reaches its city's official 3-year survival rate"
            + (f" ({src})." if src else ".")
            + ("" if units.get("location_specific", True) else
               " Same figure in every cell of this city: location did not predict survival in held-out data here.")
            + " A model, not a forecast.")
    return 200, _with_share(out | {"calibrated": True, "currency": units["currency"], "rent_ceiling": rows, "note": note},
                            city, lat, lon, p)
