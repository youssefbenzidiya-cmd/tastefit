"""Offline builder: calibrate the break-even runway model on official restaurant survival (Paris SIRENE, Chicago
business licences).

Not part of the app. Reads the survival study's loaders read-only (no bytecode written to that folder), the raw
official-register/OSM-history files in /tmp/survival_raw and the city pack; writes aggregates only.

  TASTEFIT_SURVIVAL_DIR=/path/to/survival python3 cityscan_calibrate.py --pack /tmp/cityscan-pack-paris.json --out /tmp/cityscan-calib-paris.json \
      [--embed cityscan_calibration.json] [--boot 100] [--city chicago]

Steps
  1. Official units: SIRENE employer establishments opened 2014+ in Paris (NAF 56.10A restaurant, 56.10C fast food,
     56.30Z bar/cafe), follow-up to the OSM history end date, exactly as the survival study's pooled model.
  2. Relative demand r_i of each unit = Huff captured demand of its 400 m cell for the group's concept x the pack's
     detection-consistent calibration (cityscan.analyze), i.e. the same number the unit card uses.
  3. Per group: (theta, sigma) by method of moments on KM 1- and 3-year survival; cluster bootstrap over 500 m blocks.
  4. Held-out validation: west/east split of arrondissements (both folds), IPCW Brier at 3 years vs the city-average
     baseline, with and without the location Cox term (Cox refitted on the training half only).
  5. Forward test: calibrate on 2014-2018 openings, score 2019+ openings.
"""
import argparse
import json
import math
import os
import random
import statistics
import sys
import time
from collections import defaultdict

sys.dont_write_bytecode = True
SURVIVAL_DIR = os.environ.get("TASTEFIT_SURVIVAL_DIR") or sys.exit(
    "set TASTEFIT_SURVIVAL_DIR to the survival study folder (official.py, pooled.py, compare.py, survival.py)")
sys.path.insert(0, SURVIVAL_DIR)
import official  # noqa: E402
import pooled  # noqa: E402
from compare import COHORT_FROM, RAW, Y, load_osm  # noqa: E402
from survival import concordance_fast, cox_fit, ipcw_brier, kaplan_meier, km_at  # noqa: E402

sys.path.pop(0)
import cityscan as cs  # noqa: E402
import cityscan_breakeven as be  # noqa: E402

GROUP_OF_NAF = {"restaurant": "restaurant", "fast_food": "fast_food", "bar": "bar_cafe", "cafe": "bar_cafe"}
GROUP_CONCEPT = be.GROUP_CONCEPT
GROUPS = list(GROUP_CONCEPT)
N_BINS, N_DRAWS = 40, 400
SOURCES = {"paris": ("INSEE SIRENE, employer establishments opened 2014+", "SIRENE", "EUR", "Licence Ouverte"),
           "chicago": ("City of Chicago business licences (retail food, tavern), new licences issued 2014+",
                       "Chicago business licences", "USD", "City of Chicago Data Portal terms")}
STRIP_M = 1500  # Chicago has no arrondissements: west/east validation folds are 1.5 km longitude strips


