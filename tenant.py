"""Tenant match: which food concepts should rent this vacant unit, and can each one pay the rent?

The pipeline is fixed and code-orchestrated, so every run makes a bounded number of Qloo calls:
locate -> local taste (recommend, entity_tags, audience_demographics) -> candidate concepts (LLM, find_tags)
-> benchmarks (recommend) -> local affinity (rank) -> white space (recommend in the area) -> audience overlap
(compare_audiences) -> reach (trends) -> hotspots (where_popular) -> rent ceiling join -> memo.
The LLM proposes candidates and writes the memo; scores and GO / ADJUST / NO-GO calls are computed here.
When no LLM answers, a fixed catalog and a template memo take over, so a run always finishes.
"""
import concurrent.futures
import datetime
import json
import re
import threading
import time

import geo
import llm
import qloo
import scan

BUDGET = 25  # Qloo network calls per run; cache hits are free
N_CANDIDATES = 6
N_DEEP = 4  # concepts that get the white-space check and a GO / ADJUST / NO-GO call
N_COMPARE = 2  # of those, concepts that get the audience-overlap check
LOCAL_LIMIT = 10
PARALLEL = 3
RETRY_AFTER = 2.0  # seconds before the single retry of a rate-limited call
LLM_TIMEOUT = 40  # per provider; Cloud Run cuts requests at 300 s and a run makes up to 3 LLM calls
WEIGHTS = {"demand": 0.45, "white_space": 0.35, "overlap": 0.20}
THRESHOLDS = (("GO", 65), ("ADJUST", 45), ("NO-GO", 0))

# Alcohol-led concepts are excluded (owner's halal rule). Food formats that are merely called "bar" stay.
_FOOD_BARS = re.compile(r"\b(juice|salad|espresso|coffee|smoothie|dessert|noodle|sushi|poke|milk|snack|chocolate|"
                        r"oatmeal|acai|açaí|soup|burrito|taco|ramen|tea|matcha|yogurt|cereal|breakfast)\s+bars?\b", re.I)
_ALCOHOL = re.compile(r"\b(bars?|pubs?|gastropubs?|brew\w*|taprooms?|taverns?|wines?|winery|wineries|cocktails?|"
                      r"beers?|biergarten|spirits|sake|speakeasy|speakeasies|izakayas?|lounges?|nightclubs?|"
                      r"distiller\w*|cider\w*|liquor|alcohol\w*|saloons?|cantinas?|whisk(e)?y|vineyards?|"
                      r"tequilas?|mezcal\w*|vodka|rum|gin|bourbon|champagne|prosecco|margaritas?|sangria)\b", re.I)


def alcohol_led(text):
    return bool(_ALCOHOL.search(_FOOD_BARS.sub(" ", text or "")))


# Deterministic candidates when no LLM answers: (concept, tag query, rent group, reference chain for trends).
CATALOG = [
    ("ramen shop", "ramen", "restaurant", None),
    ("Korean fried chicken", "korean fried chicken", "fast_food", "Bonchon Chicken"),
    ("Mediterranean bowls", "mediterranean", "fast_food", "CAVA"),
    ("specialty coffee", "specialty coffee", "cafe", "Blue Bottle Coffee"),
    ("salad bowls", "salad", "fast_food", "Sweetgreen"),
    ("taqueria", "tacos", "fast_food", "Chipotle"),
    ("Vietnamese pho", "vietnamese", "restaurant", None),
    ("smash burgers", "burgers", "fast_food", "Shake Shack"),
    ("Lebanese grill", "lebanese", "restaurant", None),
    ("bakery", "bakery", "other", None),
]
RENT_GROUPS = ("restaurant", "fast_food")
_TAG_PREF = ("urn:tag:category:place", "urn:tag:genre:place", "urn:tag:cuisine", "urn:tag:popular_food_item")


