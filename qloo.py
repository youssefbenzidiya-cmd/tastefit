"""Qloo access through the official hackathon harness (`qloo exec`), the event's supported surface.

The harness reads QLOO_API_KEY and QLOO_BASE_URL from the environment and returns normalized JSON.
"""
import collections
import json
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path

_LOCAL_BIN = Path(__file__).parent / "node_modules" / ".bin" / "qloo"
QLOO_BIN = os.environ.get("QLOO_BIN") or (str(_LOCAL_BIN) if _LOCAL_BIN.exists() else shutil.which("qloo") or "qloo")


class LRUCache:
    """Thread-safe in-memory LRU with a TTL. Process memory only: Qloo results never reach disk (AGENTS.md rule 2)."""

    def __init__(self, maxsize=256, ttl=6 * 3600, clock=time.monotonic):
        self.maxsize, self.ttl, self.clock = maxsize, ttl, clock
        self.data, self.lock = collections.OrderedDict(), threading.Lock()

    def get(self, key):
        with self.lock:
            hit = self.data.get(key)
            if hit is None:
                return None
            stamp, value = hit
            if self.clock() - stamp >= self.ttl:
                del self.data[key]
                return None
            self.data.move_to_end(key)
            return value

    def put(self, key, value):
        with self.lock:
            self.data[key] = (self.clock(), value)
            self.data.move_to_end(key)
            while len(self.data) > self.maxsize:
                self.data.popitem(last=False)


CACHE = LRUCache()


def exec_cached(operation, params, timeout=90):
    """(result, from_cache). Only ok/empty results are cached, so failures are retried on the next run."""
    key = operation + json.dumps(params, sort_keys=True)
    hit = CACHE.get(key)
    if hit is not None:
        return hit, True
    result = exec_workflow(operation, params, timeout)
    if isinstance(result, dict) and result.get("status") in ("ok", "empty"):
        CACHE.put(key, result)
    return result, False


def exec_workflow(operation, params, timeout=90):
    if not os.environ.get("QLOO_API_KEY"):
        raise RuntimeError("QLOO_API_KEY is not set")
    env = {**os.environ, "QLOO_BASE_URL": os.environ.get("QLOO_BASE_URL", "https://hackathon.api.qloo.com")}
    env.setdefault("QLOO_TRUSTED_BASE_URL", env["QLOO_BASE_URL"])
    p = subprocess.run([QLOO_BIN, "exec", operation, "--input", json.dumps({k: v for k, v in params.items() if v})],
                       capture_output=True, text=True, timeout=timeout, env=env)
    out = p.stdout.strip()
    if not out:
        raise RuntimeError(p.stderr.strip()[-500:] or f"qloo exec {operation} failed")
    try:
        return json.loads(out)
    except ValueError:
        raise RuntimeError(f"qloo exec {operation} returned non-JSON output") from None


def find_tags(query, limit=8):
    return exec_workflow("find_tags", {"query": query, "limit": limit})


def recommend_places(location, signal_tags=None, demographic=None, include_tags=None, limit=12):
    """Places in `location` ranked by affinity to the tag/audience profile."""
    return exec_workflow("recommend", {
        "target_type": "place", "signal_tags": signal_tags, "include_tags": include_tags,
        "signal_location": location if demographic else None, "demographic": demographic,
        "filter_location": location, "limit": limit})


def area_taste(location, demographic=None, limit=15):
    """What the local audience over-indexes on: top places when the signal is the area itself."""
    return exec_workflow("recommend", {"target_type": "place", "signal_location": location,
                                       "demographic": demographic, "filter_location": location, "limit": limit})


def where_popular(entity, within, limit=10):
    """Sub-areas of `within` where one entity (e.g. a benchmark place) has the strongest affinity."""
    return exec_workflow("where_popular", {"entity": entity, "within": within, "limit": limit})


# Parameter builders for the tenant-match agent: (operation, params) pairs it runs through exec_cached.

def p_area_taste(location, limit=10):
    return "recommend", {"target_type": "place", "signal_location": location, "filter_location": location,
                         "limit": limit, "explain": False}


def p_concept_places(signal_location, filter_location, tag_urn, limit=10):
    """Places of one concept tag in `filter_location`, ranked by the affinity of the `signal_location` audience."""
    return "recommend", {"target_type": "place", "signal_location": signal_location, "filter_location": filter_location,
                         "include_tags": [tag_urn], "limit": limit, "explain": False}


def p_entity_tags(entity_ids, limit=20):
    return "entity_tags", {"entities": list(entity_ids)[:10], "entity_type": "place", "limit": limit}


def p_demographics(entity_id):
    return "audience_demographics", {"entity": entity_id, "entity_type": "place"}


def p_find_tags(query, limit=6):
    return "find_tags", {"query": query, "limit": limit}


def p_rank(option_ids, location):
    """One call so the scores are comparable (Qloo: scores from separate rank calls are not)."""
    return "rank", {"options": list(option_ids)[:10], "option_type": "place", "signal_location": location}


def p_compare(group_a, group_b, limit=8):
    return "compare_audiences", {"group_a": list(group_a)[:10], "group_b": list(group_b)[:10], "limit": limit}


def p_trends(brand, start_date, end_date, limit=20):
    """Qloo trending covers brands, not single places, so a concept's momentum is read from a reference chain."""
    return "trends", {"entities": [brand], "entity_type": "brand", "start_date": start_date, "end_date": end_date,
                      "limit": limit}


def p_where_popular(entity_id, within, limit=8):
    return "where_popular", {"entity": entity_id, "entity_type": "place", "within": within, "limit": limit}
