---
name: tastefit-guardrails
description: Product, data-licensing and privacy rules for changing TasteFit (Qloo taste data, Nominatim geocoding, site maths, verdict memo). Use before editing app.py, agent.py, qloo.py, geo.py, index.html or the CI pipeline.
---

# TasteFit guardrails

Before you change code, read `AGENTS.md` at the repository root and apply its rules.

## Checklist

- [ ] No secret in code, tests, logs, HTML or error messages. Keys come from environment variables.
- [ ] Qloo data is not written anywhere that outlives the request.
- [ ] Only the concept and place name reach Qloo; nothing personal reaches Qloo or the LLM.
- [ ] Geocoding stays at 1 request per second with a User-Agent; map keeps OpenStreetMap attribution.
- [ ] `geo.py` changes have a matching test in `tests/test_geo.py`.
- [ ] The memo prompt still cites only Qloo facts and keeps the Limits section.
- [ ] `/health` still returns `ok <commit sha>`; `/run` still returns 400 on missing input.
- [ ] Every CI security job (including `cve-gate` and `policy-guard`) and the smoke test still run.
- [ ] On a `medic/` or `duo/` branch, nothing under `AGENTS.md`, `.gitlab/duo/`, `skills/` or `CODEOWNERS`
      changed (rule 9).
- [ ] Container CVE fixes bump the base image or rely on `apk upgrade`; never relax the scanner.

## Verify

```bash
python -m unittest discover -s tests -t . -v
```
