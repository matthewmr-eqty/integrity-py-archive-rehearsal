#!/usr/bin/env bash
# Save one release's docs, with its real wheel reports, as docs-site/archive/vX.Y/.
#
#     scripts/archive_release.sh 2.5.0
#
# Run from a checkout of main with the release's four reports in docs/generated/, as release.yml
# does after publishing. A tag is pushed before its reports exist, so the tag's own copy of the
# docs can only hold placeholders; this folder replaces it. The newest patch replaces its
# group's folder. Latest is re-rendered too, so it shows the same reports, unless the release is
# a backport to an older group. Tests: scripts/test_archive_release.py.
set -euo pipefail

VERSION=${1:?usage: archive_release.sh X.Y.Z}
if [[ ! $VERSION =~ ^([0-9]+)\.([0-9]+)\.[0-9]+$ ]]; then
  echo "archive_release: $VERSION is not X.Y.Z" >&2
  exit 1
fi
GROUP="${BASH_REMATCH[1]}.${BASH_REMATCH[2]}"
ROOT=$(git rev-parse --show-toplevel)
PYTHON=${PYTHON:-python3}
REPORTS=(
  auditwheel-show-linux-x86_64.txt
  auditwheel-show-linux-aarch64.txt
  otool-show-macos-arm64.txt
  otool-show-macos-x86_64.txt
)
FOLDERS="$ROOT/docs-site/archive/folders.json"

if ! git -C "$ROOT" rev-parse --quiet --verify "refs/tags/v$VERSION" >/dev/null; then
  echo "archive_release: no tag v$VERSION" >&2
  exit 1
fi
# Release versions, oldest first.
RELEASES=$(git -C "$ROOT" tag --list 'v*.*.*' | sed -nE 's/^v([0-9]+\.[0-9]+\.[0-9]+)$/\1/p' |
  sort -t. -k1,1n -k2,2n -k3,3n)
NEWEST=$(tail -n 1 <<<"$RELEASES")
GROUP_NEWEST=$(grep -F "$GROUP." <<<"$RELEASES" | awk -F. -v g="$GROUP" '$1"."$2 == g' | tail -n 1)
# The group's folder must be its newest release, so an older patch has nothing to save.
if [ "$VERSION" != "$GROUP_NEWEST" ]; then
  echo "archive_release: $VERSION is older than $GROUP_NEWEST; nothing archived."
  exit 0
fi
BACKPORT=false
[ "$VERSION" != "$NEWEST" ] && BACKPORT=true

for report in "${REPORTS[@]}"; do
  if [ ! -f "$ROOT/docs/generated/$report" ]; then
    echo "archive_release: docs/generated/$report is missing" >&2
    exit 1
  fi
done
# main already holds the previous release's reports, so a download that silently did nothing
# would archive those under this version. The Linux reports name their wheel; the macOS ones don't.
for report in auditwheel-show-linux-x86_64.txt auditwheel-show-linux-aarch64.txt; do
  if ! grep -qF "eqty_sdk-$VERSION-" "$ROOT/docs/generated/$report"; then
    echo "archive_release: docs/generated/$report is not the $VERSION report" >&2
    exit 1
  fi
done

TAG_TREE=$(mktemp -d)
git -C "$ROOT" worktree add --quiet --detach "$TAG_TREE" "v$VERSION"
trap 'git -C "$ROOT" worktree remove --force "$TAG_TREE"' EXIT

# A backport cut from a release before docs-site/ has no pages to save, and its tag now outranks
# the group's folder, which fails the docs build until the folder is re-keyed by hand.
if [ ! -d "$TAG_TREE/docs-site/src/content/docs" ]; then
  old=$(jq -r --arg group "$GROUP." 'keys[] | select(startswith($group))' "$FOLDERS")
  echo "archive_release: v$VERSION has no docs-site/ to archive. In folders.json, rename the" \
    "\"${old:-$GROUP.x}\" key to \"$VERSION\", or the docs build fails." >&2
  exit 1
fi

# The tag's pages, filled by the tag's own render script, with the real reports. Each render runs
# from its own tree: griffe would otherwise find ./eqty_sdk in the working directory first.
for report in "${REPORTS[@]}"; do
  cp "$ROOT/docs/generated/$report" "$TAG_TREE/docs/generated/$report"
done
(cd "$TAG_TREE" && "$PYTHON" scripts/render_api_docs.py >/dev/null)

DEST="$ROOT/docs-site/archive/v$GROUP"
rm -rf "${DEST:?}"
cp -R "$TAG_TREE/docs-site/src/content/docs" "$DEST"

jq -S --arg version "$VERSION" --arg group "$GROUP." --arg dir "archive/v$GROUP" \
  'with_entries(select(.key | startswith($group) | not)) + {($version): $dir}' \
  "$FOLDERS" >"$FOLDERS.tmp"
mv "$FOLDERS.tmp" "$FOLDERS"
echo "archive_release: $VERSION → docs-site/archive/v$GROUP"

# Latest documents $NEWEST, so a backport's reports stay out of it.
if $BACKPORT; then
  git -C "$ROOT" checkout -- docs/generated
  git -C "$ROOT" clean -fdq -- docs/generated
  echo "archive_release: backport; latest's pages and reports are unchanged."
  exit 0
fi
(cd "$ROOT" && "$PYTHON" scripts/render_api_docs.py >/dev/null)

# The old MkDocs addresses of the release that was current are redirected to latest. Once a newer
# group ships they belong to that group's archived version; no check fails if they are left, so
# say it here, where release.yml copies it into the PR.
jq -r '.redirects[] | select(.destination | test("^/(\\$2/?)?$")) | .source' "$ROOT/vercel.json" |
  { grep -oE '\(([0-9]+)\\\.([0-9]+)\\\.' || true; } | sort -u |
  while read -r source; do
    old=$(sed -E 's/^\(([0-9]+)\\\.([0-9]+)\\\.$/\1.\2/' <<<"$source")
    if [ "$old" != "$GROUP" ]; then
      echo "archive_release: vercel.json sends /$old.x/ addresses to latest; now that $GROUP is current, send them to /v$old/."
    fi
  done
