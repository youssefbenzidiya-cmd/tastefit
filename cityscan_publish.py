"""Offline: turn a raw city pack into the small published file the app serves (packs/<slug>.json.gz).

    python3 cityscan_publish.py PACK.json --slug paris [--concept "specialty coffee" ...] [--top 10]

The app never runs the model. This script ranks cells for each concept and, for calibrated cities, computes the
landlord view (rent ceiling per tenant group) for every study cell, so /scan/unit is a lookup.
A city whose food-place data confidence is below GATE_MIN is published as data-thin: no rankings, no rent numbers.
"""
import argparse
import gzip
import json
import sys
import time
from datetime import datetime, timezone

import cityscan as cs
import cityscan_breakeven as be

# Food-place data confidence (OSM vs Overture capture-recapture). Set from two cities, Paris 0.556 and Casablanca
# 0.181, so it is provisional.
GATE_MIN = 0.40
GT_MIN = 0.70
DEFAULT_CONCEPTS = ["specialty coffee", "burger", "pizza", "French bistro", "sushi", "brunch"]
TOP_KEYS = ("id", "centroid", "bounds", "neighborhood", "opportunity", "confidence_width", "suitability",
            "reasons_en", "reasons_fr", "watch_out_en", "watch_out_fr", "market_en", "market_fr",
            "nearest_competitors", "data_completeness")


def quality(city, ground_truth=None, gt_source=None):
    """Gate on an official register when one exists (chance-corrected recall, cityscan_groundtruth.py); otherwise
    on agreement between OSM and Overture. Agreement under-rates cities where one source is weak but the union is
    complete (Chicago: agreement 0.333, kappa against business licences 0.884), so it is only the fallback."""
    cov = (city["coverage"] or {}).get("city") or {}
    conf = cov.get("data_confidence_food")
    if ground_truth:
        k = ground_truth["kappa"]
        passed = k >= GT_MIN
        reason = None if passed else (
            "Map data for this city misses too many officially registered food businesses (chance-corrected "
            "recall {:.2f}) to rank areas or price rent.".format(k))
        basis = {"basis": "official register", "source": gt_source, "ground_truth": ground_truth,
                 "threshold": GT_MIN,
                 "threshold_note": "chance-corrected recall vs official register; Paris 0.841, Chicago 0.884"}
    else:
        passed = conf is not None and conf >= GATE_MIN
        reason = None if passed else (
            "Map data for this city is too incomplete (about {:.0%} of food places confirmed by two sources) "
            "to rank areas or price rent.".format(conf or 0))
        basis = {"basis": "two-source agreement", "threshold": GATE_MIN,
                 "threshold_note": "provisional fallback when no official register: Paris 0.556, Casablanca 0.181"}
    return {"gate_passed": passed, "data_confidence_food": conf, "reason": reason, "coverage": cov} | basis


def scan_summary(res):
    v = res["model"].get("validation") or {}
    return {"concept": res["concept"]["text"], "label": res["concept"]["label"], "profile": res["concept"]["profile"],
            "fitted": res["model"]["fitted"], "n_presences": res["model"]["n_presences"],
            "auc_cv": v.get("auc_cv"), "auc_ci95": v.get("auc_ci95"), "ranking": res["params"].get("ranking"),
            "top": [{k: t[k] for k in TOP_KEYS if k in t} for t in res["top"]], "limits": res["limits"]}


def landlord_rows(rows):
    return [{"group": r["group"], "label_en": r["label_en"], "label_fr": r["label_fr"],
             "rent_max": r["rent_max_city_target"]["rent_max"], "ci90": r["rent_max_city_target"]["ci90"],
             "target_s3": r["rent_max_city_target"]["target"], "rent_max_80": r["rent_max_80"]["rent_max"],
             "location_hr": r["location_hr"], "relative_demand": r["relative_demand"]} for r in rows]


def publish(pack, slug, concepts, top, ground_truth=None, gt_source=None):
    city = cs.build_city(pack["info"], pack["elements"], overture=pack.get("overture"))
    q = quality(city, ground_truth, gt_source)
    g = city["grid"]
    out = {"slug": slug, "city": {k: pack["info"][k] for k in ("name", "display_name", "center", "bbox")},
           "grid": {k: g[k] for k in ("bbox", "rows", "cols", "cell_m", "dlat", "dlon")}, "study": sorted(city["study"]),
           "quality": q, "sources": city["sources"],
           "attribution": "© OpenStreetMap contributors (ODbL 1.0); © Overture Maps Foundation (CDLA-Permissive-2.0)",
           "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "scans": [], "units": None}
    if not q["gate_passed"]:
        return out
    for c in concepts:
        res, _ = cs.analyze(city, c, top=top)
        out["scans"].append(scan_summary(res))
    city_key = cs._norm(pack["info"].get("name") or "").split(",")[0].strip()
    if be.calibration(city_key, "restaurant"):
        pts = [cs.cell_center(g, i) for i in city["study"]]
        units = cs.unit_concepts_many(city, pts, catalog=list(be.GROUP_CONCEPT.values()))
        out["units"] = {"mode": "calibrated", "currency": "EUR",
                        "cells": {str(u["cell"]): landlord_rows(u["landlord"]["rows"]) for u in units}}
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("pack")
    ap.add_argument("--slug", required=True)
    ap.add_argument("--concept", action="append")
    ap.add_argument("--top", type=int, default=25)
    ap.add_argument("--ground-truth", help="JSON line from cityscan_groundtruth.py")
    ap.add_argument("--ground-truth-source", help="name of the official register, shown to users")
    a = ap.parse_args()
    t0 = time.perf_counter()
    with open(a.pack) as f:
        doc = publish(json.load(f), a.slug, a.concept or DEFAULT_CONCEPTS, a.top,
                      json.loads(a.ground_truth) if a.ground_truth else None, a.ground_truth_source)
    path = f"packs/{a.slug}.json.gz"
    with gzip.open(path, "wt", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, separators=(",", ":"))
    print(f"{path}: gate_passed={doc['quality']['gate_passed']} scans={len(doc['scans'])} "
          f"units={len((doc['units'] or {}).get('cells', {}))} in {time.perf_counter() - t0:.0f}s", file=sys.stderr)
