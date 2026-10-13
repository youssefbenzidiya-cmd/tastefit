"""Taste Gap Index: "loved here, under-served here". Standard library only.

Demand over-index D (Qloo): the same exemplar places ranked twice, once for the area's audience and once for the
city's, each standardised within its own call, D = z_area - z_city. Scores from separate rank calls are never
compared raw; D only assumes Qloo rescales each call by an increasing affine map.

Supply ratio theta (OpenStreetMap): concept venues near the unit against the count expected if the local food mix
matched the city's, shrunk with a Gamma prior fitted over the city's cells (Clayton & Kaldor 1987), so 0 or 1
venue cannot produce a wild ratio. P_under = P(theta < 1) by seeded Monte Carlo.

The venue file (packs/food/<slug>.json.gz) is built offline from one Overpass query (`python tgi.py build`); it holds
OSM-derived points only, never Qloo data.
"""
import gzip
import json
import math
import os
import random
import re
import statistics
import threading

import cityscan
import cityscan_math

PACKS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "packs")
FOOD_FILE = os.path.join(PACKS, "food", "{slug}.json.gz")
RADIUS_M = 600  # the pack's "reasons" radius
BLOCK = 3  # prior fitted on 3x3 blocks of 400 m cells, about the area of the 600 m disc
DRAWS = 4000
T_DEMAND, P_HIGH, P_LOW = 0.5, 0.8, 0.2
LOW_COVERAGE = 0.8  # prior weight above which the local count barely moves the estimate: shown as low coverage
CLAIM_RADIUS_M = 500  # venue-existence check for named competitors
NAME_MATCH, NAME_MAYBE = 0.6, 0.45  # City Scan's name-similarity threshold (METHOD.md 1b); below it, borderline
BETA = (0.0, 1.0, -1.0)  # (alpha, beta_D, beta_log_theta): stated priors, not fitted ("uncalibrated")

# OSM cuisine values per catalog concept; other concepts fall back to City Scan's concept mapping.
CUISINES = {
    "ramen": {"ramen", "noodle"},
    "korean fried chicken": {"korean", "chicken", "fried_chicken", "wings"},
    "mediterranean": {"mediterranean", "greek"},
    "specialty coffee": {"coffee_shop", "coffee"},
    "salad": {"salad", "healthy", "bowl", "poke"},
    "taqueria": {"mexican", "tacos", "taco", "burrito", "tex-mex"},
    "pho": {"vietnamese", "pho"},
    "burger": {"burger"},
    "lebanese": {"lebanese", "middle_eastern", "shawarma", "falafel", "arab"},
    "bakery": {"bakery", "pastry"},
}
FOOD = {"restaurant": "r", "cafe": "c", "fast_food": "f", "food_court": "f", "ice_cream": "i", "bakery": "b"}


# ---------- demand over-index ----------

def zs(xs):
    m = statistics.fmean(xs)
    sd = statistics.stdev(xs) if len(xs) > 1 else 0.0
    return [(x - m) / sd if sd > 0 else 0.0 for x in xs]


def demand_overindex(local_scores, city_scores):
    """{concept: D}; both dicts {concept: score} from ONE rank call each over the SAME exemplar set."""
    cs = sorted(set(local_scores) & set(city_scores))
    if len(cs) < 2:
        return {}
    zl, zc = zs([local_scores[c] for c in cs]), zs([city_scores[c] for c in cs])
    return {c: zl[i] - zc[i] for i, c in enumerate(cs)}


def rank_scores(rank_res, concept_by_id):
    """{concept: score} from one rank call: the call's own affinity, or minus the position when any is missing."""
    rows = [r for r in ((rank_res or {}).get("results") or []) if r.get("entity_id") in concept_by_id]
    rows.sort(key=lambda r: -(r.get("affinity") or 0))
    raw = all(isinstance(r.get("affinity"), (int, float)) for r in rows)
    out = {}
    for pos, r in enumerate(rows):
        for concept in concept_by_id[r["entity_id"]]:
            out.setdefault(concept, float(r["affinity"]) if raw else -float(pos))
    return out


# ---------- supply ratio ----------