class Ledger:
    """Runs Qloo calls within the budget and records each one as a readable step (never the key)."""

    def __init__(self, budget=BUDGET, sleep=time.sleep):
        self.budget, self.network, self.cached, self.lock, self.steps = budget, 0, 0, threading.Lock(), []
        self.sleep, self.cache = sleep, qloo.LRUCache()

    def _reserve(self):
        with self.lock:
            if self.network >= self.budget:
                return False
            self.network += 1
            return True

    def call(self, op, params, purpose):
        result, step = self._call(op, params, purpose)
        with self.lock:
            self.steps.append(step)
        return result, step

    def _call(self, op, params, purpose):
        step = {"workflow": op, "purpose": purpose, "input": _display(params), "cached": False}
        if not self._reserve():
            return None, {**step, "skipped": "call budget reached"}
        try:
            result, hit = qloo.exec_cached(op, params, self.cache)
            if _rate_limited(result) and self._reserve():
                self.sleep(RETRY_AFTER)
                result, hit = qloo.exec_cached(op, params, self.cache)
        except Exception as e:  # noqa: BLE001  one failed call degrades the memo, never the whole run
            return None, {**step, "error": type(e).__name__}
        if hit:
            with self.lock:
                self.network -= 1
                self.cached += 1
        status = result.get("status") if isinstance(result, dict) else None
        step.update(cached=hit, status=status)
        if status not in ("ok", "empty"):
            err = (result or {}).get("error") or {}
            step["error"] = err.get("code") or status or "error"
        return result, step

    def workflows(self):
        return sorted({s["workflow"] for s in self.steps if not s.get("skipped") and not s.get("error")})

    def batch(self, jobs):
        """[(op, params, purpose)] run in parallel; returns [(result, step)] in input order."""
        if not jobs:
            return []
        with concurrent.futures.ThreadPoolExecutor(max_workers=PARALLEL) as pool:
            return list(pool.map(lambda j: self.call(*j), jobs))


def _rate_limited(result):
    return isinstance(result, dict) and ((result.get("error") or {}).get("code") == "QLOO_RATE_LIMIT")


def _display(params):
    return {k: v for k, v in params.items() if k not in ("explain",)}


def _results(res):
    r = (res or {}).get("results")
    return r if isinstance(r, list) else []


def _place(r):
    p = r.get("properties") or {}
    return {"id": r.get("entity_id"), "name": r.get("name"), "affinity": r.get("affinity"),
            "address": p.get("address"), "price_level": p.get("price_level"),
            "description": (p.get("short_description") or "")[:140]}


# ---------- candidates ----------

CANDIDATE_PROMPT = """You help a commercial broker find food & drink tenants for a vacant retail unit.
Area: {area} ({city}).
Places this area's audience over-indexes on (Qloo): {places}
Tags that characterize those places (Qloo): {tags}
Audience age/gender skew vs. average (Qloo): {demo}
Propose {n} distinct restaurant or cafe concepts that this audience would likely love and that a single vacant unit
could host. Prefer concepts the area does not obviously already have. Do not propose bars, pubs, breweries, wine,
cocktail or any alcohol-led concept. Answer with JSON only:
{{"candidates": [{{"concept": "short name", "tag_query": "1-3 words to search Qloo's tag ontology",
 "rent_group": "restaurant | fast_food | cafe | other", "reference_chain": "a well-known chain in this format, or null",
 "why": "one line grounded in the data above"}}]}}"""


def _json_block(text):
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except ValueError:
        return None


def _clean_candidates(raw):
    out, seen = [], set()
    for c in raw if isinstance(raw, list) else []:
        if not isinstance(c, dict) or not isinstance(c.get("concept"), str):
            continue
        name = " ".join(c["concept"].split())[:60]
        query = " ".join(str(c.get("tag_query") or name).split())[:40]
        if not name or name.lower() in seen or alcohol_led(name) or alcohol_led(query):
            continue
        seen.add(name.lower())
        group = c.get("rent_group") if c.get("rent_group") in ("restaurant", "fast_food", "cafe", "other") else "other"
        chain = c.get("reference_chain") if isinstance(c.get("reference_chain"), str) else None
        out.append({"concept": name, "tag_query": query, "rent_group": group,
                    "reference_chain": (chain or "")[:50] or None, "why": str(c.get("why") or "")[:200]})
    return out


