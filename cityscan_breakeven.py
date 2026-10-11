"""Break-even SCENARIO model for one unit and one concept. Standard library only, pure and seeded.

Not a forecast: it turns the Huff captured demand of a location into a monthly P&L under stated, editable
assumptions and reports how often the scenario breaks even and which assumption drives the answer.

  covers/day   = covers_avg x relative_demand x demand_mult
                 (relative_demand = captured demand of this location / demand served by an average existing venue)
  revenue      = covers/day x ticket x open days per month
  profit/month = revenue x (1 - cogs - other_var) - rent - staff - other_fixed
  payback      = capex / profit (when profit > 0)

Uncertain inputs are triangular (lo, mode, hi) or lognormal; 2,000 Monte Carlo draws with a fixed seed;
first-order and total Sobol indices (Saltelli et al. 2010; Jansen 1999) and one-at-a-time tornado data on profit.
"""
import json
import math
import os
import random
from statistics import NormalDist

import cityscan_math as cm

PARAM_ORDER = ["covers_avg", "demand_mult", "ticket", "days", "cogs", "other_var", "staff", "other_fixed"]
LABELS = {
    "covers_avg": ("covers/day of an average existing venue", "couverts/jour d'un établissement moyen"),
    "demand_mult": ("captured-demand model error", "erreur du modèle de demande captée"),
    "ticket": ("average ticket", "ticket moyen"),
    "days": ("open days per month", "jours d'ouverture par mois"),
    "cogs": ("food and drink cost (% of sales)", "coût matière (% du CA)"),
    "other_var": ("other variable costs (% of sales)", "autres coûts variables (% du CA)"),
    "staff": ("staff cost per month", "masse salariale mensuelle"),
    "other_fixed": ("other fixed costs per month", "autres charges fixes mensuelles"),
    "capex": ("opening investment", "investissement d'ouverture"),
}

ASSUME = "TasteFit assumption, replace with local data"
_SMIG = ("staff at 1-2x the Moroccan minimum wage (SMIG, roughly 3,000-3,500 MAD/month gross in 2024-25) "
         "plus employer charges; " + ASSUME)
_SMIC = "staff at about the French SMIC (~1,800 EUR/month gross in 2025) plus ~45% employer charges; " + ASSUME
_CHI_MW = ("staff paid around the Chicago minimum wage ($17.05/h from 1 July 2026, tipped $12.96; City of Chicago "
           "Office of Labor Standards) plus ~10% employer payroll taxes, sized at ~30% of sales; " + ASSUME)
_COGS = "rule of thumb in restaurant cost accounting: food and drink cost 25-35% of sales; " + ASSUME


def tri(lo, mode, hi, source=ASSUME):
    return {"dist": "tri", "lo": lo, "mode": mode, "hi": hi, "source": source}


def lognormal(median, sigma, source=ASSUME):
    return {"dist": "lognormal", "median": median, "sigma": sigma, "source": source}


def _profile(currency, covers, ticket, days, cogs, other_var, staff, other_fixed, capex, ticket_src=ASSUME,
             staff_src=ASSUME):
    return {"currency": currency,
            "inputs": {"covers_avg": tri(*covers, "average covers/day of an existing venue of this kind; no public "
                                                  "source: conservative TasteFit assumption, replace with door "
                                                  "counts"),
                       "demand_mult": lognormal(1.0, 0.7, "multiplicative error of the Huff captured-demand index "
                                                          "(a factor of 2 at 1 sd); stated, not estimated: the "
                                                          "index did not predict openings (ABLATION.md)"),
                       "ticket": tri(*ticket, ticket_src), "days": tri(*days), "cogs": tri(*cogs, _COGS),
                       "other_var": tri(*other_var, "card fees, packaging, delivery commissions; " + ASSUME),
                       "staff": tri(*staff, staff_src),
                       "other_fixed": tri(*other_fixed, "utilities, licences, maintenance, marketing; " + ASSUME),
                       "capex": tri(*capex, "fit-out and equipment; " + ASSUME)}}


