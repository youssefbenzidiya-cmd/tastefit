"""Offline backtest: do Qloo's area rankings point at the concepts that actually opened and survived in Chicago?

    python3 backtest_chicago.py [--json OUT.json]

Ground truth is the City of Chicago Food Inspections register (public, no key): a restaurant or bakery "opened" when
its license got its first passing License inspection in 2023-2024, and "survived" when no later inspection says Out of
Business and it was inspected again since SURVIVAL_SEEN. Business names are matched to the ten catalog concepts with
keywords; names that match none are left out.

For each area, three orderings of the ten concepts are scored against that area's survived openings:
  qloo   one rank call: each concept's Chicago benchmark place ranked for the area's audience (the tenant agent's
         demand signal);
  llm    the same LLM TasteFit uses, asked to rank the ten concepts for the area with no data;
  prior  citywide share of survived openings per concept, computed without the area (leave-one-area-out).
Only numbers are printed or written; no Qloo response and no business name is stored.

Caveat: Qloo and the LLM describe today's taste, after these places opened, so this checks agreement with what
survived, not a forecast made in 2023.
"""
import argparse
import collections
import json
import math
import random
import re
import sys
import time
import urllib.parse
import urllib.request

import qloo
import tenant

REGISTER = "https://data.cityofchicago.org/resource/4ijn-s7e5.json"
WINDOW = ("2023-01-01", "2024-12-31")
SURVIVAL_SEEN = "2025-04-01"
RADIUS_KM = 1.2
K = 3
FACILITIES = ("Restaurant", "Bakery")
AREAS = [
    ("Wicker Park", 41.9088, -87.6796), ("Logan Square", 41.9217, -87.7005), ("Pilsen", 41.8564, -87.6560),
    ("Lincoln Park", 41.9214, -87.6513), ("West Loop", 41.8826, -87.6476), ("Hyde Park", 41.7943, -87.5907),
    ("Andersonville", 41.9800, -87.6685), ("Lakeview", 41.9400, -87.6540), ("Bridgeport", 41.8380, -87.6510),
    ("Uptown", 41.9666, -87.6533), ("Chinatown", 41.8517, -87.6338), ("River North", 41.8924, -87.6341),
]
CONCEPTS = [c[0] for c in tenant.CATALOG]
KEYWORDS = {  # first match wins, so the specific patterns come before the broad ones
    "ramen shop": r"\bRAMEN\b",
    "Korean fried chicken": r"BONCHON|KYOCHON|PELICANA|BB\.?Q CHICKEN|KOREAN (FRIED )?CHICKEN|\bCHIMAEK",
    "Vietnamese pho": r"\bPHO\b|VIETNAM|BANH MI",
    "Lebanese grill": r"LEBAN|SHAWARMA|BEIRUT|ZAATAR|ZA'ATAR|MANOUSH",
    "Mediterranean bowls": r"\bCAVA\b|MEDITERRAN|FALAFEL|\bGYROS?\b|\bGREEK\b",
    "salad bowls": r"SALAD|SWEETGREEN|\bCHOPT\b",
    "taqueria": r"TAQUER|\bTACOS?\b|CHIPOTLE|BURRITO",
    "smash burgers": r"BURGER|SHAKE SHACK",
    "specialty coffee": r"COFFEE|ESPRESSO|ROASTER",
    "bakery": r"BAKERY|BAKE SHOP|BAKEHOUSE|PATISS|BOULANG|PANADER",
}
_KEYWORDS = [(c, re.compile(p)) for c, p in KEYWORDS.items()]
LLM_PROMPT = """Rank these food concepts by how likely a new one is to open and stay open in {area}, Chicago.
Concepts: {concepts}
Answer with JSON only: {{"ranking": ["concept", ...]}} using the exact names, best first, all of them."""


def classify(name):
    upper = (name or "").upper()
    return next((c for c, rx in _KEYWORDS if rx.search(upper)), None)


def km(a, b):
    kx = math.cos(math.radians((a[0] + b[0]) / 2))
    return 111.32 * math.hypot(a[0] - b[0], (a[1] - b[1]) * kx)


def openings(rows):
    """{license: {name, lat, lon, opened}} from the full License-inspection history: the licence's first passing
    License inspection falls inside WINDOW. Licences that already passed one before WINDOW are not new openings."""
    out, earlier = {}, set()
    for r in sorted(rows, key=lambda r: r.get("inspection_date") or ""):
        lic, day = r.get("license_"), (r.get("inspection_date") or "")[:10]
        passed = str(r.get("results", "")).startswith("Pass")
        if not lic or lic == "0":
            continue
        if day < WINDOW[0]:
            if passed:
                earlier.add(lic)
            continue
        if day > WINDOW[1] or lic in out or lic in earlier or not passed or r.get("latitude") is None:
            continue
        out[lic] = {"name": r.get("aka_name") or r.get("dba_name") or "", "lat": float(r["latitude"]),
                    "lon": float(r["longitude"]), "opened": day}
    return out


