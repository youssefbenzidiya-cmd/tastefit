"""Equal-utility market share for Chicago and Philadelphia, from a cached official aggregate.

Request time reads `data/us_fafh.json` and the city's OpenStreetMap food venues. It does not
call the network and does not write Qloo data. The figure is an estimate with the assumptions
stated on `note`: a lower bound on food-away-from-home spend (one income bin is left out) and
an upper bound before costs. It is not profit and not a forecast.
"""
import functools
import gzip
import json
import math
import re
from pathlib import Path

import geo

RADIUS_KM = 1.2  # same disc as the Chicago backtest area
DATA = Path(__file__).parent / "data" / "us_fafh.json"
FOOD = Path(__file__).parent / "packs" / "food" / "{slug}.json.gz"
OUTSIDE_US = "not available"
NON_US = {"paris", "casablanca"}

# ACS 2024 B19001 bins (E002..E017) to CEX 2024 Table 1203 income-before-taxes bands.
# E012 ($60,000 to $74,999) crosses the CEX $70,000 boundary and is not assigned.
BAND_OF = {
    "E002": "lt_15000",
    "E003": "lt_15000",
    "E004": "15_29999",
    "E005": "15_29999",
    "E006": "15_29999",
    "E007": "30_39999",
    "E008": "30_39999",
    "E009": "40_49999",
    "E010": "40_49999",
    "E011": "50_69999",
    "E012": "unassigned",
    "E013": "70_99999",
    "E014": "100_149999",
    "E015": "100_149999",
    "E016": "150_199999",
    "E017": "200_plus",
}


def _slug(city):
    return re.sub(r"[^a-z0-9]+", "-", str(city or "").strip().lower()).strip("-")


def food_away_from_home(counts, means):
    """(annual dollars or None, unassigned households or None, assigned households).

    `counts` maps B19001 suffixes E002..E017 to household counts. None or a negative
    sentinel is missing and is never treated as zero. A missing assigned bin makes the
    annual total None. The unassigned bin adds nothing to spend.
    """
    annual, assigned = 0, 0
    unassigned = 0
    unassigned_known = True
    for code, band in BAND_OF.items():
        n = counts.get(code)
        if n is None or (isinstance(n, int) and n < 0) or not isinstance(n, int):
            if band == "unassigned":
                unassigned_known = False
            else:
                return None, None, None
            continue
        if band == "unassigned":
            unassigned += n
        else:
            annual += n * means[band]
            assigned += n
    return annual, (unassigned if unassigned_known else None), assigned


def monthly_dollars(annual, venues):
    """Nearest dollar of annual spend split equally across this place and `venues` competitors, then /12."""
    return int(math.floor(annual / (12 * (1 + venues)) + 0.5))


@functools.lru_cache(maxsize=1)
def table():
    with DATA.open(encoding="utf-8") as f:
        return json.load(f)


@functools.lru_cache(maxsize=4)
def _venues(slug):
    path = Path(str(FOOD).format(slug=slug))
    if not path.is_file():
        return None
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return json.load(f)["venues"]


def _box(lat, lon):
    dlat = RADIUS_KM / geo.KM_PER_DEG_LAT + 1e-6
    dlon = RADIUS_KM / (111.32 * max(0.2, math.cos(math.radians(lat)))) + 1e-6
    return lat - dlat, lat + dlat, lon - dlon, lon + dlon


def _within(lat, lon, rows, lat_i, lon_i):
    s, n, w, e = _box(lat, lon)
    kept = []
    for p in rows:
        plat, plon = p[lat_i], p[lon_i]
        if s <= plat <= n and w <= plon <= e and geo.km_between((lat, lon), (plat, plon)) <= RADIUS_KM:
            kept.append(p)
    return kept


def _dollars(n):
    return f"${n:,.0f}"


def _outside(display_name):
    name = (display_name or "").lower()
    if name:
        return "united states" not in name
    return None


