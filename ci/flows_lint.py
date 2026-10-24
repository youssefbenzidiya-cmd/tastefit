"""Validate GitLab Duo flow definitions in .gitlab/duo/flows/."""
import sys
from pathlib import Path

import yaml

REQUIRED = ("version", "components", "prompts", "flow")
FORBIDDEN = {"max_cycles"}


def find_keys(node, keys, path=""):
    if isinstance(node, dict):
        for k, v in node.items():
            if k in keys:
                yield f"{path}/{k}"
            yield from find_keys(v, keys, f"{path}/{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from find_keys(v, keys, f"{path}[{i}]")


def lint(path):
    errors = []
    data = yaml.safe_load(path.read_text())
    if not isinstance(data, dict):
        return ["top level is not a mapping"]
    errors += [f"missing key '{k}'" for k in REQUIRED if not data.get(k)]
    if data.get("version") != "v1":
        errors.append(f"version must be \"v1\", got {data.get('version')!r}")
    components = data.get("components") or []
    names = {c.get("name") for c in components if isinstance(c, dict)}
    prompt_ids = {p.get("prompt_id") for p in data.get("prompts") or [] if isinstance(p, dict)}
    entry = (data.get("flow") or {}).get("entry_point")
    if not entry:
        errors.append("missing flow.entry_point")
    elif entry not in names:
        errors.append(f"flow.entry_point '{entry}' is not a component name")
    for c in components:
        pid = c.get("prompt_id") if isinstance(c, dict) else None
        if pid and pid not in prompt_ids:
            errors.append(f"component '{c.get('name')}' uses unknown prompt_id '{pid}'")
    errors += [f"forbidden key {k}" for k in find_keys(data, FORBIDDEN)]
    return errors


def main():
    files = sorted(Path(".gitlab/duo/flows").glob("*.yml"))
    if not files:
        sys.exit("flows-lint: no flow files found in .gitlab/duo/flows/")
    failed = False
    for f in files:
        errors = lint(f)
        print(f"{f}: {'OK' if not errors else 'FAIL'}")
        for e in errors:
            print(f"  - {e}")
        failed |= bool(errors)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