def survived(history, opened, seen=SURVIVAL_SEEN):
    """history: [(date, result)] for one license. Alive = never Out of Business since opening, and inspected since `seen`."""
    later = [(d[:10], res) for d, res in history if d[:10] >= opened]
    if any(res == "Out of Business" for _, res in later):
        return False
    return any(d >= seen for d, _ in later)


def outcomes(opened, alive, areas=AREAS, radius=RADIUS_KM):
    """{area: {concept: [openings, survived]}} for classified openings within `radius` of an area's centre. Areas
    overlap, so each opening counts once, for the nearest centre, and the areas stay independent."""
    table = {a[0]: {c: [0, 0] for c in CONCEPTS} for a in areas}
    for lic, o in opened.items():
        concept = classify(o["name"])
        if concept is None:
            continue
        dist, name = min((km((o["lat"], o["lon"]), (lat, lon)), name) for name, lat, lon in areas)
        if dist <= radius:
            table[name][concept][0] += 1
            table[name][concept][1] += int(alive.get(lic, False))
    return table


def prior_order(table, leave_out):
    totals = collections.Counter()
    for area, row in table.items():
        if area != leave_out:
            for c, (_, s) in row.items():
                totals[c] += s
    return sorted(CONCEPTS, key=lambda c: (-totals[c], CONCEPTS.index(c)))


def hits_at_k(order, row, k=K):
    """(survived openings whose concept is in the top k, all survived openings)."""
    top = set(order[:k])
    return sum(s for c, (_, s) in row.items() if c in top), sum(s for _, s in row.values())


def survival_split(order, row, k=K):
    """Survival rate of openings in the top-k concepts vs. the rest: ((survived, opened), (survived, opened))."""
    top = set(order[:k])
    a = [sum(row[c][i] for c in row if c in top) for i in (1, 0)]
    b = [sum(row[c][i] for c in row if c not in top) for i in (1, 0)]
    return tuple(a), tuple(b)


def spearman(order, row):
    """Rank correlation between a concept order and survived counts (ties averaged); None if counts are all equal."""
    def ranks(values):
        idx = sorted(range(len(values)), key=lambda i: values[i])
        out = [0.0] * len(values)
        i = 0
        while i < len(idx):
            j = i
            while j + 1 < len(idx) and values[idx[j + 1]] == values[idx[i]]:
                j += 1
            for m in range(i, j + 1):
                out[idx[m]] = (i + j) / 2
            i = j + 1
        return out
    pred = ranks([-order.index(c) for c in CONCEPTS])
    obs = ranks([row[c][1] for c in CONCEPTS])
    mp, mo = sum(pred) / len(pred), sum(obs) / len(obs)
    num = sum((p - mp) * (o - mo) for p, o in zip(pred, obs))
    den = math.sqrt(sum((p - mp) ** 2 for p in pred) * sum((o - mo) ** 2 for o in obs))
    return num / den if den else None


def bootstrap_diff(pairs, n=2000, seed=7):
    """90% interval of pooled hit-rate(a) - hit-rate(b) over areas, resampled with replacement. pairs: [(ha, hb, tot)]."""
    rng, diffs = random.Random(seed), []
    for _ in range(n):
        s = [pairs[rng.randrange(len(pairs))] for _ in pairs]
        tot = sum(p[2] for p in s)
        if tot:
            diffs.append((sum(p[0] for p in s) - sum(p[1] for p in s)) / tot)
    diffs.sort()
    return (round(diffs[int(0.05 * len(diffs))], 3), round(diffs[int(0.95 * len(diffs)) - 1], 3)) if diffs else None


# ---------- network (only run from the command line) ----------

def _register(where, select, limit=50000):
    rows, offset = [], 0
    while True:
        q = urllib.parse.urlencode({"$select": select, "$where": where, "$limit": limit, "$offset": offset,
                                    "$order": "inspection_id"})
        page = json.load(urllib.request.urlopen(f"{REGISTER}?{q}", timeout=120))
        rows += page
        if len(page) < limit:
            return rows
        offset += limit


def load_register():
    facilities = " or ".join(f"facility_type='{f}'" for f in FACILITIES)
    lic_rows = _register(f"inspection_type='License' and inspection_date <= '{WINDOW[1]}T23:59:59' and ({facilities})",
                         "license_,dba_name,aka_name,inspection_date,results,latitude,longitude")
    opened = openings(lic_rows)
    hist = collections.defaultdict(list)
    for r in _register(f"inspection_date >= '{WINDOW[0]}' and ({facilities})", "license_,inspection_date,results"):
        if r.get("license_") in opened:
            hist[r["license_"]].append((r.get("inspection_date") or "", r.get("results") or ""))
    alive = {lic: survived(hist[lic], o["opened"]) for lic, o in opened.items()}
    return opened, alive


