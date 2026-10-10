"""TasteFit agent: tests a restaurant/cafe concept against the local taste of a neighborhood."""
import json
import os
import re
import urllib.request

import geo
import qloo

SYSTEM = """You are TasteFit, an analyst that checks whether a food & drink concept fits the local taste of an area,
before a founder commits to a lease. Your only evidence is your tools (the Qloo taste graph).

Method:
1. Translate the concept into Qloo tags with find_tags (cuisine, format, vibe). Several tags can share a name with
   different types; pick the one that matches the concept and use its tag URN. If none matches, refine the query.
2. Read the area's overall taste with area_taste, then the places that best match the concept's tags with competitors.
3. Take the strongest benchmark place and call hotspots to see which sub-areas over-index for that taste.
4. Call site_math: it geocodes what you gathered and returns the demand barycenter, the competition barycenter,
   the catchment radius and the suggested opening point with its distance to the nearest competitor.
5. Look for a gap: strong affinity for the concept's tags but few strong places serving it.
If a tool returns status "needs_input", fix the inputs and retry once.
Finish with a memo in Markdown:
## Verdict (1-2 sentences of rationale. Do not write the call or a score here: the server computes the fit score as the
   mean of taste match, competition gap and location strength, where location strength is the site_math opportunity
   index, and maps it to GO (70+), ADJUST (45-69) or NO-GO (below 45).)
## Evidence (3-5 bullets, each citing a place, tag or number returned by a tool)
## Best location within the area (use the site_math numbers in km; describe the suggested point by nearby streets
   or sub-areas you saw in tool results, never invent an address)
## Positioning: menu signatures, price tier, vibe (grounded in the tags)
## Risks
## Before you sign (3-5 bullets: what to verify on site, e.g. evening foot traffic at the suggested spot, rent vs. price tier, a named competitor's busiest hours)
## Limits: Qloo describes aggregate group affinities, not foot traffic, rents or individual behaviour; say what this memo does not establish.
After the memo, add your two qualitative sub-scores as one fenced JSON block (it is removed before display):
```json
{"taste_match": {"score": 0-100, "reason": "how strongly the area's audience favors the concept's tags"},
 "competition_gap": {"score": 0-100, "reason": "high when few strong places already serve it"}}
```
Be concise. Never invent places, tags or numbers; separate Qloo results from your interpretation."""

SUBSCORES = (("taste_match", "Taste match"), ("competition_gap", "Competition gap"))
THRESHOLDS = (("GO", 70), ("ADJUST", 45), ("NO-GO", 0))

TOOLS = [
    {"name": "find_tags", "description": "Candidate Qloo tags (URN, name, type) for a free-text concept, e.g. 'korean fried chicken', 'specialty coffee'.",
     "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}},
    {"name": "area_taste", "description": "Places the area's audience has the highest affinity for, optionally for a demographic like 'millennials'.",
     "parameters": {"type": "object", "properties": {"location": {"type": "string"}, "demographic": {"type": "string"}},
                    "required": ["location"]}},
    {"name": "competitors", "description": "Places in the area ranked by affinity to the concept's tag URNs (competition and benchmarks).",
     "parameters": {"type": "object", "properties": {"location": {"type": "string"},
                                                     "tag_urns": {"type": "array", "items": {"type": "string"}},
                                                     "demographic": {"type": "string"}},
                    "required": ["location", "tag_urns"]}},
    {"name": "hotspots", "description": "Sub-areas within the area where one place (name or Qloo id) has the strongest affinity.",
     "parameters": {"type": "object", "properties": {"place": {"type": "string"}, "location": {"type": "string"}},
                    "required": ["place", "location"]}},
    {"name": "site_math", "description": "Barycenter site analysis over everything gathered so far (call after competitors and hotspots).",
     "parameters": {"type": "object", "properties": {}}},
]


class Site:
    """Places gathered during a run, for the barycenter analysis and the map."""

    def __init__(self, area):
        self.area, self.demand, self.competition, self.result = area, [], [], None
        self.geocache = {}

    def add(self, tool, result):
        if tool in ("area_taste", "hotspots"):
            self.demand += geo.extract(result)
        elif tool == "competitors":
            self.competition += geo.extract(result)

    def compute(self):
        self.demand = geo.locate(self.demand, self.area, cache=self.geocache)
        self.competition = geo.locate(self.competition, self.area, cache=self.geocache)
        self.result = geo.analyze(self.demand, self.competition)
        return self.result or {"error": "not enough located places for a barycenter (need 2+ demand points)"}

    def payload(self):
        pts = lambda ps: [{k: p[k] for k in ("name", "lat", "lon", "w")} for p in ps]  # noqa: E731
        return {**self.result, "demand": pts(self.demand), "competition": pts(self.competition)}


def call_tool(name, a, site=None):
    try:
        if name == "site_math":
            return site.compute()
        if name == "find_tags":
            return qloo.find_tags(a["query"])
        if name == "area_taste":
            return qloo.area_taste(a["location"], a.get("demographic"))
        if name == "competitors":
            return qloo.recommend_places(a["location"], a["tag_urns"], a.get("demographic"))
        if name == "hotspots":
            return qloo.where_popular(a["place"], a["location"])
        return {"error": f"unknown tool {name}"}
    except Exception as e:  # noqa: BLE001  the model sees the error and can adapt
        return {"error": f"{type(e).__name__}: {e}"}


def chat(messages):
    body = {"model": os.environ.get("LLM_MODEL", "turboderp/Qwen3.8-27B-exl3"), "messages": messages,
            "tools": [{"type": "function", "function": t} for t in TOOLS], "temperature": 0.2}
    req = urllib.request.Request(os.environ.get("LLM_BASE_URL", "https://hermes.ai.unturf.com/v1").rstrip("/") + "/chat/completions",
                                 data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json",
                                          "Authorization": f"Bearer {os.environ.get('LLM_API_KEY', 'none')}"})
    with urllib.request.urlopen(req, timeout=180) as r:
        return json.loads(r.read())["choices"][0]["message"]