_CASA_DAYS, _PARIS_DAYS, _US_DAYS = (26, 28, 30), (24, 26, 29), (26, 29, 30)
DEFAULTS = {
    ("casablanca", "cafe"): _profile("MAD", (50, 100, 200), (30, 37, 45), _CASA_DAYS, (.25, .30, .38),
                                     (.02, .04, .07), (15000, 22000, 32000), (4000, 7000, 12000),
                                     (350000, 550000, 900000),
                                     ticket_src="brief: Casablanca specialty coffee ticket 30-45 MAD", staff_src=_SMIG),
    ("casablanca", "fast_casual"): _profile("MAD", (80, 150, 260), (45, 60, 85), _CASA_DAYS, (.28, .33, .40),
                                            (.03, .08, .15), (18000, 26000, 38000), (5000, 8000, 14000),
                                            (300000, 500000, 850000), staff_src=_SMIG),
    ("casablanca", "healthy"): _profile("MAD", (60, 110, 200), (55, 75, 100), _CASA_DAYS, (.30, .35, .42),
                                        (.03, .08, .15), (18000, 26000, 38000), (5000, 8000, 14000),
                                        (300000, 500000, 850000), staff_src=_SMIG),
    ("casablanca", "restaurant"): _profile("MAD", (40, 80, 140), (80, 130, 200), _CASA_DAYS, (.28, .32, .38),
                                           (.02, .05, .10), (30000, 45000, 65000), (8000, 12000, 20000),
                                           (600000, 1000000, 1800000), staff_src=_SMIG),
    ("casablanca", "fine_dining"): _profile("MAD", (30, 60, 110), (250, 350, 500), _CASA_DAYS, (.28, .33, .40),
                                            (.02, .04, .07), (45000, 65000, 95000), (12000, 18000, 30000),
                                            (1200000, 2000000, 3500000), staff_src=_SMIG),
    ("casablanca", "bar"): _profile("MAD", (40, 80, 150), (80, 120, 180), _CASA_DAYS, (.22, .28, .35),
                                    (.02, .04, .07), (25000, 35000, 50000), (8000, 12000, 20000),
                                    (500000, 900000, 1500000), staff_src=_SMIG),
    ("paris", "cafe"): _profile("EUR", (100, 180, 320), (4.5, 6, 8.5), _PARIS_DAYS, (.22, .28, .35),
                                (.02, .03, .05), (9000, 13000, 19000), (2000, 3500, 6000), (80000, 150000, 260000),
                                staff_src=_SMIC),
    ("paris", "restaurant"): _profile("EUR", (50, 90, 150), (18, 26, 40), _PARIS_DAYS, (.27, .32, .36),
                                      (.02, .04, .08), (20000, 30000, 45000), (4000, 6000, 10000),
                                      (200000, 350000, 600000), staff_src=_SMIC),
    ("paris", "fast_casual"): _profile("EUR", (100, 180, 300), (10, 13, 17), _PARIS_DAYS, (.28, .32, .38),
                                       (.03, .08, .15), (12000, 18000, 26000), (3000, 4500, 7000),
                                       (120000, 220000, 380000), staff_src=_SMIC),
    ("chicago", "cafe"): _profile("USD", (100, 180, 320), (5, 7, 10), _US_DAYS, (.25, .30, .35),
                                  (.03, .04, .06), (8000, 12000, 18000), (2000, 3500, 6000),
                                  (90000, 180000, 320000), staff_src=_CHI_MW),
    ("chicago", "restaurant"): _profile("USD", (50, 90, 150), (20, 30, 45), (25, 27, 30), (.28, .32, .36),
                                        (.03, .05, .09), (18000, 26000, 38000), (4000, 6500, 10000),
                                        (250000, 450000, 800000), staff_src=_CHI_MW),
    ("chicago", "fast_casual"): _profile("USD", (100, 180, 300), (11, 14, 18), _US_DAYS, (.28, .31, .36),
                                         (.04, .09, .16), (14000, 20000, 29000), (3000, 5000, 8000),
                                         (150000, 280000, 450000), staff_src=_CHI_MW),
}


def defaults(city_key, profile):
    """Default inputs for (city, concept profile), or None when TasteFit has no defaults for that city."""
    d = DEFAULTS.get((city_key, profile)) or DEFAULTS.get((city_key, "restaurant"))
    return None if d is None else {"currency": d["currency"], "inputs": {k: dict(v) for k, v in d["inputs"].items()}}


def quantile(spec, u):
    """Inverse CDF of an input distribution at u in (0, 1)."""
    if spec["dist"] == "tri":
        a, c, b = spec["lo"], spec["mode"], spec["hi"]
        if b <= a:
            return a
        if u < (c - a) / (b - a):
            return a + math.sqrt(u * (b - a) * (c - a))
        return b - math.sqrt((1 - u) * (b - a) * (b - c))
    if spec["sigma"] <= 0:
        return spec["median"]
    return spec["median"] * math.exp(spec["sigma"] * NormalDist().inv_cdf(min(max(u, 1e-12), 1 - 1e-12)))


def draw(spec, rng):
    return quantile(spec, rng.random())


def monthly(v, relative_demand, rent):
    """Deterministic monthly P&L for one set of input values v (dict)."""
    covers = v["covers_avg"] * relative_demand * v["demand_mult"]
    revenue = covers * v["ticket"] * v["days"]
    profit = revenue * (1 - v["cogs"] - v["other_var"]) - rent - v["staff"] - v["other_fixed"]
    return {"covers_day": covers, "revenue": revenue, "profit": profit}


