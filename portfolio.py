"""Stable Street: a tenant mix whose negotiable-rent surplus is an exact potential.

Monderer and Shapley 1996; Bogomolnaia and Jackson 2002. Existence with externalities is prior
work (Pycia and Yenmez, UZH econ WP 392), as are mall deferred acceptance with transfers, Osawa
et al. (arXiv 2011.06778) and Prendergast, Gould and Pashigian (CEPR DP3598). This file does not
claim that theory. It estimates an uncalibrated neighbour matrix from this run's audience overlap,
reports when fixed rents have no stable assignment, and counts the moves in the certificate.
Standard library only. No extra Qloo call.
"""
import itertools
import math

import decision

# Stated, uncalibrated map. Same category cannibalises; a different category is the complement proxy.
EFFECT_SCALE = 25.0
COMPLEMENT_SHARE = 0.5
VERDICT_SHIFT = {"GO": 0.0, "ADJUST": -15.0, "NO-GO": -40.0}
MAX_UNITS = 5
MIN_UNITS = 3
EPS = 1e-9
RENT_FLEXIBILITY = "This street needs rent flexibility to settle."
# One currency unit per surplus point per month. Stated and uncalibrated. Not a market rent.
POINT_TO_RENT = 1.0
RENT_SPLIT_LABEL = "suggested, uncalibrated"


def line_edges(n):
    """Typed rent order, as a line. About 100 m, or one mall floor: the distance is not measured."""
    return [(i, i + 1) for i in range(max(0, n - 1))]


def _call(concept, rent):
    afford = decision.affordability(concept.get("rent_row"), rent)
    if concept.get("rent_first"):
        return decision.verdict(afford, bool(concept.get("strong")), bool(concept.get("avoid")))
    score = concept.get("fit") or 0
    call = "GO" if score >= 65 else "ADJUST" if score >= 45 else "NO-GO"
    if afford == "over":
        return "NO-GO"
    if afford == "stretch" and call == "GO":
        return "ADJUST"
    return call


def standalone(concept, rent):
    """v(t, u): this run's fit score plus the rent-first shift at that unit's asking rent."""
    return float(concept.get("fit") or 0) + VERDICT_SHIFT.get(_call(concept, rent), 0.0)


def neighbour_effect(tenant, other):
    """Uncalibrated a(t, s). Scales with the neighbour's fit, so a(t, s) and a(s, t) can differ.

    p = min(overlap_t, overlap_s) / 100, or 0 when either overlap is missing.
    f_s = fit_s / 100.
    Same category: -EFFECT_SCALE * p * f_s. Different category: +EFFECT_SCALE * COMPLEMENT_SHARE * p * f_s.
    """
    left, right = tenant.get("overlap"), other.get("overlap")
    if left is None or right is None:
        return 0.0
    share = min(float(left), float(right)) / 100.0
    neighbour_fit = float(other.get("fit") or 0) / 100.0
    magnitude = EFFECT_SCALE * share * neighbour_fit
    same = (tenant.get("category") or "other") == (other.get("category") or "other")
    return -magnitude if same else magnitude * COMPLEMENT_SHARE


def effect_matrix(concepts):
    n = len(concepts)
    matrix = [[0.0] * n for _ in range(n)]
    for i, tenant in enumerate(concepts):
        for j, other in enumerate(concepts):
            if i != j:
                matrix[i][j] = neighbour_effect(tenant, other)
    return matrix


def symmetric_part(matrix):
    n = len(matrix)
    out = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(n):
            out[i][j] = (matrix[i][j] + matrix[j][i]) / 2.0
    return out


def surplus(assign, values, effects, edges):
    """S(μ) = Σ v + Σ_{occupied edges} (a(t,s) + a(s,t)). The one-sided part cancels."""
    total = 0.0
    for unit, tenant in enumerate(assign):
        if tenant is not None:
            total += values[tenant][unit]
    for left, right in edges:
        tenant, other = assign[left], assign[right]
        if tenant is not None and other is not None:
            total += effects[tenant][other] + effects[other][tenant]
    return total


