"""City Scan: where in a city should a restaurant concept open? Free OpenStreetMap data, standard library only.

Pipeline per city and concept (formulas and citations in METHOD.md; maths in cityscan_math.py):
  1. Empirical-Bayes counts: per-cell Poisson counts of each footfall generator with a Gamma prior fitted on the city.
  2. Demand surface D_i = sum_g w(g) K(d), K(d) = exp(-d / 400 m), on a grid of <= 2,500 cells.
  3. Suitability: presence-background logistic regression (existing venues of the concept vs city cells) on
     standardised log kernel densities, validated by spatial block cross-validation (AUC with bootstrap CI).
  4. Captured demand: Huff gravity model against the existing same-concept venues (cannibalisation).
  5. Agglomeration: local Ripley L-function excess at 300 m, reported separately and added for destination concepts.
  6. Opportunity = suitability x captured demand (+ agglomeration); 100 Monte Carlo draws from the count posteriors;
     cells ranked by the lower 5% bound.
  7. Diagnostics: Moran's I of model residuals, Getis-Ord Gi* hot spots of opportunity.

Network (Nominatim, one bounded Overpass query per city, in-memory TTL cache) is separate from the pure model, which
also runs on an offline "city pack" (filtered OSM elements). Data (c) OpenStreetMap contributors, ODbL.
"""
import json
import math
import os
import random
import re
import sys
import threading
import time
import unicodedata
import urllib.parse
import urllib.request
from datetime import datetime, timezone

import cityscan_math as cm

try:
    import geo
    _last_call = getattr(geo, "_last_call", [0.0])  # one Nominatim throttle for the whole process
except ImportError:  # pragma: no cover
    _last_call = [0.0]

USER_AGENT = "TasteFit-CityScan/0.2 (https://github.com/youssefbenzidiya-cmd/tastefit)"
ATTRIBUTION = "\u00a9 OpenStreetMap contributors"
OVERPASS_URLS = [u for u in os.environ.get("OVERPASS_URL", "").split(",") if u] or [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
]
CACHE_TTL_S = 6 * 3600
M_PER_DEG_LAT = 110_570.0

PARAMS = {
    "cell_m": 400, "max_cells": 2500, "max_km": 20,
    "kernel_lambda_m": 400.0, "kernel_cutoff_m": 1200.0,
    "huff_alpha": 1.0, "huff_beta": 2.0, "huff_floor_m": 50.0, "huff_radius_m": 2000.0,
    "ripley_r_m": 300.0, "l2": 1.0, "min_presences": 15, "max_rows": 2000,
    "cv_folds": 5, "bootstrap": 200, "draws": 100, "pool": 150, "seed": 20261011, "explain_r_m": 600.0,
    "activity_min": 1.0,
    "rank_q": 0.2,            # quantile used when opts["rank"] == "q" (not the default: ABLATION.md)
    "min_narrow": 30,         # minimum venues matching a narrow concept's cues (e.g. specialty coffee)
    "min_ranked": 30,         # fewer presences: "insufficient data" in unit -> concepts
    "generic_attract": 0.5,   # Huff attractiveness of a broad competitor relative to a narrow-concept venue
    "eb": False,              # shrunk counts as features (ABLATION.md: no gain); the Gamma posterior still sets intervals
}
# Default model switches; every one is ablated in cityscan_ablation.py and the defaults follow ABLATION.md:
# captured demand (Huff), agglomeration, kernel smoothing and quantile ranking did not earn their place in the
# score. Captured demand is still computed for the card and the break-even scenario; the Monte Carlo still gives
# the interval.
DEFAULT_OPTS = {"weights": "map", "kernel_features": False, "kernel_demand": False, "huff": True, "demand": False,
                "agg": False, "suitability": True, "gate": True, "rank": "point"}

# ----------------------------------------------------------------------------------------------------------------
# Concept -> OSM tags. Words are matched after lowercasing and stripping accents (EN, FR, ES, AR).

FOOD_AMENITIES = {"restaurant", "cafe", "fast_food", "food_court", "ice_cream", "bar", "pub", "biergarten"}
BAR_AMENITIES = {"bar", "pub", "biergarten"}

SYNONYMS = {
    "coffee": {"words": ["specialty coffee", "coffee shop", "coffee", "espresso", "barista", "cafe", "cafeteria",
                         "torrefacteur", "cafe de especialidad", "قهوة", "مقهى", "كافيه"],
               "cuisine": ["coffee_shop", "coffee"], "amenity": ["cafe"], "profile": "cafe", "label": "coffee shops"},
    "tea": {"words": ["tea house", "teahouse", "tea room", "bubble tea", "boba", "salon de the", "casa de te", "شاي"],
            "cuisine": ["tea", "bubble_tea"], "amenity": [], "profile": "cafe", "label": "tea rooms"},
    "bakery": {"words": ["bakery", "boulangerie", "patisserie", "pastry", "pastries", "panaderia", "pasteleria",
                         "مخبزة", "حلويات"],
               "cuisine": ["pastry", "cake", "bakery", "dessert"], "amenity": [], "profile": "cafe",
               "label": "bakeries and pastry cafés"},
    "brunch": {"words": ["brunch", "breakfast", "petit dejeuner", "desayuno", "فطور"],
               "cuisine": ["breakfast", "brunch"], "amenity": [], "profile": "cafe", "label": "brunch places"},
    "ice_cream": {"words": ["ice cream", "gelato", "glacier", "helado", "heladeria", "مثلجات", "ايس كريم"],
                  "cuisine": ["ice_cream", "frozen_yogurt"], "amenity": ["ice_cream"], "profile": "fast_casual",
                  "label": "ice cream shops"},
    "korean": {"words": ["korean", "coreen", "coreano", "كوري"], "cuisine": ["korean"], "label": "Korean places"},
    "chicken": {"words": ["fried chicken", "chicken", "wings", "poulet", "pollo", "دجاج", "فراخ"],
                "cuisine": ["chicken", "fried_chicken", "wings"], "profile": "fast_casual",
                "label": "chicken places"},
    "burger": {"words": ["burger", "burgers", "hamburguesa", "برغر", "برجر"], "cuisine": ["burger"],
               "profile": "fast_casual", "label": "burger places"},
    "pizza": {"words": ["pizza", "pizzeria", "بيتزا"], "cuisine": ["pizza"], "profile": "fast_casual",
              "label": "pizzerias"},
    "japanese": {"words": ["sushi", "japanese", "japonais", "japones", "ramen", "سوشي", "ياباني"],
                 "cuisine": ["sushi", "japanese", "ramen"], "label": "Japanese places"},
    "moroccan": {"words": ["moroccan", "marocain", "marocaine", "marroqui", "tagine", "tajine", "couscous",
                           "مغربي", "مغربية", "طاجين", "كسكس"],
                 "cuisine": ["moroccan", "couscous", "north_african"], "label": "Moroccan restaurants"},
    "lebanese": {"words": ["lebanese", "libanais", "libanes", "shawarma", "chawarma", "falafel", "لبناني", "شاورما",
                           "فلافل"],
                 "cuisine": ["lebanese", "shawarma", "falafel", "middle_eastern"], "label": "Lebanese places"},
    "kebab": {"words": ["kebab", "doner", "كباب"], "cuisine": ["kebab", "turkish"], "profile": "fast_casual",
              "label": "kebab shops"},
    "italian": {"words": ["italian", "italien", "italienne", "italiano", "pasta", "trattoria", "إيطالي", "ايطالي"],
                "cuisine": ["italian", "pasta"], "label": "Italian restaurants"},
    "french": {"words": ["french", "francais", "francaise", "bistro", "bistrot", "brasserie", "frances", "فرنسي"],
               "cuisine": ["french"], "label": "French restaurants"},
    "indian": {"words": ["indian", "indien", "indienne", "indio", "curry", "هندي"], "cuisine": ["indian"],
               "label": "Indian restaurants"},
    "chinese": {"words": ["chinese", "chinois", "chinoise", "chino", "dim sum", "صيني"], "cuisine": ["chinese"],
                "label": "Chinese restaurants"},
    "thai": {"words": ["thai", "thailandais", "tailandes", "تايلاندي"], "cuisine": ["thai"],
             "label": "Thai restaurants"},
    "mexican": {"words": ["mexican", "mexicain", "mexicaine", "mexicano", "taco", "tacos", "burrito", "taqueria",
                          "مكسيكي"],
                "cuisine": ["mexican", "tex-mex", "tacos", "burrito"], "label": "Mexican places"},
    "bbq": {"words": ["barbecue", "bbq", "barbacoa", "smokehouse", "مشويات", "شواء"],
            "cuisine": ["barbecue", "bbq", "grill"], "label": "barbecue places"},
    "seafood": {"words": ["seafood", "fish", "poisson", "fruits de mer", "mariscos", "pescado", "marisqueria",
                          "سمك", "مأكولات بحرية"],
                "cuisine": ["seafood", "fish"], "label": "seafood restaurants"},
    "vegan": {"words": ["vegan", "vegane", "vegetalien", "vegetalienne", "vegano", "vegana", "plant based",
                        "plant-based", "نباتي"],
              "cuisine": ["vegan"], "diet": "vegan", "profile": "healthy", "label": "vegan places"},
    "vegetarian": {"words": ["vegetarian", "vegetarien", "vegetarienne", "vegetariano"], "cuisine": ["vegetarian"],
                   "diet": "vegetarian", "profile": "healthy", "label": "vegetarian places"},
    "bowls": {"words": ["bowls", "bowl", "poke", "salad", "salade", "ensalada", "healthy", "acai", "سلطة", "صحي"],
              "cuisine": ["poke", "salad", "healthy", "hawaiian", "bowl", "juice"], "profile": "healthy",
              "label": "salad and bowl places"},
    "fast_food": {"words": ["fast food", "fast-food", "quick service", "snack", "restauration rapide",
                            "comida rapida", "وجبات سريعة"],
                  "cuisine": [], "amenity": ["fast_food"], "profile": "fast_casual", "label": "fast-food outlets"},
    "bar": {"words": ["wine bar", "bar a vin", "cocktail", "cocktails", "bar", "pub", "taproom", "bodega", "حانة"],
            "cuisine": ["wine", "cocktails"], "amenity": ["bar", "pub", "biergarten"], "profile": "bar",
            "label": "bars"},
}
MODIFIERS = {
    "fine_dining": ["fine dining", "gastronomique", "gastronomic", "haute cuisine", "upscale", "alta cocina",
                    "gourmet", "راقي", "فاخر"],
    "fast_casual": ["fast food", "quick service", "takeaway", "take away", "a emporter", "counter",
                    "comida rapida", "street food", "وجبات سريعة"],
}
# Narrow concepts: words that make a concept narrower than its OSM tag, and the name/brand cues that identify its
# venues. When too few venues carry a cue, the broad tag set is used and the output says so.
SPECIALTY_WORDS = ["specialty", "speciality", "third wave", "de specialite", "de especialidad", "roastery",
                   "torrefacteur", "مختصة"]
SPECIALTY_CUES = {
    "coffee": {"name_words": ["coffee", "roast", "roaster", "roasters", "roastery", "torrefacteur", "torrefaction",
                              "espresso", "barista", "brew", "brewing", "specialty", "speciality", "kaffee"],
               "brands": ["% arabica", "starbucks", "tim hortons", "costa coffee", "columbus cafe", "bondi coffee",
                          "kavo", "coutume", "ten belles", "kb coffee", "telescope", "lomi", "terres de cafe",
                          "cafe kitsune", "brewlab"],
               "cuisine": ["coffee_roastery"]},
}
CATALOG = ["specialty coffee", "tea house", "bakery café", "brunch", "ice cream", "Korean fried chicken", "burger",
           "pizza", "sushi", "Moroccan fine dining", "Lebanese shawarma", "Italian trattoria", "French bistro",
           "Indian curry", "tacos", "vegan bowls"]


def _norm(text):
    text = unicodedata.normalize("NFKD", str(text).lower())
    return "".join(ch for ch in text if not unicodedata.combining(ch))