def _names(obj, limit=6):
    """First entity names found anywhere in a harness result, for the progress view."""
    found = []

    def walk(o):
        if len(found) >= limit:
            return
        if isinstance(o, dict):
            if isinstance(o.get("name"), str) and o["name"] not in found:
                found.append(o["name"])
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(obj)
    return found


_FENCED_JSON = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.S | re.I)
_SCORE_BULLET = re.compile(r"^\s*[-*]\s*\**\s*(Taste match|Competition gap)\s*\**\s*:\s*(\d{1,3})\s*/\s*100\s*[-—–:]*\s*(.*)$",
                           re.I | re.M)


def _clamp(v):
    """Integer score in 0-100, or None when `v` is not a number."""
    if isinstance(v, bool):
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    if x != x:
        return None
    return int(min(100.0, max(0.0, x)) + 0.5)


def _clean_verdict(obj):
    """{key: {"score": int, "reason": str}} for the sub-scores the model gave validly, or None."""
    if not isinstance(obj, dict):
        return None
    out = {}
    for key, _ in SUBSCORES:
        v = obj.get(key)
        score, reason = (_clamp(v.get("score")), v.get("reason")) if isinstance(v, dict) else (_clamp(v), None)
        if score is not None:
            out[key] = {"score": score, "reason": " ".join(reason.split())[:200] if isinstance(reason, str) else ""}
    return out or None


def parse_verdict(text):
    """Splits the model's final message into (memo markdown without the JSON block, validated sub-scores or None).

    Falls back to the older "- Taste match: NN/100 - reason" bullets when no JSON block is present."""
    text = text or ""
    for m in reversed(list(_FENCED_JSON.finditer(text))):
        try:
            verdict = _clean_verdict(json.loads(m.group(1)))
        except ValueError:
            continue
        if verdict:
            return (text[:m.start()] + text[m.end():]).strip(), verdict
    bullets = {("taste_match" if name.lower().startswith("taste") else "competition_gap"): {"score": score, "reason": reason}
               for name, score, reason in _SCORE_BULLET.findall(text)}
    return text.strip(), _clean_verdict(bullets)


