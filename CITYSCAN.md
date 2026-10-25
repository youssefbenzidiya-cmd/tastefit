# City Scan: wiring notes

City Scan ranks every 400 m cell of a city for a restaurant concept, and ranks concepts for a given unit, from free
OpenStreetMap data. Maths and citations: `METHOD.md`. It is not wired into the app yet because `app.py`, `index.html`
and friends are being edited in parallel; this file is the plan.

## Files

| File | Role |
|---|---|
| `cityscan_math.py` | pure maths: kernel, spatial hash, Huff, Ripley L, logistic regression (IRLS + Laplace), AUC + bootstrap, spatial block folds, empirical Bayes, Moran's I, Getis-Ord Gi* |
| `cityscan.py` | concept → OSM tags (EN/FR/ES/AR), city packs, model pipeline, unit → concepts, backtest, Nominatim/Overpass fetch with TTL cache, CLI |
| `cityscan_overture.py` | **offline builder only** (DuckDB in a throwaway venv): adds Overture Places to a pack; never imported by the app |
| `cityscan_breakeven.py` | break-even **scenario model** (Huff captured demand → covers → revenue → margin vs rent and fixed costs; Monte Carlo, Sobol, tornado); editable city × profile defaults with sources |
| `cityscan_ablation.py` | **offline**: baselines, ablations and the forward-in-time test behind `ABLATION.md` |
| `cityscan_calibrate.py` | **offline**: calibrates the break-even runway model on official survival (Paris SIRENE, Chicago business licences via `--city chicago`; the survival study's loaders, read-only), bootstrap CIs, spatial and forward validation; `--embed` writes the aggregates |
| `cityscan_dvf.py` | **offline**: capitalisation test. Streams DVF Paris (about 2 MB per year, cached in `/tmp/cityscan-dvf`) and correlates commercial €/m² with cell demand measures. Writes `/tmp/cityscan-dvf-paris.json` |
| `cityscan_calibration.json` | calibrated aggregates only (θ, σ, κ, bootstrap draws, official 1-/3-year survival per group, covariate scaling); no venue-level data. The app reads it with `json` (stdlib) |
| `tests/test_cityscan_calibration.py` | calibration recovers known θ, σ and κ on simulated outcomes; conditional break-even covers formula; calibrated card and badge logic |
| `scan.html` | **Not shipped.** Plan for a standalone results page. The live app does not serve `/scan.html`. |
| `tests/test_cityscan_math.py`, `tests/test_cityscan.py`, `tests/test_cityscan_breakeven.py` | synthetic, offline tests |

What the score is (after `ABLATION.md`): cells are ranked by the point estimate of **suitability** among cells at
or above the city's median suitability; the Monte Carlo gives the 90% interval. Huff captured demand is shown and drives the break-even
scenario, but it is not in the ranking: on a forward test (venues first mapped after 2024-01-01), adding it made the
ranking significantly worse.

## CLI

```bash
# offline pack from saved responses (or let `scan --city` fetch live)
python3 cityscan.py pack "Casablanca, Morocco" --place place.json --query-out q.txt   # Overpass query for the bbox
python3 cityscan.py pack "Casablanca, Morocco" --place place.json --osm osm.json --out /tmp/cityscan-pack-casablanca.json
python3 cityscan.py scan --pack /tmp/cityscan-pack-casablanca.json --concept "specialty coffee" \
    --backtest --unit "Maarif" --unit "33.59,-7.63" --rent 15000 --rent 30000 --out /tmp/cityscan-casablanca.json

# ablation study (offline): OSM metadata of food venues (out meta) gives the forward-in-time test
python3 cityscan_ablation.py /tmp/cityscan-pack-casablanca.json /tmp/cityscan-raw/casablanca-food-meta.json \
    --cutoff 2024-01-01 --concept "specialty coffee" --concept "Moroccan fine dining" --out /tmp/cityscan-ablation.json

# survival calibration (offline; needs /tmp/survival_raw from the survival study), then Paris unit cards
python3 cityscan_calibrate.py --pack /tmp/cityscan-pack-paris.json --out /tmp/cityscan-calib-paris.json \
    --embed cityscan_calibration.json --boot 100
python3 cityscan.py scan --pack /tmp/cityscan-pack-paris.json --unit "rue Oberkampf (11e)@48.8650,2.3770" \
    --catalog "specialty coffee" --catalog "fast food" --rent 6000 --rent 12000 --out /tmp/cityscan-paris-units.json
# rent ceiling (founder) and landlord view: add the asking rent and the surface
python3 cityscan.py scan --pack /tmp/cityscan-pack-paris.json --unit "rue Oberkampf (11e)@48.8650,2.3770" \
    --catalog "specialty coffee" --catalog "fast food" --catalog "restaurant" --rent 6000 --rent 12000 \
    --asking-rent 9000 --surface 60 --out /tmp/cityscan-paris-units.json
# scan.html?src=…&view=unit&unit=0&concept=specialty%20coffee&focus=ceiling   (or focus=landlord)
python3 cityscan_dvf.py --pack /tmp/cityscan-pack-paris.json --out /tmp/cityscan-dvf-paris.json

# optional second source (builder machine only; duckdb never enters the app or requirements)
python3 -m venv /tmp/cityscan-venv && /tmp/cityscan-venv/bin/pip install duckdb
python3 cityscan_overture.py /tmp/cityscan-pack-casablanca.json /tmp/cityscan-pack-casablanca.json \
    --site-packages /tmp/cityscan-venv/lib/python3.14/site-packages
python3 cityscan.py scan --pack ... --compare-osm-only   # also rank with OSM alone, report the change
python3 cityscan.py scan --pack ... --osm-only           # ignore the Overture layer
```

Overture cost: one anonymous S3 query per city (Casablanca 56 s / 6,911 places; Paris 83 s / 46,148 places with
`threads=2`, `memory_limit='1GB'`). It adds about 0.5 MB (Casablanca) to 4 MB (Paris) to the pack.

Never commit raw packs (OSM extracts) or scratch outputs: they belong in `/tmp` or a cache. Only the published,
derived packs from `cityscan_publish.py` go in `packs/`, under ODbL with attribution (`packs/NOTICE.md`).

## Proposed routes in `app.py`

```python
elif url.path == "/scan":            # rank cells
    q = urllib.parse.parse_qs(url.query)
    city, concept = q.get("city", [""])[0][:120], q.get("concept", [""])[0][:300]
    if not city or not concept:
        return self._send(400, "text/plain", b"city and concept are required")
    res, _ = cityscan.analyze(cityscan.build_city(**_pack(city)), concept, top=min(int(q.get("top", ["10"])[0]), 25))
    self._send(200, "application/json", json.dumps(res, ensure_ascii=False).encode())
elif url.path == "/scan/unit":       # rank concepts for a point
    # params: city, lat, lon, [concepts=comma list] -> cityscan.unit_concepts(city_model, lat, lon, catalog)
elif url.path == "/scan.html":
    self._send(200, "text/html; charset=utf-8", SCAN_PAGE)
```

`_pack(city)` wraps `cityscan.fetch_pack(city)` and returns `{"info": pack["info"], "elements": pack["elements"]}`;
cache the built city model (`build_city`) per city in a dict with the same TTL so concepts reuse it.

Request parameters:

| Route | Params | Errors |
|---|---|---|
| `GET /scan` | `city` (≤120 chars), `concept` (≤300), `top` (1–25, default 10), optional `backtest=1` | 400 missing input, 404 city not found, 503 Overpass busy |
| `GET /scan/unit` | `city`, `lat`, `lon`, optional `concepts` | 400 missing input or point outside the scanned area |

Response of `/scan` (one object; `scan.html` also accepts `{"scans": [...], "backtest": [...], "units": [...]}`):

```text
city        {query, name, display_name, center, bbox}
concept     {text, recognized, profile, label, osm{amenity, cuisine, diet}}
grid        {bbox, cell_m, rows, cols, cells_total, cells_study}
params      {kernel_lambda_m, huff_alpha, huff_beta, huff_floor_m, huff_radius_m, ripley_r_m, l2, draws, pool, seed,
             agglomeration_gamma, options{}, ranking}
model       {fitted, note, presence_set (narrow|broad), presence_note, n_presences, n_competitors, n_background,
             estimation, constrained_at_zero[], intercept, coefficients{}, coefficient_se{}, demand_weights{},
             validation{auc_cv, auc_ci95[2], top20_capture, folds, scheme, platt{a, b, brier, base_rate}} | null}
diagnostics {moran_residuals{I, expected, variance, z}, gi_star{hot_cells_z_gt_1.96, cold_cells_z_lt_-1.96}}
top[]       {rank, id, centroid{lat, lon}, bounds[[s,w],[n,e]], neighborhood,
             opportunity{rank_score (20th pct), lcb (5th), median, ucb (95th), point}, confidence_width, eligible,
             suitability, relative_demand, logit, logit_contributions{}, reasons_hidden_ci_crosses_0,
             huff_share, captured_demand_index, agglomeration{l_excess_m, term}, gi_star_z,
             reasons_en[], reasons_fr[], watch_out_en[], watch_out_fr[], market_en, market_fr,
             nearest_competitors[{name, m}], counts_ci90{}, data_completeness, source_gaps{cat: {osm, overture,
             gap_osm, gap_overture}}}
cells[]     {id, bounds, opp, rank?, eligible?: 0, c?}   for the choropleth (c = OSM/Overture completeness)
units[]     (CLI / `/scan/unit`) {point, cell, neighborhood, place, concepts[{concept, tier ("ranked" |
             "insufficient data"), city_percentile, calibrated_probability, base_rate, n_presences, … card fields,
             breakeven{label "SCENARIO MODEL - not a forecast", currency, relative_demand, inputs{name: {dist, lo,
             mode, hi | median, sigma, source}}, by_rent[{rent, p_break_even, profit_p10_p50_p90, revenue_p50,
             covers_day_p10_p50_p90, rent_to_revenue_p50, payback_months_p50, p_payback_within_36m}],
             sensitivity{profit_at_medians, sobol[{name, first_order, total}], tornado[{name, low, high, swing}],
             depends_most_on_en, depends_most_on_fr}} | null}]}
sources     ["OpenStreetMap", "Overture Maps Places"?]
coverage    null | {city{data_confidence, data_confidence_food, osm_pois, overture_pois, matched, union_pois, ...},
                    chapman{restaurant|cafe|bar|food|...: {osm, overture, matched, union, N_hat, N_ci95,
                    missed_by_both, detection_osm, detection_overture, detection_union}}, kappa_min}
attribution, limits[], timings
```

## UI hook

- Add a link "Where should I open?" in the `index.html` nav to `/scan.html`, and on the memo page a button "Scan the
  whole city for this concept" that opens `/scan.html?city=…&concept=…` (prefill the form from the query string).
- `scan.html` keeps the same fonts, pine/saffron palette and the pinned Leaflet 1.9.4 with SRI, and keeps the
  OpenStreetMap attribution on the map (AGENTS.md rule 4).
- The broker card is `/scan/unit`: concepts ranked by within-concept city percentile and calibrated probability
  ("insufficient data" under 30 venues), rank score ± half-width, reasons whose coefficient CI excludes 0, three
  nearest competitors, and the break-even scenario (rent buttons, editable assumptions, tornado chart, banner
  "SCENARIO MODEL · NOT A FORECAST"). `/scan/unit` should accept `rent` (repeatable) and pass it as `rents`.

## Production on Cloud Run free tier

`scan.html` is not shipped. The live app is pin-first on Cloud Run
(https://tastefit-776637567140.us-central1.run.app). City Scan JSON (`/scan`, `/scan/unit`) is served
from prebuilt packs. The notes below are pack-build constraints, not a claim that Render is live.

- **Overpass**: one bounded query per city per TTL (6 h in memory). Dense cities are heavy (Paris: 16 MB, 34 s;
  the main instance answered 504 twice at `maxsize` 512 MB, then 200 at 256 MB). Serialise fetches (`_fetch_lock`,
  already there), send the User-Agent, retry on the mirror, and do not fan out per concept. Better: build packs offline
  (a scheduled CI job or a cron on a laptop, `cityscan.py pack`) for the launch cities and ship them as a cache
  outside git (object storage), refreshed weekly. ODbL attribution travels with the pack.
- **Overture**: never queried from the server. DuckDB is a ~50 MB native wheel and a city query reads hundreds of MB
  of Parquet ranges; it belongs in the same offline pack job. The server only reads `pack["overture"]` (plain JSON),
  so the app stays standard library only. Show "© Overture Maps Foundation" next to the OSM attribution whenever
  `sources` includes it (CDLA-Permissive-2.0 asks for attribution of the data, not share-alike).
- **Nominatim**: one call per new city, ≤1 req/s (throttle shared with `geo.py` via `geo._last_call`), cached 7 days.
- **Memory**: a Paris pack is ~4 MB JSON, the built city model a few tens of MB in Python objects; cache at most
  ~3 city models (LRU) on a 512 MB instance.
- **CPU**: per concept 5–7 s single-threaded (100 Monte Carlo draws over 150 candidate cells, spatial CV adds ~1 s);
  unit mode over the 16-concept catalog 11–13 s. The `/run` SSE pattern fits: stream progress events, or lower
  `draws` to 50 for interactive use and keep 100 for exported reports. Free instances sleep after idle, so the first
  scan after wake-up also pays the pack fetch: pre-warm the launch cities.
- **Abuse**: cap `top`, reject concepts over 300 chars, rate-limit `/scan` per IP (it is far heavier than `/run`).
- **Privacy**: only a city name and a concept leave the server (to Nominatim/Overpass). No Qloo data is used or stored
  by City Scan.
- **Tests**: the CI `unit-tests` job already runs `unittest discover`, so the new tests run there; ruff's selected
  rules pass on the new files.
