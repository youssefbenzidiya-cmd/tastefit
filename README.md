---
title: TasteFit
emoji: 🍜
colorFrom: yellow
colorTo: red
sdk: docker
app_port: 7860
license: mit
---

# TasteFit

A taste-market-fit agent for new restaurants and cafes.

**Live demo:** https://tastefit.onrender.com (free instance; the first request after idle can take about 50 seconds to wake up).

Before a founder signs a lease, or a commercial broker proposes a unit, TasteFit checks whether the planned concept fits the local taste of a specific neighborhood. It uses the Qloo Taste AI API to read what the area's audience likes, who already serves it, and where demand concentrates, then writes an investment-style **GO / ADJUST / NO-GO** memo with a fit score, the best location, a positioning plan and the key risks.

## How it works

An LLM agent runs a tool loop over the official Qloo hackathon harness (`qloo exec`):

| Agent tool | Qloo workflow | Purpose |
|---|---|---|
| `find_tags` | `find_tags` | Turn the concept ("Korean fried chicken counter") into Qloo tag URNs, choosing the right one among same-name tags |
| `area_taste` | `recommend` (place, location signal) | Places the area's audience has the highest affinity for |
| `competitors` | `recommend` (place, concept tags, location filter) | Existing places that match the concept: competition and benchmarks |
| `hotspots` | `where_popular` | Sub-areas where the strongest benchmark over-indexes |
| `site_math` | local (`geo.py`) | Barycenter site analysis over everything gathered, shown on a map |

### Site math (`geo.py`)

Hotspot cells come with coordinates; places carry an address, geocoded with OpenStreetMap Nominatim (keyless, max 1 request per second).

- **Demand center**: affinity-weighted barycenter of the places and hotspot cells the area favors, `c = Σ wᵢ pᵢ / Σ wᵢ`.
- **Competition center**: the same barycenter over places that match the concept.
- **Catchment radius**: weighted standard distance of demand around its center.
- **Suggested spot**: on a 41×41 grid over the area, maximize `Σ wᵢ·K(x, dᵢ) − 0.7 · Σ wⱼ·K(x, cⱼ)`, with a Gaussian kernel `K` whose width follows the demand spread (0.3 to 3 km). This is a gravity model: be close to demand, away from look-alike competitors.
- **Opportunity index**: the score at the suggested spot as a share of the peak demand pull (0 to 100).

The map uses Leaflet with OpenStreetMap tiles. The suggested spot points to a block to scout, not a vetted address.

The memo only cites places, tags and numbers returned by Qloo, plus OpenStreetMap supply counts attributed as such in tenant memos, keeps Qloo results separate from interpretation, and ends with a **Limits** section: Qloo describes aggregate group affinities, not foot traffic, rents or individual behaviour. No personal data is sent to Qloo; inputs are a concept and a place name.

### City Scan and rent ceiling (`/scan`, `/scan/unit`)

Two read-only JSON endpoints serve prebuilt city packs (`packs/`, built offline by `cityscan_publish.py`; method in `METHOD.md`). The server never runs the model and makes no network call for them.

- `GET /scan?city=paris&concept=specialty%20coffee&top=10`: the cells of the city where the concept fits best, with reasons, nearest competitors and a 90% Monte Carlo interval on each score.
- `GET /scan/unit?city=paris&lat=48.865&lon=2.377`: for one unit, the highest monthly rent each tenant type (restaurant, fast food, bar/café) can pay and still reach the city's official 3-year survival rate, or "not viable at any rent" where even rent-free falls short. This is calibrated on official survival for Paris (SIRENE, EUR) and Chicago (business licences, USD). Other cities say "not calibrated" rather than borrow these numbers.
- Each city's map data is first checked against an official business register where one exists: the share of registered food businesses that have a mapped place within 50 m, corrected for matches expected by chance (the same test after shifting the register 300 m). Cities below 0.70 get the reason and no numbers.
  - Paris scores 0.84 (INSEE SIRENE).
  - Chicago scores 0.88 (City of Chicago business licences).
  - Philadelphia scores 0.60 (L&I commercial activity licences), so it is shipped as data-thin.
- **Does captured spend cover the asking rent?** Chicago and Philadelphia only. Census tracts in Cook County and Philadelphia County (ACS 2024 5-year, B19001 household income and B01003 population; public summary file) whose centroids fall within 1.2 km are multiplied by BLS Consumer Expenditure Survey 2024 Table 1203 mean food-away-from-home dollars for the matching income band. The ACS $60,000 to $74,999 bin crosses the CEX $70,000 line, so those households are left out and the spend is a lower bound. Share is 1/(1+n) among the new place and every OpenStreetMap food venue within 1.2 km (equal utility, the conditional logit when utilities are tied, McFadden 1974; food-away-from-home is not split by cuisine). Monthly captured spend is that annual figure times the share, divided by 12, with no discounting. It is a rough ceiling under those assumptions, before costs, not profit and not a forecast: missing mapped venues push it up, and workers and visitors are not counted, which pushes it down. One household is treated as one consumer unit. Cities below the 0.70 map-quality gate still get no opportunity scores and no rent ceilings. Philadelphia's spend note adds that its venue file failed that check, so a missing venue would overstate the share. A Chicago backtest of 2023-2024 openings found no measurable gain over a citywide baseline; this estimate does not change that. Outside the US the field is "not available". The cached aggregate is `data/us_fafh.json` (`build_us_fafh.py`).
- Without a register, the check falls back to the share of food places confirmed by two map sources (below 40% fails, a provisional threshold). Casablanca (18%) is shipped as that case.
- The Chicago calibration replicates the Paris finding: measured location demand does not predict 3-year survival in either city. So the ceiling mostly reflects each tenant type's calibrated revenue against its costs, and Chicago figures are city-level per tenant type rather than a location signal (`METHOD.md`, section 11).