def scenario(relative_demand, rent, inputs, draws=2000, seed=20261011, horizon_months=120):
    """Monte Carlo of the monthly P&L: P(break-even), profit quantiles, rent-to-revenue, payback."""
    rng = random.Random(seed)
    profit, revenue, covers, r2r, payback = [], [], [], [], []
    for _ in range(draws):
        v = {k: draw(inputs[k], rng) for k in PARAM_ORDER}
        capex = draw(inputs["capex"], rng)
        m = monthly(v, relative_demand, rent)
        profit.append(m["profit"])
        revenue.append(m["revenue"])
        covers.append(m["covers_day"])
        r2r.append(rent / m["revenue"] if m["revenue"] > 0 else float("inf"))
        payback.append(capex / m["profit"] if m["profit"] > 0 else float("inf"))
    med_pb = cm.percentile(payback, 0.5)
    return {
        "rent": rent, "draws": draws, "seed": seed,
        "p_break_even": round(sum(1 for p in profit if p >= 0) / draws, 3),
        "profit_p10_p50_p90": [round(cm.percentile(profit, q)) for q in (0.1, 0.5, 0.9)],
        "revenue_p50": round(cm.percentile(revenue, 0.5)),
        "covers_day_p10_p50_p90": [round(cm.percentile(covers, q), 1) for q in (0.1, 0.5, 0.9)],
        "rent_to_revenue_p50": round(cm.percentile(r2r, 0.5), 3) if math.isfinite(cm.percentile(r2r, 0.5)) else None,
        "payback_months_p50": round(med_pb, 1) if med_pb <= horizon_months else None,
        "p_payback_within_36m": round(sum(1 for p in payback if p <= 36) / draws, 3),
    }


def sensitivity(relative_demand, rent, inputs, n=2048, seed=20261011):
    """Sobol indices and tornado data of monthly profit with respect to every uncertain input."""
    samplers = [(lambda spec: (lambda rng: draw(spec, rng)))(inputs[k]) for k in PARAM_ORDER]
    f = lambda x: monthly(dict(zip(PARAM_ORDER, x)), relative_demand, rent)["profit"]  # noqa: E731
    s1, st, _ = cm.sobol_indices(f, samplers, n=n, seed=seed)
    base = {k: quantile(inputs[k], 0.5) for k in PARAM_ORDER}
    p0 = monthly(base, relative_demand, rent)["profit"]
    tornado = []
    for k in PARAM_ORDER:
        lo = monthly({**base, k: quantile(inputs[k], 0.1)}, relative_demand, rent)["profit"]
        hi = monthly({**base, k: quantile(inputs[k], 0.9)}, relative_demand, rent)["profit"]
        tornado.append({"input": k, "profit_at_p10": round(lo), "profit_at_p90": round(hi), "swing": round(abs(hi - lo))})
    tornado.sort(key=lambda t: -t["swing"])
    sobol = sorted(({"input": k, "first_order": round(max(a, 0.0), 3), "total": round(max(b, 0.0), 3)}
                    for k, a, b in zip(PARAM_ORDER, s1, st)), key=lambda t: -t["first_order"])
    top = sobol[0]["input"]
    return {"profit_at_medians": round(p0), "sobol": sobol, "tornado": tornado, "n_base": n,
            "depends_most_on_en": f"Your result depends mostly on the {LABELS[top][0]} "
                                  f"({100 * sobol[0]['first_order']:.0f}% of the profit variance on its own).",
            "depends_most_on_fr": f"Le résultat dépend surtout de : {LABELS[top][1]} "
                                  f"({100 * sobol[0]['first_order']:.0f} % de la variance du résultat à elle seule)."}


# ----------------------------------------------------------------------------------------------------------------
# Survival-anchored calibration (official-calibrated cities)
#
# Runway model. Expected covers/day at a location = theta * r^kappa; a venue's own covers = that x exp(sigma z).
# Monthly result pi = covers x ticket x days x (1 - cogs - other_var) - rent - (staff + other_fixed), and with a cash
# buffer of b months of fixed costs the venue is still open after t months iff pi >= -b (staff + other_fixed) / t.
# theta, sigma (and the demand elasticity kappa) are fitted so the model's mean survival over the group's real
# venues matches official 3-year and 1-year survival. In calibration rent = rho x the location's expected revenue
# (cited rho range below); on the card rent is the user's figure.

BUFFER_MONTHS = 6
RENT_SHARE = tri(0.05, 0.10, 0.15, "rent-to-revenue of French restaurants: 8-12% typical, 10-15% in Paris, up to "
                                   "15% for premium sites (L'Hotellerie-Restauration; ProRestauration 2026; Almo); "
                                   "INSEE Esane 2023 aggregate 5.1%")
RENT_SHARES = {"chicago": tri(0.04, 0.06, 0.10, "occupancy cost-to-sales of US restaurants: median 6.0% in urban "
                                                "areas, full- and limited-service (National Restaurant Association, "
                                                "Restaurant Operations Data Abstract 2025, 2024 data); 6-10% rule "
                                                "of thumb, 10% excessive (Restaurant Resource Group)")}


