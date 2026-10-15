"""Build Chicago community-area footfall aggregates from the City of Chicago Data Portal.

Writes data/chicago_footfall.json. Counts only: no trip rows, no rider identifiers, and no Divvy
gender or birth year. Every window ends before 2023-01-01, the first day of the Chicago backtest.

    python3 build_chicago_footfall.py

The portal terms are the City of Chicago data disclaimer. This script is a few aggregated SoQL
queries, spaced a second apart.
"""
import datetime
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

UA = "TasteFit/footfall (community-area counts; https://data.cityofchicago.org/)"
ROOT = Path(__file__).parent
OUT = ROOT / "data" / "chicago_footfall.json"
BACKTEST_END = "2023-01-01"
AREAS = [
    ("Wicker Park", 41.9088, -87.6796), ("Logan Square", 41.9217, -87.7005), ("Pilsen", 41.8564, -87.6560),
    ("Lincoln Park", 41.9214, -87.6513), ("West Loop", 41.8826, -87.6476), ("Hyde Park", 41.7943, -87.5907),
    ("Andersonville", 41.9800, -87.6685), ("Lakeview", 41.9400, -87.6540), ("Bridgeport", 41.8380, -87.6510),
    ("Uptown", 41.9666, -87.6533), ("Chinatown", 41.8517, -87.6338), ("River North", 41.8924, -87.6341),
]


def get(url, timeout=300, retries=3):
    last = None
    for attempt in range(retries):
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as res:
                return json.load(res)
        except (TimeoutError, urllib.error.URLError) as exc:
            last = exc
            time.sleep(5)
    raise last


def soda(dataset, params, timeout=300, retries=3):
    url = f"https://data.cityofchicago.org/resource/{dataset}.json?{urllib.parse.urlencode(params)}"
    return get(url, timeout=timeout, retries=retries)