def utility(tenant, unit, assign, values, effects, edges):
    """U_t(u) given μ. Neighbours are the other occupied units, not the occupant of u."""
    total = values[tenant][unit]
    for left, right in edges:
        other_unit = right if left == unit else left if right == unit else None
        if other_unit is None:
            continue
        other = assign[other_unit]
        if other is not None:
            total += effects[tenant][other]
    return total


def total_utility(assign, values, effects, edges):
    return sum(utility(tenant, unit, assign, values, effects, edges)
               for unit, tenant in enumerate(assign) if tenant is not None)


def _replace(assign, unit, tenant):
    nxt = list(assign)
    nxt[unit] = tenant
    return nxt


def _swap(assign, left, right):
    nxt = list(assign)
    nxt[left], nxt[right] = nxt[right], nxt[left]
    return nxt


def improving_moves(assign, values, effects, edges):
    """Replacements and swaps that strictly raise street surplus. Empty means the certificate holds."""
    base = surplus(assign, values, effects, edges)
    used = {tenant for tenant in assign if tenant is not None}
    occupied = [unit for unit, tenant in enumerate(assign) if tenant is not None]
    moves = []
    for tenant in range(len(values)):
        if tenant in used:
            continue
        for unit in occupied:
            nxt = _replace(assign, unit, tenant)
            if surplus(nxt, values, effects, edges) > base + EPS:
                moves.append(("replace", unit, tenant))
    for index, left in enumerate(occupied):
        for right in occupied[index + 1:]:
            nxt = _swap(assign, left, right)
            if surplus(nxt, values, effects, edges) > base + EPS:
                moves.append(("swap", left, right))
    return moves


def check_counts(assign, n_tenants):
    """How many replacements and swaps a verifier has to look at. O(units x tenants + units^2)."""
    occupied = sum(1 for tenant in assign if tenant is not None)
    unmatched = n_tenants - len({tenant for tenant in assign if tenant is not None})
    return unmatched * occupied, occupied * (occupied - 1) // 2


def greedy(values):
    """Each unit, in order, takes the free tenant with the highest stand-alone value."""
    n_units = len(values[0]) if values else 0
    used = set()
    assign = [None] * n_units
    for unit in range(n_units):
        best, best_value = None, None
        for tenant in range(len(values)):
            if tenant in used:
                continue
            value = values[tenant][unit]
            if best is None or value > best_value:
                best, best_value = tenant, value
        if best is not None:
            assign[unit] = best
            used.add(best)
    return assign


def local_search(values, effects, edges):
    """Stop when surplus cannot rise. Each accepted move is a strict increase, and the set is finite."""
    assign = greedy(values)
    history = [surplus(assign, values, effects, edges)]
    for _ in range(1000):
        moves = improving_moves(assign, values, effects, edges)
        if not moves:
            break
        kind, left, right = moves[0]
        assign = _replace(assign, left, right) if kind == "replace" else _swap(assign, left, right)
        history.append(surplus(assign, values, effects, edges))
    replacements, swaps = check_counts(assign, len(values))
    return {"assignment": assign, "history": history, "replacements": replacements, "swaps": swaps}


def _fillings(n_units, n_tenants):
    width = min(n_units, n_tenants)
    if width == 0:
        yield [None] * n_units
        return
    for slots in itertools.combinations(range(n_units), width):
        for tenants in itertools.permutations(range(n_tenants), width):
            assign = [None] * n_units
            for unit, tenant in zip(slots, tenants):
                assign[unit] = tenant
            yield assign


def swap_blocks(assign, left, right, values, effects, edges):
    """Fixed rents: both movers must strictly gain. Neighbours do not have to consent."""
    tenant, other = assign[left], assign[right]
    if tenant is None or other is None:
        return False
    before_tenant = utility(tenant, left, assign, values, effects, edges)
    before_other = utility(other, right, assign, values, effects, edges)
    nxt = _swap(assign, left, right)
    return (utility(other, left, nxt, values, effects, edges) > before_other + EPS
            and utility(tenant, right, nxt, values, effects, edges) > before_tenant + EPS)


