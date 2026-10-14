"""Survivorship check and split-conformal intervals for the Chicago backtest. Standard library only.

Heckman (1979) is not estimated. His correction needs a selection equation and an exclusion restriction:
something that changes who is observed and does not change the outcome except through that. The City of
Chicago inspections file does not have one. A licence is absent from the survivor count because it closed,
because it was not inspected again after April 2025, or because its name matched no catalog keyword.
Inspection effort and cuisine are the same kinds of facts that enter survival, and the keyword rule is
the label itself. There is also no file of applications that never opened.

The published hit rate counts survivors only. Inverse-probability weights (Horvitz and Thompson 1952)
reweight those survivors. Assumptions, stated and not tested:

- The propensity in a concept-by-area cell is survivors / openings, saturated on the cells in the table.
- A cell with openings and no survivor has propensity 0. It is dropped (positivity fails). Its openings
  are counted in `dropped_for_positivity` and are not given an invented survival.
- Weights apply only to keyword-matched licences already in the register. They do not stand in for
  businesses that never received a licence inspection.
- The propensity is computed from the same table that is scored. It is a reweighting of this sample,
  not a model fitted on other cities.

Where every cell with an opening also has a survivor, the weighted total equals the opening count, so
the IPW hit rate is the share of openings (not survivors) whose concept is in the top k.

Split conformal intervals follow Vovk, Gammerman and Shafer (Algorithmic Learning in a Random World,
2005). Residuals are absolute errors on a calibration half. The quantile uses the finite-sample rank
ceil((n+1)(1-alpha)). The interval is that quantile around the score. It is computed for the leave-one-area
citywide survivor share, because a Qloo fit score is not stored and is not recomputed here.
"""
import math
import random

ALPHA = 0.1

HECKMAN_REASON = (
    "Not identified. Heckman (1979) needs an exclusion restriction, and the Chicago inspections file "
    "has none: closing, a missing re-inspection, and a keyword miss are not separable from the survival "
    "outcome, and applications that never opened are not in the file."
)


def heckman():
    return {"identified": False, "reason": HECKMAN_REASON}


def ipw_hit(order, row, k=3):
    """(weighted survivors in the top k, weighted survivors, openings dropped because propensity is 0)."""
    top = set(order[:k])
    weighted_top = weighted = 0.0
    dropped = 0
    for concept, counts in row.items():
        opened, survived = counts
        if opened <= 0:
            continue
        if survived <= 0:
            dropped += opened
            continue
        weight = opened / survived
        weighted += survived * weight
        if concept in top:
            weighted_top += survived * weight
    return weighted_top, weighted, dropped


def add_ipw(methods_row, table, orders, areas, k=3):
    """Attach IPW hit rates onto a `score` result. `methods_row` is mutated and returned."""
    for method, by_area in orders.items():
        if method not in methods_row:
            continue
        top = total = 0.0
        dropped = 0
        for area in areas:
            w_top, w_all, drop = ipw_hit(by_area[area], table[area], k)
            top += w_top
            total += w_all
            dropped += drop
        methods_row[method]["ipw_hit_at_k"] = round(top / total, 3) if total else None
        methods_row[method]["ipw_openings"] = round(total, 3)
        methods_row[method]["dropped_for_positivity"] = dropped
    return methods_row


def prior_pairs(table, concepts):
    """(citywide leave-one-area survivor share, this area's survival rate) for cells with an opening."""
    pairs = []
    for area, row in table.items():
        totals = {c: 0 for c in concepts}
        for other, other_row in table.items():
            if other == area:
                continue
            for concept, counts in other_row.items():
                totals[concept] += counts[1]
        denom = sum(totals.values())
        for concept in concepts:
            opened, survived = row[concept]
            if opened <= 0:
                continue
            share = (totals[concept] / denom) if denom else 0.0
            pairs.append((share, survived / opened))
    return pairs


def split_conformal(pairs, alpha=ALPHA, seed=7):
    """90% split-conformal interval around the score, by default. None when a half is empty or the
    quantile rank falls past the calibration sample (the interval would be infinite)."""
    if len(pairs) < 2 or not 0 < alpha < 1:
        return None
    rng = random.Random(seed)
    rows = list(pairs)
    rng.shuffle(rows)
    cut = len(rows) // 2
    cal, test = rows[:cut], rows[cut:]
    if not cal or not test:
        return None
    residuals = sorted(abs(outcome - score) for score, outcome in cal)
    rank = math.ceil((len(cal) + 1) * (1 - alpha))
    if rank > len(cal):
        return {"alpha": alpha, "n_cal": len(cal), "n_test": len(test), "quantile": None,
                "coverage": None, "mean_width": None,
                "note": "The calibration half is too small for a finite interval at this alpha."}
    q = residuals[rank - 1]
    covered = sum(1 for score, outcome in test if abs(outcome - score) <= q)
    return {"alpha": alpha, "n_cal": len(cal), "n_test": len(test), "quantile": round(q, 3),
            "coverage": round(covered / len(test), 3), "mean_width": round(2 * q, 3),
            "note": ("Split conformal interval (Vovk, Gammerman and Shafer 2005) around the leave-one-area "
                     "citywide survivor share. This is not an interval for a stored Qloo fit score.")}