## Qloo

TasteFit is a new project, started Oct 10, 2026. The broker / landlord mode ("who should rent this unit?") was built for the Qloo hackathon on top of it.

### Workflows used and why

All calls go through the official harness (`qloo exec`); 8 of its 9 workflows are used.

| Workflow | Where | Why |
|---|---|---|
| `recommend` (place, location signal) | both modes | What the area's audience over-indexes on: the taste a tenant would sell into |
| `recommend` (place, tag + location filter) | both modes | Benchmarks for a concept in the city, and the existing supply inside the area where no OpenStreetMap venue file ships for the city |
| `entity_tags` | tenant | The tags behind the area's favourite places (cuisine, vibe, price) |
| `audience_demographics` | tenant | Age and gender skew of the area's top place, shown as relative bars |
| `find_tags` | both modes | Turn a concept into a Qloo tag URN; the choice and the alternatives are shown |
| `rank` | tenant | One call ranks every benchmark for the area's audience, so the demand scores are comparable (scores from separate calls are not); a second ranks the same benchmarks for the whole city, for the over-index |
| `compare_audiences` | tenant | Shared taste between the area's favourites and the top concepts' benchmarks |
| `where_popular` | both modes | Where the top benchmark's audience concentrates, and how far the unit is from it |
| `trends` | tenant | Reach of the top concept's reference chain (trends cover brands, not places) |

`describe` is not needed: `recommend` already returns each place's description. A tenant run makes at most 25 Qloo calls; a cache that lives for one request makes repeated calls free and is dropped when the run ends.

### One step, request to result (redacted)

For a unit clicked in Wicker Park, Chicago, step 5 ranks the six concepts' Chicago benchmarks for the local audience:

```json
{"workflow": "rank",
 "input": {"options": ["<benchmark id: taqueria>", "<benchmark id: ramen shop>", "..."],
           "option_type": "place", "signal_location": "Wicker Park"}}
```

Qloo returns an affinity per option. TasteFit turns the order into a demand score from 0 to 100 within that call, adds the white-space count from `recommend` with the concept's tag and the area as filter, and the overlap from `compare_audiences`, then joins the City Scan rent ceiling for the clicked cell. The page shows every step with the request it sent; no Qloo response is stored.

### Does Qloo help? Backtest (Chicago, offline)

`backtest_chicago.py` checks the tenant agent's demand ranking against what actually opened and survived. The ground truth is the City of Chicago Food Inspections register: a restaurant or bakery opened when its licence's first passing License inspection ever falls in 2023-2024 (licences that passed one earlier, such as renewals, are left out), and survived when no later inspection says Out of Business and it was inspected again since April 2025. Names are matched to the ten catalog concepts by keyword (200 of 1,751 openings match). For each area (1.2 km radius), the ten concepts are ordered by one Qloo `rank` call, and by the citywide share of surviving openings without that area (a "what is opening everywhere" baseline). The output is numbers only (`backtest_chicago.json`).

| 9 Chicago areas, 31 surviving openings | Qloo demand | Citywide baseline | Random |
|---|---|---|---|
| Surviving openings whose concept is in the top 3 | 29% | 68% | 30% |
| Mean rank correlation with surviving openings | 0.02 | 0.31 | 0 |
| Survival rate of openings in the top-3 concepts (vs. the rest) | 64% of 14 (44% of 50) | 49% of 43 (48% of 21) | |

Each opening counts once, for the nearest area centre, because neighbouring areas overlap. 3 of the 12 areas are not scored (Pilsen, Chinatown, River North): they had no surviving catalog opening, or Qloo's rank call returned no usable ranking (2 areas). So this is 9 areas and 31 openings, a small sample.

Result: **no gain**. Qloo's demand ranking did no better than random at naming the concepts that opened and survived, and the citywide baseline did clearly better: the Qloo-minus-baseline difference has a 90% bootstrap interval of -66 to -4 points. Openings in the concepts Qloo ranked highest did survive more often (64% vs. 44%), but on 14 openings that is not evidence. Earlier runs the same day, before each opening was counted once, gave Qloo between 30% and 54%, so the rank output also varies between runs. The LLM-only column needs a working LLM for every area and is not reported. Qloo and the LLM describe today's taste, after these places opened, so this checks agreement with what survived, not a forecast made in 2023.