def replacement_blocks(assign, unit, tenant, values, effects, edges):
    """Fixed rents: an outsider blocks when their utility at the unit beats the incumbent's."""
    incumbent = assign[unit]
    if incumbent is None or tenant in assign:
        return False
    before = utility(incumbent, unit, assign, values, effects, edges)
    nxt = _replace(assign, unit, tenant)
    return utility(tenant, unit, nxt, values, effects, edges) > before + EPS


def ntu_stable(assign, values, effects, edges):
    occupied = [unit for unit, tenant in enumerate(assign) if tenant is not None]
    for index, left in enumerate(occupied):
        for right in occupied[index + 1:]:
            if swap_blocks(assign, left, right, values, effects, edges):
                return False
    used = {tenant for tenant in assign if tenant is not None}
    for tenant in range(len(values)):
        if tenant in used:
            continue
        for unit in occupied:
            if replacement_blocks(assign, unit, tenant, values, effects, edges):
                return False
    return True


def ntu_stable_exists(values, effects, edges):
    n_units = len(values[0]) if values else 0
    return any(ntu_stable(assign, values, effects, edges) for assign in _fillings(n_units, len(values)))


def _role(deviation):
    """Above the street average pays less (anchor). Below it pays more (free-rider)."""
    if deviation > EPS:
        return "anchor"
    if deviation < -EPS:
        return "free-rider"
    return "neutral"


def roles_for(contributions):
    occupied = [value for value in contributions if value is not None]
    mean = sum(occupied) / len(occupied) if occupied else 0.0
    return [None if value is None else _role(value - mean) for value in contributions]


def neighbour_contributions(assignment, effects, edges):
    """Symmetric part of a, summed over occupied neighbours. Positive means the tenant raises their value."""
    sym = symmetric_part(effects)
    out = [None] * len(assignment)
    for unit, tenant in enumerate(assignment):
        if tenant is None:
            continue
        total = 0.0
        for left, right in edges:
            other_unit = right if left == unit else left if right == unit else None
            if other_unit is None:
                continue
            other = assignment[other_unit]
            if other is not None:
                total += sym[tenant][other]
        out[unit] = total
    return out


def suggested_rents(rents, contributions):
    """Fixed monthly rents. Anchors pay less, free-riders more, and the sum stays the asking total.

    The shift is POINT_TO_RENT currency units per surplus point above or below the street average.
    It shrinks if a suggested rent would not stay positive. There is no interest and no discounting.
    """
    cents = [int(round(float(rent) * 100)) for rent in rents]
    occupied = [i for i, value in enumerate(contributions) if value is not None]
    unchanged = [cents[i] / 100.0 for i in range(len(cents))]
    if len(occupied) < 2:
        return unchanged, POINT_TO_RENT
    mean = sum(contributions[i] for i in occupied) / len(occupied)
    scale = POINT_TO_RENT
    for _ in range(24):
        shifts = [0] * len(cents)
        for i in occupied[:-1]:
            shifts[i] = int(round(-(contributions[i] - mean) * scale * 100))
        shifts[occupied[-1]] = -sum(shifts[i] for i in occupied[:-1])
        trial = [cents[i] + shifts[i] for i in range(len(cents))]
        if all(trial[i] > 0 for i in occupied):
            return [value / 100.0 for value in trial], scale
        scale /= 2.0
    return unchanged, 0.0


def fixed_rent_note(values, effects, edges):
    if ntu_stable_exists(values, effects, edges):
        return ""
    return RENT_FLEXIBILITY


def _proof(replacements, swaps, needs_flexibility):
    text = (
        f"No improving move: {replacements} replacements and {swaps} swaps checked. "
        "Neighbour effects are an uncalibrated map of this run's audience overlap "
        "(same category cannibalises, a different category is treated as complementary footfall, "
        "and the effect scales with the neighbour's fit); no extra Qloo call. "
        "Units are treated as a line of neighbours, about 100 m apart or on one mall floor; "
        "that distance is not measured. "
        "With negotiable rent only the symmetric part of those effects changes the total. "
        "The 2023-2024 Chicago backtest found no measurable gain, so this is not a survival forecast."
    )
    if needs_flexibility:
        text += " " + RENT_FLEXIBILITY
    return text


