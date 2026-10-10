"""Qloo access through the official hackathon harness (`qloo exec`), the event's supported surface.

The harness reads QLOO_API_KEY and QLOO_BASE_URL from the environment and returns normalized JSON.
"""
import json
import os
import shutil
import subprocess
from pathlib import Path

_LOCAL_BIN = Path(__file__).parent / "node_modules" / ".bin" / "qloo"
QLOO_BIN = os.environ.get("QLOO_BIN") or (str(_LOCAL_BIN) if _LOCAL_BIN.exists() else shutil.which("qloo") or "qloo")


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
    return json.loads(out)


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