def propose(area, city, places, tags, demo, chat_fn):
    """(candidates, source): LLM proposals, topped up from the fixed catalog when the LLM fails or proposes too few."""
    prompt = CANDIDATE_PROMPT.format(area=area, city=city, n=N_CANDIDATES + 2,
                                     places="; ".join(f"{p['name']} ({p['description']})" for p in places[:8]) or "none",
                                     tags=", ".join(tags[:15]) or "none", demo=json.dumps(demo) if demo else "unknown")
    cands, source = [], "catalog"
    try:
        msg = chat_fn([{"role": "user", "content": prompt}])
        cands = _clean_candidates((_json_block(msg.get("content")) or {}).get("candidates"))
        source = msg.get("provider") or "llm"
    except Exception:  # noqa: BLE001  the catalog takes over
        pass
    names = {c["concept"].lower() for c in cands}
    for concept, query, group, chain in CATALOG:
        if len(cands) >= N_CANDIDATES + 2:
            break
        if concept.lower() not in names:
            cands.append({"concept": concept, "tag_query": query, "rent_group": group, "reference_chain": chain,
                          "why": "TasteFit default candidate (no LLM proposal)"})
    return cands, source


def pick_tag(res):
    """(chosen tag, alternatives) from a find_tags result: place-scoped category/cuisine tags first, alcohol excluded."""
    tags = [t for t in _results(res) if isinstance(t, dict) and str(t.get("id", "")).startswith("urn:tag:")
            and not alcohol_led(t.get("name")) and "wine_and_spirits" not in t.get("id", "")]
    if not tags:
        return None, []
    def pref(t):
        return next((i for i, p in enumerate(_TAG_PREF) if t.get("type", t["id"]).startswith(p)), len(_TAG_PREF))
    best = min(tags, key=pref)
    if pref(best) == len(_TAG_PREF):
        return None, [t.get("name") for t in tags[:4]]
    alts = [f"{t.get('name')} ({t.get('type', '').split(':')[-2] if t.get('type') else '?'})"
            for t in tags if t is not best][:3]
    return {"id": best["id"], "name": best.get("name"), "type": best.get("type")}, alts


# ---------- profile helpers ----------

_NOISE_TAGS = ("credit_card", "payment", "wine_and_spirits", "accessibility", "parking", "inclusivity")
_GENERIC_TAGS = {"place", "restaurant", "average", "food", "point of interest", "establishment"}


def local_tags(res):
    """Tag names that characterize the area's places, cross-domain ones included (music, media...), minus noise."""
    out = []
    for t in _results(res):
        if not isinstance(t, dict) or any(n in str(t.get("id", "")) for n in _NOISE_TAGS):
            continue
        name = (t.get("name") or "").strip()
        if name and not alcohol_led(name) and name.lower() not in {o.lower() for o in out}:
            out.append(name)
    return out


_BAR_LEAD = re.compile(r"^\W*(?:[\w'-]+\s+){0,3}(?:wine\s+|cocktail\s+|sports\s+|dive\s+)?"
                       r"(?:bars?|pubs?|brewery|brewpub|taproom|tavern|speakeasy|nightclub)\b", re.I)


def usable_benchmark(places):
    """First place that is not alcohol-led: by name, or a description that opens as a bar. A restaurant that
    merely serves wine stays (halal rule: alcohol-led concepts are excluded, not every licensed restaurant)."""
    return next((p for p in places if not alcohol_led(p["name"]) and not _BAR_LEAD.match(p["description"] or "")), None)


def demographics(res):
    """{"age": {bucket: skew}, "gender": {...}} for the first entity, skews rounded; {} when absent."""
    q = (_results(res)[:1] or [{}])[0].get("query") or {}
    return {k: {b: round(v, 2) for b, v in q[k].items() if isinstance(v, (int, float))}
            for k in ("age", "gender") if isinstance(q.get(k), dict)}


def shared_tags(res, n=5):
    """(tag names, overlap index 0-100) from a compare_audiences result: mean Qloo score of the top shared tags."""
    r = (res or {}).get("results")
    tags = (r.get("tags") if isinstance(r, dict) else r) or []
    rows = [(t.get("name"), (t.get("query") or {}).get("score")) for t in tags if isinstance(t, dict)]
    rows = [(nm, s) for nm, s in rows if nm and isinstance(s, (int, float)) and not alcohol_led(nm)
            and nm.strip().lower() not in _GENERIC_TAGS][:n]
    if not rows:
        return [], None
    return [nm for nm, _ in rows], round(100 * sum(s for _, s in rows) / len(rows))


