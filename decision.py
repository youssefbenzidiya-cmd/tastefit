"""Decision layer for the tenant match: rent-first verdict, exact Shapley attribution, value of information.
Standard library only.

Verdict (lexicographic, not a weighted sum): an unpayable rent cannot be rescued by taste. Affordability comes from
the City Scan rent ceiling and its 90% range; taste from the Taste Gap quadrant where the run has one.

Shapley (Shapley 1953): the explanatory score sigma(sum beta_k x_k) is split exactly, over all coalitions, into the
contributions of taste over-index (Qloo rank), supply ratio (OpenStreetMap), audience fit (Qloo compare) and rent
headroom (City Scan), against a city-typical baseline. The weights are stated priors, not fitted; the split explains
TasteFit's scoring model, it is not causal. Momentum from `trends` is left out: it covers one reference brand, not
each concept.

Value of information (Howard 1966): monthly profit draws at the asking rent from TasteFit's stated cost inputs give
EVPI = E[max(pi, 0)] - max(E[pi], 0), the most a perfect forecast could be worth per month for the sign / walk
decision, and a binned EVPPI per input that ranks the checks worth doing before signing. Monthly profit only: no
discounting, no interest.
"""
import itertools
import math
import random

import cityscan_breakeven as cb

# ---------- verdict ----------

def affordability(rent_row, asking):
    """'ok' | 'stretch' | 'over', or None when there is no ceiling or no asking rent."""
    if rent_row is None:
        return None
    if not rent_row.get("viable"):
        return "over"
    if asking is None:
        return None
    hi = (rent_row.get("ci90") or [None, rent_row["rent_max"]])[1]
    if asking > hi:
        return "over"
    return "stretch" if asking > rent_row["rent_max"] else "ok"


def verdict(afford, strong_taste, avoid=False):
    """NO-GO if the rent is over the ceiling's range or the taste quadrant is 'avoid'; GO only with an affordable
    (or unpriced) rent and strong taste evidence; ADJUST otherwise (negotiate, or reposition)."""
    if afford == "over" or avoid:
        return "NO-GO"
    if afford in ("ok", None) and strong_taste:
        return "GO"
    return "ADJUST"


# ---------- Shapley ----------

BETA = {"taste": 1.0, "supply": -1.0, "audience": 1.0, "rent": 1.0}  # stated priors on standardised inputs
SOURCE = {"taste": "Qloo", "supply": "OpenStreetMap", "audience": "Qloo", "rent": "City Scan"}


def sigmoid(z):
    return 1 / (1 + math.exp(-z))


def shapley(f, x, base):
    """Exact Shapley values of f at x against base (dicts feature -> value), over all 2^n coalitions."""
    keys = list(x)
    n = len(keys)
    phi = dict.fromkeys(keys, 0.0)

    def v(s):
        return f({k: (x[k] if k in s else base[k]) for k in keys})
    for k in keys:
        others = [j for j in keys if j != k]
        for r in range(n):
            w = math.factorial(r) * math.factorial(n - r - 1) / math.factorial(n)
            for s in itertools.combinations(others, r):
                phi[k] += w * (v(set(s) | {k}) - v(set(s)))
    return phi


def features(overindex=None, theta=None, overlap=None, overlap_median=None, rent_max=None, asking=None):
    """(x, base) on the model's scale; a feature that was not measured sits at its baseline (phi = 0)."""
    x = {"taste": overindex or 0.0, "supply": math.log(theta) if theta else 0.0,
         "audience": (overlap - overlap_median) / 50 if overlap is not None and overlap_median is not None else 0.0,
         "rent": max(-1.0, min(1.0, (rent_max - asking) / rent_max)) if rent_max and asking is not None else 0.0}
    return x, dict.fromkeys(x, 0.0)


def explain(x, base, beta=BETA):
    """{score, baseline, phi, qloo_share}: scores 0-100, phi in score points, qloo_share of sum |phi|."""
    f = lambda v: 100 * sigmoid(sum(beta[k] * v[k] for k in v))  # noqa: E731
    phi = shapley(f, x, base)
    total = sum(abs(p) for p in phi.values())
    qloo = sum(abs(p) for k, p in phi.items() if SOURCE[k] == "Qloo")
    return {"score": round(f(x), 1), "baseline": round(f(base), 1), "phi": {k: round(p, 1) for k, p in phi.items()},
            "qloo_share": round(qloo / total, 2) if total > 1e-9 else None}


# ---------- value of information ----------

PROFILE = {"restaurant": "restaurant", "fast_food": "fast_casual", "cafe": "cafe"}
CHECKS = {"covers_day": "your real covers per day (door counts at a comparable venue)",
          "ticket": "the average ticket", "staff": "staff cost (payroll quotes)",
          "other_fixed": "utilities, licences and maintenance quotes", "cogs": "food cost share (supplier quotes)",
          "other_var": "card and delivery fees", "days": "opening days per month"}


def evpi(pis):
    m = sum(pis) / len(pis)
    return sum(max(p, 0.0) for p in pis) / len(pis) - max(m, 0.0)


def evppi(pis, xs, bins=20):
    """Binned estimator: draws sorted by the input into equal-count bins, decision taken per bin."""
    order = sorted(range(len(pis)), key=xs.__getitem__)
    size = len(order) // bins
    means = [sum(pis[i] for i in order[b * size:(b + 1) * size]) / size for b in range(bins)]
    m = sum(pis) / len(pis)
    return sum(max(v, 0.0) for v in means) / bins - max(m, 0.0)


def profit_draws(inputs, relative_demand, rent, n=2000, seed=20261011):
    rng = random.Random(seed)
    pis, xs = [], {k: [] for k in CHECKS}
    for _ in range(n):
        v = {k: cb.draw(inputs[k], rng) for k in cb.PARAM_ORDER}
        m = cb.monthly(v, relative_demand, rent)
        pis.append(m["profit"])
        xs["covers_day"].append(m["covers_day"])
        for k in CHECKS:
            if k != "covers_day":
                xs[k].append(v[k])
    return pis, xs


def value_of_information(city_key, group, relative_demand, rent, n=2000, seed=20261011):
    """{currency, evpi, p_profit, checks: [{input, label, evppi}] best first}, or None without stated inputs."""
    d = cb.defaults(city_key, PROFILE.get(group, "")) if group in PROFILE else None
    if d is None or relative_demand is None or rent is None:
        return None
    pis, xs = profit_draws(d["inputs"], relative_demand, rent, n, seed)
    # The binned estimator is biased upward for an input that does not matter; measure that floor with a random key.
    rng = random.Random(seed + 1)
    floor = evppi(pis, [rng.random() for _ in pis])
    checks = sorted(({"input": k, "label": CHECKS[k], "evppi": round(max(0.0, e)), "above_noise": e > 2 * floor}
                     for k, e in ((k, evppi(pis, xs[k])) for k in CHECKS)), key=lambda c: -c["evppi"])
    return {"currency": d["currency"], "evpi": round(evpi(pis)), "noise_floor": round(max(0.0, floor)),
            "p_profit": round(sum(p > 0 for p in pis) / n, 3), "draws": n, "checks": checks}
