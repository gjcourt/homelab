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
# docs/operations/apps/bench-cloud.md (lands with homelab#1483). Scheduled by
# infra/controllers/github-rulesets/ (daily --apply, then --check).
#
#   scripts/github-rulesets.sh            # dry run: report what would change
#   scripts/github-rulesets.sh --apply    # create/update the ruleset everywhere
#   scripts/github-rulesets.sh --check    # exit 1 if any repo lacks it (drift)
#   scripts/github-rulesets.sh --apply --repo scratch   # one repo only (trial)
#
# Run --check after --apply: only the check reads back each ruleset and asks
# GitHub whether the caller can bypass it (current_user_can_bypass).
#
# Needs `gh` authenticated as George (admin on every repo). Idempotent.
set -euo pipefail

OWNER=gjcourt
NAME=default-branch-guard
MODE=dry-run
ONLY=
usage() { echo "usage: $0 [--apply|--check] [--repo NAME]" >&2; exit 2; }
# --apply and --check are mutually exclusive; last-wins would silently turn a
# `--check --apply` typo into a write. --repo must be followed by a name, so
# `--repo --apply` is a usage error rather than a lookup of a repo "--apply".
setmode() { [[ $MODE == dry-run || $MODE == "$1" ]] || usage; MODE=$1; }
while [[ $# -gt 0 ]]; do
  case "$1" in
    --apply) setmode apply ;;
    --check) setmode check ;;
    --repo) [[ -n "${2:-}" && "$2" != -* ]] || usage; ONLY=$2; shift ;;
    *) usage ;;
  esac
  shift
done

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

errf=$(mktemp); trap 'rm -f "$errf"' EXIT

# Compare what matters: target, enforcement, conditions, every rule *with* its
# parameters, and the bypass list. jq -S below sorts object keys, so field order
# in the API response doesn't matter.
NORM='{t: .target, e: .enforcement, c: .conditions,
       r: ([.rules[] | {type, parameters: (.parameters // null)}] | sort_by(.type)),
       b: ([.bypass_actors[]? | {actor_id, actor_type, bypass_mode}] | sort_by(.actor_type, .actor_id))}'
want=$(jq -cS "$NORM" <<<"$BODY")

# Fetch the repo list up front: a failure inside `done < <(...)` would be
# invisible and yield zero repos, i.e. a false-green --check.
if [[ -n "$ONLY" ]]; then
  # Trial on one repo: it must exist and not be archived (rulesets can't be
  # written to an archived repo).
  archived=$(gh repo view "$OWNER/$ONLY" --json isArchived --jq .isArchived) || {
    echo "FATAL: could not look up $OWNER/$ONLY (missing, or no access)" >&2; exit 1; }
  [[ "$archived" == false ]] || { echo "FATAL: $OWNER/$ONLY is archived" >&2; exit 1; }
  repos=$ONLY
else
  repos=$(gh repo list "$OWNER" --limit 500 --no-archived --json name --jq '.[].name' | sort) || {
    echo "FATAL: could not list $OWNER repos" >&2; exit 1; }
fi
nrepos=$(grep -c . <<<"$repos" || true)
if [[ $nrepos -eq 0 ]]; then echo "FATAL: $OWNER has no repos listed" >&2; exit 1; fi
if [[ $nrepos -ge 500 ]]; then echo "FATAL: hit the 500-repo list limit; raise --limit" >&2; exit 1; fi

ok=0 changed=0 missing=0 failed=0
while IFS= read -r repo; do
  existing=$(gh api "repos/$OWNER/$repo/rulesets?per_page=100" \
      --jq ".[] | select(.name == \"$NAME\") | .id" 2>"$errf") || {
    echo "FAIL    $repo  (list rulesets: $(tr '\n' ' ' <"$errf"))"; failed=$((failed + 1)); continue; }
  if [[ -n "$existing" ]]; then
    live=$(gh api "repos/$OWNER/$repo/rulesets/$existing" 2>"$errf") || {
      echo "FAIL    $repo  (get ruleset $existing: $(tr '\n' ' ' <"$errf"))"; failed=$((failed + 1)); continue; }
    if [[ "$(jq -cS "$NORM" <<<"$live")" == "$want" ]]; then
      # The Admin-role bypass (actor_id 5) is undocumented; this is GitHub's own
      # answer to "can the caller (George) bypass it?". If not, his merges and
      # renovate-automerge (his PAT) would be blocked too.
      can=$(jq -r '.current_user_can_bypass // "unknown"' <<<"$live")
      if [[ "$can" != always ]]; then
        echo "FAIL    $repo  (ruleset matches, but current_user_can_bypass=$can, want always)"
        failed=$((failed + 1)); continue
      fi
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
        hint=""
        # Personal-account rulesets on private repos need GitHub Pro; on a plan
        # without it GitHub's 403 asks to upgrade or make the repo public.
        if grep -qi "upgrade to github pro\|make this repository public" <<<"$out"; then
          hint="PLAN: private-repo rulesets need GitHub Pro — remove this repo from the App installation; "
        fi
        echo "FAIL    $repo  ($verb: $hint${out//$'\n'/ })"; failed=$((failed + 1))
      fi ;;
  esac
done <<<"$repos"

echo "---"
echo "mode=$MODE ok=$ok needs-change=$missing changed=$changed failed=$failed"
# A repo the ruleset can't be applied to (e.g. a private repo on a plan without
# rulesets) is one the App can merge on: remove it from the App's installation.
if [[ "$MODE" == check && $((missing + failed)) -gt 0 ]]; then exit 1; fi
if [[ $failed -gt 0 ]]; then exit 1; fi
