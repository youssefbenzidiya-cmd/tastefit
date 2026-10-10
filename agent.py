"""TasteFit agent: tests a restaurant/cafe concept against the local taste of a neighborhood."""
import json
import os
import urllib.request

import qloo

SYSTEM = """You are TasteFit, an analyst that checks whether a food & drink concept fits the local taste of an area,
before a founder commits to a lease. Your only evidence is your tools (the Qloo taste graph).

Method:
1. Translate the concept into Qloo tags with find_tags (cuisine, format, vibe). Several tags can share a name with
   different types; pick the one that matches the concept and use its tag URN. If none matches, refine the query.
2. Read the area's overall taste with area_taste, then the places that best match the concept's tags with competitors.
3. Take the strongest benchmark place and call hotspots to see which sub-areas over-index for that taste.
4. Look for a gap: strong affinity for the concept's tags but few strong places serving it.
If a tool returns status "needs_input", fix the inputs and retry once.
Finish with a memo in Markdown:
## Verdict: GO / ADJUST / NO-GO (fit score 0-100)
## Evidence (3-5 bullets, each citing a place, tag or number returned by a tool)
## Best location within the area
## Positioning: menu signatures, price tier, vibe (grounded in the tags)
## Risks
## Limits: Qloo describes aggregate group affinities, not foot traffic, rents or individual behaviour; say what this memo does not establish.
Be concise. Never invent places, tags or numbers; separate Qloo results from your interpretation."""

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
]


def call_tool(name, a):
    try:
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


def run(concept, location, max_steps=14):
    """Yields ('step', text) events while working, then ('memo', markdown)."""
    messages = [{"role": "system", "content": SYSTEM},
                {"role": "user", "content": f"Concept: {concept}\nLocation: {location}"}]
    for _ in range(max_steps):
        msg = chat(messages)
        messages.append({k: v for k, v in msg.items() if k in ("role", "content", "tool_calls")})
        if not msg.get("tool_calls"):
            yield "memo", msg.get("content") or ""
            return
        for tc in msg["tool_calls"]:
            args = json.loads(tc["function"].get("arguments") or "{}")
            result = call_tool(tc["function"]["name"], args)
            yield "step", f"{tc['function']['name']}({json.dumps(args, ensure_ascii=False)}) -> {json.dumps(result, ensure_ascii=False)[:400]}"
            messages.append({"role": "tool", "tool_call_id": tc["id"], "content": json.dumps(result, ensure_ascii=False)[:6000]})
    yield "memo", "Stopped: step limit reached."


if __name__ == "__main__":
    import sys
    for kind, text in run(sys.argv[1], sys.argv[2]):
        print(("  -> " if kind == "step" else "\n") + text)