def build_scorecard(verdict, site_result):
    """Fit score and GO / ADJUST / NO-GO call computed in code from the model's sub-scores and the site maths."""
    if not verdict:
        return None
    bars = [{"key": k, "name": n, "score": verdict[k]["score"], "reason": verdict[k]["reason"], "source": "model"}
            for k, n in SUBSCORES if k in verdict]
    rec = (site_result or {}).get("recommended") or {}
    oi = _clamp(rec.get("opportunity_index"))
    if oi is not None:
        reason = f"Opportunity index {oi}/100 at the suggested spot"
        if rec.get("nearest_competitor_km") is not None:
            reason += f", {rec['nearest_competitor_km']} km from the nearest competitor"
        bars.append({"key": "location_strength", "name": "Location strength", "score": oi, "reason": reason,
                     "source": "site_math"})
    fit = int(sum(b["score"] for b in bars) / len(bars) + 0.5)
    method = ("Fit score = mean of the sub-scores, computed by TasteFit. Location strength is the site-maths opportunity "
              "index; taste match and competition gap are the analyst's reading of the Qloo results. "
              "GO at 70+, ADJUST at 45-69, NO-GO below 45.")
    complete = len(bars) == len(SUBSCORES) + 1
    if not complete:
        method += (" Some sub-scores are missing, so TasteFit gives no GO / ADJUST / NO-GO call; "
                   "the score shown is provisional.")
    call = next(call for call, floor in THRESHOLDS if fit >= floor) if complete else "INCOMPLETE"
    return {"call": call, "fit": fit, "bars": bars, "complete": complete, "method": method}


def memo_with_scorecard(memo, sc):
    """Rewrites the Verdict heading with the computed call and score, and puts a Scorecard section right after it."""
    head = f"## Verdict: {sc['call']} (fit score {sc['fit']})"
    card = "## Scorecard\n" + "\n".join(f"- {b['name']}: {b['score']}/100 - {b['reason']}".rstrip(" -")
                                        for b in sc["bars"]) + "\n\n"
    out, placed = [], False
    for part in re.split(r"(?m)^(?=##\s)", memo):
        if re.match(r"##\s*Scorecard\b", part, re.I):
            continue
        if not placed and re.match(r"##\s*Verdict\b", part, re.I):
            body = part.split("\n", 1)[1].strip() if "\n" in part else ""
            out.append(head + "\n" + body + "\n\n" + card)
            placed = True
        else:
            out.append(part)
    if not placed:
        out.insert(0, head + "\n\n" + card)
    return "".join(out).strip()


def _arguments(raw):
    """(args dict, problem) for a tool call; malformed JSON becomes a problem the model can fix."""
    if isinstance(raw, dict):
        return raw, None
    try:
        args = json.loads(raw or "{}")
    except ValueError as e:
        return {}, f"arguments are not valid JSON ({e.msg}); call the tool again with a JSON object"
    if not isinstance(args, dict):
        return {}, "arguments must be a JSON object; call the tool again"
    return args, None


def run(concept, location, max_steps=14, chat_fn=None):
    """Yields ('step', dict) while working, then ('geo', dict) for the map, ('scorecard', dict) and ('memo', markdown).

    Yields ('error', 'step_limit') when the model is still calling tools after `max_steps` turns."""
    chat_fn = chat_fn or chat
    messages = [{"role": "system", "content": SYSTEM},
                {"role": "user", "content": f"Concept: {concept}\nLocation: {location}"}]
    site = Site(location)
    for _ in range(max_steps):
        msg = chat_fn(messages)
        messages.append({k: v for k, v in msg.items() if k in ("role", "content", "tool_calls")})
        if not msg.get("tool_calls"):
            if site.result is None:
                site.compute()
            if site.result:
                yield "geo", site.payload()
            memo, verdict = parse_verdict(msg.get("content"))
            sc = build_scorecard(verdict, site.result)
            if sc:
                yield "scorecard", sc
                memo = memo_with_scorecard(memo, sc)
            yield "memo", memo
            return
        for tc in msg["tool_calls"]:
            fn = tc.get("function") or {}
            name = fn.get("name") or "unknown"
            args, problem = _arguments(fn.get("arguments"))
            if problem:
                result = {"error": problem}
            else:
                result = call_tool(name, args, site)
                site.add(name, result)
            failed = isinstance(result, dict) and bool(result.get("error"))
            yield "step", {"tool": name, "args": args, "names": _names(result),
                           "error": "lookup failed" if failed else None,
                           "site": result if name == "site_math" and isinstance(result, dict) and not failed else None}
            messages.append({"role": "tool", "tool_call_id": tc.get("id", ""),
                             "content": json.dumps(result, ensure_ascii=False)[:6000]})
    yield "error", "step_limit"


if __name__ == "__main__":
    import sys
    for kind, text in run(sys.argv[1], sys.argv[2]):
        print(("  -> " if kind == "step" else "\n") + str(text))
