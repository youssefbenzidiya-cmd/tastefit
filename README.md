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

## License

MIT
