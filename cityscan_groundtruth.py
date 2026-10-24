"""Offline: how complete is a city pack's food layer against an official register of active food businesses?

    python3 cityscan_groundtruth.py PACK.json OFFICIAL.csv [--radius 50] [--shift 300]

OFFICIAL.csv has `lat,lon` rows (active establishments; for example Chicago Business Licenses or INSEE SIRENE).
Recall = share of official points with an OSM or Overture food place within `radius` m. Dense cities match by
chance, so the same is measured after moving every official point `shift` m in a random direction (placebo), and the
score is the chance-corrected recall kappa = (recall - placebo) / (1 - placebo). Prints one JSON line for
cityscan_publish.py --ground-truth.
"""
import argparse
import csv
import json
import math
import random

import cityscan as cs

FOOD = {"restaurant", "cafe", "fast_food", "bar", "pub", "biergarten", "food_court", "ice_cream", "nightclub"}
CELL_DEG = 0.001


def food_points(pack):
    out = []
    for e in pack["elements"]:
        t = e.get("tags") or {}
        c = e if "lat" in e else e.get("center") or {}
        if t.get("amenity") in FOOD and c.get("lat") is not None:
            out.append((c["lat"], c["lon"]))
    for lat, lon, _name, cat, _conf in (pack.get("overture") or {}).get("places", []):
        if (cs.overture_tags(cat) or {}).get("amenity") in FOOD:
            out.append((lat, lon))
    return out


def index(points):
    g = {}
    for la, lo in points:
        g.setdefault((int(la / CELL_DEG), int(lo / CELL_DEG)), []).append((la, lo))
    return g


def near(g, la, lo, m):
    kx = 111_320 * math.cos(math.radians(la))
    r = math.ceil(m / (CELL_DEG * kx))
    i, j = int(la / CELL_DEG), int(lo / CELL_DEG)
    return any(math.hypot((a - la) * 111_320, (b - lo) * kx) <= m
               for di in range(-r, r + 1) for dj in range(-r, r + 1) for a, b in g.get((i + di, j + dj), []))


def shifted(la, lo, d, rng):
    a = rng.uniform(0, 2 * math.pi)
    return la + d * math.sin(a) / 111_320, lo + d * math.cos(a) / (111_320 * math.cos(math.radians(la)))


def ground_truth(pack, official, radius=50.0, shift=300.0, seed=1):
    s, w, n, e = cs.query_bbox(pack["info"])
    pts = [(la, lo) for la, lo in official if s <= la <= n and w <= lo <= e]
    if not pts:
        raise ValueError("no official point inside the pack's area")
    g, rng = index(food_points(pack)), random.Random(seed)
    recall = sum(near(g, la, lo, radius) for la, lo in pts) / len(pts)
    placebo = sum(near(g, *shifted(la, lo, shift, rng), radius) for la, lo in pts) / len(pts)
    kappa = (recall - placebo) / (1 - placebo) if placebo < 1 else 0.0
    return {"n_official": len(pts), "recall": round(recall, 3), "placebo": round(placebo, 3),
            "kappa": round(kappa, 3), "radius_m": radius, "shift_m": shift}


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("pack")
    ap.add_argument("official")
    ap.add_argument("--radius", type=float, default=50.0)
    ap.add_argument("--shift", type=float, default=300.0)
    a = ap.parse_args()
    with open(a.pack) as f:
        pack = json.load(f)
    with open(a.official, newline="") as f:
        official = [(float(r["lat"]), float(r["lon"])) for r in csv.DictReader(f)]
    print(json.dumps(ground_truth(pack, official, a.radius, a.shift)))