def estimate(city, lat, lon, asking_rent=None, display_name="", tracts=None, venue_rows=None, gate_passed=True):
    """A share estimate, or a note. `note` is the sentence the page shows."""
    slug = _slug(city)
    try:
        lat, lon = float(lat), float(lon)
    except (TypeError, ValueError):
        return {"available": False, "note": OUTSIDE_US}
    doc = None
    rows = tracts
    if rows is None and slug not in ("chicago", "philadelphia"):
        return _unavailable(slug, display_name)
    if rows is None:
        try:
            doc = table()
            rows = doc["tracts"][slug]
        except (OSError, KeyError, ValueError):
            return {"available": False, "note": "Spend estimate unavailable: the ACS table did not load."}
    venues = venue_rows if venue_rows is not None else _venues(slug)
    if venues is None:
        return {"available": False, "note": "Spend estimate unavailable: this city has no mapped food-venue file, so the choice set is unknown."}
    caught = _within(lat, lon, rows, 1, 2)
    near = _within(lat, lon, venues, 0, 1)
    if not caught:
        return {"available": False, "city": slug,
                "note": f"No ACS tract centroid within {RADIUS_KM} km, so the spend estimate is not available here."}
    annual = sum(t[5] for t in caught)
    households = sum(t[4] for t in caught)
    unassigned = sum(t[6] for t in caught)
    population = sum(t[3] for t in caught)
    n = len(near)
    monthly = monthly_dollars(annual, n)
    rent = _asking(asking_rent)
    covers = None if rent is None else monthly >= rent
    note = _note(annual, households, unassigned, n, monthly, rent, covers, gate_passed)
    out = {"available": True, "city": slug, "radius_km": RADIUS_KM, "tracts": len(caught),
           "population": population, "households": households, "unassigned_households": unassigned,
           "annual_fafh_usd": annual, "venues": n, "share": 1 / (1 + n), "monthly_spend_usd": monthly,
           "currency": "USD", "note": note}
    if rent is not None:
        out["asking_rent"] = rent
        out["pays_rent"] = covers
    return out


def _asking(v):
    try:
        r = float(v)
    except (TypeError, ValueError):
        return None
    return r if 0 < r < 1e7 else None


def _unavailable(slug, display_name):
    if slug in NON_US or _outside(display_name) is True:
        return {"available": False, "note": OUTSIDE_US}
    if _outside(display_name) is False:
        return {"available": False, "note": "Not estimated for this US city. The spend estimate covers Chicago and Philadelphia only."}
    return {"available": False, "note": OUTSIDE_US}


def _note(annual, households, unassigned, venues, monthly, rent, covers, gate_passed):
    choice = 1 + venues
    venue_bit = (f"{venues} mapped food venues" if venues else "no mapped food venue within the disc, so the share is 1")
    text = (
        f"Estimate, not a forecast: about {_dollars(monthly)} a month of food-away-from-home spend "
        f"from census tracts whose centroids sit within {RADIUS_KM} km "
        f"({_dollars(annual)} a year across {households:,} households, equal-utility share 1/{choice} "
        f"with {venue_bit}). Rough ceiling under these assumptions, before costs, so this is not profit. "
        f"Missing mapped venues would push it up. Households are residents only: workers and visitors are not counted, which pushes it down. "
        f"The ACS $60,000 to $74,999 bin ({unassigned:,} households) is excluded because it crosses the CEX $70,000 line. "
        f"One household stands in for one consumer unit. Monthly figures, no discounting. "
        f"Sources: ACS 2024 5-year tables B19001 and B01003; BLS CEX 2024 Table 1203, Food away from home. "
        f"A Chicago backtest of 2023-2024 openings found no measurable gain over a citywide baseline."
    )
    if not gate_passed:
        text += (" This city's OpenStreetMap food venues failed the map-quality check, so the venue count is unreliable "
                 "and a missing venue would overstate the share.")
    if rent is not None:
        side = "at or below" if covers else "above"
        text += f" The asking rent of {_dollars(rent)} a month is {side} this rough ceiling."
    return text