**Taste Gap Index.** The second hypothesis was that Qloo helps once combined with supply: a concept the area loves *more than the city does* and that is *under-served nearby*. `tgi.py` computes, per concept:

- **Over-index D**: the same benchmarks ranked once for the area and once for the whole city, each standardised within its own call, D = z(area) − z(city). Scores from different rank calls are never compared raw.
- **Supply ratio θ**: OpenStreetMap venues of that cuisine within 600 m against the count expected from the local food mix at the city's concept share (a location quotient, Isserman 1977), shrunk with a Gamma prior fitted over the city's 1.2 km blocks (Clayton & Kaldor 1987) so one or zero venues cannot produce a wild ratio; P(under-supplied) = P(θ < 1) from 4,000 seeded draws.
- **Index** σ(D − log θ) with stated, unfitted weights, and a quadrant label (white space / proven demand, crowded / empty for a reason / avoid / neutral).

The backtest (`--food`, results in `backtest_chicago_tgi.json`) uses an OSM snapshot dated 2023-01-01, before the window, so supply has no look-ahead; D still comes from today's Qloo. The four orderings were fixed before the run. Same 9 areas and 31 surviving openings:

| Top-3 hit rate (rank correlation) | |
|---|---|
| Qloo rank alone (as above) | 29% (0.02) |
| Over-index D alone | 35% (−0.04) |
| OSM under-supply alone | 32% (−0.10) |
| Taste Gap Index | 48% (−0.16) |
| Citywide baseline + Taste Gap Index | 55% (0.23) |
| Citywide baseline alone | 68% (0.31) |

Result: **no measurable gain**. The index beats the plain Qloo rank by 19 points, but the 90% interval of that difference (−13 to +42 points) includes zero, and every TGI variant is below the citywide baseline (index minus baseline: −36 to −6 points; adding the index to the baseline makes it worse, −22 to −6). On 9 areas, what is opening everywhere in Chicago predicts better than local taste or local gaps. The app still shows the index, labelled uncalibrated, because it is the clearest way to explain a recommendation; OSM supply also replaces four Qloo calls per run.

What this means for the product: Qloo is useful here as evidence a broker can show (what the area loves, who already serves it, where the audience is), not as a validated predictor of tenant survival. Every tenant memo's Limits section reports this backtest result (`tenant.BACKTEST_NOTE`, added by code if the LLM leaves it out).

**Survivorship and intervals** (`backtest_bias.py`, numbers in `backtest_bias.json`). A Heckman selection correction (Heckman 1979) is not estimated. It needs an exclusion restriction, and this file has none: a licence is missing from the survivor count because it closed, because it was not inspected again, or because its name matched no catalog keyword. Those are not separable from survival, and applications that never opened are not in the register.

Inverse-probability weights (Horvitz and Thompson 1952) are used instead. The propensity in a concept-by-area cell is survivors / openings. A cell with openings and no survivor is dropped, not filled in. The weights cover keyword-matched licences only. On a register pull with no Qloo call (10 areas that have a survivor, including River North, which the 9-area Qloo table left out), the citywide baseline's top-3 hit rate is 0.686 on 35 survivors and 0.766 after weighting (64 weighted openings; 10 openings dropped because the propensity was 0). Weighting does not reverse the published result. The Qloo column was not recomputed: rank orders are not stored, and this pull made no Qloo call.

A split-conformal interval (Vovk, Gammerman and Shafer 2005) around the leave-one-area citywide survivor share, 90%, has quantile 1 and mean width 2 on a rate that lives in [0, 1]. Coverage on the test half is 1 because the interval is the whole range. It does not tighten the claim, and it is not an interval for a Qloo fit score. Those scores are not stored.

### Decision layer (`decision.py`)

Three additions turn the evidence into a decision. None of them uses an extra Qloo call.

- **Rent-first call.** This applies wherever a concept has a Taste Gap label. The rent is checked before taste: no taste fit rescues a rent the unit cannot carry.
  - NO-GO if the asking rent is above the ceiling's 90% range, or the label is "avoid".
  - GO if the rent fits and taste is strong. Strong means "white space", or a positive over-index in the run's top third by Taste Gap Index.
  - ADJUST otherwise, with the adjustment spelled out: negotiate to the central ceiling, or reposition toward the run's white-space concept.
  - Concepts without a label (no venue file for the city) keep the score thresholds. The score still orders concepts within each call.