def rent_share(city_key):
    return RENT_SHARES.get(city_key, RENT_SHARE)
SURVIVAL_GROUP = {"restaurant": "restaurant", "fine_dining": "restaurant", "healthy": "restaurant",
                  "fast_casual": "fast_food", "cafe": "bar_cafe", "bar": "bar_cafe"}
GROUP_PROFILE = {"restaurant": "restaurant", "fast_food": "fast_casual", "bar_cafe": "cafe"}
GROUP_LABEL = {"restaurant": ("restaurants", "restaurants"), "fast_food": ("fast food", "restauration rapide"),
               "bar_cafe": ("bars and cafés", "débits de boissons")}
GROUP_CONCEPT = {"restaurant": "restaurant", "fast_food": "fast food", "bar_cafe": "cafe"}
HR_PER_SD = {"same_concept_150m": (0.932, 0.908, 0.957), "poi_200m": (0.917, 0.893, 0.942)}
CALIBRATION_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cityscan_calibration.json")
# Survival-study quality gate (FINDINGS.md): cities where map-based survival odds are not shown.
GATE = {"casablanca": {"en": "survival-study quality gate failed on all 4 criteria (Maârif: OSM closure rate 1.5, "
                             "noise share 52%, predicted bias +32 pp, 28 openings; no official business register)",
                       "fr": "contrôle qualité de l'étude de survie échoué sur les 4 critères (Maârif : taux de "
                             "fermeture OSM 1,5, part de bruit 52 %, biais prédit +32 pts, 28 ouvertures ; pas de "
                             "registre officiel des entreprises)"}}
_calibration = None


def calibration(city_key, profile):
    """Calibrated parameters for (city, concept profile) with the cost inputs they were fitted under, or None."""
    global _calibration
    if _calibration is None:
        try:
            with open(CALIBRATION_PATH, encoding="utf-8") as f:
                _calibration = json.load(f)
        except OSError:
            _calibration = {}
    group = SURVIVAL_GROUP.get(profile)
    c = (_calibration.get(city_key) or {}).get(group)
    d = defaults(city_key, GROUP_PROFILE.get(group, ""))
    if not c or d is None:
        return None
    return dict(c, inputs=d["inputs"], currency=d["currency"], rent_share=rent_share(city_key),
                buffer_months=_calibration["_provenance"]["buffer_months"])


def gate_text(city_key, detection_osm=None):
    """Why a city gets the conditional card instead of survival odds."""
    seen_en = f"OSM sees ~{100 * detection_osm:.0f}% of venues" if detection_osm else "venue coverage unknown"
    seen_fr = f"OSM voit ~{100 * detection_osm:.0f} % des établissements" if detection_osm else "couverture inconnue"
    g = GATE.get(city_key, {"en": "no official business-survival data calibrated for this city",
                            "fr": "pas de données officielles de survie calibrées pour cette ville"})
    return (f"Map data here is too incomplete for survival odds: {seen_en} (two-source capture-recapture); "
            f"{g['en']}.",
            f"Les données cartographiques sont trop incomplètes pour des probabilités de survie : {seen_fr} "
            f"(capture-recapture à deux sources) ; {g['fr']}.")
HR_SOURCE = "pooled city-stratified Cox model on official openings (Paris, Lyon, Chicago, Philadelphia), survival study"
_SURV_KEYS = ("ticket", "days", "cogs", "other_var", "staff", "other_fixed")


def runway_factor(months, buffer_months=BUFFER_MONTHS):
    return max(0.0, 1.0 - buffer_months / months)


def survival_draws(inputs, n=400, seed=20261011, rent_share=RENT_SHARE):
    """Common random numbers for the survival model, per draw: monthly margin per daily cover, fixed costs,
    rent per daily cover at the drawn rent-to-revenue share, and the venue's demand shock z."""
    rng = random.Random(seed)
    nd = NormalDist()
    out = []
    for _ in range(n):
        tk, dy, cg, ov, sf, of = (draw(inputs[k], rng) for k in _SURV_KEYS)
        rho = draw(rent_share, rng)
        out.append((tk * dy * (1 - cg - ov), sf + of, rho * tk * dy,
                    nd.inv_cdf(min(max(rng.random(), 1e-12), 1 - 1e-12))))
    return out


def _log_thresholds(rel_demands, draws, sigma, kappa=1.0):
    """log of the covers/day theta an average venue would need to reach break-even, per (venue, draw). Rent is a
    share of the location's expected revenue (theta x r^kappa covers), the venue's own shock exp(sigma z) is not:
    profit = theta r^kappa (exp(sigma z) m - rent_pc) - fixed."""
    base = []
    for m, f, rent_pc, z in draws:
        v = math.exp(sigma * z) * m - rent_pc
        base.append(math.log(f) - math.log(v) if v > 0 else math.inf)
    out = []
    for r in rel_demands:
        lr = kappa * math.log(max(r, 1e-9))
        out.extend(b - lr for b in base)
    out.sort()
    return out


