#!/usr/bin/env bash
# Ensure every gjcourt repo has the `default-branch-guard` ruleset.
#
# Why: bench-cloud agents act as the `bench-cloud` GitHub App with Contents +
# Pull requests write. Branch protection here doesn't bind a PR's author once
# checks pass (no required approvals, enforce_admins off), so on its own it
# would let the App merge its own PRs. This ruleset restricts updates, deletion
# and force-pushes on the DEFAULT branch to bypass actors only — and the only
# bypass actor is the repository Admin role (George, and anything acting with
# his token, e.g. renovate-automerge). The App is not an admin, so it can push
# branches and open PRs but cannot merge or push the default branch.
#
# Plan: docs/plans/2026-09-25-bench-cloud-agent.md. Runbook:
# docs/operations/apps/bench-cloud.md.
#
#   scripts/github-rulesets.sh            # dry run: report what would change
#   scripts/github-rulesets.sh --apply    # create/update the ruleset everywhere
#   scripts/github-rulesets.sh --check    # exit 1 if any repo lacks it (drift)
#
# Needs `gh` authenticated as George (admin on every repo). Idempotent.
set -euo pipefail

OWNER=gjcourt
NAME=default-branch-guard
MODE=dry-run
case "${1:-}" in
  "") ;;
  --apply) MODE=apply ;;
  --check) MODE=check ;;
  *) echo "usage: $0 [--apply|--check]" >&2; exit 2 ;;
esac

# actor_id 5 = the built-in repository Admin role.
BODY=$(cat <<'JSON'
{
  "name": "default-branch-guard",
  "target": "branch",
  "enforcement": "active",
  "conditions": {"ref_name": {"include": ["~DEFAULT_BRANCH"], "exclude": []}},
  "rules": [
    {"type": "update", "parameters": {"update_allows_fetch_and_merge": false}},
    {"type": "deletion"},
    {"type": "non_fast_forward"}
  ],
  "bypass_actors": [
    {"actor_id": 5, "actor_type": "RepositoryRole", "bypass_mode": "always"}
  ]
}
JSON
)

ok=0 changed=0 missing=0 failed=0
while IFS= read -r repo; do
  existing=$(gh api "repos/$OWNER/$repo/rulesets" --jq ".[] | select(.name == \"$NAME\") | .id" 2>&1) || {
    echo "FAIL    $repo  (list rulesets: ${existing//$'\n'/ })"; failed=$((failed + 1)); continue; }
  if [[ -n "$existing" ]]; then
    current=$(gh api "repos/$OWNER/$repo/rulesets/$existing" \
      --jq '{e: .enforcement, c: .conditions, r: ([.rules[] | .type] | sort), b: .bypass_actors}')
    want=$(jq -c '{e: .enforcement, c: .conditions, r: ([.rules[] | .type] | sort), b: .bypass_actors}' <<<"$BODY")
    if [[ "$(jq -cS . <<<"$current")" == "$(jq -cS . <<<"$want")" ]]; then
      echo "ok      $repo"; ok=$((ok + 1)); continue
    fi
    verb=update; method=PUT; path="repos/$OWNER/$repo/rulesets/$existing"
  else
    verb=create; method=POST; path="repos/$OWNER/$repo/rulesets"
  fi
  missing=$((missing + 1))
  case "$MODE" in
    dry-run|check) echo "WOULD-$verb $repo" ;;
    apply)
      if out=$(gh api -X "$method" "$path" --input - <<<"$BODY" 2>&1); then
        echo "${verb}d $repo"; changed=$((changed + 1))
      else
        echo "FAIL    $repo  ($verb: ${out//$'\n'/ })"; failed=$((failed + 1))
      fi ;;
  esac
done < <(gh repo list "$OWNER" --limit 500 --no-archived --json name --jq '.[].name' | sort)

echo "---"
echo "mode=$MODE ok=$ok needs-change=$missing changed=$changed failed=$failed"
# A repo the ruleset can't be applied to (e.g. a private repo on a plan without
# rulesets) is one the App can merge on: remove it from the App's installation.
if [[ "$MODE" == check && $((missing + failed)) -gt 0 ]]; then exit 1; fi
if [[ $failed -gt 0 ]]; then exit 1; fi
