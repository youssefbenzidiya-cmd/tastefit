# TasteFit agent guide

TasteFit tells restaurant founders and commercial brokers whether a concept fits a neighborhood. Founders act on its
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
9. Agent-authored branches (`medic/`, `duo/`) may not modify `AGENTS.md`, `.gitlab/duo/**`, `.agents/**`, `skills/**`,
   `ci/**`, `security/**` or `CODEOWNERS`. Only humans change the rules agents follow and the scripts and
   allowlist that enforce the security gates. The CI `policy-guard` job enforces this.

## Duo flows in this project

Definitions live in `.gitlab/duo/flows/`. Together they cover Plan to Production, and every step leaves
a trace (issue, note, merge request or review) in the project:

- **Issue Planner** (`issue-planner.yml`, Plan): runs when an issue is created or the planner is
  mentioned. Rewrites the issue into a spec with acceptance criteria and a test plan, checks it against
  the rules above, adds `type::`, `priority::` and `size::` labels, and marks small, safe issues
  "Ready for implementation" for the Developer flow.
- **Developer flow** (GitLab foundational flow, Create): implements on a `duo/` branch. It can delegate
  testing to the workspace subagent `.agents/agents/tastefit-tester.md`.
- **Domain Reviewer** (`domain-reviewer.yml`, Review): runs when a merge request is created or the bot is
  assigned as reviewer. A deterministic step prefetches the diff, then the reviewer posts inline comments
  with suggestions and a summary "Verdict for <sha>: GO / ADJUST / NO-GO" against rules 1 to 9.
- **Pipeline Medic** (`pipeline-medic.yml`, Verify): runs on any failed pipeline. A lead agent delegates
  to `log_analyst` (classifies CODE, CONFIG, INFRA or SECURITY with evidence) and `fixer` (minimal fix on
  `medic/<pipeline id>` with a merge request). INFRA failures, such as production down, open an
  `incident` issue; unfixable High/Critical CVEs from `cve-gate` open a `security` issue. It never merges.
- **Release Scribe** (`release-scribe.yml`, Release): runs on every passed pipeline on `main`, files
  release notes with deployment proof and a security summary, then notes "Shipped to production in
  <sha>" on every issue the shipped commits or merge requests reference.