def _has_word(text, word):
    if re.search(r"[\u0600-\u06ff]", word):
        return word in text
    return re.search(r"(?<![a-z0-9])" + re.escape(word) + r"(?![a-z0-9])", text) is not None


def map_concept(text):
    """Concept words -> match groups of OSM tags, a concept profile and a short label. Pure."""
    t = _norm(text)
    groups, profile = [], None
    for key, s in SYNONYMS.items():
        hit = next((w for w in sorted(s["words"], key=len, reverse=True) if _has_word(t, _norm(w))), None)
        if hit is None or (key == "bar" and hit == "bar" and groups):
            continue  # "sushi bar", "coffee bar": the format word, not a bar concept
        groups.append({"key": key, "cuisine": s.get("cuisine", []), "amenity": s.get("amenity", []),
                       "diet": s.get("diet"), "names": [_norm(w) for w in s["words"] if len(_norm(w)) >= 4],
                       "label": s["label"], "matched_word": hit})
        profile = profile or s.get("profile")
    for mod, words in MODIFIERS.items():
        if any(_has_word(t, _norm(w)) for w in words):
            profile = mod
            break
    recognized = bool(groups)
    if not groups:
        groups = [{"key": "restaurant", "cuisine": [], "amenity": ["restaurant", "fast_food"], "diet": None,
                   "names": [], "label": "restaurants", "matched_word": None}]
    amenities = sorted({a for g in groups for a in g["amenity"]} or {"restaurant", "fast_food", "cafe"})
    narrow = any(_has_word(t, _norm(w)) for w in SPECIALTY_WORDS)
    cues = next((SPECIALTY_CUES[g["key"]] for g in groups if g["key"] in SPECIALTY_CUES), None) if narrow else None
    return {"text": text, "recognized": recognized, "profile": profile or "restaurant", "groups": groups,
            "cues": cues, "label": " / ".join(g["label"] for g in groups),
            "osm": {"amenity": amenities, "cuisine": sorted({c for g in groups for c in g["cuisine"]}),
                    "diet": sorted({g["diet"] for g in groups if g["diet"]})}}


def competitor_weight(tags, concept):
    """0..1: how directly an OSM food venue competes with the concept (mean over the concept's groups). Pure."""
    if tags.get("amenity") not in FOOD_AMENITIES:
        return 0.0
    cuisines = {c.strip() for c in _norm(tags.get("cuisine", "")).replace(",", ";").split(";") if c.strip()}
    name = _norm(tags.get("name", ""))
    total = 0.0
    for g in concept["groups"]:
        s = 0.0
        if cuisines & set(g["cuisine"]) or tags.get("amenity") in g["amenity"]:
            s = 1.0
        elif g["diet"] and tags.get(f"diet:{g['diet']}") in ("only", "yes"):
            s = 1.0 if tags.get(f"diet:{g['diet']}") == "only" else 0.5
        elif name and any(_has_word(name, w) for w in g["names"]):
            s = 1.0
        total += s
    return round(total / len(concept["groups"]), 3)


def matches_cues(tags, cues):
    """True when a venue's name, brand or cuisine carries one of a narrow concept's cues."""
    text = _norm(" ".join(tags.get(k, "") for k in ("name", "brand", "name:en", "name:fr")))
    cuisines = set(_norm(tags.get("cuisine", "")).replace(",", ";").split(";"))
    return (any(_has_word(text, w) for w in cues["name_words"]) or any(b in text for b in cues["brands"])
            or bool(cuisines & set(cues["cuisine"])))


# ----------------------------------------------------------------------------------------------------------------
# Signals, priors and labels

POINT_CATS = ["office", "coworking", "university", "school", "station", "bus_stop", "mall", "hotel", "attraction",
              "nightlife", "culture", "fitness"]
AREA_CATS = ["residential", "commercial"]  # hectares of landuse polygons (population and jobs proxies)
COUNT_CATS = POINT_CATS + AREA_CATS + ["food_other"]

# Model features: standardised log1p of kernel densities of these (category, multiplier) sums.
MODEL_FEATURES = {
    "office": [("office", 1), ("coworking", 1)], "university": [("university", 1)], "school": [("school", 1)],
    "transit": [("station", 3), ("bus_stop", 1)], "mall": [("mall", 1)], "hotel": [("hotel", 1)],
    "attraction": [("attraction", 1)], "nightlife": [("nightlife", 1)], "culture": [("culture", 1)],
    "fitness": [("fitness", 1)], "residential": [("residential", 1)], "commercial": [("commercial", 1)],
    "food_other": [("food_other", 1)],
}
FEATURE_NAMES = list(MODEL_FEATURES)
# Sign constraints: footfall generators cannot lower suitability; other food (competition or agglomeration) can.
NONNEG = [k for k, f in enumerate(FEATURE_NAMES) if f != "food_other"]

# Relevance of each signal to a concept profile (0 to 3). Used only for (a) the demand-surface weights and
# (b) the documented fallback prior when a city has too few presences to learn from.
RELEVANCE = {
    "cafe": {"office": 3, "commercial": 1.5, "university": 3, "school": 0.5, "transit": 2, "mall": 1.5,
             "hotel": 1, "attraction": 1, "nightlife": 0.3, "culture": 1, "fitness": 0.5, "residential": 1.5},
    "fast_casual": {"office": 2, "commercial": 1.5, "university": 3, "school": 1.5, "transit": 2, "mall": 2.5,
                    "hotel": 0.5, "attraction": 1, "nightlife": 2, "culture": 1.5, "fitness": 0.5,
                    "residential": 2},
    "healthy": {"office": 3, "commercial": 1.5, "university": 2.5, "school": 0.3, "transit": 1.5, "mall": 1,
                "hotel": 0.5, "attraction": 0.5, "nightlife": 0.3, "culture": 0.5, "fitness": 2.5,
                "residential": 1.5},
    "fine_dining": {"office": 1.5, "commercial": 0.5, "university": 0.3, "school": 0, "transit": 1, "mall": 0.5,
                    "hotel": 3, "attraction": 2.5, "nightlife": 1.5, "culture": 2, "fitness": 0, "residential": 1},
    "bar": {"office": 1.5, "commercial": 0.5, "university": 2, "school": 0, "transit": 1.5, "mall": 0.5,
            "hotel": 1.5, "attraction": 1, "nightlife": 3, "culture": 2, "fitness": 0, "residential": 1.5},
    "restaurant": {"office": 2, "commercial": 1, "university": 1.5, "school": 0.5, "transit": 1.5, "mall": 1.5,
                   "hotel": 1.5, "attraction": 1.5, "nightlife": 1, "culture": 1.5, "fitness": 0.3,
                   "residential": 2},
}
# Trip attraction of one mapped object relative to one office (per hectare for land use). A stated prior.
TRIP_BASE = {"office": 1, "coworking": 2, "university": 5, "school": 2, "station": 5, "bus_stop": 0.5, "mall": 5,
             "hotel": 2, "attraction": 2, "nightlife": 1, "culture": 2, "fitness": 1, "residential": 0.5,
             "commercial": 0.5}
CAT_TO_FEATURE = {c: f for f, parts in MODEL_FEATURES.items() for c, _ in parts}
# Weight of the clustering bonus: destination (comparison) concepts gain from clusters, convenience ones do not.
AGGLOMERATION_GAMMA = {"fine_dining": 0.15, "bar": 0.15, "restaurant": 0.10, "fast_casual": 0.05, "cafe": 0.0,
                       "healthy": 0.0}
PRIOR_LOGIT_SCALE = 1.0  # fallback prior rescaled so the city's median |logit| is 1
PRIOR_SD = 0.5  # stated uncertainty of every fallback coefficient (standardised scale)

LABELS = {
    "office": ("offices and coworking spaces", "bureaux et espaces de coworking"),
    "university": ("university or college sites", "sites universitaires"),
    "school": ("schools", "écoles"),
    "transit": ("transit stops", "arrêts de transport"),
    "mall": ("malls, department stores or markets", "centres commerciaux, grands magasins ou marchés"),
    "hotel": ("hotels", "hôtels"),
    "attraction": ("museums and attractions", "musées et attractions"),
    "nightlife": ("bars and nightclubs", "bars et discothèques"),
    "culture": ("cinemas and theatres", "cinémas et théâtres"),
    "fitness": ("gyms and sports centres", "salles de sport"),
    "residential": ("ha of residential land", "ha de quartiers résidentiels"),
    "commercial": ("ha of commercial land", "ha de zones commerciales"),
    "food_other": ("other restaurants and cafés", "autres restaurants et cafés"),
}


def demand_weights(profile):
    """w(g) of the demand surface: trip attraction prior x concept relevance / 2."""
    rel = RELEVANCE[profile]
    return {c: TRIP_BASE[c] * rel[CAT_TO_FEATURE[c]] / 2 for c in POINT_CATS + AREA_CATS}


def prior_beta(profile):
    """Fallback logit coefficients on standardised features when a city has too few presences to fit."""
    rel = RELEVANCE[profile]
    return [0.0] + [0.5 if f == "food_other" else 0.25 * rel[f] for f in FEATURE_NAMES]


# ----------------------------------------------------------------------------------------------------------------
# Grid

def clip_bbox(bbox, center, max_km):
    """Intersect a (south, west, north, east) bbox with a max_km square around center. Pure."""
    s, w, n, e = bbox
    half_lat = max_km * 500 / M_PER_DEG_LAT
    half_lon = max_km * 500 / (111_320 * math.cos(math.radians(center[0])))
    return (max(s, center[0] - half_lat), max(w, center[1] - half_lon),
            min(n, center[0] + half_lat), min(e, center[1] + half_lon))


def make_grid(bbox, cell_m=400, max_cells=2500):
    """Square cells over the bbox; cell size grows when the cap would be exceeded. Pure."""
    s, w, n, e = bbox
    if not (n > s and e > w):
        raise ValueError("empty bbox")
    kx = 111_320 * math.cos(math.radians((s + n) / 2))
    h_m, w_m = (n - s) * M_PER_DEG_LAT, (e - w) * kx
    cell_m = max(cell_m, math.sqrt(h_m * w_m / max_cells))
    while math.ceil(h_m / cell_m) * math.ceil(w_m / cell_m) > max_cells:
        cell_m *= 1.02
    rows, cols = max(1, math.ceil(h_m / cell_m)), max(1, math.ceil(w_m / cell_m))
    return {"bbox": [s, w, n, e], "cell_m": cell_m, "rows": rows, "cols": cols,
            "dlat": cell_m / M_PER_DEG_LAT, "dlon": cell_m / kx}


def cell_of(grid, lat, lon):
    s, w, n, e = grid["bbox"]
    if not (s <= lat <= n and w <= lon <= e):
        return None
    r = min(int((lat - s) / grid["dlat"]), grid["rows"] - 1)
    c = min(int((lon - w) / grid["dlon"]), grid["cols"] - 1)
    return r * grid["cols"] + c


def cell_bounds(grid, i):
    r, c = divmod(i, grid["cols"])
    s, w = grid["bbox"][0] + r * grid["dlat"], grid["bbox"][1] + c * grid["dlon"]
    return [[round(s, 6), round(w, 6)], [round(s + grid["dlat"], 6), round(w + grid["dlon"], 6)]]


def cell_center(grid, i):
    r, c = divmod(i, grid["cols"])
    return (grid["bbox"][0] + (r + 0.5) * grid["dlat"], grid["bbox"][1] + (c + 0.5) * grid["dlon"])


def neighbors(grid, i):
    r, c = divmod(i, grid["cols"])
    return [(r + dr) * grid["cols"] + c + dc for dr in (-1, 0, 1) for dc in (-1, 0, 1)
            if (dr or dc) and 0 <= r + dr < grid["rows"] and 0 <= c + dc < grid["cols"]]


# ----------------------------------------------------------------------------------------------------------------
# OSM elements