def trend_summary(res):
    """{"brand", "percentile", "change", "weeks"} from a trends result, or None."""
    s = ((res or {}).get("series") or [None])[0]
    pts = [p for p in (s or {}).get("points") or [] if isinstance(p.get("population_percentile"), (int, float))]
    if not pts:
        return None
    pts.sort(key=lambda p: p.get("date", ""))
    first, last = pts[0]["population_percentile"], pts[-1]["population_percentile"]
    return {"brand": (s.get("entity") or {}).get("name"), "percentile": round(100 * last),
            "change": round(100 * (last - first), 1), "weeks": len(pts),
            "from": pts[0].get("date"), "to": pts[-1].get("date")}


def ambiguous_choice(res):
    """First candidate id when trends could not resolve a brand name unambiguously."""
    for issue in ((res or {}).get("resolution") or {}).get("issues") or []:
        for c in issue.get("candidates") or []:
            if c.get("id"):
                return c["id"], c.get("name")
    return None, None


# ---------- scoring ----------

def demand_scores(rank_res, concept_by_id):
    """{concept: (score 0-100, position, of)} from one rank call: position-based, comparable only within this run."""
    order = [r.get("entity_id") for r in sorted(_results(rank_res), key=lambda r: -(r.get("affinity") or 0))]
    order = [i for i in order if i in concept_by_id]
    n = len(order)
    out = {}
    for pos, eid in enumerate(order):
        score = 100 if n == 1 else round(100 * (n - 1 - pos) / (n - 1))
        for concept in concept_by_id[eid]:
            out.setdefault(concept, (score, pos + 1, n))
    return out


def tenant_score(demand, white_space, overlap):
    parts = {"demand": demand, "white_space": white_space, "overlap": overlap}
    used = {k: v for k, v in parts.items() if v is not None}
    w = sum(WEIGHTS[k] for k in used)
    return round(sum(WEIGHTS[k] * v for k, v in used.items()) / w) if w else None


def rent_check(group, rent_rows, asking):
    """(row or None, note, downgrade) joining a concept's rent group with the City Scan ceiling for the unit's cell."""
    if group == "cafe":
        return None, "No ceiling: official data counts cafés together with bars, which TasteFit leaves out.", None
    if group not in RENT_GROUPS:
        return None, "No ceiling for this format in the official data.", None
    if rent_rows is None:
        return None, "Rent ceiling not calibrated for this city.", None
    row = next((r for r in rent_rows if r.get("group") == group), None)
    if row is None:
        return None, "No ceiling computed for this cell.", None
    if not row.get("viable"):
        return row, row.get("note") or "Not viable at any rent here.", "NO-GO"
    if asking is None:
        return row, f"Max rent {row['rent_max']:.0f}/month at the city's 3-year survival rate.", None
    hi = (row.get("ci90") or [None, row["rent_max"]])[1]
    if asking > hi:
        return row, f"Asking rent {asking:.0f} is above the ceiling's 90% range (up to {hi:.0f}).", "NO-GO"
    if asking > row["rent_max"]:
        return row, f"Asking rent {asking:.0f} is above the central ceiling {row['rent_max']:.0f}: negotiate.", "ADJUST"
    return row, f"Asking rent {asking:.0f} is within the ceiling {row['rent_max']:.0f}.", None


def verdict(score, downgrade):
    call = next(c for c, floor in THRESHOLDS if (score or 0) >= floor)
    order = [c for c, _ in THRESHOLDS]
    if downgrade and order.index(downgrade) > order.index(call):
        call = downgrade
    return call


# ---------- baseline (same LLM, no tools) ----------

BASELINE_PROMPT = """A commercial broker has a vacant retail unit in {area} ({city}).
Without any external data, list the {n} restaurant or cafe concepts most likely to succeed there.
No alcohol-led concepts. Answer with JSON only:
{{"candidates": [{{"concept": "short name", "why": "one line"}}]}}"""

_STOP = {"shop", "restaurant", "cafe", "café", "counter", "house", "spot", "kitchen", "bistro", "eatery", "concept",
         "style", "modern", "casual", "fast", "the", "and", "a", "of", "s"}


def _tokens(s):
    return {w.rstrip("s") for w in re.findall(r"[a-zà-ÿ]+", (s or "").lower()) if w not in _STOP}


