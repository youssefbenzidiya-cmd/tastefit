"""Chicago footfall as a stated signal. Reads the committed aggregates. No network and no Qloo call.

The weights are stated priors, fixed before the backtest, not fitted to 2023-2024 survival.
A community-area count is the same for every concept. Concepts differ because each weight vector
loads on a different mix of evening drop-offs, weekend drop-offs, Divvy trip ends and CTA entries.
"""
import json
from pathlib import Path

FILE = Path(__file__).parent / "data" / "chicago_footfall.json"
COMPONENTS = ("evening_dropoffs", "weekend_dropoffs", "divvy_ends", "cta_boardings")
# Dinner concepts load on evening and weekend drop-offs. Daytime concepts load on rail entries and Divvy ends.
WEIGHTS = {
    "ramen shop": (0.45, 0.30, 0.10, 0.15),
    "Korean fried chicken": (0.40, 0.25, 0.15, 0.20),
    "Mediterranean bowls": (0.35, 0.25, 0.15, 0.25),
    "specialty coffee": (0.15, 0.20, 0.25, 0.40),
    "salad bowls": (0.15, 0.20, 0.25, 0.40),
    "taqueria": (0.40, 0.30, 0.15, 0.15),
    "Vietnamese pho": (0.45, 0.30, 0.10, 0.15),
    "smash burgers": (0.40, 0.25, 0.15, 0.20),
    "Lebanese grill": (0.45, 0.30, 0.10, 0.15),
    "bakery": (0.15, 0.25, 0.20, 0.40),
}
# Added to a 0-100 Taste Gap supply score. Stated, not fitted.
FOOTFALL_POINTS = 100
BACKTEST_START = "2023-01-01"


def load(doc=None):
    if doc is None:
        with FILE.open(encoding="utf-8") as handle:
            doc = json.load(handle)
    return doc


def assert_before_window(doc):
    """The committed windows must end before the backtest. A later file is not a valid signal."""
    for ds in doc["datasets"]:
        if ds["end"] >= BACKTEST_START or ds["start"] >= BACKTEST_START:
            raise ValueError(f"{ds['id']} overlaps the backtest window")


def city_shares(doc):
    """{community number: {component: share of the city total}}."""
    rows = doc["community_areas"]
    totals = {k: sum(r[k] for r in rows) for k in COMPONENTS}
    out = {}
    for row in rows:
        out[row["number"]] = {k: (row[k] / totals[k] if totals[k] else 0.0) for k in COMPONENTS}
    return out


def _area_row(doc, number):
    return next(r for r in doc["community_areas"] if r["number"] == number)


def concept_score(doc, community_number, concept, shares=None):
    """Weighted city share. Higher when this concept's stated mix matches where the counts sit."""
    shares = shares if shares is not None else city_shares(doc)
    mix = shares[str(community_number)]
    return sum(w * mix[k] for w, k in zip(WEIGHTS[concept], COMPONENTS))


def for_text(doc, text):
    """The community area named by a backtest area or by a community-area name, else None."""
    if not text:
        return None
    folded = text.casefold()
    for place in doc.get("backtest_areas") or []:
        if place["area"].casefold() in folded:
            row = _area_row(doc, place["community_area_number"])
            return row | {"matched": place["area"]}
    for row in doc["community_areas"]:
        if row["name"] and row["name"].casefold() in folded:
            return row | {"matched": row["name"].title()}
    return None


def signal(doc, text, shares=None):
    """Public sentence for one place. None when the committed table has no row."""
    row = for_text(doc, text)
    if row is None:
        return None
    shares = shares if shares is not None else city_shares(doc)
    mix = shares[row["number"]]
    tnp = next(d for d in doc["datasets"] if d["id"] == "m6dm-c72p")
    divvy = next(d for d in doc["datasets"] if d["id"] == "fg6s-gzvg")
    cta = next(d for d in doc["datasets"] if d["id"] == "5neh-572f")
    sentence = (
        f"Demand already there: {row['name'].title()} had "
        f"{mix['evening_dropoffs']:.1%} of Chicago evening rideshare drop-offs "
        f"({tnp['start']} to {tnp['end']}, dataset {tnp['id']}) and "
        f"{mix['weekend_dropoffs']:.1%} of weekend drop-offs, "
        f"{mix['divvy_ends']:.1%} of Divvy trip ends ({divvy['start']} to {divvy['end']}, dataset {divvy['id']}), "
        f"and {mix['cta_boardings']:.1%} of CTA 'L' station entries "
        f"({cta['start']} to {cta['end']}, dataset {cta['id']}). "
        "Community-area totals from the City of Chicago Data Portal, not a count at this door, "
        "and not a vetted address."
    )
    return {"label": "Demand already there", "community_area": row["name"].title(),
            "community_area_number": row["number"], "city_share": {k: round(mix[k], 4) for k in COMPONENTS},
            "sentence": sentence}