def classify(tags):
    """Footfall signals an OSM element contributes (food venues are handled separately). Pure."""
    out = []
    a, t, sh, rw, pt = (tags.get(k, "") for k in ("amenity", "tourism", "shop", "railway", "public_transport"))
    if tags.get("office") or a == "coworking_space":
        out.append("coworking" if a == "coworking_space" or tags.get("office") == "coworking" else "office")
    if a in ("university", "college"):
        out.append("university")
    elif a == "school":
        out.append("school")
    if rw in ("station", "halt", "tram_stop") or pt == "station":
        out.append("station")
    elif tags.get("highway") == "bus_stop":
        out.append("bus_stop")
    if sh in ("mall", "department_store") or a == "marketplace":
        out.append("mall")
    if t in ("hotel", "hostel", "guest_house"):
        out.append("hotel")
    elif t in ("attraction", "museum", "gallery", "viewpoint", "theme_park", "zoo"):
        out.append("attraction")
    if a in ("bar", "pub", "nightclub", "biergarten"):
        out.append("nightlife")
    if a in ("cinema", "theatre", "arts_centre", "concert_hall"):
        out.append("culture")
    if tags.get("leisure") in ("fitness_centre", "sports_centre"):
        out.append("fitness")
    return list(dict.fromkeys(out))


def _point(el):
    if "lat" in el and "lon" in el:
        return el["lat"], el["lon"]
    c = el.get("center")
    return (c["lat"], c["lon"]) if c else None


def _area_shares(grid, b):
    """Hectares of a bounding box falling in each cell it overlaps."""
    s, w, n, e = b["minlat"], b["minlon"], b["maxlat"], b["maxlon"]
    g = grid["bbox"]
    s, w, n, e = max(s, g[0]), max(w, g[1]), min(n, g[2]), min(e, g[3])
    if n <= s or e <= w:
        return {}
    ha_per_deg2 = M_PER_DEG_LAT * (grid["cell_m"] / grid["dlon"]) / 10_000
    r0, r1 = int((s - g[0]) / grid["dlat"]), min(int((n - g[0]) / grid["dlat"]), grid["rows"] - 1)
    c0, c1 = int((w - g[1]) / grid["dlon"]), min(int((e - g[1]) / grid["dlon"]), grid["cols"] - 1)
    out = {}
    for r in range(r0, r1 + 1):
        for c in range(c0, c1 + 1):
            cs, cw = g[0] + r * grid["dlat"], g[1] + c * grid["dlon"]
            dy = min(n, cs + grid["dlat"]) - max(s, cs)
            dx = min(e, cw + grid["dlon"]) - max(w, cw)
            if dx > 0 and dy > 0 and dx * dy * ha_per_deg2 > 0.01:
                out[r * grid["cols"] + c] = dx * dy * ha_per_deg2
    return out


def _place_name(tags):
    name = tags.get("name", "")
    if name and re.search(r"[\u0600-\u06ff\u2d30-\u2d7f]", name):
        name = tags.get("name:fr") or tags.get("name:en") or name
    return name


_OV_CUISINE = {"taco": "tacos", "breakfast_and_brunch": "breakfast", "health_food": "healthy", "doner_kebab": "kebab",
               "fish_and_chips": "fish", "bar_and_grill": "grill", "asian_fusion": "asian", "hot_dog": "hot_dog",
               "chicken_wings": "wings", "middle_eastern": "middle_eastern"}
_OV_BARS = {"bar", "lounge", "hookah_bar", "tapas_bar", "wine_bar", "beer_bar", "sports_bar", "cocktail_bar",
            "hotel_bar", "beer_garden", "whiskey_bar", "gay_bar", "dive_bar", "piano_bar"}
_OV_SCHOOLS = {"school", "elementary_school", "middle_school", "high_school", "private_school", "public_school",
               "preschool", "religious_school", "day_care_preschool"}
_OV_FITNESS = {"gym", "fitness_studio", "sport_or_fitness_facility", "yoga_studio", "pilates_studio", "boxing_class",
               "gymnastics_center", "fitness_trainer"}
_OV_NOT_OFFICE = {"doctors_office", "post_office", "office_supply_store", "business_office_supplies_and_stationery"}


def overture_tags(cat):
    """Overture Places category (taxonomy.primary / basic_category) -> equivalent OSM tags, or None. Pure."""
    c = (cat or "").lower()
    if c in ("coffee_shop", "coffee_roastery"):
        return {"amenity": "cafe", "cuisine": c}
    if c in ("cafe", "tea_room", "smoothie_juice_bar", "bubble_tea"):
        return {"amenity": "cafe", **({"cuisine": {"tea_room": "tea", "smoothie_juice_bar": "juice",
                                                    "bubble_tea": "bubble_tea"}[c]} if c != "cafe" else {})}
    if c in ("ice_cream_shop", "frozen_yoghurt_shop", "gelato"):
        return {"amenity": "ice_cream"}
    if c == "pub":
        return {"amenity": "pub"}
    if c in _OV_BARS:
        return {"amenity": "bar"}
    if c in ("night_club", "nightclub", "dance_club"):
        return {"amenity": "nightclub"}
    if c == "fast_food_restaurant":
        return {"amenity": "fast_food"}
    if c == "salad_bar":
        return {"amenity": "restaurant", "cuisine": "salad"}
    if c in ("restaurant", "diner", "steakhouse", "pancake_house", "food_court"):
        return {"amenity": "food_court" if c == "food_court" else "restaurant",
                **({"cuisine": {"steakhouse": "steak_house", "pancake_house": "pancake"}[c]}
                   if c in ("steakhouse", "pancake_house") else {})}
    if c.endswith("_restaurant"):
        stem = c[: -len("_restaurant")]
        return {"amenity": "restaurant", "cuisine": _OV_CUISINE.get(stem, stem)}
    if c == "coworking_space":
        return {"amenity": "coworking_space"}
    if c == "college_university":
        return {"amenity": "university"}
    if c in _OV_SCHOOLS:
        return {"amenity": "school"}
    if c in ("train_station", "metro_station", "subway_station", "light_rail_and_subway_stations", "tram_station",
             "railway_station"):
        return {"railway": "station"}
    if c in ("shopping_mall", "shopping_center", "department_store", "farmers_market", "flea_market",
             "public_market"):
        return {"shop": "mall"}
    if c in ("hotel", "hostel", "motel", "bed_and_breakfast", "guest_house", "resort"):
        return {"tourism": "hotel"}
    if c in ("museum", "art_gallery", "monument", "observatory", "landmark_and_historical_building") \
            or c.endswith("_museum") or c.endswith("_attraction"):
        return {"tourism": "museum"}
    if c in ("movie_theater", "cinema", "theatre_venue", "theater", "performing_arts_venue"):
        return {"amenity": "cinema"}
    if c in _OV_FITNESS:
        return {"leisure": "fitness_centre"}
    if c not in _OV_NOT_OFFICE and ("office" in c or c.endswith("_company") or c.startswith("b2b_")
                                    or c.endswith("_agency")):
        return {"office": "company"}
    return None


def _food_key(amenity):
    return {"cafe": "cafe", "ice_cream": "cafe", "bar": "bar", "pub": "bar", "biergarten": "bar"}.get(amenity,
                                                                                                     "restaurant")


def compare_key(tags):
    """Category used to compare the two sources (None when only one source covers it, e.g. bus stops)."""
    if tags.get("amenity") in FOOD_AMENITIES:
        return _food_key(tags["amenity"])
    cats = classify(tags)
    if not cats:
        return None
    k = {"coworking": "office", "bus_stop": None}.get(cats[0], cats[0])
    return k


COMPARE_KEYS = ["restaurant", "cafe", "bar", "office", "university", "school", "station", "mall", "hotel",
                "attraction", "nightlife", "culture", "fitness"]
# Count layer -> compare key whose detection rate applies (layers absent here are OSM-only: q = 1, kappa = 1).
LAYER_KEY = {"office": "office", "coworking": "office", "university": "university", "school": "school",
             "station": "station", "mall": "mall", "hotel": "hotel", "attraction": "attraction",
             "nightlife": "nightlife", "culture": "culture", "fitness": "fitness", "food_other": "food"}
KAPPA_MIN = 0.2
# Chapman needs both sources to survey the same population. That holds for food venues; office, school and
# attraction categories are defined differently in the two taxonomies, so their detection rates are reported only.
DETECTION_KEYS = {"food", "restaurant", "cafe", "bar"}


_GENERIC_NAME = set("cafe caffe coffee restaurant resto snack pizzeria patisserie boulangerie glacier brasserie "
                    "salon bar pub de du des la le les l d el al and et the chez espace food "
                    "مقهى مطعم".split())


def _name_key(name):
    """Normalised name without generic venue words, so 'Café Leopardi' and 'leopardi ليوباردي' compare well."""
    toks = re.sub(r"[^\w\s]", " ", _norm(name or "")).split()
    return " ".join(t for t in toks if t not in _GENERIC_NAME)


KEEP_TAGS = {"amenity", "cuisine", "name", "brand", "name:fr", "name:en", "diet:vegan", "diet:vegetarian", "office", "railway",
             "public_transport", "highway", "shop", "tourism", "leisure", "place", "landuse"}