def _frac_le(sorted_vals, x):
    lo, hi = 0, len(sorted_vals)
    while lo < hi:
        mid = (lo + hi) // 2
        if sorted_vals[mid] <= x:
            lo = mid + 1
        else:
            hi = mid
    return lo / len(sorted_vals)


def mean_survival(theta, sigma, rel_demands, draws, months, buffer_months=BUFFER_MONTHS, kappa=1.0):
    """Model survival to `months`, averaged over venues (relative demands) and cost draws (rent = rho x revenue)."""
    f = runway_factor(months, buffer_months)
    if f <= 0:
        return 1.0
    return _frac_le(_log_thresholds(rel_demands, draws, sigma, kappa), math.log(theta) - math.log(f))


def calibrate(rel_demands, s1, s3, draws, buffer_months=BUFFER_MONTHS, sigma_max=3.0, iters=24, kappa=1.0):
    """Method of moments: theta matches mean 3-year survival exactly for each sigma; sigma is bisected so the mean
    1-year survival also matches. Returns theta, sigma, the fitted moments and whether sigma hit a bound."""
    f12, f36 = runway_factor(12, buffer_months), runway_factor(36, buffer_months)

    def solve(sigma):
        v = _log_thresholds(rel_demands, draws, sigma, kappa)
        k = min(max(int(round(s3 * len(v))) - 1, 0), len(v) - 1)
        log_theta = v[k] + math.log(f36)
        return log_theta, _frac_le(v, log_theta - math.log(f12)), _frac_le(v, log_theta - math.log(f36))

    def doomed(sigma):  # share of draws whose own demand shock cannot cover rent at any theta
        return sum(1 for m, _, rent_pc, z in draws if math.exp(sigma * z) * m <= rent_pc) / len(draws)

    if doomed(sigma_max) >= 1 - s1:
        a, c = 0.0, sigma_max
        for _ in range(iters):
            mid = (a + c) / 2
            a, c = (mid, c) if doomed(mid) < 1 - s1 else (a, mid)
        sigma_max = a
    lo, hi = 0.0, sigma_max
    lt, m1, m3 = solve(lo)
    bound = None
    if m1 < s1:  # even with no venue-level dispersion the model's 1-to-3-year drop is too large
        bound, sigma = "sigma=0 (target 1y-3y gap wider than the runway model allows)", 0.0
    else:
        lt_hi, m1_hi, m3_hi = solve(hi)
        if m1_hi > s1:
            bound, sigma, lt, m1, m3 = f"sigma={sigma_max} (upper bound)", hi, lt_hi, m1_hi, m3_hi
        else:
            for _ in range(iters):
                mid = (lo + hi) / 2
                lt, m1, m3 = solve(mid)
                lo, hi = (mid, hi) if m1 > s1 else (lo, mid)
            sigma = (lo + hi) / 2
            lt, m1, m3 = solve(sigma)
    return {"theta": math.exp(lt), "sigma": sigma, "kappa": kappa, "fit_s1": m1, "fit_s3": m3, "bound": bound}


def calibrate_kappa(rel_lo, rel_hi, s1, s3, s3_lo, s3_hi, draws, buffer_months=BUFFER_MONTHS, kappa_max=1.0,
                    iters=12):
    """Third moment: the demand elasticity kappa (covers = theta x r^kappa x exp(sigma z)) is bisected so that the
    model's 3-year survival gap between venues above and below the median relative demand matches the official gap
    (s3_hi - s3_lo); theta and sigma are refitted on all venues at each kappa. kappa = 1 is the uncalibrated Huff
    assumption; a gap at or below 0 gives kappa = 0 (demand index carries no survival signal)."""
    rel = rel_lo + rel_hi
    target = s3_hi - s3_lo

    def gap(k):
        f = calibrate(rel, s1, s3, draws, buffer_months, kappa=k)
        g = (mean_survival(f["theta"], f["sigma"], rel_hi, draws, 36, buffer_months, k) -
             mean_survival(f["theta"], f["sigma"], rel_lo, draws, 36, buffer_months, k))
        return g, f

    if target <= 0:
        f = calibrate(rel, s1, s3, draws, buffer_months, kappa=0.0)
        return f | {"model_gap": 0.0, "target_gap": target, "kappa_bound": "kappa=0 (no positive gap)"}
    g_hi, f_hi = gap(kappa_max)
    if g_hi <= target:
        return f_hi | {"model_gap": g_hi, "target_gap": target, "kappa_bound": f"kappa={kappa_max} (upper bound)"}
    lo, hi = 0.0, kappa_max
    for _ in range(iters):
        mid = (lo + hi) / 2
        g, _ = gap(mid)
        lo, hi = (mid, hi) if g < target else (lo, mid)
    g, f = gap((lo + hi) / 2)
    return f | {"model_gap": g, "target_gap": target, "kappa_bound": None}


