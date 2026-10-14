"""Build data/us_fafh.json from ACS 2024 5-year and BLS CEX 2024 Table 1203.

Standard library only. Uses the Census API when CENSUS_API_KEY is set; otherwise streams the
public table-based summary files. Writes the small aggregate and nothing else. Never prints the key.
"""
import io
import json
import os
import re
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import market_share

UA = "TasteFit/1.0 (hackathon research; https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43239248/tastefit)"
OUT = Path(__file__).parent / "data" / "us_fafh.json"
CEX_URL = "https://www.bls.gov/cex/tables/calendar-year/mean-item-share-average-standard-error/cu-income-before-taxes-2024.xlsx"
B19001_URL = "https://www2.census.gov/programs-surveys/acs/summary_file/2024/table-based-SF/data/5YRData/acsdt5y2024-b19001.dat"
B01003_URL = "https://www2.census.gov/programs-surveys/acs/summary_file/2024/table-based-SF/data/5YRData/acsdt5y2024-b01003.dat"
GAZ_URL = "https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2024_Gazetteer/2024_gaz_tracts_{st}.txt"
API = "https://api.census.gov/data/2024/acs/acs5"
# Summary-file geography id prefix, then county. Chicago is inside Cook County; the county is wider than the city.
COUNTIES = {"17031": "chicago", "42101": "philadelphia"}
NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
# Column order of Table 1203 after the item label. Checked against the header text before use.
BANDS = ("all", "lt_15000", "15_29999", "30_39999", "40_49999", "50_69999", "70_99999", "100_149999", "150_199999", "200_plus")
LABEL_BITS = (
    ("all", "all"),
    ("lt_15000", "less than"),
    ("15_29999", "$15,000"),
    ("30_39999", "$30,000"),
    ("40_49999", "$40,000"),
    ("50_69999", "$50,000"),
    ("70_99999", "$70,000"),
    ("100_149999", "$100,000"),
    ("150_199999", "$150,000"),
    ("200_plus", "$200,000"),
)


def _get(url, dest=None):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=180) as r:
        data = r.read()
    if dest:
        dest.write_bytes(data)
    return data


def _estimate(cell):
    """A non-negative count, or None. Census missing sentinels are negative and are not zero."""
    cell = (cell or "").strip()
    if cell in ("", "."):
        return None
    try:
        n = int(float(cell)) if "." in cell else int(cell)
    except ValueError:
        return None
    return n if n >= 0 else None


def _xlsx_rows(blob):
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        strings = []
        root = ET.fromstring(z.read("xl/sharedStrings.xml"))
        for si in root.findall("m:si", NS):
            strings.append("".join(t.text or "" for t in si.iter("{http://schemas.openxmlformats.org/spreadsheetml/2006/main}t")))
        sheet = ET.fromstring(z.read("xl/worksheets/sheet1.xml"))
        rows = []
        for row in sheet.findall("m:sheetData/m:row", NS):
            vals = {}
            for c in row.findall("m:c", NS):
                ref, kind = c.get("r"), c.get("t")
                v = c.find("m:v", NS)
                val = v.text if v is not None else ""
                if kind == "s" and val.isdigit():
                    val = strings[int(val)]
                vals["".join(ch for ch in ref if ch.isalpha())] = val
            rows.append((int(row.get("r")), vals))
        return rows


def cex_means(blob):
    """{band: integer mean dollars} for Food away from home, mean row, or raises."""
    rows = _xlsx_rows(blob)
    header = next(vals for _, vals in rows if vals.get("A", "").strip() == "Item")
    labels = []
    for col in "BCDEFGHIJK":
        labels.append(re.sub(r"\s+", " ", str(header.get(col, "")).replace("\n", " ")).strip().lower())
    for band, bit in LABEL_BITS:
        label = labels[BANDS.index(band)]
        if bit not in label:
            raise SystemExit(f"CEX column for {band} is {label!r}, expected {bit!r}")
    idx = next(i for i, (_, vals) in enumerate(rows) if vals.get("A", "").strip() == "Food away from home")
    mean = rows[idx + 1][1]
    if mean.get("A", "").strip() != "Mean":
        raise SystemExit("the row after Food away from home is not the mean")
    out = {}
    for band, col in zip(BANDS, "BCDEFGHIJK"):
        out[band] = int(float(mean[col]))
    return out, labels


def _gazetteer(st):
    """{11-digit geoid: (lat, lon)} for one state's tract gazetteer."""
    text = _get(GAZ_URL.format(st=st)).decode("latin-1")
    lines = text.splitlines()
    head = [h.strip() for h in lines[0].split("\t")]
    lat_i, lon_i, id_i = head.index("INTPTLAT"), head.index("INTPTLONG"), head.index("GEOID")
    out = {}
    for line in lines[1:]:
        parts = line.split("\t")
        if len(parts) <= max(lat_i, lon_i, id_i):
            continue
        geoid = parts[id_i].strip()
        if geoid[:5] not in COUNTIES:
            continue
        try:
            out[geoid] = (float(parts[lat_i]), float(parts[lon_i]))
        except ValueError:
            continue
    return out


