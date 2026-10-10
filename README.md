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

Before a founder signs a lease, or a lender funds the opening, TasteFit checks whether the planned concept fits the local taste of a specific neighborhood. It uses the Qloo Taste AI API to read what the area's audience likes, who already serves it, and where demand concentrates, then writes an investment-style **GO / ADJUST / NO-GO** memo with a fit score, the best location, a positioning plan and the key risks.

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

The map uses Leaflet with OpenStreetMap tiles, and the suggested spot links to Google Maps and Street View (plain links, no API key). It points to a block to scout, not a vetted address.

The memo only cites places, tags and numbers returned by Qloo, keeps Qloo results separate from interpretation, and ends with a **Limits** section: Qloo describes aggregate group affinities, not foot traffic, rents or individual behaviour. No personal data is sent to Qloo; inputs are a concept and a place name.

## Run locally

Requires Python 3.10+ and Node.js 22.19+ (for the harness).

```bash
npm install                        # installs @qloo/qloo-harness locally
export QLOO_API_KEY=...            # Qloo hackathon key, never commit it
export QLOO_BASE_URL=https://hackathon.api.qloo.com
export LLM_BASE_URL=...            # any OpenAI-compatible endpoint with tool calling
export LLM_API_KEY=...
export LLM_MODEL=...
python app.py                      # http://localhost:8000
```

The API key stays on the server; the browser only receives the streamed steps and the memo.

CLI: `python agent.py "Korean fried chicken counter" "Austin, TX"`

## Deploy

Deployed on Render (free Docker web service) from the `Dockerfile`; set the environment variables above in the Render dashboard.

## Agentic DevSecOps on GitLab

TasteFit started on GitHub (https://github.com/youssefbenzidiya-cmd/tastefit) with no CI, no tests and manual deploys. On GitLab, every commit goes through a pipeline and three GitLab Duo Agent Platform flows look after the project without being asked.

**Pipeline** (`.gitlab-ci.yml`): `test` (unit tests, lint) → `build` (Docker image pushed to the GitLab container registry) → `security` (SAST, secret detection, container scanning of that image) → `deploy` (Render deploy hook pinned to the exact commit) → `verify` (smoke test polls `/health` until it returns `ok <commit sha>`, then checks the page and the input validation). A daily scheduled pipeline rebuilds and rescans the image for newly published CVEs and runs an uptime check on production, so an outage or a new vulnerability also wakes Pipeline Medic.

**Flows** (definitions in `.gitlab/duo/flows/`, rules in `AGENTS.md` and `skills/tastefit-guardrails/`):

| Flow | Runs when | What it does |
|---|---|---|
| Pipeline Medic | any pipeline fails | Reads the failing job logs, classifies the failure (code, config, infra), and opens a minimal fix merge request validated with the CI linter, or a diagnosis issue. It never touches its own `medic/` branches twice, so it cannot loop. |
| Domain Reviewer | a merge request is created, or the bot is assigned as reviewer | Checks the rules a generic reviewer cannot know: secrets, Qloo data terms, privacy, Nominatim policy, site-maths tests, memo honesty, protected security jobs. Answers GO / ADJUST / NO-GO, like TasteFit itself. |
| Release Scribe | a pipeline on `main` passes | Writes release notes for restaurant owners, with deployment proof (the smoke-tested commit SHA) and a security summary across all three scanners; opens issues for High or Critical findings. |

| | Before (GitHub) | After (GitLab) |
|---|---|---|
| Tests | none | 9 unit tests on every push |
| Security | none | SAST, secret detection, container scanning |
| Container CVEs | unknown | 338 (5 Critical, 67 High) found by the first scan, flagged by Release Scribe, fixed by an Alpine rebuild: 0 in production ([#2](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43239248/tastefit/-/work_items/2), [!3](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43239248/tastefit/-/merge_requests/3), [#4](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43239248/tastefit/-/work_items/4)) |
| Deploy | manual | exact commit, verified live by SHA |
| Broken pipeline | noticed by chance | diagnosed, fix MR opened and reviewed in under 4 minutes ([pipeline](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43239248/tastefit/-/pipelines/2934704707) smoke test failed 22:16:08 UTC, [fix MR](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43239248/tastefit/-/merge_requests/1) opened 22:17:47, review posted 22:19:51) |
| Review | none | product-rule review on every merge request |
| Release notes | none | written for owners on every production deploy |

Features start as issues assigned to GitLab's foundational Duo Developer flow. For example, [#5](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43239248/tastefit/-/work_items/5) became [!4](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43239248/tastefit/-/merge_requests/4), and the build id is now in the live page footer.

The human stays the supervisor: agents open merge requests and issues, a person merges.

## License

MIT