def gamma_prior(n_cells, e_cells):
    """Clayton-Kaldor moment fit of Gamma(a, rate b) for one concept over the city's cells with E > 0."""
    pairs = [(n, e) for n, e in zip(n_cells, e_cells) if e > 0]
    if not pairs:
        return None
    se = sum(e for _, e in pairs)
    m = sum(n for n, _ in pairs) / se
    if m <= 0:
        return None
    v = sum(e * (n / e - m) ** 2 for n, e in pairs) / se - m / statistics.fmean(e for _, e in pairs)
    v = max(v, 1e-6)
    return m * m / v, m / v


def supply_ratio(n, e, a, b, draws=DRAWS, seed=7):
    """(posterior mean of theta, P(theta < 1)) under Gamma(a + n, rate b + e)."""
    rng = random.Random(seed)
    shape, rate = a + n, b + e
    under = sum(rng.gammavariate(shape, 1 / rate) < 1 for _ in range(draws)) / draws
    return shape / rate, under


def label(d, p_under, t=T_DEMAND):
    if d is None or p_under is None:
        return None
    if d >= t and p_under >= P_HIGH:
        return "white space"
    if d >= t and p_under <= P_LOW:
        return "proven demand, crowded"
    if d <= -t and p_under >= P_HIGH:
        return "empty for a reason"
    if d <= -t and p_under <= P_LOW:
        return "avoid"
    return "neutral"


def score(d, theta, beta=BETA):
    """0-100, sigma(alpha + b1 D + b2 log theta); uncalibrated stated priors. Missing parts count as 0."""
    x = beta[0] + beta[1] * (d or 0.0) + (beta[2] * math.log(theta) if theta else 0.0)
    return round(100 / (1 + math.exp(-x)))


# ---------- OSM venues ----------

def cuisines_for(concept, tag_name=None):
    """OSM cuisine values for a concept: the catalog map first, then City Scan's mapping. Empty set if unknown."""
    text = cityscan._norm(f"{concept} {tag_name or ''}")
    for key, vals in CUISINES.items():
        if all(w in text for w in key.split()) or any(re.search(rf"\b{re.escape(v)}\b", text) for v in vals - {"bowl", "coffee"}):
            return vals
    m = cityscan.map_concept(concept)
    return set(m["osm"]["cuisine"]) if m["recognized"] else set()


def matches(venue, cuisines):
    kind, cuisine = venue[2], venue[3]
    toks = {t.strip() for t in re.split(r"[;,]", cuisine or "") if t.strip()}
    if toks & cuisines:
        return True
    return kind == "b" and "bakery" in cuisines or kind == "c" and not toks and "coffee_shop" in cuisines