def _from_api(key):
    """{geoid: {pop, counts}} or None when the API does not return JSON. Does not print the key."""
    want = ["B01003_001E", "B19001_001E"] + [f"B19001_{i:03d}E" for i in range(2, 18)]
    found = {}
    for state, county in (("17", "031"), ("42", "101")):
        q = urllib.parse.urlencode({"get": ",".join(want), "for": "tract:*", "in": f"state:{state} county:{county}", "key": key})
        try:
            raw = _get(API + "?" + q)
            rows = json.loads(raw)
        except (OSError, ValueError, json.JSONDecodeError):
            return None
        if not rows or not isinstance(rows, list) or not isinstance(rows[0], list):
            return None
        head = rows[0]
        for row in rows[1:]:
            rec = dict(zip(head, row))
            geoid = f"{rec['state']}{rec['county']}{rec['tract']}"
            counts = {f"E{i:03d}": _estimate(rec.get(f"B19001_{i:03d}E")) for i in range(2, 18)}
            found[geoid] = {"pop": _estimate(rec.get("B01003_001E")), "households": _estimate(rec.get("B19001_001E")),
                            "counts": counts}
    return found


def _summary_table(url, prefixes):
    """Stream a table-based summary file and keep tract rows for the two counties."""
    raw = _get(url).decode("utf-8", "replace")
    lines = raw.splitlines()
    head = lines[0].split("|")
    keep = {}
    for line in lines[1:]:
        if not any(line.startswith(p) for p in prefixes):
            continue
        parts = line.split("|")
        geoid = parts[0].split("US", 1)[-1]
        keep[geoid] = {head[i]: parts[i] if i < len(parts) else "" for i in range(len(head))}
    return keep


def _from_summary():
    prefixes = tuple("1400000US" + c for c in COUNTIES)
    income = _summary_table(B19001_URL, prefixes)
    pop = _summary_table(B01003_URL, prefixes)
    found = {}
    for geoid, row in income.items():
        counts = {f"E{i:03d}": _estimate(row.get(f"B19001_E{i:03d}")) for i in range(2, 18)}
        found[geoid] = {"pop": _estimate((pop.get(geoid) or {}).get("B01003_E001")),
                        "households": _estimate(row.get("B19001_E001")), "counts": counts}
    return found


def build(records, means, access):
    spend_means = {k: v for k, v in means.items() if k != "all"}
    cities = {name: [] for name in COUNTIES.values()}
    skipped = {"no_centroid": 0, "incomplete_bins": 0}
    points = {}
    for st in ("17", "42"):
        points.update(_gazetteer(st))
    for geoid, rec in records.items():
        city = COUNTIES.get(geoid[:5])
        if city is None:
            continue
        latlon = points.get(geoid)
        if latlon is None:
            skipped["no_centroid"] += 1
            continue
        annual, unassigned, _assigned = market_share.food_away_from_home(rec["counts"], spend_means)
        if annual is None or unassigned is None or rec["pop"] is None or rec["households"] is None:
            skipped["incomplete_bins"] += 1
            continue
        cities[city].append([geoid, round(latlon[0], 6), round(latlon[1], 6), rec["pop"], rec["households"], annual, unassigned])
    for rows in cities.values():
        rows.sort(key=lambda r: r[0])
    doc = {
        "source": {
            "census_access": access,
            "acs": {
                "vintage": "2024 5-year",
                "tables": ["B19001", "B01003"],
                "b19001_url": B19001_URL,
                "b01003_url": B01003_URL,
                "gazetteer": GAZ_URL,
                "geography": "Census tracts in Cook County IL (17031) and Philadelphia County PA (42101). "
                             "Philadelphia County is the city. Cook County is wider than Chicago; a point uses centroids within 1.2 km only.",
            },
            "cex": {
                "table": "Table 1203",
                "year": 2024,
                "item": "Food away from home",
                "statistic": "Mean",
                "unit": "annual dollars per consumer unit",
                "url": CEX_URL,
                "means": means,
            },
            "bin_band": {f"B19001_{code}": band for code, band in market_share.BAND_OF.items()},
            "unassigned_bin": "B19001_E012 ($60,000 to $74,999) crosses the CEX $70,000 boundary and is excluded from spend.",
            "assumption": "One ACS household is treated as one CEX consumer unit. No discounting.",
            "skipped": skipped,
            "tracts": {city: len(rows) for city, rows in cities.items()},
        },
        "tracts": cities,
    }
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    return doc


def main():
    key = os.environ.get("CENSUS_API_KEY", "").strip()
    print("census_api_key_set", bool(key))
    means, _labels = cex_means(_get(CEX_URL))
    print("cex_means", [means[b] for b in BANDS])
    records, access = None, None
    if key:
        records = _from_api(key)
        access = "Census API 2024 acs/acs5" if records else None
        if records is None:
            print("census_api_unavailable")
    if records is None:
        records = _from_summary()
        access = "public ACS 2024 5-year table-based summary file; CENSUS_API_KEY was not set" if not key else (
            "public ACS 2024 5-year table-based summary file; the Census API did not return JSON")
    doc = build(records, means, access)
    print("access", access)
    print("tracts", doc["source"]["tracts"], "skipped", doc["source"]["skipped"])
    print("wrote", OUT, "bytes", OUT.stat().st_size)


if __name__ == "__main__":
    sys.exit(main())