def _inside_ring(lon, lat, ring):
    inside = False
    j = len(ring) - 1
    for i, (xi, yi) in enumerate(ring):
        xj, yj = ring[j]
        if (yi > lat) != (yj > lat) and lon < (xj - xi) * (lat - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def _inside_polygon(lon, lat, polygon):
    if not polygon:
        return False
    if not _inside_ring(lon, lat, polygon[0]):
        return False
    return not any(_inside_ring(lon, lat, hole) for hole in polygon[1:])


def _polygons(geometry):
    kind, coords = geometry["type"], geometry["coordinates"]
    if kind == "Polygon":
        return [coords]
    if kind == "MultiPolygon":
        return coords
    return []


def load_areas():
    """Community-area polygons from Boundaries - Community Areas (igwz-8jzy). Not a trip file."""
    url = "https://data.cityofchicago.org/resource/igwz-8jzy.geojson?$limit=80"
    fc = get(url, timeout=120)
    areas = []
    for feat in fc["features"]:
        props = {k.lstrip(":"): v for k, v in feat["properties"].items()}
        number = str(props.get("area_num_1") or props.get("area_numbe") or "").split(".")[0].lstrip("0") or "0"
        areas.append({"number": number, "name": props.get("community") or "", "polygons": _polygons(feat["geometry"])})
    if len(areas) < 77:
        raise SystemExit(f"expected 77 community areas, got {len(areas)}")
    return areas


def locate(areas, lon, lat):
    for area in areas:
        if any(_inside_polygon(lon, lat, poly) for poly in area["polygons"]):
            return area
    return None


def _area_key(value):
    text = str(value).strip()
    if "." in text:
        text = text.split(".", 1)[0]
    return text.lstrip("0") or "0"


def _counts(rows, key="area"):
    out = {}
    for row in rows:
        name = row.get(key)
        if name is None or name == "":
            continue
        key_ = _area_key(name)
        out[key_] = out.get(key_, 0) + int(float(row["n"]))
    return out


# Month by month. A quarter or a full year on this table times out. Every slice ends before the backtest.
MONTHS = tuple((f"2022-{m:02d}-01", "2023-01-01" if m == 12 else f"2022-{m + 1:02d}-01") for m in range(1, 13))


def _midpoint(start, end):
    a, b = datetime.date.fromisoformat(start), datetime.date.fromisoformat(end)
    return (a + (b - a) / 2).isoformat()


def _tnp_query(start, end, where_extra):
    where = (f"trip_end_timestamp >= '{start}T00:00:00' AND trip_end_timestamp < '{end}T00:00:00' "
             "AND dropoff_community_area IS NOT NULL AND " + where_extra)
    rows = soda("m6dm-c72p", {
        "$select": "dropoff_community_area as area, count(1) as n",
        "$where": where,
        "$group": "dropoff_community_area",
        "$limit": 100,
    }, timeout=150, retries=1)
    return _counts(rows)


def _tnp_slice(slug, start, end, where_extra, depth=0):
    """One slice, then two halves if the portal times out. Counts only."""
    cache = Path("/tmp/tnp-cache")
    cache.mkdir(exist_ok=True)
    path = cache / f"{slug}-{start}_{end}.json"
    legacy = cache / f"{slug}-{start}.json"
    if path.is_file():
        return {k: int(v) for k, v in json.loads(path.read_text()).items()}
    if (start, end) in MONTHS and legacy.is_file():
        return {k: int(v) for k, v in json.loads(legacy.read_text()).items()}
    try:
        part = _tnp_query(start, end, where_extra)
    except (TimeoutError, urllib.error.URLError):
        if depth >= 2:
            raise
        mid = _midpoint(start, end)
        print(f"tnp {slug} {start} split at {mid}", flush=True)
        left = _tnp_slice(slug, start, mid, where_extra, depth + 1)
        time.sleep(1)
        right = _tnp_slice(slug, mid, end, where_extra, depth + 1)
        part = dict(left)
        for key, count in right.items():
            part[key] = part.get(key, 0) + count
    path.write_text(json.dumps(part))
    return part


def tnp(where_extra):
    """Evening drop-offs, month by month. The hour filter is small enough for one month."""
    totals = {}
    slug = "evening"
    for start, end in MONTHS:
        part = _tnp_slice(slug, start, end, where_extra)
        print(f"tnp {slug} {start} {sum(part.values())}", flush=True)
        for key, count in part.items():
            totals[key] = totals.get(key, 0) + count
        time.sleep(1)
    return totals


def _load_counts(path):
    return {k: int(v) for k, v in json.loads(path.read_text()).items()}


def _complete_month(slug, start, end):
    cache = Path("/tmp/tnp-cache")
    spanned = cache / f"{slug}-{start}_{end}.json"
    legacy = cache / f"{slug}-{start}.json"
    if spanned.is_file():
        return _load_counts(spanned)
    if legacy.is_file():
        return _load_counts(legacy)
    return None


def _day_counts(day):
    """One calendar day. The timestamp bounds are the weekend filter, so date_extract_dow is not used."""
    cache = Path("/tmp/tnp-cache")
    cache.mkdir(exist_ok=True)
    path = cache / f"day-{day}.json"
    if path.is_file():
        return _load_counts(path)
    end = (datetime.date.fromisoformat(day) + datetime.timedelta(days=1)).isoformat()
    where = (f"trip_end_timestamp >= '{day}T00:00:00' AND trip_end_timestamp < '{end}T00:00:00' "
             "AND dropoff_community_area IS NOT NULL")
    rows = soda("m6dm-c72p", {
        "$select": "dropoff_community_area as area, count(1) as n",
        "$where": where,
        "$group": "dropoff_community_area",
        "$limit": 100,
    }, timeout=120, retries=2)
    part = _counts(rows)
    path.write_text(json.dumps(part))
    print(f"tnp day {day} {sum(part.values())}", flush=True)
    return part


def weekend_days(py_weekday, slug):
    """Saturday (weekday 5) or Sunday (weekday 6) in 2022. Complete months are reused."""
    totals, covered = {}, set()
    for start, end in MONTHS:
        part = _complete_month(slug, start, end)
        if part is None:
            continue
        print(f"tnp {slug} {start} {sum(part.values())} cached-month", flush=True)
        for key, count in part.items():
            totals[key] = totals.get(key, 0) + count
        day = datetime.date.fromisoformat(start)
        stop = datetime.date.fromisoformat(end)
        while day < stop:
            if day.weekday() == py_weekday:
                covered.add(day.isoformat())
            day += datetime.timedelta(days=1)
    missing = []
    day = datetime.date(2022, 1, 1)
    while day.year == 2022:
        if day.weekday() == py_weekday and day.isoformat() not in covered:
            missing.append(day.isoformat())
        day += datetime.timedelta(days=1)
    print(f"tnp {slug} days left {len(missing)}", flush=True)
    gate = threading.Lock()

    def fetch(one):
        with gate:
            time.sleep(1)
        return _day_counts(one)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(fetch, one) for one in missing]
        for fut in as_completed(futures):
            part = fut.result()
            for key, count in part.items():
                totals[key] = totals.get(key, 0) + count
    return totals


