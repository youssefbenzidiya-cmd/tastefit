#!/bin/sh
# Agent-authored merge requests (source branch medic/* or duo/*) may not edit their own policy.
set -eu

PROTECTED='^(AGENTS\.md|((\.gitlab|docs)/)?CODEOWNERS|\.gitlab/duo/|\.agents/|skills/|ci/|security/)'
AGENT_BRANCH='^(medic|duo)/'

branch="${CI_MERGE_REQUEST_SOURCE_BRANCH_NAME:?not a merge request pipeline}"
base="${CI_MERGE_REQUEST_DIFF_BASE_SHA:?missing merge request diff base}"

changed=$(git diff --name-only --no-renames "$base" HEAD)
protected=$(printf '%s\n' "$changed" | grep -E "$PROTECTED" || true)

echo "Source branch: $branch"
echo "Changed files:"
printf '  %s\n' $changed

if printf '%s' "$branch" | grep -Eq "$AGENT_BRANCH"; then
  if [ -n "$protected" ]; then
    echo "policy-guard: agent branch '$branch' edits protected policy paths:"
    printf '  %s\n' $protected
    echo "Agents may not change their own policy. A human must make this change on a non-agent branch."
    exit 1
  fi
  echo "policy-guard: agent branch, no protected paths touched"
else
  [ -z "$protected" ] || { echo "policy-guard: human branch touches policy paths (needs code owner review):"; printf '  %s\n' $protected; }
  echo "policy-guard: OK"
fi