def units_official(city):
    _, meta = load_osm(city)
    end = meta["END"]
    rows = official.load(city)
    lat0 = statistics.fmean(r["lat"] for r in rows)
    out = []
    for r in rows:
        if not (r["new"] and r["employer"] and COHORT_FROM <= r["start"] < end):
            continue
        sub = r["sub"] if city == "paris" else int(pooled.proj(r["lon"], r["lat"], lat0)[0] // STRIP_M)
        stop = min(r["end"], end)
        if sub is None or stop <= r["start"]:
            continue
        out.append({"lon": r["lon"], "lat": r["lat"], "start": r["start"], "concept": r["concept"],
                    "group": GROUP_OF_NAF[r["concept"]], "sub": sub, "t": (stop - r["start"]) / Y,
                    "e": int(r["closed"] and r["end"] <= end), "year": 1970 + int(r["start"] // Y)})
    return out, end


def history_covariates(units, city):
    """Same-concept within 150 m and POI within 200 m at the opening year (OSM history), standardised over units:
    the pooled model's exact definitions, free of survivor leakage."""
    states = [json.loads(line) for line in open(RAW / f"states_{city}.jsonl")]
    lat0 = statistics.fmean(u["lat"] for u in units)
    rs = pooled.rasters(states, lat0, range(2014, 2027))
    del states
    pooled.add_covariates(units, rs, lat0)
    del rs
    for n in ("same_concept_150m", "poi_200m"):
        vals = [u["x_raw"][n] for u in units]
        mu, sd = statistics.fmean(vals), statistics.pstdev(vals) or 1.0
        for u in units:
            u.setdefault("z", {})[n] = (u["x_raw"][n] - mu) / sd
    for u in units:
        x, y = pooled.proj(u["lon"], u["lat"], lat0)
        u["block"] = (int(x // 500), int(y // 500))


def pack_demand(city, units):
    """r_i per unit from the group's analysis, and pack-based covariates (for the live card) standardised over
    the units' locations."""
    stats = {}
    for g in GROUPS:
        t0 = time.perf_counter()
        _, st = cs.analyze(city, GROUP_CONCEPT[g], top=0, pool=0, cv=False, draws=None)
        same, poi = [], []
        for u in units:
            if u["group"] != g:
                continue
            j = cs.cell_of(city["grid"], u["lat"], u["lon"])
            if j is None or j not in city["study_set"]:
                continue
            u["r"] = st["E"][j] * st["demand_calib"]
            x, y = city["proj"].xy(u["lat"], u["lon"])
            same.append(math.log1p(len(st["cidx"].within(x, y, 150))))
            poi.append(math.log1p(sum(len(city["index"][c].within(x, y, 200)) for c in cs.POINT_CATS)))
        stats[g] = {"demand_calib": st["demand_calib"], "n_presences": st["n_presences"],
                    "pack_z": {"same_concept_150m": [statistics.fmean(same), statistics.pstdev(same) or 1.0],
                               "poi_200m": [statistics.fmean(poi), statistics.pstdev(poi) or 1.0]}}
        print(f"  {g}: analysis {time.perf_counter() - t0:.1f}s, units with r: {len(same)}", file=sys.stderr)
    return stats


def km(units, t):
    s, se, lo, hi = km_at(kaplan_meier([u["t"] for u in units], [u["e"] for u in units]), t)
    return s


def bins(rs, k=N_BINS):
    """Medians of k equal-count bins of r (the calibration targets are means over venues)."""
    rs = sorted(rs)
    n = len(rs)
    return [rs[(2 * b + 1) * n // (2 * k)] for b in range(k)] if n >= k else rs


def fit_group(units, draws, buffer_months=be.BUFFER_MONTHS, kappa=None):
    """(theta, sigma) on 1- and 3-year KM; kappa from the 3-year gap between venues above and below the median
    relative demand, unless fixed."""
    s1, s3 = km(units, 1.0), km(units, 3.0)
    if kappa is not None:
        return be.calibrate(bins([u["r"] for u in units]), s1, s3, draws, buffer_months, kappa=kappa) | {
            "s1": s1, "s3": s3}
    med = statistics.median(u["r"] for u in units)
    lo = [u for u in units if u["r"] <= med]
    hi = [u for u in units if u["r"] > med]
    f = be.calibrate_kappa(bins([u["r"] for u in lo], N_BINS // 2), bins([u["r"] for u in hi], N_BINS // 2),
                           s1, s3, km(lo, 3.0), km(hi, 3.0), draws, buffer_months)
    return f | {"s1": s1, "s3": s3}


def bootstrap(units, draws, reps, seed):
    by = defaultdict(list)
    for u in units:
        by[u["block"]].append(u)
    keys = list(by)
    rng = random.Random(seed)
    out = []
    for _ in range(reps):
        sample = [u for _ in keys for u in by[keys[rng.randrange(len(keys))]]]
        f = fit_group(sample, draws)
        out.append((f["theta"], f["sigma"], f["kappa"]))
    return out


def predict_model(theta, sigma, r, draws, months=36):
    """Unit-level model survival at the city-typical rent share (as in calibration)."""
    f = be.runway_factor(months)
    return sum(1 for m, fx, rent_pc, z in draws if theta * r * (math.exp(sigma * z) * m - rent_pc) >= fx * f) / len(draws)


def cox_hr(train, test, names=("same_concept_150m", "poi_200m")):
    xs = [[u["z"][n] for n in names] for u in train]
    fit = cox_fit(xs, [u["t"] for u in train], [u["e"] for u in train])
    mu = [statistics.fmean(u["z"][n] for u in train) for n in names]
    return fit, [math.exp(sum(b * (u["z"][n] - m) for b, n, m in zip(fit["beta"], names, mu))) for u in test]


def calib_deciles(preds, units, k=5):
    order = sorted(range(len(units)), key=lambda i: preds[i])
    out = []
    for b in range(k):
        idx = order[b * len(order) // k:(b + 1) * len(order) // k]
        us = [units[i] for i in idx]
        out.append({"pred_mean": round(statistics.fmean(preds[i] for i in idx), 3),
                    "observed_km_s3": round(km(us, 3.0), 3), "n": len(us)})
    return out


def spatial_split(units):
    cen = defaultdict(list)
    for u in units:
        cen[u["sub"]].append(u["lon"])
    order = sorted(cen, key=lambda s: statistics.fmean(cen[s]))
    west = set(order[:len(order) // 2])
    return west, [s for s in order if s not in west]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pack", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--embed")
    ap.add_argument("--boot", type=int, default=100)
    ap.add_argument("--city", default="paris", choices=sorted(SOURCES))
    a = ap.parse_args()
    t0 = time.perf_counter()
    source, short, currency, licence = SOURCES[a.city]
    share = be.rent_share(a.city)
    units, end = units_official(a.city)
    print(f"official units: {len(units)} ({time.perf_counter() - t0:.1f}s)", file=sys.stderr)
    history_covariates(units, a.city)
    print(f"history covariates done ({time.perf_counter() - t0:.1f}s)", file=sys.stderr)
    pack = json.load(open(a.pack))
    city = cs.build_city(pack["info"], pack["elements"], overture=pack.get("overture"))
    del pack
    pstats = pack_demand(city, units)
    del city
    with_r = [u for u in units if "r" in u]
    print(f"units in study cells: {len(with_r)}/{len(units)} ({time.perf_counter() - t0:.1f}s)", file=sys.stderr)

    res = {"city": a.city, "source": source, "censor_end_days": end, "buffer_months": be.BUFFER_MONTHS,
           "rent_share": share, "n_units": len(units), "n_units_with_r": len(with_r), "groups": {},
           "method": "method of moments (theta exact on 3-year KM, sigma bisected on 1-year KM); cluster bootstrap "
                     "over 500 m blocks"}
    for g in GROUPS:
        us = [u for u in with_r if u["group"] == g]
        inputs = be.defaults(a.city, be.GROUP_PROFILE[g])["inputs"]
        draws = be.survival_draws(inputs, N_DRAWS, rent_share=share)
        f = fit_group(us, draws)
        boot = bootstrap(us, draws, a.boot, seed=GROUPS.index(g) + 1)
        th, sg, kp = [b[0] for b in boot], [b[1] for b in boot], [b[2] for b in boot]
        sens = {}
        for b in (3, 9):
            fb = fit_group(us, draws, b)
            sens[f"buffer_{b}m"] = {"theta": round(fb["theta"], 1), "sigma": round(fb["sigma"], 3),
                                    "kappa": round(fb["kappa"], 3), "bound": fb["bound"]}
        fh = fit_group(us, draws, kappa=1.0)
        sens["kappa_1_huff_as_is"] = {"theta": round(fh["theta"], 1), "sigma": round(fh["sigma"], 3),
                                      "bound": fh["bound"]}
        rs = sorted(u["r"] for u in us)
        res["groups"][g] = {
            "group": g, "profile": be.GROUP_PROFILE[g], "concept_for_demand": GROUP_CONCEPT[g], "currency": currency,
            "official": {"s1": round(f["s1"], 4), "s3": round(f["s3"], 4), "n": len(us),
                         "events": sum(u["e"] for u in us), "source": short},
            "theta": round(f["theta"], 2), "sigma": round(f["sigma"], 4), "kappa": round(f["kappa"], 4),
            "bound": f["bound"], "kappa_bound": f["kappa_bound"], "target_gap_s3_hi_minus_lo": round(f["target_gap"], 4),
            "model_gap": round(f["model_gap"], 4),
            "fit_s1": round(f["fit_s1"], 4), "fit_s3": round(f["fit_s3"], 4),
            "theta_ci90": [round(cs_, 1) for cs_ in be._interval(th)],
            "sigma_ci90": [round(cs_, 3) for cs_ in be._interval(sg)],
            "kappa_ci90": [round(cs_, 3) for cs_ in be._interval(kp)],
            "boot": [[round(x, 2), round(y, 4), round(k, 4)] for x, y, k in boot],
            "buffer_sensitivity": sens,
            "r_quantiles_p10_p50_p90": [round(rs[int(q * (len(rs) - 1))], 3) for q in (0.1, 0.5, 0.9)],
            "pack_z": pstats[g]["pack_z"], "demand_calib": pstats[g]["demand_calib"]}
        gr = res["groups"][g]
        print(f"{g}: n={len(us)} S1={f['s1']:.3f} S3={f['s3']:.3f} theta={f['theta']:.1f} {gr['theta_ci90']} "
              f"sigma={f['sigma']:.3f} {gr['sigma_ci90']} kappa={f['kappa']:.3f} {gr['kappa_ci90']} "
              f"gap target={f['target_gap']:.4f} model={f['model_gap']:.4f} bound={f['bound']} "
              f"kbound={f['kappa_bound']} sens={sens} ({time.perf_counter() - t0:.1f}s)", file=sys.stderr)

    # Does relative demand itself carry survival signal? Cox on all units with log r and the location covariates.
    names = ["log_r", "same_concept_150m", "poi_200m", "fast_food", "bar"]
    xs = [[math.log(max(u["r"], 1e-6)), u["z"]["same_concept_150m"], u["z"]["poi_200m"],
           float(u["group"] == "fast_food"), float(u["group"] == "bar_cafe")] for u in with_r]
    sd_lr = statistics.pstdev(x[0] for x in xs) or 1.0
    mu_lr = statistics.fmean(x[0] for x in xs)
    for x in xs:
        x[0] = (x[0] - mu_lr) / sd_lr
    cf = cox_fit(xs, [u["t"] for u in with_r], [u["e"] for u in with_r])
    res["cox_check"] = {n: [round(h, 3), round(lo, 3), round(hi, 3), round(p, 4)]
                        for n, h, (lo, hi), p in zip(names, cf["hr"], cf["hr_ci"], cf["p"])}
    res["cox_check_note"] = "HR per SD of log relative demand and of the history covariates; {} official units".format(a.city.title())
    print(f"cox check: {res['cox_check']}", file=sys.stderr)

    west, east = spatial_split(with_r)
    folds = []
    for name, tr_s, te_s in (("train west, test east", set(west), set(east)),
                             ("train east, test west", set(east), set(west))):
        tr = [u for u in with_r if u["sub"] in tr_s]
        te = [u for u in with_r if u["sub"] in te_s]
        draws_by = {g: be.survival_draws(be.defaults(a.city, be.GROUP_PROFILE[g])["inputs"], N_DRAWS, rent_share=share)
                    for g in GROUPS}
        sc = score_by_group(tr, te, draws_by)
        folds.append({"fold": name, ("train_arrondissements" if a.city == "paris" else "train_strips_1500m"): sorted(tr_s), **sc})
        print(f"{name}: {sc['brier_all_groups']}", file=sys.stderr)
    res["validation_spatial"] = folds
    c_min = min(d["c_model_cox"] for f in folds for d in f["groups"].values())
    for gr in res["groups"].values():
        gr["location_hr"], gr["location_c_min"] = c_min > 0.5, c_min
    print(f"location HR applied: {c_min > 0.5} (min held-out C {c_min})", file=sys.stderr)

    fw_tr = [u for u in with_r if u["year"] <= 2018]
    fw_te = [u for u in with_r if u["year"] >= 2019]
    draws_by = {g: be.survival_draws(be.defaults(a.city, be.GROUP_PROFILE[g])["inputs"], N_DRAWS, rent_share=share) for g in GROUPS}
    res["validation_forward"] = {"train": "openings 2014-2018", "test": "openings 2019+ (3-year IPCW Brier)",
                                 **score_by_group(fw_tr, fw_te, draws_by)}
    print(f"forward: {res['validation_forward']['brier_all_groups']}", file=sys.stderr)
    res["elapsed_s"] = round(time.perf_counter() - t0, 1)
    with open(a.out, "w") as fh:
        json.dump(res, fh, indent=1)
    if a.embed:
        keep = ("group", "profile", "concept_for_demand", "currency", "official", "theta", "sigma", "kappa",
                "bound", "kappa_bound", "theta_ci90", "sigma_ci90", "kappa_ci90", "boot", "pack_z",
                "buffer_sensitivity", "location_hr", "location_c_min")
        try:
            emb = json.load(open(a.embed, encoding="utf-8"))
        except OSError:
            emb = {}
        emb[a.city] = {g: {k: v[k] for k in keep} for g, v in res["groups"].items()}
        prov = emb.setdefault("_provenance", {"builder": "cityscan_calibrate.py", "buffer_months": be.BUFFER_MONTHS,
                                              "method": res["method"]})
        prov.setdefault("sources", {})[a.city] = f"{source} ({licence}); OSM history for covariates"
        prov.setdefault("rent_shares", {})[a.city] = share
        with open(a.embed, "w") as fh:
            json.dump(emb, fh, indent=1, ensure_ascii=False)
    print(f"done in {res['elapsed_s']}s -> {a.out}", file=sys.stderr)


def score_by_group(train, test, draws_by):
    """score() with group-specific cost draws."""
    rows = {"baseline": [], "model_huff_kappa1": [], "model": [], "model_cox": [], "cox_only": []}
    tt, ee = [], []
    detail = {}
    for g in GROUPS:
        tr = [u for u in train if u["group"] == g]
        te = [u for u in test if u["group"] == g]
        if len(tr) < 200 or len(te) < 200:
            continue
        draws = draws_by[g]
        f = fit_group(tr, draws)
        f1 = fit_group(tr, draws, kappa=1.0)
        base = f["s3"]
        mod = [predict_model(f["theta"], f["sigma"], u["r"] ** f["kappa"], draws) for u in te]
        huff = [predict_model(f1["theta"], f1["sigma"], u["r"], draws) for u in te]
        _, hr = cox_hr(tr, te)
        add = {"baseline": [base] * len(te), "model_huff_kappa1": huff, "model": mod, "model_cox": [m ** h for m, h in zip(mod, hr)],
               "cox_only": [base ** h for h in hr]}
        t_, e_ = [u["t"] for u in te], [u["e"] for u in te]
        for n in rows:
            rows[n] += add[n]
        tt += t_
        ee += e_
        detail[g] = {"n_train": len(tr), "n_test": len(te), "train_s3": round(base, 3),
                     "test_km_s3": round(km(te, 3.0), 3), "theta": round(f["theta"], 1),
                     "sigma": round(f["sigma"], 3), "kappa": round(f["kappa"], 3), "bound": f["bound"],
                     "mean_pred_huff_kappa1": round(statistics.fmean(huff), 3),
                     "sd_pred_huff_kappa1": round(statistics.pstdev(huff), 3),
                     "mean_pred_model": round(statistics.fmean(mod), 3),
                     "sd_pred_model": round(statistics.pstdev(mod), 3),
                     "brier": {n: round(ipcw_brier(t_, e_, v, 3.0), 5) for n, v in add.items()},
                     "c_model": round(concordance_fast(t_, e_, [-m for m in mod]), 3),
                     "c_model_cox": round(concordance_fast(t_, e_, [-m for m in add["model_cox"]]), 3),
                     "calibration_model": calib_deciles(mod, te),
                     "calibration_model_cox": calib_deciles(add["model_cox"], te)}
    return {"brier_all_groups": {n: round(ipcw_brier(tt, ee, v, 3.0), 5) for n, v in rows.items()},
            "n_test": len(tt), "groups": detail}


if __name__ == "__main__":
    main()