def qloo_orders(areas, led):
    """{area: concept order} from one rank call per area over the concepts' Chicago benchmarks."""
    bench = {}
    for concept, query, _group, _chain in tenant.CATALOG:
        res, _ = led.call(*qloo.p_find_tags(query), purpose="tag")
        tag, _ = tenant.pick_tag(res)
        if not tag:
            continue
        res, _ = led.call(*qloo.p_concept_places("Chicago", "Chicago", tag["id"], limit=5), purpose="benchmark")
        b = tenant.usable_benchmark([tenant._place(r) for r in tenant._results(res) if r.get("entity_id")])
        if b:
            bench.setdefault(b["id"], []).append(concept)
    orders, failures = {}, collections.Counter()
    for name, _, _ in areas:
        res, step = led.call(*qloo.p_rank(list(bench), f"{name}, Chicago"), purpose="rank")
        scores = tenant.demand_scores(res, bench)
        orders[name] = sorted(CONCEPTS, key=lambda c: (scores.get(c, (0, 99))[1], CONCEPTS.index(c))) if scores else None
        if not scores:
            failures[step.get("error") or f"status {(res or {}).get('status') or 'none'}: no usable ranking"] += 1
    return orders, len(bench), dict(failures)


def llm_orders(areas, chat_fn, tries=3, wait=20, sleep=time.sleep):
    orders, provider = {}, None
    for name, _, _ in areas:
        msg = None
        for attempt in range(tries):
            try:
                msg = chat_fn([{"role": "user", "content": LLM_PROMPT.format(area=name, concepts=", ".join(CONCEPTS))}])
                break
            except Exception:  # noqa: BLE001  free endpoints rate-limit; wait and retry
                if attempt + 1 < tries:
                    sleep(wait)
        if msg is None:
            orders[name] = None
            continue
        provider = msg.get("provider") or provider
        ranking = [c for c in ((tenant._json_block(msg.get("content")) or {}).get("ranking") or []) if c in CONCEPTS]
        ranking = list(dict.fromkeys(ranking))
        orders[name] = ranking + [c for c in CONCEPTS if c not in ranking] if len(ranking) >= K else None
    return orders, provider


def common_areas(table, orders):
    """Areas with at least one survivor and an order from every method, so all methods are scored on the same rows."""
    return [a for a, row in table.items()
            if sum(s for _, s in row.values()) and all(by_area.get(a) for by_area in orders.values())]


def score(table, orders):
    """Per method, on the common areas: pooled hit@K, survival split, mean Spearman."""
    out, areas = {}, common_areas(table, orders)
    for method, by_area in orders.items():
        hit = tot = 0
        top, rest, rhos, n = [0, 0], [0, 0], [], 0
        for area in areas:
            row, order = table[area], by_area[area]
            n += 1
            h, t = hits_at_k(order, row)
            hit, tot = hit + h, tot + t
            (ts, to), (rs, ro) = survival_split(order, row)
            top, rest = [top[0] + ts, top[1] + to], [rest[0] + rs, rest[1] + ro]
            rho = spearman(order, row)
            if rho is not None:
                rhos.append(rho)
        out[method] = {"areas": n, "hit_at_k": round(hit / tot, 3) if tot else None, "survivors": tot,
                       "survival_top": round(top[0] / top[1], 3) if top[1] else None, "opened_top": top[1],
                       "survival_rest": round(rest[0] / rest[1], 3) if rest[1] else None, "opened_rest": rest[1],
                       "spearman": round(sum(rhos) / len(rhos), 3) if rhos else None}
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--json")
    ap.add_argument("--no-llm", action="store_true")
    args = ap.parse_args(argv)

    opened, alive = load_register()
    table = outcomes(opened, alive)
    led = tenant.Ledger(budget=40)
    q_orders, n_bench, q_failures = qloo_orders(AREAS, led)
    orders = {"qloo": q_orders, "prior": {a[0]: prior_order(table, a[0]) for a in AREAS}}
    provider = None
    if not args.no_llm:
        import llm
        orders["llm"], provider = llm_orders(AREAS, lambda m: llm.chat(m, timeout=150))
    result = {
        "window": WINDOW, "survival_seen": SURVIVAL_SEEN, "radius_km": RADIUS_KM, "k": K, "areas": len(AREAS),
        "openings": len(opened), "classified": sum(classify(o["name"]) is not None for o in opened.values()),
        "survival_rate_all": round(sum(alive.values()) / len(alive), 3) if alive else None,
        "qloo_calls": led.network, "benchmarks": n_bench, "qloo_rank_failures": q_failures, "llm_provider": provider,
        "random_hit_at_k": K / len(CONCEPTS), "areas_scored": common_areas(table, orders),
        "methods": score(table, orders),
    }
    pairs = {}
    for m in orders:
        if m != "qloo":
            rows = [(hits_at_k(orders["qloo"][a], table[a])[0], hits_at_k(orders[m][a], table[a])[0],
                     hits_at_k(orders["qloo"][a], table[a])[1]) for a in common_areas(table, orders)]
            pairs[f"qloo_minus_{m}_ci90"] = bootstrap_diff(rows) if rows else None
    result.update(pairs)
    text = json.dumps(result, indent=1)
    print(text)
    if args.json:
        with open(args.json, "w") as f:
            f.write(text + "\n")


if __name__ == "__main__":
    sys.exit(main())