def make_pack(info, elements):
    """Offline city pack: Nominatim info plus OSM elements reduced to coordinates and the tags the model reads."""
    out = []
    for el in elements:
        tags = {k: v for k, v in (el.get("tags") or {}).items() if k in KEEP_TAGS}
        if not tags:
            continue
        e = {"tags": tags}
        if "bounds" in el:
            e["bounds"] = el["bounds"]
        else:
            p = _point(el)
            if p is None:
                continue
            e["lat"], e["lon"] = round(p[0], 6), round(p[1], 6)
        out.append(e)
    return {"info": info, "elements": out, "attribution": ATTRIBUTION + " (ODbL)",
            "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}


# ----------------------------------------------------------------------------------------------------------------
# City model (concept independent)

def build_city(info, elements, params=None, overture=None):
    """Grid, per-cell counts, empirical-Bayes posteriors and kernel densities for every signal. Pure.

    With `overture` (pack["overture"]), POIs from both sources are matched and de-duplicated; the union is counted,
    Chapman detection rates set the Poisson exposure q, and cell completeness sets the posterior discount kappa.
    """
    P = {**PARAMS, **(params or {})}
    bbox = clip_bbox(info["bbox"], info["center"], P["max_km"])
    grid = make_grid(bbox, P["cell_m"], P["max_cells"])
    rows, cols, n = grid["rows"], grid["cols"], grid["rows"] * grid["cols"]
    s, w, nn, e = grid["bbox"]
    proj = cm.Projection((s + nn) / 2, (w + e) / 2)
    counts = {c: [0.0] * n for c in POINT_CATS + AREA_CATS}
    pts = {c: [] for c in POINT_CATS}
    food, places, activity = [], [], [0.0] * n
    osm, ov = [], []

    def poi(lat, lon, tags, source):
        i = cell_of(grid, lat, lon)
        key = compare_key(tags)
        if i is None or (key is None and not classify(tags)):
            return None
        x, y = proj.xy(lat, lon)
        return {"lat": lat, "lon": lon, "x": x, "y": y, "cell": i, "tags": tags, "key": key, "source": source,
                "match": "food" if tags.get("amenity") in FOOD_AMENITIES else key, "name": _name_key(tags.get("name"))}

    for el in elements:
        tags = el.get("tags") or {}
        if tags.get("landuse") in ("residential", "commercial", "retail") and "bounds" in el:
            key = "residential" if tags["landuse"] == "residential" else "commercial"
            for i, ha in _area_shares(grid, el["bounds"]).items():
                counts[key][i] += ha
            continue
        p = _point(el)
        if p is None:
            continue
        if tags.get("place") in ("suburb", "quarter", "neighbourhood") and tags.get("name"):
            x, y = proj.xy(*p)
            places.append({"name": _place_name(tags), "lat": p[0], "lon": p[1], "x": x, "y": y, "kind": tags["place"],
                           "norm": _norm(" ".join(tags.get(k, "") for k in ("name", "name:fr", "name:en")))})
            continue
        q = poi(p[0], p[1], tags, "osm")
        if q:
            osm.append(q)
    for lat, lon, name, cat, _conf in (overture or {}).get("places", []):
        tags = overture_tags(cat)
        if tags:
            q = poi(lat, lon, {**tags, **({"name": name} if name else {})}, "overture")
            if q:
                ov.append(q)

    coverage = two_source_coverage(osm, ov, n) if ov else None
    union = osm + [q for k, q in enumerate(ov) if k not in coverage["matched_ov"]] if ov else osm
    for q in union:
        i, tags = q["cell"], q["tags"]
        if tags.get("amenity") in FOOD_AMENITIES:
            food.append(q)
            activity[i] += 1
        cats = classify(tags)
        for c in cats:
            counts[c][i] += 1
            pts[c].append((q["x"], q["y"], 1.0))
        activity[i] += bool(cats)
    cell_m = grid["cell_m"]
    offsets = cm.cell_kernel_offsets(cell_m, P["kernel_lambda_m"], P["kernel_cutoff_m"])
    act_s = cm.smooth(activity, rows, cols, offsets)
    study = [i for i in range(n) if act_s[i] >= P["activity_min"]]
    city = {"info": info, "grid": grid, "params": P, "proj": proj, "offsets": offsets, "counts": counts,
            "food": food, "places": places, "study": study, "study_set": set(study),
            "xy": [proj.xy(*cell_center(grid, i)) for i in range(n)],
            "index": {c: cm.GridIndex(pts[c], cell_m) for c in POINT_CATS},
            "eb": {}, "post": {}, "dens": {}, "q": {}, "kappa": [1.0] * n, "coverage": None,
            "sources": ["OpenStreetMap"] + (["Overture Maps Places"] if ov else [])}
    if coverage:
        defined = [coverage["completeness"][i] for i in study if coverage["completeness"][i] is not None]
        mean_c = sum(defined) / len(defined) if defined else 1.0
        city["kappa"] = [max(KAPPA_MIN, mean_c if c is None else c) for c in coverage["completeness"]]
        city["q"] = {k: v["detection_union"] for k, v in coverage["chapman"].items() if k in DETECTION_KEYS}
        food_c = [cm.completeness([(coverage["n_osm"][k][i], coverage["n_ov"][k][i])
                                   for k in ("restaurant", "cafe", "bar")]) for i in study]
        food_c = [c for c in food_c if c is not None]
        coverage["city"] = {
            "data_confidence": round(mean_c, 3),
            "data_confidence_food": round(sum(food_c) / len(food_c), 3) if food_c else None,
            "cells_with_data": len(defined),
            "share_cells_completeness_lt_0.5": round(sum(1 for c in defined if c < 0.5) / max(len(defined), 1), 3),
            "osm_pois": len(osm), "overture_pois": len(ov), "matched": len(coverage["matched_ov"]),
            "union_pois": len(union)}
        city["coverage"] = coverage
    for c in POINT_CATS + AREA_CATS:
        _eb_layer(city, c, counts[c])
    return city


def two_source_coverage(osm, ov, n_cells, max_d=50.0, min_sim=0.6):
    """Match OSM and Overture POIs (same match group, < max_d m, similar names); per-cell gaps and completeness;
    Chapman totals per compare category. Matched Overture items take the OSM category (OSM is the reference
    taxonomy). Pure."""
    matched_ov, ov_key = set(), {}
    groups = sorted({q["match"] for q in osm + ov if q["match"]})
    for gname in groups:
        a = [k for k, q in enumerate(osm) if q["match"] == gname]
        b = [k for k, q in enumerate(ov) if q["match"] == gname]
        if not a or not b:
            continue
        pairs = cm.match_points([(osm[k]["x"], osm[k]["y"], osm[k]["name"]) for k in a],
                                [(ov[k]["x"], ov[k]["y"], ov[k]["name"]) for k in b], max_d, min_sim)
        for ia, ib in pairs:
            matched_ov.add(b[ib])
            ov_key[b[ib]] = osm[a[ia]]["key"]
    n_osm = {k: [0] * n_cells for k in COMPARE_KEYS}
    n_ov = {k: [0] * n_cells for k in COMPARE_KEYS}
    m = dict.fromkeys(COMPARE_KEYS, 0)
    for q in osm:
        if q["key"] in n_osm:
            n_osm[q["key"]][q["cell"]] += 1
    for k, q in enumerate(ov):
        key = ov_key.get(k, q["key"])
        if key in n_ov:
            n_ov[key][q["cell"]] += 1
            if k in matched_ov:
                m[key] += 1
    comp = [cm.completeness([(n_osm[k][i], n_ov[k][i]) for k in COMPARE_KEYS]) for i in range(n_cells)]
    chap = {}
    for k in COMPARE_KEYS + ["food"]:
        keys = ("restaurant", "cafe", "bar") if k == "food" else (k,)
        n1 = sum(sum(n_osm[x]) for x in keys)
        n2 = sum(sum(n_ov[x]) for x in keys)
        mm = sum(m[x] for x in keys)
        if n1 + n2 == 0:
            continue
        ch = cm.chapman(n1, n2, mm)
        chap[k] = {"osm": n1, "overture": n2, "matched": mm, "union": ch["union"], "N_hat": round(ch["N"], 1),
                   "N_ci95": [round(v, 1) for v in ch["ci95"]], "missed_by_both": round(ch["missed_by_both"], 1),
                   "detection_osm": round(min(1.0, n1 / ch["N"]), 3) if ch["N"] > 0 else 1.0,
                   "detection_overture": round(min(1.0, n2 / ch["N"]), 3) if ch["N"] > 0 else 1.0,
                   "detection_union": round(min(1.0, ch["union"] / ch["N"]), 3) if ch["N"] > 0 else 1.0}
    return {"matched_ov": matched_ov, "n_osm": n_osm, "n_ov": n_ov, "completeness": comp, "chapman": chap}


def cell_gaps(city, i):
    """Per-category gaps for one cell: share of Overture POIs missing from OSM and vice versa."""
    cov = city["coverage"]
    if not cov:
        return None
    out = {}
    for k in COMPARE_KEYS:
        a, b = cov["n_osm"][k][i], cov["n_ov"][k][i]
        if a or b:
            out[k] = {"osm": a, "overture": b, "gap_osm": round(cm.coverage_gap(a, b), 2),
                      "gap_overture": round(cm.coverage_gap(b, a), 2)}
    return out


def _eb_layer(city, cat, counts):
    """Fit the detection-aware Gamma prior on study cells; store posterior means (0 outside the study area) and
    their kernel smoothing."""
    g = city["grid"]
    study, study_set = city["study"], city["study_set"]
    q = city["q"].get(LAYER_KEY.get(cat), 1.0) or 1.0
    a, b = cm.eb_gamma_prior([counts[i] for i in study], q)
    city["eb"][cat] = (a, b, q)
    city["counts"][cat] = counts
    if city["params"]["eb"]:
        post = [(a + counts[i]) / (b + q) if i in study_set else 0.0 for i in range(len(counts))]
    else:  # ablation: raw counts, no shrinkage and no detection correction
        post = [float(counts[i]) if i in study_set else 0.0 for i in range(len(counts))]
    city["post"][cat] = post
    city["dens"][cat] = cm.smooth(post, g["rows"], g["cols"], city["offsets"])


def _kappa(city, cat, i):
    return city["kappa"][i] if cat in LAYER_KEY else 1.0


def neighborhood(city, i):
    """OSM suburb/neighbourhood point inside the cell closest to its centre, else the nearest within 1.5 km."""
    x, y = city["xy"][i]
    g = city["grid"]
    half = g["cell_m"] / 2
    inside = [p for p in city["places"] if abs(p["x"] - x) <= half and abs(p["y"] - y) <= half]
    dist = lambda p: math.hypot(p["x"] - x, p["y"] - y)  # noqa: E731
    if inside:
        return min(inside, key=dist)["name"]
    near = [p for p in city["places"] if dist(p) <= 1500]
    return f"near {min(near, key=dist)['name']}" if near else None


# ----------------------------------------------------------------------------------------------------------------
# Concept model

def _features(dens, i):
    return [math.log1p(sum(m * dens[c][i] for c, m in MODEL_FEATURES[f])) for f in FEATURE_NAMES]


def _band_coord(city, i):
    g = city["grid"]
    r, c = divmod(i, g["cols"])
    return c if g["cols"] >= g["rows"] else r


def analyze(city, concept_text, top=10, cv=False, pool=None, extra_cells=(), draws=None, seed=None, opts=None):
    """Full model for one concept. Returns (JSON result, internal state for unit cards). Pure and seeded.

    `opts` switches components off for the ablation study (see DEFAULT_OPTS)."""
    t0 = time.perf_counter()
    P = city["params"]
    O = {**DEFAULT_OPTS, **(opts or {})}
    pool_size = P["pool"] if pool is None else pool
    draws = P["draws"] if draws is None else draws
    if O["demand"] and not (O["huff"] and O["kernel_demand"] and O["suitability"]):
        draws = 0  # the Monte Carlo implements suitability, or the full suitability x Huff formula only
    seed = P["seed"] if seed is None else seed
    g, study, study_set = city["grid"], city["study"], city["study_set"]
    rows_, cols_, n = g["rows"], g["cols"], g["rows"] * g["cols"]
    cell_m = g["cell_m"]
    concept = map_concept(concept_text)
    prof = concept["profile"]

    # Presences (existing venues of the concept) and the concept-specific "other food" layer. A narrow concept
    # ("specialty coffee") uses the venues carrying its cues as presences when there are enough of them; every
    # broad match stays a Huff competitor, with lower attractiveness.
    # A multi-part concept ("bakery café", "Korean fried chicken"): presences match every part; partial matches
    # are competitors only (otherwise "bakery café" would learn where every café is).
    broad, full, fo, fo_pts, night_adj = [], [], [0.0] * n, [], [0.0] * n
    for v in city["food"]:
        w = competitor_weight(v["tags"], concept)
        if w >= 0.5:
            broad.append(v)
            if w >= 1.0 - 1e-9:
                full.append(v)
            if v["tags"].get("amenity") in BAR_AMENITIES:
                night_adj[v["cell"]] += 1
        elif w == 0 and v["tags"].get("amenity") not in BAR_AMENITIES:
            fo[v["cell"]] += 1
            fo_pts.append((v["x"], v["y"], 1.0))
    narrow = [v for v in full if matches_cues(v["tags"], concept["cues"])] if concept["cues"] else []
    use_narrow = len(narrow) >= P["min_narrow"]
    if not use_narrow and len(full) < len(broad):
        narrow, use_narrow = full, True
    pres = narrow if use_narrow else broad
    narrow_ids = {id(v) for v in narrow} if use_narrow else None
    presence_note = None
    if len(concept["groups"]) > 1 and not concept["cues"]:
        presence_note = (f"Presences = {len(full)} venues matching every part of the concept ({concept['label']}); "
                         f"the other {len(broad) - len(full)} partial matches count as weaker competitors.")
    elif concept["cues"]:
        presence_note = (f"Presences = {len(narrow)} venues whose name or brand signals the narrow concept "
                         f"(out of {len(broad)} {concept['label']}); the other {len(broad) - len(narrow)} count as "
                         f"weaker competitors." if use_narrow else
                         f"Only {len(narrow)} venues carry a name/brand cue for this narrow concept (< "
                         f"{P['min_narrow']}): presences are all {len(broad)} {concept['label']}, so the model "
                         f"describes the broad category.")
    layer = {"eb": dict(city["eb"]), "post": dict(city["post"]), "dens": dict(city["dens"]),
             "counts": dict(city["counts"])}
    view = {**city, **layer}
    _eb_layer(view, "food_other", fo)
    if any(night_adj):  # a bar concept: its own venues are presences, not nightlife demand
        _eb_layer(view, "nightlife", [max(0.0, a - b) for a, b in zip(city["counts"]["nightlife"], night_adj)])
    dens, post, eb, counts = view["dens"], view["post"], view["eb"], view["counts"]
    fo_index = cm.GridIndex(fo_pts, cell_m)

    # Standardised features over the study area (kernel densities, or own-cell counts for the ablation).
    X = {i: _features(dens if O["kernel_features"] else post, i) for i in study}
    mu, sd = cm.standardize([X[i] for i in study])
    Z = {i: [(v - m) / s for v, m, s in zip(X[i], mu, sd)] for i in study}

    # Presence-background training set.
    rng = random.Random(seed)
    pres_cells = [v["cell"] for v in pres if v["cell"] in study_set]
    if len(pres_cells) > P["max_rows"]:
        pres_cells = sorted(rng.sample(pres_cells, P["max_rows"]))
    bg = study if len(study) <= P["max_rows"] else sorted(rng.sample(study, P["max_rows"]))
    n_p = len(pres_cells)
    fitted = n_p >= P["min_presences"]

    def rows(p_cells, b_cells):
        Xr = [Z[c] for c in p_cells] + [Z[c] for c in b_cells]
        y = [1] * len(p_cells) + [0] * len(b_cells)
        wts = [1.0] * len(p_cells) + [len(p_cells) / len(b_cells)] * len(b_cells)
        return Xr, y, wts

    prior = prior_beta(prof)
    spread = cm.percentile([abs(cm.logit(prior, Z[i])) for i in study], 0.5) or 1.0
    prior = [b * PRIOR_LOGIT_SCALE / spread for b in prior]  # median |logit| = 1 across the city

    def fit_pinned(p_cells, b_cells):
        Xr, y, wts = rows(p_cells, b_cells)
        if O["weights"] == "l2":  # ablation: plain ridge toward 0, no sign constraints
            return cm.fit_logistic(Xr, y, wts, l2=P["l2"]), set()
        return cm.fit_logistic_nonneg(Xr, y, wts, l2=P["l2"], prior=prior[1:], nonneg=NONNEG)

    def fit(p_cells, b_cells):
        return fit_pinned(p_cells, b_cells)[0]

    learned = fitted and O["weights"] != "prior"
    pinned = set()
    if learned:
        beta, pinned = fit_pinned(pres_cells, bg)
        Xr, _, wts = rows(pres_cells, bg)
        cov = cm.logistic_covariance(Xr, wts, beta, P["l2"], fixed=pinned)
    else:
        beta = prior
        cov = [[PRIOR_SD ** 2 if a == b else 0.0 for b in range(len(beta))] for a in range(len(beta))]
    chol = cm.cholesky(cov)
    L = {i: cm.logit(beta, Z[i]) for i in study}
    p = {i: cm.sigmoid(L[i]) for i in study}

    validation, oof, platt = None, None, None
    if cv:
        validation, oof = _validate(city, Z, L, pres_cells, bg, learned, fit, P)
        if oof:
            y_cell = [1 if c in set(pres_cells) else 0 for c in study]
            a_, b_ = cm.platt([oof.get(c, L[c]) for c in study], y_cell)
            platt = (a_, b_)
            cal = [cm.sigmoid(a_ * oof.get(c, L[c]) + b_) for c in study]
            validation["platt"] = {"a": round(a_, 4), "b": round(b_, 4),
                                   "brier": round(cm.brier(y_cell, cal), 4),
                                   "base_rate": round(sum(y_cell) / len(y_cell), 4)}

    # Residual autocorrelation (Moran's I) on cells: presence indicator minus calibrated suitability.
    has = [0] * n
    for c in pres_cells:
        has[c] = 1
    mean_y = sum(has[i] for i in study) / len(study)
    mean_p = sum(p.values()) / len(study)
    k_cal = mean_y / mean_p if mean_p else 0.0
    fitted = learned
    moran = cm.morans_i({i: has[i] - k_cal * p[i] for i in study}, cm.rook_neighbors(rows_, cols_, study))

    # Demand surface and Huff capture against existing same-concept venues.
    wd = demand_weights(prof)
    W = [sum(wd[c] * post[c][i] for c in wd) for i in range(n)]
    D = cm.smooth(W, rows_, cols_, city["offsets"]) if O["kernel_demand"] else W
    attract = (lambda v: 1.0 if id(v) in narrow_ids else P["generic_attract"]) if use_narrow else (lambda v: 1.0)
    comp = [(v["x"], v["y"], v) for v in broad]
    cidx = cm.GridIndex(comp, cell_m)
    pidx = cm.GridIndex([(v["x"], v["y"], v) for v in pres], cell_m) if use_narrow else cidx
    beta_h, alpha_h, floor_h, R = P["huff_beta"], P["huff_alpha"], P["huff_floor_m"], P["huff_radius_m"]
    # Unmapped competitors (Chapman detection rate q of the union, when a second source exists) are assumed to sit
    # like the mapped ones: competitor sums are scaled by 1/q, consistently with the calibration by N_hat below.
    src = city["coverage"]
    key = "cafe" if set(concept["osm"]["amenity"]) <= {"cafe", "ice_cream"} else \
        "bar" if set(concept["osm"]["amenity"]) <= BAR_AMENITIES else "restaurant"
    q_det = (src["chapman"].get(key, {}).get("detection_union") if src else None) or 1.0
    S = {i: sum(attract(t[2]) ** alpha_h * max(d, floor_h) ** -beta_h
                for d, t in cidx.within(*city["xy"][i], R)) / q_det
         for i in study}
    reach = math.ceil(R / cell_m)
    huff_offs = []
    for dr in range(-reach, reach + 1):
        for dc in range(-reach, reach + 1):
            d = math.hypot(dr, dc) * cell_m
            if d <= R:
                huff_offs.append((dr, dc, d if (dr or dc) else 0.3826 * cell_m))  # mean distance within a square

    def huff_terms(j):
        r, c = divmod(j, cols_)
        out = []
        for dr, dc, d in huff_offs:
            rr, cc = r + dr, c + dc
            if 0 <= rr < rows_ and 0 <= cc < cols_ and rr * cols_ + cc in study_set:
                i = rr * cols_ + cc
                out.append((i, cm.huff_share(d, S[i], 1.0, alpha_h, beta_h, floor_h)))
        return out

    # Captured demand is always computed (it feeds the break-even scenario and the card); it enters the ranking
    # score only when O["demand"] is on, which the forward test does not support (ABLATION.md).
    E, share = {}, {}
    for j in study:
        if not O["huff"]:  # ablation: demand at the site, competition ignored
            E[j], share[j] = D[j], 0.0
            continue
        terms = huff_terms(j)
        num = sum(D[i] * pr for i, pr in terms)
        tot = sum(D[i] for i, _ in terms)
        E[j], share[j] = num, (num / tot if tot else 0.0)
    e_norm = max(E.values()) or 1.0
    # Demand calibration for the break-even model: an average existing competitor serves sum(D) / N_hat, with
    # N_hat the competitor count corrected for detection (Chapman) when a second source is available.
    n_hat = sum(attract(v) for v in broad) / q_det
    demand_calib = n_hat / (sum(D[i] for i in study) or 1.0)

    # Agglomeration: local L-function excess of same-concept venues at r.
    r_k = P["ripley_r_m"]
    area = len(study) * cell_m * cell_m
    gamma = AGGLOMERATION_GAMMA[prof] if O["agg"] else 0.0
    l_exc = {j: cm.local_l_excess(len(pidx.within(*city["xy"][j], r_k)), len(pres), area, r_k) for j in study}
    agg = {j: gamma * max(0.0, math.tanh(l_exc[j] / r_k)) for j in study}
    suit = p if O["suitability"] else dict.fromkeys(study, 1.0)
    opp = {j: suit[j] * (E[j] / e_norm if O["demand"] else 1.0) + agg[j] for j in study}
    gi = cm.getis_ord_gi_star(opp, cm.queen_neighbors(rows_, cols_, study, self_=True))

    # Eligibility: suitability at least the city median (a cheap, empty block is not an opportunity for a concept
    # that does not live there). Monte Carlo for the best eligible cells; rank by the 20th percentile.
    p_med = cm.percentile([p[j] for j in study], 0.5)
    eligible = {j for j in study if p[j] >= p_med} if O["gate"] else set(study)
    ranked = sorted(eligible, key=lambda j: (-opp[j], j))
    pool_cells = list(dict.fromkeys(ranked[:pool_size] + [c for c in extra_cells if c in study_set]))
    mc = _monte_carlo(city, view, pool_cells, huff_terms if O["demand"] else None, beta, chol, mu, sd, wd, agg,
                      e_norm, draws, seed, O["kernel_features"]) if draws else {j: dict.fromkeys(("q05", "q20", "q50", "q95"), opp[j]) for j in pool_cells}
    rank_key = {"q": f"q{round(100 * P['rank_q']):02d}", "q05": "q05", "point": None}[O["rank"]]
    rank_val = {j: (mc[j][rank_key] if rank_key else opp[j]) for j in pool_cells}
    order = sorted((j for j in pool_cells if j in eligible), key=lambda j: (-rank_val[j], j))
    # One score for every study cell (evaluation): ranked pool first, then eligible cells, then the rest.
    top_set = set(order)
    score = {j: (2.0 + rank_val[j]) if j in top_set else (1.0 + opp[j]) if j in eligible else opp[j] - 1.0
             for j in study}

    state = {"concept": concept, "Z": Z, "beta": beta, "p": p, "E": E, "share": share, "opp": opp, "mc": mc,
             "l_exc": l_exc, "agg": agg, "gi": gi, "cidx": cidx, "fo_index": fo_index, "counts": counts,
             "eb": eb, "fitted": fitted, "n_presences": len(pres), "cov": cov, "pinned": pinned, "D": D,
             "score": score, "eligible": eligible, "oof": oof, "platt": platt, "L": L, "rank_key": rank_key,
             "demand_calib": demand_calib, "pres_cells": pres_cells, "presence_set": "narrow" if use_narrow
             else "broad"}
    top_cells = [_card(city, state, j) | {"rank": k} for k, j in enumerate(order[:top], 1)]
    result = {
        "city": {k: city["info"].get(k) for k in ("query", "name", "display_name", "center", "bbox")},
        "concept": {k: concept[k] for k in ("text", "recognized", "profile", "label", "osm")},
        "grid": {"bbox": [round(v, 6) for v in g["bbox"]], "cell_m": round(cell_m), "rows": rows_, "cols": cols_,
                 "cells_total": n, "cells_study": len(study)},
        "params": {k: P[k] for k in ("kernel_lambda_m", "huff_alpha", "huff_beta", "huff_floor_m", "huff_radius_m",
                                     "ripley_r_m", "l2", "draws", "pool", "seed")}
                  | {"agglomeration_gamma": gamma, "options": O,
                     "ranking": ((f"{rank_key} of the Monte Carlo score" if rank_key else "point estimate")
                                 + (" of suitability" if not O["demand"] else " of suitability x captured demand")
                                 + (f", among cells with suitability >= the city median ({p_med:.3f}); the 90% "
                                    "interval comes from the Monte Carlo" if O["gate"] else ""))},
        "model": {"fitted": fitted,
                  "note": None if fitted else (f"Only {n_p} existing venues (< {P['min_presences']}): suitability "
                                               "uses the documented prior weights, not learned ones."),
                  "presence_set": state["presence_set"], "presence_note": presence_note,
                  "n_presences": len(pres), "n_presences_used": n_p, "n_background": len(bg),
                  "n_competitors": len(broad), "estimation": ("MAP, Gaussian prior centred on the stated weights, "
                                                              "footfall generators constrained >= 0"
                                                              if O["weights"] == "map" else O["weights"]),
                  "constrained_at_zero": [FEATURE_NAMES[k] for k in sorted(pinned)],
                  "intercept": round(beta[0], 4),
                  "coefficients": {f: round(b, 4) for f, b in zip(FEATURE_NAMES, beta[1:])},
                  "coefficient_se": {f: round(math.sqrt(max(cov[k + 1][k + 1], 0.0)), 4)
                                     for k, f in enumerate(FEATURE_NAMES)},
                  "demand_weights": {k: round(v, 3) for k, v in wd.items()},
                  "validation": validation},
        "diagnostics": {"moran_residuals": {k: round(v, 4) for k, v in moran.items()},
                        "gi_star": {"hot_cells_z_gt_1.96": sum(1 for z in gi.values() if z > 1.96),
                                    "cold_cells_z_lt_-1.96": sum(1 for z in gi.values() if z < -1.96)}},
        "top": top_cells,
        "cells": [{"id": j, "bounds": cell_bounds(g, j), "opp": round(100 * opp[j], 1),
                   **({"rank": round(100 * rank_val[j], 1)} if j in top_set else {}),
                   **({"eligible": 0} if j not in eligible else {}),
                   **({"c": round(src["completeness"][j], 2)} if src and src["completeness"][j] is not None else {})}
                  for j in study if opp[j] > 0.005],
        "sources": city["sources"],
        "coverage": None if not src else {"city": src["city"], "chapman": src["chapman"],
                                          "kappa_min": KAPPA_MIN},
        "attribution": ATTRIBUTION + " (ODbL), via Overpass API and Nominatim"
                       + ("; \u00a9 Overture Maps Foundation (CDLA-Permissive-2.0) and its sources"
                          if src else ""),
        "limits": [
            "OpenStreetMap completeness varies by city and district; the Gamma-Poisson layer widens intervals "
            "where counts are small but cannot see venues nobody mapped."
            + (" Overture Places is merged as a second source; cells where the two disagree get wider intervals, "
               "and Chapman totals assume independent sources and exact matching (neither holds exactly)."
               if src else ""),
            "Suitability is relative (presence-background), not an absolute probability of success; it learns "
            "where venues of the concept exist today, including any historical bias.",
            "Demand weights per generator type are a stated prior; rents, income, visibility and real foot "
            "traffic are not observed.",
            "A cell is a block to scout, not a vetted address.",
        ],
        "timings": {"model_s": round(time.perf_counter() - t0, 2)},
    }
    return result, state


def _validate(city, Z, L, pres_cells, bg, fitted, fit, P):
    """Spatial block CV (bands along the longer grid axis): pooled out-of-fold AUC, bootstrap CI, top-20% capture.
    Returns (summary, out-of-fold logits per study cell)."""
    study = city["study"]
    folds = cm.block_folds([_band_coord(city, i) for i in study], P["cv_folds"])
    fold_of = dict(zip(study, folds))
    oof = {}
    for f in range(P["cv_folds"]):
        held = [i for i in study if fold_of[i] == f]
        if not fitted:
            oof.update({i: L[i] for i in held})
            continue
        tr_p = [c for c in pres_cells if fold_of[c] != f]
        tr_b = [c for c in bg if fold_of[c] != f]
        if len(tr_p) < 5 or not tr_b:
            continue
        b = fit(tr_p, tr_b)
        oof.update({i: cm.logit(b, Z[i]) for i in held})
    pos = [oof[c] for c in pres_cells if c in oof]
    neg = [oof[c] for c in bg if c in oof]
    if len(pos) < 3 or not neg:
        return {"auc_cv": None, "auc_ci95": None, "top20_capture": None, "folds": P["cv_folds"],
                "note": "too few presences to validate"}, None
    a = cm.auc(pos, neg)
    lo, hi = cm.bootstrap_auc_ci(pos, neg, P["bootstrap"], seed=P["seed"])
    ranked = sorted(oof, key=lambda i: (-oof[i], i))
    top = set(ranked[:math.ceil(0.2 * len(ranked))])
    capture = sum(1 for c in pres_cells if c in top) / len([c for c in pres_cells if c in oof])
    return {"auc_cv": round(a, 3), "auc_ci95": [round(lo, 3), round(hi, 3)], "top20_capture": round(capture, 3),
            "folds": P["cv_folds"], "scheme": "spatial bands along the longer axis" if fitted
            else "prior weights scored on all presences (nothing fitted)"}, oof


def _monte_carlo(city, view, pool_cells, huff_terms, beta, chol, mu, sd, wd, agg, e_norm, draws, seed,
                 kernel_features=True):
    """Per draw: every count from its Gamma posterior and the coefficients from N(beta, cov); recompute
    suitability (and, when huff_terms is given, demand and Huff capture) for the pool."""
    g = city["grid"]
    rows_, cols_ = g["rows"], g["cols"]
    offs, study_set = city["offsets"], city["study_set"]
    terms = {j: huff_terms(j) for j in pool_cells} if huff_terms else {j: [] for j in pool_cells}
    hcells = {i for j in pool_cells for i, _ in terms[j]}

    def window(i):
        r, c = divmod(i, cols_)
        out = []
        for dr, dc, k in offs:
            rr, cc = r + dr, c + dc
            if 0 <= rr < rows_ and 0 <= cc < cols_ and rr * cols_ + cc in study_set:
                out.append((rr * cols_ + cc, k))
        return out

    win = {i: window(i) for i in hcells | set(pool_cells)}
    kcells = sorted({m for i in win for m, _ in win[i]})
    shapes = {}
    for c in COUNT_CATS:
        a, b, q = view["eb"][c]
        shapes[c] = [cm.gamma_posterior(a, b, view["counts"][c][m], q, _kappa(city, c, m)) for m in kcells]
        shapes[c] = [(sh, 1.0 / rt) for sh, rt in shapes[c]]
    pos = {m: k for k, m in enumerate(kcells)}
    rng = random.Random(seed)
    samples = {j: [] for j in pool_cells}
    for _ in range(draws):
        lam = {c: [rng.gammavariate(a, s) if a > 0 else 0.0 for a, s in shapes[c]] for c in COUNT_CATS}
        b_draw = cm.mvn_draw(beta, chol, rng)
        for k in NONNEG:  # the sign constraint also truncates the coefficient draws
            b_draw[k + 1] = max(0.0, b_draw[k + 1])
        Wd = [sum(wd[c] * lam[c][k] for c in wd) for k in range(len(kcells))]
        D = {i: sum(Wd[pos[m]] * k for m, k in win[i]) for i in hcells}
        for j in pool_cells:
            if kernel_features:
                dj = {c: sum(lam[c][pos[m]] * k for m, k in win[j]) for c in COUNT_CATS}
            else:
                dj = {c: lam[c][pos[j]] for c in COUNT_CATS}
            x = [math.log1p(sum(mm * dj[c] for c, mm in MODEL_FEATURES[f])) for f in FEATURE_NAMES]
            z = [(v - m) / s for v, m, s in zip(x, mu, sd)]
            e = sum(D[i] * pr for i, pr in terms[j]) / e_norm if huff_terms else 1.0
            samples[j].append(cm.sigmoid(cm.logit(b_draw, z)) * e + agg[j])
    return {j: {"q05": cm.percentile(v, 0.05), "q20": cm.percentile(v, 0.2), "q50": cm.percentile(v, 0.5),
                "q95": cm.percentile(v, 0.95)} for j, v in samples.items()}


def _count_within(city, state, feature, x, y, r):
    """Raw mapped count for a model feature within r metres (hectares for land use)."""
    if feature in ("residential", "commercial"):
        return sum(state["counts"][feature][i] for i in city["study"]
                   if math.hypot(city["xy"][i][0] - x, city["xy"][i][1] - y) <= r)
    if feature == "food_other":
        return len(state["fo_index"].within(x, y, r))
    return sum(len(city["index"][c].within(x, y, r)) for c, _ in MODEL_FEATURES[feature])


def _reason(city, state, f, contrib, coef, x, y, r):
    """Plain-language line for one logit term; says when a term helps because the signal is below average."""
    en, fr = LABELS[f]
    if f == "transit":
        st = len(city["index"]["station"].within(x, y, r))
        bus = len(city["index"]["bus_stop"].within(x, y, r))
        what_en = f"{st} rail, metro or tram stations and {bus} bus stops within {r:.0f} m"
        what_fr = f"{st} gares, stations de métro ou de tram et {bus} arrêts de bus à moins de {r:.0f} m"
    else:
        v = _count_within(city, state, f, x, y, r)
        num = f"{v:.0f}" if f not in ("residential", "commercial") else f"{v:.1f}"
        what_en, what_fr = f"{num} {en} within {r:.0f} m", f"{num} {fr} à moins de {r:.0f} m"
    above = (contrib > 0) == (coef > 0)
    tail_en = "" if above else " (fewer than the city average: less competition)" if contrib > 0 \
        else " (below the city average)"
    tail_fr = "" if above else " (moins que la moyenne de la ville : moins de concurrence)" if contrib > 0 \
        else " (sous la moyenne de la ville)"
    if contrib < 0 and above:
        tail_en, tail_fr = " (this concept does better away from them)", " (ce concept réussit mieux à l'écart)"
    return f"{contrib:+.2f}: {what_en}{tail_en}", f"{contrib:+.2f} : {what_fr}{tail_fr}"


def _card(city, state, j, x=None, y=None):
    """Broker card for a cell (or a point inside it): interval, exact logit decomposition, competitors."""
    g = city["grid"]
    P = city["params"]
    cx, cy = city["xy"][j]
    x, y = (cx, cy) if x is None else (x, y)
    coef = dict(zip(FEATURE_NAMES, state["beta"][1:]))
    se = {f: math.sqrt(max(state["cov"][k + 1][k + 1], 0.0)) for k, f in enumerate(FEATURE_NAMES)}
    # A reason is shown only when its coefficient's 90% interval excludes 0 (learned models only).
    solid = {f for f in FEATURE_NAMES if state["fitted"] and abs(coef[f]) - cm.Z90 * se[f] > 0}
    contrib = {f: coef[f] * z for f, z in zip(FEATURE_NAMES, state["Z"][j])}
    pos_ = sorted((f for f in contrib if contrib[f] >= 0.15 and f in solid), key=lambda f: -contrib[f])[:3]
    neg_ = sorted((f for f in contrib if contrib[f] <= -0.15 and f in solid), key=lambda f: contrib[f])[:1]
    hidden = sum(1 for f in contrib if abs(contrib[f]) >= 0.15 and f not in solid)
    r = P["explain_r_m"]
    reasons = [_reason(city, state, f, contrib[f], coef[f], cx, cy, r) for f in pos_]
    risks = [_reason(city, state, f, contrib[f], coef[f], cx, cy, r) for f in neg_]
    if not state["fitted"]:
        risks.append((f"Unproven: only {state['n_presences']} {state['concept']['label']} mapped in the city, so "
                      "suitability uses prior weights with wide uncertainty",
                      f"Non prouvé : seulement {state['n_presences']} établissements de ce type cartographiés dans la "
                      "ville, l'adéquation repose sur des poids a priori très incertains"))
    mcq = state["mc"].get(j) or {}
    lcb, q20, med, ucb = (mcq.get(k) for k in ("q05", "q20", "q50", "q95"))
    lat, lon = cell_center(g, j)
    near = sorted(state["cidx"].within(x, y, 5000), key=lambda t: t[0])[:3]
    counts_ci = {}
    for c in ("office", "bus_stop", "university", "food_other"):
        a, b, q = state["eb"][c]
        shape, rate = cm.gamma_posterior(a, b, state["counts"][c][j], q, _kappa(city, c, j))
        lo, hi = cm.gamma_interval(shape, rate)
        counts_ci[c] = {"observed": state["counts"][c][j], "posterior_mean": round(shape / rate, 2),
                        "ci90": [round(lo, 2), round(hi, 2)]}
    comp = city["coverage"]["completeness"][j] if city["coverage"] else None
    l_exc = state["l_exc"][j]
    return {
        "id": j, "centroid": {"lat": round(lat, 6), "lon": round(lon, 6)}, "bounds": cell_bounds(g, j),
        "neighborhood": neighborhood(city, j),
        "opportunity": {"rank_score": _r(mcq.get(state["rank_key"]) if state["rank_key"] else state["opp"][j]),
                        "q20": _r(q20), "lcb": _r(lcb), "median": _r(med), "ucb": _r(ucb),
                        "point": _r(state["opp"][j])},
        "confidence_width": _r(ucb - lcb) if lcb is not None else None,
        "eligible": j in state["eligible"],
        "suitability": round(state["p"][j], 3),
        "calibrated_probability": (round(cm.sigmoid(state["platt"][0] * state["L"][j] + state["platt"][1]), 3)
                                   if state["platt"] else None),
        "relative_demand": round(state["E"][j] * state["demand_calib"], 3),
        "reasons_hidden_ci_crosses_0": hidden,
        "logit": round(cm.logit(state["beta"], state["Z"][j]), 3),
        "logit_contributions": {f: round(v, 3) for f, v in sorted(contrib.items(), key=lambda t: -abs(t[1]))},
        "huff_share": round(state["share"][j], 3),
        "captured_demand_index": round(state["E"][j] / max(state["E"].values()), 3),
        "agglomeration": {"l_excess_m": round(l_exc, 1), "term": _r(state["agg"][j])},
        "gi_star_z": round(state["gi"][j], 2),
        "reasons_en": [e for e, _ in reasons], "reasons_fr": [f for _, f in reasons],
        "watch_out_en": [e for e, _ in risks], "watch_out_fr": [f for _, f in risks],
        "market_en": (f"Would capture about {100 * state['share'][j]:.0f}% of the walk-in demand within "
                      f"{P['huff_radius_m'] / 1000:.0f} km against {state['n_presences']} existing "
                      f"{state['concept']['label']} (Huff model)"),
        "market_fr": (f"Capterait environ {100 * state['share'][j]:.0f} % de la demande à pied dans un rayon de "
                      f"{P['huff_radius_m'] / 1000:.0f} km face aux {state['n_presences']} concurrents existants "
                      f"(modèle de Huff)"),
        "nearest_competitors": [{"name": t[1][2]["tags"].get("name") or "(unnamed)", "m": round(t[0])}
                                for t in near],
        "counts_ci90": counts_ci,
        "data_completeness": None if comp is None else round(comp, 2),
        "source_gaps": cell_gaps(city, j),
    }


def _r(v):
    return None if v is None else round(100 * v, 1)


# ----------------------------------------------------------------------------------------------------------------
# Unit -> concepts, and the backtest report

def find_place(city, name):
    """A named OSM suburb/neighbourhood from the pack (accent-insensitive), 'lat,lon' or 'label@lat,lon'."""
    m = re.match(r"^\s*(?:(.+?)\s*@)?\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*$", name)
    if m:
        return {"name": m.group(1) or name, "lat": float(m.group(2)), "lon": float(m.group(3))}
    key = _norm(name)
    hits = [p for p in city["places"] if _has_word(p["norm"], key) or key in p["norm"]]
    if not hits:
        return None
    rank = {"suburb": 0, "quarter": 1, "neighbourhood": 2}
    return min(hits, key=lambda p: (rank[p["kind"]], p["name"]))


def _detection_key(concept):
    am = set(concept["osm"]["amenity"])
    return "cafe" if am <= {"cafe", "ice_cream"} else "bar" if am <= BAR_AMENITIES else "restaurant"


def survival_z(city, calib, x, y):
    """Standardised location covariates of the pooled Cox model, from the pack: same-group venues within 150 m and
    points of interest within 200 m (log1p counts, standardised over the official venues' locations)."""
    group = map_concept(calib["concept_for_demand"])
    same = sum(1 for v in city["food"]
               if abs(v["x"] - x) <= 150 and abs(v["y"] - y) <= 150 and math.hypot(v["x"] - x, v["y"] - y) <= 150
               and competitor_weight(v["tags"], group) >= 0.5)
    poi = sum(len(city["index"][c].within(x, y, 200)) for c in POINT_CATS)
    out = {}
    for name, raw in (("same_concept_150m", same), ("poi_200m", poi)):
        mu, sd = calib["pack_z"][name]
        out[name] = (math.log1p(raw) - mu) / sd
    return out


def survival_card(city, city_key, concept, profile, relative_demand, rents, x, y, asking=None, surface=None):
    """Official-calibrated city: calibrated survival card with the rent ceiling. Otherwise: conditional break-even
    card (with the conditional rent ceiling) and the reason."""
    import cityscan_breakeven as be

    calib = be.calibration(city_key, profile)
    if calib:
        z = survival_z(city, calib, x, y)
        card = be.calibrated_card(relative_demand, list(rents), calib, z["same_concept_150m"], z["poi_200m"])
        card["rent_ceiling"] = be.rent_ceiling_card(relative_demand, calib, z["same_concept_150m"], z["poi_200m"],
                                                    asking=rents[0] if asking is None else asking,
                                                    surface_m2=surface)
        return card
    d = be.defaults(city_key, profile)
    if d is None:
        return None
    det = ((city["coverage"] or {}).get("chapman", {}).get(_detection_key(concept)) or {}).get("detection_osm")
    en, fr = be.gate_text(city_key, det)
    return be.conditional_card(list(rents), d["inputs"], gate_en=en, gate_fr=fr) | {
        "currency": d["currency"], "inputs": d["inputs"]}


def landlord_view(city, city_key, x, y, rel_by_group, catalog=None, surface=None):
    """For one unit: the rent ceiling of every tenant type (survival concept group), ranked. Each group's target
    is its own official city 3-year survival, and an 80% common target is also given. Catalog concepts are
    listed under the group whose calibration they use."""
    import cityscan_breakeven as be

    subs = {}
    for c in dict.fromkeys(list(CATALOG) + list(catalog or [])):
        g = be.SURVIVAL_GROUP.get(map_concept(c)["profile"])
        if g:
            subs.setdefault(g, []).append(c)
    rows = []
    for g in be.GROUP_PROFILE:
        calib = be.calibration(city_key, be.GROUP_PROFILE[g])
        if not calib:
            continue
        z = survival_z(city, calib, x, y)
        rel = rel_by_group.get(g, 1.0)
        rc = be.rent_ceiling_card(rel, calib, z["same_concept_150m"], z["poi_200m"], surface_m2=surface,
                                  curve_points=21)
        rows.append({"group": g, "label_en": be.GROUP_LABEL[g][0], "label_fr": be.GROUP_LABEL[g][1],
                     "sub_concepts": subs.get(g, []), "relative_demand": round(rel, 3),
                     "kappa": calib.get("kappa"), "location_hr": rc["location_hr"], "city_s3": rc["city_s3"],
                     "rent_max_city_target": rc["ceilings"]["city"], "rent_max_80": rc["ceilings"]["80"],
                     "currency": rc["currency"], "curve": rc["curve"]})
    rows.sort(key=lambda r: -r["rent_max_city_target"]["rent_max"])
    return rows


def conditional_landlord(city_key, catalog=None):
    """Data-thin city: what each tenant type could pay at its stated default covers and ticket (conditional)."""
    import cityscan_breakeven as be

    rows = []
    for prof in ("cafe", "fast_casual", "restaurant", "bar", "healthy", "fine_dining"):
        d = be.defaults(city_key, prof)
        if d is None or (city_key, prof) not in be.DEFAULTS:
            continue
        m = {k: v["mode"] for k, v in d["inputs"].items() if v["dist"] == "tri"}
        rows.append({"profile": prof, "covers_day": m["covers_avg"], "ticket": m["ticket"], "currency": d["currency"],
                     "max_rent_break_even": round(be.max_rent_break_even(m["covers_avg"], m["ticket"], m["days"],
                                                                         m["cogs"], m["other_var"], m["staff"],
                                                                         m["other_fixed"]))})
    rows.sort(key=lambda r: -r["max_rent_break_even"])
    return rows


def unit_concepts(city, lat, lon, catalog=None, draws=None, rents=(15000, 30000)):
    """For one point: every catalog concept, comparable across concepts (see unit_concepts_many)."""
    return unit_concepts_many(city, [(lat, lon)], catalog, draws, rents)[0]


def unit_concepts_many(city, points, catalog=None, draws=None, rents=(15000, 30000), asking=None, surface=None):
    """For each point, rank the catalog concepts on scales that are comparable across concepts: the point's
    percentile within each concept's own city distribution of opportunity, then the Platt-calibrated probability
    (held-out) that a cell like this hosts the concept. Concepts with fewer than min_ranked presences are listed as
    "insufficient data", not ranked. Each concept also gets a break-even card at the given monthly rents: survival
    odds calibrated on official data where available (Paris), a conditional break-even card otherwise."""
    P = city["params"]
    cells, xys = [], []
    for lat, lon in points:
        j = cell_of(city["grid"], lat, lon)
        if j is None or j not in city["study_set"]:
            raise ValueError("point outside the scanned area")
        cells.append(j)
        xys.append(city["proj"].xy(lat, lon))
    city_key = _norm(city["info"].get("name") or "").split(",")[0].strip()
    out = [{"point": {"lat": la, "lon": lo}, "cell": j, "neighborhood": neighborhood(city, j), "concepts": []}
           for (la, lo), j in zip(points, cells)]
    for c in catalog or CATALOG:
        res, st = analyze(city, c, top=0, pool=0, extra_cells=cells, draws=draws, cv=True)
        vals = sorted(st["opp"][i] for i in city["study"])
        for u, j, (x, y) in zip(out, cells, xys):
            card = _card(city, st, j, x, y)
            pct = 100 * sum(1 for v in vals if v <= st["opp"][j]) / len(vals)
            cal = cm.sigmoid(st["platt"][0] * st["L"][j] + st["platt"][1]) if st["platt"] else None
            n_used = res["model"]["n_presences_used"]
            enough = st["fitted"] and n_used >= P["min_ranked"]
            u["concepts"].append({
                "concept": c, "profile": res["concept"]["profile"], "model_fitted": st["fitted"],
                "n_presences": n_used, "presence_set": st["presence_set"],
                "tier": "ranked" if enough else "insufficient data",
                "city_percentile": round(pct, 1),
                "calibrated_probability": None if cal is None else round(cal, 3),
                "base_rate": (res["model"]["validation"] or {}).get("platt", {}).get("base_rate"),
                "suitability": card["suitability"], "huff_share": card["huff_share"],
                "relative_demand": card["relative_demand"], "opportunity": card["opportunity"],
                "confidence_width": card["confidence_width"], "eligible": card["eligible"],
                "reasons_en": card["reasons_en"], "reasons_fr": card["reasons_fr"],
                "watch_out_en": card["watch_out_en"], "watch_out_fr": card["watch_out_fr"],
                "reasons_hidden_ci_crosses_0": card["reasons_hidden_ci_crosses_0"],
                "presence_note": res["model"]["presence_note"],
                "nearest_competitors": card["nearest_competitors"],
                "breakeven": survival_card(city, city_key, res["concept"], res["concept"]["profile"],
                                           card["relative_demand"], rents, x, y, asking, surface)})
    import cityscan_breakeven as be

    for u, (x, y) in zip(out, xys):
        rel_by_group = {}
        for c in u["concepts"]:
            rel_by_group.setdefault(be.SURVIVAL_GROUP.get(c["profile"]), c["relative_demand"])
        if be.calibration(city_key, "restaurant"):
            u["landlord"] = {"mode": "calibrated", "rows": landlord_view(city, city_key, x, y, rel_by_group, catalog,
                                                                         surface)}
        else:
            det = ((city["coverage"] or {}).get("chapman", {}).get("restaurant") or {}).get("detection_osm")
            en, fr = be.gate_text(city_key, det)
            u["landlord"] = {"mode": "conditional", "rows": conditional_landlord(city_key, catalog),
                             "gate_en": en, "gate_fr": fr}
    for u in out:
        u["concepts"].sort(key=lambda c: (c["tier"] != "ranked", -c["city_percentile"],
                                          -(c["calibrated_probability"] or 0)))
    return out


def backtest_row(result):
    m, v = result["model"], result["model"]["validation"] or {}
    mo = result["diagnostics"]["moran_residuals"]
    return {"city": result["city"]["name"], "concept": result["concept"]["text"], "n_presences": m["n_presences"],
            "fitted": m["fitted"], "auc_cv": v.get("auc_cv"), "auc_ci95": v.get("auc_ci95"),
            "moran_I": mo["I"], "moran_z": mo["z"], "top20_capture": v.get("top20_capture")}


# ----------------------------------------------------------------------------------------------------------------
# Network (Nominatim + Overpass), with an in-memory TTL cache

_cache = {}
_fetch_lock = threading.Lock()


def _cached(key, fn, ttl=CACHE_TTL_S):
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    value = fn()
    _cache[key] = (time.time(), value)
    return value


def _get_json(url, data=None, timeout=30):
    req = urllib.request.Request(url, data=data, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def city_bbox(city):
    """Nominatim lookup: (display name, center, bbox). At most one request per second."""
    def fetch():
        wait = 1.1 - (time.time() - _last_call[0])
        if wait > 0:
            time.sleep(wait)
        _last_call[0] = time.time()
        url = "https://nominatim.openstreetmap.org/search?" + urllib.parse.urlencode(
            {"q": city, "format": "jsonv2", "limit": 1, "accept-language": "en"})
        return parse_nominatim(_get_json(url, timeout=15), city)
    return _cached(("nominatim", city.strip().lower()), fetch, ttl=7 * 86400)


def parse_nominatim(hits, city):
    if not hits:
        raise LookupError(f"city not found: {city}")
    h = hits[0]
    s, n, w, e = (float(v) for v in h["boundingbox"])
    return {"query": city, "name": h.get("name") or city, "display_name": h.get("display_name"),
            "center": [float(h["lat"]), float(h["lon"])], "bbox": [s, w, n, e]}


def query_bbox(info, params=None):
    P = {**PARAMS, **(params or {})}
    return clip_bbox(info["bbox"], info["center"], P["max_km"])


def overpass_query(bbox, timeout=120):
    s, w, n, e = (round(v, 5) for v in bbox)
    return f"""[out:json][timeout:{timeout}][maxsize:268435456][bbox:{s},{w},{n},{e}];
(
  nwr["amenity"~"^(restaurant|cafe|fast_food|food_court|ice_cream|bar|pub|biergarten|nightclub|university|college|school|cinema|theatre|arts_centre|concert_hall|coworking_space|marketplace)$"];
  nwr["office"];
  nwr["railway"~"^(station|halt|tram_stop)$"];
  nwr["public_transport"="station"];
  node["highway"="bus_stop"];
  nwr["shop"~"^(mall|department_store)$"];
  nwr["tourism"~"^(hotel|hostel|guest_house|attraction|museum|gallery|viewpoint|theme_park|zoo)$"];
  nwr["leisure"~"^(fitness_centre|sports_centre)$"];
  nwr["place"~"^(suburb|quarter|neighbourhood)$"];
)->.all;
node.all;
out qt;
(way.all; relation.all;);
out tags center qt;
way["landuse"~"^(residential|commercial|retail)$"];
out tags bb qt;
"""


def overpass(bbox, timeout=120):
    """One bounded Overpass query for the bbox; tries the next mirror on 429/5xx. Serialised per process."""
    body = urllib.parse.urlencode({"data": overpass_query(bbox, timeout)}).encode()
    last = None
    for attempt, url in enumerate(OVERPASS_URLS):
        if attempt:
            time.sleep(5)
        try:
            data = _get_json(url, data=body, timeout=timeout + 30)
            if "remark" in data and not data.get("elements"):
                raise RuntimeError(data["remark"])
            return data.get("elements", [])
        except Exception as e:  # noqa: BLE001  try the next mirror, then give up
            last = e
    raise RuntimeError(f"Overpass failed: {last}")


def fetch_pack(city):
    """Network side: Nominatim + one Overpass query -> city pack. Cached per city for CACHE_TTL_S."""
    with _fetch_lock:
        info = city_bbox(city)
        bbox = query_bbox(info)
        return _cached(("pack", tuple(round(v, 4) for v in bbox)), lambda: make_pack(info, overpass(bbox)))


def _load(path):
    with open(path) as f:
        return json.load(f)


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="City Scan (OpenStreetMap, standard library only).")
    sub = ap.add_subparsers(dest="cmd", required=True)
    pk = sub.add_parser("pack", help="build an offline city pack from saved Nominatim/Overpass responses")
    pk.add_argument("city")
    pk.add_argument("--place", required=True)
    pk.add_argument("--osm")
    pk.add_argument("--query-out", help="write the Overpass query for the city's bbox and exit")
    pk.add_argument("--out")
    sc = sub.add_parser("scan", help="rank cells for concepts; optional backtest and unit -> concepts cards")
    sc.add_argument("--pack", help="offline city pack (default: fetch live)")
    sc.add_argument("--city")
    sc.add_argument("--concept", action="append", default=[])
    sc.add_argument("--unit", action="append", default=[], help="OSM neighbourhood name or 'lat,lon'")
    sc.add_argument("--backtest", action="store_true")
    sc.add_argument("--top", type=int, default=10)
    sc.add_argument("--draws", type=int)
    sc.add_argument("--rent", type=float, action="append", help="monthly rent(s) for the break-even scenario")
    sc.add_argument("--catalog", action="append", help="concepts for the unit cards (default: the full catalog)")
    sc.add_argument("--asking-rent", type=float, help="the landlord's asking rent (rent-ceiling marker)")
    sc.add_argument("--surface", type=float, help="unit surface in m2 (rent ceiling per m2)")
    sc.add_argument("--osm-only", action="store_true", help="ignore the pack's Overture layer")
    sc.add_argument("--compare-osm-only", action="store_true",
                    help="also rank with OSM alone and report how the top cells change")
    sc.add_argument("--out")
    a = ap.parse_args()

    if a.cmd == "pack":
        info = parse_nominatim(_load(a.place), a.city)
        if a.query_out:
            with open(a.query_out, "w") as f:
                f.write(overpass_query(query_bbox(info)))
            sys.exit(0)
        data = _load(a.osm)
        if data.get("remark") and not data.get("elements"):
            sys.exit(f"Overpass error: {data['remark']}")
        pack = make_pack(info, data["elements"])
        with open(a.out, "w") as f:
            json.dump(pack, f, ensure_ascii=False)
        print(f"pack: {len(pack['elements'])} elements -> {a.out}", file=sys.stderr)
        sys.exit(0)

    t0 = time.perf_counter()
    pack = _load(a.pack) if a.pack else fetch_pack(a.city)
    t_load = time.perf_counter() - t0
    city = build_city(pack["info"], pack["elements"], overture=None if a.osm_only else pack.get("overture"))
    t_build = time.perf_counter() - t0 - t_load
    print(f"{pack['info']['name']}: {len(pack['elements'])} elements, sources {city['sources']}, grid "
          f"{city['grid']['rows']}x{city['grid']['cols']} @ {city['grid']['cell_m']:.0f} m, {len(city['study'])} "
          f"study cells (load {t_load:.1f}s, build {t_build:.1f}s)", file=sys.stderr)
    if city["coverage"]:
        print(f"coverage: {city['coverage']['city']}", file=sys.stderr)
        for k in ("restaurant", "cafe", "food"):
            print(f"  chapman {k}: {city['coverage']['chapman'].get(k)}", file=sys.stderr)
    osm_city = build_city(pack["info"], pack["elements"]) if a.compare_osm_only and city["coverage"] else None
    doc = {"scans": [], "backtest": [], "units": [], "ranking_change": [],
           "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    for c in a.concept:
        res, _ = analyze(city, c, top=a.top, cv=a.backtest, draws=a.draws)
        doc["scans"].append(res)
        if a.backtest:
            doc["backtest"].append(backtest_row(res))
        if osm_city:
            ref, _ = analyze(osm_city, c, top=a.top, cv=a.backtest, draws=a.draws)
            if a.backtest:
                doc["backtest"].append(backtest_row(ref) | {"sources": "OSM only"})
            both, solo = [t["id"] for t in res["top"]], [t["id"] for t in ref["top"]]
            change = {"concept": c, "top_overlap": len(set(both) & set(solo)), "of": len(both),
                      "n_presences_osm_only": ref["model"]["n_presences"],
                      "n_presences_union": res["model"]["n_presences"],
                      "osm_only_top3": [(t["neighborhood"], t["opportunity"]["rank_score"]) for t in ref["top"][:3]],
                      "union_top3": [(t["neighborhood"], t["opportunity"]["rank_score"]) for t in res["top"][:3]],
                      "osm_only_rank_of_union_top3": [solo.index(i) + 1 if i in solo else None for i in both[:3]]}
            doc["ranking_change"].append(change)
            print(f"ranking change: {change}", file=sys.stderr)
        m = res["model"]
        print(f"\n== {c}: {m['n_presences']} presences, fitted={m['fitted']}, validation={m['validation']}, "
              f"moran={res['diagnostics']['moran_residuals']}, {res['timings']}", file=sys.stderr)
        for t in res["top"][:3]:
            print(f"#{t['rank']} {t['neighborhood']} score {t['opportunity']['rank_score']} 90% "
                  f"[{t['opportunity']['lcb']}, {t['opportunity']['ucb']}] p={t['suitability']} "
                  f"rel.demand={t['relative_demand']} hidden={t['reasons_hidden_ci_crosses_0']} "
                  f"huff={t['huff_share']} L-r={t['agglomeration']['l_excess_m']} Gi*={t['gi_star_z']}",
                  file=sys.stderr)
            for r in t["reasons_en"]:
                print("    -", r, file=sys.stderr)
    places = []
    for u in a.unit:
        pl = find_place(city, u)
        if pl:
            places.append((u, pl))
        else:
            print(f"unit {u}: not found in pack", file=sys.stderr)
    if places:
        t1 = time.perf_counter()
        cards = unit_concepts_many(city, [(pl["lat"], pl["lon"]) for _, pl in places], catalog=a.catalog,
                                   draws=a.draws, rents=a.rent or [15000, 30000], asking=a.asking_rent,
                                   surface=a.surface)
        dt = round(time.perf_counter() - t1, 1)
        for (u, pl), card in zip(places, cards):
            doc["units"].append(card | {"query": u, "place": pl["name"], "timing_s_all_units": dt})
            print(f"\n== unit {u} ({pl['name']} {pl['lat']:.5f},{pl['lon']:.5f}), all units in {dt}s",
                  file=sys.stderr)
            for c in card["concepts"][:6]:
                be_ = c["breakeven"] or {}
                print(f"  {c['concept']} [{c['tier']}, n={c['n_presences']}]: pct={c['city_percentile']} "
                      f"P(cal)={c['calibrated_probability']} rank={c['opportunity']['rank_score']} "
                      f"rel.demand={c['relative_demand']} {be_.get('mode')}", file=sys.stderr)
                if be_.get("mode") == "calibrated":
                    print(f"    {be_['baseline_en']}; HR={be_['location_hr']} z={be_['z']}", file=sys.stderr)
                for r in be_.get("by_rent", []):
                    print(f"    rent {r['rent']:.0f}: " + ", ".join(f"{k}={v}" for k, v in r.items() if k != "rent"),
                          file=sys.stderr)
                if be_.get("mode") == "conditional":
                    print(f"    max rent at {be_['covers_day']} covers x {be_['ticket']}: "
                          f"{be_['max_rent_break_even']}; {be_['gate_en']}", file=sys.stderr)
                rc = be_.get("rent_ceiling")
                if rc:
                    print("    rent ceiling: " + ", ".join(f"{k}: {v['rent_max']:.0f} {v['ci90']}"
                                                         for k, v in rc["ceilings"].items())
                          + f" | {rc.get('asking', {}).get('text_en')}", file=sys.stderr)
            ll = card.get("landlord") or {}
            for r in ll.get("rows", []):
                print(f"  landlord {ll['mode']}: " + json.dumps({k: v for k, v in r.items() if k != 'curve'},
                                                               ensure_ascii=False), file=sys.stderr)
    if a.out:
        with open(a.out, "w") as f:
            json.dump(doc, f, ensure_ascii=False)
    else:
        json.dump(doc, sys.stdout, ensure_ascii=False)
