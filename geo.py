"""Site math: weighted barycenters of demand and competition, and a gravity-style search for the best opening point.

Coordinates come from Qloo heatmap points directly; places only carry an address, which is geocoded with
OpenStreetMap Nominatim (free, keyless; their policy allows at most one request per second).
"""
import json
import math
import threading
import time
import urllib.parse
import urllib.request

KM_PER_DEG_LAT = 110.57
USER_AGENT = "TasteFit/1.0 (https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43239248/tastefit)"
MIN_INTERVAL = 1.1  # seconds between Nominatim requests, process-wide (policy: at most 1 per second)
_rate_lock = threading.Lock()
_last_call = [-MIN_INTERVAL]
_clock = time.monotonic
_sleep = time.sleep


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _coords(d):
    for holder in (d, d.get("location"), (d.get("properties") or {}).get("geocode") if isinstance(d.get("properties"), dict) else None):
        if isinstance(holder, dict):
            lat = _num(holder.get("latitude", holder.get("lat")))
            lon = _num(holder.get("longitude", holder.get("lon", holder.get("lng"))))
            if lat is not None and lon is not None and (lat, lon) != (0, 0):
                return lat, lon
    return None


def _weight(d):
    q = d.get("query") if isinstance(d.get("query"), dict) else {}
    w = _num(d.get("affinity", q.get("affinity")))
    return w if w is not None and w > 0 else 1.0


def extract(result):
    """Points {name, lat, lon, w, address} from a harness result: named places and unnamed heatmap cells."""
    out = []

    def walk(o):
        if isinstance(o, dict):
            c = _coords(o)
            props = o.get("properties") if isinstance(o.get("properties"), dict) else {}
            addr = props.get("address") if isinstance(props.get("address"), str) else None
            if o.get("name") or c:
                if c or addr:
                    out.append({"name": o.get("name"), "lat": c[0] if c else None, "lon": c[1] if c else None,
                                "w": _weight(o), "address": addr})
                    return
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(result)
    return out


def _throttle():
    """Blocks until a Nominatim request may start; shared by every thread of the process."""
    with _rate_lock:
        wait = MIN_INTERVAL - (_clock() - _last_call[0])
        if wait > 0:
            _sleep(wait)
        _last_call[0] = _clock()


def geocode(query, cache=None):
    """(lat, lon) for a free-text address, or None. `cache` is a per-run dict: addresses come from Qloo results,
    so nothing is kept across requests."""
    if cache is not None and query in cache:
        return cache[query]
    _throttle()
    url = "https://nominatim.openstreetmap.org/search?" + urllib.parse.urlencode({"q": query, "format": "json", "limit": 1})
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            hits = json.loads(r.read())
        found = (float(hits[0]["lat"]), float(hits[0]["lon"])) if hits else None
    except Exception:  # noqa: BLE001  a missing point only shrinks the sample
        found = None
    if cache is not None:
        cache[query] = found
    return found


AREA_KEYS = ("neighbourhood", "quarter", "suburb", "city_district")


def reverse(lat, lon):
    """{"areas": [names, finest first], "city", "postcode"} for a map click, or None. One throttled Nominatim call."""
    _throttle()
    url = "https://nominatim.openstreetmap.org/reverse?" + urllib.parse.urlencode(
        {"lat": f"{lat:.6f}", "lon": f"{lon:.6f}", "format": "json", "zoom": 16, "addressdetails": 1,
         "accept-language": "en"})
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            addr = json.loads(r.read()).get("address") or {}
    except Exception:  # noqa: BLE001  the caller asks for a typed neighborhood instead
        return None
    city = addr.get("city") or addr.get("town") or addr.get("village") or addr.get("municipality")
    if not city:
        return None
    areas = list(dict.fromkeys(addr[k] for k in AREA_KEYS if addr.get(k) and addr[k] != city))
    return {"areas": areas, "city": city, "postcode": addr.get("postcode")}


def km_between(a, b):
    """Equirectangular distance in km between two (lat, lon) points; accurate at neighborhood scale."""
    kx = 111.32 * math.cos(math.radians((a[0] + b[0]) / 2))
    return math.hypot((a[1] - b[1]) * kx, (a[0] - b[0]) * KM_PER_DEG_LAT)


