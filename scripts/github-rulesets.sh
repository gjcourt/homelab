#!/usr/bin/env bash
# Ensure every gjcourt repo has the `default-branch-guard` and
# `renovate-branch-guard` rulesets.
#
# What it enforces on each repo's DEFAULT branch, for everyone but the bypass
# actor below: changes arrive via a pull request (0 approvals required), the
# branch can't be deleted, and it can't be force-pushed. None of that gets in
# the way of George's normal PR-and-merge workflow, so his merges are not rule
# bypasses (2026-09-27 trial: a plain `gh pr merge`, no --admin, went through).
#
# What it does NOT do: stop the bench-cloud GitHub App merging its own PR. The
# 2026-09-26 trial proved a `restrict updates` rule would — but it also blocks
# George's own `gh pr merge` unless he uses `--admin`, which his standing rules
# forbid. Rulesets can only exempt actors, not target one, so any rule that
# stops the App stops George too. Decision (George, 2026-09-27, "option 2"):
# no merge block. The App not merging is the agent's policy (managed CLAUDE.md
# + deny list); a merge by bench-cloud[bot] is caught after the fact by the
# hourly merge audit (lands with homelab#1491), which pages George to revert
# it. What the ruleset DOES guarantee is that every App change to a default
# branch is a visible PR merge — never a direct or force push.
#
# The only bypass actor is the repository Admin role (George, and anything
# acting with his token, e.g. renovate-automerge).
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
    {"type": "pull_request", "parameters": {
      "required_approving_review_count": 0,
      "dismiss_stale_reviews_on_push": false,
      "require_code_owner_review": false,
      "require_last_push_approval": false,
      "required_review_thread_resolution": false}},
    {"type": "deletion"},
    {"type": "non_fast_forward"}
  ],
  "bypass_actors": [
    {"actor_id": 5, "actor_type": "RepositoryRole", "bypass_mode": "always"}
  ]
}
JSON
)

# Renovate's branches: only the Admin role may create, update, delete or
# force-push refs/heads/renovate/**. Self-hosted Renovate and
# renovate-automerge both act as George (his PAT), so they bypass; the
# bench-cloud App cannot create or touch a renovate/ branch. Without this, the
# App could open `renovate/anything` with a forged "patch" table and
# renovate-automerge — which recognises Renovate PRs by branch prefix only —
# would merge it with George's token.
#
# No `non_fast_forward` rule here, deliberately. With it, a force-push by an
# Admin-bypass actor to renovate/** fails with GitHub "Internal Server Error"
# (reproduced twice on a scratch repo, 2026-09-29; request ID of the second:
# F4FC:34C773:C2EE3:F73DD:6ABBD082) — and Renovate force-pushes
# as George on every rebase. Without it, creation/update/force-push/delete all
# bypass cleanly. Nothing is lost: `update` already refuses every non-bypass
# push to renovate/**, forced or not.
BODY_RENOVATE=$(cat <<'JSON'
{
  "name": "renovate-branch-guard",
  "target": "branch",
  "enforcement": "active",
  "conditions": {"ref_name": {"include": ["refs/heads/renovate/**"], "exclude": []}},
  "rules": [
    {"type": "creation"},
    {"type": "update", "parameters": {"update_allows_fetch_and_merge": false}},
    {"type": "deletion"}
  ],
  "bypass_actors": [
    {"actor_id": 5, "actor_type": "RepositoryRole", "bypass_mode": "always"}
  ]
}
JSON
)
RULESETS=("$BODY" "$BODY_RENOVATE")

errf=$(mktemp); trap 'rm -f "$errf"' EXIT

