"""Fail the pipeline on Critical/High container findings that are not in security/cve-allowlist.txt."""
import datetime
import json
import sys
from collections import Counter
from pathlib import Path

BLOCKING = {"Critical", "High"}
ALLOWLIST = Path("security/cve-allowlist.txt")


def load_allowlist(today):
    active, expired = {}, {}
    if not ALLOWLIST.exists():
        return active, expired
    for line in ALLOWLIST.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(maxsplit=2)
        if len(parts) < 3:
            sys.exit(f"cve-gate: malformed allowlist line (need id, expiry, reason): {line}")
        ident, expiry, reason = parts
        (active if datetime.date.fromisoformat(expiry) >= today else expired)[ident] = reason
    return active, expired


def main(path):
    report = Path(path)
    if not report.exists():
        sys.exit(f"cve-gate: {path} not found; container_scanning did not produce a report")
    vulns = json.loads(report.read_text()).get("vulnerabilities", [])
    active, expired = load_allowlist(datetime.date.today())

    counts = Counter(v.get("severity", "Unknown") for v in vulns)
    blocking, accepted = [], []
    for v in vulns:
        if v.get("severity") not in BLOCKING:
            continue
        ids = {i.get("value") for i in v.get("identifiers", [])}
        pkg = v.get("location", {}).get("dependency", {}).get("package", {}).get("name", "?")
        ver = v.get("location", {}).get("dependency", {}).get("version", "?")
        row = (v["severity"], ",".join(sorted(i for i in ids if i)), f"{pkg}@{ver}", v.get("solution", ""))
        (accepted if ids & active.keys() else blocking).append(row)

    Path("cve-metrics.txt").write_text(
        "".join(f"container_vulns_{s.lower()} {counts.get(s, 0)}\n" for s in ("Critical", "High", "Medium", "Low"))
        + f"container_vulns_blocking {len(blocking)}\n")

    print("Severity counts:", dict(counts))
    for ident in expired:
        print(f"Allowlist entry expired: {ident}")
    for sev, ids, pkg, fix in accepted:
        print(f"ACCEPTED {sev:8} {ids:40} {pkg:40} {fix}")
    for sev, ids, pkg, fix in blocking:
        print(f"BLOCKING {sev:8} {ids:40} {pkg:40} {fix}")
    if blocking:
        sys.exit(f"cve-gate: {len(blocking)} Critical/High finding(s). Upgrade the package or base image, "
                 f"or add a reviewed, expiring entry to {ALLOWLIST}.")
    print("cve-gate: no unaccepted Critical/High findings")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "gl-container-scanning-report.json")