- **Score breakdown.** An exact Shapley split (Shapley 1953) over all 16 coalitions of four signals, against a city-typical baseline: taste over-index (Qloo), supply ratio (OpenStreetMap), audience fit (Qloo, relative to the run's median), and rent headroom (City Scan).
  - The card shows each signal's contribution and the Qloo share.
  - The weights are stated priors, not fitted, so the split explains TasteFit's model, not cause and effect.
  - Momentum is left out because `trends` covers one reference brand, not each concept.
- **Check first, before signing.** This is the expected value of partial perfect information (Howard 1966; binned estimator, 20 bins, 2,000 seeded draws). It is computed on monthly profit at the asking rent, or at the ceiling when none is given, using the City Scan break-even model's stated cost ranges.
  - It ranks which input (covers per day, ticket, staff, …) is worth checking before signing, in currency per month of decision value.
  - A check is shown only when its value exceeds twice a noise floor, measured with a random input.
  - The figures are monthly, with no discounting and no interest.
  - They come from TasteFit's stated assumptions, not the user's own numbers. For example, a Chicago restaurant at 6,000/month: covers per day dominates.
- **Street match.** When the form lists at least two monthly rents, the same run's fit scores are the preferences in a unit-proposing Gale–Shapley match (Gale and Shapley 1962). The tenant side ranks by fit, then by a lower asking rent. The landlord side ranks by the rent-first verdict, then by fit. At most k concepts of one rent category are matched on the street (k is 1 unless the request sets it, from 1 to 5). N is capped at 5 units. The match reuses this run and makes no extra Qloo call. The page states when there is no blocking pair. Stability is not a survival forecast: the 2023-2024 Chicago backtest found no measurable gain.
- **Prediction fingerprint.** Every memo ends with the SHA-256 of the concept, the area, the shortlist verdicts and the UTC time. The hash does not cover Qloo payloads, and `data/ledger.json` does not store fit scores. Committed batches are on `/ledger`. The pre-registered score is the Brier score (Brier 1950): the mean of (p - o) squared. p is a fixed map of the verdict label already inside the fingerprint (GO 0.66, ADJUST 0.50, NO-GO 0.33), not a Qloo fit score and not a calibrated probability of opening. A batch with any other verdict has no p. o is 1 only when a new Chicago food licence matching the frozen keyword appears before the check date. Skill is the Brier score of one constant citywide rate, computed on the check date from the same public file, minus TasteFit's Brier score. The 2023-2024 backtest found no measurable gain, so a negative skill score stays a possible result. The Chicago batch registered on 2026-10-14 has verdict `unscored`: the rank call returned a rate limit, so no GO / ADJUST / NO-GO label was produced, and the Brier score is not defined for that batch. The first listed concept is a catalog entry, not a ranking. The registered point is a map point, not a vetted address.

### Limits

- Qloo describes aggregate taste affinities, not foot traffic, sales or rents. The rent ceiling comes from City Scan (official survival data for Paris and Chicago only).
- Scores are relative within one run's `rank` call; two runs can order close concepts differently.
- `filter_location` is fuzzy for small neighborhoods (a Wicker Park filter can return Lincoln Park places).
- Trend data in the hackathon environment showed no change over the periods tested, and the page says so.
- Alcohol-led concepts and benchmarks are excluded by design.

## Run locally

Requires Python 3.10+ and Node.js 22.19+ (for the harness).

```bash
git clone https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43239248/tastefit.git && cd tastefit
npm ci                             # installs @qloo/qloo-harness locally (no Python packages needed)
export QLOO_API_KEY=...            # Qloo hackathon key, never commit it
export QLOO_BASE_URL=https://hackathon.api.qloo.com
export GEMINI_API_KEY=...          # optional: free Google AI Studio key. Tries gemini-3.8-flash (8s), then gemini-3.1-flash-lite
export LLM_BASE_URL=...            # optional fallback: any OpenAI-compatible endpoint (default: a free keyless one)
python app.py                      # http://localhost:8000 (broker mode: /?mode=broker)
python backtest_chicago.py --no-llm  # optional: the Qloo backtest above (public Chicago data, about 35 Qloo calls)
python backtest_chicago.py --public-only  # register only: IPW and conformal, no Qloo call
python tgi.py build chicago 2023-01-01 /tmp/chicago_2023.json.gz && \
  python backtest_chicago.py --no-llm --food /tmp/chicago_2023.json.gz  # optional: with the Taste Gap Index
```

The API keys stay on the server; the browser only receives the streamed steps and the memo. LLM calls go to Gemini first when `GEMINI_API_KEY` is set, then to the fallback endpoint. The tenant match never depends on an LLM to finish: without one it uses a fixed candidate list and a template memo.

CLI: `python agent.py "Korean fried chicken counter" "Austin, TX"`

**MCP server** (`mcp_server.py`, standard library, stdio JSON-RPC): exposes `tenant_match`, `city_scan` and `city_scan_unit` to any MCP client. The key comes from the client's environment and is redacted from every tool result.

```json
{"mcpServers": {"tastefit": {"command": "python3", "args": ["/path/to/tastefit/mcp_server.py"],
  "env": {"QLOO_API_KEY": "...", "QLOO_BASE_URL": "https://hackathon.api.qloo.com"}}}}
```

**Tenant match** (`GET /tenant?lat=41.9088&lon=-87.6796&rent=4000`, or `?city=Paris&location=Le%20Marais`): for a vacant unit, which food concepts the local audience wants that nobody nearby serves, and whether each can pay the rent. A fixed pipeline of at most 25 Qloo calls (8 workflows; a cache that lives for one request only, so repeated calls within a run are free and nothing Qloo returns outlives the request) streams each step, then a scored shortlist, a "without Qloo" comparison and a memo (`tenant.py`).

**Claim ledger** (the "without Qloo" panel): the same LLM, with no tools, suggests concepts and names up to 5 competitors within 500 m. Each concept is marked confirmed (Taste Gap "white space", or "neutral" with a positive over-index), contradicted ("proven demand, crowded", "empty for a reason", "avoid"), partly supported, or not tested by this run. Each named venue is looked up in OpenStreetMap within 500 m with City Scan's name matcher (similarity >= 0.6 found, 0.45-0.6 ambiguous); Qloo's own area favourites that read as food venues go through the same check (1.5 km, since they are area-level; the OSM file holds food venues only). The page shows counts, and calls them agreement with the run's evidence and existence in open data, not accuracy: OSM misses real venues too. Supply estimates whose city-wide prior carries >= 80% of the weight are badged "Data coverage: low": the local OSM count barely moves them. This happens when the expected count near the unit is small or the city's block counts vary little; it is not a measure of how complete OSM mapping is.

Front end: `index.html` plus `static/` (`app.js`, `styles.css`, icons, and the Schibsted Grotesk font under the SIL Open Font License, served from the app itself), no build step. The map starts from a few example points; they are places on a map, not vacancy listings. "Read a sample memo" replays `/demo` (synthetic data, labelled as such).

Tests: `python -m unittest discover -s tests -t . -v` (276 tests).

## Deploy

Deployed on Render (free Docker web service) from the `Dockerfile`; set the environment variables above in the Render dashboard. On GitLab, CI triggers the deploy with the exact commit SHA (see below).

**Google Cloud:** also live on Cloud Run at https://tastefit-776637567140.us-central1.run.app (us-central1, 0 to 1 instances, free tier). The `deploy-gcp` job copies the signed, scanned CI image by digest to Artifact Registry and deploys that digest, authenticated by Workload Identity Federation (no stored key); `smoke-gcp` checks that `/health` returns the commit SHA.

## Agentic DevSecOps on GitLab

TasteFit started on GitHub (https://github.com/youssefbenzidiya-cmd/tastefit) with no CI, no tests and no post-deploy check. On GitLab, every commit goes through a 7-stage pipeline, and four custom GitLab Duo Agent Platform flows plan, fix, review and release the project when events happen, without being asked. A person merges every change.

**Pipeline** ([`.gitlab-ci.yml`](.gitlab-ci.yml)): `test` → `build` → `security` → `package` → `deploy` → `verify` → `release`. On the latest `main` pipeline ([2935652252](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43239248/tastefit/-/pipelines/2935652252)) all 15 jobs passed: `unit-tests`, `lint`, `flows-lint`, `image-build`, `semgrep-sast`, `secret_detection`, `dependency-scanning`, `container_scanning`, `cve-gate`, `sbom`, `sign-image`, `verify-signature`, `deploy-render`, `smoke-test`, `release`. A daily schedule (06:00 UTC) rebuilds and rescans the image and checks that production is up.

### GitLab stages covered

| Stage | What automates it | Evidence |
|---|---|---|
| **Plan** | Issue Planner turns a new issue into a scoped plan with labels and open questions. Issues assigned to GitLab's Duo Developer flow become MRs. | [#15](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43239248/tastefit/-/work_items/15) (planner), [#5](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43239248/tastefit/-/work_items/5) (Duo Developer) |
| **Create** | Duo Developer opens feature MRs; Pipeline Medic opens fix MRs | [!4](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43239248/tastefit/-/merge_requests/4), [!1](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43239248/tastefit/-/merge_requests/1), [!9](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43239248/tastefit/-/merge_requests/9), [!11](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43239248/tastefit/-/merge_requests/11) |
| **Verify** | Unit tests with a JUnit report and coverage (74%), `ruff` lint, `flows-lint` of the Duo flow files; Medic checks its fixes with the CI linter | [pipeline 2935652252](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43239248/tastefit/-/pipelines/2935652252) (159 of 159 passed) |
| **Package** | Docker image built, started and health-checked, then pushed to the GitLab container registry; CycloneDX SBOM; cosign keyless signature, verified in CI | [container registry](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43239248/tastefit/container_registry), jobs [`sbom`](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43239248/tastefit/-/jobs/17092440239), [`sign-image`](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43239248/tastefit/-/jobs/17092440240), [`verify-signature`](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43239248/tastefit/-/jobs/17092440241) |
| **Secure** | SAST, secret detection, container scanning, dependency scanning, and `cve-gate` (fails on Critical or High container findings that are not in `security/cve-allowlist.txt`); Release Scribe triages findings | [#1](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43239248/tastefit/-/work_items/1), [#2](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43239248/tastefit/-/work_items/2), [!3](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43239248/tastefit/-/merge_requests/3), [#4](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43239248/tastefit/-/work_items/4), [`cve-gate` job](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43239248/tastefit/-/jobs/17092440238) |
| **Release** | Render deploy pinned to the commit SHA as the GitLab `production` environment; GitLab Releases with the image digest and SBOM; Release Scribe notes for owners | [environment](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43239248/tastefit/-/environments/41337937), [releases](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43239248/tastefit/-/releases), [#19](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43239248/tastefit/-/work_items/19) |
| **Configure** | Environment-based config (no secrets in code), CI/CD variables for deploy credentials, Dockerfile `HEALTHCHECK` checked by `image-build` | [`.gitlab-ci.yml`](.gitlab-ci.yml), [`Dockerfile`](Dockerfile) |
| **Monitor** | Smoke test waits until `/health` returns the deployed SHA; daily uptime check with response-time metrics | [pipeline 2934713922](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43239248/tastefit/-/pipelines/2934713922), [scheduled pipeline 2935294308](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43239248/tastefit/-/pipelines/2935294308) |
| **Govern** | 9 rules in [`AGENTS.md`](AGENTS.md) enforced by Domain Reviewer; `CODEOWNERS`; `policy-guard` job fails an agent branch (`medic/`, `duo/`) that edits agent policy, CI scripts or the CVE allowlist | [!5](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43239248/tastefit/-/merge_requests/5) (NO-GO under rule 8); `policy-guard` passed on the MR pipelines of [!8](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43239248/tastefit/-/merge_requests/8), [!12](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43239248/tastefit/-/merge_requests/12) and [!14](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43239248/tastefit/-/merge_requests/14) |

### Flows

Definitions are in [`.gitlab/duo/flows/`](.gitlab/duo/flows); rules are in [`AGENTS.md`](AGENTS.md) and `skills/tastefit-guardrails/`. Each flow runs as its own service account with an explicit toolset. No flow has a merge tool.

| Flow | Runs when | What it does |
|---|---|---|
| Issue Planner | an issue is created | Rewrites the issue as a scoped plan with acceptance criteria, sets labels, and lists the questions a person must answer before work starts. |
| Duo Developer (GitLab foundational flow) | an issue is assigned to it | Implements the issue and opens a merge request. |
| Pipeline Medic | any pipeline fails | Reads the failing job logs, classifies the failure, and opens a minimal fix MR on `medic/<pipeline id>`, checked with the CI linter. It looks for an existing MR first and adds a note instead of a duplicate. On `medic/` and `duo/` refs it never opens a fix, so it cannot loop. The definition splits the work between a `log_analyst` and a `fixer` subagent. |
| Domain Reviewer | a merge request is created, or the bot is assigned as reviewer | Checks the 9 product rules a generic reviewer cannot know (secrets, Qloo data terms, privacy, Nominatim policy, site-maths tests, memo honesty, stdlib-only server, protected security jobs, agent governance), posts inline suggestions, and answers GO / ADJUST / NO-GO, like TasteFit itself. |
| Release Scribe | a pipeline on `main` passes | Writes release notes for restaurant owners with deployment proof (deployed SHA, smoke-test job) and a security summary across all scanners. Opens a grouped issue for High or Critical findings, and posts a note on issues the release shipped. |

### Live proof

Every row links to a public page in this project.

| Claim | Evidence |
|---|---|
| The smoke test went red after a good deploy at 22:16:08 UTC. It was a false negative: `curl` exit 23, caused by `grep -q` closing the pipe under `pipefail`. | [pipeline 2934704707](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43239248/tastefit/-/pipelines/2934704707), [failed job 17087586018](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43239248/tastefit/-/jobs/17087586018) |
| Pipeline Medic opened a one-line fix MR at 22:17:47, without being asked, and validated it with the CI linter | [!1](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43239248/tastefit/-/merge_requests/1), commit [`4d905e3e`](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43239248/tastefit/-/commit/4d905e3efa648353e79fd1fdbfd727aac47cac05) |
| A second Medic run found that MR and added a note instead of a duplicate | [!1](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43239248/tastefit/-/merge_requests/1) (note at 22:18:15) |
| Domain Reviewer posted GO with its rule table at 22:19:51; a person merged at 22:20:38; the next pipeline deployed and passed | [!1](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43239248/tastefit/-/merge_requests/1), [pipeline 2934713922](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43239248/tastefit/-/pipelines/2934713922) |
| Medic fixed a failed `release` job (shell-evaluated backticks in the release description) | [pipeline 2934914083](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43239248/tastefit/-/pipelines/2934914083), [!9](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43239248/tastefit/-/merge_requests/9) |
| Medic fixed a `lint` failure on `main`; the next pipeline passed all 15 jobs and published release v1.86 | [pipeline 2935452861](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43239248/tastefit/-/pipelines/2935452861), [!11](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43239248/tastefit/-/merge_requests/11), [pipeline 2935462422](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43239248/tastefit/-/pipelines/2935462422), [v1.86](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43239248/tastefit/-/releases/v1.86) |
| Medic also fixed a `lint` failure on a research branch | [!10](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43239248/tastefit/-/merge_requests/10) |
| Domain Reviewer answered NO-GO to our own change that skipped security jobs on docs-only commits (rule 8), then GO after rework | [!5](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43239248/tastefit/-/merge_requests/5) |
| Domain Reviewer answered NO-GO on a real bug: the Chicago rent ceiling was fitted under the Paris rent share (5-10-15%) instead of Chicago's (4-6-10%). After the fix the restaurant ceiling went from $5,970 to $3,230. Two ADJUST rounds followed (a test that only checked metadata, then docs), then GO. | [!14](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43239248/tastefit/-/merge_requests/14) |
| Plan to production: Issue Planner planned [#15](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43239248/tastefit/-/work_items/15) within 90 seconds of its creation (acceptance criteria, test plan, AGENTS.md check) and asked for decisions; a person answered and implemented it in [!12](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43239248/tastefit/-/merge_requests/12); Domain Reviewer answered ADJUST (5 findings), then GO after the fix; merged, deployed, released as v1.96, and Release Scribe noted the shipment on #15 | [#15](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43239248/tastefit/-/work_items/15), [!12](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43239248/tastefit/-/merge_requests/12), [pipeline 2935534874](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43239248/tastefit/-/pipelines/2935534874), [v1.96](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43239248/tastefit/-/releases/v1.96) |
| Issue assigned to Duo Developer became an MR with three new tests, got GO, and the live footer showed the new build | [#5](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43239248/tastefit/-/work_items/5), [!4](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43239248/tastefit/-/merge_requests/4), release notes [#8](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43239248/tastefit/-/work_items/8) |
| Release Scribe's first notes reported 338 container findings (5 Critical, 67 High) and it filed a grouped security issue | [#1](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43239248/tastefit/-/work_items/1), [#2](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43239248/tastefit/-/work_items/2) |
| Alpine rebuild: 0 container findings on the image GitLab builds from the deployed commit, still 0 today | [!3](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43239248/tastefit/-/merge_requests/3), [#4](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43239248/tastefit/-/work_items/4), [#19](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43239248/tastefit/-/work_items/19) |
| Image 141 MB → 100 MB compressed (−29%) | [container registry](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43239248/tastefit/container_registry) (tags `c1605d83` vs `ac836f36`) |
| Six GitLab Releases so far (v1.58 to v1.120), each with the deployed commit, the signed image digest and an SBOM link | [releases](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43239248/tastefit/-/releases), e.g. [v1.120](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43239248/tastefit/-/releases/v1.120) |
| Daily scheduled rescan and uptime check | [schedules](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43239248/tastefit/-/pipeline_schedules), [scheduled pipeline 2935294308](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43239248/tastefit/-/pipelines/2935294308) |
| Production environment with per-commit deployments | [environment `production`](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43239248/tastefit/-/environments/41337937) |

**The loop, measured.** Time from the failed job to the Medic fix MR, on every real failure so far:

| Failure | Job failed (UTC) | Fix MR opened | Delay | Review | Merged by a person |
|---|---|---|---|---|---|
| smoke-test on `main` | 22:16:08 | [!1](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43239248/tastefit/-/merge_requests/1) 22:17:47 | 1 min 39 s | GO 22:19:51 | 22:20:38 |
| release on `main` | 01:08:07 | [!9](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43239248/tastefit/-/merge_requests/9) 01:10:03 | 1 min 56 s | GO 01:12:46 | 01:12:51 |
| lint on a research branch | 08:20:41 | [!10](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43239248/tastefit/-/merge_requests/10) 08:22:29 | 1 min 48 s | GO 08:35:15 | 08:37:10 |
| lint on `main` | 08:29:29 | [!11](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43239248/tastefit/-/merge_requests/11) 08:30:39 | 1 min 10 s | GO 08:35:13 | 08:37:06 |

Review times depend on when a person assigned the reviewer (see Limits).

### Seeded benchmark

On 11 Oct 2026 we opened eight merge requests through the API. Each had one planted problem, and none had a hint in its title or description. We recorded what the agents did without stepping in. All eight are now closed, unmerged.

| Planted problem | MR | Expected | Domain Reviewer (time to verdict) | Pipeline / Medic |
|---|---|---|---|---|
| Harmless copy edit | [!16](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43239248/tastefit/-/merge_requests/16) | GO | GO (47 s) | green |
| Slug limit that breaks unit tests | [!17](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43239248/tastefit/-/merge_requests/17) | red pipeline, fix MR | ADJUST (54 s) | unit-tests red; Medic fix [!25](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43239248/tastefit/-/merge_requests/25) restores the limit |
| Hardcoded (fake) API key | [!18](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43239248/tastefit/-/merge_requests/18) | NO-GO (rule 1) | NO-GO (51 s) | unit-tests red; Medic fix [!24](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43239248/tastefit/-/merge_requests/24) removes the key and restores the guard |
| Qloo responses written to disk | [!19](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43239248/tastefit/-/merge_requests/19) | NO-GO (rule 2) | NO-GO (54 s) | green |
| Secret detection disabled | [!20](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43239248/tastefit/-/merge_requests/20) | NO-GO (rule 8) | NO-GO (55 s) | green |
| `geo.py` default changed, no test | [!21](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43239248/tastefit/-/merge_requests/21) | ADJUST (rule 5) | NO-GO (53 s) | unit-tests red; Medic fix [!26](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43239248/tastefit/-/merge_requests/26) restores the default |
| Memo told to give an exact street address | [!22](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43239248/tastefit/-/merge_requests/22) | NO-GO (rule 6) | ADJUST (51 s), then NO-GO under Reviewer 1.2.0 | green |
| `duo/` branch edits `AGENTS.md` | [!23](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43239248/tastefit/-/merge_requests/23) | policy-guard fails, NO-GO (rule 9) | NO-GO (57 s) | policy-guard red; no Medic fix (agents may not touch policy) |

What it shows:
- All seven planted problems were flagged (six NO-GO, one ADJUST), and the safe change got a GO. Every verdict arrived within a minute.
- All three Medic fixes were correct and minimal. Each restored the guard or default the change broke. None edited a test to make it pass.
- **The benchmark found a policy gap, and a human closed it.** In !22 the Reviewer found the rule 6 problem but answered ADJUST, exactly as its rubric said: only rules 1, 2, 3, 8 and 9 were blocking. We made rule 6 blocking in [!27](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43239248/tastefit/-/merge_requests/27), a human branch that the Reviewer itself reviewed, GO. We published the flow as 1.2.0 and re-ran it on !22. The verdict became NO-GO, and the Reviewer noted that its earlier ADJUST was too lenient.
- Limits:
  - Secret detection did not flag the fake key; it has no rule for its format. The Reviewer and the unit tests caught it.
  - On !21 the Reviewer was stricter than expected, because the change also broke the demo-report tests.

### Before and after

The baseline is GitHub commit [`8c465c1`](https://github.com/youssefbenzidiya-cmd/tastefit/commit/8c465c1) (2026-10-10, 19:08 UTC), the last commit before any GitLab work. The next commit, [`fda392a`](https://github.com/youssefbenzidiya-cmd/tastefit/commit/fda392a) (19:22 UTC), added the first tests, `/health` and a first `.gitlab-ci.yml`.

| | Before (GitHub `8c465c1`) | After (GitLab `main`) |
|---|---|---|
| Tests | none | 164 unit tests with JUnit and coverage, plus lint, on every push |
| CI/CD | none | 7-stage pipeline: test, build, security, package, deploy, verify, release |
| Image | `node:22-slim` (Debian 12), built by the host, never scanned | `node:22-alpine`, built in CI, started and health-checked, signed, with an SBOM |
| Container findings | not measured (the same Dockerfile later scanned at 338: 5 Critical, 67 High) | 0 on the image GitLab builds from the deployed commit |
| Image size | 141 MB compressed (same Dockerfile, measured in the GitLab registry) | 100 MB (−29%) |
| Deploy | Render web service, no post-deploy check | exact commit, verified live by `/health` returning that SHA |
| Releases | none | GitLab Releases with image digest and SBOM, plus owner-facing notes from Release Scribe |
| Broken pipeline | noticed by chance | fix MR opened by Pipeline Medic in under 2 minutes, 4 times so far |
| Review | none | Domain Reviewer on every MR, 9 product rules |
| Agents | none | Issue Planner, Pipeline Medic, Domain Reviewer, Release Scribe, plus GitLab's Duo Developer |

### Limits

- **A person merges.** No flow has a merge tool, and every merged merge request so far was merged by a person after a Domain Reviewer verdict. A fix waits for a human.
- **Bot-created MRs** do not fire the MR-created trigger, so a person assigns Domain Reviewer on them (as on !1, !9, !10 and !11).
- **Scanned image vs deployed image on Render.** Google Cloud Run runs the exact image GitLab built, scanned and signed (same digest). Render, the second host, builds its own image from the same Dockerfile at the same commit, so its bytes are not the ones we scanned.
- **`cve-gate` covers container findings only.** Dependency scanning reports 4 High findings (`undici`, `brace-expansion`), tracked in [#12](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43239248/tastefit/-/work_items/12), and they do not fail the pipeline.
- **Stale Vulnerability Report records.** The report still lists 72 Critical/High container records for the superseded Debian image `c1605d83` ([#9](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43239248/tastefit/-/work_items/9)). Current scans of the Alpine image show 0.
- **Three Medium SAST findings** (SSRF, CWE-918, at `cityscan.py:1418`, `geo.py:87` and `agent.py:110`) appear in every release note and are not yet triaged.
- **Tool gaps.** `create_vulnerability_issue` rejected our calls, so Release Scribe groups findings in one issue.

## License

MIT