# GitHub does not echo rule parameters back verbatim (measured 2026-09-26/27):
#   - it OMITS parameters at their default (`update` came back with none,
#     though created with update_allows_fetch_and_merge:false);
#   - it ADDS server defaults we never sent (`pull_request` came back with
#     allowed_merge_methods, required_reviewers and
#     require_extra_approval_for_unattributed_changes — which GitHub documents
#     as having no effect when the rule requires zero approvals).
# So compare only the parameters this script MANAGES — the keys in BODY — and
# treat default values (false/0/null) as absent on both sides. A managed key
# flipped to a non-default value (e.g. dismiss_stale_reviews_on_push:true,
# required_approving_review_count:1) is still drift.
#
# Compare what matters: target, enforcement, conditions, every rule with its
# managed parameters, and the bypass list. jq -S sorts object keys, so field
# order in the API response doesn't matter. $managed maps rule type -> the
# parameter keys BODY sets for it.
# shellcheck disable=SC2016  # $managed/$k/$ty are jq variables, not shell
NORM='{t: .target, e: .enforcement, c: .conditions,
       r: ([.rules[] | .type as $ty
              | {type, parameters: ((.parameters // {})
                  | with_entries(select((.key as $k | ($managed[$ty] // []) | index($k)) != null))
                  | with_entries(select(.value != false and .value != 0 and .value != null))
                  | if . == {} then null else . end)}] | sort_by(.type)),
       b: ([.bypass_actors[]? | {actor_id, actor_type, bypass_mode}] | sort_by(.actor_type, .actor_id))}'
# norm BODY < ruleset-json : normalise against the parameter keys BODY manages.
norm() {
  local managed
  managed=$(jq -c '[.rules[] | {key: .type, value: ((.parameters // {}) | keys)}] | from_entries' <<<"$1")
  jq -cS --argjson managed "$managed" "$NORM"
}

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
for BODY in "${RULESETS[@]}"; do
  NAME=$(jq -r .name <<<"$BODY")
  want=$(norm "$BODY" <<<"$BODY")
  existing=$(gh api "repos/$OWNER/$repo/rulesets?per_page=100" \
      --jq ".[] | select(.name == \"$NAME\") | .id" 2>"$errf") || {
    echo "FAIL    $repo [$NAME]  (list rulesets: $(tr '\n' ' ' <"$errf"))"; failed=$((failed + 1)); continue; }
  if [[ -n "$existing" ]]; then
    live=$(gh api "repos/$OWNER/$repo/rulesets/$existing" 2>"$errf") || {
      echo "FAIL    $repo [$NAME]  (get ruleset $existing: $(tr '\n' ' ' <"$errf"))"; failed=$((failed + 1)); continue; }
    if [[ "$(norm "$BODY" <<<"$live")" == "$want" ]]; then
      # The Admin-role bypass (actor_id 5) is undocumented; this is GitHub's own
      # answer to "can the caller (George) bypass it?". If not, his merges and
      # renovate-automerge (his PAT) would be blocked too.
      can=$(jq -r '.current_user_can_bypass // "unknown"' <<<"$live")
      if [[ "$can" != always ]]; then
        echo "FAIL    $repo [$NAME]  (ruleset matches, but current_user_can_bypass=$can, want always)"
        failed=$((failed + 1)); continue
      fi
      echo "ok      $repo [$NAME]"; ok=$((ok + 1)); continue
    fi
    verb=update; method=PUT; path="repos/$OWNER/$repo/rulesets/$existing"
  else
    verb=create; method=POST; path="repos/$OWNER/$repo/rulesets"
  fi
  missing=$((missing + 1))
  case "$MODE" in
    dry-run|check) echo "WOULD-$verb $repo [$NAME]" ;;
    apply)
      if out=$(gh api -X "$method" "$path" --input - <<<"$BODY" 2>&1); then
        echo "${verb}d $repo [$NAME]"; changed=$((changed + 1))
      else
        hint=""
        # Personal-account rulesets on private repos need GitHub Pro; on a plan
        # without it GitHub's 403 asks to upgrade or make the repo public.
        if grep -qi "upgrade to github pro\|make this repository public" <<<"$out"; then
          hint="PLAN: private-repo rulesets need GitHub Pro — remove this repo from the App installation; "
        fi
        echo "FAIL    $repo [$NAME]  ($verb: $hint${out//$'\n'/ })"; failed=$((failed + 1))
      fi ;;
  esac
done
done <<<"$repos"

echo "---"
echo "mode=$MODE ok=$ok needs-change=$missing changed=$changed failed=$failed"
# A repo the ruleset can't be applied to (e.g. a private repo on a plan without
# rulesets) is one the App can merge on: remove it from the App's installation.
if [[ "$MODE" == check && $((missing + failed)) -gt 0 ]]; then exit 1; fi
if [[ $failed -gt 0 ]]; then exit 1; fi
