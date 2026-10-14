"""A 4 to 8 week pop-up before a lease. Monthly figures only: no interest and no discounting.

The length is a stated rule inside that band. The success line is the mode break-even of the
rent-first ceiling on the check that value of information ranks first. The plan has its own
SHA-256. It is not part of the five-field prediction fingerprint.
"""
import cityscan_breakeven as cb
import decision
import ledger

WEEKS = (4, 6, 8)
AT_LEAST = {"covers_day", "ticket", "days"}
AT_MOST = {"staff", "other_fixed", "cogs", "other_var"}


def central(spec):
    """The stated centre of an input. Triangular mode, or the lognormal median. Not a fitted value."""
    if spec["dist"] == "tri":
        return float(spec["mode"])
    if spec["dist"] == "lognormal":
        return float(spec["median"])
    raise ValueError("unknown input distribution")


def _values(inputs):
    return {k: central(inputs[k]) for k in cb.PARAM_ORDER}


def threshold_for(check, inputs, relative_demand, rent):
    """(threshold, direction) so mode monthly profit is zero at `rent`. None when it cannot be solved."""
    v = _values(inputs)
    covers = v["covers_avg"] * relative_demand * v["demand_mult"]
    margin = 1 - v["cogs"] - v["other_var"]
    fixed = rent + v["staff"] + v["other_fixed"]
    revenue = covers * v["ticket"] * v["days"]
    if check == "covers_day":
        denom = v["ticket"] * v["days"] * margin
        return (None if denom <= 0 else (fixed / denom, "at_least"))
    if check == "ticket":
        denom = covers * v["days"] * margin
        return (None if denom <= 0 else (fixed / denom, "at_least"))
    if check == "days":
        denom = covers * v["ticket"] * margin
        return (None if denom <= 0 else (fixed / denom, "at_least"))
    if check == "staff":
        return (covers * v["ticket"] * v["days"] * margin - rent - v["other_fixed"], "at_most")
    if check == "other_fixed":
        return (covers * v["ticket"] * v["days"] * margin - rent - v["staff"], "at_most")
    if check == "cogs":
        if revenue <= 0:
            return None
        return (1 - v["other_var"] - fixed / revenue, "at_most")
    if check == "other_var":
        if revenue <= 0:
            return None
        return (1 - v["cogs"] - fixed / revenue, "at_most")
    return None


def _round_threshold(check, value):
    if check in ("cogs", "other_var"):
        return round(value, 3)
    if check in ("covers_day", "days"):
        return round(value, 1)
    return round(value)


def weeks_for(voi):
    """4, 6 or 8. Stated, with no discount rate: one dominant check is 4 weeks, several are 8."""
    checks = [c for c in (voi or {}).get("checks") or [] if c.get("above_noise")]
    evpi = (voi or {}).get("evpi") or 0
    if len(checks) >= 2:
        return 8
    if checks and evpi and checks[0]["evppi"] * 2 >= evpi:
        return 4
    return 6


def _check_name(voi):
    checks = [c for c in (voi or {}).get("checks") or [] if c.get("above_noise")]
    if checks:
        return checks[0]["input"]
    return "covers_day"


def recommend(area, concept, city_key, rent_row, asking, voi, ts):
    """Plan for the top concept, or None when the ceiling or the stated inputs are missing."""
    if not rent_row or not rent_row.get("viable") or rent_row.get("relative_demand") is None:
        return None
    if rent_row.get("rent_max") is None:
        return None
    group = concept.get("rent_group")
    profile = decision.PROFILE.get(group)
    defaults = cb.defaults(city_key, profile) if profile else None
    if defaults is None:
        return None
    check = _check_name(voi)
    if check not in decision.CHECKS:
        return None
    rent = float(rent_row["rent_max"])
    solved = threshold_for(check, defaults["inputs"], rent_row["relative_demand"], rent)
    if solved is None:
        return None
    raw, direction = solved
    threshold = _round_threshold(check, raw)
    hi = (rent_row.get("ci90") or [None, rent])[1]
    range_threshold = None
    if isinstance(hi, (int, float)) and hi != rent:
        solved_hi = threshold_for(check, defaults["inputs"], rent_row["relative_demand"], float(hi))
        if solved_hi is not None:
            range_threshold = _round_threshold(check, solved_hi[0])
    weeks = weeks_for(voi)
    if weeks not in WEEKS:
        raise ValueError("trial length is outside 4 to 8 weeks")
    plan = {"area": area, "concept": concept["concept"], "check": check, "direction": direction,
            "rent": rent, "threshold": threshold, "weeks": weeks, "ts": ts}
    plan["sha256"] = ledger.trial_fingerprint(plan)
    plan["currency"] = defaults["currency"]
    plan["range_threshold"] = range_threshold
    plan["label"] = decision.CHECKS[check]
    plan["afford"] = decision.affordability(rent_row, asking)
    plan["sentence"] = sentence(plan)
    return plan


def sentence(plan):
    word = "at least" if plan["direction"] == "at_least" else "at most"
    unit = plan.get("currency") or ""
    shown = plan["threshold"]
    if plan["check"] in ("cogs", "other_var"):
        value = f"{shown:.1%}" if isinstance(shown, float) else str(shown)
    elif plan["check"] in ("covers_day", "days"):
        value = str(shown)
    else:
        value = f"{shown:.0f} {unit}".strip()
    text = (f"Test before you lease: a {plan['weeks']}-week pop-up for {plan['concept']}. "
            f"Success is {plan.get('label') or plan['check']} {word} {value}, "
            f"the mode break-even at the rent-first ceiling of {plan['rent']:.0f} {unit}/month.")
    if plan.get("range_threshold") is not None:
        text += f" The top of the ceiling's 90% range would move that line to {plan['range_threshold']}."
    text += (" Monthly figures, no interest and no discounting. "
             f"Trial plan `{plan['sha256']}`. "
             "When the pop-up ends, log the observed value, the weeks run and whether it cleared. "
             "That log is not the five-field prediction fingerprint. "
             "A pop-up is not a vetted address.")
    return text


def judge(plan, observed, weeks_run):
    """An outcome ready to log. It does not write the ledger file."""
    weeks_run = int(weeks_run)
    if weeks_run < 1 or weeks_run > 8:
        raise ValueError("weeks_run must be from 1 to 8")
    observed = float(observed)
    if plan["direction"] == "at_least":
        cleared = observed >= float(plan["threshold"])
    elif plan["direction"] == "at_most":
        cleared = observed <= float(plan["threshold"])
    else:
        raise ValueError("unknown direction")
    outcome = {"cleared": cleared, "observed": observed, "plan_sha256": plan["sha256"], "weeks_run": weeks_run}
    outcome["sha256"] = ledger.trial_outcome_fingerprint(outcome)
    return outcome
