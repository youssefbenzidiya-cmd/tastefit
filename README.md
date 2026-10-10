# TasteFit

A taste-market-fit agent for new restaurants and cafes.

Before a founder signs a lease, or a lender funds the opening, TasteFit checks whether the planned concept fits the local taste of a specific neighborhood. It uses the Qloo Taste AI API to read what the area's audience likes, who already serves it, and where demand concentrates, then writes an investment-style **GO / ADJUST / NO-GO** memo with a fit score, the best location, a positioning plan and the key risks.

## How it works

An LLM agent runs a tool loop over Qloo:

| Tool | Qloo call | Purpose |
|---|---|---|
| `find_tags` | `/v2/tags` | Turn the concept ("Korean fried chicken counter") into Qloo tags |
| `area_taste` | `/v2/insights`, `filter.type=urn:tag` | What the neighborhood's audience over-indexes on |
| `competitors` | `/v2/insights`, `filter.type=urn:entity:place` | Existing places with that taste, with affinity, price and rating |
| `hotspots` | `/v2/insights`, `filter.type=urn:heatmap` | Sub-areas where affinity for the concept is highest |

The agent only states numbers that come from Qloo; if data is missing it says so instead of guessing.

## Run locally

Requires Python 3.10+ and no third-party packages.

```bash
export QLOO_API_KEY=...            # Qloo hackathon key
export LLM_BASE_URL=...            # any OpenAI-compatible endpoint
export LLM_API_KEY=...
export LLM_MODEL=...
python app.py                      # http://localhost:8000
```

CLI: `python agent.py "Korean fried chicken counter" "Austin, TX"`

## Deploy

`render.yaml` deploys it as a free Render web service; set the environment variables above in the Render dashboard.

## License

MIT