def _divvy_rows(start, end, depth=0):
    cache = Path("/tmp/divvy-cache")
    cache.mkdir(exist_ok=True)
    path = cache / f"{start}_{end}.json"
    if path.is_file():
        return json.loads(path.read_text())
    try:
        rows = soda("fg6s-gzvg", {
            "$select": "to_latitude as lat, to_longitude as lon, count(1) as n",
            "$where": f"stop_time >= '{start}T00:00:00' AND stop_time < '{end}T00:00:00'",
            "$group": "to_latitude, to_longitude",
            "$limit": 5000,
        }, timeout=180, retries=1)
    except (TimeoutError, urllib.error.URLError):
        if depth >= 2:
            raise
        mid = _midpoint(start, end)
        print(f"divvy {start} split at {mid}", flush=True)
        left = _divvy_rows(start, mid, depth + 1)
        time.sleep(1)
        right = _divvy_rows(mid, end, depth + 1)
        rows = left + right
    path.write_text(json.dumps(rows))
    return rows


def divvy_by_area(areas):
    totals, missed, points = {}, 0, set()
    for month in range(1, 13):
        start = f"2019-{month:02d}-01"
        end = "2020-01-01" if month == 12 else f"2019-{month + 1:02d}-01"
        rows = _divvy_rows(start, end)
        print(f"divvy {start} {sum(int(float(r['n'])) for r in rows)}", flush=True)
        for row in rows:
            lat, lon = float(row["lat"]), float(row["lon"])
            points.add((round(lat, 5), round(lon, 5)))
            area = locate(areas, lon, lat)
            n = int(float(row["n"]))
            if area is None:
                missed += n
                continue
            totals[area["number"]] = totals.get(area["number"], 0) + n
        time.sleep(1)
    return totals, missed, len(points)


def cta_by_area(areas):
    rides = soda("5neh-572f", {
        "$select": "station_id, stationname, sum(rides) as n",
        "$where": "date >= '2022-01-01T00:00:00' AND date < '2023-01-01T00:00:00'",
        "$group": "station_id, stationname",
        "$limit": 500,
    })
    time.sleep(1)
    stops = soda("8pix-ypme", {"$select": "map_id, station_name, location", "$limit": 500})
    by_id = {}
    for stop in stops:
        loc = stop.get("location") or {}
        if loc.get("latitude") is None:
            continue
        by_id.setdefault(str(stop.get("map_id")), (float(loc["latitude"]), float(loc["longitude"])))
    totals, missed = {}, 0
    for row in rides:
        point = by_id.get(str(row.get("station_id")))
        if point is None:
            missed += int(float(row["n"]))
            continue
        area = locate(areas, point[1], point[0])
        if area is None:
            missed += int(float(row["n"]))
            continue
        totals[area["number"]] = totals.get(area["number"], 0) + int(float(row["n"]))
    return totals, missed