def assign(units, concepts, k=1):
    """Certificate for 3 to 5 units. k is accepted and is not a diversity cap; cannibalisation is a(t, s)."""
    units = list(units)[:MAX_UNITS]
    k = max(1, int(k))
    empty = {"matches": [], "k": k, "blocking": [], "needs_rent_flexibility": False,
             "replacements": 0, "swaps": 0, "steps": [],
             "proof": "No street certificate: a street needs at least three rents and one scored concept. No extra Qloo call."}
    if len(units) < MIN_UNITS or not concepts:
        return empty
    values = [[standalone(concept, unit["rent"]) for unit in units] for concept in concepts]
    effects = effect_matrix(concepts)
    edges = line_edges(len(units))
    found = local_search(values, effects, edges)
    needs = fixed_rent_note(values, effects, edges) == RENT_FLEXIBILITY
    contributions = neighbour_contributions(found["assignment"], effects, edges)
    asking = [unit["rent"] for unit in units]
    suggested, applied_scale = suggested_rents(asking, contributions)
    roles = roles_for(contributions)
    rows = []
    split_rows = []
    for index, unit in enumerate(units):
        tenant = found["assignment"][index]
        concept = concepts[tenant] if tenant is not None else None
        rows.append({"unit": unit["id"], "rent": unit["rent"],
                     "concept": concept["id"] if concept else None,
                     "verdict": _call(concept, unit["rent"]) if concept else None,
                     "category": (concept.get("category") or "other") if concept else None,
                     "fit": concept.get("fit") if concept else None})
        if contributions[index] is None:
            continue
        split_rows.append({"unit": unit["id"], "concept": concept["id"] if concept else None,
                           "contribution": contributions[index], "role": roles[index],
                           "asking": float(unit["rent"]), "suggested": suggested[index]})
    return {"matches": rows, "k": k, "blocking": [], "needs_rent_flexibility": needs,
            "replacements": found["replacements"], "swaps": found["swaps"], "steps": found["history"],
            "proof": _proof(found["replacements"], found["swaps"], needs),
            "rents": {unit["id"]: unit["rent"] for unit in units},
            "rent_split": {"label": RENT_SPLIT_LABEL, "rows": split_rows,
                           "total_asking": sum(float(rent) for rent in asking),
                           "total_suggested": sum(suggested),
                           "applied_scale": applied_scale,
                           "note": _split_note(applied_scale)}}


def _split_note(applied_scale):
    note = ("Each figure is the symmetric part of the neighbour effect on the tenants next door. "
            "An anchor is above the street average and pays less. A free-rider is below it and pays more. "
            "The shift starts at one currency unit per surplus point and is recentered so the monthly total "
            "stays the asking total. Fixed rents only: no interest and no discounting.")
    if applied_scale < POINT_TO_RENT:
        note += " Shrunk to keep every rent positive."
    return note


MAX_CONCEPTS = 8


def _clean_name(value):
    name = str(value or "").strip()
    if not name or len(name) > 80 or any(ord(ch) < 32 for ch in name):
        raise ValueError("each concept needs an id")
    return name


def _finite_score(value, name):
    if not math.isfinite(value) or not 0 <= value <= 100:
        raise ValueError(f"{name} must be a finite number from 0 to 100")
    return value


def _clean_concept(raw):
    if not isinstance(raw, dict):
        raise ValueError("each concept needs an id")
    try:
        fit = _finite_score(float(0 if raw.get("fit") is None else raw.get("fit")), "fit")
    except (TypeError, ValueError, OverflowError):
        raise ValueError("fit must be a finite number from 0 to 100") from None
    overlap = raw.get("overlap")
    if overlap is not None:
        try:
            overlap = _finite_score(float(overlap), "overlap")
        except (TypeError, ValueError, OverflowError):
            raise ValueError("overlap must be a finite number from 0 to 100") from None
    rent_row = raw.get("rent_row") if isinstance(raw.get("rent_row"), dict) else None
    return {"id": _clean_name(raw.get("id")), "category": str(raw.get("category") or "other")[:40],
            "fit": fit, "overlap": overlap, "strong": bool(raw.get("strong")), "avoid": bool(raw.get("avoid")),
            "rent_first": bool(raw.get("rent_first")), "rent_row": rent_row}


