# TasteFit agent guide

TasteFit tells restaurant founders and lenders whether a concept fits a neighborhood. Founders act on its
GO / ADJUST / NO-GO verdict with real money, so every change must keep the memo correct and honest.

## Layout

- `app.py`: standard-library HTTP server. `/` serves `index.html`, `/run` streams the agent with
  Server-Sent Events, `/health` returns `ok <commit sha>` for the CI smoke test.
- `agent.py`: LLM tool loop over the Qloo harness.
- `qloo.py`: wrapper around `qloo exec` (Qloo Taste AI hackathon harness).
- `geo.py`: barycenter and gravity-model site maths, Nominatim geocoding.
- `index.html`: single-page front end with Leaflet and OpenStreetMap tiles.
- `tests/`: `python -m unittest discover -s tests -t .`

## Rules

1. Secrets (`QLOO_API_KEY`, `LLM_API_KEY`, deploy keys) come from environment variables only.
2. Qloo responses are used within a request and never persisted to disk, a database or the repository.
   Test fixtures are synthetic.
3. Only a concept and a place name are sent to Qloo. No personal data in Qloo or LLM calls.
4. Nominatim: at most 1 request per second with a descriptive User-Agent. Keep the OpenStreetMap
   attribution visible on the map.
5. A change to `geo.py` comes with a test in `tests/test_geo.py`, and the README formulas stay accurate.
6. The memo cites only Qloo-returned facts, keeps its Limits section, and never presents the suggested
   spot as a vetted address.
7. The server stays standard-library only. `/run` rejects missing inputs with 400.
8. Never remove or skip unit tests, SAST, secret detection, container scanning or the smoke test.

## Duo flows in this project

Definitions live in `.gitlab/duo/flows/`:

- **Pipeline Medic** (`pipeline-medic.yml`): runs on any failed pipeline, opens a fix merge request or a
  diagnosis issue.
- **Domain Reviewer** (`domain-reviewer.yml`): runs on every new merge request and checks the rules above.
- **Release Scribe** (`release-scribe.yml`): runs on every passed pipeline on `main`, files release notes
  with deployment proof and a security summary.
