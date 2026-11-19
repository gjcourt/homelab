#!/usr/bin/env bash
# Report PRs MERGED BY the bench-cloud GitHub App in the lookback window.
#
# Under the default-branch-guard ruleset (option 2, scripts/github-rulesets.sh)
# the App cannot push to a default branch, but it CAN merge its own PR — the
# agent's policy says never merge, and this is how a violation gets noticed.
# Exit 1 (and print each PR) if any merge by the App is found, so the Job
# fails and BenchCloudAppMerged fires.
#
#   scripts/bench-cloud/merge-audit.sh [--since 25h] [--bot bench-cloud[bot]]
#
# Needs `gh` authenticated with read access to all gjcourt repos (the CronJob
# uses George's RENOVATE_TOKEN). Read-only.
set -euo pipefail
OWNER=gjcourt
BOT='bench-cloud[bot]'
SINCE=25h
while [[ $# -gt 0 ]]; do
  case "$1" in
    --since) SINCE=${2:?}; shift 2 ;;
    --bot) BOT=${2:?}; shift 2 ;;
    *) echo "usage: $0 [--since 25h] [--bot NAME]" >&2; exit 2 ;;
  esac
done
case "$SINCE" in *h) hrs=${SINCE%h} ;; *) echo "--since takes hours, e.g. 25h" >&2; exit 2 ;; esac
[[ "$hrs" =~ ^[0-9]+$ && $hrs -gt 0 ]] || { echo "bad --since: $SINCE" >&2; exit 2; }

# GNU date in the image; BSD date on a Mac.
since=$(date -u -d "-$hrs hours" +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date -u -v-"${hrs}"H +%Y-%m-%dT%H:%M:%SZ)

# Search can't filter on who merged, so list every merged PR in the window and
# read merged_by on each. Volume is a few dozen a day.
# Every exit-1 path pages George (BenchCloudAppMerged is critical and cannot
# tell a merge from a broken audit), so one retry absorbs a transient GitHub
# blip before it becomes a page. stdin is closed so gh can never eat the loop's
# input below.
retry() { "$@" </dev/null || { sleep 10; "$@" </dev/null; }; }

prs=$(retry gh search prs --owner "$OWNER" --merged --merged-at ">=$since" --limit 1000 \
        --json repository,number --jq '.[] | "\(.repository.nameWithOwner) \(.number)"') || {
  echo "FATAL: search failed" >&2; exit 1; }
n=$(grep -c . <<<"$prs" || true)
if [[ $n -ge 1000 ]]; then echo "FATAL: hit the 1000-result search cap; shorten --since" >&2; exit 1; fi

found=0 errors=0 seen=0
while read -r repo num; do
  [[ -n "${repo:-}" ]] || continue
  seen=$((seen + 1))
  # `// "-"`: tab is IFS whitespace, so an empty field (merged_by null for a
  # deleted account) would collapse and shift author into $by.
  if ! line=$(retry gh api "repos/$repo/pulls/$num" \
      --jq '[.merged_by.login // "-", .user.login // "-", .base.ref, .merged_at, .html_url, .title] | @tsv'); then
    echo "ERROR   $repo#$num: could not read"; errors=$((errors + 1)); continue
  fi
  IFS=$'\t' read -r by author base at url title <<<"$line"
  if [[ "$by" == "$BOT" ]]; then
    echo "MERGED-BY-APP  $url  base=$base  author=$author  at=$at  \"$title\""
    found=$((found + 1))
  fi
done <<<"$prs"

echo "---"
if [[ $seen -ne $n ]]; then echo "ERROR   read $seen of $n PRs"; errors=$((errors + 1)); fi
echo "window since $since: merged PRs checked=$n merged-by-$BOT=$found read-errors=$errors"
[[ $found -eq 0 && $errors -eq 0 ]]