def _clean_units(raw):
    if not isinstance(raw, list) or not MIN_UNITS <= len(raw) <= MAX_UNITS:
        raise ValueError("a street needs 3 to 5 rents")
    units = []
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("each unit needs an id and a rent")
        try:
            rent = float(item.get("rent"))
        except (TypeError, ValueError, OverflowError):
            raise ValueError("each unit needs a positive monthly rent") from None
        if not (0 < rent < 1e7):
            raise ValueError("each unit needs a positive monthly rent")
        units.append({"id": _clean_name(item.get("id")), "rent": rent})
    return units


def placed_utilities(units, concepts, matches):
    """Utility of each placed concept under the published assignment. No Qloo call."""
    if not concepts or not matches or len(matches) != len(units):
        return {}
    index = {concept["id"]: i for i, concept in enumerate(concepts)}
    assignment = []
    for match in matches:
        concept = match.get("concept")
        assignment.append(index[concept] if concept in index else None)
    values = [[standalone(concept, unit["rent"]) for unit in units] for concept in concepts]
    effects = effect_matrix(concepts)
    edges = line_edges(len(units))
    out = {}
    for unit, tenant in enumerate(assignment):
        if tenant is None:
            continue
        out[concepts[tenant]["id"]] = utility(tenant, unit, assignment, values, effects, edges)
    return out


def what_if(units, concepts, remove=None, add=None):
    """Re-solve the street after adding or removing one tenant. Uses only the concepts passed in."""
    if not isinstance(concepts, list):
        raise ValueError("concepts must be a list")
    units = _clean_units(units)
    concepts = [_clean_concept(concept) for concept in concepts]
    if len(concepts) > MAX_CONCEPTS:
        raise ValueError("at most 8 concepts")
    remove = _clean_name(remove) if remove else ""
    if isinstance(add, dict):
        add_name = _clean_name(add.get("id")) if add.get("id") else ""
    else:
        add_name = _clean_name(add) if add else ""
    if bool(remove) == bool(add_name):
        raise ValueError("add or remove one tenant")
    before = assign(units, concepts)
    added_note = ""
    if remove:
        if remove not in {concept["id"] for concept in concepts}:
            raise ValueError("that tenant is not in this run")
        nxt = [concept for concept in concepts if concept["id"] != remove]
    else:
        if add_name in {concept["id"] for concept in concepts}:
            raise ValueError("that tenant is already in this run")
        if len(concepts) >= MAX_CONCEPTS:
            raise ValueError("at most 8 concepts")
        fresh = dict(add) if isinstance(add, dict) else {"id": add_name}
        fresh["id"] = add_name
        if fresh.get("fit") is None and fresh.get("overlap") is None:
            fresh["fit"] = 0
            fresh["overlap"] = None
            fresh.setdefault("category", "other")
            added_note = ("No taste data in this run, so this concept's fit and neighbour effects are 0. "
                          "No extra Qloo call.")
        nxt = concepts + [_clean_concept(fresh)]
    after = assign(units, nxt)
    before_values = placed_utilities(units, concepts, before.get("matches") or [])
    after_values = placed_utilities(units, nxt, after.get("matches") or [])
    names = set(before_values) | set(after_values)
    if add_name:
        names.add(add_name)
    if remove:
        names.add(remove)
    changes = []
    for name in sorted(names):
        old, new = before_values.get(name), after_values.get(name)
        changes.append({"concept": name, "before": old, "after": new,
                        "delta": None if old is None or new is None else new - old})
    return {"before": before, "after": after, "changes": changes, "qloo_calls": 0, "added_note": added_note}