def baseline(area, city, chat_fn):
    try:
        msg = chat_fn([{"role": "user", "content": BASELINE_PROMPT.format(area=area, city=city, n=N_CANDIDATES)}])
    except Exception:  # noqa: BLE001
        return None
    rows = []
    for c in ((_json_block(msg.get("content")) or {}).get("candidates") or [])[:N_CANDIDATES]:
        if isinstance(c, dict) and isinstance(c.get("concept"), str):
            rows.append({"concept": c["concept"][:60], "why": str(c.get("why") or "")[:200],
                         "excluded": alcohol_led(c["concept"])})
    return {"provider": msg.get("provider"), "candidates": rows}


def judge_baseline(base, shortlist):
    """Marks each LLM-only claim confirmed / contradicted / not tested against what Qloo returned in this run."""
    if not base:
        return None
    for row in base["candidates"]:
        toks = _tokens(row["concept"])
        match = next((s for s in shortlist if toks & (_tokens(s["concept"]) | _tokens(s["tag"]["name"] if s.get("tag") else ""))), None)
        if row["excluded"]:
            row["status"], row["evidence"] = "excluded", "Alcohol-led: left out."
        elif match is None:
            row["status"], row["evidence"] = "not tested", "Not among the concepts Qloo evaluated in this run."
        elif match.get("call") in ("GO", "ADJUST") and match.get("position", 99) <= 3:
            row["status"] = "confirmed"
            row["evidence"] = f"Qloo: {match['concept']} ranks {match['position']} of {match['of']} for this audience, {match['call']}."
        elif match.get("local_supply") is not None and match["local_supply"] >= LOCAL_LIMIT * 0.8:
            row["status"] = "contradicted"
            row["evidence"] = f"Qloo: {match['local_supply']}+ places already serve {match['concept']} here."
        elif match.get("call") == "NO-GO" or match.get("position", 0) > (match.get("of") or 0) / 2:
            row["status"] = "contradicted"
            row["evidence"] = (f"Qloo: {match['concept']} ranks {match.get('position')} of {match.get('of')}"
                               + (f", {match['call']}" if match.get("call") else "") + ".")
        else:
            row["status"], row["evidence"] = "partly", f"Qloo: {match['concept']} is mid-table for this audience."
    mentioned = set().union(*(_tokens(r["concept"]) for r in base["candidates"])) if base["candidates"] else set()
    base["qloo_only"] = [s["concept"] for s in shortlist[:3] if s.get("call") in ("GO", "ADJUST")
                         and not (_tokens(s["concept"]) & mentioned)]
    return base


# ---------- memo ----------

MEMO_PROMPT = """Write a one-page tenant memo for a commercial broker in Markdown, using ONLY the JSON evidence below.
Do not change any verdict, score or number, and do not invent places, tags, rents or numbers.
Sections: ## Recommendation (2 sentences naming the top tenant and its call), ## Shortlist (one bullet per scored
concept: call, score, the Qloo evidence behind it, rent check), ## Why this audience (local places, tags and age skew),
## Before you sign (3 bullets of on-site checks), ## Limits (Qloo describes aggregate taste affinities, not foot
traffic, sales or individuals; rank scores are relative to these candidates; rent ceilings are a model, not a forecast;
in a Chicago backtest Qloo demand rankings did not predict which concepts survived better than a citywide baseline).
Evidence:
{evidence}"""

LIMITS = ("## Limits\n- Qloo describes aggregate taste affinities of an area's audience, not foot traffic, sales or any "
          "individual.\n- Demand scores are relative to the candidates in this run (one Qloo rank call), not absolute.\n"
          "- White space counts the places Qloo returns for the concept within its own area boundary (capped at "
          f"{LOCAL_LIMIT}); it is not a census of competitors.\n- Rent ceilings are a survival model built from "
          "official data, not a forecast; cafés are not covered.\n- The unit is a point on a map, not a vetted address.")
BACKTEST_NOTE = ("- In a Chicago backtest of 2023-2024 openings, Qloo demand rankings did not predict which concepts opened "
                 "and survived better than a citywide baseline: read them as taste evidence, not a survival forecast.")
LIMITS += "\n" + BACKTEST_NOTE