def spot_survival(theta, sigma, rel_demand, rent, draws, months, buffer_months=BUFFER_MONTHS, kappa=1.0):
    """P(still open after `months`) at an explicit monthly rent; months=0 gives P(monthly break-even)."""
    f = 1.0 if months == 0 else runway_factor(months, buffer_months)
    covers = theta * max(rel_demand, 1e-9) ** kappa
    k = sum(1 for m, fx, _, z in draws if covers * math.exp(sigma * z) * m >= rent + fx * f)
    return k / len(draws)


def _interval(vals, level=0.90):
    a = (1 - level) / 2
    return [cm.percentile(vals, a), cm.percentile(vals, 1 - a)]


def location_hr(z_same, z_poi):
    return HR_PER_SD["same_concept_150m"][0] ** z_same * HR_PER_SD["poi_200m"][0] ** z_poi


def calibrated_card(rel_demand, rents, calib, z_same=0.0, z_poi=0.0, n=1000, seed=20261011, use_cox=True):
    """Unit card in official-calibrated cities: P(still open at 1 and 3 years) and P(monthly break-even) at each
    rent, from the calibrated runway model at the spot's relative demand, times the location's Cox hazard ratio.
    90% interval from the bootstrap of (theta, sigma) and the HR confidence intervals; a risk badge only when the
    interval excludes the official city baseline."""
    inputs = calib["inputs"]
    use_cox = use_cox and calib.get("location_hr", True)
    draws = survival_draws(inputs, n, seed)
    rng = random.Random(seed + 1)
    nd = NormalDist()
    se = {k: (math.log(v[2]) - math.log(v[1])) / (2 * 1.96) for k, v in HR_PER_SD.items()}
    hr = location_hr(z_same, z_poi) if use_cox else 1.0
    base3, base1 = calib["official"]["s3"], calib["official"]["s1"]
    modes = {k: inputs[k]["mode"] for k in _SURV_KEYS}
    margin = modes["ticket"] * modes["days"] * (1 - modes["cogs"] - modes["other_var"])
    th0, sg0, k0, b = calib["theta"], calib["sigma"], calib.get("kappa", 1.0), calib["buffer_months"]
    by_rent = []
    for rent in rents:
        s3 = spot_survival(th0, sg0, rel_demand, rent, draws, 36, b, k0) ** hr
        s1 = spot_survival(th0, sg0, rel_demand, rent, draws, 12, b, k0) ** hr
        pbe = spot_survival(th0, sg0, rel_demand, rent, draws, 0, b, k0)
        boot = []
        for bt in calib["boot"]:
            th, sg, kp = (list(bt) + [k0])[:3]
            h = (math.exp(z_same * nd.inv_cdf(rng.random()) * se["same_concept_150m"] +
                          z_poi * nd.inv_cdf(rng.random()) * se["poi_200m"]) * hr) if use_cox else 1.0
            boot.append(spot_survival(th, sg, rel_demand, rent, draws, 36, b, kp) ** h)
        lo, hi = _interval(boot)
        badge = "lower risk than city" if lo > base3 else "higher risk than city" if hi < base3 else None
        by_rent.append({"rent": rent, "p_open_3y": round(s3, 3), "p_open_3y_ci90": [round(lo, 3), round(hi, 3)],
                        "p_open_1y": round(s1, 3), "p_monthly_break_even": round(pbe, 3),
                        "break_even_covers_day": round((rent + modes["staff"] + modes["other_fixed"]) / margin),
                        "expected_covers_day": round(th0 * max(rel_demand, 1e-9) ** k0), "risk_badge": badge})
    g_en, g_fr = GROUP_LABEL[calib["group"]]
    return {"mode": "calibrated", "label": "CALIBRATED ON OFFICIAL SURVIVAL - still a model, not a forecast",
            "currency": calib["currency"], "group": calib["group"], "relative_demand": round(rel_demand, 3),
            "baseline_en": f"City baseline 3-yr survival for {g_en}: {100 * base3:.0f}% "
                           f"(1-yr {100 * base1:.0f}%; official {calib['official']['source']}, "
                           f"n={calib['official']['n']})",
            "baseline_fr": f"Survie à 3 ans de référence ({g_fr}, ville) : {100 * base3:.0f} % "
                           f"(1 an {100 * base1:.0f} % ; source officielle {calib['official']['source']}, "
                           f"n={calib['official']['n']})",
            "city_s3": base3, "city_s1": base1, "location_hr": round(hr, 3),
            "typical_rent": round(calib.get("rent_share", RENT_SHARE)["mode"] * th0 * max(rel_demand, 1e-9) ** k0 * modes["ticket"] *
                                  modes["days"], -2),
            "z": {"same_concept_150m": round(z_same, 2), "poi_200m": round(z_poi, 2)},
            "theta": round(calib["theta"], 1), "theta_ci90": calib["theta_ci90"], "sigma": round(calib["sigma"], 3),
            "sigma_ci90": calib["sigma_ci90"], "kappa": round(k0, 3), "kappa_ci90": calib.get("kappa_ci90"),
            "buffer_months": b,
            "by_rent": by_rent, "inputs": inputs, "hr_source": HR_SOURCE, "hr_per_sd": HR_PER_SD,
            "boot": calib["boot"]}