def locate(points, area, limit=10, cache=None):
    """Fill missing coordinates by geocoding addresses; drops points that cannot be placed."""
    cache = {} if cache is None else cache
    placed = []
    for p in points:
        if p["lat"] is None:
            if limit <= 0 or not p.get("address"):
                continue
            limit -= 1
            c = geocode(p["address"] if area.lower() in p["address"].lower() else f"{p['address']}, {area}", cache)
            if not c:
                continue
            p = {**p, "lat": c[0], "lon": c[1]}
        placed.append(p)
    return placed


def _proj(lat0, lon0):
    kx = 111.32 * math.cos(math.radians(lat0))
    to_xy = lambda lat, lon: ((lon - lon0) * kx, (lat - lat0) * KM_PER_DEG_LAT)  # noqa: E731
    to_ll = lambda x, y: (lat0 + y / KM_PER_DEG_LAT, lon0 + x / kx)  # noqa: E731
    return to_xy, to_ll


def barycenter(points):
    """Affinity-weighted center of mass: sum(w_i * p_i) / sum(w_i)."""
    sw = sum(p["w"] for p in points)
    return (sum(p["w"] * p["lat"] for p in points) / sw, sum(p["w"] * p["lon"] for p in points) / sw)


def analyze(demand, competition, competition_penalty=0.7, grid=41):
    """Barycenters, weighted standard distance, and the grid point maximizing demand pull minus competition pull.

    Pull at x = sum_i w_i * exp(-d(x, p_i)^2 / (2 sigma^2)), sigma derived from the demand spread.
    """
    if len(demand) < 2:
        return None
    every = demand + competition
    lat0, lon0 = barycenter(every)
    to_xy, to_ll = _proj(lat0, lon0)
    dc = barycenter(demand)
    dcx, dcy = to_xy(*dc)
    sw = sum(p["w"] for p in demand)
    spread = math.sqrt(sum(p["w"] * ((to_xy(p["lat"], p["lon"])[0] - dcx) ** 2 + (to_xy(p["lat"], p["lon"])[1] - dcy) ** 2)
                           for p in demand) / sw)
    sigma = min(max(spread / 2, 0.3), 3.0)

    D = [(*to_xy(p["lat"], p["lon"]), p["w"]) for p in demand]
    C = [(*to_xy(p["lat"], p["lon"]), p["w"]) for p in competition]
    xs = [x for x, _, _ in D + C]
    ys = [y for _, y, _ in D + C]
    pad = sigma
    x_lo, x_hi, y_lo, y_hi = min(xs) - pad, max(xs) + pad, min(ys) - pad, max(ys) + pad

    def pull(x, y, pts):
        return sum(w * math.exp(-((x - px) ** 2 + (y - py) ** 2) / (2 * sigma ** 2)) for px, py, w in pts)

    best, best_xy, peak_demand = -math.inf, (dcx, dcy), 0.0
    for i in range(grid):
        for j in range(grid):
            x = x_lo + (x_hi - x_lo) * i / (grid - 1)
            y = y_lo + (y_hi - y_lo) * j / (grid - 1)
            d = pull(x, y, D)
            peak_demand = max(peak_demand, d)
            s = d - competition_penalty * pull(x, y, C)
            if s > best:
                best, best_xy = s, (x, y)

    rec = to_ll(*best_xy)
    dist = lambda a, b: math.hypot(a[0] - b[0], a[1] - b[1])  # noqa: E731
    out = {
        "demand_center": {"lat": round(dc[0], 6), "lon": round(dc[1], 6)},
        "catchment_km": round(spread, 2),
        "recommended": {"lat": round(rec[0], 6), "lon": round(rec[1], 6),
                        "opportunity_index": round(100 * max(best, 0) / peak_demand) if peak_demand else 0,
                        "km_from_demand_center": round(dist(best_xy, (dcx, dcy)), 2),
                        "nearest_competitor_km": round(min((dist(best_xy, c[:2]) for c in C), default=0), 2) if C else None},
        "demand_points": len(demand),
        "competitor_points": len(competition),
        "method": "affinity-weighted barycenter; opportunity = demand pull - 0.7 x competition pull, Gaussian kernel "
                  f"sigma {sigma:.2f} km, {grid}x{grid} grid",
    }
    if competition:
        cc = barycenter(competition)
        out["competition_center"] = {"lat": round(cc[0], 6), "lon": round(cc[1], 6)}
        out["competition_center_km_from_demand"] = round(dist(to_xy(*cc), (dcx, dcy)), 2)
    return out