def template_memo(area, city, shortlist, profile):
    scored = [s for s in shortlist if s.get("call")]
    lines = [f"# Tenant memo: {area if city.lower() in area.lower() else f'{area}, {city}'}", ""]
    if scored:
        top = scored[0]
        lines += ["## Recommendation", f"{top['concept']} is the strongest tenant for this unit "
                  f"({top['call']}, score {top['score']}).", ""]
    lines.append("## Shortlist")
    for s in scored:
        bits = [f"demand {s['demand']} (rank {s['position']} of {s['of']})"]
        if s.get("local_supply") is not None:
            bits.append(f"{s['local_supply']} local places serve it")
        if s.get("shared_tags"):
            bits.append("shared taste: " + ", ".join(s["shared_tags"][:3]))
        lines.append(f"- **{s['concept']}**: {s['call']}, score {s['score']}; " + "; ".join(bits) + f". Rent: {s['rent_note']}")
    if profile.get("places"):
        lines += ["", "## Why this audience", "Local favourites (Qloo): " + ", ".join(p["name"] for p in profile["places"][:5]) + "."]
        if profile.get("tags"):
            lines.append("Characteristic tags: " + ", ".join(profile["tags"][:8]) + ".")
    lines += ["", "## Before you sign", "- Count evening and weekend foot traffic at the unit.",
              "- Check the extraction and kitchen fit-out the top concept needs.",
              "- Visit the named local favourites to confirm price tier and queues.", "", LIMITS]
    return "\n".join(lines)


def ensure_limits(memo):
    """Appends the Limits section if the LLM dropped it, and the backtest finding if its Limits section left it out
    (the prompt puts Limits last)."""
    if not re.search(r"(?m)^##\s*Limits\b", memo):
        return memo.rstrip() + "\n\n" + LIMITS
    return memo if "backtest" in memo.lower() else memo.rstrip() + "\n" + BACKTEST_NOTE


# ---------- the run ----------

def resolve_area(city, location, lat, lon):
    """(area labels to try, city, (lat, lon) or None, error)."""
    if lat is not None and lon is not None:
        rev = geo.reverse(lat, lon)
        if not rev:
            return [], city, (lat, lon), "Could not name that point; type the neighborhood instead."
        city = rev["city"] or city
        labels = [f"{a}, {city}" for a in rev["areas"][:2]] or [city]
        if location:
            labels.insert(0, f"{location}, {city}")
        return labels, city, (lat, lon), None
    if not location:
        return [], city, None, "location or a map point is required"
    label = location if city.lower() in location.lower() else f"{location}, {city}"
    pt = geo.geocode(label)
    return [label], city, pt, None


