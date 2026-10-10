"""TasteFit agent: tests a restaurant/cafe concept against the local taste of a neighborhood."""
import json
import os
import urllib.request

import qloo

SYSTEM = """You are TasteFit, an analyst that decides whether a food & drink concept fits the local taste of a place,
before a founder signs a lease or a lender funds the opening. You only trust data from your tools (Qloo taste graph).

Method:
1. Translate the concept into Qloo tags with find_tags (cuisine, format, vibe). Use only returned tag ids.
2. Read the area's taste with area_taste, and the places locals already love for this concept with competitors.
3. Use hotspots to find the sub-areas where this taste is strongest.
4. Look for a gap: high affinity for the concept's tags but few strong places serving it.
Finish with a memo in Markdown:
## Verdict: GO / ADJUST / NO-GO (fit score 0-100)
## Why (3-5 bullets, each citing a number from the tools)
## Best location within the area
## Positioning: menu signatures, price tier, vibe (grounded in the tags)
## Risks
Be concise and specific. Never invent places or numbers."""

TOOLS = [
    {"name": "find_tags", "description": "Qloo tag ids for a free-text taste, e.g. 'specialty coffee', 'moroccan', 'brunch'.",
     "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}},
    {"name": "area_taste", "description": "Place tags that over-index in an area (its taste profile).",
     "parameters": {"type": "object", "properties": {"location": {"type": "string"},
                                                     "age": {"type": "string", "enum": ["35_and_younger", "36_to_55", "55_and_older"]}},
                    "required": ["location"]}},
    {"name": "competitors", "description": "Places in an area ranked by affinity to the concept tags (existing competition and benchmarks).",
     "parameters": {"type": "object", "properties": {"location": {"type": "string"},
                                                     "tag_ids": {"type": "array", "items": {"type": "string"}},
                                                     "price_max": {"type": "integer"}},
                    "required": ["location", "tag_ids"]}},
    {"name": "hotspots", "description": "Sub-areas of a city scored by affinity to the concept tags.",
     "parameters": {"type": "object", "properties": {"location": {"type": "string"},
                                                     "tag_ids": {"type": "array", "items": {"type": "string"}}},
                    "required": ["location", "tag_ids"]}},
]


def call_tool(name, a):
    try:
        if name == "find_tags":
            return qloo.search_tags(a["query"])
        if name == "area_taste":
            return qloo.taste_of_area(a["location"], a.get("age"))
        if name == "competitors":
            return qloo.places(a["location"], a["tag_ids"], price_max=a.get("price_max"))
        if name == "hotspots":
            return qloo.heatmap(a["location"], a["tag_ids"])
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
            messages.append({"role": "tool", "tool_call_id": tc["id"], "content": json.dumps(result, ensure_ascii=False)})
    yield "memo", "Stopped: step limit reached."


if __name__ == "__main__":
    import sys
    for kind, text in run(sys.argv[1], sys.argv[2]):
        print(("  -> " if kind == "step" else "\n") + text)
