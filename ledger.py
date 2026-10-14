"""Pre-registered prediction fingerprints. Standard library only.

A fingerprint is the SHA-256 of a canonical JSON object with exactly five fields: concept, area,
shortlist (concept and verdict only), verdict, and a UTC timestamp. Qloo payloads and personal
data are not part of it. Live runs put the fingerprint in the memo and do not write this file.
Committed batches live in data/ledger.json, which the /ledger page only reads.
"""
import hashlib
import html
import json
from pathlib import Path

FILE = Path(__file__).parent / "data" / "ledger.json"


def canonical(entry):
    """Stable JSON for the five public fields. Key order does not matter; this sort does."""
    payload = {
        "area": entry["area"],
        "concept": entry["concept"],
        "shortlist": [{"concept": s["concept"], "verdict": s["verdict"]} for s in entry["shortlist"]],
        "ts": entry["ts"],
        "verdict": entry["verdict"],
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def fingerprint(entry):
    return hashlib.sha256(canonical(entry).encode("utf-8")).hexdigest()


# Fixed labels for a future scored batch. These are protocol constants, not a fit score from Qloo.
VERDICT_P = {"GO": 0.66, "ADJUST": 0.50, "NO-GO": 0.33}


def probabilities(batch):
    """Brier inputs from verdict labels already inside the fingerprint. None when any label is outside the map."""
    rows = []
    for item in batch.get("shortlist") or []:
        p = VERDICT_P.get(item.get("verdict"))
        if p is None or not item.get("concept"):
            return None
        rows.append({"concept": item["concept"], "verdict": item["verdict"], "p": p})
    return rows or None


def brier_canonical(batch):
    """Locks the protocol probabilities. A batch with no complete verdict map has no canonical form."""
    forecasts = probabilities(batch)
    if forecasts is None:
        return None
    payload = {"check_on": batch["check_on"], "forecasts": forecasts, "sha256": batch["sha256"]}
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def brier_fingerprint(batch):
    raw = brier_canonical(batch)
    if raw is None:
        return None
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def from_shortlist(area, concepts, ts):
    """concepts are already public: concept, verdict, and optionally score. No Qloo fields."""
    shortlist = [{"concept": c["concept"], "verdict": c["verdict"]}
                 for c in concepts if c.get("concept") and c.get("verdict")]
    top = shortlist[0] if shortlist else {"concept": "", "verdict": ""}
    entry = {"concept": top["concept"], "area": area, "shortlist": shortlist, "verdict": top["verdict"], "ts": ts}
    entry["sha256"] = fingerprint(entry)
    return entry


def memo_line(entry):
    return ("- Prediction fingerprint `" + entry["sha256"] + "` "
            "(SHA-256 of the concept, the area, the shortlist verdicts and the UTC time; no Qloo payload). "
            "Pre-registered batches and the Brier check (Brier 1950) are listed at /ledger. "
            "The fingerprint is not a claim that the forecast will be right.")


def ensure_fingerprint(memo, entry):
    if not entry or "Prediction fingerprint" in memo:
        return memo
    return memo.rstrip() + "\n" + memo_line(entry) + "\n"


def _public_rent(value):
    number = float(value or 0)
    return int(number) if number.is_integer() else number


def street_canonical(outcome):
    """Street certificate. Unit ids, rents, assigned concepts, move counts, and the flexibility flag only."""
    units = [{"concept": match.get("concept") or "", "id": match.get("unit") or "",
              "rent": _public_rent(match.get("rent"))}
             for match in (outcome.get("matches") or [])]
    payload = {"needs_rent_flexibility": bool(outcome.get("needs_rent_flexibility")),
               "replacements": int(outcome.get("replacements") or 0),
               "swaps": int(outcome.get("swaps") or 0),
               "units": units}
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def street_fingerprint(outcome):
    return hashlib.sha256(street_canonical(outcome).encode("utf-8")).hexdigest()


def street_line(outcome):
    return ("- Street certificate `" + street_fingerprint(outcome) + "` "
            "(SHA-256 of the unit ids, the rents, the assigned concepts, the moves checked "
            "and whether the street needs rent flexibility; no Qloo payload).")


def ensure_street(memo, outcome):
    if not outcome or not outcome.get("matches") or "Street certificate" in (memo or ""):
        return memo
    return (memo or "").rstrip() + "\n" + street_line(outcome) + "\n"


def load():
    with FILE.open(encoding="utf-8") as f:
        return json.load(f)


def _esc(value):
    return html.escape(str(value if value is not None else ""), quote=True)


def page(doc=None):
    """HTML for /ledger. Every value from the file is escaped."""
    doc = load() if doc is None else doc
    protocol = doc.get("protocol") or {}
    rows = []
    for batch in doc.get("batches") or []:
        shown = batch.get("forecasts") or batch.get("shortlist") or []
        items = "".join(
            "<li>" + _esc(s.get("concept")) + ": " + _esc(s.get("verdict")) + "</li>"
            for s in shown)
        mapped = probabilities(batch)
        if mapped is None:
            lock_line = "<p>No probability was registered, so the Brier score is not defined for this batch.</p>"
        else:
            lock_line = ("<p>Brier inputs are the fixed verdict map (GO 0.66, ADJUST 0.50, NO-GO 0.33), "
                         "not a fit score. Lock <code>" + _esc(brier_fingerprint(batch)) + "</code></p>")
        rows.append(
            "<article class=\"scan\"><h2>" + _esc(batch.get("area")) + "</h2>"
            "<p>First listed concept <b>" + _esc(batch.get("concept")) + "</b> (not a ranking), verdict <b>"
            + _esc(batch.get("verdict")) + "</b>, registered " + _esc(batch.get("ts")) + ".</p>"
            "<p>Fingerprint <code>" + _esc(batch.get("sha256")) + "</code></p>"
            + lock_line
            + "<p>Check on " + _esc(batch.get("check_on")) + " against " + _esc(batch.get("check_against"))
            + ". Point " + _esc(batch.get("lat")) + ", " + _esc(batch.get("lon"))
            + " (a map point, not a vetted address).</p>"
            "<ul>" + items + "</ul>"
            "<p>" + _esc(batch.get("note")) + "</p></article>")
    body = "".join(rows) or "<p>No batch is pre-registered yet.</p>"
    return """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>TasteFit prediction ledger</title><link rel="stylesheet" href="/styles.css"></head>
<body><main class="wrap">
<p><a href="/">TasteFit</a></p>
<h1>Prediction ledger</h1>
<p>Each batch is a SHA-256 fingerprint of the concept, the area, the shortlist verdicts and the UTC time.
The file is committed in the repository. A live memo shows its own fingerprint and does not write this page.
No Qloo payload and no personal data are stored here.</p>
<h2>How the check is scored</h2>
<p>""" + _esc(protocol.get("brier")) + """</p>
<p>""" + _esc(protocol.get("outcome")) + """</p>
<p>""" + _esc(protocol.get("comparator")) + """</p>
""" + body + """
</main></body></html>
"""
