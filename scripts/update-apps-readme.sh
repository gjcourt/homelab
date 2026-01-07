#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readme="$repo_root/apps/README.md"
base_dir="$repo_root/apps/base"

start_marker='<!-- apps:list:start -->'
end_marker='<!-- apps:list:end -->'

if [[ ! -f "$readme" ]]; then
  echo "ERROR: README not found: $readme" >&2
  exit 1
fi

if [[ ! -d "$base_dir" ]]; then
  echo "ERROR: Base apps directory not found: $base_dir" >&2
  exit 1
fi

if ! grep -qF "$start_marker" "$readme" || ! grep -qF "$end_marker" "$readme"; then
  echo "ERROR: Markers not found in $readme" >&2
  echo "Add these lines under '## Current applications':" >&2
  echo "$start_marker" >&2
  echo "$end_marker" >&2
  exit 1
fi

tmp_block="$(mktemp)"
tmp_out="$(mktemp)"

cleanup() {
  rm -f "$tmp_block" "$tmp_out"
}
trap cleanup EXIT

# Generate a stable list of apps from apps/base/<app>/kustomization.yaml
# Output format: Markdown bullets with links.
find "$base_dir" -mindepth 1 -maxdepth 1 -type d -print \
  | while IFS= read -r app_dir; do
      if [[ -f "$app_dir/kustomization.yaml" ]]; then
        app_name="$(basename "$app_dir")"
        printf -- "- [%s](base/%s/)\n" "$app_name" "$app_name"
      fi
    done \
  | LC_ALL=C sort > "$tmp_block"

# Rewrite README, replacing the content between markers.
awk -v start="$start_marker" -v end="$end_marker" -v blockfile="$tmp_block" '
  $0 == start {
    print
    while ((getline line < blockfile) > 0) {
      print line
    }
    in_block = 1
    next
  }
  $0 == end {
    in_block = 0
    print
    next
  }
  in_block == 1 { next }
  { print }
' "$readme" > "$tmp_out"

mv "$tmp_out" "$readme"

echo "Updated: $readme"