def conditional_card(rents, inputs, covers=None, ticket=None, gate_en="", gate_fr=""):
    """Data-thin cities: no probability. Monthly result at the user's covers and ticket, and the covers/day needed
    to break even at each rent (all other inputs at their stated modes)."""
    m = {k: inputs[k]["mode"] for k in PARAM_ORDER if inputs[k]["dist"] == "tri"}
    covers = m["covers_avg"] if covers is None else covers
    ticket = m["ticket"] if ticket is None else ticket
    unit = ticket * m["days"] * (1 - m["cogs"] - m["other_var"])
    fixed = m["staff"] + m["other_fixed"]
    rows = [{"rent": rent, "break_even_covers_day": math.ceil(break_even_covers(
                 rent, ticket, m["days"], m["cogs"], m["other_var"], m["staff"], m["other_fixed"])),
             "monthly_result": round(covers * unit - rent - fixed),
             "revenue": round(covers * ticket * m["days"])} for rent in rents]
    return {"mode": "conditional", "label": "CONDITIONAL - no survival odds for this city",
            "covers_day": covers, "ticket": ticket, "days": m["days"], "cogs": m["cogs"],
            "other_var": m["other_var"], "staff": m["staff"], "other_fixed": m["other_fixed"],
            "max_rent_break_even": round(max_rent_break_even(covers, ticket, m["days"], m["cogs"], m["other_var"],
                                                             m["staff"], m["other_fixed"])),
            "by_rent": rows, "gate_en": gate_en, "gate_fr": gate_fr}


def break_even_covers(rent, ticket, days, cogs, other_var, staff, other_fixed):
    return (rent + staff + other_fixed) / (ticket * days * (1 - cogs - other_var))


def max_rent_break_even(covers, ticket, days, cogs, other_var, staff, other_fixed):
    """Conditional ceiling: the monthly rent at which the given covers/day and ticket exactly break even."""
    return covers * ticket * days * (1 - cogs - other_var) - staff - other_fixed


# ----------------------------------------------------------------------------------------------------------------
# Rent ceiling: the calibrated runway model inverted for rent (bid rent of each tenant type)

RENT_TARGETS = (0.70, 0.80, 0.90)


def rent_capacity(theta, sigma, kappa, rel_demand, draws, months=36, buffer_months=BUFFER_MONTHS):
    """Per draw, the highest monthly rent at which that venue is still open after `months`."""
    f = runway_factor(months, buffer_months)
    c = theta * max(rel_demand, 1e-9) ** kappa
    return [c * math.exp(sigma * z) * m - fx * f for m, fx, _, z in draws]


def survival_at_rent(caps, rent, hr=1.0):
    return (sum(1 for a in caps if a >= rent) / len(caps)) ** hr


def rent_ceiling(caps, target, hr=1.0, tol=1.0):
    """Largest rent R* with P(open at 36 months) = share(caps >= R*)^hr >= target, by bisection on a curve that is
    non-increasing in rent. With common random numbers R* moves smoothly with the parameters. May be negative:
    then even a free unit does not reach the target."""
    lo, hi = min(caps) - 1.0, max(caps) + 1.0
    if survival_at_rent(caps, lo, hr) < target:
        return lo
    while hi - lo > tol:
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if survival_at_rent(caps, mid, hr) >= target else (lo, mid)
    return lo


def _boot_hr(rng, nd, se, z_same, z_poi, hr):
    return math.exp(z_same * nd.inv_cdf(rng.random()) * se["same_concept_150m"] +
                    z_poi * nd.inv_cdf(rng.random()) * se["poi_200m"]) * hr