class Venues:
    """Food venues of one city on the pack's grid, with per-cell totals for the prior."""

    def __init__(self, grid, venues):
        self.grid, self.venues = grid, venues
        self.cells = [cityscan.cell_of(grid, v[0], v[1]) for v in venues]
        self.blocks = [self._block(c) for c in self.cells]
        self.food_by_block = {}
        for b in self.blocks:
            if b is not None:
                self.food_by_block[b] = self.food_by_block.get(b, 0) + 1

    def _block(self, cell):
        if cell is None:
            return None
        r, c = divmod(cell, self.grid["cols"])
        return (r // BLOCK, c // BLOCK)

    def supply(self, cuisines, lat, lon, radius=RADIUS_M, draws=DRAWS):
        """{n, food, expected, theta, p_under, raw} for the disc around (lat, lon); None if the concept is unmapped."""
        if not cuisines:
            return None
        hit = [matches(v, cuisines) for v in self.venues]
        n_city, f_city = sum(hit), len(self.venues)
        if not n_city or not f_city:
            return None
        share = n_city / f_city
        conc = {}
        for b, h in zip(self.blocks, hit):
            if b is not None and h:
                conc[b] = conc.get(b, 0) + 1
        keys = list(self.food_by_block)
        prior = gamma_prior([conc.get(k, 0) for k in keys], [self.food_by_block[k] * share for k in keys])
        if prior is None:
            return None
        kx = 111_320 * math.cos(math.radians(lat))
        n = food = 0
        for v, h in zip(self.venues, hit):
            if math.hypot((v[0] - lat) * 111_320, (v[1] - lon) * kx) <= radius:
                food += 1
                n += h
        e = food * share
        theta, p_under = supply_ratio(n, e, *prior, draws=draws)
        w = prior[1] / (prior[1] + e)  # weight of the city prior in the posterior mean
        return {"n": n, "food": food, "expected": round(e, 2), "theta": round(theta, 3), "p_under": round(p_under, 3),
                "raw": round(n / e, 2) if e else None, "prior_weight": round(w, 3),
                "coverage": "low" if w >= LOW_COVERAGE else "ok"}

    def find(self, name, lat, lon, radius=CLAIM_RADIUS_M):
        """(True | None | False, matched OSM name): a venue with a similar name (City Scan's matcher) within `radius`
        m; None when the best match is only borderline."""
        key = cityscan._name_key(name)
        if not key:
            return False, None
        kx = 111_320 * math.cos(math.radians(lat))
        best, best_name = 0.0, None
        for v in self.venues:
            if len(v) > 4 and v[4] and math.hypot((v[0] - lat) * 111_320, (v[1] - lon) * kx) <= radius:
                s = cityscan_math.name_similarity(key, cityscan._name_key(v[4]))
                if s > best:
                    best, best_name = s, v[4]
        if best >= NAME_MATCH:
            return True, best_name
        return (None, best_name) if best >= NAME_MAYBE else (False, None)


_cache, _lock = {}, threading.Lock()


def load(slug):
    """Venues for a city slug, or None when no venue file ships for it. Cached per process (OSM data only)."""
    slug = (slug or "").strip().lower()
    with _lock:
        if slug not in _cache:
            path = FOOD_FILE.format(slug=re.sub(r"[^a-z0-9_-]", "", slug))
            try:
                with gzip.open(path, "rt") as f:
                    d = json.load(f)
                _cache[slug] = Venues(d["grid"], d["venues"])
            except OSError:
                _cache[slug] = None
        return _cache[slug]


# ---------- offline build ----------

def overpass_food_query(bbox, date=None):
    s, w, n, e = (round(v, 5) for v in bbox)
    when = f'[date:"{date}T00:00:00Z"]' if date else ""
    return (f"[out:json][timeout:300][maxsize:268435456]{when}[bbox:{s},{w},{n},{e}];"
            '(nwr["amenity"~"^(restaurant|cafe|fast_food|food_court|ice_cream)$"];nwr["shop"~"^(bakery|pastry)$"];);'
            "out tags center qt;")


def compact(elements):
    """[[lat, lon, kind, cuisine, name]] from Overpass elements; bars and pubs are not queried."""
    out = []
    for el in elements:
        t = el.get("tags") or {}
        c = el if "lat" in el else el.get("center") or {}
        kind = FOOD.get(t.get("amenity")) or ("b" if t.get("shop") in ("bakery", "pastry") else None)
        if kind and c.get("lat") is not None:
            out.append([round(c["lat"], 6), round(c["lon"], 6), kind, cityscan._norm(t.get("cuisine", ""))[:60],
                        (t.get("name") or t.get("brand") or "")[:60]])
    return out


def build(slug, date=None, out=None):
    import urllib.parse
    import urllib.request
    with gzip.open(os.path.join(PACKS, f"{slug}.json.gz"), "rt") as f:
        grid = json.load(f)["grid"]
    body = urllib.parse.urlencode({"data": overpass_food_query(grid["bbox"], date)}).encode()
    last = None
    for url in cityscan.OVERPASS_URLS:
        try:
            req = urllib.request.Request(url, data=body, headers={"User-Agent": "TasteFit/1.0 (offline TGI build)"})
            data = json.load(urllib.request.urlopen(req, timeout=400))
            break
        except Exception as e:  # noqa: BLE001  next mirror
            last = e
    else:
        raise RuntimeError(f"Overpass failed: {type(last).__name__}")
    pack = {"slug": slug, "as_of": date or "current", "source": "OpenStreetMap (ODbL 1.0)", "grid": grid,
            "venues": compact(data.get("elements", []))}
    path = out or FOOD_FILE.format(slug=slug)
    with gzip.open(path, "wt") as f:
        json.dump(pack, f, separators=(",", ":"))
    return path, len(pack["venues"])


if __name__ == "__main__":
    import sys
    if len(sys.argv) >= 3 and sys.argv[1] == "build":
        print(build(sys.argv[2], *(sys.argv[3:4] or [None]), *(sys.argv[4:5] or [None])))
    else:
        sys.exit("usage: python tgi.py build <slug> [YYYY-MM-DD] [out.json.gz]")
