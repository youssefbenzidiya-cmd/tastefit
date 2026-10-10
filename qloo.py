"""Minimal Qloo client (hackathon environment)."""
import json
import os
import urllib.parse
import urllib.request

BASE = os.environ.get("QLOO_BASE_URL", "https://hackathon.api.qloo.com")


def _get(path, params):
    key = os.environ.get("QLOO_API_KEY")
    if not key:
        raise RuntimeError("QLOO_API_KEY is not set")
    qs = urllib.parse.urlencode({k: v for k, v in params.items() if v not in (None, "")})
    req = urllib.request.Request(f"{BASE}{path}?{qs}", headers={"X-Api-Key": key, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=40) as r:
        return json.loads(r.read())


def search_tags(query, tag_type=None, take=8):
    """Tag ids for a free-text interest, e.g. 'moroccan cuisine' or 'specialty coffee'."""
    d = _get("/v2/tags", {"filter.query": query, "filter.tag.types": tag_type, "take": take})
    return [{"id": t.get("id"), "name": t.get("name"), "type": t.get("type")} for t in d.get("results", {}).get("tags", [])]


def search_entities(query, types="urn:entity:place", take=5):
    d = _get("/search", {"query": query, "types": types, "take": take})
    return [{"id": e.get("entity_id"), "name": e.get("name"), "address": (e.get("properties") or {}).get("address")}
            for e in d.get("results", [])]


def places(location_query, tags=None, age=None, price_min=None, price_max=None, take=15):
    """Places near a location ranked by affinity to the given taste signals."""
    d = _get("/v2/insights", {
        "filter.type": "urn:entity:place",
        "filter.location.query": location_query,
        "signal.interests.tags": ",".join(tags or []),
        "signal.demographics.age": age,
        "filter.price_level.min": price_min, "filter.price_level.max": price_max,
        "take": take,
    })
    out = []
    for e in d.get("results", {}).get("entities", []):
        p = e.get("properties") or {}
        out.append({"name": e.get("name"), "affinity": round(e.get("query", {}).get("affinity", 0) or 0, 3),
                    "popularity": round(e.get("popularity", 0) or 0, 3), "price_level": p.get("price_level"),
                    "rating": p.get("business_rating"), "address": p.get("address"),
                    "tags": [t.get("name") for t in (e.get("tags") or [])[:8]]})
    return out


def heatmap(location_query, tags, age=None):
    """Sub-areas of a location scored by affinity to the taste signals."""
    d = _get("/v2/insights", {"filter.type": "urn:heatmap", "filter.location.query": location_query,
                              "signal.interests.tags": ",".join(tags), "signal.demographics.age": age})
    cells = d.get("results", {}).get("heatmap", [])
    cells = sorted(cells, key=lambda c: -(c.get("query", {}).get("affinity", 0) or 0))
    return [{"geohash": c.get("location", {}).get("geohash"), "lat": c.get("location", {}).get("latitude"),
             "lng": c.get("location", {}).get("longitude"),
             "affinity": round(c.get("query", {}).get("affinity", 0) or 0, 3),
             "popularity": round(c.get("query", {}).get("popularity", 0) or 0, 3)} for c in cells[:12]]


def taste_of_area(location_query, age=None, take=20):
    """Which tags over-index among places in an area: the local taste profile."""
    d = _get("/v2/insights", {"filter.type": "urn:tag", "filter.tag.types": "urn:tag:genre:place",
                              "filter.location.query": location_query, "signal.demographics.age": age, "take": take})
    return [{"tag": t.get("name"), "id": t.get("tag_id") or t.get("id"),
             "affinity": round(t.get("query", {}).get("affinity", 0) or 0, 3)}
            for t in d.get("results", {}).get("tags", [])]