def rent_ceiling_card(rel_demand, calib, z_same=0.0, z_poi=0.0, target=None, asking=None, surface_m2=None,
                      n=2000, seed=20261011, curve_points=41):
    """Founder view: the maximum monthly rent at which P(still open at 36 months) stays at or above the target
    (default: the official city baseline of the group), at this spot (Cox location adjustment applied), with a 90%
    interval from the bootstrap of the calibrated parameters and the HR intervals; the survival-vs-rent curve; and
    where the asking rent sits against the city norm."""
    draws = survival_draws(calib["inputs"], n, seed)
    rng, nd = random.Random(seed + 2), NormalDist()
    se = {k: (math.log(v[2]) - math.log(v[1])) / (2 * 1.96) for k, v in HR_PER_SD.items()}
    use_loc = calib.get("location_hr", True)
    hr = location_hr(z_same, z_poi) if use_loc else 1.0
    b, k0 = calib["buffer_months"], calib.get("kappa", 1.0)
    base3 = calib["official"]["s3"]
    caps = rent_capacity(calib["theta"], calib["sigma"], k0, rel_demand, draws, 36, b)
    boots = []
    for bt in calib["boot"]:
        th, sg, kp = (list(bt) + [k0])[:3]
        boots.append((sorted(rent_capacity(th, sg, kp, rel_demand, draws, 36, b)),
                      _boot_hr(rng, nd, se, z_same, z_poi, hr) if use_loc else 1.0))
    targets = {"city": base3, **{f"{round(100 * t)}": t for t in RENT_TARGETS}}
    ceilings = {}
    for key, t in targets.items():
        pt = rent_ceiling(caps, t, hr)
        lo, hi = _interval([_ceiling_sorted(c, t, h) for c, h in boots])
        ceilings[key] = {"target": round(t, 4), "rent_max": round(pt, -1), "ci90": [round(lo, -1), round(hi, -1)],
                         "per_m2": round(pt / surface_m2, 1) if surface_m2 else None}
    chosen = ceilings["city"] if target is None else {
        "target": target, "rent_max": round(rent_ceiling(caps, target, hr), -1),
        "ci90": [round(v, -1) for v in _interval([_ceiling_sorted(c, target, h) for c, h in boots])],
        "per_m2": round(rent_ceiling(caps, target, hr) / surface_m2, 1) if surface_m2 else None}
    top = max(ceilings["70"]["rent_max"] * 1.4, (asking or 0) * 1.3, 1000.0)
    curve = []
    for i in range(curve_points):
        rent = top * i / (curve_points - 1)
        vals = [_surv_sorted(c, rent) ** h for c, h in boots]
        lo, hi = _interval(vals)
        curve.append([round(rent), round(survival_at_rent(caps, rent, hr), 4), round(lo, 4), round(hi, 4)])
    out = {"group": calib["group"], "currency": calib["currency"], "city_s3": base3, "location_hr": round(hr, 3),
           "target": chosen, "ceilings": ceilings, "curve": curve, "surface_m2": surface_m2}
    if asking is not None:
        p = survival_at_rent(caps, asking, hr)
        d = 100 * (p - base3)
        if abs(d) < 0.5:
            rel_en, rel_fr = "at the city norm", "à la norme de la ville"
        else:
            rel_en = f"{abs(d):.0f} points {'below' if d < 0 else 'above'} the city norm"
            rel_fr = f"{abs(d):.0f} points {'sous' if d < 0 else 'au-dessus de'} la norme de la ville"
        out["asking"] = {"rent": asking, "p_open_3y": round(p, 3), "points_vs_city": round(d, 1),
                         "text_en": f"At {asking:,.0f} {calib['currency']} you are {rel_en} "
                                    f"({100 * p:.0f}% vs {100 * base3:.0f}% open at 3 years)",
                         "text_fr": f"À {asking:,.0f} {calib['currency']} vous êtes {rel_fr} "
                                    f"({100 * p:.0f} % contre {100 * base3:.0f} % ouverts à 3 ans)".replace(",", " ")}
    return out


def _surv_sorted(sorted_caps, rent):
    """share(caps >= rent) on sorted capacities."""
    lo, hi = 0, len(sorted_caps)
    while lo < hi:
        mid = (lo + hi) // 2
        if sorted_caps[mid] < rent:
            lo = mid + 1
        else:
            hi = mid
    return (len(sorted_caps) - lo) / len(sorted_caps)


def _ceiling_sorted(sorted_caps, target, hr):
    """Same R* as rent_ceiling, read off the sorted capacities: the k-th largest capacity with
    (k / N)^hr >= target."""
    n = len(sorted_caps)
    k = math.ceil(n * target ** (1 / hr) - 1e-9)
    if k <= 0:
        return sorted_caps[-1]
    if k > n:
        return sorted_caps[0] - 1.0
    return sorted_caps[n - k]


def breakeven_card(relative_demand, rents, city_key, profile, draws=2000, seed=20261011):
    """Everything the unit card needs, or None when there are no defaults for the city."""
    d = defaults(city_key, profile)
    if d is None:
        return None
    return {"label": "SCENARIO MODEL - not a forecast", "currency": d["currency"],
            "relative_demand": round(relative_demand, 3), "inputs": d["inputs"],
            "by_rent": [scenario(relative_demand, r, d["inputs"], draws, seed) for r in rents],
            "sensitivity": sensitivity(relative_demand, rents[0], d["inputs"], seed=seed)}
