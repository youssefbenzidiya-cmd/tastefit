"""Stable matching of vacant units to concepts. Gale and Shapley 1962. Standard library only.

Landlords (units) propose. A unit prefers a better rent-first verdict, then a higher fit score.
A concept prefers a higher fit score, then a lower asking rent. Fit scores are reused from one
run, so this makes no Qloo call. At most k concepts of one category may be matched on the street.
"""
import decision

CALL_RANK = {"GO": 0, "ADJUST": 1, "NO-GO": 2}
MAX_UNITS = 5


def deferred_acceptance(unit_prefs, concept_prefs, category=None, k=1):
    """{unit: concept or None}. Unit-proposing deferred acceptance with a per-category cap."""
    category = category or {}
    k = max(1, int(k))
    free = [u for u, prefs in unit_prefs.items() if prefs]
    nxt = {u: 0 for u in unit_prefs}
    hold = {}
    matched = {u: None for u in unit_prefs}
    rank = {c: {u: i for i, u in enumerate(prefs)} for c, prefs in concept_prefs.items()}

    def counts():
        out = {}
        for c in hold:
            cat = category.get(c, "")
            out[cat] = out.get(cat, 0) + 1
        return out

    steps = sum(len(p) for p in unit_prefs.values()) + 1
    while free and steps:
        steps -= 1
        u = free.pop(0)
        prefs = unit_prefs[u]
        i = nxt[u]
        if i >= len(prefs):
            continue
        nxt[u] = i + 1
        c = prefs[i]
        if c not in rank or u not in rank[c]:
            free.append(u)
            continue
        current = hold.get(c)
        cat = category.get(c, "")
        if current is None and counts().get(cat, 0) >= k:
            free.append(u)
            continue
        if current is None or rank[c][u] < rank[c][current]:
            if current is not None:
                matched[current] = None
                free.append(current)
            hold[c] = u
            matched[u] = c
        else:
            free.append(u)
    return matched


def blocking_pairs(matched, unit_prefs, concept_prefs, category=None, k=1):
    """Pairs that both prefer each other to their match without breaking the category cap."""
    category = category or {}
    k = max(1, int(k))
    concept_match = {c: u for u, c in matched.items() if c}
    counts = {}
    for c in concept_match:
        cat = category.get(c, "")
        counts[cat] = counts.get(cat, 0) + 1
    rank_c = {c: {u: i for i, u in enumerate(prefs)} for c, prefs in concept_prefs.items()}
    blocks = []
    for u, prefs in unit_prefs.items():
        current = matched.get(u)
        for c in prefs:
            if c == current:
                break
            order = rank_c.get(c) or {}
            if u not in order:
                continue
            other = concept_match.get(c)
            if other is not None and order[u] > order[other]:
                continue
            cat = category.get(c, "")
            new = counts.get(cat, 0)
            if other is None:
                new += 1
            if current and category.get(current, "") == cat:
                new -= 1
            if new > k:
                continue
            blocks.append((u, c))
    return blocks


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


def assign(units, concepts, k=1):
    """Match up to 5 units. Returns matches, the proof line, and any blocking pairs (none when stable)."""
    units = list(units)[:MAX_UNITS]
    k = max(1, int(k))
    if len(units) < 2 or not concepts:
        return {"matches": [], "k": k, "blocking": [],
                "proof": "No matching: a street needs at least two rents and one scored concept. No extra Qloo call."}
    category = {c["id"]: c.get("category") or "other" for c in concepts}
    unit_prefs = {}
    for u in units:
        ranked = sorted(concepts, key=lambda c: (CALL_RANK[_call(c, u["rent"])], -(c.get("fit") or 0), c["id"]))
        unit_prefs[u["id"]] = [c["id"] for c in ranked]
    concept_prefs = {}
    for c in concepts:
        ranked_units = sorted(units, key=lambda u: (-(c.get("fit") or 0), u["rent"], u["id"]))
        concept_prefs[c["id"]] = [u["id"] for u in ranked_units]
    matched = deferred_acceptance(unit_prefs, concept_prefs, category, k)
    blocks = blocking_pairs(matched, unit_prefs, concept_prefs, category, k)
    by_id = {c["id"]: c for c in concepts}
    rent_of = {u["id"]: u["rent"] for u in units}
    rows = []
    for u in units:
        cid = matched.get(u["id"])
        concept = by_id.get(cid) if cid else None
        rows.append({"unit": u["id"], "rent": u["rent"], "concept": cid,
                     "verdict": _call(concept, u["rent"]) if concept else None,
                     "category": category.get(cid) if cid else None,
                     "fit": concept.get("fit") if concept else None})
    proof = ("No blocking pair under Gale and Shapley (1962), with at most "
             f"{k} of one category on this street. Fit scores are reused from this run; no extra Qloo call. "
             "The 2023-2024 Chicago backtest found no measurable gain, so stability is not a survival forecast."
             if not blocks else
             "Blocking pair remains: " + ", ".join(f"{u} with {c}" for u, c in blocks) + ".")
    return {"matches": rows, "k": k, "blocking": [{"unit": u, "concept": c} for u, c in blocks], "proof": proof,
            "rents": rent_of}