def run(city, location=None, lat=None, lon=None, asking_rent=None, chat_fn=None, today=None):
    """Yields (kind, data): unit, step*, profile, candidates, shortlist, baseline, memo, budget; or error."""
    chat_fn = chat_fn or (lambda messages: llm.chat(messages, timeout=LLM_TIMEOUT))
    today = today or datetime.date.today()
    led = Ledger()
    labels, city, point, problem = resolve_area(city or "", location, lat, lon)
    if problem:
        yield "error", problem
        return

    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    base_future = pool.submit(baseline, labels[0], city, chat_fn)

    rent_rows, unit = None, {"city": city, "area": labels[0], "point": point and {"lat": point[0], "lon": point[1]}}
    if point:
        code, body = scan.unit(city, point[0], point[1])
        if code == 200 and body.get("calibrated") and body.get("rent_ceiling") is not None:
            rent_rows = body["rent_ceiling"]
            unit.update(currency=body.get("currency"), rent_note=body.get("note"), cell=body.get("cell"))
        else:
            unit["rent_note"] = body.get("note") or body.get("error") or "Rent ceiling not calibrated for this city."
    yield "unit", unit

    # 1-2. Local taste profile.
    area, places = labels[0], []
    for label in labels:
        res, step = led.call(*qloo.p_area_taste(label), purpose=f"What the {label} audience over-indexes on")
        places = [_place(r) for r in _results(res) if r.get("entity_id")]
        yield "step", {**step, "names": [p["name"] for p in places[:6]]}
        if places:
            area = label
            break
    if not places:
        pool.shutdown(wait=False)
        yield "error", "Qloo returned no places for this area. Try a better-known neighborhood name."
        return
    unit["area"] = area
    ids = [p["id"] for p in places]
    (tag_res, tag_step), (demo_res, demo_step) = led.batch([
        (*qloo.p_entity_tags(ids[:5]), "Tags that characterize the area's favourite places"),
        (*qloo.p_demographics(ids[0]), f"Audience age/gender skew of {places[0]['name']}")])
    tags, demo = local_tags(tag_res), demographics(demo_res)
    yield "step", {**tag_step, "names": tags[:8]}
    yield "step", {**demo_step, "names": [f"{k}: {v:+.2f}" for k, v in (demo.get("age") or {}).items()][:6]}
    profile = {"area": area, "places": places[:8], "tags": tags[:12], "demographics": demo}
    yield "profile", profile

    # 3. Candidates and their Qloo tags.
    cands, source = propose(area, city, places, tags, demo, chat_fn)
    resolved = []
    queue = list(cands)
    while queue and len(resolved) < N_CANDIDATES:
        wave, queue = queue[:N_CANDIDATES - len(resolved)], queue[N_CANDIDATES - len(resolved):]
        for c, (res, step) in zip(wave, led.batch([(*qloo.p_find_tags(c["tag_query"]),
                                                    f"Qloo tag for '{c['concept']}'") for c in wave])):
            tag, alts = pick_tag(res)
            yield "step", {**step, "choice": tag and f"{tag['name']} ({tag['id']})", "alternatives": alts,
                           "names": [] if tag else ["no place tag found: dropped"]}
            if tag and tag["id"] not in {r["tag"]["id"] for r in resolved}:
                resolved.append({**c, "tag": tag})
        if not wave:
            break
    yield "candidates", {"source": source, "concepts": [{k: c[k] for k in ("concept", "why", "rent_group")}
                                                        | {"tag": c["tag"]} for c in resolved]}

    # 4. Benchmarks: the city's place for each concept that this area's audience likes most.
    for c, (res, step) in zip(resolved, led.batch([
            (*qloo.p_concept_places(area, city, c["tag"]["id"], limit=5), f"Benchmark {c['concept']} place in {city}")
            for c in resolved])):
        top = [_place(r) for r in _results(res) if r.get("entity_id")]
        c["benchmark"] = usable_benchmark(top)
        yield "step", {**step, "names": [p["name"] for p in top]}
    ranked = [c for c in resolved if c.get("benchmark")]

    # 5. Local affinity: rank every benchmark against the area in ONE call so scores are comparable.
    by_id = {}
    for c in ranked:
        by_id.setdefault(c["benchmark"]["id"], []).append(c["concept"])
    scores = {}
    if by_id:
        res, step = led.call(*qloo.p_rank(list(by_id), area), purpose=f"Rank the benchmarks for the {area} audience")
        scores = demand_scores(res, by_id)
        yield "step", {**step, "names": [f"{cs[0]}" for _, cs in sorted(by_id.items(), key=lambda kv: scores.get(kv[1][0], (0, 99))[1])]}
    for c in ranked:
        c["demand"], c["position"], c["of"] = scores.get(c["concept"], (None, None, None))
    ranked.sort(key=lambda c: (c["position"] is None, c["position"] or 0))
    deep = [c for c in ranked if c["demand"] is not None][:N_DEEP]

    # 6. White space: how many places already serve each concept inside the area.
    for c, (res, step) in zip(deep, led.batch([
            (*qloo.p_concept_places(area, area, c["tag"]["id"], limit=LOCAL_LIMIT), f"Existing {c['concept']} supply in {area}")
            for c in deep])):
        local = [_place(r) for r in _results(res) if r.get("entity_id")]
        if res is not None and not step.get("error"):
            c["local_supply"], c["local_names"] = len(local), [p["name"] for p in local[:5]]
            c["white_space"] = round(100 * (1 - min(len(local), LOCAL_LIMIT) / LOCAL_LIMIT))
        yield "step", {**step, "names": [p["name"] for p in local[:6]] or ["none found: white space"]}

    # 7. Audience overlap: the area's favourite places vs. each concept's benchmark.
    for c, (res, step) in zip(deep[:N_COMPARE], led.batch([
            (*qloo.p_compare(ids[:3], [c["benchmark"]["id"]]), f"Shared taste: {area} favourites vs. {c['benchmark']['name']}")
            for c in deep[:N_COMPARE]])):
        c["shared_tags"], c["overlap"] = shared_tags(res)
        yield "step", {**step, "names": c["shared_tags"]}

    # 8. Hotspots of the top concept's benchmark, and how far the unit is from them.
    heat = []
    if deep:
        res, step = led.call(*qloo.p_where_popular(deep[0]["benchmark"]["id"], city),
                             purpose=f"Where {deep[0]['benchmark']['name']}'s audience concentrates in {city}")
        heat = [{"lat": r["location"]["latitude"], "lon": r["location"]["longitude"],
                 "w": (r.get("query") or {}).get("affinity")} for r in _results(res)
                if isinstance(r.get("location"), dict) and "latitude" in r["location"]]
        if heat and point:
            deep[0]["hotspot_km"] = round(min(geo.km_between(point, (h["lat"], h["lon"])) for h in heat), 2)
        yield "step", {**step, "names": [f"{len(heat)} hotspots"] + ([f"nearest {deep[0]['hotspot_km']} km from the unit"]
                                                                       if deep[0].get("hotspot_km") is not None else [])}

    # 9. Reach: Qloo trending covers brands, not places, so read the top concept's reference chain.
    start = (today - datetime.timedelta(days=182)).isoformat()
    for c in [c for c in deep if c.get("reference_chain")][:1]:
        res, step = led.call(*qloo.p_trends(c["reference_chain"], start, today.isoformat()),
                             purpose=f"Reach of {c['reference_chain']} (reference chain for {c['concept']})")
        if res and res.get("status") == "needs_input":
            cid, cname = ambiguous_choice(res)
            yield "step", {**step, "names": [f"ambiguous: chose {cname}" if cid else "brand not found"]}
            if not cid:
                continue
            res, step = led.call(*qloo.p_trends(cid, start, today.isoformat()), purpose=f"Reach of {cname}")
        c["trend"] = trend_summary(res)
        t = c["trend"]
        yield "step", {**step, "names": [f"{t['brand']}: percentile {t['percentile']}, change {t['change']:+} pts "
                                         f"over {t['weeks']} weeks"] if t else []}

    # Join with the rent ceiling and compute the calls.
    shortlist = []
    for c in ranked:
        row = {k: c.get(k) for k in ("concept", "why", "rent_group", "tag", "benchmark", "demand", "position", "of",
                                     "local_supply", "local_names", "white_space", "shared_tags", "overlap", "trend",
                                     "hotspot_km")}
        if c in deep:
            rent_row, note, down = rent_check(c["rent_group"], rent_rows, asking_rent)
            row["score"] = tenant_score(c["demand"], c.get("white_space"), c.get("overlap"))
            row["call"] = verdict(row["score"], down)
            row["rent_note"] = note
            row["rent_max"] = rent_row and rent_row.get("rent_max")
        shortlist.append(row)
    shortlist.sort(key=lambda s: (s.get("score") is None, -(s.get("score") or 0)))
    yield "shortlist", {"area": area, "city": city, "currency": unit.get("currency"), "asking_rent": asking_rent,
                        "weights": WEIGHTS, "concepts": shortlist, "heat": heat[:8],
                        "method": "Score = 0.45 x demand (position in one Qloo rank call) + 0.35 x white space "
                                  f"(100 minus 10 per local place, {LOCAL_LIMIT}+ = 0) + 0.20 x audience overlap "
                                  "(Qloo compare score). GO 65+, ADJUST 45-64, NO-GO below 45; a rent above the "
                                  "ceiling downgrades the call."}

    base = judge_baseline(base_future.result(), shortlist)
    pool.shutdown(wait=False)
    yield "baseline", base or {"unavailable": True}

    evidence = {"area": area, "city": city, "asking_rent": asking_rent, "currency": unit.get("currency"),
                "profile": {"places": [p["name"] for p in places[:6]], "tags": tags[:10], "age_skew": demo.get("age")},
                "shortlist": [{k: v for k, v in s.items() if k not in ("benchmark", "tag", "local_names")}
                              | {"benchmark": (s.get("benchmark") or {}).get("name")} for s in shortlist]}
    try:
        msg = chat_fn([{"role": "user", "content": MEMO_PROMPT.format(evidence=json.dumps(evidence, ensure_ascii=False))}])
        memo, author = (msg.get("content") or "").strip(), msg.get("provider") or "llm"
        if len(memo) < 200:
            raise ValueError("memo too short")
    except Exception:  # noqa: BLE001  the template memo always works
        memo, author = template_memo(area, city, shortlist, profile), "template"
    yield "memo", {"markdown": ensure_limits(memo), "author": author}
    yield "budget", {"qloo_calls": led.network, "cache_hits": led.cached, "budget": led.budget,
                     "workflows": led.workflows()}
