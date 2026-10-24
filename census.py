"""US Census Bureau geocoder + ACS 5-year aggregates. Standard library only.

Public tract statistics, never personal data. Nothing is written to disk; the live fetch is an
in-memory LRU. A key is optional (`CENSUS_API_KEY`) and comes from the environment only.
"""
import functools
import json
import urllib.parse
import urllib.request

# Census.gov ACS requires CENSUS_API_KEY as of 2026; ACS estimates are read through Census Reporter.

USER_AGENT = "TasteFit/1.0 (https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43239248/tastefit)"
GEOCODER = "https://geocoding.geo.census.gov/geocoder/geographies/coordinates"
REPORTER = "https://api.censusreporter.org/1.0/data/show/latest"
TIMEOUT = 4
NA = -666666666


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.loads(r.read().decode())


@functools.lru_cache(maxsize=512)
def fetch_live(url):
    """In-memory only. Same tract or ACS URL is free after the first pin."""
    return fetch(url)


def _num(v):
    try:
        n = float(v)
    except (TypeError, ValueError):
        return None
    if n <= NA + 1:
        return None
    return n


def tract_at(lat, lon, fetch=fetch):
    q = urllib.parse.urlencode({
        "x": f"{float(lon):.4f}", "y": f"{float(lat):.4f}",
        "benchmark": "Public_AR_Current", "vintage": "Current_Current", "format": "json",
    })
    data = fetch(GEOCODER + "?" + q)
    tracts = (((data or {}).get("result") or {}).get("geographies") or {}).get("Census Tracts") or []
    if not tracts:
        return None
    t = tracts[0]
    return {
        "geoid": str(t.get("GEOID") or ""),
        "name": t.get("NAME") or "",
        "state": str(t.get("STATE") or "").zfill(2),
        "county": str(t.get("COUNTY") or "").zfill(3),
        "tract": str(t.get("TRACT") or "").zfill(6),
    }


def _est(block, code):
    return _num(((block or {}).get("estimate") or {}).get(code))


def acs5(state, county, tract, fetch=fetch):
    """ACS 5-year via Census Reporter (census.gov data API now requires a key; numbers are still ACS)."""
    geoid = f"{state}{county}{tract}"
    url = REPORTER + "?" + urllib.parse.urlencode({
        "table_ids": "B19013,B25064,B01003,B01002,B17001,B25003",
        "geo_ids": f"14000US{geoid}",
    })
    payload = fetch(url)
    data = ((payload or {}).get("data") or {}).get(f"14000US{geoid}") or {}
    income = _est(data.get("B19013"), "B19013001")
    rent = _est(data.get("B25064"), "B25064001")
    pop = _est(data.get("B01003"), "B01003001")
    age = _est(data.get("B01002"), "B01002001")
    univ = _est(data.get("B17001"), "B17001001")
    poor = _est(data.get("B17001"), "B17001002")
    owners = _est(data.get("B25003"), "B25003002")
    renters = _est(data.get("B25003"), "B25003003")
    vintage = ((payload or {}).get("release") or {}).get("name") or "ACS 5-year"
    return {
        "median_income": int(income) if income is not None else None,
        "median_gross_rent": int(rent) if rent is not None else None,
        "population": int(pop) if pop is not None else None,
        "median_age": round(age, 1) if age is not None else None,
        "poverty_rate": round(poor / univ, 4) if univ and poor is not None else None,
        "owner_hh": int(owners) if owners is not None else None,
        "renter_hh": int(renters) if renters is not None else None,
        "vintage": vintage,
    }


def card(lat, lon, fetch=None):
    """Tract identity plus ACS 5-year levels for a map point, or None if the geocoder is empty.

    ACS failures still return the tract: licences and the pin do not wait on a second HTTP call.
    Live calls use `fetch_live` (LRU). Tests inject `fetch`.
    """
    get = fetch if fetch is not None else fetch_live
    t = tract_at(lat, lon, fetch=get)
    if not t:
        return None
    try:
        a = acs5(t["state"], t["county"], t["tract"], fetch=get)
    except Exception:
        a = {}
    vintage = a.pop("vintage", "ACS 5-year") if isinstance(a, dict) else "ACS 5-year"
    return {
        "geoid": t["geoid"], "name": t["name"], "state": t["state"], "county": t["county"],
        "tract": t["tract"], "vintage": vintage, **a,
    }
