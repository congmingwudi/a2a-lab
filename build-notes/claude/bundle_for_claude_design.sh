#!/usr/bin/env bash
# Flatten build-notes/claude into one upload for Claude Design.
#
#   build-notes/claude/bundle_for_claude_design.sh [OUT_DIR]
#
# Produces OUT_DIR/claude-code-build-notes/{claude-code-build-notes.md,screenshots/,notes/}
# and OUT_DIR/claude-code-build-notes.zip. OUT_DIR defaults to build-notes/claude/dist
# (gitignored — the flattened copy is a build product, never a second source of truth).
# See CLAUDE-DESIGN-HANDOFF.md alongside this script for what to do with the zip.
#
# Bash on purpose: `for n in $ORDER` does not word-split under zsh, which is how the
# first hand-run of this produced a bundle containing only the README.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC="$HERE"
OUT="${1:-$HERE/dist}"
NAME=claude-code-build-notes
BUNDLE="$OUT/$NAME"
COMBINED="$BUNDLE/$NAME.md"

# README's "Recommended presentation arc" (12 → 04/02/15 → 01 → 03 → 05/07/13 → 06/08/09 → 10),
# plus the two notes the arc omits, placed where the notes say they belong:
# 14 after 12 (its "output side of the loop" complement), 11 after 01 (the sweep companion).
ORDER=(12 14 04 02 15 01 11 03 05 07 13 06 08 09 10)

rm -rf "$BUNDLE" "$OUT/$NAME.zip"
mkdir -p "$BUNDLE/notes"

{
  echo "# How this lab was built with Claude — slide source bundle"
  echo
  echo "_Generated $(date +%Y-%m-%d) from \`build-notes/claude/\` in the a2a-lab repo by"
  echo "\`bundle_for_claude_design.sh\`. Notes are in the README's **Recommended presentation arc**"
  echo "order (12 → 04/02/15 → 01 → 03 → 05/07/13 → 06/08/09 → 10), with 14 placed after 12 (its"
  echo "'output side of the loop' complement) and 11 after 01 (the sweep companion). Each note ends"
  echo "with a **Put this in the presentation** block — headline, three bullets, described visual —"
  echo "and carries an evidence grade (repository-backed / vendor-documented / observed) that should"
  echo "survive onto the slide. Screenshots referenced as \`screenshots/*.png\` sit alongside this"
  echo "file; captions are in \`screenshots/README.md\`._"
  echo; echo "---"; echo
  cat "$SRC/README.md"
  for n in "${ORDER[@]}"; do
    f=$(ls "$SRC"/"$n"-*.md)
    echo; echo; echo "---"; echo
    echo "<!-- source: build-notes/claude/$(basename "$f") -->"; echo
    cat "$f"
  done
  echo; echo; echo "---"; echo
  echo "<!-- source: build-notes/claude/screenshots/README.md -->"; echo
  cat "$SRC/screenshots/README.md"
} > "$COMBINED"

cp "$SRC"/[0-9]*.md "$SRC/README.md" "$BUNDLE/notes/"
cp -R "$SRC/screenshots" "$BUNDLE/screenshots"
( cd "$OUT" && zip -qr "$NAME.zip" "$NAME" )

echo "bundle: $BUNDLE"
echo "zip:    $OUT/$NAME.zip ($(du -h "$OUT/$NAME.zip" | cut -f1))"
echo "notes stitched: $(grep -c '^<!-- source: build-notes/claude/[0-9]' "$COMBINED")   slide-spec blocks: $(grep -ci '^## Put this in the presentation' "$COMBINED")"
