"""City Scan lookups for the app: reads prebuilt packs (packs/<slug>.json.gz), never runs the model or the network.

Each function returns (status, payload). A payload with an "error" key is a plain-text message for the client.
"""
import functools
import gzip
import json
import os
import re
from pathlib import Path

import census
import cityscan_breakeven as be
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


def cell_box(grid, j):
    s, w, n, e = grid["bbox"]
    r, c = divmod(int(j), grid["cols"])
    south = s + r * grid["dlat"]
    west = w + c * grid["dlon"]
    return [south, west, min(south + grid["dlat"], n), min(west + grid["dlon"], e)]


def _uniq_comps(rows, n=3):
    out, seen = [], set()
    for row in rows or []:
        name = (row or {}).get("name")
        if not name or name in seen:
            continue
        seen.add(name)
        out.append(row)
        if len(out) >= n:
            break
    return out


def _cell_card(scan, top):
    c = top.get("centroid") or {}
    return {
        "neighborhood": top.get("neighborhood"),
        "concept": scan.get("concept"),
        "competitors": _uniq_comps(top.get("nearest_competitors")),
        "suitability": top.get("suitability"),
        "huff": top.get("market_en"),
        "reasons": (top.get("reasons_en") or [])[:3],
        "watch": (top.get("watch_out_en") or [])[:2],
        "centroid": {"lat": c["lat"], "lon": c["lon"]} if c.get("lat") is not None and c.get("lon") is not None else None,
        "cell": top.get("id"),
    }


def nearest_top(pack, lat, lon):
    """Closest ranked cell in the pack's scans: neighborhood, OSM reasons, Huff, competitors."""
    best, best_d = None, None
    for s in pack.get("scans") or []:
        for t in s.get("top") or []:
            c = t.get("centroid") or {}
            if c.get("lat") is None or c.get("lon") is None:
                continue
            d = (c["lat"] - lat) ** 2 + (c["lon"] - lon) ** 2
            if best_d is None or d < best_d:
                best, best_d = _cell_card(s, t), d
    return best


def nearby_cells(pack, lat, lon, n=8):
    """Distinct neighborhood centroids around the pin, for the map. Pack data, no extra network."""
    ranked = []
    for s in pack.get("scans") or []:
        for t in s.get("top") or []:
            c = t.get("centroid") or {}
            if c.get("lat") is None or c.get("lon") is None:
                continue
            d = (c["lat"] - lat) ** 2 + (c["lon"] - lon) ** 2
            ranked.append((d, s, t))
    ranked.sort(key=lambda x: x[0])
    seen, out = set(), []
    for _, s, t in ranked:
        hood = t.get("neighborhood") or ""
        if hood in seen:
            continue
        seen.add(hood)
        card = _cell_card(s, t)
        out.append({k: card[k] for k in ("neighborhood", "concept", "centroid", "suitability")})
        if len(out) >= n:
            break
    return out


def spot(city, lat, lon):
    """Pin on the scanned city: Census tract + pack cell. Census is live; failures degrade to the pack."""
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
        return 200, _thin(p)
    j = cell_of(p["grid"], lat, lon)
    if j is None or j not in p["study"]:
        return 400, {"error": f"That point is outside the scanned area of {p['city']['name']}."}
    demo = None
    try:
        demo = census.card(lat, lon)
    except Exception:
        demo = None
    place = nearest_top(p, lat, lon)
    nearby = nearby_cells(p, lat, lon)
    return 200, {
        "city": p["city"]["name"], "data_thin": False,
        "point": {"lat": lat, "lon": lon}, "cell": j, "bounds": cell_box(p["grid"], j),
        "census": demo, "place": place, "nearby": nearby,
        "attribution": p["attribution"],
        "note": "A cell is a block to scout, not a vetted address. Census figures are ACS tract aggregates.",
    }


