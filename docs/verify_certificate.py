#!/usr/bin/env python3
"""Find an improving move a Stable Street certificate missed.

    python3 docs/verify_certificate.py docs/published-street.json

Exit 0 when no replacement or swap raises the street surplus. Exit 1 and name
the move when one does. Exit 2 when the file is not a street certificate.

The surplus is the one in portfolio.py: stand-alone value plus the symmetric
neighbour effect on occupied edges. No Qloo call. Standard library only, plus
this repo.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import portfolio  # noqa: E402


def load(path):
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(doc, dict):
        raise ValueError("the file must be a JSON object")
    units = doc.get("units")
    concepts = doc.get("concepts")
    matches = doc.get("matches")
    if not isinstance(units, list) or not isinstance(concepts, list) or not isinstance(matches, list):
        raise ValueError("the file needs units, concepts and matches")
    if len(matches) != len(units):
        raise ValueError("matches must line up with units")
    return units, concepts, matches


def assignment(units, concepts, matches):
    index = {concept["id"]: i for i, concept in enumerate(concepts)}
    assigned = []
    for unit, match in zip(units, matches):
        if match.get("unit") != unit.get("id"):
            raise ValueError("match unit ids must follow the units list")
        concept = match.get("concept")
        if concept is None:
            assigned.append(None)
        elif concept not in index:
            raise ValueError(f"unknown concept {concept}")
        else:
            assigned.append(index[concept])
    return assigned


def values_for(units, concepts):
    return [[portfolio.standalone(concept, unit["rent"]) for unit in units] for concept in concepts]


def main(argv):
    if len(argv) != 2:
        print("usage: python3 docs/verify_certificate.py <street.json>", file=sys.stderr)
        return 2
    try:
        units, concepts, matches = load(argv[1])
        assigned = assignment(units, concepts, matches)
        values = values_for(units, concepts)
        effects = portfolio.effect_matrix(concepts)
        edges = portfolio.line_edges(len(units))
        moves = portfolio.improving_moves(assigned, values, effects, edges)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"not a street certificate: {exc}", file=sys.stderr)
        return 2
    base = portfolio.surplus(assigned, values, effects, edges)
    if not moves:
        replacements, swaps = portfolio.check_counts(assigned, len(concepts))
        print(f"No improving move: {replacements} replacements and {swaps} swaps checked. Surplus {base:.4f}.")
        return 0
    kind, left, right = moves[0]
    if kind == "replace":
        unit = units[left]["id"]
        current = concepts[assigned[left]]["id"] if assigned[left] is not None else "empty"
        incoming = concepts[right]["id"]
        nxt = portfolio._replace(assigned, left, right)
        print(f"Improving move: replace {current} in {unit} with {incoming}.")
    else:
        nxt = portfolio._swap(assigned, left, right)
        print(f"Improving move: swap {units[left]['id']} and {units[right]['id']}.")
    print(f"Surplus {base:.4f} to {portfolio.surplus(nxt, values, effects, edges):.4f}.")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
