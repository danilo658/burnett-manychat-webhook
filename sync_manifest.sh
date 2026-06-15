#!/usr/bin/env bash
# Sync the project's manifest.json into this webhook repo + push to GitHub.
# Render will pick up the new manifest on the next request (60s cache TTL).
#
# Run from anywhere — paths are resolved relative to this script.
#
# Usage:
#   ./sync_manifest.sh                 # auto-commit, auto-push
#   ./sync_manifest.sh --dry-run       # show diff, don't commit
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE="$(cd "$HERE/../.." && pwd)/clients/SMM-Authority/Lead-Magnets/manifest.json"
TARGET="$HERE/manifest_fallback.json"

if [[ ! -f "$SOURCE" ]]; then
  echo "ERROR: source manifest not found at $SOURCE" >&2
  exit 1
fi

cp "$SOURCE" "$TARGET"

cd "$HERE"
if git diff --quiet manifest_fallback.json; then
  echo "✓ Manifest already up to date — nothing to commit."
  exit 0
fi

if [[ "${1:-}" == "--dry-run" ]]; then
  echo "=== Diff against last pushed manifest ==="
  git --no-pager diff manifest_fallback.json
  exit 0
fi

KW_COUNT=$(python3 -c "import json,sys; print(len(json.load(open('manifest_fallback.json'))))")
git add manifest_fallback.json
git commit -m "Sync manifest — ${KW_COUNT} freebies"
git push origin main
echo ""
echo "✓ Pushed updated manifest (${KW_COUNT} freebies). Render will see it within ~60s."