def _taste_places(res):
    """Named Qloo entities. Names without coordinates still count — the pin is the geography."""
    out, seen = [], set()

    def walk(o):
        if len(out) >= 8:
            return
        if isinstance(o, dict):
            name = o.get("name")
            if isinstance(name, str):
                name = name.strip()
                if name and name not in seen:
                    seen.add(name)
                    q = o.get("query") if isinstance(o.get("query"), dict) else {}
                    w = o.get("affinity", q.get("affinity"))
                    try:
                        w = float(w) if w is not None else None
                    except (TypeError, ValueError):
                        w = None
                    out.append({"name": name, "affinity": w})
                    return
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(res)
    return out


def taste(concept, location):
    """Qloo on the pin: only a concept and a place name. Never persisted. Offline when the key is missing."""
    concept = (concept or "").strip()[:300]
    location = (location or "").strip()[:120]
    if not concept or not location:
        return 400, {"error": "concept and location are required"}
    empty = {"status": "offline", "places": [], "loves": [], "concept": concept, "location": location, "tag": None}
    if not os.environ.get("QLOO_API_KEY"):
        return 200, empty
    try:
        from concurrent.futures import ThreadPoolExecutor
        import qloo
        with ThreadPoolExecutor(max_workers=2) as pool:
            loves_f = pool.submit(lambda loc=location: qloo.area_taste(loc, limit=8))
            tag_res = qloo.find_tags(concept, limit=4)
            urn = _first_tag(tag_res)
            rec = qloo.recommend_places(location, signal_tags=[urn] if urn else None, limit=8)
            try:
                loves = loves_f.result()
            except Exception:
                loves = {}
        return 200, {
            "status": "ok", "places": _taste_places(rec), "loves": _taste_places(loves),
            "concept": concept, "location": location, "tag": urn,
        }
    except Exception:
        return 200, empty


def _first_tag(obj):
    found = []

    def walk(o):
        if found:
            return
        if isinstance(o, dict):
            u = o.get("id") or o.get("urn")
            if isinstance(u, str) and u.startswith("urn:tag"):
                found.append(u)
                return
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(obj)
    return found[0] if found else None


GROUPS = tuple(be.GROUP_PROFILE)


@functools.lru_cache(maxsize=8)
def _ask_card(city, group):
    calib = be.calibration(city, be.GROUP_PROFILE[group])
    if calib is None:
        return None
    rel = 1.0
    # asking=12000 only stretches the curve past real Chicago listings; it is not returned.
    card = be.rent_ceiling_card(rel, calib, asking=12000, n=2000, seed=20261011)
    s_free = card["curve"][0][1]
    r80 = card["ceilings"]["80"]["rent_max"]
    return {
        "city": city, "group": group, "label_en": be.GROUP_LABEL[group][0],
        "currency": card["currency"], "city_s3": card["city_s3"], "city_s1": calib["official"]["s1"],
        "n": calib["official"]["n"], "source": calib["official"]["source"],
        "kappa": round(float(calib.get("kappa") or 0.0), 3),
        "location_hr": card["location_hr"],
        "s_free": s_free, "eighty_for_sale": r80 > 0,
        "capex_default": calib["inputs"]["capex"]["mode"],
        "ceilings": card["ceilings"], "curve": card["curve"],
        "caps": card["caps"], "fx": card["fx"], "draws_n": len(card["caps"]),
        "buffer_months": card["buffer_months"],
        "note": ("Location did not predict 3-year survival in held-out Chicago licences "
                 "(κ = 0, HR_loc = 1). The street does not change these odds. A model, not a forecast."),
    }


def ask(city, group):
    """Chicago (and other calibrated cities) survival-vs-rent curve. No map, no Qloo. Cached."""
    if not city or not group:
        return 400, {"error": "city and group are required"}
    slug = slugify(city[:120])
    g = group[:40].strip().lower().replace("-", "_").replace(" ", "_")
    if g not in GROUPS:
        return 400, {"error": f"group must be one of {', '.join(GROUPS)}"}
    card = _ask_card(slug, g)
    if card is None:
        return 404, {"error": f"No survival calibration for {slug}/{g}."}
    return 200, card