def main():
    areas = load_areas()
    print("areas", len(areas), flush=True)
    time.sleep(1)
    evening = tnp("date_extract_hh(trip_end_timestamp) between 17 and 21")
    print("evening", len(evening), sum(evening.values()), flush=True)
    time.sleep(1)
    saturday = weekend_days(5, "saturday")
    time.sleep(1)
    sunday = weekend_days(6, "sunday")
    weekend = {k: saturday.get(k, 0) + sunday.get(k, 0) for k in set(saturday) | set(sunday)}
    print("weekend", len(weekend), flush=True)
    time.sleep(1)
    divvy, divvy_missed, divvy_points = divvy_by_area(areas)
    print("divvy", len(divvy), "missed", divvy_missed, "points", divvy_points, flush=True)
    time.sleep(1)
    cta, cta_missed = cta_by_area(areas)
    print("cta", len(cta), "missed", cta_missed, flush=True)
    community = []
    for area in areas:
        community.append({
            "number": area["number"],
            "name": area["name"],
            "evening_dropoffs": evening.get(area["number"], 0),
            "weekend_dropoffs": weekend.get(area["number"], 0),
            "divvy_ends": divvy.get(area["number"], 0),
            "cta_boardings": cta.get(area["number"], 0),
        })
    placed = []
    for name, lat, lon in AREAS:
        area = locate(areas, lon, lat)
        if area is None:
            raise SystemExit(f"{name} is outside every community area")
        placed.append({"area": name, "lat": lat, "lon": lon, "community_area_number": area["number"],
                       "community_area_name": area["name"]})
    doc = {
        "source": "City of Chicago Data Portal",
        "terms": "https://www.chicago.gov/city/en/narr/foia/data_disclaimer.html",
        "portal": "https://data.cityofchicago.org/",
        "personal_data": "Counts only. No trip rows, no rider identifiers, no Divvy gender or birth year.",
        "leakage": "Every series ends before 2023-01-01, the first day of the Chicago backtest window.",
        "datasets": [
            {"id": "m6dm-c72p", "name": "Transportation Network Providers - Trips (2018 - 2022)",
             "url": "https://data.cityofchicago.org/d/m6dm-c72p",
             "measure": "Drop-offs grouped by dropoff_community_area.",
             "evening": "Trip end hour 17 through 21, date_extract_hh.",
             "weekend": "Each Saturday and Sunday in 2022, by trip end timestamp. A Saturday evening trip is also in the evening series.",
             "start": "2022-01-01", "end": "2022-12-31"},
            {"id": "fg6s-gzvg", "name": "Divvy Trips",
             "url": "https://data.cityofchicago.org/d/fg6s-gzvg",
             "measure": "Trip ends grouped by station coordinate, then summed into the community area that contains the station.",
             "start": "2019-01-01", "end": "2019-12-31",
             "note": "This portal file ends 2020-01-21. 2019 is its last full year and is before the backtest window.",
             "stations_outside_chicago": divvy_missed, "station_points": divvy_points},
            {"id": "5neh-572f", "name": "CTA - Ridership - 'L' Station Entries - Daily Totals",
             "url": "https://data.cityofchicago.org/d/5neh-572f",
             "measure": "Sum of station entries (rides). Stations located with dataset 8pix-ypme and placed in a community area.",
             "stations_dataset": "8pix-ypme",
             "start": "2022-01-01", "end": "2022-12-31",
             "stations_unplaced": cta_missed},
        ],
        "boundaries": {"id": "igwz-8jzy", "name": "Boundaries - Community Areas",
                       "url": "https://data.cityofchicago.org/d/igwz-8jzy"},
        "community_areas": community,
        "backtest_areas": placed,
    }
    for ds in doc["datasets"]:
        if ds["end"] >= BACKTEST_END:
            raise SystemExit(f"{ds['id']} ends on or after the backtest window")
    OUT.write_text(json.dumps(doc, indent=1) + "\n")
    print("wrote", OUT, flush=True)


if __name__ == "__main__":
    main()
